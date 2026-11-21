//! 一次检查的共享入口 —— **CLI 与 GUI（后台检查）走同一套逻辑、写同一份业务日志** ✓。
//!
//! 为什么要有它（审计 P2-5）：GUI 的「后台检查」原先自己复制了一份
//! 「守卫 → 账号 → 密码 → `session::check_once`」✗ —— 两处逻辑各写一遍，改一处就漂移（R7）；
//! 而且**从不写 `campus_login.log`** ✗：只用界面的用户打开诊断包，
//! `logs/campus_login.log` 永远是空的、`metrics.json` 全 0（2.x 那边每天都有业务日志）。
//!
//! 现在分三层：
//!   1. [`evaluate`] 是**纯逻辑**（探机结果 / 密码 / HTTP 客户端全部入参注入 → 单测可覆盖、hermetic ✓）；
//!   2. [`check_once`] 是环境层（真探 Wi-Fi/IP、读密码文件、真 HTTP ✓），CLI 与 GUI 都调它；
//!   3. [`append_business_log`] 把结果按 **CLI 同一种格式**追进 `campus_login.log`
//!      —— `metrics::collect` 与诊断包直接可用 ✓（日志目录不存在会自动建 ✓）。
//!
//! 等级口径（ok / fail / skip）只在 [`kind_of`] 一处 ✓ —— 统计与界面文案不会各说各话 ✗。

use crate::config::Config;
use crate::logfile::RotatingLog;
use crate::metrics::{self, Kind};
use crate::net::{HttpGet, PlainHttp};
use crate::probe::{self, Probe};
use crate::session::{self, Outcome};
use std::path::Path;
use std::time::{Duration, SystemTime};

/// 业务日志文件名 —— 与 2.x 同名同口径（诊断包 `logs/campus_login.log` / 统计都读它 ✓）。
pub const BUSINESS_LOG_NAME: &str = "campus_login.log";

/// 一次检查的结论：等级（进日志 / 统计）+ 一行中文（CLI 打印 / 界面显示 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckResult {
    /// ok / fail / skip（口径见 [`kind_of`] ✓）
    pub kind: Kind,
    /// 一行中文（就是写进日志/界面显示的那句 ✓）
    pub summary: String,
}

impl CheckResult {
    pub fn new(kind: Kind, summary: impl Into<String>) -> CheckResult {
        CheckResult { kind, summary: summary.into() }
    }
}

/// **纯逻辑**：守卫 → 账号 → 密码 → `session::check_once`（不读盘、不探机、不联网 ✓）。
///
/// 顺序与 CLI 一直以来的口径一致（审计 P2-5 要求两边统一）：
/// 先看「在不在校园网」（不在就直接 skip，别白跑认证），再看账号 / 密码，最后才发请求 ✓。
/// 等级：不在校园网 / 账号未设置 = `Skip`（不算失败 ✓，否则用户一回家就进退避 ✗）；
/// 密码空 / 网关不可达 / 被拒 = `Fail` ✓。
pub fn evaluate(
    cfg: &Config,
    probe: &Probe,
    password: &str,
    client: &dyn HttpGet,
    timeout: Duration,
) -> CheckResult {
    let verdict = crate::guard_allows(cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        return CheckResult::new(
            Kind::Skip,
            format!(
                "{}（Wi-Fi: {}）",
                verdict.reason,
                probe.ssid.clone().unwrap_or_else(|| "读不到".to_string())
            ),
        );
    }
    if !cfg.account_configured() {
        return CheckResult::new(Kind::Skip, "账号未设置，已跳过检查");
    }
    if password.is_empty() {
        return CheckResult::new(Kind::Fail, "密码是空的（去密码文件里写一行）");
    }
    let report = session::check_once(cfg, password, client, timeout);
    CheckResult::new(kind_of(&report.outcome), report.outcome.summary_cn())
}

/// 环境层：探一次 Wi-Fi / 本机 IP + 读 `password.txt` → [`evaluate`]（会起外部命令 / 读盘 ✓）。
///
/// **CLI 的 `one_check` 与 GUI 的后台检查都调它** ✓ —— 这样两边的判定、等级、文案完全同源。
pub fn check_once(cfg: &Config, timeout: Duration) -> CheckResult {
    let probe = probe::snapshot();
    let password = crate::password::read();
    evaluate(cfg, &probe, &password, &PlainHttp, timeout)
}

/// `Outcome` → 统计等级（**口径只有这一处** ✓，免得 CLI / GUI 各判一套 ✗）。
pub fn kind_of(outcome: &Outcome) -> Kind {
    match outcome {
        Outcome::SkippedNoAccount => Kind::Skip,
        Outcome::AlreadyOnline | Outcome::LoggedIn { .. } => Kind::Ok,
        _ => Kind::Fail,
    }
}

/// 业务日志对象（落点只有这一处：`<数据目录>/logs/campus_login.log` ✓）。
pub fn business_log(log_dir: &Path) -> RotatingLog {
    RotatingLog::business(log_dir.join(BUSINESS_LOG_NAME))
}

/// 把一次检查写进业务日志（**与 CLI 同一种格式** ✓；父目录不存在会自动建 ✓）。
///
/// 写失败不 panic：调用方（CLI / GUI 后台线程）负责把可读错误打到 stderr ✓ ——
/// 检查本身已经做完，不该因为日志写不进去就丢掉结果 ✗。
pub fn append_business_log(
    log_dir: &Path,
    finished: SystemTime,
    kind: Kind,
    summary: &str,
) -> Result<(), String> {
    let log = business_log(log_dir);
    log.append_line(&metrics::log_line(finished, kind, summary))
        .map_err(|e| format!("写业务日志失败（{}）：{}", log.path().display(), e))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::net::HttpReply;
    use std::fs;
    use std::path::PathBuf;

    /// 假 HTTP 客户端：只在内存里回一段 JSONP，**不碰网络** ✓。
    struct FakeHttp {
        body: String,
    }

    impl HttpGet for FakeHttp {
        fn get(&self, _url: &str, _timeout: Duration) -> Result<HttpReply, String> {
            Ok(HttpReply { status: 200, location: None, body: self.body.clone() })
        }
    }

    fn on_campus_cfg() -> Config {
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string(); // 一眼假的合成账号 ✓（§9.4）
        cfg.suffix = "@yd".to_string();
        cfg.network_guard_enabled = false;
        cfg
    }

    fn on_campus_probe() -> Probe {
        Probe { ssid: Some("Campus-WiFi".to_string()), ips: vec!["172.16.80.77".to_string()] }
    }

    fn always_online() -> FakeHttp {
        FakeHttp { body: "dr1({\"result\":1})".to_string() }
    }

    /// 临时根目录（**不建** logs/ 子目录 —— 断言 append 会自己建出来 ✓）。
    fn temp_root(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sfl-runtime-{}-{}", tag, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        dir
    }

    const TIMEOUT: Duration = Duration::from_millis(50);
    const DEMO_PASSWORD: &str = "#Demo-Pwd-0000#"; // 一眼假的合成密码 ✓

    #[test]
    fn evaluate_maps_the_shared_four_outcomes_to_kinds() {
        // ① 已在线 → Ok
        let online = FakeHttp { body: "dr1({\"result\":1})".to_string() };
        let result = evaluate(&on_campus_cfg(), &on_campus_probe(), DEMO_PASSWORD, &online, TIMEOUT);
        assert_eq!(result.kind, Kind::Ok, "{}", result.summary);

        // ② 不在校园网 → Skip（不算失败 ✓）
        let mut guarded = on_campus_cfg();
        guarded.network_guard_enabled = true;
        guarded.guard_allowed_ssids = "Dorm-WiFi".to_string();
        let result = evaluate(&guarded, &on_campus_probe(), DEMO_PASSWORD, &always_online(), TIMEOUT);
        assert_eq!(result.kind, Kind::Skip, "{}", result.summary);
        assert!(result.summary.contains("Wi-Fi: Campus-WiFi"), "守卫跳过要带上当前 Wi-Fi ✓: {}", result.summary);

        // ③ 账号未设置 → Skip（不联网 ✓）
        let mut no_account = on_campus_cfg();
        no_account.account = String::new();
        let result = evaluate(&no_account, &on_campus_probe(), DEMO_PASSWORD, &always_online(), TIMEOUT);
        assert_eq!(result.kind, Kind::Skip, "{}", result.summary);

        // ④ 密码空 → Fail（CLI 一直是这个口径 ✓）
        let result = evaluate(&on_campus_cfg(), &on_campus_probe(), "", &always_online(), TIMEOUT);
        assert_eq!(result.kind, Kind::Fail, "{}", result.summary);

        // ⑤ 网关不可达 → Fail
        struct DeadGateway;
        impl HttpGet for DeadGateway {
            fn get(&self, _url: &str, _timeout: Duration) -> Result<HttpReply, String> {
                Err("connection refused".to_string())
            }
        }
        let result = evaluate(&on_campus_cfg(), &on_campus_probe(), DEMO_PASSWORD, &DeadGateway, TIMEOUT);
        assert_eq!(result.kind, Kind::Fail, "{}", result.summary);
    }

    #[test]
    fn one_check_writes_exactly_one_business_log_line_metrics_can_read() {
        // 审计 P2-5 的核心断言：一次真正检查 → campus_login.log 恰好 1 行 → metrics 能统计到 ✓
        let root = temp_root("one-line");
        let log_dir = root.join("logs"); // **故意不建** —— append 必须自己建目录 ✓
        assert!(!log_dir.exists());

        let result = evaluate(&on_campus_cfg(), &on_campus_probe(), DEMO_PASSWORD, &always_online(), TIMEOUT);
        assert_eq!(result.kind, Kind::Ok, "{}", result.summary);

        let finished = SystemTime::now();
        append_business_log(&log_dir, finished, result.kind, &result.summary).unwrap();

        let path = log_dir.join(BUSINESS_LOG_NAME);
        assert!(path.is_file(), "日志目录不存在也要能建出来 ✓: {}", path.display());
        let text = fs::read_to_string(&path).unwrap();
        let lines: Vec<String> = text.lines().map(str::to_string).collect();
        assert_eq!(lines.len(), 1, "一次检查只写一行 ✓: {:?}", lines);
        assert!(lines[0].contains(" [ok] "), "格式与 CLI 一致 ✓: {:?}", lines);
        assert!(lines[0].contains("Z [ok] "), "UTC 时间戳（带 Z）紧跟等级标记 ✓: {:?}", lines);
        assert!(!lines[0].contains(DEMO_PASSWORD), "日志里绝不许有密码 ✗: {:?}", lines);

        // 诊断包的统计口径：同一份日志行 → 1 次成功 ✓
        let metrics = metrics::collect(&lines, 7, finished);
        assert_eq!((metrics.ok, metrics.fail, metrics.skip), (1, 0, 0), "{}", metrics.summary_cn());
        assert_eq!(metrics.success_rate, Some(10_000), "1/1 = 100% ✓");
        assert_eq!(metrics.last_summary.as_deref(), Some(result.summary.as_str()));

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn skipped_check_is_logged_as_skip_and_not_counted_as_failure() {
        let root = temp_root("skip-line");
        let mut cfg = on_campus_cfg();
        cfg.network_guard_enabled = true;
        cfg.guard_allowed_ssids = "Dorm-WiFi".to_string();
        let result = evaluate(&cfg, &on_campus_probe(), DEMO_PASSWORD, &always_online(), TIMEOUT);
        assert_eq!(result.kind, Kind::Skip);

        let finished = SystemTime::now();
        append_business_log(&root, finished, result.kind, &result.summary).unwrap();
        let lines: Vec<String> =
            fs::read_to_string(root.join(BUSINESS_LOG_NAME)).unwrap().lines().map(str::to_string).collect();
        let metrics = metrics::collect(&lines, 7, finished);
        assert_eq!((metrics.ok, metrics.fail, metrics.skip), (0, 0, 1), "{}", metrics.summary_cn());
        assert_eq!(metrics.success_rate, None, "skip 不进成功率（也不该显示 0% ✗）");

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn appending_twice_grows_the_same_file_in_order() {
        let root = temp_root("two-lines");
        let first = SystemTime::now() - Duration::from_secs(60);
        append_business_log(&root, first, Kind::Ok, "已在线，无需登录").unwrap();
        append_business_log(&root, SystemTime::now(), Kind::Fail, "网关不可达：连接超时").unwrap();
        let lines: Vec<String> =
            fs::read_to_string(root.join(BUSINESS_LOG_NAME)).unwrap().lines().map(str::to_string).collect();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].contains("[ok]"), "{:?}", lines);
        assert!(lines[1].contains("[fail]"), "{:?}", lines);
        let _ = fs::remove_dir_all(&root);
    }
}
