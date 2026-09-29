//! 脱敏诊断包 —— 一键打包「出问题时要给别人看的东西」✓。
//!
//! 内容（**全部脱敏** ✓）：
//!   - `说明.txt`：这是什么包、里面有什么、怎么发（含时间戳与版本 ✓）
//!   - `status.json`：版本 / 通道 / 平台 / 数据目录 / 配置校验结果（**无凭据** ✓）
//!   - `config.sanitized.json`：配置（账号只留前 4 位 + `******` ✓；配置里本来就没有密码字段 ✓）
//!   - `metrics.json`：近 N 天统计 ✓
//!   - `logs/campus_login.log`：日志尾部（**逐行过脱敏器** ✓，最多 400 行 ✓）
//!
//! 铁律（有测试守着）：**密码永远不进包** ✓；任何文本都过一遍 [`secret::scrub_url`] ✓ ——
//! 就算某条异常信息里带着整条登录 URL（含密码），也会被擦成 `<url>` ✓。

use crate::config::Config;
use crate::metrics::Metrics;
use crate::secret;
use crate::{timefmt, zipwriter, Status};
use std::time::SystemTime;

/// 日志最多带多少行（够排障，又不至于让包变大 ✓）
pub const LOG_TAIL_MAX_LINES: usize = 400;

/// 打包输入。
pub struct Inputs<'a> {
    pub status: &'a Status,
    pub config: &'a Config,
    /// 业务日志的全部行（最旧 → 最新 ✓，从轮转文件读出来就是这顺序 ✓）
    pub log_lines: &'a [String],
    pub metrics: &'a Metrics,
    /// 额外文本（例如升级日志尾部 ✓）；文件名请用 ASCII ✓
    pub extra: Vec<(&'a str, String)>,
}

/// 生成诊断包字节（ZIP ✓）。
pub fn build_package(inputs: &Inputs<'_>, now: SystemTime) -> Result<Vec<u8>, String> {
    let status_json = serde_json::to_string_pretty(inputs.status).map_err(|e| e.to_string())?;
    let mut entries: Vec<(String, String)> = vec![
        ("说明.txt".to_string(), readme_text(inputs, now)),
        ("status.json".to_string(), scrub_all(&status_json)),
        ("config.sanitized.json".to_string(), sanitized_config(inputs.config)?),
        ("metrics.json".to_string(), metrics_json(inputs.metrics)),
        ("logs/campus_login.log".to_string(), scrubbed_log_tail(inputs.log_lines)),
    ];
    for (name, text) in &inputs.extra {
        entries.push(((*name).to_string(), scrub_all(text)));
    }

    let refs: Vec<zipwriter::ZipEntry<'_>> = entries
        .iter()
        .map(|(name, text)| zipwriter::ZipEntry { name, data: text.as_bytes() })
        .collect();
    zipwriter::build(&refs, now)
}

/// 配置 → 脱敏 JSON（账号打码；顺手兜底屏蔽任何像密码的键 ✓）。
pub fn sanitized_config(cfg: &Config) -> Result<String, String> {
    let mut value = serde_json::to_value(cfg).map_err(|e| e.to_string())?;
    if let Some(map) = value.as_object_mut() {
        let masked = if cfg.account_configured() {
            secret::MaskedAccount(&cfg.account).to_string()
        } else {
            String::new()
        };
        map.insert("account".to_string(), serde_json::json!(masked));
        // 兜底：万一将来配置里出现敏感键 ✗
        for key in ["password", "passwd", "pwd", "token", "secret", "credential"] {
            if map.contains_key(key) {
                map.insert(key.to_string(), serde_json::json!("***"));
            }
        }
    }
    let text = serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?;
    Ok(scrub_all(&text))
}

fn metrics_json(metrics: &Metrics) -> String {
    let days: Vec<serde_json::Value> = metrics
        .days
        .iter()
        .map(|day| {
            serde_json::json!({
                "date": day.date,
                "ok": day.ok,
                "fail": day.fail,
                "skip": day.skip,
            })
        })
        .collect();
    serde_json::json!({
        "window_days": metrics.window_days,
        "ok": metrics.ok,
        "fail": metrics.fail,
        "skip": metrics.skip,
        "unmarked": metrics.unmarked,
        "success_rate_percent": metrics.success_rate.map(|rate| rate as f64 / 100.0),
        "first_at": metrics.first_at,
        "last_at": metrics.last_at,
        "days": days,
    })
    .to_string()
}

fn scrubbed_log_tail(lines: &[String]) -> String {
    let start = lines.len().saturating_sub(LOG_TAIL_MAX_LINES);
    let mut out = String::new();
    if start > 0 {
        out.push_str(&format!(
            "（日志较长，这里只带最后 {} 行 / 共 {} 行）\n",
            LOG_TAIL_MAX_LINES,
            lines.len()
        ));
    }
    for line in &lines[start..] {
        out.push_str(&scrub_all(line));
        out.push('\n');
    }
    out
}

/// 逐行擦除 —— **兜底**：日志里万一混进 URL / 密码也不能带走 ✓
pub fn scrub_all(text: &str) -> String {
    text.lines().map(secret::scrub_url).collect::<Vec<_>>().join("\n")
}

fn readme_text(inputs: &Inputs<'_>, now: SystemTime) -> String {
    format!(
        "\
星尘闪连 诊断包（已脱敏）
生成时间：{}（UTC）
版本：{}（{}）
平台：{} / {}　目标：{}
数据目录：{}
便携模式：{}

本包含什么：
  说明.txt              就是本文件
  status.json           版本 / 通道 / 平台 / 数据目录 / 配置校验结果
  config.sanitized.json 配置（**账号只留前 4 位**，其余打码）
  metrics.json          近 {} 天连接质量统计（成功 / 失败 / 跳过 / 成功率）
  logs/campus_login.log 业务日志尾部（最多 {} 行，逐行脱敏）

本包**不包含**什么（重要）：
  - 你的密码（永远不进包）
  - 完整账号（只留前 4 位）
  - 任何 URL（异常信息里的网址会被擦成 <url>）

发给别人之前，建议你自己先解压看一眼 ✓。
",
        timefmt::format_utc(now),
        inputs.status.version,
        inputs.status.channel_label,
        inputs.status.os,
        inputs.status.arch,
        inputs.status.target,
        inputs.status.data_dir,
        if inputs.status.portable { "是" } else { "否" },
        inputs.metrics.window_days,
        LOG_TAIL_MAX_LINES
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::metrics::{self, Kind};
    use std::time::{Duration, UNIX_EPOCH};

    fn at(secs: u64) -> SystemTime {
        UNIX_EPOCH + Duration::from_secs(secs)
    }

    /// 组一份「最坏情况」输入：日志里带着**整条登录 URL（含密码）** ✗
    fn sample() -> (Config, Status, Vec<String>, Metrics) {
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        cfg.suffix = "@yd".to_string();
        let pwd = "SuperSecret!42";
        let lines = vec![
            metrics::log_line(at(1_790_630_400), Kind::Ok, "已在线，无需登录"),
            format!(
                "2026-09-28 20:00:00Z [fail] connection failed for http://172.16.80.3:801/eportal/portal/login?user_password={}&callback=dr1",
                pwd
            ),
        ];
        let metrics_report = metrics::collect(&lines, 7, at(1_790_630_400));
        (cfg, Status::collect(), lines, metrics_report)
    }

    #[test]
    fn package_has_expected_entries_and_no_secrets_at_all() {
        let (cfg, status, lines, metrics_report) = sample();
        let inputs = Inputs {
            status: &status,
            config: &cfg,
            log_lines: &lines,
            metrics: &metrics_report,
            extra: Vec::new(),
        };
        let zip = build_package(&inputs, at(1_790_630_400)).unwrap();

        // ① 条目清单 ✓
        let names = zipwriter::entry_names(&zip).unwrap();
        for expected in [
            "说明.txt",
            "status.json",
            "config.sanitized.json",
            "metrics.json",
            "logs/campus_login.log",
        ] {
            assert!(names.contains(&expected.to_string()), "缺 {} ✗（实际 {:?}）", expected, names);
        }

        // ② 隐私铁律：store 模式**不压缩** → 明文可搜，正好用来做最严格的隐私断言 ✓
        let text = String::from_utf8_lossy(&zip);
        assert!(!text.contains("SuperSecret!42"), "密码进了包 ✗");
        assert!(!text.contains("user_password="), "密码参数名都不该出现 ✓");
        assert!(!text.contains("http://"), "URL 进了包 ✗");
        // 注意：网关地址本身不是秘密（配置里就有 ✓），关键是**日志里那条 URL 必须被擦掉** ✓
        assert!(
            text.contains("connection failed for <url>"),
            "日志里的 URL 应被擦成 <url>，且保留可读原因 ✓"
        );
        assert!(text.contains("2023******"), "账号必须是打码形式 ✓");
        assert!(!text.contains("2023001234"), "完整账号进了包 ✗");

        // ③ 该有的内容还得在 ✓
        assert!(text.contains("已在线，无需登录"), "日志正文要在 ✓");
        assert!(text.contains("success_rate_percent"), "统计字段要在 ✓");
        assert!(text.contains("本包**不包含**什么"), "说明要写清楚不含什么 ✓");
    }

    #[test]
    fn password_like_keys_are_blanked_defensively() {
        let mut cfg = Config::default();
        cfg.extra.insert("password".to_string(), serde_json::json!("leak-me"));
        cfg.extra.insert("token".to_string(), serde_json::json!("tok-123"));
        let json = sanitized_config(&cfg).unwrap();
        assert!(!json.contains("leak-me"), "密码形状的键必须被打码 ✗: {}", json);
        assert!(!json.contains("tok-123"));
        assert!(json.contains("\"password\": \"***\""));
    }

    #[test]
    fn extra_files_are_included_and_log_tail_is_capped() {
        let (cfg, status, mut lines, _) = sample();
        lines.extend(
            (0..600).map(|i| format!("2026-09-28 21:{:02}:00Z [ok] 第 {} 行", i % 60, i)),
        );
        let metrics_report = metrics::collect(&lines, 7, at(1_790_630_400));
        let inputs = Inputs {
            status: &status,
            config: &cfg,
            log_lines: &lines,
            metrics: &metrics_report,
            extra: vec![("upgrade.log", "升级日志尾部\n".to_string())],
        };
        let zip = build_package(&inputs, at(1_790_630_400)).unwrap();
        let text = String::from_utf8_lossy(&zip);
        assert!(text.contains("（日志较长，这里只带最后 400 行"), "截断要有说明 ✓");
        assert!(text.contains("upgrade.log"), "额外文件要在 ✓");
        assert!(text.contains("升级日志尾部"));
        assert!(zipwriter::entry_names(&zip).unwrap().contains(&"upgrade.log".to_string()));
    }

    #[test]
    fn output_is_deterministic_for_the_same_inputs() {
        let (cfg, status, lines, metrics_report) = sample();
        let make = || {
            build_package(
                &Inputs {
                    status: &status,
                    config: &cfg,
                    log_lines: &lines,
                    metrics: &metrics_report,
                    extra: Vec::new(),
                },
                at(1_790_630_400),
            )
            .unwrap()
        };
        assert_eq!(make(), make(), "同样输入必须产出同样字节 ✓（便于比对摘要）");
    }

    #[test]
    fn readme_mentions_version_platform_and_window() {
        let (cfg, status, lines, metrics_report) = sample();
        let text = readme_text(
            &Inputs {
                status: &status,
                config: &cfg,
                log_lines: &lines,
                metrics: &metrics_report,
                extra: Vec::new(),
            },
            at(1_790_630_400),
        );
        assert!(text.contains(&status.version), "{}", text);
        assert!(text.contains("近 7 天"));
        assert!(text.contains("2026-09-28 21:20:00Z"), "生成时间用 UTC ✓");
    }
}

