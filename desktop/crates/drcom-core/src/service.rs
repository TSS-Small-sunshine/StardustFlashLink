//! 后台常驻（服务化）：把「检查循环」交给系统管着。
//!
//! 与 [`crate::autostart`] 是**同一套落点、两种用法** ✓：
//!   - `autostart`：登录时拉起来（用户级、免管理员 ✓）；
//!   - `service`：注册成真正的常驻服务，可 start / stop / restart / 查状态 ✓。
//!
//! 三条线：
//!   - **Windows**：交给 NSSM 托管，服务名固定 [`WINDOWS_SERVICE_NAME`]（`DrcomAutoLogin`）。
//!     ⚠️ **这个字符串不能改** ✗ —— 2.x 的 `install.bat` / `uninstall.bat` / 升级脚本都认它 ✓；
//!     同时遵守 2.x 的 `AppExit` 口径（`Default Ignore` + `0 Ignore`，**不写 Restart** ✗ ——
//!     重试由程序自己的退避负责 ✓，避免 NSSM 重启风暴 ✗）。
//!   - **Linux**：`systemd --user` 单元（与 autostart 同一个文件 ✓）。
//!   - **macOS**：`launchctl` 的 LaunchAgent（同上 ✓）。
//!
//! 命令与状态解析全是**纯函数**（单测逐字覆盖 ✓），执行层只有薄薄一层 ✓。

use crate::platform::{self, Os};
use std::path::{Path, PathBuf};

/// Windows 服务名 —— 2.x 沿用，**不可更改** ✗（存量脚本依赖 ✓）。
pub const WINDOWS_SERVICE_NAME: &str = "DrcomAutoLogin";

/// 常驻状态。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ServiceState {
    Running,
    Stopped,
    /// 还没注册过 ✓
    NotInstalled,
    /// 查不出来（工具缺失 / 输出看不懂 ✓）
    Unknown(String),
}

impl ServiceState {
    pub fn is_running(&self) -> bool {
        matches!(self, ServiceState::Running)
    }

    /// 给界面 / 命令行的一行中文 ✓。
    pub fn label_cn(&self) -> String {
        match self {
            ServiceState::Running => "正在运行 ✓".to_string(),
            ServiceState::Stopped => "已安装但没在跑".to_string(),
            ServiceState::NotInstalled => "还没注册成常驻服务".to_string(),
            ServiceState::Unknown(reason) => format!("查不出来：{}", reason),
        }
    }
}

/// 对服务做什么 ✓。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Control {
    Start,
    Stop,
    Restart,
}

impl Control {
    pub fn as_str(self) -> &'static str {
        match self {
            Control::Start => "start",
            Control::Stop => "stop",
            Control::Restart => "restart",
        }
    }

    pub fn label_cn(self) -> &'static str {
        match self {
            Control::Start => "启动",
            Control::Stop => "停止",
            Control::Restart => "重启",
        }
    }
}

/// 这一平台用什么当「服务」✓。
pub fn flavor_name() -> &'static str {
    flavor_name_for(Os::current())
}

/// **纯函数**：平台 → 常驻方式的名称 ✓（三平台可参数化单测 —— R2：平台差异收在这里 ✓）。
pub fn flavor_name_for(os: Os) -> &'static str {
    match os {
        Os::Windows => "Windows 服务（NSSM 托管）",
        Os::MacOs => "launchd LaunchAgent（用户级）",
        Os::Linux => "systemd --user 单元",
    }
}

/// 这一平台需要的外部工具 ✓。
pub fn manager_program() -> &'static str {
    manager_program_for(Os::current())
}

/// **纯函数**：平台 → 外部工具名 ✓（供参数化单测与 CLI 展示用 ✓）。
pub fn manager_program_for(os: Os) -> &'static str {
    match os {
        Os::Windows => "nssm",
        Os::MacOs => "launchctl",
        Os::Linux => "systemctl",
    }
}

/// 工具在手边吗 ✓。
pub fn manager_available() -> bool {
    which(manager_program()).is_some()
}

/// 找可执行文件：先看**程序目录旁边** ✓，再把 PATH 逐个走一遍 ✓
/// （2.x 的安装包就是把 `nssm.exe` 跟主程序放一起的 ✓）。
///
/// 只查文件系统、**绝不试运行** ✗ —— 早先那版用 `program --help` 探活，
/// 于是「查个状态」也要启动一次外部程序 ✗：CI 上正好撞到一个不返回的子进程，
/// 把整个 `cargo test` 拖了 6 小时 ✗（来由写在 [`crate::proc`] 顶上 ✓）。
pub fn which(program: &str) -> Option<PathBuf> {
    let exe = std::env::current_exe().ok();
    which_within(program, exe.as_deref(), std::env::var_os("PATH").as_deref())
}

/// 纯函数版（单测用 ✓）：exe 所在目录优先 ✓，然后按 PATH 顺序找 ✓。
fn which_within(
    program: &str,
    exe: Option<&Path>,
    path_env: Option<&std::ffi::OsStr>,
) -> Option<PathBuf> {
    if let Some(dir) = exe.and_then(Path::parent) {
        if let Some(found) = find_in_dir(dir, program) {
            return Some(found);
        }
    }
    std::env::split_paths(path_env?).find_map(|dir| find_in_dir(&dir, program))
}

/// 一个目录里找可执行文件 ✓（Windows 同时认 `名字` 与 `名字.exe` ✓）。
fn find_in_dir(dir: &Path, program: &str) -> Option<PathBuf> {
    let mut candidates = vec![dir.join(program)];
    if cfg!(target_os = "windows") {
        candidates.push(dir.join(format!("{}.exe", program)));
    }
    candidates.into_iter().find(|candidate| candidate.is_file())
}

// ---------------- Windows：NSSM ----------------

fn set(key: &str, value: &str) -> Vec<String> {
    vec![
        "set".to_string(),
        WINDOWS_SERVICE_NAME.to_string(),
        key.to_string(),
        value.to_string(),
    ]
}

/// NSSM 的 `AppExit` 命令 —— **子参数与值是两个独立 argv** ✓。
///
/// nssm 的 AppExit 是「子参数 + 值」两级：`Default` / `0` 是**子参数名**，`Ignore` 是值。
/// 正确形状是 **5 段 argv**（不含 nssm.exe 本身）：
///
/// ```text
/// nssm set DrcomAutoLogin AppExit Default Ignore
/// nssm set DrcomAutoLogin AppExit 0 Ignore
/// ```
///
/// 反例（3.0 早期写错的形状）：把 `"AppExit Default"` 当成**一个** argv 元素传进去 →
/// nssm 报 `Parameter "AppExit" requires a subparameter!`，安装链在第 6 条命令中断，
/// 系统里留下一个「已注册、没配 AppExit（= NSSM 默认 Restart）」的半成品服务 ✗（违反 R8）。
/// 2.x v2.0.4.2 专门修过同一处（`auto_update.py` 的 `_set_nssm_appexit`：注释「拆成两个 argv」），
/// `install.bat` / `packaging/setup.iss` 也是同一形状 ✓。
///
/// R8：**只写 Ignore，绝不写 Restart** ✗（重试归程序自己的退避管 ✓）。
pub fn nssm_appexit_commands() -> Vec<Vec<String>> {
    // (子参数, 值) —— 与 2.x setup.iss / install.bat 逐字一致 ✓
    [("Default", "Ignore"), ("0", "Ignore")]
        .iter()
        .map(|(subparam, value)| {
            vec![
                "set".to_string(),
                WINDOWS_SERVICE_NAME.to_string(),
                "AppExit".to_string(),
                (*subparam).to_string(),
                (*value).to_string(),
            ]
        })
        .collect()
}

fn log_path(name: &str) -> String {
    platform::log_dir().join(name).display().to_string()
}

/// NSSM 安装一条龙（**数组顺序 = 执行顺序** ✓）。
pub fn nssm_install_commands(exe: &Path, args: &[&str]) -> Vec<Vec<String>> {
    let mut launch = vec![
        "install".to_string(),
        WINDOWS_SERVICE_NAME.to_string(),
        exe.display().to_string(),
    ];
    launch.extend(args.iter().map(|arg| arg.to_string()));
    let mut commands = vec![
        launch,
        set(
            "AppDirectory",
            &exe.parent().map(|p| p.display().to_string()).unwrap_or_default(),
        ),
        set("Start", "SERVICE_AUTO_START"),
        set("DisplayName", "星尘闪连（校园网自动登录）"),
        set("Description", "自动检查校园网在线状态，掉线自动重登"),
    ];
    // 2.x 不变量 I7 / 3.0 的 R8：崩溃**不自动重启** ✗ —— 重试由程序自己的退避负责 ✓
    // （逐元素正确的 5 段 argv 在 nssm_appexit_commands 里，别在这里拼字符串 ✗）
    commands.extend(nssm_appexit_commands());
    commands.push(set("AppStdout", &log_path("service_stdout.log")));
    commands.push(set("AppStderr", &log_path("service_stderr.log")));
    commands.push(set("AppRotateFiles", "1"));
    commands
}

/// NSSM 卸载 ✓（`confirm` 是 nssm 的免交互开关 ✓）。
pub fn nssm_remove_command() -> Vec<String> {
    vec![
        "remove".to_string(),
        WINDOWS_SERVICE_NAME.to_string(),
        "confirm".to_string(),
    ]
}

/// NSSM 启停 / 重启 ✓。
pub fn nssm_control_command(action: Control) -> Vec<String> {
    vec![action.as_str().to_string(), WINDOWS_SERVICE_NAME.to_string()]
}

/// 查状态用 `sc.exe`：**普通用户也能查** ✓（不必管理员 ✓）。
pub fn sc_query_command() -> Vec<String> {
    vec!["query".to_string(), WINDOWS_SERVICE_NAME.to_string()]
}

/// 解析 `sc query` 的输出 ✓（状态名是 ASCII，中文系统上也是 ✓）。
pub fn parse_sc_state(stdout: &str, exit_code: i32) -> ServiceState {
    // 1060 = ERROR_SERVICE_DOES_NOT_EXIST ✓（比认中英文报错文案可靠 ✓）
    if exit_code == 1060 {
        return ServiceState::NotInstalled;
    }
    let upper = stdout.to_ascii_uppercase();
    if upper.contains("RUNNING") {
        return ServiceState::Running;
    }
    if upper.contains("STOPPED") || upper.contains("STOP_PENDING") {
        return ServiceState::Stopped;
    }
    if exit_code != 0 {
        return ServiceState::Unknown(format!("sc.exe 退出码 {}", exit_code));
    }
    ServiceState::Unknown("sc.exe 输出里没有 STATE 行".to_string())
}

/// 同名服务**已经存在**时的两条路 ✓。
///
/// 这不是异常情况 ✗ —— 服务名是**故意与 2.x 共用**的 ✓（存量 `install.bat` / 升级脚本都认它 ✓），
/// 所以「已经有一个在跑」是**常态** ✓：查到的很可能是 2.x 装的那个 ✓。
pub fn existing_service_hint(exe: &Path) -> String {
    format!(
        "服务「{name}」已经存在 ✓ —— 这个名字是我们**故意与 2.x 共用**的 ✓。\n\
         查到「正在运行」时，跑的大概率是 2.x 装的那个 ✓。两条路：\n\
         \x20 ① **接管它**（把指向换到 3.0 的程序 ✓）：\n     \
         nssm set {name} Application \"{exe}\"\n     \
         nssm set {name} AppDirectory \"{dir}\"\n     \
         nssm set {name} AppParameters run\n     \
         nssm restart {name}\n\
         \x20 ② 或先 `service uninstall`（会先停掉现在跑着的那个 ✓）再 `service install` ✓",
        name = WINDOWS_SERVICE_NAME,
        exe = exe.display(),
        dir = exe.parent().map(|p| p.display().to_string()).unwrap_or_default()
    )
}

/// 查状态时的一句提醒 ✓（Windows 上服务名与 2.x 共用 ✓）。
pub fn shared_name_note() -> String {
    format!(
        "注意：服务名与 2.x 共用（{}）✓ —— 查到「正在运行」也可能是 2.x 那个 ✓",
        WINDOWS_SERVICE_NAME
    )
}

/// NSSM 缺失时的中文说明 ✓（3.0 不自己实现 SCM 调度器 ✗ —— 那要写一堆 unsafe ✓）。
pub fn nssm_missing_hint() -> String {
    nssm_missing_hint_for(Os::Windows, &exe_dir_label())
}

/// **纯函数**版（程序目录标签由调用方给 → 单测不依赖本机安装路径 ✓）。
pub fn nssm_missing_hint_for(os: Os, beside_dir: &str) -> String {
    format!(
        "没找到 nssm.exe ✗ —— {} 需要它托管（2.x 安装包里自带 ✓）。\n\
         两种办法：\n  \
         ① 把 nssm.exe 放到程序目录旁（{}）；\n  \
         ② 先用 `autostart on` 顶上（登录时自动跑 ✓，免管理员 ✓，只是没有服务级开机即起 ✓）",
        flavor_name_for(os),
        beside_dir
    )
}

/// 程序目录（提示里「把 nssm.exe 放到哪」用 ✓）。
fn exe_dir_label() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.display().to_string()))
        .unwrap_or_else(|| "程序目录".to_string())
}

/// `service status` 要不要显示「与 2.x 同名」的提醒 ✓（**纯函数** → 三平台可参数化单测 ✓）。
pub fn status_shows_shared_name_note(os: Os) -> bool {
    matches!(os, Os::Windows)
}

/// 找不到管理工具时给哪条提示 ✓（**平台差异收在这里**，CLI 里不再写 `cfg!` —— R2 ✓）。
pub fn missing_manager_hint(os: Os) -> String {
    match os {
        Os::Windows => nssm_missing_hint_for(Os::Windows, &exe_dir_label()),
        _ => format!("装好 {} 之后再用 `service install` ✓", manager_program_for(os)),
    }
}

// ---------------- Linux / macOS ----------------

/// systemd --user 的单元名 ✓（与 autostart 写的是同一个文件 ✓）。
pub fn systemd_unit_name() -> String {
    format!("{}.service", platform::APP_ID)
}

/// systemd 启停 / 重启 ✓（全带 `--user` ⇒ 免 root ✓）。
pub fn systemd_commands(action: Control) -> Vec<Vec<String>> {
    let unit = systemd_unit_name();
    match action {
        Control::Start => vec![vec!["--user".to_string(), "start".to_string(), unit]],
        Control::Stop => vec![vec!["--user".to_string(), "stop".to_string(), unit]],
        Control::Restart => vec![vec!["--user".to_string(), "restart".to_string(), unit]],
    }
}

/// 解析 `systemctl --user is-active <unit>` 的输出 ✓。
pub fn parse_systemd_state(stdout: &str) -> ServiceState {
    match stdout.trim() {
        "active" | "activating" | "reloading" => ServiceState::Running,
        "inactive" | "failed" | "deactivating" => ServiceState::Stopped,
        "" => ServiceState::Unknown("systemctl 没有输出".to_string()),
        other => ServiceState::Unknown(format!("systemctl 说：{}", other)),
    }
}

/// launchctl 命令 ✓（用 plist 路径，`-w` = 持久化 ✓）。
pub fn launchd_commands(action: Control, plist: &str) -> Vec<Vec<String>> {
    match action {
        Control::Start => vec![vec!["load".to_string(), "-w".to_string(), plist.to_string()]],
        Control::Stop => vec![vec!["unload".to_string(), "-w".to_string(), plist.to_string()]],
        Control::Restart => vec![
            vec!["unload".to_string(), "-w".to_string(), plist.to_string()],
            vec!["load".to_string(), "-w".to_string(), plist.to_string()],
        ],
    }
}

/// 解析 `launchctl list` 里我们自己那一行 ✓
/// （格式 `PID  Status  Label`；PID 是 `-` 表示已加载但没在跑 ✓）。
pub fn parse_launchctl_list(stdout: &str) -> ServiceState {
    let label = crate::autostart::MAC_LABEL;
    for line in stdout.lines() {
        let mut parts = line.split_whitespace();
        let (Some(pid), Some(_status), Some(name)) = (parts.next(), parts.next(), parts.next())
        else {
            continue;
        };
        if name != label {
            continue;
        }
        return if pid == "-" {
            ServiceState::Stopped
        } else {
            ServiceState::Running
        };
    }
    ServiceState::NotInstalled
}

// ---------------- 执行层（很薄 ✓） ----------------

/// 跑一条命令并拿 stdout；失败时把 stderr 带回来 ✓（**带硬超时** ✓，见 [`crate::proc`]）。
fn run(program: &Path, args: &[String]) -> Result<String, String> {
    let got = crate::proc::run(program, args)?;
    if got.ok() {
        return Ok(got.stdout);
    }
    Err(format!(
        "{} {} 没成功：{}",
        program.display(),
        args.first().cloned().unwrap_or_default(),
        got.detail()
    ))
}

/// 跑一条命令，**成败都拿回文本与退出码** ✓（查状态要用退出码 ✓）。
fn run_quiet(program: &Path, args: &[String]) -> Result<(String, i32), String> {
    let got = crate::proc::run(program, args)?;
    let mut text = got.stdout;
    text.push_str(&got.stderr);
    Ok((text, got.code))
}

/// 将要做什么（`--dry-run`：**只打印，不动系统** ✓）。
pub fn plan(exe: &Path, args: &[&str]) -> Vec<String> {
    let program = manager_program();
    match Os::current() {
        Os::Windows => {
            let mut lines: Vec<String> = nssm_install_commands(exe, args)
                .iter()
                .map(|argv| format!("{} {}", program, argv.join(" ")))
                .collect();
            lines.push(format!("{} start {}", program, WINDOWS_SERVICE_NAME));
            lines
        }
        // Linux / macOS：文件与启停都走 autostart 那一套（**同一份文件** ✓，不会两处漂移 ✗）
        _ => {
            let mut lines = crate::autostart::plan(exe, args);
            let tail = match Os::current() {
                Os::Linux => systemd_commands(Control::Start),
                _ => launchd_commands(
                    Control::Start,
                    &crate::autostart::file_path()
                        .map(|p| p.display().to_string())
                        .unwrap_or_default(),
                ),
            };
            for argv in tail {
                lines.push(format!("{} {}", program, argv.join(" ")));
            }
            lines
        }
    }
}

/// 现在是什么状态 ✓（只读 ✓）。
pub fn status() -> ServiceState {
    match Os::current() {
        Os::Windows => {
            let args = sc_query_command();
            match run_quiet(Path::new("sc"), &args) {
                Ok((text, code)) => parse_sc_state(&text, code),
                Err(e) => ServiceState::Unknown(e),
            }
        }
        _ => {
            // 没写过单元文件 → 一定没注册 ✓（省一次命令调用 ✓）
            if crate::autostart::file_path().map(|p| p.is_file()) != Some(true) {
                return ServiceState::NotInstalled;
            }
            match Os::current() {
                Os::Linux => {
                    let mut args = vec!["--user".to_string(), "is-active".to_string()];
                    args.push(systemd_unit_name());
                    match run_quiet(&PathBuf::from("systemctl"), &args) {
                        Ok((text, _)) => parse_systemd_state(&text),
                        Err(e) => ServiceState::Unknown(e),
                    }
                }
                _ => match run_quiet(&PathBuf::from("launchctl"), &["list".to_string()]) {
                    Ok((text, _)) => parse_launchctl_list(&text),
                    Err(e) => ServiceState::Unknown(e),
                },
            }
        }
    }
}

/// 注册并启动 ✓（返回给用户看的一句话 ✓）。
pub fn install(exe: &Path, args: &[&str]) -> Result<String, String> {
    match Os::current() {
        Os::Windows => {
            let nssm = which("nssm").ok_or_else(nssm_missing_hint)?;
            // 同名服务已经存在（2.x 装的就是这个名字 ✓）→ 别硬装 ✗，给两条明确的路 ✓
            if !matches!(status(), ServiceState::NotInstalled) {
                return Err(existing_service_hint(exe));
            }
            for argv in nssm_install_commands(exe, args) {
                run(&nssm, &argv)?;
            }
            // 启动失败不算致命（可能没提权 ✓）—— 如实报出来，别假装成功 ✗
            let started = run(&nssm, &nssm_control_command(Control::Start)).is_ok();
            Ok(format!(
                "已注册 {}「{}」{}（这一步需要管理员权限 ✗）",
                flavor_name(),
                WINDOWS_SERVICE_NAME,
                if started {
                    "并已启动 ✓"
                } else {
                    "；启动没成功，请用管理员命令行再试 ✓"
                }
            ))
        }
        _ => {
            let message = crate::autostart::enable(exe, args)?;
            Ok(format!("已注册 {}：{}", flavor_name(), message))
        }
    }
}

/// 「注销服务」要跑的命令 ✓（**纯函数**，审计 P2-4）。
///
/// **幂等**：`NotInstalled` → **空列表**，一个删除命令都不跑 ✓ ——
/// `nssm remove DrcomAutoLogin confirm` 在服务不存在时会非零退出，靠「命令失败来兜底」
/// 会把「本来就没装」误报成失败 ✗（正是文档承诺「uninstall 幂等 ✓」被打破的原因）。
///
/// 顺序 = 执行顺序：先尽力停（停不了不算注销失败 ✓），再删 ✓。
pub fn uninstall_plan(state: &ServiceState) -> Vec<Vec<String>> {
    if matches!(state, ServiceState::NotInstalled) {
        // 没装 → 不跑任何命令（也不要求 nssm 在手边 ✓）
        return Vec::new();
    }
    vec![nssm_control_command(Control::Stop), nssm_remove_command()]
}

/// 停止并注销 ✓（**幂等** ✓：本来就没装 = 成功、什么都不做 ✓）。
pub fn uninstall() -> Result<String, String> {
    match Os::current() {
        Os::Windows => {
            // 先查状态（只读 ✓，sc query 不要管理员 ✓）→ 没装就直接成功返回 ✓
            let plan = uninstall_plan(&status());
            if plan.is_empty() {
                return Ok(format!(
                    "本来就没装服务「{}」✓（什么都没做）",
                    WINDOWS_SERVICE_NAME
                ));
            }
            let nssm = which("nssm").ok_or_else(nssm_missing_hint)?;
            // 停：尽力而为 —— 已经停了 / 停不动都不影响「注销」这个目的 ✓
            let _ = run(&nssm, &nssm_control_command(Control::Stop));
            run(&nssm, &nssm_remove_command())?;
            Ok(format!("已注销服务「{}」✓", WINDOWS_SERVICE_NAME))
        }
        _ => crate::autostart::disable(),
    }
}

/// 启停 / 重启 ✓。
pub fn control(action: Control) -> Result<String, String> {
    match Os::current() {
        Os::Windows => {
            let nssm = which("nssm").ok_or_else(nssm_missing_hint)?;
            run(&nssm, &nssm_control_command(action))?;
            Ok(format!(
                "已{}服务「{}」✓",
                action.label_cn(),
                WINDOWS_SERVICE_NAME
            ))
        }
        Os::Linux => {
            let systemctl =
                which("systemctl").ok_or("没找到 systemctl ✗（这台机器可能没用 systemd）")?;
            for argv in systemd_commands(action) {
                run(&systemctl, &argv)?;
            }
            Ok(format!("已{} {} ✓", action.label_cn(), systemd_unit_name()))
        }
        _ => {
            let launchctl = which("launchctl").ok_or("没找到 launchctl ✗")?;
            let plist = crate::autostart::file_path()
                .map(|p| p.display().to_string())
                .unwrap_or_default();
            for argv in launchd_commands(action, &plist) {
                run(&launchctl, &argv)?;
            }
            Ok(format!("已{} LaunchAgent ✓", action.label_cn()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// 故意用带空格的路径 ✓（自启/服务配置最常见的翻车点 ✗）。
    fn exe() -> PathBuf {
        PathBuf::from(r"C:\Program Files\Stardust Flash Link\stardust-flash-link.exe")
    }

    #[test]
    fn nssm_appexit_is_two_five_segment_argvs_and_never_restart() {
        // 2.x 的正确形状（install.bat:178-179 / packaging/setup.iss:287-288 /
        // auto_update.py:884-897「拆成两个 argv」）：**逐元素**断言，不再 join 成字符串 ✗
        // （旧测试断言 join 后的子串，"set … AppExit Default" + "set … AppExit Default Ignore"
        //   两行拼起来恰好凑出 "AppExit Default Ignore"，所以 argv 边界错了也绿 —— 假绿 ✗）
        let commands = nssm_appexit_commands();
        let expected: Vec<Vec<String>> = vec![
            vec!["set", "DrcomAutoLogin", "AppExit", "Default", "Ignore"]
                .into_iter()
                .map(str::to_string)
                .collect(),
            vec!["set", "DrcomAutoLogin", "AppExit", "0", "Ignore"]
                .into_iter()
                .map(str::to_string)
                .collect(),
        ];
        assert_eq!(commands, expected, "AppExit 命令必须逐元素是这个形状 ✓");

        for argv in &commands {
            // ① 5 段：(nssm.exe) set <服务> AppExit <子参数> <值> ✓
            assert_eq!(argv.len(), 5, "必须是 5 段 argv（nssm.exe 之外）: {:?}", argv);
            assert_eq!(argv[0], "set");
            assert_eq!(argv[1], WINDOWS_SERVICE_NAME);
            assert_eq!(argv[2], "AppExit");
            assert!(matches!(argv[3].as_str(), "Default" | "0"), "子参数: {:?}", argv);
            assert_eq!(argv[4], "Ignore", "R8 只允许 Ignore: {:?}", argv);
            // ② 子参数绝不许与参数名拼成**同一个** argv 元素 ✗
            //    （那正是真机报 "Parameter \"AppExit\" requires a subparameter!" 的形状 ✓）
            for element in argv {
                assert!(
                    !element.starts_with("AppExit "),
                    "子参数不许并进参数名那个 argv ✗: {:?}",
                    element
                );
            }
            // ③ R8：绝不写 Restart ✗（NSSM 重启风暴）
            for element in argv {
                assert!(
                    !element.to_ascii_lowercase().contains("restart"),
                    "AppExit 绝不许写 Restart ✗: {:?}",
                    argv
                );
            }
        }
    }

    #[test]
    fn nssm_install_keeps_the_two_x_invariants() {
        let commands = nssm_install_commands(&exe(), &["run"]);
        let joined: Vec<String> = commands.iter().map(|argv| argv.join(" ")).collect();
        let all = joined.join("\n");

        // ① 服务名必须还是 2.x 那个（install.bat / uninstall.bat / 升级脚本都认它 ✓）
        assert_eq!(WINDOWS_SERVICE_NAME, "DrcomAutoLogin", "这个字符串不能改 ✗");
        assert!(joined[0].starts_with(&format!("install {} ", WINDOWS_SERVICE_NAME)), "{}", joined[0]);
        assert!(joined[0].contains("stardust-flash-link.exe run"), "{}", joined[0]);

        // ② AppExit：安装链里必须出现**逐元素正确**的两条（不再断言 join 后的子串 ✗）
        let appexit: Vec<Vec<String>> = commands
            .iter()
            .filter(|argv| argv.len() >= 3 && argv[2] == "AppExit")
            .cloned()
            .collect();
        assert_eq!(
            appexit,
            nssm_appexit_commands(),
            "安装链里的 AppExit 命令必须与纯函数逐元素一致: {:?}",
            appexit
        );
        // 任何一条 argv 都不许把子参数并进参数名元素（假绿那个 bug 的形状 ✗）
        for argv in &commands {
            for element in argv {
                assert!(
                    !element.starts_with("AppExit "),
                    "子参数不许并进参数名那个 argv ✗: {:?}",
                    element
                );
                assert!(
                    !element.to_ascii_lowercase().contains("restart"),
                    "NSSM 重启风暴是明令禁止的 ✗: {:?}",
                    all
                );
            }
        }

        // ③ 自动启动 / 工作目录 / 日志 ✓
        assert!(all.contains("Start SERVICE_AUTO_START"), "{}", all);
        assert!(all.contains("AppDirectory"), "{}", all);
        assert!(all.contains("AppStderr"), "{}", all);
    }

    #[test]
    fn existing_service_hint_offers_a_quoted_takeover_path() {
        let text = existing_service_hint(&exe());
        assert!(text.contains("2.x"), "要说清为什么会有同名服务 ✓\n{}", text);
        // 审计 P2-6 的回归点：**路径必须带双引号** ✗ —— 默认安装路径含空格，
        // 不带引号时用户复制粘贴到 CMD 会被按空格拆成多个 argv，写坏 2.x 的服务 ✗
        assert!(
            text.contains(&format!("Application \"{}\"", exe().display())),
            "Application 的路径必须带双引号 ✗\n{}",
            text
        );
        assert!(
            text.contains(&format!(
                "AppDirectory \"{}\"",
                exe().parent().unwrap().display()
            )),
            "AppDirectory 的路径必须带双引号 ✗\n{}",
            text
        );
        assert!(text.contains("AppParameters run"), "{}", text);
        assert!(text.contains("service uninstall"), "{}", text);
        assert!(shared_name_note().contains(WINDOWS_SERVICE_NAME));
        // 带空格的路径**不许**以裸形式出现（否则就是又忘了加引号 ✗）
        assert!(
            !text.contains(&format!("Application {}", exe().display())),
            "裸路径会把含空格的路径拆坏 ✗\n{}",
            text
        );
    }

    #[test]
    fn uninstall_is_idempotent_when_the_service_is_not_installed() {
        // 审计 P2-4 的回归点：没装时**一条命令都不跑**（不是跑 remove 再指望它成功 ✗）
        assert!(
            uninstall_plan(&ServiceState::NotInstalled).is_empty(),
            "没装 → 空命令列表（uninstall 直接 Ok ✓）"
        );
        // 装了（不管在跑还是停了）→ 先停再删，逐元素断言 ✓
        for state in [ServiceState::Running, ServiceState::Stopped] {
            assert_eq!(
                uninstall_plan(&state),
                vec![nssm_control_command(Control::Stop), nssm_remove_command()],
                "{:?} 的注销计划不对 ✗",
                state
            );
        }
        // 查不出来时保持旧行为：尽力删一次（不能因为一次 sc 探测失败就假装「没装」✓）
        assert!(!uninstall_plan(&ServiceState::Unknown("sc 调用失败".into())).is_empty());
    }

    #[test]
    fn platform_names_are_pure_and_parameterized() {
        // R2 / 审计 P2-10：平台分支用 Os 参数化 → 三个平台在任何一个 runner 上都能断言 ✓
        assert_eq!(manager_program_for(Os::Windows), "nssm");
        assert_eq!(manager_program_for(Os::MacOs), "launchctl");
        assert_eq!(manager_program_for(Os::Linux), "systemctl");
        assert!(flavor_name_for(Os::Windows).contains("NSSM"));
        assert!(flavor_name_for(Os::MacOs).contains("launchd"));
        assert!(flavor_name_for(Os::Linux).contains("systemd"));
        assert_eq!(manager_program(), manager_program_for(Os::current()));
        assert_eq!(flavor_name(), flavor_name_for(Os::current()));

        // service status 的提示：只有 Windows 才有「与 2.x 同名」那条 ✓
        assert!(status_shows_shared_name_note(Os::Windows));
        assert!(!status_shows_shared_name_note(Os::Linux));
        assert!(!status_shows_shared_name_note(Os::MacOs));

        // 找不到管理工具的提示：Windows 指向 nssm.exe，Linux/macOS 指向自己的工具 ✓
        assert!(missing_manager_hint(Os::Windows).contains("nssm.exe"));
        assert!(missing_manager_hint(Os::Linux).contains("systemctl"));
        assert!(missing_manager_hint(Os::MacOs).contains("launchctl"));
    }

    #[test]
    fn sc_state_parsing_covers_real_outputs() {
        let running = "SERVICE_NAME: DrcomAutoLogin\n        TYPE               : 10  WIN32_OWN_PROCESS\n        STATE              : 4  RUNNING\n";
        assert_eq!(parse_sc_state(running, 0), ServiceState::Running);
        assert_eq!(
            parse_sc_state("        STATE              : 1  STOPPED\n", 0),
            ServiceState::Stopped
        );
        // 1060 = ERROR_SERVICE_DOES_NOT_EXIST ✓（比认中英文文案可靠 ✓）
        assert_eq!(parse_sc_state("", 1060), ServiceState::NotInstalled);
        assert!(matches!(parse_sc_state("?", 5), ServiceState::Unknown(_)));
        assert!(matches!(parse_sc_state("", 0), ServiceState::Unknown(_)));
        assert_eq!(parse_sc_state("state : 4 running", 0), ServiceState::Running, "大小写不敏感 ✓");
    }

    #[test]
    fn systemd_state_commands_are_exact_and_rootless() {
        assert_eq!(parse_systemd_state("active\n"), ServiceState::Running);
        assert_eq!(parse_systemd_state("inactive"), ServiceState::Stopped);
        assert_eq!(parse_systemd_state("failed"), ServiceState::Stopped);
        assert!(matches!(parse_systemd_state("怪东西"), ServiceState::Unknown(_)));
        for action in [Control::Start, Control::Stop, Control::Restart] {
            let cmd = systemd_commands(action);
            assert_eq!(cmd.len(), 1);
            assert_eq!(cmd[0][0], "--user", "必须 --user（免 root ✓）");
            assert_eq!(cmd[0][1], action.as_str());
            assert!(cmd[0][2].ends_with(".service"), "{:?}", cmd[0]);
        }
        assert_eq!(systemd_unit_name(), format!("{}.service", platform::APP_ID));
    }

    #[test]
    fn launchctl_list_parsing_handles_three_states() {
        let running = "PID\tStatus\tLabel\n-\t0\tcom.apple.Finder\n4321\t0\tcom.stardust.flashlink\n";
        assert_eq!(parse_launchctl_list(running), ServiceState::Running);
        assert_eq!(
            parse_launchctl_list("PID\tStatus\tLabel\n-\t0\tcom.stardust.flashlink\n"),
            ServiceState::Stopped,
            "已加载但没在跑（PID 是 - ✓）"
        );
        assert_eq!(
            parse_launchctl_list("PID\tStatus\tLabel\n"),
            ServiceState::NotInstalled
        );
        assert_eq!(
            parse_launchctl_list("PID\tStatus\tLabel\n-\t0\tcom.other.thing\n"),
            ServiceState::NotInstalled,
            "只看我们自己那行 ✓"
        );
        assert_eq!(launchd_commands(Control::Restart, "/tmp/x.plist").len(), 2, "重启 = unload + load ✓");
    }

    #[test]
    fn plan_stays_platform_shaped_and_never_asks_for_privileges() {
        let plan = plan(&exe(), &["run"]);
        assert!(!plan.is_empty());
        let text = plan.join("\n");
        if cfg!(target_os = "windows") {
            assert!(text.contains("nssm install DrcomAutoLogin"), "{}", text);
            assert!(text.contains("nssm start DrcomAutoLogin"), "{}", text);
            // 用户 --dry-run 看到的就是这两条（逐字核对 2.x install.bat:178-179 的形状 ✓）
            assert!(
                text.contains("nssm set DrcomAutoLogin AppExit Default Ignore"),
                "dry-run 的 AppExit 形状不对（子参数必须单独成段）✗:\n{}",
                text
            );
            assert!(
                text.contains("nssm set DrcomAutoLogin AppExit 0 Ignore"),
                "dry-run 的 AppExit 形状不对 ✗:\n{}",
                text
            );
            assert_eq!(
                text.lines().filter(|line| line.contains("AppExit")).count(),
                2,
                "AppExit 只该有两条（多一条就意味着形状又拼错了）✗:\n{}",
                text
            );
            assert!(!text.contains("Restart"), "R8 绝不许出现 Restart ✗:\n{}", text);
        } else {
            assert!(text.contains("launchctl") || text.contains("systemctl"), "{}", text);
        }
        assert!(!text.contains("sudo"), "一次 sudo 都不该有 ✗:\n{}", text);
        assert!(!text.contains("HKLM"), "绝不碰机器级服务 ✗:\n{}", text);
    }

    #[test]
    fn status_is_readonly_and_says_something() {
        let state = status();
        assert!(!state.label_cn().is_empty());
        assert_eq!(state.is_running(), matches!(state, ServiceState::Running));
    }

    #[test]
    fn which_finds_the_tool_beside_the_exe_then_on_path() {
        // 造两个同名「工具」：程序目录旁边那个必须赢 ✓（2.x 安装包就是这么放的 ✓）
        let root = std::env::temp_dir().join("drcom-which-test");
        let _ = std::fs::remove_dir_all(&root);
        let beside = root.join("app");
        let on_path = root.join("bin");
        std::fs::create_dir_all(&beside).unwrap();
        std::fs::create_dir_all(&on_path).unwrap();
        let tool = if cfg!(target_os = "windows") {
            "nssm.exe"
        } else {
            "nssm"
        };
        std::fs::write(beside.join(tool), b"").unwrap();
        std::fs::write(on_path.join(tool), b"").unwrap();
        let exe = beside.join(if cfg!(target_os = "windows") {
            "app.exe"
        } else {
            "app"
        });

        assert_eq!(
            which_within("nssm", Some(exe.as_path()), None),
            Some(beside.join(tool)),
            "程序目录旁边优先 ✓"
        );
        // 旁边没有 → 按 PATH 顺序找 ✓
        assert_eq!(
            which_within("nssm", None, Some(on_path.as_os_str())),
            Some(on_path.join(tool)),
            "PATH 要能兜住 ✓"
        );
        // 哪都没有 → None ✓（而且**一个程序都没启动过** ✗ —— 这才是重点 ✓）
        assert_eq!(
            which_within("drcom-nope-xyz", Some(exe.as_path()), Some(on_path.as_os_str())),
            None
        );
        assert_eq!(which_within("nssm", Some(exe.as_path()), None), Some(beside.join(tool)));

        let _ = std::fs::remove_dir_all(&root);
    }
}





