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
    channel::{Channel, Version},
    config::Config,
    logfile::RotatingLog,
    platform, portal, protocol,
    scheduler::Scheduler,
    secret, service, session, timefmt, update, update_apply, Outcome, Status,
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
        Some("checksum") => checksum_cmd(&args[1..]),
        Some("update") => update_cmd(&args[1..]),
        Some("package-files") => package_files_cmd(&args[1..]),
        Some("archive") => archive_cmd(&args[1..]),
        Some("serve") => serve(&args[1..]),
        Some(other) => {
            eprintln!("未知命令: {}", other);
            print_help();
            2
        }
    }
}

fn print_help() {
    println!("{}", help_text());
}

/// 帮助文本（**纯函数**：单测能断言「帮助里不再宣传 --password」✓）。
fn help_text() -> String {
    format!(
        "{} {}（跨平台核心）\n\n\
         用法:\n  stardust-flash-link <命令> [选项]\n\n\
         命令:\n  \
         version                     显示版本 / 通道 / 平台\n  \
         status                      显示状态 JSON（与本地 API 同一份数据）\n  \
         selfcheck                   内置自检\n  \
         login [--dry-run] [--host H] [--account A] [--suffix S]\n  \
         run [--once]                守护循环：按配置间隔检查、失败走退避；--once 退出码：成功/跳过 0、失败 1\n  \
         profile list|save|activate|delete|auto   配置方案（校内公共场合=无尾缀、宿舍=@yd 等）\n  \
         diagnostics [--out F] [--days N]        生成脱敏诊断包（ZIP，密码永不进包 ✓）\n  \
         portal [--url U] [--timeout N]         门户检测：是不是被校园网门户拦住了（未认证会被 302 到登录页）\n  \
         autostart on|off|status [--dry-run]    开机自启（当前用户级，免管理员 ✓；跑的是无界面循环）\n  \
         service install|uninstall|start|stop|restart|status [--dry-run]   注册成常驻服务（Windows 走 NSSM ✓）\n  \
         checksum [文件]             算 SHA-256（不给文件就算自己 ✓）—— 打包校验 / 升级凭据 ✓\n  \
         update check [--channel C]  检查更新（只检查/下载/校验，**不自我替换** ✓）\n  \
         update download <路径> [--channel C]   检查 + 下载 + SHA256 校验（失败会清理半成品 ✓）\n  \
         update apply <安装包.zip> [--dry-run] [--no-restart]   用本地安装包自我替换（同目录 GUI+CLI 一起换，默认重启，不联网）\n  \
         update rollback [--version V] [--dry-run]   回滚到备份版本（备份在 安装目录/backup/<版本>/）\n  \
         update backups              列出所有备份版本（含每个版本备份了哪些程序）\n  \
         package-files --out DIR [--target T] [--exe E] [--arch A]   写出该平台随包分发的说明文件\n  \
         archive --out F.zip [--prefix 目录名] DIR...  打包目录成 ZIP（用自带实现 ✓；--prefix 固定包内顶层目录名）\n  \
         serve [--port N]            起本地控制 API（默认取配置 ui_port = {}，只绑 127.0.0.1）\n\n\
         配置目录: {}\n",
        drcom_core::APP_NAME,
        drcom_core::app_version_string(),
        server::DEFAULT_UI_PORT,
        platform::config_dir().display()
    )
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

/// **纯函数**：这次 serve 用哪个端口 ✓（审计 P3-6）。
///
/// 口径：`--port` 显式覆盖 > 配置里的 `ui_port`（默认 8848）> 内置默认值。
/// 旧实现永远用 8848 ✗ —— 2.x 用户把 UI 端口改过之后，3.0 的 serve 会和 2.x 服务抢 8848。
pub fn resolve_serve_port(override_value: Option<&str>, configured: u16) -> Result<u16, String> {
    match override_value {
        Some(raw) => raw
            .parse::<u16>()
            .map_err(|_| format!("端口不合法: {}", raw))
            .and_then(|port| {
                if port == 0 {
                    // 端口 0 = 系统随机分配：CLI 场景里用户要的是「监听某个端口」，不是随便一个 ✓
                    Err("端口不合法: 0（不能是 0，要 1–65535）".to_string())
                } else {
                    Ok(port)
                }
            }),
        // 配置里的 ui_port 由 Config::validate 保证 ≥ 1024 ✓；这里再兜一层 0 ✓
        None if configured == 0 => Ok(server::DEFAULT_UI_PORT),
        None => Ok(configured),
    }
}

/// `serve [--port N]`：起本地控制 API（只绑回环 ✓；默认端口取配置 `ui_port`）。
fn serve(args: &[String]) -> i32 {
    let configured = drcom_core::legacy::load_config_or_default().config.ui_port;
    let port = match resolve_serve_port(take_opt(args, "--port").as_deref(), configured) {
        Ok(port) => port,
        Err(error) => {
            eprintln!("{}", error);
            return 2;
        }
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

/// **纯函数**：login 的参数校验 ✓（返回可读错误即退出码 2 = 用法错误）。
///
/// 审计 P2-11：**删掉 `--password`** ✗ —— 密码只从 `password.txt` 读（AGENTS.md I6 / §9.4），
/// 写在命令行里会进 shell 历史与进程列表，同机其他用户/进程都能看到 ✗。
/// 这里显式给出可读错误，而不是「当成普通参数忽略后继续登录」✗。
fn login_args_error(args: &[String]) -> Option<String> {
    const ALLOWED: [&str; 4] = ["--dry-run", "--host", "--account", "--suffix"];
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if arg == "--password" || arg == "-p" {
            return Some(
                "不再支持 --password ✗ —— 密码只从 password.txt 读"
                    .to_string()
                    + "（别把密码放进命令行：shell 历史和进程列表里都看得到 ✗）",
            );
        }
        if !arg.starts_with("--") {
            return Some(format!("多余的位置参数：{}（login 只接受选项）", arg));
        }
        if !ALLOWED.contains(&arg.as_str()) {
            return Some(format!(
                "不认识的参数：{}（用法：login [--dry-run] [--host H] [--account A] [--suffix S]）",
                arg
            ));
        }
        // 与 take_opt 同口径（P3-3）：下一个参数是 **flag** 就不算值 → 不消费它，
        // 让它继续参与校验（于是 `--host --dry-run` = host 缺值 + 正常 dry-run ✓）
        if matches!(arg.as_str(), "--host" | "--account" | "--suffix")
            && args.get(index + 1).is_some_and(|next| !next.starts_with("--"))
        {
            index += 1;
        }
        index += 1;
    }
    None
}

/// `login`：读配置（命令行可覆盖）→ 查在线 → 需要时登录。
fn login(args: &[String]) -> i32 {
    // 先校验参数（用法错误 → 2，且**不读配置/不联网** ✓）
    if let Some(error) = login_args_error(args) {
        eprintln!("{}", error);
        eprintln!("用法：login [--dry-run] [--host H] [--account A] [--suffix S]");
        return 2;
    }
    // 读配置走 legacy 兼容层（审计 P1-3）：3.0 没有配置、2.x 有时，只读沿用 2.x 的账号/网关 ✓
    let mut cfg = match drcom_core::legacy::load_config() {
        Ok(read) => read.config,
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
    // 唯一来源：password.txt（# 开头是密码原文、剥 BOM、剥提示行 ✓）；命令行不再接受密码 ✗
    let password = drcom_core::password::read();

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
            "密码为空：请写入 {}（一行密码；以 `#` 开头的密码也照原样读取）",
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
/// 递归收集目录里的文件 ✓ →（ZIP 条目名 = `<目录名>/<相对路径>`，分隔符统一 `/` ✓）。
fn collect_files(
    root: &std::path::Path,
    prefix: &str,
    dir: &std::path::Path,
    out: &mut Vec<(String, std::path::PathBuf)>,
) -> Result<(), String> {
    let entries =
        std::fs::read_dir(dir).map_err(|e| format!("读目录 {} 失败: {}", dir.display(), e))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("读目录 {} 失败: {}", dir.display(), e))?;
        let path = entry.path();
        if path.is_dir() {
            collect_files(root, prefix, &path, out)?;
            continue;
        }
        let relative = path
            .strip_prefix(root)
            .map_err(|e| format!("算相对路径失败: {}", e))?;
        let name = relative
            .components()
            .map(|part| part.as_os_str().to_string_lossy().to_string())
            .collect::<Vec<_>>()
            .join("/");
        out.push((format!("{}/{}", prefix, name), path));
    }
    Ok(())
}

/// **纯函数**：`--prefix` 的合法性 ✓（审计 P3-16）。
///
/// 包内顶层目录名必须是**单段**（不带路径分隔符、不是 `.` / `..`、没有控制字符），
/// 否则会造出「路径穿越型」条目名，解压器行为不可控 ✗。
pub fn validate_zip_prefix(prefix: &str) -> Result<(), String> {
    if prefix.trim().is_empty() {
        return Err(
            "--prefix 不能为空 ✗（例：--prefix stardust-flash-link-3.0.0.0-preview.5-linux-x86_64）"
                .to_string(),
        );
    }
    if prefix.trim() != prefix {
        return Err(format!("--prefix 首尾不能有空白 ✗: {:?}", prefix));
    }
    if prefix.contains('/') || prefix.contains('\\') {
        return Err(format!("--prefix 只能是单段目录名，不能带路径分隔符 ✗: {}", prefix));
    }
    if prefix == "." || prefix == ".." {
        return Err(format!("--prefix 不能是 . 或 .. ✗: {}", prefix));
    }
    if prefix.chars().any(char::is_control) {
        return Err(format!("--prefix 不能包含控制字符 ✗: {:?}", prefix));
    }
    Ok(())
}

/// `archive --out <文件.zip> [--prefix 目录名] <目录>...`：用**自带的 ZIP 实现**打包 ✓
/// （CI 里因此不需要外部 `zip`/`tar` ✓，三平台行为一致 ✓；无压缩 = store ✓）。
///
/// 审计 P3-16：包内顶层目录**原来取输入目录名**（CI 传 `pkg/staging` → zip 里是 `staging/…`，
/// 用户解压得到一个叫 staging 的目录；换个调用方式（绝对路径 / 尾斜杠）产物结构还会变 ✗）。
/// 现在可以显式 `--prefix` 固定它；**不传时保持旧行为**（向后兼容 ✓）。
fn archive_cmd(args: &[String]) -> i32 {
    let out_path = match take_opt(args, "--out") {
        Some(value) => std::path::PathBuf::from(value),
        None => {
            eprintln!("用法: archive --out <文件.zip> [--prefix 目录名] <目录>...");
            return 2;
        }
    };
    // 显式顶层目录名（不给 = 旧行为：取输入目录名 ✓）
    let explicit_prefix = match take_opt(args, "--prefix") {
        Some(value) => {
            if let Err(e) = validate_zip_prefix(&value) {
                eprintln!("{}", e);
                return 2;
            }
            Some(value)
        }
        None => None,
    };
    // 目录 = 除 `--out` / `--prefix` 及其取值以外的位置参数 ✓
    let mut dirs: Vec<std::path::PathBuf> = Vec::new();
    let mut skip_next = false;
    for arg in args {
        if skip_next {
            skip_next = false;
            continue;
        }
        if arg == "--out" || arg == "--prefix" {
            skip_next = true;
            continue;
        }
        if arg.starts_with("--") {
            continue;
        }
        dirs.push(std::path::PathBuf::from(arg));
    }
    if dirs.is_empty() {
        eprintln!("至少要给一个目录 ✓（例：archive --out pkg.zip staging）");
        return 2;
    }

    let mut collected: Vec<(String, std::path::PathBuf)> = Vec::new();
    for dir in &dirs {
        let prefix = match &explicit_prefix {
            Some(prefix) => prefix.clone(),
            None => dir
                .file_name()
                .map(|name| name.to_string_lossy().to_string())
                .unwrap_or_else(|| "package".to_string()),
        };
        if let Err(e) = collect_files(dir, &prefix, dir, &mut collected) {
            eprintln!("{}", e);
            return 1;
        }
    }
    if collected.is_empty() {
        eprintln!("目录里没有文件 ✗（{}）", dirs[0].display());
        return 1;
    }
    collected.sort_by(|a, b| a.0.cmp(&b.0)); // 顺序稳定 → 产物可复现 ✓

    let mut blobs: Vec<Vec<u8>> = Vec::with_capacity(collected.len());
    for (_, path) in &collected {
        match std::fs::read(path) {
            Ok(bytes) => blobs.push(bytes),
            Err(e) => {
                eprintln!("读 {} 失败: {}", path.display(), e);
                return 1;
            }
        }
    }
    let entries: Vec<drcom_core::zipwriter::ZipEntry<'_>> = collected
        .iter()
        .zip(blobs.iter())
        .map(|((name, _), data)| drcom_core::zipwriter::ZipEntry { name, data })
        .collect();
    let bytes = match drcom_core::zipwriter::build(&entries, std::time::SystemTime::now()) {
        Ok(bytes) => bytes,
        Err(e) => {
            eprintln!("打包失败: {}", e);
            return 1;
        }
    };
    if let Some(parent) = out_path.parent() {
        if !parent.as_os_str().is_empty() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                eprintln!("创建 {} 失败: {}", parent.display(), e);
                return 1;
            }
        }
    }
    if let Err(e) = std::fs::write(&out_path, &bytes) {
        eprintln!("写 {} 失败: {}", out_path.display(), e);
        return 1;
    }

    // 自检：**把刚写出去的文件读回来解析中央目录** ✓（写完就坏要当场发现 ✗）
    let read_back = std::fs::read(&out_path)
        .map_err(|e| e.to_string())
        .ok()
        .and_then(|raw| drcom_core::zipwriter::entry_names(&raw));
    match read_back {
        Some(names) => {
            println!(
                "{}  （{} 字节，{} 个条目 ✓ 自检通过）",
                out_path.display(),
                bytes.len(),
                names.len()
            );
            for name in &names {
                println!("  {}", name);
            }
            0
        }
        None => {
            eprintln!("写出的 ZIP 自检失败 ✗（中央目录读不回来）");
            1
        }
    }
}

/// 写一个文件并把名字记下来 ✓。
fn write_package_file(
    dir: &std::path::Path,
    name: &str,
    content: &str,
    written: &mut Vec<String>,
) -> Result<(), String> {
    let path = dir.join(name);
    std::fs::write(&path, content).map_err(|e| format!("写 {} 失败: {}", path.display(), e))?;
    written.push(name.to_string());
    Ok(())
}

/// `package-files --out DIR`：写出该平台随包分发的说明文件 ✓。
///
/// CI 打包时会先调它 ✓ —— 于是 `.desktop` / `Info.plist` / `deb-control` 的内容
/// 也就**受单测保护**了 ✓（比把这些字符串散在 YAML 里可控 ✓）。
/// 第一行输出的是产物基名（`stardust-flash-link-<版本>-<目标>` ✓），脚本可以直接拿去用 ✓。
fn package_files_cmd(args: &[String]) -> i32 {
    let out_dir = std::path::PathBuf::from(take_opt(args, "--out").unwrap_or_else(|| ".".to_string()));
    if let Err(e) = std::fs::create_dir_all(&out_dir) {
        eprintln!("创建 {} 失败: {}", out_dir.display(), e);
        return 1;
    }
    let current = std::env::current_exe().ok();
    let exe_name = current
        .as_ref()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
        .unwrap_or_else(|| "stardust-flash-link".to_string());
    let size = current
        .as_ref()
        .and_then(|p| std::fs::metadata(p).ok())
        .map(|meta| meta.len())
        .unwrap_or(0);
    // 交叉编译时，宿主编出来的辅助程序会自报宿主目标 ✗ → 允许显式覆盖 ✓
    let info = drcom_core::package::PackageInfo::current(&exe_name, size)
        .with_overrides(
            take_opt(args, "--target").as_deref(),
            take_opt(args, "--exe").as_deref(),
            take_opt(args, "--version").as_deref(),
        )
        // deb 架构也要能覆盖 ✓（宿主是 amd64，但包可能是 armhf ✓）
        .with_arch(take_opt(args, "--arch").as_deref());

    let mut written: Vec<String> = Vec::new();
    let mut result = write_package_file(
        &out_dir,
        "README.txt",
        &drcom_core::package::readme_txt(&info),
        &mut written,
    );
    if result.is_ok() {
        result = match platform::Os::current() {
            platform::Os::Linux => write_package_file(
                &out_dir,
                &format!("{}.desktop", platform::APP_ID),
                &drcom_core::package::desktop_entry(&info),
                &mut written,
            )
            .and_then(|()| {
                write_package_file(
                    &out_dir,
                    "deb-control",
                    &drcom_core::package::deb_control(&info),
                    &mut written,
                )
            }),
            platform::Os::MacOs => write_package_file(
                &out_dir,
                "Info.plist",
                &drcom_core::package::macos_info_plist(&info),
                &mut written,
            ),
            // Windows 先用 zip 分发 ✓（安装器在 M3 后续做 ✓）
            platform::Os::Windows => Ok(()),
        };
    }
    match result {
        Ok(()) => {
            println!("{}", drcom_core::package::artifact_base(&info));
            for name in &written {
                println!("  已写出 {}", name);
            }
            0
        }
        Err(e) => {
            eprintln!("{}", e);
            1
        }
    }
}

/// `checksum`：算 SHA-256 ✓（只读 ✓）—— 打包校验与自动升级的凭据都走它 ✓。
fn checksum_cmd(args: &[String]) -> i32 {
    let target = match args.first() {
        Some(path) if !path.starts_with("--") => std::path::PathBuf::from(path),
        _ => match std::env::current_exe() {
            Ok(exe) => exe,
            Err(e) => {
                eprintln!("拿不到自身路径: {}", e);
                return 1;
            }
        },
    };
    match drcom_core::checksum::sha256_file(&target) {
        Ok(hex) => {
            // 格式与 `sha256sum` 对齐：`<hex>  <文件名>` ✓（两个空格 ✓）
            println!("{}  {}", hex, target.display());
            0
        }
        Err(e) => {
            eprintln!("{}", e);
            1
        }
    }
}

/// update check / download：M4 自动更新的**第一块**（只检查 / 下载 / 校验，不自我替换 ✓）。
///
/// 通道默认取配置里的 update_channel（正式版 → 只认正式版 ✓；预览 → 认 preview/rc ✓），
/// 可用 --channel 临时覆盖；auto_update_enabled 只影响「自动」，手动检查照跑 ✓。
fn update_cmd(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("check") => update_check_cmd(&args[1..]),
        Some("download") => update_download_cmd(&args[1..]),
        Some("apply") => update_apply_cmd(&args[1..]),
        Some("rollback") => update_rollback_cmd(&args[1..]),
        Some("backups") => update_backups_cmd(&args[1..]),
        Some("help") | Some("--help") | Some("-h") => {
            print_update_help();
            0
        }
        Some(other) => {
            eprintln!(
                "未知的 update 子命令：{}（可用：check / download / apply / rollback / backups）",
                other
            );
            2
        }
        None => {
            print_update_help();
            2
        }
    }
}

/// update 的全部用法（`update --help` / 输错子命令时打 ✓）。
fn print_update_help() {
    println!("用法:");
    println!("  update check [--channel snapshot|preview|rc|release]   检查更新（联网）");
    println!(
        "  update download <目标路径> [--channel snapshot|preview|rc|release]   检查 + 下载 + SHA256 校验（不安装）"
    );
    println!(
        "  update apply <安装包.zip> [--dry-run] [--no-restart] [--install-dir <目录>]\n    用本地安装包**自我替换**（同目录 GUI+CLI 一起换；先备份，失败自动回滚；默认替换完拉起新程序；不联网）"
    );
    println!(
        "  update rollback [--version V] [--dry-run] [--no-restart] [--install-dir <目录>]\n    回滚到 backup/<版本>/ 里的备份（备份里有几个程序就恢复几个；不给 --version 就取最新）"
    );
    println!("  update backups [--install-dir <目录>]                 列出所有备份版本（含每个版本备份了哪些程序）");
    println!("提示: 安装目录默认 = 本程序所在目录；备份目录 = <安装目录>/backup/<版本>/");
}

/// **纯函数**：解析这次要用的通道（--channel 覆盖 > 配置 update_channel ✓）。
fn resolve_update_channel(override_value: Option<&str>, config: &Config) -> Result<Channel, String> {
    match override_value {
        Some(raw) => Channel::parse(raw).ok_or_else(|| {
            format!("通道写错了：{}（可用：snapshot / preview / rc / release）", raw)
        }),
        None => Ok(update::channel_from_config(config)),
    }
}

/// 读配置：坏配置不阻塞检查（2.x 只读回退 / 默认值由 legacy 层处理 ✓）。
fn config_for_update() -> (Config, String) {
    match drcom_core::legacy::load_config() {
        Ok(read) => {
            let hint = if read.is_legacy() { read.legacy_hint() } else { String::new() };
            (read.config, hint)
        }
        Err(error) => {
            eprintln!("（读配置失败，先按默认值检查：{}）", error);
            (Config::default(), String::new())
        }
    }
}

fn command_channel(args: &[String], config: &Config) -> Option<Channel> {
    match resolve_update_channel(take_opt(args, "--channel").as_deref(), config) {
        Ok(channel) => Some(channel),
        Err(error) => {
            eprintln!("{}", error);
            None
        }
    }
}

/// update check：打印当前通道 / 最新版本 / 是否有更新 / 资产名 / 大小 / SHA256 / 下载 URL ✓。
fn update_check_cmd(args: &[String]) -> i32 {
    let (config, legacy_hint) = config_for_update();
    let Some(channel) = command_channel(args, &config) else { return 2 };
    println!("{} 自动更新检查", drcom_core::APP_NAME);
    println!("当前版本: {}", drcom_core::app_version_string());
    println!(
        "自动更新: {}",
        if config.auto_update_enabled {
            "已启用 ✓"
        } else {
            "未启用（这次是手动检查 ✓）"
        }
    );
    if !legacy_hint.is_empty() {
        println!("配置来源: {}", legacy_hint);
    }
    // HTTPS 走系统 curl（std 没有 TLS；见 drcom_core::net::CurlHttp 的说明 ✓）
    let http = net::CurlHttp;
    let check = update::check_for_update(drcom_core::app_version(), channel, &http);
    for line in update::report_lines(&check, channel) {
        println!("{}", line);
    }
    match check {
        update::UpdateCheck::UpToDate | update::UpdateCheck::Available { .. } => 0,
        _ => 1,
    }
}

/// update download：检查 + 下载 + SHA256 校验，**不安装 / 不自我替换** ✓。
fn update_download_cmd(args: &[String]) -> i32 {
    let raw_target = match args.first() {
        Some(value) if !value.starts_with("--") => value.clone(),
        _ => {
            eprintln!("用法: update download <目标路径> [--channel snapshot|preview|rc|release]");
            eprintln!("（目标路径可以是文件；给一个已存在的目录时用资产名当文件名 ✓）");
            return 2;
        }
    };
    let (config, legacy_hint) = config_for_update();
    let Some(channel) = command_channel(args, &config) else { return 2 };
    println!("{} 自动更新下载", drcom_core::APP_NAME);
    println!("当前版本: {}", drcom_core::app_version_string());
    if !legacy_hint.is_empty() {
        println!("配置来源: {}", legacy_hint);
    }
    let http = net::CurlHttp;
    let check = update::check_for_update(drcom_core::app_version(), channel, &http);
    for line in update::report_lines(&check, channel) {
        println!("{}", line);
    }
    let update::UpdateCheck::Available { asset, .. } = &check else {
        println!("没有可下载的更新（没有改动任何文件 ✓）。");
        return 1;
    };
    let dest = download_destination(&raw_target, &asset.name);
    println!();
    println!("开始下载并校验（SHA256 缺失会直接拒绝 ✓）...");
    match update::download_and_verify(&http, asset, &dest) {
        Ok(bytes) => {
            println!("下载完成: {}（{} 字节）", dest.display(), bytes);
            println!("SHA256: {}", asset.sha256.as_deref().unwrap_or("（缺失）"));
            println!(
                "文件已校验通过；**M4 这一块不会自我替换 / 安装**（Windows 正在运行的 exe 会被锁 ✗）"
            );
            0
        }
        Err(error) => {
            eprintln!("{}", error);
            1
        }
    }
}

// ── update apply / rollback / backups（M4 第二块：自我替换 / 回滚 ✓）────────────

/// `--flag` 开关（布尔参数，不吃下一个参数 ✓）。
fn has_flag(args: &[String], key: &str) -> bool {
    args.iter().any(|arg| arg == key)
}

/// 取第一个**位置参数**（跳过 `--xxx` 开关与 `value_flags` 的取值 ✓）。
fn first_positional(args: &[String], value_flags: &[&str]) -> Option<String> {
    let mut skip_next = false;
    for arg in args {
        if skip_next {
            skip_next = false;
            continue;
        }
        if value_flags.iter().any(|flag| flag == arg) {
            skip_next = true;
            continue;
        }
        if arg.starts_with("--") {
            continue;
        }
        return Some(arg.clone());
    }
    None
}

/// 安装目录：--install-dir 给了就用它；否则 = **本程序所在目录** ✓（便携版口径）。
///
/// 3.0 的安装目录就是「exe 放哪儿」，不是注册表里的路径 —— 所以不用去猜 ✓。
fn resolve_install_dir(install_dir: Option<&str>) -> Result<std::path::PathBuf, String> {
    match install_dir {
        Some(raw) => {
            let dir = std::path::PathBuf::from(raw);
            if !dir.is_dir() {
                return Err(format!("安装目录不存在：{}", dir.display()));
            }
            Ok(dir)
        }
        None => {
            let exe = std::env::current_exe()
                .map_err(|error| format!("拿不到本程序路径：{}", error))?;
            exe.parent()
                .map(std::path::Path::to_path_buf)
                .ok_or_else(|| "本程序没有所在目录 —— 用 --install-dir 指对目录".to_string())
        }
    }
}

/// 备份根目录 = <安装目录>/backup ✓。
fn resolve_backup_root(install_dir: Option<&str>) -> Result<std::path::PathBuf, String> {
    Ok(resolve_install_dir(install_dir)?.join(update_apply::BACKUP_DIR_NAME))
}

/// 安装目录里至少要有一个本项目程序，否则说明目录指错了 ✓。
fn require_programs(dir: &std::path::Path) -> Result<Vec<(String, std::path::PathBuf)>, String> {
    let programs = update_apply::installed_programs(dir);
    if programs.is_empty() {
        return Err(format!(
            "安装目录里没有本项目的程序（{} / {}）—— 用 --install-dir 指对目录",
            update_apply::expected_exe_name(),
            update_apply::cli_exe_name()
        ));
    }
    Ok(programs)
}

/// update apply <zip>：**本地安装包 → 校验 → 备份 → 一起替换**（不联网）。
///
/// 安装目录里的 GUI 与 CLI **一起换**（只换正在运行的那个会留下「一旧一新」✗）。
fn update_apply_cmd(args: &[String]) -> i32 {
    let dry_run = has_flag(args, "--dry-run");
    let no_restart = has_flag(args, "--no-restart");
    let install_dir_arg = take_opt(args, "--install-dir");
    let Some(raw_zip) = first_positional(args, &["--install-dir"]) else {
        eprintln!("用法: update apply <安装包.zip> [--dry-run] [--no-restart] [--install-dir <目录>]");
        eprintln!("（安装包 = update download 下来的那个 ZIP；apply **不联网** ✓）");
        return 2;
    };
    let zip_path = std::path::PathBuf::from(&raw_zip);
    let metadata = match std::fs::metadata(&zip_path) {
        Ok(metadata) => metadata,
        Err(error) => {
            eprintln!("找不到安装包 {}：{}", zip_path.display(), error);
            return 1;
        }
    };
    if !metadata.is_file() {
        eprintln!("安装包不是一个文件：{}", zip_path.display());
        return 1;
    }
    if metadata.len() == 0 {
        eprintln!("安装包是空文件：{}", zip_path.display());
        return 1;
    }
    if metadata.len() > update_apply::MAX_PACKAGE_BYTES {
        eprintln!(
            "安装包太大（{} 字节，上限 {}）—— 不像是本项目的安装包",
            metadata.len(),
            update_apply::MAX_PACKAGE_BYTES
        );
        return 1;
    }
    let install_dir = match resolve_install_dir(install_dir_arg.as_deref()) {
        Ok(dir) => dir,
        Err(error) => {
            eprintln!("{}", error);
            return 1;
        }
    };
    let programs = match require_programs(&install_dir) {
        Ok(programs) => programs,
        Err(error) => {
            eprintln!("{}", error);
            return 1;
        }
    };
    let backup_root = install_dir.join(update_apply::BACKUP_DIR_NAME);
    let zip_bytes = match std::fs::read(&zip_path) {
        Ok(bytes) => bytes,
        Err(error) => {
            eprintln!("读安装包 {} 失败：{}", zip_path.display(), error);
            return 1;
        }
    };

    println!("{} 自我替换（本地安装包，**不联网** ✓）", drcom_core::APP_NAME);
    println!("当前版本: {}", drcom_core::app_version_string());
    println!("安装目录: {}", install_dir.display());
    let names: Vec<&str> = programs.iter().map(|(name, _)| name.as_str()).collect();
    println!("要更新的程序: {}（{} 个）", names.join(" / "), names.len());
    println!("安装包: {}（{} 字节）", zip_path.display(), metadata.len());
    println!("备份目录: {}", backup_root.display());

    let current_version = drcom_core::app_version_string();
    let outcome = match update_apply::install_zip_into_dir(
        &install_dir,
        &zip_bytes,
        &backup_root,
        &current_version,
        None,
        dry_run,
    ) {
        Ok(outcome) => outcome,
        Err(error) => {
            eprintln!("{}", error);
            return 1;
        }
    };
    println!();
    println!(
        "{}替换计划（{} 个程序）:",
        if dry_run { "dry-run：" } else { "" },
        outcome.plans.len()
    );
    for plan in &outcome.plans {
        println!("  · {}", plan.installed_exe.display());
        for step in &plan.steps {
            println!("    {}", step);
        }
    }
    if dry_run {
        println!();
        println!("dry-run：以上只是计划，**没有改动任何文件** ✓（去掉 --dry-run 才会真的替换）");
        return 0;
    }
    println!();
    println!("已安装 {} ✓（{} 个程序）", outcome.target_version, outcome.plans.len());
    for plan in &outcome.plans {
        println!(
            "  {}（备份 {}）",
            plan.installed_exe.display(),
            plan.backup_exe.display()
        );
    }
    for warning in &outcome.warnings {
        println!("提示: {}", warning);
    }
    if no_restart {
        println!("按 --no-restart：没有自动重启 —— **重启程序后生效** ✓");
        return 0;
    }
    let Some(target) = outcome.restart_target() else {
        eprintln!("内部错误：没有可重启的程序");
        return 1;
    };
    match update_apply::restart_detached(&target) {
        Ok(()) => {
            println!("已拉起新程序 ✓（本进程即将退出）");
            0
        }
        Err(error) => {
            eprintln!("{}", error);
            1
        }
    }
}

/// update rollback：从备份把**全部程序**换回去（--dry-run 只打印计划）。
fn update_rollback_cmd(args: &[String]) -> i32 {
    let dry_run = has_flag(args, "--dry-run");
    let no_restart = has_flag(args, "--no-restart");
    let install_dir_arg = take_opt(args, "--install-dir");
    let version = take_opt(args, "--version");
    if let Some(stray) = first_positional(args, &["--install-dir", "--version"]) {
        eprintln!(
            "不认识的参数：{}（用法：update rollback [--version V] [--dry-run] [--no-restart] [--install-dir <目录>]）",
            stray
        );
        return 2;
    }
    let install_dir = match resolve_install_dir(install_dir_arg.as_deref()) {
        Ok(dir) => dir,
        Err(error) => {
            eprintln!("{}", error);
            return 1;
        }
    };
    let backup_root = install_dir.join(update_apply::BACKUP_DIR_NAME);
    let versions = update_apply::backup_versions(&backup_root);
    if versions.is_empty() {
        eprintln!(
            "还没有任何备份（{}）—— 只有做过一次 update apply 之后才有可回滚的版本",
            backup_root.display()
        );
        return 1;
    }
    println!("{} 回滚", drcom_core::APP_NAME);
    println!("安装目录: {}", install_dir.display());
    println!("备份目录: {}", backup_root.display());
    println!("可用备份:");
    for (backup_version, files) in &versions {
        let names: Vec<&str> = files.iter().map(|(name, _)| name.as_str()).collect();
        println!(
            "  {}  （{} 个程序：{}）",
            backup_version,
            names.len(),
            names.join(" / ")
        );
    }
    let plan = match update_apply::plan_rollback_all(&install_dir, &backup_root, version.as_deref()) {
        Ok(plan) => plan,
        Err(error) => {
            eprintln!("{}", error);
            return 1;
        }
    };
    println!();
    println!(
        "{}回滚计划（目标版本 {}）:",
        if dry_run { "dry-run：" } else { "" },
        plan.version
    );
    for step in &plan.steps {
        println!("  {}", step);
    }
    for note in &plan.notes {
        println!("  说明: {}", note);
    }
    if dry_run {
        println!();
        println!("dry-run：以上只是计划，**没有改动任何文件** ✓");
        return 0;
    }
    let outcome = match update_apply::rollback_programs(&plan, false) {
        Ok(outcome) => outcome,
        Err(error) => {
            eprintln!("{}", error);
            return 1;
        }
    };
    println!();
    println!("已回滚到 {} ✓（{} 个程序）", outcome.version, outcome.new_exes.len());
    for (exe, from) in outcome.new_exes.iter().zip(outcome.restored_from.iter()) {
        println!("  {}（来自 {}）", exe.display(), from.display());
    }
    for exe in &outcome.new_exes {
        if let Some(dir) = exe.parent() {
            let name = exe
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default();
            for warning in update_apply::cleanup_stale_files(dir, &name) {
                println!("提示: {}", warning);
            }
        }
    }
    if no_restart {
        println!("按 --no-restart：没有自动重启 —— **重启程序后生效** ✓");
        return 0;
    }
    let Some(target) = outcome.restart_target() else {
        eprintln!("内部错误：没有可重启的程序");
        return 1;
    };
    match update_apply::restart_detached(&target) {
        Ok(()) => {
            println!("已拉起回滚后的程序 ✓（本进程即将退出）");
            0
        }
        Err(error) => {
            eprintln!("{}", error);
            1
        }
    }
}

/// update backups：列出备份（只读；每个版本带上备份了哪些程序）。
fn update_backups_cmd(args: &[String]) -> i32 {
    let install_dir_arg = take_opt(args, "--install-dir");
    if let Some(stray) = first_positional(args, &["--install-dir"]) {
        eprintln!(
            "不认识的参数：{}（用法：update backups [--install-dir <目录>]）",
            stray
        );
        return 2;
    }
    let backup_root = match resolve_backup_root(install_dir_arg.as_deref()) {
        Ok(root) => root,
        Err(error) => {
            eprintln!("{}", error);
            return 1;
        }
    };
    println!("{} 备份列表", drcom_core::APP_NAME);
    println!("备份目录: {}", backup_root.display());
    let versions = update_apply::backup_versions(&backup_root);
    if versions.is_empty() {
        println!("还没有备份 —— 每次 update apply 都会自动备份一份 ✓");
        return 0;
    }
    for (version, files) in &versions {
        println!("  {}", version);
        for (name, path) in files {
            let size = std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0);
            println!("    {}  {} 字节  {}", name, size, path.display());
        }
    }
    println!("回滚：update rollback --version <版本>（不给 --version 就取最新的 ✓）");
    0
}

/// **纯函数**：目标路径是已存在的目录 → 用资产名当文件名（避免把文件写成目录名 ✗）。
fn download_destination(raw: &str, asset_name: &str) -> std::path::PathBuf {
    let path = std::path::PathBuf::from(raw);
    if path.is_dir() {
        path.join(asset_name)
    } else {
        path
    }
}

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
            // 平台分支收在 drcom-core 的纯函数里（R2 / 审计 P2-10）：这里只查一次 Os 再往下传 ✓
            let os = platform::Os::current();
            println!("常驻方式: {}", service::flavor_name());
            println!("管理工具: {}（{}）", service::manager_program(), if available { "可用 ✓" } else { "没找到 ✗" });
            println!("状态: {}", service::status().label_cn());
            if service::status_shows_shared_name_note(os) {
                println!("{}", service::shared_name_note());
            }
            if !available {
                println!();
                println!("{}", service::missing_manager_hint(os));
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

    // 同上：诊断包里也要反映「配置实际来自 2.x 还是 3.0」（来源字段在 status 里 ✓）
    let cfg = drcom_core::legacy::load_config_or_default().config;
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
    let mut legacy_announced = false;
    loop {
        // 读配置走 legacy 兼容层（审计 P1-3）：3.0 数据目录没有配置时**只读**沿用 2.x 的，
        // 否则接管 2.x 服务后会「账号未设置」静默掉线 ✗
        let read = match drcom_core::legacy::load_config() {
            Ok(read) => read,
            Err(e) => {
                eprintln!("{}", e);
                if once {
                    return 1;
                }
                std::thread::sleep(Duration::from_secs(30));
                continue;
            }
        };
        // 把「这次读的是 2.x 的数据」写进**业务日志**（只写一次，避免每轮刷屏 ✓）
        if !legacy_announced && read.is_legacy() {
            legacy_announced = true;
            let legacy = read.path.clone().unwrap_or_default();
            let line = format!(
                "{} {}",
                timefmt::format_utc(SystemTime::now()),
                drcom_core::legacy::notice_text(&legacy, &platform::data_dir())
            );
            let _ = log_line(&line);
            println!("{}", line);
        }
        let mut cfg = read.config;
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
                // 审计 P3-1：成功 / 跳过 = 0，失败 = 1（退出码语义表见 desktop/README.md ✓）
                return once_exit_code(kind);
            }
        }
        // 5 秒醒一次看配置/到点没（很轻；不做网络请求 ✓）
        std::thread::sleep(Duration::from_secs(5));
    }
}

/// `run --once` 的退出码语义 ✓（**纯函数**；desktop/README.md 的「退出码」表就是它 ✓）。
///
/// 审计 P3-1：旧实现不管结果都 `return 0` ✗ —— 作为 systemd 单元 / 计划任务 / CI 脚本入口时，
/// 「这次登录失败了」根本传递不出去。口径与 `login` 一致：
/// **成功 = 0、跳过 = 0（不算失败）、失败 = 1** ✓；用法错误另有 2（见 [`run`]）。
fn once_exit_code(kind: drcom_core::metrics::Kind) -> i32 {
    match kind {
        drcom_core::metrics::Kind::Fail => 1,
        _ => 0,
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
    // 唯一实现：drcom_core::password（CLI 不再自己复制一份读法 ✗）
    let password = drcom_core::password::read();
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
    // 读配置走 legacy 兼容层：2.x 有方案时 `profile list` 也能看到 ✓（只读读取，不改 2.x ✓）
    let mut cfg = match drcom_core::legacy::load_config() {
        Ok(read) => read.config,
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
/// 取 `--key value` 形式的值（零依赖参数解析 ✓）。
///
/// 审计 P3-3：下一个参数如果是另一个 **flag**（`--` 开头）就不算值 ✓ ——
/// 旧实现会把 `login --password --dry-run` 里的 `--dry-run` 当成密码 ✗。
/// 缺值时返回 `None`，调用方按「没给」处理（与旧行为兼容 ✓）。
fn take_opt(args: &[String], key: &str) -> Option<String> {
    let idx = args.iter().position(|a| a == key)?;
    args.get(idx + 1)
        .filter(|value| !value.starts_with("--"))
        .cloned()
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
    fn archive_cmd_zips_a_tree_and_self_checks() {
        let root = std::env::temp_dir().join("drcom-archive-test");
        let _ = std::fs::remove_dir_all(&root);
        let staging = root.join("staging");
        std::fs::create_dir_all(staging.join("sub")).unwrap();
        std::fs::write(staging.join("README.txt"), b"hello").unwrap();
        std::fs::write(staging.join("sub/app"), b"binary-ish").unwrap();
        let zip = root.join("out/pkg.zip");

        assert_eq!(
            archive_cmd(&["--out".to_string()]),
            2,
            "少了目录 → 用法提示 ✓"
        );
        assert_eq!(
            archive_cmd(&[staging.display().to_string()]),
            2,
            "少了 --out → 用法提示 ✓"
        );

        let args = vec![
            "--out".to_string(),
            zip.display().to_string(),
            staging.display().to_string(),
        ];
        assert_eq!(archive_cmd(&args), 0, "打包成功 ✓");
        assert!(zip.is_file(), "产出文件必须存在 ✓");
        let raw = std::fs::read(&zip).unwrap();
        assert_eq!(&raw[..2], b"PK", "ZIP 魔数 ✓");
        let names = drcom_core::zipwriter::entry_names(&raw).expect("写出来必须是合法 ZIP ✓");
        assert!(names.contains(&"staging/README.txt".to_string()), "{:?}", names);
        assert!(
            names.contains(&"staging/sub/app".to_string()),
            "子目录要用 / 分隔 ✓: {:?}",
            names
        );

        // 审计 P3-16：显式 --prefix 固定包内顶层目录名（不随调用方式变化 ✓）
        let prefixed = root.join("out/pkg-prefixed.zip");
        let args = vec![
            "--out".to_string(),
            prefixed.display().to_string(),
            "--prefix".to_string(),
            "stardust-flash-link-9.9.9.9-linux-x86_64".to_string(),
            staging.display().to_string(),
        ];
        assert_eq!(archive_cmd(&args), 0, "带 --prefix 也要成功 ✓");
        let raw = std::fs::read(&prefixed).unwrap();
        let names = drcom_core::zipwriter::entry_names(&raw).expect("必须是合法 ZIP ✓");
        assert!(!names.is_empty(), "{:?}", names);
        for name in &names {
            assert!(
                name.starts_with("stardust-flash-link-9.9.9.9-linux-x86_64/"),
                "所有条目都要在显式 prefix 下 ✓: {:?}",
                names
            );
            assert!(!name.starts_with("staging/"), "不再出现输入目录名 ✗: {:?}", names);
        }
        assert!(names.contains(&"stardust-flash-link-9.9.9.9-linux-x86_64/README.txt".to_string()));
        // 非法前缀 → 用法错误（2），不写文件 ✓
        for bad in ["", "a/b", "a\\b", "..", " a", "a\nb"] {
            assert!(validate_zip_prefix(bad).is_err(), "{:?} 必须被拒绝 ✗", bad);
        }
        for good in ["staging", "stardust-flash-link-3.0.0.0-preview.5-linux-x86_64", "a.b_c-1"] {
            assert!(validate_zip_prefix(good).is_ok(), "{:?} 应当合法 ✓", good);
        }
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn package_files_writes_into_a_temp_dir() {
        let dir = std::env::temp_dir().join("drcom-package-files");
        let _ = std::fs::remove_dir_all(&dir);
        let args = vec!["--out".to_string(), dir.display().to_string()];
        assert_eq!(package_files_cmd(&args), 0, "写出成功 ✓");
        assert!(dir.join("README.txt").is_file(), "README 必须有 ✓");
        // 用 platform::Os（不是 cfg!）—— 与业务代码同一套平台判定 ✓
        match platform::Os::current() {
            platform::Os::Linux => {
                assert!(dir.join("deb-control").is_file(), "Linux 要带 deb 元数据 ✓");
                assert!(
                    dir.join(format!("{}.desktop", platform::APP_ID)).is_file(),
                    "Linux 要带桌面项 ✓"
                );
            }
            platform::Os::MacOs => {
                assert!(dir.join("Info.plist").is_file(), "macOS 要带 Info.plist ✓");
            }
            platform::Os::Windows => {}
        }
        // 目录不存在时要能自己建出来 ✓（CI 里就是这么用的 ✓）
        let nested = dir.join("a/b/c");
        assert_eq!(
            package_files_cmd(&["--out".to_string(), nested.display().to_string()]),
            0,
            "多级目录也该自动创建 ✓"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn checksum_cmd_hashes_real_files_and_fails_softly() {
        let dir = std::env::temp_dir().join("drcom-cli-checksum");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("hello.bin");
        std::fs::write(&path, b"abc").unwrap();
        assert_eq!(checksum_cmd(&[path.display().to_string()]), 0, "真实文件 ✓");
        assert_eq!(
            checksum_cmd(&[dir.join("missing.bin").display().to_string()]),
            1,
            "文件不存在 → 退出码 1（不 panic ✗）"
        );
        let _ = std::fs::remove_file(&path);
    }

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
    fn take_opt_finds_value_and_never_treats_a_flag_as_a_value() {
        let args: Vec<String> = ["login", "--host", "172.16.80.3", "--dry-run"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(take_opt(&args, "--host").as_deref(), Some("172.16.80.3"));
        assert_eq!(take_opt(&args, "--port"), None);
        assert_eq!(take_opt(&args, "--dry-run"), None, "布尔开关不吃下一个参数 ✓");
        // 审计 P3-3 的回归点：`--host --dry-run` 是「host 没给值」，
        // **不许**把 `--dry-run` 当成 host 的值 ✗
        let missing = ["login", "--host", "--dry-run"]
            .iter()
            .map(|s| s.to_string())
            .collect::<Vec<_>>();
        assert_eq!(take_opt(&missing, "--host"), None, "flag 不能当值 ✗");
        assert_eq!(take_opt(&missing, "--dry-run"), None);
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
    fn login_rejects_password_on_the_command_line() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        // 审计 P2-11：密码只从 password.txt 读（AGENTS.md I6 / §9.4）→ --password 必须被拒绝 ✗
        let err = login_args_error(&s(&["--password", "#Demo-Pwd-0000#"])).expect("--password 必须被拒绝 ✗");
        assert!(err.contains("password.txt"), "要告诉用户密码放哪 ✓: {}", err);
        assert!(!err.contains("Demo-Pwd"), "错误信息绝不许回显密码 ✗: {}", err);
        // 参数校验在读配置之前 → 直接拿到退出码 2（用法错误）✓
        assert_eq!(login(&s(&["--password", "#Demo-Pwd-0000#"])), 2);
        // 正常参数不受影响 ✓
        assert!(login_args_error(&s(&["--dry-run", "--host", "172.16.80.3"])).is_none());
        assert!(login_args_error(&s(&["--account", "2023001234", "--suffix", "@yd"])).is_none());
        // 与 take_opt 同口径：`--host --dry-run` = host 缺值 + 正常 dry-run（不许吞掉 --dry-run ✗）
        assert!(login_args_error(&s(&["--host", "--dry-run"])).is_none());
        assert!(take_opt(&s(&["--host", "--dry-run"]), "--host").is_none());
        assert!(login_args_error(&s(&["--dry-run", "--unknown"])).is_some(), "未知参数要报用法 ✗");
        assert!(login_args_error(&s(&["stray-value"])).is_some(), "多余位置参数要报用法 ✗");
        // 帮助里也不再宣传 --password ✓
        assert!(!help_text().contains("--password"), "帮助里不许再出现 --password ✗");
    }

    #[test]
    fn run_once_exit_codes_follow_the_documented_table() {
        use drcom_core::metrics::Kind;
        // 审计 P3-1：成功 / 跳过 = 0（跳过不算失败，与 2.x 同口径 ✓）；失败 = 1 ✓
        assert_eq!(once_exit_code(Kind::Ok), 0);
        assert_eq!(once_exit_code(Kind::Skip), 0, "跳过不是失败 ✓");
        assert_eq!(once_exit_code(Kind::Fail), 1, "失败必须能传出去 ✓");
        // --help 里也要写明（脚本作者看帮助就能查到 ✓）
        let help = help_text();
        assert!(help.contains("--once"), "{}", help);
        assert!(help.contains("成功/跳过 0"), "帮助要写清退出码语义 ✓: {}", help);
    }

    #[test]
    fn serve_port_comes_from_config_and_can_be_overridden() {
        // 审计 P3-6：默认取配置里的 ui_port（不再永远 8848 ✗）
        assert_eq!(
            resolve_serve_port(None, 8848).unwrap(),
            8848,
            "默认配置的 ui_port 就是 8848 ✓"
        );
        assert_eq!(resolve_serve_port(None, 9001).unwrap(), 9001, "2.x 改过端口就跟随 ✓");
        assert_eq!(resolve_serve_port(Some("9999"), 9001).unwrap(), 9999, "--port 优先 ✓");
        assert_eq!(resolve_serve_port(None, 0).unwrap(), server::DEFAULT_UI_PORT, "坏配置兜底 ✓");
        assert!(resolve_serve_port(Some("abc"), 8848).is_err(), "非数字要报错 ✓");
        assert!(resolve_serve_port(Some("0"), 8848).is_err(), "0 不是可用端口 ✓");
        assert!(resolve_serve_port(Some("70000"), 8848).is_err(), "超出 u16 ✓");
        // 配置默认值与 server 常量必须对齐（否则「默认 8848」这句话就不成立 ✗）
        assert_eq!(Config::default().ui_port, server::DEFAULT_UI_PORT);
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

    #[test]
    fn update_cmd_validates_input_without_touching_the_network() {
        assert_eq!(update_cmd(&[]), 2, "缺子命令要给用法 ✓");
        assert_eq!(update_cmd(&["nope".to_string()]), 2, "未知子命令要给用法 ✓");
        assert_eq!(
            update_cmd(&["download".to_string()]),
            2,
            "download 缺目标路径要给用法 ✓"
        );
        // 通道写错 → 在联网之前就返回 2（测试因此不依赖真实网络 ✓）
        assert_eq!(
            update_cmd(&["check".to_string(), "--channel".to_string(), "nope".to_string()]),
            2
        );
        assert_eq!(
            update_cmd(&[
                "download".to_string(),
                "x.zip".to_string(),
                "--channel".to_string(),
                "??".to_string()
            ]),
            2
        );
    }

    #[test]
    fn update_channel_resolution_prefers_override_then_config() {
        let mut cfg = Config::default();
        assert_eq!(resolve_update_channel(None, &cfg).unwrap(), Channel::Release);
        cfg.update_channel = drcom_core::config::UpdateChannel::Preview;
        assert_eq!(resolve_update_channel(None, &cfg).unwrap(), Channel::Preview);
        assert_eq!(resolve_update_channel(Some("snapshot"), &cfg).unwrap(), Channel::Snapshot);
        assert_eq!(resolve_update_channel(Some("RC"), &cfg).unwrap(), Channel::Rc);
        assert_eq!(resolve_update_channel(Some(" release "), &cfg).unwrap(), Channel::Release);
        let err = resolve_update_channel(Some("nope"), &cfg).unwrap_err();
        assert!(err.contains("通道写错了"), "{}", err);
        assert!(err.contains("preview"), "错误里要列出可用通道: {}", err);
    }

    // ── M4 第二块：update apply / rollback / backups（**全部离线，临时目录** ✓）──

    fn cli_temp_dir(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("drcom-cli-{}-{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// 一眼假的「可执行文件」：只保证平台魔数对（**绝不执行** ✓）。
    fn cli_fake_exe(tag: &[u8]) -> Vec<u8> {
        let mut bytes = match platform::Os::current() {
            platform::Os::Windows => b"MZ".to_vec(),
            platform::Os::Linux => vec![0x7f, b'E', b'L', b'F'],
            platform::Os::MacOs => vec![0xCF, 0xFA, 0xED, 0xFE],
        };
        bytes.extend_from_slice(b" demo ");
        bytes.extend_from_slice(tag);
        bytes
    }

    /// 造一个「本项目 archive 打的包」：staging/README.txt + staging/<exe> ✓。
    fn cli_package_zip(exe_name: &str, exe_bytes: &[u8], version: &str) -> Vec<u8> {
        use drcom_core::zipwriter;
        let readme = format!("星尘闪连 {} · 预览版（test-target）\n", version);
        let exe_path = format!("staging/{}", exe_name);
        zipwriter::build(
            &[
                zipwriter::ZipEntry { name: "staging/README.txt", data: readme.as_bytes() },
                zipwriter::ZipEntry { name: &exe_path, data: exe_bytes },
            ],
            std::time::UNIX_EPOCH,
        )
        .unwrap()
    }

    /// 造一个同时带 GUI 与 CLI 的安装包（每个文件都在 staging/ 下 ✓）。
    fn cli_multi_package_zip(files: &[(&str, &[u8])], version: &str) -> Vec<u8> {
        use drcom_core::zipwriter;
        let readme = format!("星尘闪连 {} · 预览版（test-target）\n", version);
        let names: Vec<String> = files.iter().map(|(name, _)| format!("staging/{}", name)).collect();
        let mut entries: Vec<zipwriter::ZipEntry> = vec![zipwriter::ZipEntry {
            name: "staging/README.txt",
            data: readme.as_bytes(),
        }];
        for (index, (_, data)) in files.iter().enumerate() {
            entries.push(zipwriter::ZipEntry { name: names[index].as_str(), data });
        }
        zipwriter::build(&entries, std::time::UNIX_EPOCH).unwrap()
    }

    fn args_of(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    #[test]
    fn update_apply_subcommand_usage_is_strict() {
        assert_eq!(update_cmd(&args_of(&["apply"])), 2, "apply 缺安装包要给用法 ✓");
        assert_eq!(update_cmd(&args_of(&["rollback", "stray"])), 2, "rollback 多给参数要报错 ✓");
        assert_eq!(update_cmd(&args_of(&["backups", "stray"])), 2, "backups 多给参数要报错 ✓");
        assert_eq!(update_cmd(&args_of(&["nope"])), 2, "未知子命令要给用法 ✓");
        assert_eq!(update_cmd(&args_of(&["--help"])), 0, "update --help 要能看 ✓");
    }

    #[test]
    fn update_apply_and_rollback_round_trip_in_a_temp_install_dir() {
        let dir = cli_temp_dir("apply-roundtrip");
        let exe_name = update_apply::expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, cli_fake_exe(b"OLD")).unwrap();
        let zip_path = dir.join("pkg.zip");
        std::fs::write(
            &zip_path,
            cli_package_zip(exe_name, &cli_fake_exe(b"NEW"), "3.0.0.0-preview.99"),
        )
        .unwrap();
        let dir_arg = dir.display().to_string();
        let zip_arg = zip_path.display().to_string();

        // ① dry-run：计划打印出来，但一个文件都不许动 ✓
        assert_eq!(
            update_apply_cmd(&args_of(&[
                &zip_arg,
                "--dry-run",
                "--install-dir",
                &dir_arg
            ])),
            0
        );
        assert_eq!(std::fs::read(&installed).unwrap(), cli_fake_exe(b"OLD"), "dry-run 不许动程序 ✗");
        assert!(!dir.join("backup").exists(), "dry-run 不许建备份 ✗");

        // ② 真装（--no-restart：测试里绝不真的拉进程 ✓）
        assert_eq!(
            update_apply_cmd(&args_of(&[
                &zip_arg,
                "--no-restart",
                "--install-dir",
                &dir_arg
            ])),
            0,
            "本地安装包应当装上 ✓"
        );
        assert_eq!(std::fs::read(&installed).unwrap(), cli_fake_exe(b"NEW"));
        let backup = dir
            .join(update_apply::BACKUP_DIR_NAME)
            .join(drcom_core::app_version_string())
            .join(exe_name);
        assert_eq!(std::fs::read(&backup).unwrap(), cli_fake_exe(b"OLD"), "备份内容要正确 ✓");

        // ③ 列备份（只读 ✓）
        assert_eq!(
            update_backups_cmd(&args_of(&["--install-dir", &dir_arg])),
            0
        );

        // ④ 回滚到最新备份 → 程序回到旧字节 ✓
        assert_eq!(
            update_rollback_cmd(&args_of(&["--no-restart", "--install-dir", &dir_arg])),
            0
        );
        assert_eq!(std::fs::read(&installed).unwrap(), cli_fake_exe(b"OLD"), "回滚要恢复旧程序 ✓");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_apply_rejects_bad_inputs_without_touching_the_program() {
        let dir = cli_temp_dir("apply-bad");
        let exe_name = update_apply::expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, cli_fake_exe(b"OLD")).unwrap();
        let dir_arg = dir.display().to_string();

        // ① 假 ZIP → 可读错误 + 退出码 1，程序不动 ✓
        let bad_zip = dir.join("bad.zip");
        std::fs::write(&bad_zip, b"this is definitely not a zip").unwrap();
        assert_eq!(
            update_apply_cmd(&args_of(&[
                &bad_zip.display().to_string(),
                "--install-dir",
                &dir_arg
            ])),
            1
        );
        assert_eq!(std::fs::read(&installed).unwrap(), cli_fake_exe(b"OLD"));
        assert!(!dir.join("backup").exists());

        // ② 安装包不存在 → 1 ✓
        let ghost = dir.join("ghost.zip");
        assert_eq!(
            update_apply_cmd(&args_of(&[
                &ghost.display().to_string(),
                "--install-dir",
                &dir_arg
            ])),
            1
        );

        // ③ 没有备份时回滚 → 1（不是 panic）✓
        assert_eq!(
            update_rollback_cmd(&args_of(&["--install-dir", &dir_arg])),
            1
        );
        // ④ 备份列表在空目录里也是 0（空列表不是错误 ✓）
        assert_eq!(update_backups_cmd(&args_of(&["--install-dir", &dir_arg])), 0);
        assert_eq!(std::fs::read(&installed).unwrap(), cli_fake_exe(b"OLD"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_apply_replaces_gui_and_cli_together_and_rolls_both_back() {
        let dir = cli_temp_dir("apply-multi");
        let gui_name = update_apply::expected_exe_name();
        let cli_name = update_apply::cli_exe_name();
        std::fs::write(dir.join(gui_name), cli_fake_exe(b"OLD-GUI")).unwrap();
        std::fs::write(dir.join(cli_name), cli_fake_exe(b"OLD-CLI")).unwrap();
        let zip_path = dir.join("pkg-multi.zip");
        std::fs::write(
            &zip_path,
            cli_multi_package_zip(
                &[
                    (gui_name, &cli_fake_exe(b"NEW-GUI")),
                    (cli_name, &cli_fake_exe(b"NEW-CLI")),
                ],
                "3.0.0.0-preview.99",
            ),
        )
        .unwrap();
        let dir_arg = dir.display().to_string();
        let zip_arg = zip_path.display().to_string();

        // ① dry-run 不许动盘 ✓
        assert_eq!(
            update_apply_cmd(&args_of(&[&zip_arg, "--dry-run", "--install-dir", &dir_arg])),
            0
        );
        assert_eq!(std::fs::read(dir.join(gui_name)).unwrap(), cli_fake_exe(b"OLD-GUI"));
        assert!(!dir.join("backup").exists());

        // ② 真装：两个程序**一起**换（不许一旧一新 ✗）✓
        assert_eq!(
            update_apply_cmd(&args_of(&[&zip_arg, "--no-restart", "--install-dir", &dir_arg])),
            0
        );
        assert_eq!(std::fs::read(dir.join(gui_name)).unwrap(), cli_fake_exe(b"NEW-GUI"));
        assert_eq!(
            std::fs::read(dir.join(cli_name)).unwrap(),
            cli_fake_exe(b"NEW-CLI"),
            "CLI 也要跟着换 ✓"
        );
        let backup = dir
            .join(update_apply::BACKUP_DIR_NAME)
            .join(drcom_core::app_version_string());
        assert_eq!(std::fs::read(backup.join(gui_name)).unwrap(), cli_fake_exe(b"OLD-GUI"));
        assert_eq!(std::fs::read(backup.join(cli_name)).unwrap(), cli_fake_exe(b"OLD-CLI"));

        // ③ 回滚：两个程序一起回来 ✓
        assert_eq!(
            update_rollback_cmd(&args_of(&["--no-restart", "--install-dir", &dir_arg])),
            0
        );
        assert_eq!(std::fs::read(dir.join(gui_name)).unwrap(), cli_fake_exe(b"OLD-GUI"));
        assert_eq!(std::fs::read(dir.join(cli_name)).unwrap(), cli_fake_exe(b"OLD-CLI"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn download_destination_uses_asset_name_for_existing_dirs() {
        let dir = std::env::temp_dir().join(format!("drcom-update-dest-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(
            download_destination(&dir.display().to_string(), "pkg.zip"),
            dir.join("pkg.zip"),
            "目录 → 用资产名 ✓"
        );
        let explicit = dir.join("explicit.zip");
        assert_eq!(
            download_destination(&explicit.display().to_string(), "pkg.zip"),
            explicit,
            "文件路径要原样用 ✓"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
