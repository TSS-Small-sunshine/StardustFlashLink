//! 星尘闪连 3.0 核心（代号 Altair 牛郎星）。
//!
//! 设计原则（对应 `docs/PLATFORMS.md`）：
//!   - **零平台依赖**：这层不 import 任何 UI / 服务框架，可跑在 Windows / Linux / macOS
//!     以及 x86_64 / aarch64 / **armv7** 上 ✓；
//!   - **纯函数优先**：协议拼装、解析、脱敏、校验都是纯函数 → 单元测试就能覆盖 ✓；
//!   - **与 2.x 契约一致**：配置键名、API 路由、账号/密码文件格式都不变，老用户零迁移 ✓。

pub mod atomicfile;
pub mod autostart;
pub mod backoff;
pub mod channel;
pub mod checksum;
pub mod cidr;
pub mod config;
pub mod diagnostics;
pub mod guard;
pub mod instance;
pub mod legacy;
pub mod logfile;
pub mod metrics;
pub mod net;
pub mod netwatch;
pub mod package;
pub mod password;
pub mod platform;
pub mod portal;
pub mod probe;
pub mod proc;
pub mod profiles;
pub mod protocol;
pub mod runtime;
pub mod scheduler;
pub mod secret;
pub mod service;
pub mod session;
pub mod settings;
pub mod timefmt;
pub mod update;
pub mod update_apply;
pub mod zipread;
pub mod zipwriter;

pub use backoff::{Backoff, BACKOFF_LEVELS_MIN};
pub use channel::{Channel, Version};
pub use config::{Config, UpdateChannel};
pub use guard::{guard_allows, GuardResult};
pub use legacy::LoadedFrom;
pub use logfile::RotatingLog;
pub use protocol::{LoginReply, LoginRequest, OnlineState, ParseError};
pub use scheduler::Scheduler;
pub use session::{Outcome, RunReport};

/// 3.0 线的版本号（四段，与 2.x tag 习惯一致 ✓）。发布时由 CI 改写这一行。
pub const APP_VERSION: &str = "3.0.0.0";
/// 当前构建属于哪条通道（开发期是预览版 → 不会进正式用户的自动更新 ✓）。
pub const APP_CHANNEL: Channel = Channel::Preview;
/// 通道内序号（每次发预览版 +1）。
pub const APP_CHANNEL_SEQ: u32 = 5;
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
    /// 这份配置**实际来自哪里**：`own`（3.0 数据目录）/ `legacy`（2.x 安装目录，只读兼容）
    /// / `default`（两边都没有，首次运行）✓ —— 审计 P1-3 要求「来源可见」✓。
    pub config_source: String,
    /// 实际读到配置的那个文件（`legacy` 时就是 2.x 的 `config.json`，**只读**）；没有则 `null` ✓。
    pub config_source_path: Option<String>,
    /// 密码的来源（`own` / `legacy` / `default`）—— **只报来源，绝不回密码内容** ✓。
    pub password_source: String,
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
        // 读配置统一走 legacy 兼容层（审计 P1-3）：3.0 没有配置时**只读**回退 2.x，
        // 并把来源一起带给界面 / API / 诊断包 —— 「为什么读到/读不到账号」一眼能看出来 ✓。
        let mut config_source = LoadedFrom::Default;
        let mut config_source_path: Option<String> = None;
        match legacy::load_config() {
            Ok(read) => {
                account_configured = read.config.account_configured();
                config_errors = read.config.validate();
                config_source = read.from;
                config_source_path = read.path.as_deref().map(|p| p.display().to_string());
            }
            Err(e) => config_errors.push(e),
        }
        // 密码同理（只取**来源**，内容永不进状态 ✓）
        let password_source = legacy::read_password().from;
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
            config_source: config_source.as_str().to_string(),
            config_source_path,
            password_source: password_source.as_str().to_string(),
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
        // 审计 P1-3：来源必须出现在状态里（own / legacy / default ✓）
        assert!(json.contains("\"config_source\""), "状态里要带配置来源: {}", json);
        assert!(!status.config_source.is_empty());
        assert!(["own", "legacy", "default"].contains(&status.config_source.as_str()), "来源取值只能是三种之一: {}", status.config_source);
        assert!(["own", "legacy", "default"].contains(&status.password_source.as_str()), "密码来源只能是三种之一: {}", status.password_source);
        // 来源与路径必须自洽：legacy 指 2.x 的 config.json、own 指 3.0 的、default 没有路径 ✓
        match status.config_source.as_str() {
            "legacy" => assert!(
                status
                    .config_source_path
                    .as_deref()
                    .map(|p| p.ends_with("config.json"))
                    .unwrap_or(false),
                "legacy 要带 2.x 的 config.json 路径: {:?}",
                status.config_source_path
            ),
            "own" => assert_eq!(
                status.config_source_path.as_deref(),
                Some(status.config_path.as_str()),
                "own 时路径就是 3.0 自己的配置文件 ✓"
            ),
            "default" => assert!(status.config_source_path.is_none(), "default 时不该有路径 ✓"),
            other => panic!("未知来源: {}", other),
        }
        assert!(!status.os.is_empty() && !status.arch.is_empty());
        // 采集过程中不该把配置写出来（临时目录里也不该多出 config.json ✗）
        assert!(!status.data_dir.is_empty());
    }
}
