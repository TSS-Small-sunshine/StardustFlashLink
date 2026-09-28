//! 周期调度 —— 决定「什么时候再跑一次」，并把 2.x 里那套 **退避优先** 的语义固定下来：
//!
//!   - 正常：下次 = 本次结束时间 + `auto_check_interval_min` 分钟；
//!   - 失败：走 [`Backoff`]（5/10/20/40/60 分钟封顶），下次 = 退避的 `until`（**退避优先** ✓）；
//!   - 成功 / 跳过：立刻把退避清零 ✓。
//!
//! **单写者**原则（2.1.0.0 的 P7-4 修复）：`next_check_at` 只由这里写，
//! 「跑一次」的函数（`session::check_once`）绝不碰它 —— 否则界面倒计时会来回跳 ✗。

use crate::backoff::Backoff;
use std::time::{Duration, SystemTime};

/// 调度器（纯状态，不含线程/锁 ✓ —— 上层决定怎么共享）。
#[derive(Debug, Clone)]
pub struct Scheduler {
    pub interval: Duration,
    pub backoff: Backoff,
    next_check_at: Option<SystemTime>,
    last_check_at: Option<SystemTime>,
    /// 最近一次结果的文案（界面/日志直接用 ✓）
    pub last_summary: Option<String>,
    /// 一共跑过多少次（诊断用 ✓）
    pub runs: u64,
}

impl Scheduler {
    pub fn new(interval_min: u32) -> Scheduler {
        Scheduler {
            interval: minutes(interval_min),
            backoff: Backoff::default(),
            next_check_at: None,
            last_check_at: None,
            last_summary: None,
            runs: 0,
        }
    }

    /// 改检查间隔（界面改配置后调用 ✓）；已排的下一次**不动** —— 免得倒计时突然跳 ✓
    pub fn set_interval(&mut self, interval_min: u32) {
        self.interval = minutes(interval_min);
    }

    /// 下一次计划时间（还没排过 → `None` ✓）
    pub fn next_check_at(&self) -> Option<SystemTime> {
        self.next_check_at
    }

    pub fn last_check_at(&self) -> Option<SystemTime> {
        self.last_check_at
    }

    /// 现在到点了吗（没排过计划 = 立刻该跑 ✓）
    pub fn is_due(&self, now: SystemTime) -> bool {
        match self.next_check_at {
            None => true,
            Some(at) => now >= at,
        }
    }

    /// 距离下次还有多久（已过点 → 0 ✓；界面倒计时用）
    pub fn remaining(&self, now: SystemTime) -> Duration {
        match self.next_check_at {
            Some(at) if at > now => at.duration_since(now).unwrap_or_default(),
            _ => Duration::ZERO,
        }
    }

    /// 记录一次结果并排下一次。
    ///
    /// `finished_at` = 本次结束时间（退避与下一次都从它算起 ✓）。
    /// `ok` 由 [`crate::Outcome::is_ok`] 给出 ✓。
    pub fn record(&mut self, ok: bool, summary: &str, finished_at: SystemTime) {
        self.runs = self.runs.saturating_add(1);
        self.last_check_at = Some(finished_at);
        self.last_summary = Some(summary.to_string());
        if ok {
            self.backoff.reset();
        } else {
            self.backoff.record_failure(finished_at);
        }
        // 退避优先 ✓；没有退避就按间隔 ✓
        let wait = match self.backoff.until() {
            Some(until) if until > finished_at => {
                until.duration_since(finished_at).unwrap_or_default()
            }
            _ => self.interval,
        };
        self.next_check_at = Some(finished_at + wait);
    }

    /// 手动触发后重排：把下一次推后到「现在 + 间隔」✓（**不动**退避状态 ✓）
    pub fn reschedule_after_manual(&mut self, now: SystemTime) {
        if !self.backoff.is_waiting(now) {
            self.next_check_at = Some(now + self.interval);
        }
    }
}

fn minutes(value: u32) -> Duration {
    Duration::from_secs(u64::from(value.max(1)) * 60)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(secs: u64) -> SystemTime {
        SystemTime::UNIX_EPOCH + Duration::from_secs(secs)
    }

    #[test]
    fn first_run_is_immediately_due() {
        let sched = Scheduler::new(30);
        assert!(sched.is_due(at(1000)), "还没排过计划 → 立刻该跑 ✓");
        assert_eq!(sched.next_check_at(), None);
        assert_eq!(sched.remaining(at(1000)), Duration::ZERO);
    }

    #[test]
    fn healthy_run_waits_one_interval() {
        let mut sched = Scheduler::new(30);
        sched.record(true, "已在线，无需登录", at(0));
        assert_eq!(sched.next_check_at(), Some(at(30 * 60)));
        assert!(!sched.is_due(at(30 * 60 - 1)));
        assert!(sched.is_due(at(30 * 60)), "到点就跑 ✓");
        assert_eq!(sched.remaining(at(60)), Duration::from_secs(29 * 60));
        assert_eq!(sched.runs, 1);
        assert_eq!(sched.last_summary.as_deref(), Some("已在线，无需登录"));
    }

    #[test]
    fn failures_follow_backoff_levels() {
        let mut sched = Scheduler::new(30);
        let expected = [5u64, 10, 20, 40, 60, 60];
        let mut now = 0u64;
        for minutes in expected {
            sched.record(false, "网关不可达：连接超时", at(now));
            assert_eq!(
                sched.next_check_at(),
                Some(at(now + minutes * 60)),
                "退避优先（{} 分钟）✓",
                minutes
            );
            now += minutes * 60; // 假装到点再跑一次
        }
        assert_eq!(sched.backoff.consecutive_failures, 6);
    }

    #[test]
    fn success_clears_backoff_immediately() {
        let mut sched = Scheduler::new(30);
        sched.record(false, "失败 1", at(0));
        sched.record(false, "失败 2", at(600));
        assert_eq!(sched.backoff.consecutive_failures, 2);
        sched.record(true, "登录成功", at(2000));
        assert_eq!(sched.backoff.consecutive_failures, 0, "成功即清零 ✓");
        assert_eq!(sched.next_check_at(), Some(at(2000 + 30 * 60)), "回到正常间隔 ✓");
        assert!(sched.backoff.until().is_none());
    }

    #[test]
    fn interval_change_does_not_move_the_pending_deadline() {
        let mut sched = Scheduler::new(30);
        sched.record(true, "ok", at(0));
        let before = sched.next_check_at();
        sched.set_interval(60);
        assert_eq!(sched.next_check_at(), before, "不让倒计时突然跳 ✓");
        // 但下一次排程用新间隔 ✓
        sched.record(true, "ok", at(1800));
        assert_eq!(sched.next_check_at(), Some(at(1800 + 60 * 60)));
    }

    #[test]
    fn manual_run_reschedules_without_touching_backoff() {
        let mut sched = Scheduler::new(30);
        sched.reschedule_after_manual(at(100));
        assert_eq!(sched.next_check_at(), Some(at(100 + 1800)));
        // 退避窗口内手动跑：不把计划提前 ✗
        sched.record(false, "失败", at(2000));
        let planned = sched.next_check_at();
        sched.reschedule_after_manual(at(2100));
        assert_eq!(sched.next_check_at(), planned, "退避期间不该被手动跑覆盖 ✓");
    }

    #[test]
    fn interval_is_never_zero() {
        let sched = Scheduler::new(0);
        assert_eq!(sched.interval, Duration::from_secs(60), "至少 1 分钟 ✓");
    }

    #[test]
    fn last_check_at_is_recorded_for_ui_countdown() {
        let mut sched = Scheduler::new(15);
        assert!(sched.last_check_at().is_none());
        sched.record(true, "ok", at(7200));
        assert_eq!(sched.last_check_at(), Some(at(7200)));
        assert_eq!(sched.remaining(at(7200)), Duration::from_secs(15 * 60));
    }
}
