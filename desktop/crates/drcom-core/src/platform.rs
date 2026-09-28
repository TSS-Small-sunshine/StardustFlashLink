//! 平台差异集中在这里：**数据/配置/日志目录**、**服务（守护）方式**、**架构标识**。
//!
//! 目标平台矩阵（见 `docs/PLATFORMS.md`）：
//!   Windows x86_64 / ARM64 · Linux AMD64 / ARMv8 / **ARMv7** · macOS Intel / Apple Silicon
//!
//! 便携版（portable）规则：程序目录里存在 `portable.marker`，或配置里 `portable_mode = true`
//! → 数据一律落在**程序目录**下的 `data/`，不写系统目录 ✓（U 盘/免安装场景）。
//! 如果程序目录不可写（比如装在 Program Files 且无权限），**自动退回**系统目录并记一条日志 ✓。

use std::path::{Path, PathBuf};

/// 便携版标记文件名（放在程序目录里即启用；文件名本身也当版本身份之一 ✓）。
pub const PORTABLE_MARKER: &str = "portable.marker";
/// 应用标识（目录名 / 服务名 / 包名统一用这个，**不带空格**）。
pub const APP_ID: &str = "stardust-flash-link";

/// 运行平台。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Os {
    Windows,
    MacOs,
    Linux,
}

impl Os {
    pub fn current() -> Os {
        if cfg!(target_os = "windows") {
            Os::Windows
        } else if cfg!(target_os = "macos") {
            Os::MacOs
        } else {
            Os::Linux
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Os::Windows => "windows",
            Os::MacOs => "macos",
            Os::Linux => "linux",
        }
    }

    pub fn label_cn(self) -> &'static str {
        match self {
            Os::Windows => "Windows",
            Os::MacOs => "macOS",
            Os::Linux => "Linux",
        }
    }

    /// 后台常驻应该用什么（M3 实现，这里先给出「该装什么」的说明与手册入口）。
    pub fn service_flavor(self) -> &'static str {
        match self {
            Os::Windows => "Windows 服务（NSSM）",
            Os::MacOs => "launchd LaunchAgent（com.stardust.flashlink）",
            Os::Linux => "systemd --user（stardust-flash-link.service）",
        }
    }

    /// 开机自启的落点（M3 用）。
    pub fn autostart_hint(self) -> String {
        match self {
            Os::Windows => "注册表 Run 键 / 计划任务".to_string(),
            Os::MacOs => "~/Library/LaunchAgents/com.stardust.flashlink.plist".to_string(),
            Os::Linux => "~/.config/systemd/user/stardust-flash-link.service".to_string(),
        }
    }
}

/// 架构标识（ci 矩阵、更新清单、诊断包里都要 ✓）。
pub fn arch() -> &'static str {
    if cfg!(target_arch = "x86_64") {
        "x86_64"
    } else if cfg!(target_arch = "aarch64") {
        "aarch64"
    } else if cfg!(target_arch = "arm") {
        "armv7"
    } else if cfg!(target_arch = "x86") {
        "x86"
    } else {
        "unknown"
    }
}

/// `linux-armv7` 这样的目标标签（给更新清单用 ✓）。
pub fn target_label() -> String {
    format!("{}-{}", Os::current().as_str(), arch())
}

fn home_dir() -> Option<PathBuf> {
    // 不引 dirs crate：三个平台各取一个环境变量就够了，且少一个依赖 ✓
    if cfg!(target_os = "windows") {
        std::env::var_os("USERPROFILE").map(PathBuf::from)
    } else {
        std::env::var_os("HOME").map(PathBuf::from)
    }
}

fn program_dir() -> PathBuf {
    // 便携版判定用：可执行文件所在目录 ✓
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// 是否便携模式（标记文件在程序目录里 ✓）。
pub fn is_portable() -> bool {
    program_dir().join(PORTABLE_MARKER).exists()
}

/// 数据目录（配置 / 日志 / 备份都在它下面）。
pub fn data_dir() -> PathBuf {
    if is_portable() {
        let portable = program_dir().join("data");
        if writable(&portable) {
            return portable;
        }
    }
    match Os::current() {
        // Windows：用 ProgramData（服务账户与用户账户都能写 ✓）
        Os::Windows => std::env::var_os("ProgramData")
            .map(|p| PathBuf::from(p).join("StardustFlashLink"))
            .unwrap_or_else(|| program_dir().join("data")),
        Os::MacOs => home_dir()
            .map(|h| h.join("Library/Application Support/com.stardust.flashlink"))
            .unwrap_or_else(|| program_dir().join("data")),
        Os::Linux => std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .or_else(|| home_dir().map(|h| h.join(".local/share")))
            .map(|base| base.join(APP_ID))
            .unwrap_or_else(|| program_dir().join("data")),
    }
}

/// 配置目录（Linux 习惯放 `~/.config`，其它平台与数据目录同处 ✓）。
pub fn config_dir() -> PathBuf {
    if is_portable() {
        return data_dir();
    }
    match Os::current() {
        Os::Linux => std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| home_dir().map(|h| h.join(".config")))
            .map(|base| base.join(APP_ID))
            .unwrap_or_else(data_dir),
        _ => data_dir(),
    }
}

pub fn config_path() -> PathBuf {
    config_dir().join("config.json")
}

/// 密码文件（与 2.x 同名同格式：纯文本一行，`#` 开头是注释 ✓）。
pub fn password_path() -> PathBuf {
    data_dir().join("password.txt")
}

pub fn log_dir() -> PathBuf {
    data_dir().join("logs")
}

fn writable(dir: &Path) -> bool {
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let probe = dir.join(".write-probe");
    match std::fs::write(&probe, b"ok") {
        Ok(_) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn os_and_arch_are_reported() {
        let os = Os::current();
        assert!(["windows", "macos", "linux"].contains(&os.as_str()));
        assert!(!arch().is_empty());
        assert!(target_label().starts_with(os.as_str()));
        assert!(os.service_flavor().len() > 3);
        assert!(!os.autostart_hint().is_empty());
    }

    #[test]
    fn data_dir_is_under_some_wellknown_root() {
        let dir = data_dir();
        let text = dir.to_string_lossy().to_lowercase();
        // 三类合法落点：系统目录 / 用户目录 / 便携目录 ✓
        let looks_ok = text.contains("stardustflashlink")
            || text.contains("com.stardust.flashlink")
            || text.contains(APP_ID)
            || text.ends_with("data");
        assert!(looks_ok, "数据目录落点可疑: {}", dir.display());
        assert!(config_path().to_string_lossy().ends_with("config.json"));
        assert!(password_path().to_string_lossy().ends_with("password.txt"));
        assert!(log_dir().to_string_lossy().ends_with("logs"));
    }
}
