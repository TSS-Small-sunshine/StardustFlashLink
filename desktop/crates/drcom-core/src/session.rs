//! 一次检查的完整流程：**在线探测 → 需要时登录**（与 2.x `protocol.run_once` 对齐）。
//!
//! 这一层是「纯逻辑 + 可注入的 HTTP 客户端」：
//!   - 界面（Slint）与命令行都调 [`check_once`]，行为一致 ✓；
//!   - 单测里换成假客户端，不碰网络也能把「已在线 / 登录成功 / 密码错 / 网关不可达」
//!     四条路径全测掉 ✓；
//!   - 所有错误文本都过 [`crate::secret::scrub_url`] —— 登录 URL 里带密码，绝不能漏 ✗。

use crate::config::Config;
use crate::net::HttpGet;
use crate::protocol::{self, OnlineState};
use crate::secret;
use std::time::Duration;

/// 一次检查的结论。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// 账号没设置 → 跳过（**不算失败** ✓，与 2.x P1-7 一致）
    SkippedNoAccount,
    /// 本来就在线
    AlreadyOnline,
    /// 登录成功
    LoggedIn { msg: String },
    /// 服务端明确拒绝（密码错、账号停用等）
    LoginFailed { msg: String },
    /// 网关连不上 / 超时
    Unreachable { detail: String },
    /// 能连上但响应看不懂
    BadResponse { detail: String },
}

impl Outcome {
    /// 是否属于「健康」（自动检查里不该计退避 ✓）。
    pub fn is_ok(&self) -> bool {
        matches!(self, Outcome::SkippedNoAccount | Outcome::AlreadyOnline | Outcome::LoggedIn { .. })
    }

    /// 给界面 / 日志的一行中文说明。
    pub fn summary_cn(&self) -> String {
        match self {
            Outcome::SkippedNoAccount => "账号未设置，已跳过检查".to_string(),
            Outcome::AlreadyOnline => "已在线，无需登录".to_string(),
            Outcome::LoggedIn { msg } => format!("登录成功{}", suffix(msg)),
            Outcome::LoginFailed { msg } => format!("登录被拒绝{}", suffix(msg)),
            Outcome::Unreachable { detail } => format!("网关不可达：{}", detail),
            Outcome::BadResponse { detail } => format!("响应无法解析：{}", detail),
        }
    }
}

fn suffix(msg: &str) -> String {
    if msg.trim().is_empty() {
        String::new()
    } else {
        format!("（{}）", msg.trim())
    }
}

/// 一次检查的完整记录。
#[derive(Debug, Clone)]
pub struct RunReport {
    pub outcome: Outcome,
    /// 在线探测结果（`Unknown` = 没探到，不代表离线 ✓）
    pub online: OnlineState,
    /// 本次用的 JSONP 回调名（排障时能对上抓包 ✓）
    pub callback: String,
}

/// 跑一次检查（不写盘、不发通知 —— 那些留给上层 ✓）。
pub fn check_once(cfg: &Config, password: &str, client: &dyn HttpGet, timeout: Duration) -> RunReport {
    let callback = random_callback();
    if !cfg.account_configured() {
        return RunReport { outcome: Outcome::SkippedNoAccount, online: OnlineState::Unknown, callback };
    }

    // ① 在线探测
    let online_url = protocol::build_online_check_url(&cfg.host, &callback);
    let mut online = OnlineState::Unknown;
    match client.get(&online_url, timeout) {
        Ok(reply) => {
            if let Ok(state) = protocol::parse_online_state(&reply.body) {
                online = state;
                if state == OnlineState::Online {
                    return RunReport { outcome: Outcome::AlreadyOnline, online, callback };
                }
            }
            // 解析不了也不致命：继续尝试登录（与 2.x 行为一致 ✓）
        }
        Err(e) => {
            // 连探测都连不上 → 直接判定网关不可达，省掉一次必然失败的登录 ✓
            return RunReport {
                outcome: Outcome::Unreachable { detail: secret::scrub_url(&e) },
                online,
                callback,
            };
        }
    }

    // ② 登录
    let ip = crate::net::local_ip_towards(&cfg.host, cfg.port).unwrap_or_default();
    let req = protocol::LoginRequest {
        host: &cfg.host,
        account: &cfg.account,
        suffix: &cfg.suffix,
        password,
        ip: &ip,
        mac: "",
    };
    let nonce = callback.trim_start_matches("dr").to_string();
    let url = protocol::build_login_url(&req, &callback, &nonce);
    match client.get(&url, timeout) {
        Ok(reply) => match protocol::parse_login_reply(&reply.body) {
            Ok(reply) if reply.success => RunReport {
                outcome: Outcome::LoggedIn { msg: reply.msg },
                online,
                callback,
            },
            Ok(reply) => RunReport {
                outcome: Outcome::LoginFailed { msg: reply.msg },
                online,
                callback,
            },
            Err(e) => RunReport {
                outcome: Outcome::BadResponse { detail: e.label_cn().to_string() },
                online,
                callback,
            },
        },
        Err(e) => RunReport {
            outcome: Outcome::Unreachable { detail: secret::scrub_url(&e) },
            online,
            callback,
        },
    }
}

/// JSONP 回调名：`dr1234` 形状（与 2.x 一致；用时间纳秒当随机源，省一个依赖 ✓）。
pub fn random_callback() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    format!("dr{}", 1000 + (nanos % 9000))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::net::HttpReply;

    /// 假客户端：按 URL 关键字返回预设响应；
    /// `fail_with` 用来模拟「异常里带着整条 URL（含密码）」的最坏情况 ✓
    struct FakeClient {
        online: Option<String>,
        login: Option<String>,
        fail_with: Option<String>,
        seen: std::cell::RefCell<Vec<String>>,
    }

    impl FakeClient {
        fn new() -> FakeClient {
            FakeClient { online: None, login: None, fail_with: None, seen: Default::default() }
        }
    }

    impl HttpGet for FakeClient {
        fn get(&self, url: &str, _timeout: Duration) -> Result<HttpReply, String> {
            self.seen.borrow_mut().push(url.to_string());
            if let Some(detail) = &self.fail_with {
                return Err(format!("connection failed for {}", detail));
            }
            let body = if url.contains("chkstatus") {
                self.online.clone().unwrap_or_else(|| "cb({\"result\":0})".to_string())
            } else {
                self.login.clone().unwrap_or_else(|| "dr1({\"result\":1,\"msg\":\"ok\"})".to_string())
            };
            Ok(HttpReply { status: 200, location: None, body })
        }
    }

    fn cfg() -> Config {
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        cfg.suffix = "@yd".to_string();
        cfg
    }

    #[test]
    fn already_online_short_circuits_before_login() {
        let client = FakeClient { online: Some("cb({\"result\":1})".into()), ..FakeClient::new() };
        let report = check_once(&cfg(), "pw", &client, Duration::from_millis(50));
        assert_eq!(report.outcome, Outcome::AlreadyOnline);
        assert_eq!(report.online, OnlineState::Online);
        assert!(report.outcome.is_ok());
        assert_eq!(client.seen.borrow().len(), 1, "已在线不该再去登录 ✓");
        assert!(!client.seen.borrow()[0].contains("password"), "探测 URL 里不该有密码");
    }

    #[test]
    fn logs_in_when_offline() {
        let client = FakeClient {
            online: Some("cb({\"result\":0})".into()),
            login: Some("dr1({\"result\":1,\"msg\":\"登录成功\"})".into()),
            ..FakeClient::new()
        };
        let report = check_once(&cfg(), "pw", &client, Duration::from_millis(50));
        assert_eq!(report.outcome, Outcome::LoggedIn { msg: "登录成功".to_string() });
        assert!(report.outcome.summary_cn().contains("登录成功"));
        assert_eq!(client.seen.borrow().len(), 2);
        assert!(client.seen.borrow()[1].contains(":801/eportal/portal/login?"));
    }

    #[test]
    fn wrong_password_is_reported_as_rejected() {
        let client = FakeClient {
            online: Some("cb({\"result\":0})".into()),
            login: Some("dr1({\"result\":0,\"msg\":\"密码错误\"})".into()),
            ..FakeClient::new()
        };
        let report = check_once(&cfg(), "pw", &client, Duration::from_millis(50));
        assert_eq!(report.outcome, Outcome::LoginFailed { msg: "密码错误".to_string() });
        assert!(!report.outcome.is_ok());
        assert!(report.outcome.summary_cn().contains("密码错误"));
    }

    #[test]
    fn unreachable_gateway_reports_scrubbed_detail() {
        let secret_pwd = "SuperSecret!42";
        let leaky = format!(
            "http://172.16.80.3:801/eportal/portal/login?user_password={}&callback=dr1",
            secret_pwd
        );
        let client = FakeClient { fail_with: Some(leaky), ..FakeClient::new() };
        let report = check_once(&cfg(), secret_pwd, &client, Duration::from_millis(50));
        match &report.outcome {
            Outcome::Unreachable { detail } => {
                assert!(!detail.contains(secret_pwd), "报告里漏了密码 ✗: {}", detail);
                assert!(!detail.contains("http://"), "报告里漏了 URL ✗: {}", detail);
                assert!(detail.contains("connection failed"), "要保留可读原因: {}", detail);
            }
            other => panic!("应判定为网关不可达，实际 {:?}", other),
        }
    }

    #[test]
    fn empty_account_skips_without_touching_network() {
        let mut cfg = cfg();
        cfg.account = String::new();
        let client = FakeClient::new();
        let report = check_once(&cfg, "pw", &client, Duration::from_millis(50));
        assert_eq!(report.outcome, Outcome::SkippedNoAccount);
        assert!(report.outcome.is_ok(), "跳过不算失败 ✓");
        assert!(client.seen.borrow().is_empty(), "空账号不该发任何请求 ✓");
    }

    #[test]
    fn callback_names_look_like_v2() {
        let cb = random_callback();
        assert!(cb.starts_with("dr"));
        assert!((5..=6).contains(&cb.len()), "{}", cb);
        assert!(cb[2..].chars().all(|c| c.is_ascii_digit()));
    }

    #[test]
    fn bad_response_is_distinguished_from_unreachable() {
        let client = FakeClient {
            online: Some("cb({\"result\":0})".into()),
            login: Some("<html>portal login page</html>".into()),
            ..FakeClient::new()
        };
        let report = check_once(&cfg(), "pw", &client, Duration::from_millis(50));
        match report.outcome {
            Outcome::BadResponse { detail } => assert!(detail.contains("JSONP"), "{}", detail),
            other => panic!("应判定为响应无法解析，实际 {:?}", other),
        }
    }
}
