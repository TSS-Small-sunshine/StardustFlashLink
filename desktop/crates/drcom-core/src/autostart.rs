//! 开机自启：登录后自动把程序拉起来（**零第三方依赖** ✓）。
//!
//! 三个平台都用系统自带机制：
//!   - **Windows**：当前用户注册表 `HKCU\...\CurrentVersion\Run`
//!     —— 用户级 ⇒ **不需要管理员** ✓（`HKLM` 才要 ✗，我们绝不去碰 ✗）；
//!   - **Linux**：`~/.config/systemd/user/<app>.service`（`systemd --user` ⇒ 无需 root ✓）；
//!   - **macOS**：`~/Library/LaunchAgents/com.stardust.flashlink.plist`（LaunchAgent ⇒ 用户级 ✓）。
//!
//! 命令参数与文件内容全部由**纯函数**产出 → 单测逐字比对 ✓；
//! 真正执行的外壳只有薄薄一层 ✓。
//!
//! 自启跑的是**无界面循环**（`<程序> run` ✓），不是界面 —— 开机弹窗很讨厌 ✗。

use crate::platform::{self, Os};
use std::path::{Path, PathBuf};

/// 注册表值名 / systemd 单元名（三处保持一致 ✓）。
pub const ENTRY_NAME: &str = "StardustFlashLink";
/// macOS 的 LaunchAgent 标签 ✓。
pub const MAC_LABEL: &str = "com.stardust.flashlink";

/// 自启状态。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AutostartState {
    /// 已开启
    Enabled,
    /// 没开
    Disabled,
    /// 查不出来（命令不存在等）
    Unknown(String),
}

impl AutostartState {
    pub fn is_enabled(&self) -> bool {
        matches!(self, AutostartState::Enabled)
    }

    /// 给界面 / 命令行的一行中文 ✓。
    pub fn label_cn(&self) -> String {
        match self {
            AutostartState::Enabled => "已开启（登录后自动运行）✓".to_string(),
            AutostartState::Disabled => "未开启".to_string(),
            AutostartState::Unknown(reason) => format!("查不出来：{}", reason),
        }
    }
}

// ---------------- 纯函数：要写什么 ----------------

/// 注册表里要写的值：**带引号** ✓
/// （路径含空格时 `reg.exe` 参数会被截断 ✗ —— 这是最常见的坑）。
pub fn windows_value(exe: &Path) -> String {
    format!("\"{}\"", exe.display())
}

/// `reg add` 的参数（`HKCU` 开头 ⇒ 用户级、免管理员 ✓）。
pub fn windows_add_args(exe: &Path) -> Vec<String> {
    vec![
        "add".to_string(),
        WINDOWS_RUN_KEY.to_string(),
        "/v".to_string(),
        ENTRY_NAME.to_string(),
        "/t".to_string(),
        "REG_SZ".to_string(),
        "/d".to_string(),
        windows_value(exe),
        "/f".to_string(),
    ]
}

/// `reg query` 的参数 ✓。
pub fn windows_query_args() -> Vec<String> {
    vec![
        "query".to_string(),
        WINDOWS_RUN_KEY.to_string(),
        "/v".to_string(),
        ENTRY_NAME.to_string(),
    ]
}

/// `reg delete` 的参数 ✓。
pub fn windows_delete_args() -> Vec<String> {
    vec![
        "delete".to_string(),
        WINDOWS_RUN_KEY.to_string(),
        "/v".to_string(),
        ENTRY_NAME.to_string(),
        "/f".to_string(),
    ]
}

/// 是否把 `--user` 服务认成「已开启」（`reg query` 输出里出现我们的值名 ✓）。
pub fn reg_output_has_entry(text: &str) -> bool {
    text.contains(ENTRY_NAME)
}

const WINDOWS_RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";

/// Linux：systemd --user 单元文件内容 ✓。
pub fn systemd_unit(exe: &Path, args: &[&str]) -> String {
    format!(
        "[Unit]\n\
         Description=星尘闪连（校园网自动登录）\n\
         After=network-online.target\n\
         \n\
         [Service]\n\
         Type=simple\n\
         ExecStart={}\n\
         Restart=on-failure\n\
         RestartSec=5\n\
         \n\
         [Install]\n\
         WantedBy=default.target\n",
        exec_line(exe, args)
    )
}

/// macOS：LaunchAgent plist 内容 ✓（`RunAtLoad` + `KeepAlive` ⇒ 登录就跑、挂了重来 ✓）。
pub fn launchd_plist(exe: &Path, args: &[&str]) -> String {
    let mut arguments = format!("    <string>{}</string>\n", escape_xml(&exe.display().to_string()));
    for arg in args {
        arguments.push_str(&format!("    <string>{}</string>\n", escape_xml(arg)));
    }
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\">\n\
         <dict>\n\
         \x20   <key>Label</key>\n\
         \x20   <string>{label}</string>\n\
         \x20   <key>ProgramArguments</key>\n\
         \x20   <array>\n{arguments}    </array>\n\
         \x20   <key>RunAtLoad</key>\n\
         \x20   <true/>\n\
         \x20   <key>KeepAlive</key>\n\
         \x20   <true/>\n\
         </dict>\n\
         </plist>\n",
        label = MAC_LABEL,
        arguments = arguments
    )
}

/// 可执行文件 + 参数 → 一行 `ExecStart`（**带引号** ✓ 供 systemd 用）。
pub fn exec_line(exe: &Path, args: &[&str]) -> String {
    let mut line = format!("\"{}\"", exe.display());
    for arg in args {
        line.push(' ');
        line.push_str(arg);
    }
    line
}

fn escape_xml(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// Linux 的单元文件路径（纯函数：给个 home 就能算 ✓）。
pub fn systemd_unit_path(home: &Path) -> PathBuf {
    home.join(".config/systemd/user").join(format!("{}.service", platform::APP_ID))
}

/// macOS 的 plist 路径（纯函数 ✓）。
pub fn launchd_plist_path(home: &Path) -> PathBuf {
    home.join("Library/LaunchAgents").join(format!("{}.plist", MAC_LABEL))
}

/// 当前平台自启的落点（Windows 落在注册表 → None ✓）。
pub fn file_path() -> Option<PathBuf> {
    let home = platform::home_dir()?;
    match Os::current() {
        Os::Windows => None,
        Os::MacOs => Some(launchd_plist_path(&home)),
        Os::Linux => Some(systemd_unit_path(&home)),
    }
}

// ---------------- 执行层（很薄 ✓） ----------------

/// 将要做什么（`--dry-run` 用：**只打印，不动系统** ✓）。
pub fn plan(exe: &Path, args: &[&str]) -> Vec<String> {
    let target = file_path()
        .map(|path| path.display().to_string())
        .unwrap_or_else(|| "（Windows：注册表 Run 键）".to_string());
    match Os::current() {
        Os::Windows => vec![format!("reg.exe {}", windows_add_args(exe).join(" "))],
        Os::Linux => vec![
            format!("写入 {}", target),
            format!("内容:\n{}", systemd_unit(exe, args)),
            "systemctl --user daemon-reload".to_string(),
            format!("systemctl --user enable --now {}.service", platform::APP_ID),
        ],
        Os::MacOs => vec![
            format!("写入 {}", target),
            format!("内容:\n{}", launchd_plist(exe, args)),
            format!("launchctl load -w {}", target),
        ],
    }
}

/// 打开自启。`exe` 一般是「当前可执行文件」，`args` 通常传 `["run"]` ✓。
pub fn enable(exe: &Path, args: &[&str]) -> Result<String, String> {
    match Os::current() {
        Os::Windows => {
            run_reg(&windows_add_args(exe))?;
            Ok(format!(
                "已写入当前用户的启动项：{}（免管理员 ✓）",
                windows_value(exe)
            ))
        }
        Os::Linux => {
            let path = file_path().ok_or("找不到 home 目录，无法定位 systemd 单元 ✗")?;
            write_file(&path, &systemd_unit(exe, args))?;
            let _ = run("systemctl", &["--user", "daemon-reload"]);
            run(
                "systemctl",
                &["--user", "enable", "--now", &format!("{}.service", platform::APP_ID)],
            )?;
            Ok(format!("已安装并启用 {}", path.display()))
        }
        Os::MacOs => {
            let path = file_path().ok_or("找不到 home 目录，无法定位 LaunchAgent ✗")?;
            write_file(&path, &launchd_plist(exe, args))?;
            let plist = path.display().to_string();
            let _ = run("launchctl", &["unload", "-w", &plist]);
            run("launchctl", &["load", "-w", &plist])?;
            Ok(format!("已安装 {}", path.display()))
        }
    }
}

/// 关掉自启（**幂等** ✓：本来就没开也算成功 ✓）。
pub fn disable() -> Result<String, String> {
    match Os::current() {
        Os::Windows => {
            run_reg(&windows_delete_args())?;
            Ok("已删除启动项 ✓".to_string())
        }
        Os::Linux => {
            let service = format!("{}.service", platform::APP_ID);
            let _ = run("systemctl", &["--user", "disable", "--now", &service]);
            remove_file_if_exists()?;
            let _ = run("systemctl", &["--user", "daemon-reload"]);
            Ok("已停用并删除 systemd 单元 ✓".to_string())
        }
        Os::MacOs => {
            if let Some(path) = file_path() {
                let _ = run("launchctl", &["unload", "-w", &path.display().to_string()]);
            }
            remove_file_if_exists()?;
            Ok("已卸载 LaunchAgent ✓".to_string())
        }
    }
}

/// 现在的状态 ✓（只读 ✓）。
pub fn status() -> AutostartState {
    match Os::current() {
        Os::Windows => match crate::proc::run(Path::new("reg"), &windows_query_args()) {
            Ok(got) => {
                let text = format!("{}{}", got.stdout, got.stderr);
                if got.ok() && reg_output_has_entry(&text) {
                    AutostartState::Enabled
                } else {
                    AutostartState::Disabled
                }
            }
            Err(e) => AutostartState::Unknown(format!("调用 reg.exe 失败: {}", e)),
        },
        _ => match file_path() {
            Some(path) if path.is_file() => AutostartState::Enabled,
            Some(_) => AutostartState::Disabled,
            None => AutostartState::Unknown("找不到 home 目录".to_string()),
        },
    }
}

fn remove_file_if_exists() -> Result<(), String> {
    if let Some(path) = file_path() {
        if path.exists() {
            std::fs::remove_file(&path)
                .map_err(|e| format!("删除 {} 失败: {}", path.display(), e))?;
        }
    }
    Ok(())
}

fn run(program: &str, args: &[&str]) -> Result<(), String> {
    let owned: Vec<String> = args.iter().map(|a| a.to_string()).collect();
    // 带硬超时 ✓（外部工具卡住不许把调用方一起带走 ✗ —— 见 [`crate::proc`] ✓）
    let got = crate::proc::run(Path::new(program), &owned)?;
    if got.ok() {
        return Ok(());
    }
    Err(format!("{} 返回失败：{}", program, got.detail()))
}

fn run_reg(args: &[String]) -> Result<(), String> {
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    run("reg", &refs)
}

fn write_file(path: &Path, content: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建目录 {} 失败: {}", parent.display(), e))?;
    }
    std::fs::write(path, content).map_err(|e| format!("写入 {} 失败: {}", path.display(), e))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// 故意用带空格的 Windows 路径：**空格是这类自启配置最常见的翻车点** ✗。
    fn exe() -> PathBuf {
        PathBuf::from(r"C:\Program Files\Stardust Flash Link\stardust-flash-link.exe")
    }

    #[test]
    fn windows_entry_is_quoted_and_strictly_user_level() {
        let value = windows_value(&exe());
        assert!(value.starts_with('"') && value.ends_with('"'), "路径必须带引号 ✗: {}", value);

        let joined = windows_add_args(&exe()).join(" ");
        assert!(joined.contains(r"HKCU\"), "只用当前用户键（免管理员 ✓）: {}", joined);
        assert!(!joined.contains("HKLM"), "绝不能碰机器级键 ✗: {}", joined);
        assert!(joined.contains("/f"), "要覆盖写 ✓: {}", joined);

        assert!(reg_output_has_entry("    StardustFlashLink    REG_SZ    \"C:\\x.exe\""));
        assert!(!reg_output_has_entry("错误: 系统找不到指定的注册表项或值。"));
        assert!(windows_delete_args().contains(&"/f".to_string()));
    }

    #[test]
    fn systemd_unit_is_complete_and_quotes_the_exec_line() {
        let unit = systemd_unit(&exe(), &["run"]);
        for section in ["[Unit]", "[Service]", "[Install]"] {
            assert!(unit.contains(section), "少了 {} ✗\n{}", section, unit);
        }
        assert!(
            unit.contains(
                r#"ExecStart="C:\Program Files\Stardust Flash Link\stardust-flash-link.exe" run"#
            ),
            "{}",
            unit
        );
        assert!(unit.contains("Restart=on-failure"), "崩了要自己起来 ✓");
        assert!(unit.contains("WantedBy=default.target"), "登录即起 ✓（--user 场景）");
        assert!(unit.ends_with('\n'));
    }

    #[test]
    fn launchd_plist_is_wellformed_and_escapes_specials() {
        let plist = launchd_plist(&exe(), &["run"]);
        assert!(plist.starts_with("<?xml"));
        assert!(plist.trim_end().ends_with("</plist>"), "{}", plist);
        assert!(plist.contains("<string>com.stardust.flashlink</string>"));
        assert!(plist.contains("<key>RunAtLoad</key>"));
        assert!(plist.contains("<key>KeepAlive</key>"));
        assert!(plist.contains("<string>run</string>"));

        // 路径里有 & < > 必须转义 ✗（否则 plist 直接解析失败 ✗）
        let weird = launchd_plist(PathBuf::from("/Users/a&b/x<1>.app/bin").as_path(), &[]);
        assert!(weird.contains("a&amp;b"), "{}", weird);
        assert!(weird.contains("&lt;1&gt;"), "{}", weird);
    }

    #[test]
    fn paths_are_user_scoped_and_names_stay_in_sync() {
        let home = PathBuf::from("/home/someone");
        assert_eq!(
            systemd_unit_path(&home),
            home.join(".config/systemd/user")
                .join(format!("{}.service", platform::APP_ID))
        );
        assert_eq!(
            launchd_plist_path(&home),
            home.join("Library/LaunchAgents")
                .join(format!("{}.plist", MAC_LABEL))
        );
        assert!(MAC_LABEL.contains("stardust"), "标签要和产品对得上 ✓");
    }

    #[test]
    fn plan_never_suggests_machine_wide_or_sudo() {
        let plan = plan(&exe(), &["run"]);
        assert!(!plan.is_empty());
        let text = plan.join("\n");
        assert!(!text.contains("HKLM"), "✗ 机器级键：{}", text);
        assert!(!text.contains("sudo"), "✗ 一次 sudo 都不该有：{}", text);
    }

    #[test]
    fn status_is_readonly_and_says_something() {
        let state = status();
        assert!(!state.label_cn().is_empty());
        assert_eq!(state.is_enabled(), matches!(state, AutostartState::Enabled));
    }
}
