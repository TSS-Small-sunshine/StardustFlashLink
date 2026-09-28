//! `stardust-flash-link` —— 星尘闪连 3.0 的无界面可执行（跨平台同源）。
//!
//! 子命令（M0 范围）：
//!   - `version`    版本 + 通道 + 平台 + 架构（CI / 诊断用 ✓）
//!   - `status`     打印 `/api/status` 的那份 JSON（同一份数据 ✓）
//!   - `selfcheck`  内置自检（配置 / 协议 / 路径 / 版本）
//!   - `login`      跑一次登录（`--dry-run` 只打印脱敏后的 URL，不发请求 ✓）
//!   - `serve`      起本地控制 API（只绑 127.0.0.1 ✓）
//!
//! 界面（GUI/托盘）在 M2 接到同一份核心上 —— 这层永远保持「可以 headless 跑」✓，
//! 因为 Linux 服务器 / 树莓派上的用例就是无界面 ✓。

mod server;

use drcom_core::net;

use drcom_core::{channel::Version, config::Config, platform, protocol, secret, Status};
use std::time::Duration;

const DEFAULT_TIMEOUT_SEC: u64 = 12;

fn main() {
    let code = run(std::env::args().skip(1).collect());
    std::process::exit(code);
}

fn run(args: Vec<String>) -> i32 {
    match args.first().map(String::as_str) {
        None | Some("help") | Some("--help") | Some("-h") => {
            print_help();
            0
        }
        Some("version") | Some("--version") | Some("-V") => {
            let v = drcom_core::app_version();
            println!("{} {}", drcom_core::APP_NAME, drcom_core::app_version_string());
            println!("通道: {}（{}）", v.channel.as_str(), v.channel.label_cn());
            println!("平台: {} / {}", platform::Os::current().label_cn(), platform::arch());
            println!("目标: {}", platform::target_label());
            println!("便携: {}", if platform::is_portable() { "是" } else { "否" });
            0
        }
        Some("status") => {
            let status = Status::collect();
            match serde_json::to_string_pretty(&status) {
                Ok(json) => {
                    println!("{}", json);
                    0
                }
                Err(e) => {
                    eprintln!("序列化失败: {}", e);
                    1
                }
            }
        }
        Some("selfcheck") => selfcheck(),
        Some("login") => login(&args[1..]),
        Some("serve") => serve(&args[1..]),
        Some(other) => {
            eprintln!("未知命令: {}", other);
            print_help();
            2
        }
    }
}

fn print_help() {
    println!(
        "{} {}（跨平台核心）\n\n\
         用法:\n  stardust-flash-link <命令> [选项]\n\n\
         命令:\n  \
         version                     显示版本 / 通道 / 平台\n  \
         status                      显示状态 JSON（与本地 API 同一份数据）\n  \
         selfcheck                   内置自检\n  \
         login [--dry-run] [--host H] [--account A] [--suffix S] [--password P]\n  \
         serve [--port N]            起本地控制 API（默认 {}，只绑 127.0.0.1）\n\n\
         配置目录: {}\n",
        drcom_core::APP_NAME,
        drcom_core::app_version_string(),
        server::DEFAULT_UI_PORT,
        platform::config_dir().display()
    );
}

/// 内置自检：与 2.x 的 `_smoke_*.py` 一个思路 —— 行为级、能一眼看出哪条挂了。
fn selfcheck() -> i32 {
    let mut failures = 0usize;

    let v = drcom_core::app_version();
    report(
        &mut failures,
        "版本可解析且四段一致",
        Version::parse(&drcom_core::app_version_string()).map(|p| p.numbers()) == Some(v.numbers()),
        drcom_core::app_version_string(),
    );
    report(
        &mut failures,
        "开发期走预览通道（正式用户收不到 ✓）",
        v.channel.is_prerelease(),
        v.channel.as_str().to_string(),
    );

    let cfg = Config::default();
    report(&mut failures, "默认配置无错误", cfg.validate().is_empty(), cfg.validate().join("; "));
    let mut bad = cfg.clone();
    bad.suffix = "@xx".to_string();
    report(&mut failures, "非法后缀被抓到", !bad.validate().is_empty(), "校验漏了 ✗".to_string());
    let mut empty_account = cfg.clone();
    empty_account.account = String::new();
    report(
        &mut failures,
        "空账号仍可保存（P1-7 的行为 ✓）",
        empty_account.validate().is_empty(),
        empty_account.validate().join("; "),
    );

    let req = protocol::LoginRequest {
        host: &cfg.host,
        account: "2023001234",
        suffix: "@yd",
        password: "p@ss word+1",
        ip: "10.0.0.2",
        mac: "E25B367B8DAC",
    };
    let url = protocol::build_login_url(&req, "dr1234", "1");
    report(
        &mut failures,
        "登录 URL 形状正确（含 801 端口）",
        url.contains(":801/eportal/portal/login?"),
        url.clone(),
    );
    report(
        &mut failures,
        "密码被百分号转义（空格 %20 / 加号 %2B）",
        url.contains("%20") && url.contains("%2B"),
        url.clone(),
    );
    let scrubbed = secret::scrub_url(&url);
    report(
        &mut failures,
        "脱敏后既没有密码也没有 URL",
        !scrubbed.contains("p@ss") && !scrubbed.contains("http://") && scrubbed.contains("<url>"),
        scrubbed,
    );
    report(
        &mut failures,
        "JSONP 响应解析正常",
        matches!(protocol::parse_login_reply("dr1({\"result\":1,\"msg\":\"ok\"});"), Ok(r) if r.success),
        format!("{:?}", protocol::parse_login_reply("nope")),
    );
    report(
        &mut failures,
        "数据目录可用",
        std::fs::create_dir_all(platform::data_dir()).is_ok(),
        platform::data_dir().display().to_string(),
    );

    println!("\n结果：{} 项失败", failures);
    if failures == 0 {
        0
    } else {
        1
    }
}

fn report(failures: &mut usize, name: &str, ok: bool, detail: String) {
    if ok {
        println!("PASS  {}", name);
    } else {
        *failures += 1;
        println!("FAIL  {}  {}", name, detail);
    }
}

/// `serve --port 8848`：起本地控制 API（只绑回环 ✓）。
fn serve(args: &[String]) -> i32 {
    let port = match take_opt(args, "--port") {
        Some(raw) => match raw.parse::<u16>() {
            Ok(p) => p,
            Err(_) => {
                eprintln!("端口不合法: {}", raw);
                return 2;
            }
        },
        None => server::DEFAULT_UI_PORT,
    };
    match server::ApiServer::bind(port) {
        Ok(api) => {
            let local = api.local_port();
            println!("{} 本地控制 API: http://127.0.0.1:{}", drcom_core::APP_NAME, local);
            println!("接口: /api/health · /api/status（只读，仅绑回环 ✓）");
            let (_port, handle) = api.spawn();
            let _ = handle.join();
            0
        }
        Err(e) => {
            eprintln!("绑定 127.0.0.1:{} 失败: {}（端口被占用？）", port, e);
            1
        }
    }
}

/// `login`：读配置（命令行可覆盖）→ 查在线 → 需要时登录。
fn login(args: &[String]) -> i32 {
    let mut cfg = match Config::load(&platform::config_path()) {
        Ok(cfg) => cfg,
        Err(e) => {
            eprintln!("{}（先跑一次 `status` 看看配置目录）", e);
            return 1;
        }
    };
    if let Some(host) = take_opt(args, "--host") {
        cfg.host = host;
    }
    if let Some(account) = take_opt(args, "--account") {
        cfg.account = account;
    }
    if let Some(suffix) = take_opt(args, "--suffix") {
        cfg.suffix = suffix;
    }
    let password = match take_opt(args, "--password") {
        Some(p) => p,
        None => read_password_file(),
    };

    let errors = cfg.validate();
    if !errors.is_empty() {
        eprintln!("配置有问题：\n  - {}", errors.join("\n  - "));
        return 1;
    }
    let ip = net::local_ip_towards(&cfg.host, cfg.port).unwrap_or_default();
    let req = protocol::LoginRequest {
        host: &cfg.host,
        account: &cfg.account,
        suffix: &cfg.suffix,
        password: &password,
        ip: &ip,
        mac: "",
    };
    let url = protocol::build_login_url(&req, "dr1234", "1");
    if args.iter().any(|a| a == "--dry-run") {
        println!("DRY-RUN 请求（已脱敏）: {}", secret::scrub_url(&url));
        println!("源 IP: {}", if ip.is_empty() { "(取不到)" } else { &ip });
        return 0;
    }
    if !cfg.account_configured() {
        println!("账号未设置 → 跳过登录 ✓（设置后会自动登录）");
        return 0;
    }
    if password.is_empty() {
        eprintln!(
            "密码为空：请写入 {}（一行密码，`#` 开头的行会被跳过）",
            platform::password_path().display()
        );
        return 1;
    }

    let timeout = Duration::from_secs(DEFAULT_TIMEOUT_SEC);
    let online_url = protocol::build_online_check_url(&cfg.host, "cb");
    match net::get(&online_url, timeout) {
        Ok(reply) => match protocol::parse_online_state(&reply.body) {
            Ok(protocol::OnlineState::Online) => {
                println!("已经在线 ✓（无需登录）");
                return 0;
            }
            Ok(state) => println!("在线状态: {:?}", state),
            Err(e) => println!("在线状态解析失败（{}），继续尝试登录", e.label_cn()),
        },
        Err(e) => println!("在线检查没成功：{}（继续尝试登录）", e),
    }

    match net::get(&url, timeout) {
        Ok(reply) => match protocol::parse_login_reply(&reply.body) {
            Ok(reply) => {
                println!(
                    "{} {}",
                    if reply.success { "登录成功 ✓" } else { "登录失败 ✗" },
                    reply.msg
                );
                if reply.success {
                    0
                } else {
                    1
                }
            }
            Err(e) => {
                eprintln!("登录响应无法解析：{}", e.label_cn());
                1
            }
        },
        Err(e) => {
            eprintln!("登录请求失败：{}", e);
            1
        }
    }
}

/// 读密码文件：跳过空行与 `#` 注释行，取第一条有效内容 ✓（与 2.x 一致）。
fn read_password_file() -> String {
    let text = match std::fs::read_to_string(platform::password_path()) {
        Ok(text) => text,
        Err(_) => return String::new(),
    };
    text.lines()
        .map(|line| line.trim())
        .find(|line| !line.is_empty() && !line.starts_with('#'))
        .unwrap_or("")
        .to_string()
}

/// 取 `--key value` 形式的值（零依赖参数解析 ✓）。
fn take_opt(args: &[String], key: &str) -> Option<String> {
    let idx = args.iter().position(|a| a == key)?;
    args.get(idx + 1).cloned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn take_opt_finds_value_and_handles_missing() {
        let args: Vec<String> = ["login", "--host", "172.16.80.3", "--dry-run"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(take_opt(&args, "--host").as_deref(), Some("172.16.80.3"));
        assert_eq!(take_opt(&args, "--port"), None);
        assert_eq!(take_opt(&args, "--dry-run"), None, "布尔开关不吃下一个参数 ✓");
    }

    #[test]
    fn version_command_exits_zero() {
        assert_eq!(run(vec!["version".to_string()]), 0);
        assert_eq!(run(vec!["--help".to_string()]), 0);
        assert_eq!(run(vec!["no-such-cmd".to_string()]), 2);
    }

    #[test]
    fn selfcheck_passes_on_a_clean_machine() {
        assert_eq!(selfcheck(), 0, "内置自检必须全绿 ✓");
    }

    #[test]
    fn dry_run_never_prints_the_password() {
        // 干跑路径上唯一会打印的就是脱敏后的 URL ✓
        let req = protocol::LoginRequest {
            host: "172.16.80.3",
            account: "2023001234",
            suffix: "@yd",
            password: "TopSecret!42",
            ip: "10.0.0.2",
            mac: "",
        };
        let url = protocol::build_login_url(&req, "dr1234", "1");
        let shown = secret::scrub_url(&url);
        assert!(!shown.contains("TopSecret"), "干跑打印了密码 ✗: {}", shown);
        assert!(!shown.contains("http://"), "干跑打印了整条 URL ✗: {}", shown);
    }
}
