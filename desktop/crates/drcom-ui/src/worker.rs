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
//! 判定逻辑（[`should_run`]）是纯函数 → 单测覆盖 ✓；真正的检查复用**共享入口**
//! `drcom_core::runtime::check_once`（守卫 → 账号 → 密码 → `session::check_once` ✓），
//! 所以界面与 CLI 的判定 / 等级 / 文案完全同源 ✓（审计 P2-5 / R7）。
//!
//! **每次真正检查都会写一行业务日志** ✓（与 CLI 同格式的 `…Z [ok|fail|skip] …`）——
//! 只用界面的用户，诊断包里的 `logs/campus_login.log` 与 `metrics.json` 不再是空的 ✓；
//! 而「自动检查关闭 / 读配置失败」这类**没真正检查**的情况不写日志（不刷屏 ✓）。

use drcom_core::{netwatch, service};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

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

/// `HH:MM:SSZ` —— **UTC 口径，与业务日志逐字一致** ✓。
///
/// 审计 P2-9：这里原先硬编码 `(secs + 8 * 3600)`（注释还写着「本地 UTC+8，与日志口径一致」✗）——
/// 日志用的是 `timefmt::format_utc`（UTC，带 Z），两者差 8 小时；而且「本地 = UTC+8」
/// 只在无夏令时的中国时区成立，Linux/macOS 的海外用户会全错 ✗。
/// 现在统一交给 [`drcom_core::timefmt::clock_utc`]，口径只有一处 ✓。
fn now_clock(finished: SystemTime) -> String {
    drcom_core::timefmt::clock_utc(finished)
}

/// 一次「到点」的后台动作：**真正检查了没有** ✓。
///
/// 为什么要分开：只有 [`TickOutcome::Checked`] 才该写业务日志 ✓ ——
/// 「自动检查关闭」「读配置失败」不是一次检查，每来个检查间隔就写一行会把日志刷成噪音 ✗
/// （审计 P2-5：early-return 不刷日志）。
pub(crate) enum TickOutcome {
    /// 没真正检查（自动检查关闭 / 读配置失败）→ 只更新界面，**不写业务日志** ✗
    NotChecked(String),
    /// 真跑了一次检查 → 调用方负责写一行业务日志 ✓
    Checked(drcom_core::runtime::CheckResult),
}

impl TickOutcome {
    /// 该不该往 `campus_login.log` 追加一行 ✓（**纯函数**，单测锁死「early-return 不刷日志」）。
    pub(crate) fn should_write_business_log(&self) -> bool {
        matches!(self, TickOutcome::Checked(_))
    }

    /// 界面那一行显示什么 ✓（两种情况的文案都已就绪）。
    pub(crate) fn display_summary(&self) -> &str {
        match self {
            TickOutcome::NotChecked(line) => line,
            TickOutcome::Checked(result) => &result.summary,
        }
    }
}

/// 跑一次后台检查：配置 Gate → **共享入口** `drcom_core::runtime::check_once` ✓。
///
/// 配置 / 账号 / 密码 / 守卫 / 登录判定**全在 core**（R7）—— 界面这层只做「读配置 + 调入口」。
fn background_check() -> TickOutcome {
    // 读配置走 legacy 兼容层（审计 P1-3）：3.0 没有配置、2.x 有时只读沿用 2.x 的账号/网关 ✓
    let cfg = match drcom_core::legacy::load_config() {
        Ok(read) => read.config,
        Err(e) => return TickOutcome::NotChecked(format!("读配置失败：{}", e)),
    };
    if !cfg.auto_check_enabled {
        return TickOutcome::NotChecked("自动检查已在设置里关闭".to_string());
    }
    TickOutcome::Checked(drcom_core::runtime::check_once(&cfg, TIMEOUT))
}

/// 「一次性后台任务 + 回调」骨架：**起线程 → 跑会阻塞的事 → 结果交给回调** ✓。
///
/// 更新检查就是这个形状（点一次跑一次，不需要常驻 —— 与 [`Worker`] 的轮询分开 ✓）。
/// 回调跑在**后台线程**里；要改界面的话，调用方在回调里用
/// `slint::invoke_from_event_loop` 把闭包丢回主线程（界面属性只能在事件循环线程改 ✓）。
///
/// 线程是 detached 的：不 join、不持句柄 —— 窗口关掉后线程照常跑完，
/// 回调里的 `Weak::upgrade()` 失败就安静丢弃结果 ✓（不 panic、不碰已释放的窗口 ✗）。
pub fn spawn_once<T, F>(work: impl FnOnce() -> T + Send + 'static, on_done: F)
where
    T: Send + 'static,
    F: FnOnce(T) + Send + 'static,
{
    thread::spawn(move || on_done(work()));
}

/// 后台跑一次「检查更新」（[`crate::model::update_check`]：会起 curl / 等网络 ✓），
/// 跑完把结果交给回调 ✓。**只有用户点「检查更新」才触发** —— 启动时绝不自动调 ✗。
pub fn spawn_update_check(on_done: impl FnOnce(drcom_core::update::UpdateCheck) + Send + 'static) {
    spawn_once(crate::model::update_check, on_done);
}

/// 后台跑一次「下载并安装」（M4 第二块：下载 → SHA256 → 解包 → 备份 → 替换 ✓）。
///
/// 会联网、会写**安装目录**（备份 + 换 exe），所以必须在后台线程跑，结果回主线程再改界面 ✓。
/// 注意：**绝不碰用户数据目录**（下载走系统临时目录、装完即删 ✓）。
pub fn spawn_update_install(
    on_done: impl FnOnce(Result<crate::model::InstalledUpdate, String>) + Send + 'static,
) {
    spawn_once(crate::model::install_update, on_done);
}

/// 后台跑一次「版本回滚」（M4 收尾）：会把备份换回安装目录 → 成功后主线程重启 ✓。
///
/// 和安装一样会写安装目录（备份 → 校验 → 换文件），所以也必须在后台线程跑 ✓。
pub fn spawn_rollback(
    version: String,
    on_done: impl FnOnce(Result<crate::model::RollbackDone, String>) + Send + 'static,
) {
    spawn_once(move || crate::model::rollback_to(&version), on_done);
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
        // 同上：便宜通道只关心网关地址，读法保持一致 ✓
        let cfg = drcom_core::legacy::load_config_or_default().config;
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
            let outcome = background_check();
            let finished = SystemTime::now();
            let clock = now_clock(finished);
            // 只有**真正检查**了才写业务日志（审计 P2-5）—— 格式与 CLI 逐字同源，
            // diagnostics / metrics::collect 直接能用 ✓；「自动检查关闭」这类 early-return
            // 不写，免得每到检查点就刷一行噪音 ✗（不是一次检查）。
            let checked = outcome.should_write_business_log();
            if checked {
                if let TickOutcome::Checked(result) = &outcome {
                    if let Err(e) = drcom_core::runtime::append_business_log(
                        &drcom_core::platform::log_dir(),
                        finished,
                        result.kind,
                        &result.summary,
                    ) {
                        // 日志写不进去不该影响这次检查的结果显示 ✓
                        eprintln!("[后台检查] {}", e);
                    }
                }
            }
            let line = format!("{}（{}）· {}", outcome.display_summary(), why, clock);
            // 界面没有控制台 ✗ —— 但这行在「重定向 stderr」时能救排障 ✓（冒烟测试也靠它 ✓）
            eprintln!("[后台检查] {}", line);
            if checked {
                set_last_at(&state, clock);
            }
            set_line(&state, line, checked);
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
    fn clock_is_hh_mm_ss_utc_with_z() {
        let clock = now_clock(std::time::SystemTime::now());
        // 审计 P2-9：必须带 Z（UTC），且与日志 timefmt::format_utc 同一口径 ✓
        assert_eq!(clock.len(), 9, "{}", clock);
        assert!(clock.ends_with('Z'), "界面时钟必须是 UTC 口径（带 Z）✓: {}", clock);
        let time_part = &clock[..8];
        let parts: Vec<&str> = time_part.split(':').collect();
        assert_eq!(parts.len(), 3, "{}", clock);
        for part in &parts {
            assert_eq!(part.len(), 2, "{}", clock);
            assert!(part.chars().all(|c| c.is_ascii_digit()), "{}", clock);
        }
        assert!(parts[0].parse::<u32>().unwrap() < 24, "{}", clock);
        assert!(parts[1].parse::<u32>().unwrap() < 60, "{}", clock);
        assert!(parts[2].parse::<u32>().unwrap() < 60, "{}", clock);
        // 与日志格式同源：format_utc(now) 里就含这段时钟 ✓
        // （前后各取一次，跨秒边界也不会偶发红 ✓）
        let before = drcom_core::timefmt::format_utc(std::time::SystemTime::now());
        let after = drcom_core::timefmt::format_utc(std::time::SystemTime::now());
        assert!(
            before.contains(time_part) || after.contains(time_part),
            "日志 {} / {} 与界面时钟 {} 口径必须一致 ✓",
            before,
            after,
            clock
        );
    }

    #[test]
    fn default_state_says_it_is_starting() {
        let state = WorkerState::default();
        assert!(state.line.contains("启动"), "{}", state.line);
        assert_eq!(state.last_at, None);
        assert!(!state.active);
    }

    #[test]
    fn only_a_real_check_merits_a_business_log_line() {
        // 审计 P2-5：真正检查 → 写一行业务日志 ✓；自动检查关闭 / 读配置失败 → 不写（别刷屏 ✗）
        let not_checked = TickOutcome::NotChecked("自动检查已在设置里关闭".to_string());
        assert!(!not_checked.should_write_business_log(), "early-return 不写日志 ✗");
        assert!(not_checked.display_summary().contains("关闭"));

        let checked = TickOutcome::Checked(drcom_core::runtime::CheckResult::new(
            drcom_core::metrics::Kind::Ok,
            "已在线，无需登录",
        ));
        assert!(checked.should_write_business_log(), "真正检查必须写业务日志 ✓");
        assert_eq!(checked.display_summary(), "已在线，无需登录");
    }

    #[test]
    fn one_shot_tasks_run_off_the_ui_thread_and_call_back() {
        // 「检查更新」走的就是这条骨架：任务在后台线程跑、结果由回调带回 ✓
        let (tx, rx) = std::sync::mpsc::channel();
        let main_thread = thread::current().id();
        spawn_once(
            move || thread::current().id(),
            move |worker_thread| {
                let _ = tx.send(worker_thread);
            },
        );
        let worker_thread =
            rx.recv_timeout(Duration::from_secs(5)).expect("后台任务必须把结果交回回调");
        assert_ne!(worker_thread, main_thread, "更新检查必须在后台线程跑，绝不能卡界面 ✗");
    }
}
