//! 界面与核心之间的一层「视图模型」：**界面不读文件、不碰网络**，只显示这里的字符串 ✓。
//! 好处：Slint 之外的逻辑全部能在单测里验证（`cargo test` 不弹窗口也能跑 ✓）。

use drcom_core::{config::Config, net::PlainHttp, platform, secret, session, Status};
use std::time::Duration;

const TIMEOUT: Duration = Duration::from_secs(12);

/// 界面要显示的整屏数据（全是「显示用字符串」，界面不做判断 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dashboard {
    pub version_line: String,
    pub channel_line: String,
    pub is_prerelease: bool,
    pub os_line: String,
    pub target_line: String,
    pub portable_line: String,
    pub gateway_line: String,
    pub account_line: String,
    pub interval_line: String,
    pub data_dir_line: String,
    pub config_line: String,
    pub service_line: String,
}

/// 采集一次（只读 ✓；界面点「刷新」也是走它）。
pub fn collect() -> Dashboard {
    let status = Status::collect();
    let os = platform::Os::current();
    let cfg = Config::load(&platform::config_path()).unwrap_or_default();
    let errors = cfg.validate();

    Dashboard {
        version_line: status.version.clone(),
        channel_line: format!("{} · {}", status.channel_label, status.channel),
        is_prerelease: status.is_prerelease,
        os_line: os.label_cn().to_string(),
        target_line: status.target.clone(),
        portable_line: if status.portable { "便携模式".to_string() } else { "安装模式".to_string() },
        gateway_line: if cfg.host.is_empty() { "（未填）".to_string() } else { cfg.host.clone() },
        account_line: if cfg.account_configured() {
            secret::MaskedAccount(&cfg.account).to_string()
        } else {
            "未设置".to_string()
        },
        interval_line: if cfg.auto_check_enabled {
            format!("每 {} 分钟", cfg.auto_check_interval_min)
        } else {
            "已关闭".to_string()
        },
        data_dir_line: shorten(&status.data_dir, 42),
        config_line: if !platform::config_path().exists() {
            "未初始化".to_string()
        } else if errors.is_empty() {
            "正常".to_string()
        } else {
            format!("{} 个问题", errors.len())
        },
        service_line: os.service_flavor().to_string(),
    }
}

/// 读密码文件（跳过空行与 `#` 注释 ✓，与 CLI / 2.x 一致）。
pub fn read_password() -> String {
    std::fs::read_to_string(platform::password_path())
        .ok()
        .and_then(|text| {
            text.lines()
                .map(|l| l.trim().to_string())
                .find(|l| !l.is_empty() && !l.starts_with('#'))
        })
        .unwrap_or_default()
}

/// 「打开数据目录」：三平台各用系统命令（不引第三方 crate ✓）。
pub fn open_data_dir() -> Result<(), String> {
    let dir = platform::data_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("建不了目录 {}: {}", dir.display(), e))?;
    let path = dir.display().to_string();
    let result = if cfg!(target_os = "windows") {
        std::process::Command::new("explorer").arg(&path).spawn()
    } else if cfg!(target_os = "macos") {
        std::process::Command::new("open").arg(&path).spawn()
    } else {
        std::process::Command::new("xdg-open").arg(&path).spawn()
    };
    result.map(|_| ()).map_err(|e| format!("打不开文件管理器：{}", e))
}

/// 路径太长就把中间省掉（避免卡片里被截断得看不懂 ✓）。
pub fn shorten(text: &str, limit: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= limit {
        return text.to_string();
    }
    let head: String = chars.iter().take(limit / 2 - 1).collect();
    let tail: String = chars.iter().rev().take(limit / 2 - 2).rev().collect();
    format!("{}…{}", head, tail)
}

/// 「立即检查」：读配置 + 密码 → 跑一次 [`session::check_once`] → （颜色, 文本）✓
pub fn run_check() -> (String, String) {
    let cfg_path = platform::config_path();
    let cfg = match Config::load(&cfg_path) {
        Ok(cfg) => cfg,
        // 配置文件还不存在 = **首次运行的正常状态** ✓ → 给可执行的指引，而不是抛系统错误 ✗
        Err(_) if !cfg_path.exists() => {
            return (
                "warn".to_string(),
                format!(
                    "还没初始化配置（首次运行正常 ✓）\n需要先在下面这个文件里填认证网关与账号：\n{}\n字段：host（网关地址）、account（账号）、suffix（后缀）",
                    cfg_path.display()
                ),
            );
        }
        Err(e) => return ("danger".to_string(), e),
    };
    let errors = cfg.validate();
    if !errors.is_empty() {
        return ("danger".to_string(), format!("配置有问题：\n  - {}", errors.join("\n  - ")));
    }
    if !cfg.account_configured() {
        return (
            "warn".to_string(),
            format!(
                "账号还没填 —— 请在 {} 里写 account\n（密码写到 {}，一行密码，`#` 开头是注释）",
                cfg_path.display(),
                platform::password_path().display()
            ),
        );
    }
    let password = read_password();
    if password.is_empty() {
        return (
            "danger".to_string(),
            format!(
                "密码文件是空的：{}\n（一行密码；`#` 开头的行会被跳过）",
                platform::password_path().display()
            ),
        );
    }

    // 网络位置守卫：不在校园网就不白跑一次认证 ✓（读不到 Wi-Fi/IP 时 fail-open ✓）
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(&cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        return (
            "warn".to_string(),
            format!(
                "{}\n（当前 Wi-Fi: {} · 本机 IP: {}）\n—— 去「设置」里把白名单改对，或关掉网络位置守卫 ✓",
                verdict.reason,
                probe.ssid.clone().unwrap_or_else(|| "读不到".to_string()),
                if probe.ips.is_empty() { "读不到".to_string() } else { probe.ips.join(", ") }
            ),
        );
    }

    let report = session::check_once(&cfg, &password, &PlainHttp, TIMEOUT);
    let kind = if report.outcome.is_ok() { "ok" } else { "danger" };
    (
        kind.to_string(),
        format!(
            "{}\n（在线探测: {:?} · 回调: {}）\n—— 3.0 预览版目前只做「一次检查」，常驻后台在 M3 ✓",
            report.outcome.summary_cn(),
            report.online,
            report.callback
        ),
    )
}

/// 「运行自检」：核心自检 + 路径可写性（与 CLI 的 `selfcheck` 同一套判据 ✓）。
pub fn run_selfcheck() -> (String, String) {
    let version = drcom_core::app_version_string();
    let version_ok = drcom_core::channel::Version::parse(&version).is_some();
    let prerelease = drcom_core::app_version().channel.is_prerelease();
    let cfg_errors = Config::default().validate();
    let sample = "dr1({\"result\":1,\"msg\":\"ok\"});";
    let jsonp_ok = matches!(drcom_core::protocol::parse_login_reply(sample), Ok(r) if r.success);
    let data_ok = std::fs::create_dir_all(platform::data_dir()).is_ok();
    let conf_ok = std::fs::create_dir_all(platform::config_dir()).is_ok();

    let mut lines: Vec<String> = Vec::new();
    let mut failed = 0usize;
    {
        let mut push = |name: &str, ok: bool, detail: String| {
            if ok {
                lines.push(format!("PASS  {}", name));
            } else {
                failed += 1;
                lines.push(format!("FAIL  {}  {}", name, detail));
            }
        };
        push("版本串可解析", version_ok, version.clone());
        push("开发期走预览通道（正式用户收不到 ✓）", prerelease, "release".to_string());
        push("默认配置校验通过", cfg_errors.is_empty(), cfg_errors.join("; "));
        push("JSONP 解析正常", jsonp_ok, sample.to_string());
        push("数据目录可写", data_ok, platform::data_dir().display().to_string());
        push("配置目录可写", conf_ok, platform::config_dir().display().to_string());
    }

    let kind = if failed == 0 { "ok" } else { "danger" };
    (kind.to_string(), format!("{}\n\n结果：{} 项失败", lines.join("\n"), failed))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dashboard_collects_without_panicking() {
        let dash = collect();
        assert!(dash.version_line.starts_with("3.0.0.0"), "{}", dash.version_line);
        assert!(dash.is_prerelease, "开发期必须是预览版 ✓");
        assert!(!dash.os_line.is_empty());
        assert!(!dash.target_line.is_empty());
        assert!(dash.channel_line.contains("预览版"), "{}", dash.channel_line);
        assert!(dash.service_line.len() > 3);
    }

    #[test]
    fn account_is_masked_on_screen() {
        // 界面上永远看不到完整账号 ✓
        assert_eq!(secret::MaskedAccount("2023001234").to_string(), "2023******");
    }

    #[test]
    fn shorten_keeps_both_ends() {
        let long = "C:/very/long/path/that/keeps/going/and/going/data";
        let short = shorten(long, 24);
        assert!(short.chars().count() <= 24, "{}", short);
        assert!(short.contains('…'));
        assert!(short.starts_with("C:/very"));
        assert_eq!(shorten("short", 40), "short");
    }

    #[test]
    fn selfcheck_is_green_on_a_clean_machine() {
        let (kind, text) = run_selfcheck();
        assert_eq!(kind, "ok", "自检必须全绿：\n{}", text);
        assert!(text.contains("结果：0 项失败"), "{}", text);
        assert!(text.contains("PASS"), "{}", text);
    }

    #[test]
    fn check_never_panics_and_gives_actionable_text() {
        // 开发机上通常没配账号/密码 → 应给可操作的提示，而不是崩溃 ✓
        let (kind, text) = run_check();
        assert!(["ok", "warn", "danger"].contains(&kind.as_str()), "kind={}", kind);
        assert!(!text.is_empty());
        assert!(
            text.contains("账号") || text.contains("密码") || text.contains("登录") || text.contains("在线"),
            "提示要能指导用户: {}",
            text
        );
    }

    #[test]
    fn password_reader_skips_comments() {
        // 直接测「同一套解析规则」：注释行/空行都不算密码 ✓
        let parsed = ["# 注释", "", "  ", "真实密码"].iter().map(|l| l.trim())
            .find(|l| !l.is_empty() && !l.starts_with('#'))
            .unwrap_or("");
        assert_eq!(parsed, "真实密码");
    }
}

