//! 配置方案（2.x `profiles.py` 的纯逻辑部分）——教室 / 宿舍 / 家里各存一份「位置相关字段」。
//!
//! 规则与 2.x 一致：
//!   - **手动方案**（`match_ssids` 为空）：只在用户点「应用」时生效；
//!   - **自动方案**（填了 `match_ssids` 且总开关 `profiles_auto_switch` 开着）：
//!     每次检查发现当前 Wi-Fi 名命中 → 自动切过去 ✓；
//!   - **应用方案 = 先全量校验，通过了才写回** ✓（半套配置绝不落盘 ✗）；
//!   - 删除「当前方案」只清标记，**配置值不动** ✓。

use crate::config::{split_csv, Config};
use std::collections::BTreeMap;

/// 方案里允许出现的键（顺序 = 界面展示顺序 ✓）
///
/// `suffix` 是 v3.0 补进来的（**关键场景**）：校内公共场合（图书馆/教学楼 Wi-Fi）走**校园网**
/// 不需要运营商尾缀，而宿舍/校外商用宽带要带 `@yd`（移动）等尾缀 —— 同一张账号、不同位置
/// 需要不同尾缀 ✓。把它纳入方案就能「按 Wi-Fi 名自动切」✓。
pub const PROFILE_KEYS: [&str; 7] = [
    "host",
    "port",
    "suffix",
    "auto_check_interval_min",
    "network_guard_enabled",
    "guard_allowed_ssids",
    "guard_allowed_subnets",
];

/// 界面上的中文标签（与 2.x 的口径保持一致 ✓）
pub const PROFILE_LABELS: [(&str, &str); 7] = [
    ("host", "认证网关"),
    ("port", "网关端口"),
    ("suffix", "账号后缀（校内公共场合留空）"),
    ("auto_check_interval_min", "检查间隔"),
    ("network_guard_enabled", "网络位置守卫"),
    ("guard_allowed_ssids", "允许的 Wi-Fi 名"),
    ("guard_allowed_subnets", "允许的网段"),
];

pub const MAX_PROFILES: usize = 12;
pub const MAX_NAME_LEN: usize = 24;
pub const MAX_MATCH_SSIDS: usize = 20;

const BAD_CHARS: [char; 12] = ['\\', '/', ':', '*', '?', '"', '<', '>', '|', '\r', '\n', '\t'];

/// 方案名归一化：压掉多余空白；不合法（空 / 超长 / 含路径字符）返回 `""` ✓
pub fn normalize_name(name: &str) -> String {
    let collapsed = name.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() || collapsed.chars().count() > MAX_NAME_LEN {
        return String::new();
    }
    if collapsed.chars().any(|c| BAD_CHARS.contains(&c)) {
        return String::new();
    }
    collapsed
}

/// 从当前配置抽一份「位置相关字段」快照 ✓
pub fn snapshot(cfg: &Config) -> BTreeMap<String, serde_json::Value> {
    let mut values = BTreeMap::new();
    values.insert("host".to_string(), serde_json::json!(cfg.host));
    values.insert("port".to_string(), serde_json::json!(cfg.port));
    values.insert("suffix".to_string(), serde_json::json!(cfg.suffix));
    values.insert(
        "auto_check_interval_min".to_string(),
        serde_json::json!(cfg.auto_check_interval_min),
    );
    values.insert(
        "network_guard_enabled".to_string(),
        serde_json::json!(cfg.network_guard_enabled),
    );
    values.insert("guard_allowed_ssids".to_string(), serde_json::json!(cfg.guard_allowed_ssids));
    values.insert(
        "guard_allowed_subnets".to_string(),
        serde_json::json!(cfg.guard_allowed_subnets),
    );
    values
}

/// 把一份快照应用到配置（类型不对的字段跳过；合法性由 [`Config::validate`] 负责 ✓）
pub fn apply(cfg: &mut Config, values: &BTreeMap<String, serde_json::Value>) {
    if let Some(v) = values.get("host").and_then(|v| v.as_str()) {
        cfg.host = v.to_string();
    }
    if let Some(v) = values.get("port").and_then(|v| v.as_u64()) {
        cfg.port = v.min(u16::MAX as u64) as u16;
    }
    // 后缀：**空串是合法值** ✓（校内公共场合 = 不带运营商尾缀 ✓）
    if let Some(v) = values.get("suffix").and_then(|v| v.as_str()) {
        cfg.suffix = v.to_string();
    }
    if let Some(v) = values.get("auto_check_interval_min").and_then(|v| v.as_u64()) {
        cfg.auto_check_interval_min = v.min(u32::MAX as u64) as u32;
    }
    if let Some(v) = values.get("network_guard_enabled").and_then(|v| v.as_bool()) {
        cfg.network_guard_enabled = v;
    }
    if let Some(v) = values.get("guard_allowed_ssids").and_then(|v| v.as_str()) {
        cfg.guard_allowed_ssids = v.to_string();
    }
    if let Some(v) = values.get("guard_allowed_subnets").and_then(|v| v.as_str()) {
        cfg.guard_allowed_subnets = v.to_string();
    }
}

fn clean_ssids(list: &[String]) -> Vec<String> {
    let mut seen: Vec<String> = Vec::new();
    for item in list {
        for part in split_csv(item) {
            if !seen.contains(&part) && seen.len() < MAX_MATCH_SSIDS {
                seen.push(part);
            }
        }
    }
    seen
}

/// 方案名列表（BTreeMap 已按字典序 ✓，稳定可复现 ✓）
pub fn list(cfg: &Config) -> Vec<String> {
    cfg.profiles.keys().cloned().collect()
}

/// 保存方案（以**当前配置**为快照 ✓；不改变当前方案标记 —— 那是 [`activate`] 的事 ✓）
pub fn save_profile(cfg: &mut Config, name: &str, match_ssids: &[String]) -> Result<String, String> {
    let name = normalize_name(name);
    if name.is_empty() {
        return Err(format!(
            "方案名不合法（不能为空、不超过 {} 字，且不能含 \\ / : * ? \" < > | 等字符）",
            MAX_NAME_LEN
        ));
    }
    if !cfg.profiles.contains_key(&name) && cfg.profiles.len() >= MAX_PROFILES {
        return Err(format!("方案最多 {} 个", MAX_PROFILES));
    }
    let entry = serde_json::json!({
        "values": snapshot(cfg),
        "match_ssids": clean_ssids(match_ssids),
    });
    cfg.profiles.insert(name.clone(), entry);
    Ok(name)
}

/// 应用方案：**先在副本上全量校验，通过了才写回**（2.x 的「先全量校验，再写盘」✓）
pub fn activate(cfg: &mut Config, name: &str) -> Result<(), String> {
    let values: BTreeMap<String, serde_json::Value> = cfg
        .profiles
        .get(name)
        .ok_or_else(|| format!("没有这个方案：{}", name))?
        .get("values")
        .and_then(|v| v.as_object())
        .map(|obj| obj.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();

    let mut candidate = cfg.clone();
    apply(&mut candidate, &values);
    candidate.active_profile = name.to_string();
    let errors = candidate.validate();
    if !errors.is_empty() {
        return Err(format!("方案「{}」校验不通过：{}", name, errors.join("；")));
    }
    *cfg = candidate;
    Ok(())
}

/// 删方案：删的是**当前方案**时只清标记，配置值不动 ✓
pub fn delete(cfg: &mut Config, name: &str) -> Result<(), String> {
    if cfg.profiles.remove(name).is_none() {
        return Err(format!("没有这个方案：{}", name));
    }
    if cfg.active_profile == name {
        cfg.active_profile = String::new();
    }
    Ok(())
}

/// 按 Wi-Fi 名找「自动方案」（需 `profiles_auto_switch` 开着 ✓；精确匹配 ✓）
pub fn match_ssid(cfg: &Config, ssid: &str) -> Option<String> {
    if !cfg.profiles_auto_switch {
        return None;
    }
    let ssid = ssid.trim();
    if ssid.is_empty() {
        return None;
    }
    cfg.profiles.iter().find_map(|(name, entry)| {
        let hit = entry
            .get("match_ssids")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().any(|v| v.as_str() == Some(ssid)))
            .unwrap_or(false);
        if hit {
            Some(name.clone())
        } else {
            None
        }
    })
}

/// 自动切换：命中且与当前方案不同才切换 ✓（返回切换到的方案名）
pub fn auto_switch(cfg: &mut Config, ssid: &str) -> Result<Option<String>, String> {
    match match_ssid(cfg, ssid) {
        Some(name) if name != cfg.active_profile => {
            activate(cfg, &name)?;
            Ok(Some(name))
        }
        _ => Ok(None),
    }
}

/// 当前方案名（空 = 没用方案 ✓）
pub fn active(cfg: &Config) -> Option<&str> {
    if cfg.active_profile.is_empty() {
        None
    } else {
        Some(cfg.active_profile.as_str())
    }
}

/// **位置自适应**：读到了 Wi-Fi 名且命中自动方案就切过去 ✓
///
/// 命名上刻意叫「自适应」而不是「自动切换」，因为这是**检查前的准备动作**：
/// 走到图书馆 → 换成「校内公共场合（无尾缀）」；回宿舍 → 换回「@yd」 ✓。
/// 写盘由调用方负责（核心层不做 IO ✗），切完记得 `cfg.save()` ✓。
pub fn adapt(cfg: &mut Config, ssid: Option<&str>) -> Result<Option<String>, String> {
    match ssid {
        Some(ssid) if !ssid.trim().is_empty() => auto_switch(cfg, ssid),
        _ => Ok(None), // 读不到 Wi-Fi 名 → 什么都不做（fail-open ✓）
    }
}

/// 后缀的展示文案（界面/日志用 ✓）：空后缀 = 校内直连 ✓
pub fn suffix_label(suffix: &str) -> String {
    if suffix.trim().is_empty() {
        "校内直连（无尾缀）".to_string()
    } else {
        suffix.trim().to_string()
    }
}

/// 方案的一句话描述（界面列表用 ✓）：
/// `校内公共场合 · 校内直连（无尾缀） · 匹配 2 个 Wi-Fi`
pub fn describe(cfg: &Config, name: &str) -> String {
    let suffix = cfg
        .profiles
        .get(name)
        .and_then(|entry| entry.get("values"))
        .and_then(|v| v.get("suffix"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let matches = match_ssids_of(cfg, name);
    let hit = if matches.is_empty() {
        "手动应用".to_string()
    } else {
        format!("匹配 {} 个 Wi-Fi", matches.len())
    };
    format!("{} · {} · {}", name, suffix_label(suffix), hit)
}

/// 每个方案配了哪些 Wi-Fi 名（界面展示用 ✓）
pub fn match_ssids_of(cfg: &Config, name: &str) -> Vec<String> {
    cfg.profiles
        .get(name)
        .and_then(|entry| entry.get("match_ssids"))
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> Config {
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        cfg
    }

    /// **核心场景**（用户提的那个）：同一张账号，
    /// 校内公共场合不带尾缀、宿舍要带 @yd —— 靠「按 Wi-Fi 名自动切方案」实现 ✓
    #[test]
    fn suffix_switches_with_location() {
        let mut cfg = base();
        cfg.suffix = "@yd".to_string();
        save_profile(&mut cfg, "宿舍（移动）", &["Dorm-WiFi".to_string()]).unwrap();

        cfg.suffix = String::new(); // 校内公共场合：不带尾缀 ✓
        save_profile(&mut cfg, "校内公共场合", &["Campus-WiFi".to_string()]).unwrap();

        cfg.profiles_auto_switch = true;
        // 走进图书馆 / 教学楼：自动切到「无尾缀」✓
        let switched = auto_switch(&mut cfg, "Campus-WiFi").unwrap();
        assert_eq!(switched.as_deref(), Some("校内公共场合"));
        assert_eq!(cfg.suffix, "", "校内公共场合不该带移动尾缀 ✓");
        assert_eq!(active(&cfg), Some("校内公共场合"));
        // 回宿舍：自动切回带 @yd ✓
        let switched = auto_switch(&mut cfg, "Dorm-WiFi").unwrap();
        assert_eq!(switched.as_deref(), Some("宿舍（移动）"));
        assert_eq!(cfg.suffix, "@yd");
        // 已经在这个方案里 → 不重复切换 ✓
        assert_eq!(auto_switch(&mut cfg, "Dorm-WiFi").unwrap(), None);
    }

    #[test]
    fn empty_suffix_survives_json_round_trip() {
        let mut cfg = base();
        cfg.suffix = String::new();
        save_profile(&mut cfg, "no-suffix", &[]).unwrap();
        let back = Config::from_json(&cfg.to_json().unwrap()).unwrap();
        assert_eq!(back.suffix, "");
        let values = snapshot(&back);
        assert_eq!(values.get("suffix").and_then(|v| v.as_str()), Some(""));
        // 应用回去仍然是空串（不能被当成「没这个键」跳过去 ✗）
        let mut target = base();
        target.suffix = "@lt".to_string();
        apply(&mut target, &values);
        assert_eq!(target.suffix, "", "空后缀必须能覆盖旧值 ✓");
    }

    #[test]
    fn save_activate_delete_flow() {
        let mut cfg = base();
        cfg.host = "10.0.0.1".to_string();
        let name = save_profile(&mut cfg, "  教室   甲  ", &["Campus-WiFi".to_string()]).unwrap();
        assert_eq!(name, "教室 甲", "名字要归一化掉多余空白 ✓");
        assert_eq!(list(&cfg), vec!["教室 甲"]);
        assert_eq!(match_ssids_of(&cfg, "教室 甲"), vec!["Campus-WiFi"]);

        cfg.host = "10.9.9.9".to_string();
        activate(&mut cfg, "教室 甲").unwrap();
        assert_eq!(cfg.host, "10.0.0.1", "应用方案要覆盖网关 ✓");
        assert_eq!(active(&cfg), Some("教室 甲"));

        delete(&mut cfg, "教室 甲").unwrap();
        assert!(list(&cfg).is_empty());
        assert_eq!(active(&cfg), None, "删当前方案只清标记 ✓");
        assert_eq!(cfg.host, "10.0.0.1", "配置值不动 ✓");
        assert!(delete(&mut cfg, "不存在").is_err());
    }

    #[test]
    fn activate_validates_before_writing_anything() {
        let mut cfg = base();
        cfg.host = "172.16.80.3".to_string();
        // 手写一份「坏」方案（后缀不合法）✓
        cfg.profiles.insert(
            "坏的".to_string(),
            serde_json::json!({"values": {"host": "1.2.3.4", "suffix": "@xx"}, "match_ssids": []}),
        );
        let before = cfg.host.clone();
        let err = activate(&mut cfg, "坏的").unwrap_err();
        assert!(err.contains("校验不通过"), "{}", err);
        assert_eq!(cfg.host, before, "校验不过就不许写回 ✓（半套配置绝不落盘）");
        assert!(activate(&mut cfg, "根本没有").is_err());
    }

    #[test]
    fn name_rules_reject_bad_names() {
        let mut cfg = base();
        assert!(save_profile(&mut cfg, "", &[]).is_err());
        assert!(save_profile(&mut cfg, "   ", &[]).is_err());
        assert!(save_profile(&mut cfg, &"字".repeat(MAX_NAME_LEN + 1), &[]).is_err());
        for bad in ["a/b", "a\\b", "a:b", "a*b", "a?b", "a\"b", "a<b", "a>b", "a|b"] {
            assert!(save_profile(&mut cfg, bad, &[]).is_err(), "{} 不该被接受 ✗", bad);
        }
        assert!(
            save_profile(&mut cfg, &"字".repeat(MAX_NAME_LEN), &[]).is_ok(),
            "刚好 24 字可以 ✓"
        );
    }

    #[test]
    fn limits_and_dedup_are_enforced() {
        let mut cfg = base();
        for i in 0..MAX_PROFILES {
            save_profile(&mut cfg, &format!("方案{:02}", i), &[]).unwrap();
        }
        assert_eq!(list(&cfg).len(), MAX_PROFILES);
        assert!(save_profile(&mut cfg, "第十三个", &[]).is_err(), "超过上限要拒绝 ✓");
        assert!(save_profile(&mut cfg, "方案00", &[]).is_ok(), "覆盖同名不受上限影响 ✓");

        let mut cfg = base();
        let many: Vec<String> = (0..MAX_MATCH_SSIDS + 5).map(|i| format!("ssid-{}", i)).collect();
        save_profile(&mut cfg, "多", &many).unwrap();
        assert_eq!(match_ssids_of(&cfg, "多").len(), MAX_MATCH_SSIDS, "Wi-Fi 名要截到上限 ✓");

        let mut cfg = base();
        save_profile(&mut cfg, "去重", &["A, B".to_string(), "B".to_string(), " A ".to_string()])
            .unwrap();
        assert_eq!(match_ssids_of(&cfg, "去重"), vec!["A", "B"], "去重 + 逗号分隔都要认 ✓");
    }

    #[test]
    fn match_requires_auto_switch_and_exact_ssid() {
        let mut cfg = base();
        save_profile(&mut cfg, "校园", &["Campus-WiFi".to_string()]).unwrap();
        assert_eq!(match_ssid(&cfg, "Campus-WiFi"), None, "总开关没开就不该自动切 ✓");
        cfg.profiles_auto_switch = true;
        assert_eq!(match_ssid(&cfg, "Campus-WiFi").as_deref(), Some("校园"));
        assert_eq!(match_ssid(&cfg, "campus-wifi"), None, "精确匹配（大小写敏感）✓");
        assert_eq!(match_ssid(&cfg, "Campus-WiFi-5G"), None, "不做前缀匹配 ✓");
        assert_eq!(match_ssid(&cfg, "  "), None, "空 Wi-Fi 名不该命中 ✓");
    }

    #[test]
    fn keys_and_labels_stay_in_sync() {
        assert_eq!(PROFILE_KEYS.len(), PROFILE_LABELS.len());
        for (idx, key) in PROFILE_KEYS.iter().enumerate() {
            assert_eq!(PROFILE_LABELS[idx].0, *key, "标签顺序要和键顺序一致 ✓");
        }
        assert!(PROFILE_KEYS.contains(&"suffix"), "v3.0 必须把后缀纳入方案 ✓");
        let values = snapshot(&base());
        for key in PROFILE_KEYS {
            assert!(values.contains_key(key), "快照缺字段 {} ✗", key);
        }
    }

    #[test]
    fn adapt_switches_on_known_ssid_and_does_nothing_otherwise() {
        let mut cfg = base();
        cfg.suffix = "@yd".to_string();
        save_profile(&mut cfg, "宿舍（移动）", &["Dorm-WiFi".to_string()]).unwrap();
        // ⚠️ 保存「无尾缀」方案时必须先把 suffix 清空 —— 快照存的是**当时**的值 ✓
        cfg.suffix = String::new();
        save_profile(&mut cfg, "校内公共场合", &["Campus-WiFi".to_string()]).unwrap();
        cfg.profiles_auto_switch = true;

        // 读不到 Wi-Fi 名 → 什么都不做（fail-open ✓）
        cfg.suffix = "@yd".to_string();
        assert_eq!(adapt(&mut cfg, None).unwrap(), None);
        assert_eq!(cfg.suffix, "@yd");
        assert_eq!(adapt(&mut cfg, Some("  ")).unwrap(), None);

        // 命中「校内公共场合」→ 切过去，尾缀被清空 ✓
        assert_eq!(
            adapt(&mut cfg, Some("Campus-WiFi")).unwrap().as_deref(),
            Some("校内公共场合")
        );
        assert_eq!(cfg.suffix, "", "校内公共场合 = 不带运营商尾缀 ✓");

        // 回宿舍 → 切回带 @yd ✓
        assert_eq!(adapt(&mut cfg, Some("Dorm-WiFi")).unwrap().as_deref(), Some("宿舍（移动）"));
        assert_eq!(cfg.suffix, "@yd");

        // 总开关关着 → 手动方案不自动切 ✓
        cfg.profiles_auto_switch = false;
        cfg.suffix = "@yd".to_string();
        assert_eq!(adapt(&mut cfg, Some("Campus-WiFi")).unwrap(), None);
        assert_eq!(cfg.suffix, "@yd");
    }

    #[test]
    fn suffix_label_and_describe_are_readable() {
        assert_eq!(suffix_label(""), "校内直连（无尾缀）");
        assert_eq!(suffix_label("  "), "校内直连（无尾缀）");
        assert_eq!(suffix_label("@yd"), "@yd");
        let mut cfg = base();
        cfg.suffix = String::new();
        save_profile(&mut cfg, "校内公共场合", &["Campus-WiFi".to_string(), "Library".to_string()])
            .unwrap();
        let text = describe(&cfg, "校内公共场合");
        assert!(text.contains("校内直连（无尾缀）"), "{}", text);
        assert!(text.contains("匹配 2 个 Wi-Fi"), "{}", text);
        let mut cfg = base();
        save_profile(&mut cfg, "手动方案", &[]).unwrap();
        assert!(describe(&cfg, "手动方案").contains("手动应用"));
    }
}

