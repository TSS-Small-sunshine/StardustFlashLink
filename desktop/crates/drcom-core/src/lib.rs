//! 星尘闪连 3.0 核心（代号 Altair 牛郎星）。
//!
//! 设计原则（对应 `docs/PLATFORMS.md`）：
//!   - **零平台依赖**：这层不 import 任何 UI / 服务框架，可跑在 Windows / Linux / macOS
//!     以及 x86_64 / aarch64 / **armv7** 上 ✓；
//!   - **纯函数优先**：协议拼装、解析、脱敏、校验都是纯函数 → 单元测试就能覆盖 ✓；
//!   - **与 2.x 契约一致**：配置键名、API 路由、账号/密码文件格式都不变，老用户零迁移 ✓。

pub mod backoff;
pub mod channel;
pub mod cidr;
pub mod config;
pub mod diagnostics;
pub mod guard;
pub mod logfile;
pub mod metrics;
pub mod net;
pub mod password;
pub mod platform;
pub mod probe;
pub mod profiles;
pub mod protocol;
pub mod scheduler;
pub mod secret;
pub mod session;
pub mod settings;
pub mod timefmt;
pub mod zipwriter;

pub use backoff::{Backoff, BACKOFF_LEVELS_MIN};
pub use channel::{Channel, Version};
pub use config::{Config, UpdateChannel};
pub use guard::{guard_allows, GuardResult};
pub use logfile::RotatingLog;
pub use protocol::{LoginReply, LoginRequest, OnlineState, ParseError};
pub use scheduler::Scheduler;
pub use session::{Outcome, RunReport};

/// 3.0 线的版本号（四段，与 2.x tag 习惯一致 ✓）。发布时由 CI 改写这一行。
pub const APP_VERSION: &str = "3.0.0.0";
/// 当前构建属于哪条通道（开发期是预览版 → 不会进正式用户的自动更新 ✓）。
pub const APP_CHANNEL: Channel = Channel::Preview;
/// 通道内序号（每次发预览版 +1）。
pub const APP_CHANNEL_SEQ: u32 = 1;
/// 产品名（界面 / 安装包 / 日志横幅统一用它）。
pub const APP_NAME: &str = "星尘闪连";

fn version_from_consts() -> Version {
    let mut v = Version::parse(APP_VERSION).expect("APP_VERSION 必须是可解析的版本号");
    v.channel = APP_CHANNEL;
    v.seq = APP_CHANNEL_SEQ;
    v
}

/// 当前版本对象。
pub fn app_version() -> Version {
    version_from_consts()
}

/// 当前版本文本，如 `3.0.0.0-preview.1`。
pub fn app_version_string() -> String {
    version_from_consts().to_string_full()
}

/// 给界面 / `/api/status` 用的运行状态快照。
#[derive(Debug, Clone, serde::Serialize)]
pub struct Status {
    pub app: String,
    pub version: String,
    pub channel: String,
    pub channel_label: String,
    pub is_prerelease: bool,
    pub os: String,
    pub arch: String,
    pub target: String,
    pub portable: bool,
    pub data_dir: String,
    pub config_path: String,
    pub account_configured: bool,
    pub config_errors: Vec<String>,
    pub service_flavor: String,
}

impl Status {
    /// 采集一次状态（只读，不写盘 ✓ —— API 里被高频调用也安全）。
    pub fn collect() -> Status {
        let os = platform::Os::current();
        let mut config_errors = Vec::new();
        let mut account_configured = false;
        let path = platform::config_path();
        match Config::load(&path) {
            Ok(cfg) => {
                account_configured = cfg.account_configured();
                config_errors = cfg.validate();
            }
            Err(e) => config_errors.push(e),
        }
        let version = app_version();
        Status {
            app: APP_NAME.to_string(),
            version: version.to_string_full(),
            channel: version.channel.as_str().to_string(),
            channel_label: version.channel.label_cn().to_string(),
            is_prerelease: version.channel.is_prerelease(),
            os: os.as_str().to_string(),
            arch: platform::arch().to_string(),
            target: platform::target_label(),
            portable: platform::is_portable(),
            data_dir: platform::data_dir().display().to_string(),
            config_path: path.display().to_string(),
            account_configured,
            config_errors,
            service_flavor: os.service_flavor().to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_string_is_four_segment_prerelease() {
        let text = app_version_string();
        assert!(text.starts_with("3.0.0.0-preview."), "实际: {}", text);
        let parsed = Version::parse(&text).unwrap();
        assert_eq!(parsed.numbers(), (3, 0, 0, 0));
        assert!(parsed.channel.is_prerelease(), "开发期必须是非正式通道 ✗");
        assert_eq!(parsed.to_string_full(), text);
    }

    #[test]
    fn status_collect_is_readonly_and_serializable() {
        let status = Status::collect();
        let json = serde_json::to_string(&status).unwrap();
        assert!(json.contains("\"version\""));
        assert!(json.contains("\"target\""));
        assert!(!status.os.is_empty() && !status.arch.is_empty());
        // 采集过程中不该把配置写出来（临时目录里也不该多出 config.json ✗）
        assert!(!status.data_dir.is_empty());
    }
}
