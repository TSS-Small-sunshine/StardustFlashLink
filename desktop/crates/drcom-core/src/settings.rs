//! 设置表单的「编辑缓冲」—— 界面 / CLI 共用同一套校验与保存逻辑 ✓。
//!
//! 四条语义（与 2.x 一致 ✓）：
//!   1. **密码留空 = 不修改** ✓（`None` 或纯空白都不动密码文件 ✗）；
//!   2. **未编辑的字段一律保留** ✓（方案列表 `profiles`、`extra` 等原样带回 ✓）；
//!   3. 校验**一次全报** ✓（返回 `Vec<String>`，界面直接列出来 ✓）；
//!   4. 校验不过 → **一个字节都不写** ✓（半套配置绝不落盘 ✗）。

use crate::config::{Config, UpdateChannel};
use crate::{password, platform};
use std::path::{Path, PathBuf};

/// 一份「正在编辑的设置」。
#[derive(Debug, Clone, PartialEq)]
pub struct SettingsForm {
    pub host: String,
    pub port: u16,
    pub account: String,
    pub suffix: String,
    pub interval_min: u32,
    pub wait_timeout_sec: u32,
    pub guard_enabled: bool,
    pub guard_ssids: String,
    pub guard_subnets: String,
    pub auto_update_enabled: bool,
    pub update_channel: UpdateChannel,
    /// `None` / 空白 = **不修改** ✓
    pub password: Option<String>,
}

impl SettingsForm {
    /// 从现有配置读出来（**密码不回显** ✓）
    pub fn from_config(cfg: &Config) -> SettingsForm {
        SettingsForm {
            host: cfg.host.clone(),
            port: cfg.port,
            account: cfg.account.clone(),
            suffix: cfg.suffix.clone(),
            interval_min: cfg.auto_check_interval_min,
            wait_timeout_sec: cfg.network_wait_timeout_sec,
            guard_enabled: cfg.network_guard_enabled,
            guard_ssids: cfg.guard_allowed_ssids.clone(),
            guard_subnets: cfg.guard_allowed_subnets.clone(),
            auto_update_enabled: cfg.auto_update_enabled,
            update_channel: cfg.update_channel,
            password: None,
        }
    }

    /// 把表单套进一份配置副本（**保留未编辑字段** ✓：profiles / active_profile / extra ✓）
    pub fn to_config(&self, base: &Config) -> Config {
        let mut cfg = base.clone();
        cfg.host = self.host.trim().to_string();
        cfg.port = self.port;
        cfg.account = self.account.trim().to_string();
        cfg.suffix = self.suffix.trim().to_string();
        cfg.auto_check_interval_min = self.interval_min;
        cfg.network_wait_timeout_sec = self.wait_timeout_sec;
        cfg.network_guard_enabled = self.guard_enabled;
        cfg.guard_allowed_ssids = self.guard_ssids.trim().to_string();
        cfg.guard_allowed_subnets = self.guard_subnets.trim().to_string();
        cfg.auto_update_enabled = self.auto_update_enabled;
        cfg.update_channel = self.update_channel;
        cfg
    }

    /// 阻断性校验（复用 [`Config::validate`] ✓ —— 口径只有一处 ✓）
    pub fn validate(&self, base: &Config) -> Vec<String> {
        self.to_config(base).validate()
    }

    /// 非阻断提示（界面显示但允许保存 ✓）
    pub fn warnings(&self, base: &Config) -> Vec<String> {
        let cfg = self.to_config(base);
        let mut out = Vec::new();
        if cfg.account_configured() && password_looks_missing(self) {
            out.push("账号已填但还没有密码 —— 检查时会跳过登录".to_string());
        }
        if !cfg.account_configured() {
            out.push("账号还没填（可以先保存，之后再补）".to_string());
        }
        if cfg.network_guard_enabled
            && cfg.guard_allowed_ssids.trim().is_empty()
            && cfg.guard_allowed_subnets.trim().is_empty()
        {
            out.push("开了网络位置守卫但没配白名单 = 等于没开 ✓".to_string());
        }
        if cfg.suffix.is_empty() {
            out.push("后缀为空 = 校内直连（校内公共场合就该这样 ✓）".to_string());
        }
        out
    }

    /// 实际要写的新密码：`None` / 纯空白 = 不写 ✓
    pub fn new_password(&self) -> Option<&str> {
        self.password
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
    }

    /// 保存：**先校验**（不过就一个字节都不写 ✗）→ 先写密码 → 再写配置 ✓
    pub fn save_to(
        &self,
        base: &Config,
        config_path: &Path,
        password_path: &Path,
    ) -> Result<(), String> {
        let cfg = self.to_config(base);
        let errors = cfg.validate();
        if !errors.is_empty() {
            return Err(format!("配置有问题：{}", errors.join("；")));
        }
        if let Some(new_password) = self.new_password() {
            password::write_to(password_path, new_password)?;
        }
        cfg.save(config_path)
    }

    /// 保存到默认位置 ✓
    pub fn save(&self) -> Result<(), String> {
        let config_path = platform::config_path();
        let base = Config::load(&config_path).unwrap_or_default();
        self.save_to(&base, &config_path, &password::path())
    }
}

/// 表单没给新密码**且**磁盘上也没有 → 认为「缺密码」✓
fn password_looks_missing(form: &SettingsForm) -> bool {
    form.new_password().is_none() && password::read().is_empty()
}

/// 间隔可选值（界面下拉用 ✓，与 [`crate::config::ALLOWED_INTERVALS`] 同源 ✓）
pub fn interval_choices() -> Vec<u32> {
    crate::config::ALLOWED_INTERVALS.to_vec()
}

/// 更新通道可选值（界面下拉用 ✓）
pub fn channel_choices() -> Vec<(UpdateChannel, &'static str)> {
    vec![
        (UpdateChannel::Release, "正式版（推荐）"),
        (UpdateChannel::Preview, "预览版（尝鲜，可能不稳定）"),
    ]
}

/// 默认的密码文件路径（界面显示用 ✓）
pub fn password_path() -> PathBuf {
    password::path()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use std::fs;

    fn base() -> Config {
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        cfg.suffix = "@yd".to_string();
        cfg.profiles.insert(
            "宿舍".to_string(),
            serde_json::json!({"values": {"suffix": "@yd"}, "match_ssids": ["Dorm-WiFi"]}),
        );
        cfg.active_profile = "宿舍".to_string();
        cfg.extra.insert("未来字段".to_string(), serde_json::json!(42));
        cfg
    }

    fn temp(tag: &str) -> (PathBuf, PathBuf, PathBuf) {
        let dir = std::env::temp_dir().join(format!("sfl-set-{}-{}", tag, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        (dir.clone(), dir.join("config.json"), dir.join("password.txt"))
    }

    #[test]
    fn round_trip_preserves_unedited_fields() {
        let cfg = base();
        let form = SettingsForm::from_config(&cfg);
        assert_eq!(form.password, None, "密码绝不回显 ✓");
        let back = form.to_config(&cfg);
        assert_eq!(back, cfg, "什么都不改的话应当逐字段一致（含 profiles / extra ✓）");
    }

    #[test]
    fn editing_only_some_fields_keeps_the_rest() {
        let cfg = base();
        let mut form = SettingsForm::from_config(&cfg);
        form.host = "10.1.1.1".to_string();
        form.suffix = String::new(); // 校内公共场合：清空尾缀 ✓
        let out = form.to_config(&cfg);
        assert_eq!(out.host, "10.1.1.1");
        assert_eq!(out.suffix, "");
        assert_eq!(out.profiles, cfg.profiles, "方案列表必须原样保留 ✓");
        assert_eq!(out.active_profile, "宿舍");
        assert_eq!(out.extra.get("未来字段"), cfg.extra.get("未来字段"));
    }

    #[test]
    fn validation_blocks_saving_entirely() {
        let (dir, config_path, password_path) = temp("block");
        let cfg = base();
        let mut form = SettingsForm::from_config(&cfg);
        form.suffix = "@xx".to_string(); // 不合法 ✗
        form.interval_min = 7; // 不合法 ✗
        let errors = form.validate(&cfg);
        assert!(errors.len() >= 2, "一次全报 ✓：{:?}", errors);
        let err = form.save_to(&cfg, &config_path, &password_path).unwrap_err();
        assert!(err.contains("配置有问题"), "{}", err);
        assert!(!config_path.exists(), "校验不过时**不许写配置** ✗");
        assert!(!password_path.exists(), "更不许写密码 ✗");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn password_blank_means_do_not_change() {
        let (dir, config_path, password_path) = temp("pwdkeep");
        let cfg = base();
        crate::password::write_to(&password_path, "旧密码").unwrap();
        let mut form = SettingsForm::from_config(&cfg);
        form.host = "10.9.9.9".to_string();
        // ① None = 不动 ✓
        form.save_to(&cfg, &config_path, &password_path).unwrap();
        assert_eq!(crate::password::read_from(&password_path), "旧密码");
        // ② 纯空白 = 也不动 ✓
        form.password = Some("   ".to_string());
        form.host = "10.9.9.10".to_string();
        form.save_to(&cfg, &config_path, &password_path).unwrap();
        assert_eq!(crate::password::read_from(&password_path), "旧密码");
        // ③ 给了真密码 = 覆盖 ✓
        form.password = Some("新密码".to_string());
        form.save_to(&cfg, &config_path, &password_path).unwrap();
        assert_eq!(crate::password::read_from(&password_path), "新密码");
        let saved = Config::load(&config_path).unwrap();
        assert_eq!(saved.host, "10.9.9.10");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn warnings_are_helpful_but_non_blocking() {
        let cfg = base();
        // 账号已填但本机没有密码文件、且把后缀清空（校内公共场合）→ 各有提醒 ✓
        let mut form = SettingsForm::from_config(&cfg);
        form.suffix = String::new();
        let warnings = form.warnings(&cfg);
        assert!(warnings.iter().any(|w| w.contains("密码")), "{:?}", warnings);
        assert!(
            warnings.iter().any(|w| w.contains("后缀为空")),
            "空后缀要给出解释 ✓: {:?}",
            warnings
        );
        // 带 @yd 时不该出现「后缀为空」那条 ✓
        let form = SettingsForm::from_config(&cfg);
        assert!(!form.warnings(&cfg).iter().any(|w| w.contains("后缀为空")));

        // 守卫开着但白名单空 → 提醒 ✓
        let mut form = SettingsForm::from_config(&cfg);
        form.guard_enabled = true;
        form.guard_ssids = String::new();
        form.guard_subnets = String::new();
        assert!(form.warnings(&cfg).iter().any(|w| w.contains("等于没开")));

        // 空账号 → 提示（但仍可保存 ✓）
        let mut form = SettingsForm::from_config(&cfg);
        form.account = String::new();
        assert!(form.validate(&cfg).is_empty(), "空账号不该阻断保存 ✓（2.1.0.0 的 P1-7）");
        assert!(form.warnings(&cfg).iter().any(|w| w.contains("账号还没填")));
    }

    #[test]
    fn choices_come_from_the_single_source_of_truth() {
        assert_eq!(interval_choices(), crate::config::ALLOWED_INTERVALS.to_vec());
        let channels = channel_choices();
        assert!(channels.iter().any(|(c, _)| *c == UpdateChannel::Release));
        assert!(channels.iter().any(|(c, _)| *c == UpdateChannel::Preview));
        assert_eq!(channels[0].1, "正式版（推荐）", "默认推荐正式版 ✓");
    }
}

