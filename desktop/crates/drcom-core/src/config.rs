//! 配置：字段名 / 默认值 / 校验规则与 2.x **逐字对齐**，老用户的 `config.json` 直接能用 ✓。
//!
//! 两个关键设计：
//!   1. **未知字段原样保留**（`#[serde(flatten)] extra`）—— 3.0 早期版本不该把 2.x
//!      或更新版本写的键吃掉 ✗；
//!   2. 校验错误**一次全报**（`Vec<String>`），界面上能一次列出来，而不是修一个报一个 ✓。

use crate::channel::Channel;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

pub const ALLOWED_SUFFIXES: [&str; 4] = ["", "@yd", "@dx", "@lt"];
pub const ALLOWED_INTERVALS: [u32; 5] = [5, 15, 30, 60, 120];
pub const ALLOWED_UPDATE_INTERVALS: [u32; 3] = [6, 12, 24];

/// 3.0 起新增的通道字段（2.x 的配置里没有 → 默认正式版）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum UpdateChannel {
    Snapshot,
    Preview,
    Rc,
    Release,
}

impl From<UpdateChannel> for Channel {
    fn from(value: UpdateChannel) -> Self {
        match value {
            UpdateChannel::Snapshot => Channel::Snapshot,
            UpdateChannel::Preview => Channel::Preview,
            UpdateChannel::Rc => Channel::Rc,
            UpdateChannel::Release => Channel::Release,
        }
    }
}

impl From<Channel> for UpdateChannel {
    fn from(value: Channel) -> Self {
        match value {
            Channel::Snapshot => UpdateChannel::Snapshot,
            Channel::Preview => UpdateChannel::Preview,
            Channel::Rc => UpdateChannel::Rc,
            Channel::Release => UpdateChannel::Release,
        }
    }
}

impl UpdateChannel {
    pub fn as_str(self) -> &'static str {
        match self {
            UpdateChannel::Snapshot => "snapshot",
            UpdateChannel::Preview => "preview",
            UpdateChannel::Rc => "rc",
            UpdateChannel::Release => "release",
        }
    }

    pub fn label_cn(self) -> &'static str {
        match self {
            UpdateChannel::Snapshot => "快照版",
            UpdateChannel::Preview => "预览版",
            UpdateChannel::Rc => "候选版",
            UpdateChannel::Release => "正式版",
        }
    }

    /// 是否预发布 —— **自动更新的判据**（正式用户收不到预览版 ✓）
    pub fn is_prerelease(self) -> bool {
        !matches!(self, UpdateChannel::Release)
    }
}

/// 应用配置（字段名 = 2.x `config.json` 的键名 ✓）。
///
/// `#[serde(default)]`（**结构级**）是关键：任何缺失字段都退回 [`Config::default`]，
/// 也就是 2.x 的默认值 —— 这样「老配置 / 手写配置 / 只写几个键」都能读 ✓。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub account: String,
    pub suffix: String,
    pub auto_check_enabled: bool,
    pub auto_check_interval_min: u32,
    pub network_wait_timeout_sec: u32,
    pub network_guard_enabled: bool,
    pub guard_allowed_ssids: String,
    pub guard_allowed_subnets: String,
    pub profiles: BTreeMap<String, serde_json::Value>,
    pub active_profile: String,
    pub profiles_auto_switch: bool,
    pub ui_port: u16,
    pub auto_update_enabled: bool,
    pub update_check_interval_hours: u32,
    pub update_min_free_disk_mb: u32,
    /// 3.0 新增：订阅哪条发行通道（默认正式版 → 收不到预览版 ✓）
    #[serde(default = "default_update_channel")]
    pub update_channel: UpdateChannel,
    /// 3.0 新增：便携版标记（数据放程序目录而不是系统目录）
    #[serde(default)]
    pub portable_mode: bool,
    /// 未知字段原样保留 ✓
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

fn default_update_channel() -> UpdateChannel {
    UpdateChannel::Release
}

impl Default for Config {
    /// 与 2.x `DEFAULT_CONFIG` 对齐（含那些「默认关」的开关）。
    fn default() -> Self {
        Config {
            host: "172.16.80.3".to_string(),
            port: 80,
            account: String::new(),
            suffix: String::new(),
            auto_check_enabled: true,
            auto_check_interval_min: 30,
            network_wait_timeout_sec: 60,
            network_guard_enabled: false,
            guard_allowed_ssids: String::new(),
            guard_allowed_subnets: String::new(),
            profiles: BTreeMap::new(),
            active_profile: String::new(),
            profiles_auto_switch: false,
            ui_port: 8848,
            auto_update_enabled: false,
            update_check_interval_hours: 6,
            update_min_free_disk_mb: 200,
            update_channel: default_update_channel(),
            portable_mode: false,
            extra: BTreeMap::new(),
        }
    }
}

impl Config {
    /// 从 JSON 文本解析（缺字段自动补默认值 ✓ —— 2.x 的老配置里没有 3.0 字段）。
    pub fn from_json(text: &str) -> Result<Config, String> {
        serde_json::from_str(text).map_err(|e| format!("配置 JSON 解析失败: {}", e))
    }

    /// 序列化成好看的多行 JSON（与 2.x 的 `ensure_ascii=False, indent=2` 一致 ✓）。
    pub fn to_json(&self) -> Result<String, String> {
        serde_json::to_string_pretty(self).map_err(|e| format!("配置序列化失败: {}", e))
    }

    /// 账号是否已设置（空账号 = 跳过登录，但**不算错误** ✓）。
    pub fn account_configured(&self) -> bool {
        !self.account.trim().is_empty()
    }

    pub fn load(path: &Path) -> Result<Config, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("读不了配置文件 {}: {}", path.display(), e))?;
        Config::from_json(&text)
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("建不了配置目录 {}: {}", parent.display(), e))?;
        }
        let text = self.to_json()?;
        // 先写临时文件再改名：避免写一半崩掉把好配置毁了 ✗
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, text).map_err(|e| format!("写不了 {}: {}", tmp.display(), e))?;
        std::fs::rename(&tmp, path).map_err(|e| format!("替换 {} 失败: {}", path.display(), e))?;
        Ok(())
    }

    /// 一次报全部问题（空 = 通过）。
    pub fn validate(&self) -> Vec<String> {
        let mut errs = Vec::new();
        if self.host.trim().is_empty() {
            errs.push("认证网关地址不能为空".to_string());
        } else if self.host.contains(char::is_whitespace) || self.host.contains(':') {
            errs.push("认证网关地址不能带空格或端口（端口请填「网关端口」）".to_string());
        }
        if !ALLOWED_SUFFIXES.contains(&self.suffix.as_str()) {
            errs.push(format!("账号后缀只能是 {} 之一", ALLOWED_SUFFIXES.join(" / ")));
        }
        // 空账号**允许保存** ✓（与 2.1.0.0 的 P1-7 一致：新装机也能先存设置）
        if !self.account.is_empty() && !self.account.chars().all(|c| c.is_ascii_digit()) {
            errs.push("账号只能是数字（留空表示稍后再填）".to_string());
        }
        if !ALLOWED_INTERVALS.contains(&self.auto_check_interval_min) {
            errs.push(format!(
                "检查间隔只能是 {} 分钟之一",
                join_numbers(&ALLOWED_INTERVALS)
            ));
        }
        if !(5..=600).contains(&self.network_wait_timeout_sec) {
            errs.push("等网络超时应在 5–600 秒之间".to_string());
        }
        if self.ui_port < 1024 {
            errs.push("界面端口要 ≥ 1024（1024 以下是系统保留端口）".to_string());
        }
        if !ALLOWED_UPDATE_INTERVALS.contains(&self.update_check_interval_hours) {
            errs.push(format!(
                "升级检查间隔只能是 {} 小时之一",
                join_numbers(&ALLOWED_UPDATE_INTERVALS)
            ));
        }
        if self.update_min_free_disk_mb < 50 {
            errs.push("升级所需剩余空间至少 50 MB".to_string());
        }
        for subnet in split_csv(&self.guard_allowed_subnets) {
            if !looks_like_cidr(&subnet) {
                errs.push(format!("网段写法不对：{}（应形如 172.16.0.0/12）", subnet));
            }
        }
        errs
    }
}

fn join_numbers(values: &[u32]) -> String {
    values.iter().map(|v| v.to_string()).collect::<Vec<_>>().join(" / ")
}

/// 极简 CIDR 形状检查（真正的网段判断由 [`crate::cidr`] 负责 ✓；这里只做「写没写对形状」）。
pub fn looks_like_cidr(value: &str) -> bool {
    crate::cidr::parse_cidr(value).is_some()
}

/// 逗号分隔（中英文逗号都认，去空白、去空项）—— 与 2.x `_split_csv` 行为一致 ✓。
pub fn split_csv(value: &str) -> Vec<String> {
    value
        .replace('，', ",")
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn partial_json_uses_defaults_instead_of_failing() {
        // 只写几个键也必须能读（老配置 / 手写配置 / 我上面那步 end-to-end 就踩了这个 ✗）
        let cfg = Config::from_json("{\"host\":\"1.2.3.4\"}").expect("缺字段不该报错");
        assert_eq!(cfg.host, "1.2.3.4");
        assert_eq!(cfg.port, Config::default().port);
        assert_eq!(cfg.ui_port, 8848);
        assert!(cfg.auto_check_enabled, "默认值来自 Config::default ✓");
        assert_eq!(cfg.update_channel, UpdateChannel::Release);
        // 空对象 / 两个花括号也要行 ✓
        assert_eq!(Config::from_json("{}").unwrap(), Config::default());
    }

    #[test]
    fn unknown_fields_are_preserved_on_round_trip() {
        let text = "{\"host\":\"172.16.80.3\",\"future_field\":{\"a\":1},\"another\":7}";
        let cfg = Config::from_json(text).expect("未知字段不该导致解析失败");
        assert!(cfg.extra.contains_key("future_field"), "未知字段要留着 ✓");
        let out = cfg.to_json().unwrap();
        assert!(out.contains("future_field"), "写回时不能把未知字段丢掉 ✗: {}", out);
        assert!(out.contains("another"));
        // 再解析一次还是同一个对象 ✓
        assert_eq!(Config::from_json(&out).unwrap(), cfg);
    }

    #[test]
    fn defaults_match_v2_exactly() {
        let cfg = Config::default();
        assert_eq!(cfg.host, "172.16.80.3");
        assert_eq!(cfg.port, 80);
        assert_eq!(cfg.auto_check_interval_min, 30);
        assert_eq!(cfg.network_wait_timeout_sec, 60);
        assert_eq!(cfg.ui_port, 8848);
        assert!(!cfg.auto_update_enabled, "2.x 里升级默认关 ✓");
        assert_eq!(cfg.update_check_interval_hours, 6);
        assert_eq!(cfg.update_min_free_disk_mb, 200);
        assert!(!cfg.network_guard_enabled, "守卫默认关 ✓");
        assert!(!cfg.profiles_auto_switch, "自动切换方案默认关 ✓");
    }

    #[test]
    fn validation_reports_all_problems_at_once() {
        let mut cfg = Config::default();
        cfg.host = String::new();
        cfg.suffix = "@xx".to_string();
        cfg.auto_check_interval_min = 7;
        cfg.ui_port = 80;
        cfg.update_check_interval_hours = 5;
        cfg.guard_allowed_subnets = "172.16.0.0/12, 不是网段".to_string();
        let errors = cfg.validate();
        assert!(errors.len() >= 5, "一次全报 ✓: {:?}", errors);
        assert!(errors.iter().any(|e| e.contains("网关")));
        assert!(errors.iter().any(|e| e.contains("后缀")));
        assert!(errors.iter().any(|e| e.contains("间隔")));
        assert!(errors.iter().any(|e| e.contains("端口")));
        assert!(errors.iter().any(|e| e.contains("网段")));
    }

    #[test]
    fn empty_account_is_ok_but_non_digits_are_not() {
        let mut cfg = Config::default();
        cfg.account = String::new();
        assert!(cfg.validate().is_empty(), "空账号可保存（P1-7 ✓）");
        assert!(!cfg.account_configured());
        cfg.account = "2023001234".to_string();
        assert!(cfg.validate().is_empty());
        assert!(cfg.account_configured());
        cfg.account = "abc123".to_string();
        assert!(cfg.validate().iter().any(|e| e.contains("数字")));
    }

    #[test]
    fn split_csv_accepts_both_commas_and_trim() {
        assert_eq!(split_csv("a, b ，c ,, "), vec!["a", "b", "c"]);
        assert!(split_csv("   ").is_empty());
        assert_eq!(split_csv("Campus-WiFi"), vec!["Campus-WiFi"]);
    }

    #[test]
    fn save_and_load_round_trip_in_temp_dir() {
        let dir = std::env::temp_dir().join(format!("sfl-cfg-test-{}", std::process::id()));
        let path = dir.join("config.json");
        let mut cfg = Config::default();
        cfg.host = "10.20.30.40".to_string();
        cfg.extra.insert("keep_me".to_string(), serde_json::json!("yes"));
        cfg.save(&path).expect("写盘");
        let back = Config::load(&path).expect("读回");
        assert_eq!(back, cfg);
        assert!(path.with_extension("json.tmp").exists() == false, "临时文件要改掉 ✗");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn looks_like_cidr_uses_the_real_parser() {
        assert!(looks_like_cidr("172.16.0.0/12"));
        assert!(looks_like_cidr("fd00::/8"));
        assert!(!looks_like_cidr("172.16.0.0"));
        assert!(!looks_like_cidr("172.16.0.0/33"));
        assert!(!looks_like_cidr("不是网段"));
    }
}
