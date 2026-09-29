//! 打包产物里那几份「说明书文件」的生成（**纯字符串 ✓**）—— CI 直接落盘即可 ✓。
//!
//! 目的：让同一个二进制在三条平台上都能被人正确地装/跑 ✓，而且**内容有单测盯着** ✓
//! （比把 `.desktop` / `Info.plist` 的内容散落在 YAML 里更可控 ✓）。
//!
//!   - Linux：`.desktop` 桌面项 ✓
//!   - macOS：`.app` 里的 `Info.plist` ✓
//!   - Debian：`control` ✓（deb 元数据 ✓）
//!   - 通用：产物命名 + `SHA256SUMS` 行 ✓（自动升级对着它校验 ✓）

use crate::platform::{self, Os};

/// 一次打包的上下文 ✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageInfo {
    /// 版本串，如 `3.0.0.0-preview.1` ✓
    pub version: String,
    /// 目标标签，如 `linux-x86_64` ✓
    pub target: String,
    /// 主程序文件名（Windows 带 `.exe` ✓）
    pub exe_name: String,
    /// 人类可读名的尺寸（字节，写进 deb 的 `Installed-Size` ✓）
    pub size_bytes: u64,
}

impl PackageInfo {
    /// 按当前平台/版本自动填 ✓。
    pub fn current(exe_name: &str, size_bytes: u64) -> PackageInfo {
        PackageInfo {
            version: crate::app_version_string(),
            target: platform::target_label(),
            exe_name: exe_name.to_string(),
            size_bytes,
        }
    }

    /// 覆盖字段 ✓ —— **交叉编译时必须用** ✓：
    /// CI 在 x86_64 runner 上给 `linux-aarch64` / `linux-armv7` 打包时，
    /// 跑的是宿主编出来的辅助程序 ✗，它自报的目标会写成宿主 ✓ → 得显式指定 ✓。
    pub fn with_overrides(
        mut self,
        target: Option<&str>,
        exe_name: Option<&str>,
        version: Option<&str>,
    ) -> PackageInfo {
        if let Some(value) = target {
            self.target = value.to_string();
        }
        if let Some(value) = exe_name {
            self.exe_name = value.to_string();
        }
        if let Some(value) = version {
            self.version = value.to_string();
        }
        self
    }
}

/// 产物名（不含扩展名）：`stardust-flash-link-<版本>-<目标>` ✓
pub fn artifact_base(info: &PackageInfo) -> String {
    format!("{}-{}-{}", platform::APP_ID, info.version, info.target)
}

/// 完整产物名 ✓（`.tar.gz` / `.zip` / `.dmg` / `.deb` ✓）。
pub fn artifact_name(info: &PackageInfo, extension: &str) -> String {
    format!("{}.{}", artifact_base(info), extension)
}

/// `SHA256SUMS` 里的一行 ✓（GNU coreutils 格式：**两个空格** ✓，文件名不带路径 ✓）。
pub fn sha256sums_line(digest_hex: &str, file_name: &str) -> String {
    format!("{}  {}", digest_hex, file_name)
}

/// Linux 的 `.desktop` 桌面项 ✓。
pub fn desktop_entry(info: &PackageInfo) -> String {
    format!(
        "[Desktop Entry]\n\
         Type=Application\n\
         Name=星尘闪连\n\
         Name[en]=Stardust Flash Link\n\
         Comment=校园网认证网关自动登录：开机自启 + 周期自检\n\
         Exec={exe} run\n\
         Icon={app}\n\
         Terminal=false\n\
         Categories=Network;Utility;\n\
         Keywords=campus;network;login;\n\
         X-GNOME-Autostart-enabled=false\n\
         Comment[zh_CN]=校园网认证网关自动登录：开机自启 + 周期自检\n",
        exe = info.exe_name,
        app = platform::APP_ID
    )
}

/// Debian 的 `control` ✓（`Installed-Size` 单位是 KB ✓ —— 这是 deb 的规矩 ✓）。
pub fn deb_control(info: &PackageInfo) -> String {
    let architecture = match platform::arch() {
        "x86_64" => "amd64",
        "aarch64" => "arm64",
        "armv7" => "armhf",
        other => other,
    };
    format!(
        "Package: {package}\n\
         Version: {version}\n\
         Section: net\n\
         Priority: optional\n\
         Architecture: {architecture}\n\
         Installed-Size: {size_kb}\n\
         Maintainer: Stardust Flash Link <noreply@example.invalid>\n\
         Description: 校园网认证网关自动登录\n\
         自动检查校园网在线状态，掉线自动重登；支持按 Wi-Fi 自动切换配置方案。\n",
        package = platform::APP_ID,
        version = info.version,
        architecture = architecture,
        size_kb = (info.size_bytes + 1023) / 1024
    )
}

/// macOS 的 `Info.plist` ✓（`.app/Contents/Info.plist` ✓）。
pub fn macos_info_plist(info: &PackageInfo) -> String {
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\">\n\
         <dict>\n\
         \x20   <key>CFBundleName</key>\n\
         \x20   <string>星尘闪连</string>\n\
         \x20   <key>CFBundleDisplayName</key>\n\
         \x20   <string>Stardust Flash Link</string>\n\
         \x20   <key>CFBundleIdentifier</key>\n\
         \x20   <string>{bundle}</string>\n\
         \x20   <key>CFBundleExecutable</key>\n\
         \x20   <string>{exe}</string>\n\
         \x20   <key>CFBundleVersion</key>\n\
         \x20   <string>{version}</string>\n\
         \x20   <key>CFBundleShortVersionString</key>\n\
         \x20   <string>{version}</string>\n\
         \x20   <key>CFBundlePackageType</key>\n\
         \x20   <string>APPL</string>\n\
         \x20   <key>LSMinimumSystemVersion</key>\n\
         \x20   <string>11.0</string>\n\
         \x20   <key>NSHighResolutionCapable</key>\n\
         \x20   <true/>\n\
         </dict>\n\
         </plist>\n",
        bundle = format!("com.stardust.{}", platform::APP_ID.replace('-', "")),
        exe = info.exe_name,
        version = info.version
    )
}

/// 包里那封「怎么用」说明 ✓（纯文本，双击就能看 ✓）。
pub fn readme_txt(info: &PackageInfo) -> String {
    let service_hint = match Os::current() {
        Os::Windows => "  stardust-flash-link.exe service status      （常驻服务，Windows 走 NSSM）",
        _ => "  ./stardust-flash-link service status        （常驻服务，免 root）",
    };
    format!(
        "星尘闪连 {version} · 预览版（{target}）\n\
         ==================================================\n\n\
         这是 3.0 跨平台线的**预览版** ✓ —— 正式的自动升级不会分发它 ✓。\n\n\
         常用命令：\n\
         \x20 ./stardust-flash-link status              查看版本/平台/配置状态\n\
         \x20 ./stardust-flash-link login               跑一次登录检查\n\
         \x20 ./stardust-flash-link run                 常驻循环（按配置间隔检查）\n\
         {service_hint}\n\
         \x20 ./stardust-flash-link checksum <文件>     算 SHA-256\n\n\
         配置文件与密码沿用 2.x 的格式（config.json / password.txt）✓，老用户零迁移 ✓。\n",
        version = info.version,
        target = info.target,
        service_hint = service_hint
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn info() -> PackageInfo {
        PackageInfo {
            version: "3.0.0.0-preview.1".to_string(),
            target: "linux-x86_64".to_string(),
            exe_name: "stardust-flash-link".to_string(),
            size_bytes: 4_500_000,
        }
    }

    #[test]
    fn artifact_names_are_stable_and_readable() {
        let info = info();
        assert_eq!(
            artifact_base(&info),
            "stardust-flash-link-3.0.0.0-preview.1-linux-x86_64"
        );
        assert_eq!(
            artifact_name(&info, "tar.gz"),
            "stardust-flash-link-3.0.0.0-preview.1-linux-x86_64.tar.gz"
        );
        assert!(artifact_name(&info, "deb").ends_with(".deb"));
        // 命名里**不能有空格/斜杠** ✗（脚本里到处要用它 ✓）
        let name = artifact_name(&info, "zip");
        assert!(!name.contains(' ') && !name.contains('/') && !name.contains('\\'), "{}", name);
    }

    #[test]
    fn sha256sums_line_matches_coreutils_format() {
        let line = sha256sums_line("abc123", "bundle.tar.gz");
        assert_eq!(line, "abc123  bundle.tar.gz", "两个空格（coreutils 格式 ✓）");
        assert!(!line.contains('/'), "只写文件名，不写路径 ✓");
    }

    #[test]
    fn desktop_entry_has_the_fields_linux_needs() {
        let entry = desktop_entry(&info());
        for key in ["[Desktop Entry]", "Type=Application", "Name=", "Exec=", "Icon=", "Categories="] {
            assert!(entry.contains(key), "缺 {} ✗\n{}", key, entry);
        }
        assert!(entry.contains("Exec=stardust-flash-link run"), "Exec 要能直接启动 ✓\n{}", entry);
        assert!(entry.contains("Terminal=false"), "不该弹终端窗口 ✗");
        assert!(entry.ends_with('\n'));
    }

    #[test]
    fn deb_control_has_correct_architecture_and_size() {
        let control = deb_control(&info());
        for key in ["Package:", "Version:", "Architecture:", "Maintainer:", "Description:"] {
            assert!(control.contains(key), "缺 {} ✗\n{}", key, control);
        }
        assert!(control.contains("Package: stardust-flash-link"));
        assert!(control.contains("Version: 3.0.0.0-preview.1"));
        assert!(control.contains("Installed-Size: 4395"), "要换算成 KB ✓\n{}", control);
        // 架构名必须是 deb 认的那几个 ✓（x86_64 ✗ → amd64 ✓）
        if cfg!(target_arch = "x86_64") {
            assert!(control.contains("Architecture: amd64"), "{}", control);
        }
        assert!(control.contains("Description:"), "Description 必须非空 ✗");
    }

    #[test]
    fn macos_plist_is_wellformed() {
        let plist = macos_info_plist(&info());
        assert!(plist.starts_with("<?xml"));
        assert!(plist.trim_end().ends_with("</plist>"));
        for key in ["CFBundleExecutable", "CFBundleIdentifier", "CFBundleVersion", "CFBundlePackageType", "LSMinimumSystemVersion"] {
            assert!(plist.contains(key), "缺 {} ✗\n{}", key, plist);
        }
        assert!(plist.contains("<string>APPL</string>"));
    }

    #[test]
    fn readme_says_preview_and_lists_commands() {
        let text = readme_txt(&info());
        assert!(text.contains("预览版"), "必须写清是预览版 ✓\n{}", text);
        assert!(text.contains("3.0.0.0-preview.1"));
        for command in ["status", "login", "run", "service", "checksum"] {
            assert!(text.contains(command), "说明里该提到 {} ✓", command);
        }
        assert!(text.contains("config.json"), "要讲清配置格式兼容 ✓");
    }

    #[test]
    fn current_info_fills_itself_from_the_build() {
        let auto = PackageInfo::current("stardust-flash-link", 1024);
        assert!(auto.version.starts_with("3.0."), "{}", auto.version);
        assert!(auto.target.contains('-'), "{}", auto.target);
        assert_eq!(auto.size_bytes, 1024);
    }

    #[test]
    fn overrides_are_for_cross_builds_and_keep_the_rest() {
        let info = PackageInfo::current("host-name", 2048).with_overrides(
            Some("linux-aarch64"),
            Some("stardust-flash-link"),
            Some("9.9.9.9-preview.7"),
        );
        assert_eq!(info.target, "linux-aarch64");
        assert_eq!(info.exe_name, "stardust-flash-link");
        assert_eq!(info.version, "9.9.9.9-preview.7");
        assert_eq!(info.size_bytes, 2048, "体积不受覆盖影响 ✓");
        assert_eq!(
            artifact_base(&info),
            "stardust-flash-link-9.9.9.9-preview.7-linux-aarch64"
        );
        // 不传覆盖 → 原样保留 ✓
        let same = PackageInfo::current("x", 1).with_overrides(None, None, None);
        assert_eq!(same.exe_name, "x");
    }
}

