//! GUI 自带的后台检查 —— **让 3.0 在 Windows 上完全不必装服务** ✓。
//!
//! 为什么要有它：检查循环原本只在 CLI 的 `run` 里跑 ✗ ——
//! 于是「只用界面」的用户会发现：除了手点「立即检查」，它什么都不做 ✗
//! （开机连上 Wi-Fi 也不会自动登 ✗）。这层就是补这个洞 ✓：
//!
//!   - 每 [`netwatch::CHEAP_TICK_SECS`] 秒看一次主用地址（**不起进程** ✓），变了 → 立刻查 ✓
//!   - 每 10 秒才做一次要起进程的 Wi-Fi 名探测 ✓，变了 → 立刻查 ✓（= 换场景 ✓）
//!   - 到点（配置里的检查间隔 ✓）也查 ✓
//!
//! **两条保险** ✓：
//!   ① 系统服务已经在跑时，后台线程**不抢活** ✗（免得服务和界面各查一遍 ✓）；
//!      服务停了它会自己接手 ✓（每 60 秒复核一次 ✓）。
//!   ② 线程只在窗口活着时跑 ✓（退出前主线程会调 [`Worker::stop`] ✓）。
//!
//! 判定逻辑（[`should_run`]）是纯函数 → 单测覆盖 ✓；真正的检查复用 CLI 那条链路
//! （`guard_allows` → `session::check_once` ✓），所以两边行为一致 ✓。

use drcom_core::{config::Config, net::PlainHttp, netwatch, platform, service, session};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

/// 检查一次的超时（与 CLI 一致 ✓）。
const TIMEOUT: Duration = Duration::from_secs(12);
/// 每多少跳复核一次「服务有没有在跑」✓（跳 = 2 秒 → 60 秒 ✓）。
const SERVICE_RECHECK_TICKS: u64 = 30;

/// 服务在跑吗 → 界面还要不要自己干活 ✓（**纯函数，好测** ✓）。
pub fn should_run(service_running: bool) -> bool {
    !service_running
}

/// 后台线程对外可见的状态（给界面的 Timer 读 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerState {
    /// 一行中文，直接显示在界面上 ✓
    pub line: String,
    /// 最近一次检查的时间（`HH:MM:SS` ✓），没查过就是 None ✓
    pub last_at: Option<String>,
    /// 线程是否正在干活（用于界面文案 ✓）
    pub active: bool,
}

impl Default for WorkerState {
    fn default() -> Self {
        WorkerState {
            line: "后台检查启动中…".to_string(),
            last_at: None,
            active: false,
        }
    }
}

/// 后台检查器（持有线程句柄与共享状态 ✓）。
pub struct Worker {
    state: Arc<Mutex<WorkerState>>,
    stop: Arc<AtomicBool>,
}

impl Worker {
    /// 起一个后台线程 ✓。
    pub fn start() -> Worker {
        let state = Arc::new(Mutex::new(WorkerState::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let thread_state = state.clone();
        let thread_stop = stop.clone();
        thread::spawn(move || run_loop(thread_state, thread_stop));
        Worker { state, stop }
    }

    /// 读一眼当前状态（界面 Timer 每秒调 ✓）。
    pub fn snapshot(&self) -> WorkerState {
        self.state
            .lock()
            .map(|guard| guard.clone())
            .unwrap_or_default()
    }

    /// 让线程自己收工 ✓（退出前调 ✓）。
    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }
}

impl Clone for Worker {
    /// 便宜地克隆一份（两个字段都是 `Arc` ✓）—— 界面 Timer 需要自己持有一份 ✓。
    fn clone(&self) -> Self {
        Worker {
            state: self.state.clone(),
            stop: self.stop.clone(),
        }
    }
}

fn set_line(state: &Arc<Mutex<WorkerState>>, line: String, active: bool) {
    if let Ok(mut guard) = state.lock() {
        guard.line = line;
        guard.active = active;
    }
}

fn set_last_at(state: &Arc<Mutex<WorkerState>>, at: String) {
    if let Ok(mut guard) = state.lock() {
        guard.last_at = Some(at);
    }
}

/// `HH:MM:SS`（只用标准库 ✓ —— 为三个数字引 crate 不值得 ✗）。
fn now_clock() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let of_day = (secs + 8 * 3600) % 86400; // 本地 UTC+8，与日志口径一致 ✓
    format!(
        "{:02}:{:02}:{:02}",
        of_day / 3600,
        (of_day % 3600) / 60,
        of_day % 60
    )
}

/// 跑一次检查（与 CLI 同一条链路 ✓）。
fn check_once() -> String {
    let cfg = match Config::load(&platform::config_path()) {
        Ok(cfg) => cfg,
        Err(e) => return format!("读配置失败：{}", e),
    };
    if !cfg.auto_check_enabled {
        return "自动检查已在设置里关闭".to_string();
    }
    if !cfg.account_configured() {
        return "还没填账号 —— 去「设置…」里填一个 ✓".to_string();
    }
    let password = drcom_core::password::read();
    if password.is_empty() {
        return "还没设密码 —— 去「设置…」里填一个 ✓".to_string();
    }
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(&cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        return format!("跳过：{}", verdict.reason);
    }
    let report = session::check_once(&cfg, &password, &PlainHttp, TIMEOUT);
    report.outcome.summary_cn()
}

/// 线程主体 ✓。
fn run_loop(state: Arc<Mutex<WorkerState>>, stop: Arc<AtomicBool>) {
    let mut watcher = netwatch::Watcher::new();
    let mut last_check = Instant::now();
    let mut tick: u64 = 0;
    let mut service_running = false;

    loop {
        if stop.load(Ordering::SeqCst) {
            set_line(&state, "后台检查已停止".to_string(), false);
            return;
        }
        thread::sleep(Duration::from_secs(netwatch::CHEAP_TICK_SECS));
        tick += 1;

        // ① 每 60 秒（或第一跳）复核「服务有没有在跑」✓
        if tick == 1 || tick % SERVICE_RECHECK_TICKS == 0 {
            service_running = service::status().is_running();
            let line = if should_run(service_running) {
                "后台检查：运行中 ✓".to_string()
            } else {
                "系统服务正在跑 —— 界面不重复检查 ✓".to_string()
            };
            // 同上：没控制台时靠这行排障 ✓（冒烟测试也断言它 ✓）
            eprintln!("[后台检查] {}", line);
            set_line(&state, line, should_run(service_running));
        }
        if !should_run(service_running) {
            continue;
        }

        // ② 便宜通道：主用地址变了 → 立刻查 ✓
        let cfg = Config::load(&platform::config_path()).unwrap_or_default();
        let address = drcom_core::net::local_ip_towards(&cfg.host, cfg.port);
        let mut decision = watcher.observe_address(address);

        // ③ 贵通道：每 5 跳（10 秒）才探一次 Wi-Fi 名 ✓
        if !decision.is_check_now()
            && tick % (netwatch::SSID_TICK_SECS / netwatch::CHEAP_TICK_SECS) == 0
        {
            decision = watcher.observe_ssid(drcom_core::probe::current_ssid());
        }

        // ④ 变过了，或到点了 → 查 ✓
        let interval = Duration::from_secs(u64::from(cfg.auto_check_interval_min.max(1)) * 60);
        if decision.is_check_now() || last_check.elapsed() >= interval {
            let why = decision
                .trigger()
                .map(|trigger| trigger.summary_cn())
                .unwrap_or_else(|| "到检查时间".to_string());
            let summary = check_once();
            let clock = now_clock();
            let line = format!("{}（{}）· {}", summary, why, clock);
            // 界面没有控制台 ✗ —— 但这行在「重定向 stderr」时能救排障 ✓（冒烟测试也靠它 ✓）
            eprintln!("[后台检查] {}", line);
            set_last_at(&state, clock);
            set_line(&state, line, true);
            last_check = Instant::now();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interface_never_fights_the_service() {
        assert!(should_run(false), "没有服务时界面**必须**自己干活 ✓");
        assert!(!should_run(true), "服务在跑时界面**不许**抢活 ✗");
    }

    #[test]
    fn clock_is_hh_mm_ss() {
        let clock = now_clock();
        assert_eq!(clock.len(), 8, "{}", clock);
        let parts: Vec<&str> = clock.split(':').collect();
        assert_eq!(parts.len(), 3, "{}", clock);
        for part in &parts {
            assert_eq!(part.len(), 2, "{}", clock);
            assert!(part.chars().all(|c| c.is_ascii_digit()), "{}", clock);
        }
        assert!(parts[0].parse::<u32>().unwrap() < 24, "{}", clock);
        assert!(parts[1].parse::<u32>().unwrap() < 60, "{}", clock);
    }

    #[test]
    fn default_state_says_it_is_starting() {
        let state = WorkerState::default();
        assert!(state.line.contains("启动"), "{}", state.line);
        assert_eq!(state.last_at, None);
        assert!(!state.active);
    }
}
