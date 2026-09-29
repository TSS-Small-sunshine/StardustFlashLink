//! 极简时间格式化 —— **不引 chrono**（体积优先 ✓），只做日志/文件名需要的事。
//!
//! 说明：std 拿不到本地时区（没有 `localtime` ✗），所以这里输出 **UTC** 并带 `Z` 后缀 ✓。
//! 界面上的「本地时间」由系统/界面层自己负责（M2 再补 ✓）。

use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// `2026-09-28 04:12:33Z`（UTC ✓）
pub fn format_utc(time: SystemTime) -> String {
    let (year, month, day, hour, minute, second) = parts_utc(time);
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}Z",
        year, month, day, hour, minute, second
    )
}

/// 拆成 `(年, 月, 日, 时, 分, 秒)`（UTC ✓）—— ZIP 的 DOS 时间字段、按天分桶都要它 ✓。
pub fn parts_utc(time: SystemTime) -> (i64, u32, u32, u32, u32, u32) {
    let secs = time.duration_since(UNIX_EPOCH).unwrap_or(Duration::ZERO).as_secs();
    let (year, month, day) = civil_from_days((secs / 86_400) as i64);
    let rest = secs % 86_400;
    (year, month, day, (rest / 3600) as u32, ((rest % 3600) / 60) as u32, (rest % 60) as u32)
}

/// 只要日期部分：`2026-09-28` ✓（按天统计 / 比较大小都用它，字符串字典序 = 时间序 ✓）
pub fn date_utc(time: SystemTime) -> String {
    let (year, month, day, ..) = parts_utc(time);
    format!("{:04}-{:02}-{:02}", year, month, day)
}

/// 文件名安全的时间戳：`20260928-041233` ✓
pub fn compact_utc(time: SystemTime) -> String {
    format_utc(time)
        .chars()
        .filter(|c| c.is_ascii_digit())
        .collect::<String>()
        .chars()
        .take(15)
        .enumerate()
        .map(|(i, c)| if i == 8 { format!("-{}", c) } else { c.to_string() })
        .collect()
}

/// 「1 小时 5 分钟」「42 秒」这种给人看的中文时长 ✓
pub fn human_duration(duration: Duration) -> String {
    let secs = duration.as_secs();
    if secs < 60 {
        return format!("{} 秒", secs);
    }
    let minutes = secs / 60;
    if minutes < 60 {
        let rest = secs % 60;
        return if rest == 0 {
            format!("{} 分钟", minutes)
        } else {
            format!("{} 分 {} 秒", minutes, rest)
        };
    }
    let hours = minutes / 60;
    let rest_min = minutes % 60;
    if hours < 24 {
        return if rest_min == 0 {
            format!("{} 小时", hours)
        } else {
            format!("{} 小时 {} 分", hours, rest_min)
        };
    }
    let days = hours / 24;
    let rest_hours = hours % 24;
    if rest_hours == 0 {
        format!("{} 天", days)
    } else {
        format!("{} 天 {} 小时", days, rest_hours)
    }
}

/// Howard Hinnant 的 `civil_from_days`（公开域算法 ✓）：把「1970 起的天数」变成年月日。
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m as u32, d as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(secs: u64) -> SystemTime {
        UNIX_EPOCH + Duration::from_secs(secs)
    }

    #[test]
    fn formats_known_utc_timestamps() {
        // 期望值全部用 `python -c "datetime.fromtimestamp(x, timezone.utc)"` 核过 ✓
        assert_eq!(format_utc(at(0)), "1970-01-01 00:00:00Z");
        assert_eq!(format_utc(at(1_000_000_000)), "2001-09-09 01:46:40Z");
        assert_eq!(format_utc(at(1_774_732_800)), "2026-03-28 21:20:00Z");
        // 闰年 2 月 29 日 ✓
        assert_eq!(format_utc(at(1_709_164_800)), "2024-02-29 00:00:00Z");
        // 跨年边界 ✓
        assert_eq!(format_utc(at(1_767_225_599)), "2025-12-31 23:59:59Z");
    }

    #[test]
    fn compact_is_filename_safe() {
        let text = compact_utc(at(1_774_732_800));
        assert_eq!(text, "20260328-212000");
        assert!(text.chars().all(|c| c.is_ascii_digit() || c == '-'));
        assert_eq!(text.len(), 15);
    }

    #[test]
    fn human_duration_is_readable() {
        assert_eq!(human_duration(Duration::from_secs(0)), "0 秒");
        assert_eq!(human_duration(Duration::from_secs(59)), "59 秒");
        assert_eq!(human_duration(Duration::from_secs(60)), "1 分钟");
        assert_eq!(human_duration(Duration::from_secs(90)), "1 分 30 秒");
        assert_eq!(human_duration(Duration::from_secs(3600)), "1 小时");
        assert_eq!(human_duration(Duration::from_secs(3900)), "1 小时 5 分");
        assert_eq!(human_duration(Duration::from_secs(86_400)), "1 天");
        assert_eq!(human_duration(Duration::from_secs(90_000)), "1 天 1 小时");
    }

    #[test]
    fn parts_and_date_helpers_are_consistent_with_format() {
        let t = UNIX_EPOCH + Duration::from_secs(1_774_732_800);
        assert_eq!(parts_utc(t), (2026, 3, 28, 21, 20, 0));
        assert_eq!(date_utc(t), "2026-03-28");
        assert!(format_utc(t).starts_with(&date_utc(t)), "两者必须一致 ✓");
        let t2 = UNIX_EPOCH + Duration::from_secs(1_709_164_800); // 2024-02-29
        assert_eq!(parts_utc(t2), (2024, 2, 29, 0, 0, 0));
        assert_eq!(date_utc(t2), "2024-02-29");
    }

    #[test]
    fn future_and_pre_epoch_times_do_not_panic() {
        // 系统时间早于 1970（极端环境）也不该 panic ✓
        let _ = format_utc(UNIX_EPOCH - Duration::from_secs(1000));
        let _ = format_utc(SystemTime::now());
        assert_eq!(human_duration(Duration::ZERO), "0 秒");
    }
}
