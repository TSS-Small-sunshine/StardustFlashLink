//! 连接质量统计 —— 从**轮转后的业务日志**里现算（与 2.x `metrics.py` 同一个思路 ✓）。
//!
//! 日志行格式（3.0 起钉死，带一个机器可读的等级标记 ✓）：
//! ```text
//! 2026-09-28 15:14:15Z [ok]   已在线，无需登录
//! 2026-09-28 15:14:15Z [fail] 网关不可达：连接超时
//! 2026-09-28 15:14:15Z [skip] 不在校园网（SSID / 网段都不在白名单里）
//! ```
//!
//! 为什么加 `[ok]/[fail]/[skip]`：2.x 只能靠中文关键字猜结果 ✗ → 界面统计容易漏 ✗。
//! 现在**等级是结构化字段**，中文文案怎么改都不影响统计 ✓；老格式（没标记）算 `Other`，
//! 不计入成功率 ✓（既不吹高也不压低 ✓）。
//!
//! **也认 2.x 的旧日志格式**（审计 P3-13）：`[YYYY-MM-DD HH:MM:SS] [LEVEL] 正文`
//! （2.x 的 `logging.Formatter` 口径，本地时区、等级在正文里）。把旧 `campus_login.log`
//! 拷过来看统计时，面板不再是空的 ✓；2.x 的结局行按 2.x `metrics.py` 的常量表归类
//! （见 [`parse_line`] 的说明 ✓）。
//!
//! 口径：**success_rate = ok / (ok + fail)** —— `skip`（不在校园网 / 账号未设置）不算失败 ✓。

use std::time::{Duration, SystemTime};

/// 一行日志的等级。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// 健康（已在线 / 登录成功）
    Ok,
    /// 失败（网关不可达 / 密码错 / 被拒）
    Fail,
    /// 跳过（不在校园网 / 账号未设置）—— **不算失败** ✓
    Skip,
    /// 没标记的历史行
    Other,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Ok => "ok",
            Kind::Fail => "fail",
            Kind::Skip => "skip",
            Kind::Other => "other",
        }
    }
}

/// 解析出来的一行。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    /// `2026-09-28`（字典序 = 时间序 ✓）
    pub date: String,
    /// 完整时间戳（3.0 带 `Z`；2.x 旧日志原样不带 —— 只用于排序与显示 ✓）
    pub stamp: String,
    pub kind: Kind,
    /// 标记之后的正文（界面直接显示 ✓）
    pub text: String,
}

/// 解析一行日志；认不出时间戳 → `None` ✓（静默跳过，不让统计炸掉 ✓）。
///
/// 认**两种**格式（审计 P3-13）：
///   1. 3.0：`2026-09-28 15:14:15Z [ok] 已在线，无需登录`（UTC、等级是结构化字段 ✓）
///   2. 2.x：`[2026-09-28 07:28:05] [INFO] 已在线，无需登录`（`logging.Formatter` 的
///      `[%(asctime)s] [%(levelname)s] %(message)s`，本地时区；等级从**正文**归类 ✓）
///
/// 2.x 的正文按 2.x `metrics.py` 的常量表归类（同一份口径 ✓）：
///   - `已在线，无需登录` / `登录成功:` → ok
///   - `登录失败:` → fail（**带冒号才算一次结局** —— 紧跟在后面的
///     `登录失败，进入退避：…` 不会被重复计成第二次失败 ✓）
///   - `网络位置不在白名单` / `密码未设置，跳过登录` / `账号未设置，跳过登录` → skip
///   - `等待 Ns 后 … 仍不可达` → **fail**（审计 E：2.x `metrics.py` 的 `EV_UNREACHABLE`
///     把这一行算成不健康 —— 周期结局是 `unreachable`，`series.fail` 把它并进失败、
///     uptime 的分母也照算；3.0 再当 unmarked 就会**漏掉「网一直不通」**，成功率虚高 ✗）
///   - 其它（`开始检查 (reason=…)` / `等待中 (N): …` / 自动切方案 …）→ `Other`
///     （不计入成功率 —— 宁可少算，不算错 ✓）
pub fn parse_line(line: &str) -> Option<Record> {
    let line = line.trim_end();
    parse_v3_line(line).or_else(|| parse_v2_line(line))
}

/// 3.0 格式：`YYYY-MM-DD HH:MM:SSZ [ok|fail|skip] 正文`
fn parse_v3_line(line: &str) -> Option<Record> {
    // 时间戳固定 20 字符：`YYYY-MM-DD HH:MM:SSZ`
    if line.len() < 21 {
        return None;
    }
    // 用 get 而不是切片：坏行里若有中文跨过第 20 个字节，切片会 panic ✗（诊断包不许崩 ✓）
    let stamp = line.get(..20)?;
    if stamp.as_bytes().get(4) != Some(&b'-')
        || stamp.as_bytes().get(10) != Some(&b' ')
        || !stamp.ends_with('Z')
    {
        return None;
    }
    let date = stamp.get(..10)?.to_string();
    if !date
        .chars()
        .enumerate()
        .all(|(i, c)| if i == 4 || i == 7 { c == '-' } else { c.is_ascii_digit() })
    {
        return None;
    }
    let rest = line.get(20..)?.trim_start();
    let (kind, text) = match rest.split_once(']') {
        Some((head, tail)) if head.starts_with('[') => {
            let name = head.trim_start_matches('[').trim();
            let kind = match name {
                "ok" => Kind::Ok,
                "fail" => Kind::Fail,
                "skip" => Kind::Skip,
                _ => return None, // 未知标记：宁可当坏行丢掉，也不瞎归类 ✗
            };
            (kind, tail.trim().to_string())
        }
        _ => (Kind::Other, rest.to_string()),
    };
    Some(Record { date, stamp: stamp.to_string(), kind, text })
}

/// 2.x 格式：`[YYYY-MM-DD HH:MM:SS] [LEVEL] 正文`（本地时区、无 `Z`）
fn parse_v2_line(line: &str) -> Option<Record> {
    let rest = line.strip_prefix('[')?;
    let (stamp, after) = rest.split_once(']')?;
    if !is_plain_stamp(stamp) {
        return None;
    }
    let after = after.trim_start();
    let level_body = after.strip_prefix('[')?;
    let (_level, body) = level_body.split_once(']')?;
    let text = body.trim();
    Some(Record {
        date: stamp.get(..10)?.to_string(),
        stamp: stamp.to_string(),
        kind: classify_v2(text),
        text: text.to_string(),
    })
}

/// `YYYY-MM-DD HH:MM:SS`（19 字符，**无 Z** —— 2.x 的本地时间戳 ✓）
fn is_plain_stamp(stamp: &str) -> bool {
    let bytes = stamp.as_bytes();
    if bytes.len() != 19 {
        return false;
    }
    bytes.iter().enumerate().all(|(index, byte)| match index {
        4 | 7 => *byte == b'-',
        10 => *byte == b' ',
        13 | 16 => *byte == b':',
        _ => byte.is_ascii_digit(),
    })
}

/// 2.x 正文 → 等级（与 2.x `metrics.py` 的 `EV_*` 常量逐字对齐 ✓）。
fn classify_v2(text: &str) -> Kind {
    /// 2.x `metrics.py` 的 `EV_UNREACHABLE`：「等待 Ns 后 … 仍不可达」→ 不健康 ✓
    /// （2.x `protocol.py` 里是 `_log("等待 %ss 后 %s:%s 仍不可达", …, level=logging.ERROR)`）
    const UNREACHABLE: &str = "仍不可达";
    /// 2.x `metrics.py` 的 `EV_SKIP` 三条（守卫跳过 / 密码未设置 / 账号未设置）✓
    const SKIP_PREFIXES: [&str; 3] =
        ["网络位置不在白名单", "密码未设置，跳过登录", "账号未设置，跳过登录"];
    if text.starts_with("已在线，无需登录") || text.starts_with("登录成功:") {
        Kind::Ok
    } else if text.starts_with("登录失败:") || text.contains(UNREACHABLE) {
        // 审计 E：2.x 把「仍不可达」算成不健康（unreachable 进 series.fail / uptime 分母），
        // 3.0 的 2.x 兼容解析必须同口径，不能当 unmarked 漏掉 ✗
        Kind::Fail
    } else if SKIP_PREFIXES.iter().any(|prefix| text.starts_with(prefix)) {
        Kind::Skip
    } else {
        Kind::Other
    }
}

/// 一天的统计。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DayStat {
    pub date: String,
    pub ok: u32,
    pub fail: u32,
    pub skip: u32,
}

/// 一段时间窗口内的连接质量。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Metrics {
    /// 按日期升序，只含窗口内的天 ✓
    pub days: Vec<DayStat>,
    pub ok: u32,
    pub fail: u32,
    pub skip: u32,
    /// 没标记的历史行（不计入成功率 ✓）
    pub unmarked: u32,
    /// `ok / (ok + fail)`，四舍五入到两位小数；分母为 0 → `None` ✓
    pub success_rate: Option<u32>, // 以「万分之一」为单位，避免浮点误差 ✓
    pub first_at: Option<String>,
    pub last_at: Option<String>,
    pub last_summary: Option<String>,
    /// 窗口天数（界面显示「近 N 天」✓）
    pub window_days: u32,
}

impl Metrics {
    /// 成功率的百分比文案：`80.0%` / `—`（没有可判定的样本时 ✓）
    pub fn rate_text(&self) -> String {
        match self.success_rate {
            Some(rate) => format!("{:.1}%", rate as f64 / 100.0),
            None => "—".to_string(),
        }
    }

    /// 一行总览（界面 / CLI 都能直接用 ✓）
    pub fn summary_cn(&self) -> String {
        format!(
            "近 {} 天：成功 {} · 失败 {} · 跳过 {}（成功率 {}）",
            self.window_days,
            self.ok,
            self.fail,
            self.skip,
            self.rate_text()
        )
    }

    /// 是否没有任何可判定样本（界面显示「暂无数据」✓）
    pub fn is_empty(&self) -> bool {
        self.ok == 0 && self.fail == 0 && self.skip == 0 && self.unmarked == 0
    }
}

/// 统计最近 `window_days` 天（含今天 ✓）；`now` 用来算窗口起点 ✓。
///
/// 输入可以是**多份轮转文件拼起来的行**（最旧 → 最新 ✓）—— 内部会自己排序 ✓。
pub fn collect(lines: &[String], window_days: u32, now: SystemTime) -> Metrics {
    let window_days = window_days.max(1);
    let start = now
        .checked_sub(Duration::from_secs(u64::from(window_days - 1) * 86_400))
        .unwrap_or(SystemTime::UNIX_EPOCH);
    let cutoff = crate::timefmt::date_utc(start); // `YYYY-MM-DD` 字符串比较即可 ✓

    let mut records: Vec<Record> = lines.iter().filter_map(|line| parse_line(line)).collect();
    records.retain(|record| record.date.as_str() >= cutoff.as_str());
    records.sort_by(|a, b| a.stamp.cmp(&b.stamp));

    let mut days: Vec<DayStat> = Vec::new();
    let (mut ok, mut fail, mut skip, mut unmarked) = (0u32, 0u32, 0u32, 0u32);
    for record in &records {
        if days.last().map(|day| day.date.as_str()) != Some(record.date.as_str()) {
            days.push(DayStat { date: record.date.clone(), ..DayStat::default() });
        }
        let day = days.last_mut().expect("刚 push 过 ✓");
        match record.kind {
            Kind::Ok => {
                ok += 1;
                day.ok += 1;
            }
            Kind::Fail => {
                fail += 1;
                day.fail += 1;
            }
            Kind::Skip => {
                skip += 1;
                day.skip += 1;
            }
            Kind::Other => unmarked += 1,
        }
    }

    let decided = ok + fail;
    let success_rate = if decided == 0 {
        None
    } else {
        Some(((u64::from(ok) * 10_000 + u64::from(decided) / 2) / u64::from(decided)) as u32)
    };

    Metrics {
        days,
        ok,
        fail,
        skip,
        unmarked,
        success_rate,
        first_at: records.first().map(|r| r.stamp.clone()),
        last_at: records.last().map(|r| r.stamp.clone()),
        last_summary: records.last().map(|r| r.text.clone()),
        window_days,
    }
}

/// 把一次检查的结果写成日志行（**CLI 与界面共用同一格式** ✓）。
pub fn log_line(time: SystemTime, kind: Kind, summary: &str) -> String {
    format!("{} [{}] {}", crate::timefmt::format_utc(time), kind.as_str(), summary)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::UNIX_EPOCH;

    fn at(secs: u64) -> SystemTime {
        UNIX_EPOCH + Duration::from_secs(secs)
    }

    fn lines(text: &[&str]) -> Vec<String> {
        text.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn parses_all_four_kinds_shape() {
        let record = parse_line("2026-09-28 15:14:15Z [ok] 已在线，无需登录").unwrap();
        assert_eq!(record.date, "2026-09-28");
        assert_eq!(record.stamp, "2026-09-28 15:14:15Z");
        assert_eq!(record.kind, Kind::Ok);
        assert_eq!(record.text, "已在线，无需登录");

        assert_eq!(
            parse_line("2026-09-28 15:14:15Z [fail] 网关不可达：连接超时").unwrap().kind,
            Kind::Fail
        );
        assert_eq!(
            parse_line("2026-09-28 15:14:15Z [skip] 不在校园网").unwrap().kind,
            Kind::Skip
        );
        // 老格式（没标记）算 Other，且正文原样保留 ✓
        let old = parse_line("2026-09-28 15:14:15Z 已在线，无需登录").unwrap();
        assert_eq!(old.kind, Kind::Other);
        assert_eq!(old.text, "已在线，无需登录");
    }

    #[test]
    fn parses_the_real_two_x_log_format() {
        // 夹具取自 2.x 仓库的**真实日志形状**（不是拍脑袋编的）：
        // 时间戳/等级格式见 2.x `联网_service.py:253-255` 的 logging.Formatter
        // （`[%(asctime)s] [%(levelname)s] %(message)s`，`%Y-%m-%d %H:%M:%S`）；
        // 正文文案见 2.x `protocol.py:432-517` 与 `_smoke_static.py:371-377` /
        // `_smoke_http.py:123-131` 的样本 ✓
        let online = parse_line("[2026-09-28 07:00:00] [INFO] 已在线，无需登录").unwrap();
        assert_eq!(online.kind, Kind::Ok);
        assert_eq!(online.date, "2026-09-28");
        assert_eq!(online.stamp, "2026-09-28 07:00:00", "2.x 时间戳原样保留（不带 Z）✓");
        assert_eq!(online.text, "已在线，无需登录");

        let login_ok =
            parse_line("[2026-09-28 07:28:06] [INFO] 登录成功: Portal协议认证成功！").unwrap();
        assert_eq!(login_ok.kind, Kind::Ok);

        let fail =
            parse_line("[2026-09-28 07:28:05] [ERROR] 登录失败: 账号不存在或账号未绑定宽带").unwrap();
        assert_eq!(fail.kind, Kind::Fail);

        let skip = parse_line(
            "[2026-09-28 07:28:05] [INFO] 网络位置不在白名单（SSID=6#119 / IP=172.16.80.77），跳过本次检查",
        )
        .unwrap();
        assert_eq!(skip.kind, Kind::Skip);
        assert!(skip.text.contains("SSID=6#119"), "{}", skip.text);

        // 「登录失败，进入退避」不是第二次失败（2.x metrics.py 也只认带冒号的结局 ✓）
        let backoff = parse_line(
            "[2026-09-28 07:28:05] [WARNING] 登录失败，进入退避：连续 1 次，下次重试 5 分钟后（…）",
        )
        .unwrap();
        assert_eq!(backoff.kind, Kind::Other);

        // 2.x metrics.py 的 EV_SKIP 两条（密码 / 账号未设置）
        assert_eq!(
            parse_line("[2026-09-28 07:31:00] [WARNING] 密码未设置，跳过登录。请通过 Web UI 设置密码。")
                .unwrap()
                .kind,
            Kind::Skip
        );
        assert_eq!(
            parse_line("[2026-09-28 07:31:00] [WARNING] 账号未设置，跳过登录。请在 Web UI 的「配置 → 账户」里填写学号 / 工号。")
                .unwrap()
                .kind,
            Kind::Skip
        );

        // 周期开始 / 可达探测 / 等待过程中的重试行：都不是结局 → Other（不计入成功率 ✓）
        for line in [
            "[2026-09-28 07:28:05] [INFO] 开始检查 (reason=periodic)",
            "[2026-09-28 07:30:00] [INFO] 网络已可达（第 1 次尝试，耗时 0.02s）",
            "[2026-09-28 07:30:58] [INFO] 等待中 (1): timed out",
        ] {
            assert_eq!(parse_line(line).unwrap().kind, Kind::Other, "{}", line);
        }

        // 审计 E：2.x 的「仍不可达」是**结局**（`EV_UNREACHABLE`）→ fail，不是 unmarked ✓
        assert_eq!(
            parse_line("[2026-09-28 07:31:00] [ERROR] 等待 60s 后 172.16.80.3:80 仍不可达")
                .unwrap()
                .kind,
            Kind::Fail
        );

        // 空正文的 2.x 行也是合法日志（logging.info("")）→ Other，不是坏行 ✓
        let empty_body = parse_line("[2026-09-28 07:00:00] [INFO]").unwrap();
        assert_eq!(empty_body.kind, Kind::Other);
        assert_eq!(empty_body.text, "");

        // 3.0 自己那套格式照旧（两种格式同时认 ✓）
        assert_eq!(parse_line("2026-09-28 15:14:15Z [ok] 已在线，无需登录").unwrap().kind, Kind::Ok);
        // 2.x 的「缺新字段老行」也要认（v2.0.8.0 前后格式没变过 ✓）
        assert_eq!(
            parse_line("[2026-09-28 07:00:00] [INFO] 已在线，无需登录").unwrap().kind,
            Kind::Ok
        );
    }

    #[test]
    fn two_x_unreachable_counts_as_a_failure_like_two_x_metrics_does() {
        // 审计 E 的真实夹具：2.x `protocol.py:148` 的
        // `_log("等待 %ss 后 %s:%s 仍不可达", timeout, host, port, level=logging.ERROR)`
        // 对应 `metrics.py` 的 `EV_UNREACHABLE` —— 2.x 在 `summarize()` 里把它算成
        // 不健康（周期结局 unreachable，series.fail 并进失败，uptime 分母照算）。
        let line = "[2026-09-28 07:31:00] [ERROR] 等待 60s 后 172.16.80.3:80 仍不可达";
        let record = parse_line(line).expect("2.x 行必须能解析 ✓");
        assert_eq!(record.kind, Kind::Fail, "2.x 口径：仍不可达 = 不健康 ✗: {:?}", record);
        assert_eq!(record.stamp, "2026-09-28 07:31:00", "2.x 时间戳原样保留（不带 Z）✓");
        assert!(record.text.contains("仍不可达"));

        // 与一次成功混在一起：成功率 = 1/(1+1) = 50%（unreachable 进分母，不是被漏掉 ✓）
        let report = collect(
            &lines(&["[2026-09-28 07:00:00] [INFO] 已在线，无需登录", line]),
            7,
            at(1_790_630_400),
        );
        assert_eq!((report.ok, report.fail, report.skip, report.unmarked), (1, 1, 0, 0));
        assert_eq!(report.rate_text(), "50.0%", "2.x 把仍不可达算进不健康 ✓");
    }

    #[test]
    fn a_copied_two_x_log_shows_real_numbers_instead_of_an_empty_panel() {
        // 审计 P3-13 的目的：把 2.x 的旧 logs 拷过来 → 统计不再全 0 ✓
        let report = collect(
            &lines(&[
                "[2026-09-28 07:00:00] [INFO] 已在线，无需登录",
                "[2026-09-28 07:00:01] [INFO] 开始检查 (reason=periodic)",
                "[2026-09-28 07:10:00] [INFO] 网络位置不在白名单（SSID=6#119 / IP=172.16.80.77），跳过本次检查",
                "[2026-09-28 07:20:00] [INFO] 登录成功: Portal协议认证成功！",
                "[2026-09-28 07:30:00] [ERROR] 登录失败: 账号不存在或账号未绑定宽带",
                "[2026-09-28 07:30:00] [WARNING] 登录失败，进入退避：连续 1 次，下次重试 5 分钟后（…）",
                "[2026-09-28 07:40:00] [WARNING] 密码未设置，跳过登录。请通过 Web UI 设置密码。",
                // 审计 E：2.x 的「仍不可达」是结局（EV_UNREACHABLE）→ 2.x 自己算不健康 ✓
                "[2026-09-28 07:50:00] [ERROR] 等待 60s 后 172.16.80.3:80 仍不可达",
            ]),
            7,
            at(1_790_630_400), // 2026-09-28 21:20 UTC ✓（这些行都在窗口内）
        );
        assert_eq!((report.ok, report.fail, report.skip, report.unmarked), (2, 2, 2, 2));
        assert_eq!(
            report.rate_text(),
            "50.0%",
            "2/(2+2)：skip 与 unmarked 不进成功率；「仍不可达」按 2.x 口径进失败 ✓"
        );
        assert_eq!(report.first_at.as_deref(), Some("2026-09-28 07:00:00"));
        assert_eq!(report.days.len(), 1);
        assert_eq!(report.days[0].date, "2026-09-28");
        assert!(!report.is_empty(), "旧日志不再是空面板 ✓");
    }

    #[test]
    fn garbage_lines_are_ignored_not_misclassified() {
        for bad in [
            "",
            "hello world",
            "2026-09-28 15:14:15 没有 Z 结尾",
            "2026-09-28 15:14:15Z [weird] 未知标记",
            "not-a-date 15:14:15Z [ok] x",
            "20260928 15:14:15Z [ok] x",
            // 中文行（每字 3 字节）在第 20 个字节处不是字符边界 —— 切片会 panic，get() 不会 ✓
            "一二三四五六七八九十",
            "[2026-09-28 7:00:00] [INFO] 时间戳位数不对",
            "2026-09-28 07:00:00] [INFO] 少了左括号（不是 2.x 格式）",
        ] {
            assert!(parse_line(bad).is_none(), "不该解析成功：{:?}", bad);
        }
    }

    #[test]
    fn rate_uses_only_decidable_samples() {
        // ① 这些行是 2026-09-28 的；把「现在」放到 2027 年之后 → 全在窗口之外 ✓
        let report = collect(
            &lines(&[
                "2026-09-28 10:00:00Z [ok] 已在线",
                "2026-09-28 11:00:00Z [ok] 登录成功",
                "2026-09-28 12:00:00Z [fail] 网关不可达",
                "2026-09-28 13:00:00Z [skip] 不在校园网",
                "2026-09-28 14:00:00Z 老格式的行",
            ]),
            7,
            at(1_800_000_000), // 2027-01-15 前后 ✓
        );
        assert!(report.is_empty(), "窗口外的不该被统计 ✓");

        // ② 把「现在」放到这些行当天的晚上 → 全在窗口内 ✓
        let now = at(1_790_630_400); // 2026-09-28 21:20 ✓
        let report = collect(
            &lines(&[
                "2026-09-28 12:00:00Z [fail] 网关不可达",
                "2026-09-28 10:00:00Z [ok] 已在线",
                "2026-09-28 11:00:00Z [ok] 登录成功",
                "2026-09-28 13:00:00Z [skip] 不在校园网",
                "2026-09-28 14:00:00Z 老格式的行",
            ]),
            7,
            now,
        );
        assert_eq!((report.ok, report.fail, report.skip, report.unmarked), (2, 1, 1, 1));
        assert_eq!(report.rate_text(), "66.7%", "2/(2+1) = 66.7% ✓（skip 不算失败）");
        assert_eq!(report.last_summary.as_deref(), Some("老格式的行"), "取最新一行 ✓");
        assert!(report.summary_cn().contains("近 7 天"), "{}", report.summary_cn());
    }

    #[test]
    fn buckets_by_day_in_order() {
        let report = collect(
            &lines(&[
                "2026-09-27 23:59:59Z [ok] a",
                "2026-09-28 00:00:00Z [fail] b",
                "2026-09-28 01:00:00Z [ok] c",
            ]),
            7,
            at(1_790_000_000),
        );
        assert_eq!(report.days.len(), 2);
        assert_eq!(report.days[0].date, "2026-09-27");
        assert_eq!(report.days[0].ok, 1);
        assert_eq!(report.days[1].date, "2026-09-28");
        assert_eq!((report.days[1].ok, report.days[1].fail), (1, 1));
    }

    #[test]
    fn window_filters_old_lines_out() {
        let now = at(1_790_000_000); // 2026-09-22 前后
        let report = collect(
            &lines(&[
                "2020-01-01 00:00:00Z [fail] 很久以前",
                "2026-09-21 00:00:00Z [ok] 昨天",
            ]),
            7,
            now,
        );
        assert_eq!(report.fail, 0, "窗口外的失败不能被算进来 ✗");
        assert_eq!(report.days.len(), 1);
        assert_eq!(report.days[0].date, "2026-09-21");
    }

    #[test]
    fn no_decidable_samples_means_no_rate() {
        let report = collect(
            &lines(&["2026-09-28 10:00:00Z [skip] 账号未设置", "垃圾行"]),
            7,
            at(1_790_000_000),
        );
        assert_eq!(report.success_rate, None);
        assert_eq!(report.rate_text(), "—", "没有样本就不该显示 0% 或 100% ✗");
        assert!(!report.is_empty(), "有 skip 就不算「无数据」✓");
    }

    #[test]
    fn log_line_round_trips_through_the_parser() {
        let text = log_line(at(1_774_732_800), Kind::Fail, "网关不可达：连接超时");
        assert_eq!(text, "2026-03-28 21:20:00Z [fail] 网关不可达：连接超时");
        let record = parse_line(&text).unwrap();
        assert_eq!(record.kind, Kind::Fail);
        assert_eq!(record.text, "网关不可达：连接超时");
        assert_eq!(record.date, "2026-03-28");
    }
}

