//! 退避策略 —— 与 2.x 的 `BACKOFF_LEVELS` / `_set_backoff` / `_reset_backoff` 逐条对齐：
//! 连续失败 1 次 → 5 分钟，2 次 → 10，3 次 → 20，4 次 → 40，5 次及以后 → 60（封顶）✓。

use std::time::{Duration, SystemTime};

/// 分钟档位（索引 = 连续失败次数 - 1，封顶最后一档）—— **数值必须与 2.x 一致** ✓
pub const BACKOFF_LEVELS_MIN: [u64; 5] = [5, 10, 20, 40, 60];

/// 退避状态机（纯数据，不含锁/线程 —— 上层自己决定怎么共享 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Backoff {
    pub consecutive_failures: u32,
    pub current_minutes: u64,
    until: Option<SystemTime>,
}

impl Default for Backoff {
    fn default() -> Backoff {
        Backoff {
            consecutive_failures: 0,
            current_minutes: BACKOFF_LEVELS_MIN[0],
            until: None,
        }
    }
}

impl Backoff {
    /// 计划中的下一次重试时间（没在退避里 → `None` ✓）
    pub fn until(&self) -> Option<SystemTime> {
        self.until
    }

    /// 记录一次失败：计数 +1、按档位升压、算出 `until`；返回本次要等多久 ✓
    pub fn record_failure(&mut self, now: SystemTime) -> Duration {
        let failures = self.consecutive_failures.saturating_add(1);
        let idx = (failures.saturating_sub(1) as usize).min(BACKOFF_LEVELS_MIN.len() - 1);
        let minutes = BACKOFF_LEVELS_MIN[idx];
        self.consecutive_failures = failures;
        self.current_minutes = minutes;
        let wait = Duration::from_secs(minutes * 60);
        self.until = Some(now + wait);
        wait
    }

    /// 成功（或账号未设置而跳过）→ 清空退避 ✓
    pub fn reset(&mut self) {
        self.consecutive_failures = 0;
        self.current_minutes = BACKOFF_LEVELS_MIN[0];
        self.until = None;
    }

    /// 现在是否仍在退避窗口内
    pub fn is_waiting(&self, now: SystemTime) -> bool {
        matches!(self.until, Some(t) if now < t)
    }

    /// 距下次重试还剩多久（没在退避 → 0 ✓）
    pub fn remaining(&self, now: SystemTime) -> Duration {
        match self.until {
            Some(t) if t > now => t.duration_since(now).unwrap_or_default(),
            _ => Duration::ZERO,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn levels_match_v2_exactly() {
        assert_eq!(BACKOFF_LEVELS_MIN, [5, 10, 20, 40, 60], "改这里必须同步 2.x ✗");
        assert_eq!(Backoff::default().current_minutes, 5);
    }

    #[test]
    fn escalates_then_caps_at_60() {
        let mut backoff = Backoff::default();
        let now = SystemTime::UNIX_EPOCH;
        let expected = [5u64, 10, 20, 40, 60, 60, 60];
        for (i, minutes) in expected.iter().enumerate() {
            let wait = backoff.record_failure(now);
            assert_eq!(wait, Duration::from_secs(minutes * 60), "第 {} 次失败", i + 1);
            assert_eq!(backoff.consecutive_failures as usize, i + 1);
            assert_eq!(backoff.current_minutes, *minutes);
        }
    }

    #[test]
    fn reset_clears_everything() {
        let mut backoff = Backoff::default();
        let now = SystemTime::UNIX_EPOCH;
        backoff.record_failure(now);
        backoff.record_failure(now);
        assert!(backoff.is_waiting(now + Duration::from_secs(60)));
        backoff.reset();
        assert_eq!(backoff.consecutive_failures, 0);
        assert_eq!(backoff.current_minutes, 5);
        assert!(backoff.until().is_none());
        assert!(!backoff.is_waiting(now));
        assert_eq!(backoff.remaining(now), Duration::ZERO);
    }

    #[test]
    fn waiting_window_and_remaining_are_consistent() {
        let mut backoff = Backoff::default();
        let now = SystemTime::UNIX_EPOCH;
        backoff.record_failure(now); // 等 5 分钟
        assert!(backoff.is_waiting(now + Duration::from_secs(299)));
        assert_eq!(backoff.remaining(now + Duration::from_secs(60)), Duration::from_secs(240));
        assert!(!backoff.is_waiting(now + Duration::from_secs(300)), "刚好到点就不再等 ✓");
        assert_eq!(backoff.remaining(now + Duration::from_secs(600)), Duration::ZERO);
    }

    #[test]
    fn failure_count_saturates_instead_of_overflowing() {
        let mut backoff = Backoff::default();
        backoff.consecutive_failures = u32::MAX;
        let wait = backoff.record_failure(SystemTime::UNIX_EPOCH);
        assert_eq!(backoff.consecutive_failures, u32::MAX, "不会 panic ✓");
        assert_eq!(wait, Duration::from_secs(60 * 60), "仍然封顶 60 分钟 ✓");
    }
}
