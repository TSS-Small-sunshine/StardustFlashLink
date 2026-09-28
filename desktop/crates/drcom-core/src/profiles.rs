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
pub const PROFILE_KEYS: [&str; 6] = [
    "host",
    "port",
    "auto_check_interval_min",
    "network_guard_enabled",
    "guard_allowed_ssids",
    "guard_allowed_subnets",
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

/// 每个方案配了哪些 Wi-Fi 名（界面展示用 ✓）
pub fn match_ssids_of(cfg: &Config, name: &str) -> Vec<String> {
    cfg.profiles
        .get(name)
        .and_then(|entry| entry.get("match_ssids"))
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default()
}

