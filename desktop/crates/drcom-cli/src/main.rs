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

use drcom_core::net::{self, PlainHttp};
use drcom_core::{
    channel::Version, config::Config, logfile::RotatingLog, platform, portal, protocol,
    scheduler::Scheduler, secret, service, session, timefmt, Outcome, Status,
};
use std::time::{Duration, SystemTime};

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
        Some("run") => run_loop(&args[1..]),
        Some("profile") => profile_cmd(&args[1..]),
        Some("diagnostics") => diagnostics_cmd(&args[1..]),
        Some("portal") => portal_cmd(&args[1..]),
        Some("autostart") => autostart_cmd(&args[1..]),
        Some("service") => service_cmd(&args[1..]),
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
         run [--once]                守护循环：按配置间隔检查、失败走退避（服务化在 M3）\n  \
         profile list|save|activate|delete|auto   配置方案（校内公共场合=无尾缀、宿舍=@yd 等）\n  \
         diagnostics [--out F] [--days N]        生成脱敏诊断包（ZIP，密码永不进包 ✓）\n  \
         portal [--url U] [--timeout N]         门户检测：是不是被校园网门户拦住了（未认证会被 302 到登录页）\n  \
         autostart on|off|status [--dry-run]    开机自启（当前用户级，免管理员 ✓；跑的是无界面循环）\n  \
         service install|uninstall|start|stop|restart|status [--dry-run]   注册成常驻服务（Windows 走 NSSM ✓）\n  \
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
    // —— 网络位置守卫（2.x v2.0.6.2 的功能）：不在校园网就别白跑认证 ✓ ——
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(&cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        println!("跳过本次检查：{}", verdict.reason);
        println!(
            "（当前 Wi-Fi: {} · 本机 IP: {}）",
            probe.ssid.clone().unwrap_or_else(|| "读不到".to_string()),
            if probe.ips.is_empty() { "读不到".to_string() } else { probe.ips.join(", ") }
        );
        return 0;
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

    // —— 一次检查：守卫 → session::check_once → 写业务日志 ✓（与 GUI 同一套逻辑）——
    let timeout = Duration::from_secs(DEFAULT_TIMEOUT_SEC);
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(&cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        println!("跳过本次检查：{}", verdict.reason);
        println!(
            "（当前 Wi-Fi: {} · 本机 IP: {}）",
            probe.ssid.clone().unwrap_or_else(|| "读不到".to_string()),
            if probe.ips.is_empty() { "读不到".to_string() } else { probe.ips.join(", ") }
        );
        return 0;
    }

    let report = session::check_once(&cfg, &password, &PlainHttp, timeout);
    let summary = report.outcome.summary_cn();
    println!("{}", summary);
    if let Err(e) = log_line(&drcom_core::metrics::log_line(
        SystemTime::now(),
        kind_of(&report.outcome),
        &summary,
    )) {
        eprintln!("（日志写不进去：{}）", e);
    }
    if report.outcome.is_ok() {
        0
    } else {
        1
    }
}

/// `diagnostics [--out FILE] [--days N]`：生成**脱敏**诊断包（ZIP ✓）。
/// `service`：把常驻循环注册成系统服务 ✓
/// （Windows = NSSM 托管的服务；Linux = systemd --user；macOS = LaunchAgent ✓）。
fn service_cmd(args: &[String]) -> i32 {
    let action = args.first().map(String::as_str).unwrap_or("status");
    let exe = std::env::current_exe()
        .unwrap_or_else(|_| std::path::PathBuf::from("stardust-flash-link"));
    let run_args = ["run"];
    match action {
        "status" => {
            let available = service::manager_available();
            println!("常驻方式: {}", service::flavor_name());
            println!("管理工具: {}（{}）", service::manager_program(), if available { "可用 ✓" } else { "没找到 ✗" });
            println!("状态: {}", service::status().label_cn());
            if cfg!(target_os = "windows") {
                println!("{}", service::shared_name_note());
            }
            if !available {
                println!();
                if cfg!(target_os = "windows") {
                    println!("{}", service::nssm_missing_hint());
                } else {
                    println!("装好 {} 之后再用 `service install` ✓", service::manager_program());
                }
            }
            println!();
            println!("（也可以用 `autostart on`：登录时自动跑，免管理员、免额外工具 ✓）");
            0
        }
        "install" | "on" => {
            if args.iter().any(|a| a == "--dry-run") {
                println!("将要做（**未改动系统** ✓）：");
                for line in service::plan(&exe, &run_args) {
                    println!("  {}", line);
                }
                return 0;
            }
            match service::install(&exe, &run_args) {
                Ok(message) => {
                    println!("{}", message);
                    println!("常驻命令: {} run（无界面循环 ✓）", exe.display());
                    0
                }
                Err(e) => {
                    eprintln!("注册失败: {}", e);
                    1
                }
            }
        }
        "uninstall" | "off" => match service::uninstall() {
            Ok(message) => {
                println!("{}", message);
                println!("（用 `service install` 可以再装回来 ✓）");
                0
            }
            Err(e) => {
                eprintln!("注销失败: {}", e);
                1
            }
        },
        "start" | "stop" | "restart" => {
            let control = match action {
                "start" => service::Control::Start,
                "stop" => service::Control::Stop,
                _ => service::Control::Restart,
            };
            match service::control(control) {
                Ok(message) => {
                    println!("{}", message);
                    0
                }
                Err(e) => {
                    eprintln!("{}失败: {}", control.label_cn(), e);
                    1
                }
            }
        }
        other => {
            eprintln!(
                "用法: service install|uninstall|start|stop|restart|status [--dry-run]（收到 `{}`）",
                other
            );
            2
        }
    }
}

/// `autostart`：开 / 关 / 查开机自启。
///
/// 全部是**当前用户级**（Windows 注册表 Run / systemd --user / LaunchAgent ✓）
/// —— **一次管理员权限都不需要** ✓；`--dry-run` 只打印将要做什么 ✓。
fn autostart_cmd(args: &[String]) -> i32 {
    let action = args.first().map(String::as_str).unwrap_or("status");
    let exe = std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("stardust-flash-link"));
    let run_args = ["run"];
    match action {
        "on" | "enable" => {
            if args.iter().any(|a| a == "--dry-run") {
                println!("将要做（**未改动系统** ✓）：");
                for line in drcom_core::autostart::plan(&exe, &run_args) {
                    println!("  {}", line);
                }
                return 0;
            }
            match drcom_core::autostart::enable(&exe, &run_args) {
                Ok(msg) => {
                    println!("{}", msg);
                    println!("自启的命令: {} run（无界面循环 ✓）", exe.display());
                    0
                }
                Err(e) => {
                    eprintln!("开启自启失败: {}", e);
                    1
                }
            }
        }
        "off" | "disable" => match drcom_core::autostart::disable() {
            Ok(msg) => {
                println!("{}", msg);
                0
            }
            Err(e) => {
                eprintln!("关闭自启失败: {}", e);
                1
            }
        },
        "status" => {
            let state = drcom_core::autostart::status();
            println!("开机自启: {}", state.label_cn());
            match drcom_core::autostart::file_path() {
                Some(path) => println!("落点: {}", path.display()),
                None => println!("落点: 注册表 HKCU\\...\\Run（当前用户 ✓，免管理员 ✓）"),
            }
            0
        }
        other => {
            eprintln!("用法: autostart on|off|status [--dry-run]（收到 `{}`）", other);
            2
        }
    }
}

/// `portal`：看看是不是被校园网门户拦住了。
///
/// 实测：未认证时校园 AC 会 302 到登录页（`a79.htm`），重定向里还带着
/// AC 名字、本机地址、本机 MAC —— 排障时这些信息比「连不上」有用得多 ✓。
fn portal_cmd(args: &[String]) -> i32 {
    let url = take_opt(args, "--url").unwrap_or_else(|| portal::DEFAULT_PROBE_URL.to_string());
    let seconds = take_opt(args, "--timeout")
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(DEFAULT_TIMEOUT_SEC)
        .max(1);
    println!("探测目标: {}", url);
    let verdict = portal::probe(&PlainHttp, &url, Duration::from_secs(seconds));
    println!("结论: {}", verdict.summary_cn());
    if let portal::PortalVerdict::Blocked(hint) = &verdict {
        println!();
        println!("门户页面: http://{}{}", hint.portal_host, hint.page);
        if let Some(ip) = hint.user_ip.as_deref() {
            println!("门户看到的本机地址: {}", ip);
            let local = net::local_ip_towards(&hint.portal_host, 80);
            if portal::ip_matches(hint, local.as_deref()) == Some(false) {
                println!(
                    "⚠ 与本机出口地址({})不一致 —— 多网卡 / 代理下登录会失败 ✗",
                    local.as_deref().unwrap_or("读不到")
                );
            }
        }
        if let Some(name) = hint.ac_name.as_deref() {
            println!(
                "接入控制器(AC): {} {}",
                name,
                hint.ac_ip.as_deref().unwrap_or("-")
            );
        }
        if let Some(mac) = hint.mac.as_deref() {
            println!("门户看到的本机 MAC: {}", mac);
        }
        if let Some(redirect) = hint.redirect.as_deref() {
            println!("被拦下之前想去的地址: {}", redirect);
        }
        println!();
        println!("登录接口仍是 :801/eportal/portal/login —— 直接跑 `login` 就能过 ✓");
    }
    if verdict.is_online() {
        0
    } else {
        1
    }
}

fn diagnostics_cmd(args: &[String]) -> i32 {
    use drcom_core::{diagnostics, metrics, zipwriter};
    let days = take_opt(args, "--days")
        .and_then(|value| value.parse::<u32>().ok())
        .unwrap_or(7)
        .clamp(1, 90);
    let now = SystemTime::now();

    let business = RotatingLog::business(platform::log_dir().join("campus_login.log"));
    let lines = business.read_all_lines().unwrap_or_default();
    let upgrade = RotatingLog::upgrade(platform::log_dir().join("upgrade.log"));
    let upgrade_lines = upgrade.read_all_lines().unwrap_or_default();

    let cfg = Config::load(&platform::config_path()).unwrap_or_default();
    let status = Status::collect();
    let report = metrics::collect(&lines, days, now);

    let mut extra: Vec<(&str, String)> = Vec::new();
    if !upgrade_lines.is_empty() {
        extra.push(("upgrade.log", upgrade_lines.join("\n")));
    }
    let inputs = diagnostics::Inputs {
        status: &status,
        config: &cfg,
        log_lines: &lines,
        metrics: &report,
        extra,
    };
    let zip = match diagnostics::build_package(&inputs, now) {
        Ok(zip) => zip,
        Err(e) => {
            eprintln!("生成诊断包失败：{}", e);
            return 1;
        }
    };

    // `--out` 给了路径就按路径；只给文件名就放数据目录 ✓
    let path = match take_opt(args, "--out") {
        Some(value) if value.contains('/') || value.contains('\\') => std::path::PathBuf::from(value),
        Some(value) => platform::data_dir().join(value),
        None => platform::data_dir()
            .join(format!("stardust-flash-link-diagnostics-{}.zip", timefmt::compact_utc(now))),
    };
    if let Some(parent) = path.parent() {
        if let Err(e) = std::fs::create_dir_all(parent) {
            eprintln!("建目录失败 {}：{}", parent.display(), e);
            return 1;
        }
    }
    if let Err(e) = std::fs::write(&path, &zip) {
        eprintln!("写文件失败 {}：{}", path.display(), e);
        return 1;
    }

    println!("诊断包已生成：{}（{:.1} KB）", path.display(), zip.len() as f64 / 1024.0);
    println!("{}", report.summary_cn());
    if let Some(names) = zipwriter::entry_names(&zip) {
        println!("包含 {} 个文件：", names.len());
        for name in names {
            println!("  {}", name);
        }
    }
    println!("提示：发出去之前建议先解压看一眼 —— **密码永远不进包** ✓（日志逐行脱敏、账号只留前 4 位）");
    0
}

/// 往业务日志里追加一行（轮转策略与 2.x 一致：5 MB × 3 ✓）。
fn log_line(line: &str) -> Result<(), String> {
    let log = RotatingLog::business(platform::log_dir().join("campus_login.log"));
    log.append_line(line).map_err(|e| e.to_string())
}

/// `run [--once]`：守护循环（按配置的间隔检查；失败走退避 ✓）。
///
/// 这一层是**纯 Rust 跨平台**的：Windows 上可以先手跑，Linux/macOS 上就是 systemd/launchd
/// 起来要跑的东西 ✓（正式的服务化在 M3 ✓）。
fn run_loop(args: &[String]) -> i32 {
    let once = args.iter().any(|a| a == "--once");
    let cfg_path = platform::config_path();
    println!("{} 守护循环启动（间隔取自配置；Ctrl+C 退出）", drcom_core::APP_NAME);

    let mut scheduler = Scheduler::new(30);
    let mut last_interval = 0u32;
    let mut first = true;
    loop {
        let mut cfg = match Config::load(&cfg_path) {
            Ok(cfg) => cfg,
            Err(e) => {
                eprintln!("{}", e);
                if once {
                    return 1;
                }
                std::thread::sleep(Duration::from_secs(30));
                continue;
            }
        };
        // 配置一改就跟着变 ✓（每轮都重读，和 2.x 一样）
        if cfg.auto_check_interval_min != last_interval {
            scheduler.set_interval(cfg.auto_check_interval_min);
            last_interval = cfg.auto_check_interval_min;
        }

        // 位置自适应：Wi-Fi 名命中自动方案就切过去（**尾缀一起换** ✓）
        // 例：走进图书馆 → 「校内公共场合（无尾缀）」；回宿舍 → 「@yd」✓
        let ssid_now = drcom_core::probe::current_ssid();
        match drcom_core::profiles::adapt(&mut cfg, ssid_now.as_deref()) {
            Ok(Some(name)) => {
                if let Err(e) = cfg.save(&cfg_path) {
                    eprintln!("切方案后写盘失败：{}", e);
                }
                let line = format!(
                    "{} 位置自适应：切到方案「{}」（后缀 {}）",
                    timefmt::format_utc(SystemTime::now()),
                    name,
                    drcom_core::profiles::suffix_label(&cfg.suffix)
                );
                let _ = log_line(&line);
                println!("{}", line);
            }
            Ok(None) => {}
            Err(e) => eprintln!("位置自适应失败：{}", e),
        }

        let now = SystemTime::now();
        if first || scheduler.is_due(now) {
            let (kind, summary) = one_check(&cfg);
            let finished = SystemTime::now();
            scheduler.record(kind != drcom_core::metrics::Kind::Fail, &summary, finished);
            let _ = log_line(&drcom_core::metrics::log_line(finished, kind, &summary));
            println!(
                "{} · 连续失败 {} 次 · 下次 {} 后",
                summary,
                scheduler.backoff.consecutive_failures,
                timefmt::human_duration(scheduler.remaining(finished))
            );
            first = false;
            if once {
                return 0;
            }
        }
        // 5 秒醒一次看配置/到点没（很轻；不做网络请求 ✓）
        std::thread::sleep(Duration::from_secs(5));
    }
}

/// 跑**一次**检查：守卫 → 账号/密码校验 → `session::check_once`，返回（等级, 文案）。
///
/// 等级口径（与统计一致 ✓）：**不在校园网 / 账号未设置 = `Skip`**（不算失败 ✓，
/// 否则用户一回家就进退避 ✗）；网关不可达 / 密码错 = `Fail` ✓。
fn one_check(cfg: &Config) -> (drcom_core::metrics::Kind, String) {
    use drcom_core::metrics::Kind;
    let timeout = Duration::from_secs(DEFAULT_TIMEOUT_SEC);
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        return (
            Kind::Skip,
            format!(
                "{}（Wi-Fi: {}）",
                verdict.reason,
                probe.ssid.unwrap_or_else(|| "读不到".to_string())
            ),
        );
    }
    if !cfg.account_configured() {
        return (Kind::Skip, "账号未设置，已跳过检查".to_string());
    }
    let password = read_password_file();
    if password.is_empty() {
        return (Kind::Fail, "密码是空的（去密码文件里写一行）".to_string());
    }
    let report = session::check_once(cfg, &password, &PlainHttp, timeout);
    (kind_of(&report.outcome), report.outcome.summary_cn())
}

/// `Outcome` → 统计等级（口径只有这一处 ✓，免得两处不一致 ✗）。
fn kind_of(outcome: &Outcome) -> drcom_core::metrics::Kind {
    use drcom_core::metrics::Kind;
    match outcome {
        Outcome::SkippedNoAccount => Kind::Skip,
        Outcome::AlreadyOnline | Outcome::LoggedIn { .. } => Kind::Ok,
        _ => Kind::Fail,
    }
}

/// `profile`：方案管理（list / save / activate / delete / auto）。
///
/// 这就是「校内公共场合不带 @yd、宿舍带 @yd」的落地入口 —— **不用手改 config.json** ✓：
/// ```text
/// # 校内公共场合：无尾缀，走进校园 Wi-Fi 自动切过去
/// stardust-flash-link profile save 校内公共场合 --ssid Campus-WiFi --suffix ""
/// # 宿舍：走移动宽带
/// stardust-flash-link profile save 宿舍       --ssid Dorm-WiFi   --suffix @yd
/// stardust-flash-link profile auto on
/// ```
fn profile_cmd(args: &[String]) -> i32 {
    const ACTIONS: [&str; 5] = ["list", "save", "activate", "delete", "auto"];
    let action = args.first().map(String::as_str).unwrap_or("list");
    // 先校验子命令：这样「写错命令」不必先有配置文件就能给出正确提示 ✓
    if !ACTIONS.contains(&action) {
        eprintln!("未知的 profile 子命令：{}（可用：{}）", action, ACTIONS.join(" / "));
        return 2;
    }
    // 需要名字的子命令：**先校验参数**再读配置 —— 否则「还没配置文件」会把用法提示盖掉 ✗
    if matches!(action, "save" | "activate" | "delete") && args.get(1).is_none() {
        eprintln!("用法：profile {} <名字>", action);
        return 2;
    }

    let cfg_path = platform::config_path();
    let mut cfg = match Config::load(&cfg_path) {
        Ok(cfg) => cfg,
        Err(e) => {
            eprintln!("{}", e);
            return 1;
        }
    };
    let persist = |cfg: &Config| -> i32 {
        match cfg.save(&cfg_path) {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("{}", e);
                1
            }
        }
    };
    let use_profiles = drcom_core::profiles::list(&cfg);

    match action {
        "list" => {
            if use_profiles.is_empty() {
                println!("还没有方案。示例：");
                println!("  profile save 校内公共场合 --ssid Campus-WiFi --suffix \"\"");
                println!("  profile save 宿舍         --ssid Dorm-WiFi   --suffix @yd");
                println!("  profile auto on");
                return 0;
            }
            let active = drcom_core::profiles::active(&cfg).unwrap_or("");
            for name in &use_profiles {
                let mark = if name == active { "  ← 当前" } else { "" };
                println!("{}{}", drcom_core::profiles::describe(&cfg, name), mark);
            }
            println!(
                "自动切换：{}",
                if cfg.profiles_auto_switch { "已开启 ✓" } else { "已关闭（方案只在你手动应用时生效）" }
            );
            0
        }
        "save" => {
            let name = match args.get(1) {
                Some(name) => name.clone(),
                None => {
                    eprintln!("用法：profile save <名字> [--ssid Wi-Fi名,另一个] [--suffix @yd|空串]");
                    return 2;
                }
            };
            // `--suffix ""` 是**显式清空**（校内公共场合 ✓）
            if suffix_flag_without_value(args) {
                eprintln!(
                    "提示：`--suffix` 后面没取到值（PowerShell 会把空参数吃掉 ✗）。\n      要清空后缀请用：--suffix 空　或　--no-suffix"
                );
            }
            if let Some(suffix) = suffix_from_args(args) {
                cfg.suffix = suffix;
            }
            let ssids: Vec<String> = take_opt(args, "--ssid").map(|v| vec![v]).unwrap_or_default();
            match drcom_core::profiles::save_profile(&mut cfg, &name, &ssids) {
                Ok(saved) => {
                    println!(
                        "已保存方案「{}」：后缀 {} · {}",
                        saved,
                        drcom_core::profiles::suffix_label(&cfg.suffix),
                        if ssids.is_empty() { "手动应用".to_string() } else { format!("匹配 {}", ssids.join(", ")) }
                    );
                    persist(&cfg)
                }
                Err(e) => {
                    eprintln!("{}", e);
                    1
                }
            }
        }
        "activate" => {
            let name = match args.get(1) {
                Some(name) => name.clone(),
                None => {
                    eprintln!("用法：profile activate <名字>");
                    return 2;
                }
            };
            match drcom_core::profiles::activate(&mut cfg, &name) {
                Ok(()) => {
                    println!(
                        "已应用方案「{}」：后缀 {}",
                        name,
                        drcom_core::profiles::suffix_label(&cfg.suffix)
                    );
                    persist(&cfg)
                }
                Err(e) => {
                    eprintln!("{}", e);
                    1
                }
            }
        }
        "delete" => {
            let name = match args.get(1) {
                Some(name) => name.clone(),
                None => {
                    eprintln!("用法：profile delete <名字>");
                    return 2;
                }
            };
            match drcom_core::profiles::delete(&mut cfg, &name) {
                Ok(()) => {
                    println!("已删除方案「{}」（配置值保持不动 ✓）", name);
                    persist(&cfg)
                }
                Err(e) => {
                    eprintln!("{}", e);
                    1
                }
            }
        }
        // auto on / auto off
        _ => {
            let on = matches!(args.get(1).map(String::as_str), Some("on") | Some("true") | Some("1"));
            cfg.profiles_auto_switch = on;
            println!(
                "自动切换：{}",
                if on { "已开启 ✓（走进匹配的 Wi-Fi 就自动切方案）" } else { "已关闭" }
            );
            persist(&cfg)
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

/// 解析「后缀」参数（纯函数，好测 ✓）。
///
/// 为什么要专门的函数：**传空字符串在 PowerShell 里会被吃掉** ✗
/// （`--suffix ""` 到不了子进程 → 用户以为清空了，实际没清 ✗）。
/// 所以提供三种等价写法：
///   - `--no-suffix`（最稳 ✓）
///   - `--suffix none` / `--suffix 空` / `--suffix -`
///   - `--suffix @yd`（正常赋值 ✓）
fn suffix_from_args(args: &[String]) -> Option<String> {
    if args.iter().any(|a| a == "--no-suffix") {
        return Some(String::new());
    }
    let value = take_opt(args, "--suffix")?;
    let trimmed = value.trim();
    if matches!(trimmed, "none" | "空" | "无" | "-") {
        Some(String::new())
    } else {
        Some(trimmed.to_string())
    }
}

/// `--suffix` 出现了但**没取到值**（正是 PowerShell 吃掉空参数的那种情形 ✗）→ 该提示用户 ✓
fn suffix_flag_without_value(args: &[String]) -> bool {
    args.iter().any(|a| a == "--suffix") && take_opt(args, "--suffix").is_none()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_cmd_validates_actions_and_dry_run_touches_nothing() {
        assert_eq!(service_cmd(&["nope".to_string()]), 2, "动作写错要提示用法 ✓");
        assert_eq!(
            service_cmd(&["install".to_string(), "--dry-run".to_string()]),
            0,
            "--dry-run 只打印计划（不动系统 ✓）"
        );
        assert_eq!(service_cmd(&["status".to_string()]), 0, "status 只读 ✓");
    }

    #[test]
    fn autostart_cmd_validates_action_and_dry_run_touches_nothing() {
        assert_eq!(autostart_cmd(&["nope".to_string()]), 2, "动作写错要提示用法 ✓");
        assert_eq!(
            autostart_cmd(&["on".to_string(), "--dry-run".to_string()]),
            0,
            "--dry-run 只打印计划（不动系统 ✓）"
        );
        assert_eq!(autostart_cmd(&["status".to_string()]), 0, "status 只读 ✓");
    }

    #[test]
    fn portal_cmd_reports_failure_without_panicking() {
        // 指到本机一个必然没人听的端口 → 立刻 connection refused ✓（不依赖外网 ✓）
        let args: Vec<String> = ["--url", "http://127.0.0.1:9/", "--timeout", "1"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(portal_cmd(&args), 1, "连不上时退出码非 0 ✓");
    }

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
    fn profile_subcommand_rejects_unknown_action_without_needing_config() {
        // 子命令校验在「读配置」之前 → 写错命令时不需要配置文件也能拿到正确提示 ✓
        assert_eq!(profile_cmd(&["nope".to_string()]), 2);
    }

    #[test]
    fn profile_save_requires_a_name() {
        assert_eq!(profile_cmd(&["save".to_string()]), 2, "缺名字要给出用法 ✗");
        assert_eq!(profile_cmd(&["activate".to_string()]), 2);
        assert_eq!(profile_cmd(&["delete".to_string()]), 2);
    }

    #[test]
    fn suffix_flag_handles_the_powershell_empty_arg_trap() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        // 正常赋值 ✓
        assert_eq!(suffix_from_args(&s(&["save", "宿舍", "--suffix", "@yd"])), Some("@yd".into()));
        // 显式清空：三种等价写法 ✓
        assert_eq!(suffix_from_args(&s(&["save", "校内", "--no-suffix"])), Some(String::new()));
        assert_eq!(suffix_from_args(&s(&["save", "校内", "--suffix", "空"])), Some(String::new()));
        assert_eq!(suffix_from_args(&s(&["save", "校内", "--suffix", "none"])), Some(String::new()));
        assert_eq!(suffix_from_args(&s(&["save", "校内", "--suffix", "-"])), Some(String::new()));
        // 没写 = 不动（None ✓）
        assert_eq!(suffix_from_args(&s(&["save", "校内", "--ssid", "X"])), None);
        // 「写了但没值」= PowerShell 吃掉空参数的情形 ✗ → 要能识别出来并提示 ✓
        assert!(suffix_flag_without_value(&s(&["save", "校内", "--suffix"])));
        assert!(!suffix_flag_without_value(&s(&["save", "校内", "--suffix", "空"])));
        assert!(!suffix_flag_without_value(&s(&["save", "校内", "--no-suffix"])));
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
