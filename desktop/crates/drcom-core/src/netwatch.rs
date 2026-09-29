//! 网络变化监视：**「刚连上 Wi-Fi 就立刻登录」，而不是干等下一个周期** ✓
//!
//! 痛点（2.x 与 3.0 都有 ✗）：检查是**按周期**跑的（默认 30 分钟），
//! 开机 / 睡醒 / 走到别的 Wi-Fi 之后，哪怕网络早就通了，也得等到下一个周期才登录 ✗。
//!
//! 这里的办法：把「等周期」换成**小步快跑** ✓
//!   - 每 [`CHEAP_TICK_SECS`] 秒做一次**不起进程**的探测（UDP connect 选路由 ✓，微秒级 ✓）
//!     看**主用地址**变没变 —— 新拿到地址 = 刚连上网 ✓ → 立刻检查 ✓
//!   - 每 [`SSID_TICK_SECS`] 秒才做一次**要起进程**的 SSID 探测（`netsh` / `nmcli` ✓）
//!     Wi-Fi 名变了 = **换场景**了 ✓ → 立刻检查（并让方案自适应跟着切 ✓）
//!
//! 判定全是纯函数（[`Watcher`]）→ 单测覆盖 ✓；真探测由调用方喂进来（好注入 ✓）。

/// 便宜通道的间隔（秒）：只做 UDP connect，**不起任何进程** ✓。
pub const CHEAP_TICK_SECS: u64 = 2;
/// 贵通道的间隔（秒）：要起 `netsh` / `nmcli` 这类进程，别太勤 ✓。
pub const SSID_TICK_SECS: u64 = 10;

/// 该立刻检查的原因 ✓（日志里要写清楚，不然用户不知道为啥突然登了一次 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Trigger {
    /// 进程刚起来 ✓
    FirstRun,
    /// 地址变了（从没有 → 有，或者换了地址 ✓）
    NewAddress { from: String, to: String },
    /// Wi-Fi 名变了（换场景 ✓）
    SsidChanged { from: String, to: String },
}

impl Trigger {
    /// 给日志/命令行的一行中文 ✓。
    pub fn summary_cn(&self) -> String {
        match self {
            Trigger::FirstRun => "启动后首次检查".to_string(),
            Trigger::NewAddress { from, to } => format!("网络地址变化（{} → {}）", from, to),
            Trigger::SsidChanged { from, to } => format!("Wi-Fi 变化（{} → {}）", from, to),
        }
    }
}

/// 一次观察的结论 ✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Decision {
    /// 没变化 → 按原周期办事就行 ✓
    Idle,
    /// **立刻**检查 ✓
    CheckNow(Trigger),
}

impl Decision {
    pub fn is_check_now(&self) -> bool {
        matches!(self, Decision::CheckNow(_))
    }

    /// 取出原因 ✓（`Idle` → None ✓）。
    pub fn trigger(&self) -> Option<&Trigger> {
        match self {
            Decision::CheckNow(trigger) => Some(trigger),
            Decision::Idle => None,
        }
    }
}

/// 记住「上一次看到的网络长什么样」✓。
#[derive(Debug, Default, Clone)]
pub struct Watcher {
    address: Option<String>,
    ssid: Option<String>,
}

impl Watcher {
    pub fn new() -> Self {
        Watcher::default()
    }

    /// 进程启动时的第一次观察 → **立刻检查一次** ✓（这也是「开机就登」的关键 ✓）。
    pub fn start(&mut self, address: Option<String>) -> Decision {
        self.address = address.clone();
        Decision::CheckNow(Trigger::FirstRun)
    }

    /// 便宜通道：喂一次主用地址 ✓（没变 → `Idle` ✓）。
    pub fn observe_address(&mut self, address: Option<String>) -> Decision {
        let previous = self.address.clone();
        if previous == address {
            return Decision::Idle;
        }
        self.address = address.clone();
        match (previous, address) {
            // 从「没有地址」到「有地址」＝ 刚连上网 ✓
            (None, Some(to)) => Decision::CheckNow(Trigger::NewAddress {
                from: "（无）".to_string(),
                to,
            }),
            // 地址换了（换网 / 重新分配 ✓）
            (Some(from), Some(to)) => Decision::CheckNow(Trigger::NewAddress { from, to }),
            // 失去网络 → 什么都不用做（检查也会被守卫拦下 ✓）
            (Some(_), None) => Decision::Idle,
            (None, None) => Decision::Idle,
        }
    }

    /// 贵通道：喂一次 Wi-Fi 名 ✓（`None` = 这轮没探到 ✗ 不算变化 ✓）。
    pub fn observe_ssid(&mut self, ssid: Option<String>) -> Decision {
        let Some(current) = ssid else {
            return Decision::Idle; // 读不到就别乱判 ✓（与守卫的 fail-open 一个口径 ✓）
        };
        let previous = self.ssid.clone();
        if previous.as_deref() == Some(current.as_str()) {
            return Decision::Idle;
        }
        self.ssid = Some(current.clone());
        match previous {
            Some(from) => Decision::CheckNow(Trigger::SsidChanged { from, to: current }),
            // 第一次探到名字 → 不算「换场景」（启动那次已经检查过了 ✓）
            None => Decision::Idle,
        }
    }

    /// 现在记着的地址 / Wi-Fi 名 ✓（给界面显示用 ✓）。
    pub fn snapshot(&self) -> (Option<String>, Option<String>) {
        (self.address.clone(), self.ssid.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn addr(value: &str) -> Option<String> {
        Some(value.to_string())
    }

    #[test]
    fn startup_checks_immediately() {
        let mut watcher = Watcher::new();
        assert_eq!(
            watcher.start(addr("172.16.59.11")),
            Decision::CheckNow(Trigger::FirstRun),
            "开机就要先查一次 ✓"
        );
    }

    #[test]
    fn getting_an_address_for_the_first_time_triggers_a_check() {
        let mut watcher = Watcher::new();
        watcher.start(None); // 开机时还没网 ✓
        assert!(!watcher.observe_address(None).is_check_now(), "还是没网 → 不折腾 ✓");
        let decision = watcher.observe_address(addr("172.16.59.11"));
        assert!(decision.is_check_now(), "{:?}", decision);
        assert_eq!(
            decision.trigger(),
            Some(&Trigger::NewAddress {
                from: "（无）".to_string(),
                to: "172.16.59.11".to_string()
            }),
            "原因要写清「从无到有」✓"
        );
        assert!(
            !watcher.observe_address(addr("172.16.59.11")).is_check_now(),
            "同一个地址不该反复触发 ✓"
        );
    }

    #[test]
    fn address_change_triggers_but_losing_network_does_not() {
        let mut watcher = Watcher::new();
        watcher.start(addr("10.0.0.5"));
        assert!(watcher.observe_address(addr("172.16.30.7")).is_check_now(), "换网要立刻查 ✓");
        assert!(
            !watcher.observe_address(None).is_check_now(),
            "断网不用查（真查也会被守卫拦住 ✓）"
        );
        assert!(!watcher.observe_address(None).is_check_now());
        assert!(
            watcher.observe_address(addr("172.16.30.7")).is_check_now(),
            "重新连上要立刻查 ✓"
        );
    }

    #[test]
    fn ssid_change_is_a_scenario_switch() {
        let mut watcher = Watcher::new();
        watcher.start(addr("172.16.59.11"));
        assert!(!watcher.observe_ssid(None).is_check_now(), "读不到 Wi-Fi 名不算变化 ✓");
        assert!(
            !watcher.observe_ssid(Some("Campus-WiFi".into())).is_check_now(),
            "第一次探到名字不算「换场景」✓（启动那次已经查过 ✓）"
        );
        let decision = watcher.observe_ssid(Some("Dorm-WiFi".into()));
        assert!(decision.is_check_now(), "{:?}", decision);
        assert_eq!(
            decision.trigger().unwrap().summary_cn(),
            "Wi-Fi 变化（Campus-WiFi → Dorm-WiFi）"
        );
        assert!(!watcher.observe_ssid(Some("Dorm-WiFi".into())).is_check_now(), "同名不重复触发 ✓");
        assert!(!watcher.observe_ssid(None).is_check_now(), "中间读不到 → 不判变化 ✓");
        assert!(
            !watcher.observe_ssid(Some("Dorm-WiFi".into())).is_check_now(),
            "读不到之后回到同名 → 也不算变化 ✓"
        );
    }

    #[test]
    fn cadences_stay_in_step() {
        assert!(
            (1..=5).contains(&CHEAP_TICK_SECS),
            "便宜通道要秒级才叫「立刻」✓"
        );
        assert!(
            SSID_TICK_SECS >= CHEAP_TICK_SECS * 2,
            "贵通道要明显更慢（别每 2 秒起一次进程 ✗）"
        );
        assert_eq!(SSID_TICK_SECS % CHEAP_TICK_SECS, 0, "两个节拍要能对齐 ✓");
        assert_eq!(SSID_TICK_SECS / CHEAP_TICK_SECS, 5, "5 个便宜 tick = 1 个贵 tick ✓");
    }

    #[test]
    fn snapshot_reports_what_we_saw() {
        let mut watcher = Watcher::new();
        watcher.start(addr("1.2.3.4"));
        watcher.observe_ssid(Some("X".into()));
        assert_eq!(watcher.snapshot(), (addr("1.2.3.4"), Some("X".to_string())));
    }
}
