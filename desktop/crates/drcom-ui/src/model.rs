//! 界面与核心之间的一层「视图模型」：**界面不读文件、不碰网络**，只显示这里的字符串 ✓。
//! 好处：Slint 之外的逻辑全部能在单测里验证（`cargo test` 不弹窗口也能跑 ✓）。

use drcom_core::{
    config::Config,
    net::{CurlHttp, HttpDownload, HttpGet, PlainHttp},
    password, platform, secret, session,
    settings::SettingsForm,
    update::{self, UpdateCheck},
    update_apply, Channel, Status, Version,
};
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
    pub interval_note: String,
    pub data_dir_line: String,
    pub config_line: String,
    pub service_line: String,
    /// v2.1.1.0 风格：界面自带的后台检查状态（由 worker 每秒刷新 ✓）
    pub background_line: String,
}

/// 采集一次（只读 ✓；界面点「刷新」也是走它）。
pub fn collect() -> Dashboard {
    let status = Status::collect();
    let os = platform::Os::current();
    // 读配置统一走 legacy 兼容层（审计 P1-3）：3.0 没有配置、2.x 有时只读沿用 ✓
    let read = drcom_core::legacy::load_config_or_default();
    let cfg = read.config.clone();
    let errors = cfg.validate();

    Dashboard {
        version_line: status.version.clone(),
        channel_line: format!("{} · {}", status.channel_label, status.channel),
        is_prerelease: status.is_prerelease,
        os_line: os.label_cn().to_string(),
        target_line: status.target.clone(),
        portable_line: if status.portable { "便携模式".to_string() } else { "安装模式".to_string() },
        gateway_line: if cfg.host.is_empty() { "（未填）".to_string() } else { cfg.host.clone() },
        account_line: account_display(&cfg),
        interval_line: if cfg.auto_check_enabled {
            format!("每 {} 分钟", cfg.auto_check_interval_min)
        } else {
            "已关闭".to_string()
        },
        interval_note: match drcom_core::profiles::active(&cfg) {
            Some(name) => format!("方案：{}", name),
            None => "未启用方案".to_string(),
        },
        data_dir_line: shorten(&status.data_dir, 42),
        // 2.x 只读兼容时必须在界面上说清来源（不然用户以为已经迁移完了 ✗，审计 P1-3）
        config_line: config_line_of(read.from, platform::config_path().exists(), errors.len()),
        service_line: os.service_flavor().to_string(),
        // 只是初值 ✓ —— 真正的状态由 worker 线程每秒刷进界面 ✓
        background_line: "后台检查启动中…".to_string(),
    }
}

/// 配置状态行（**纯函数** → 单测能覆盖 ✓）。
///
/// 2.x 只读兼容时**必须**显示来源（审计 P1-3）：否则界面说「未初始化 / 正常」，
/// 用户会以为 3.0 已经在用自己的配置了 ✗。
pub fn config_line_of(from: drcom_core::LoadedFrom, own_config_exists: bool, errors: usize) -> String {
    if from.is_legacy() {
        return if errors == 0 {
            "来自 2.x 安装目录（只读兼容）".to_string()
        } else {
            format!("来自 2.x（只读兼容）· {} 个问题", errors)
        };
    }
    if !own_config_exists {
        return "未初始化".to_string();
    }
    if errors == 0 {
        "正常".to_string()
    } else {
        format!("{} 个问题", errors)
    }
}

/// 「打开数据目录」用哪个系统命令 ✓（**纯函数**：平台差异只在这里 —— R2 / 审计 P2-10；
/// 三平台可以在任何一个 runner 上参数化单测 ✓）。
///
/// 返回 `(程序, 参数)`：Windows `explorer`、macOS `open`、Linux `xdg-open`。
pub fn open_dir_command(os: platform::Os, path: &str) -> (String, Vec<String>) {
    let program = match os {
        platform::Os::Windows => "explorer",
        platform::Os::MacOs => "open",
        platform::Os::Linux => "xdg-open",
    };
    (program.to_string(), vec![path.to_string()])
}

/// 「打开数据目录」：三平台各用系统命令（不引第三方 crate ✓）。
pub fn open_data_dir() -> Result<(), String> {
    let dir = platform::data_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("建不了目录 {}: {}", dir.display(), e))?;
    let path = dir.display().to_string();
    let (program, args) = open_dir_command(platform::Os::current(), &path);
    std::process::Command::new(&program)
        .args(&args)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("打不开文件管理器：{}", e))
}

/// 账号在界面上的显示：脱敏 + 后缀（`2023******@yd` / `2023******（校内直连）`）✓
pub fn account_display(cfg: &Config) -> String {
    if !cfg.account_configured() {
        return "未设置".to_string();
    }
    let masked = secret::MaskedAccount(&cfg.account).to_string();
    if cfg.suffix.trim().is_empty() {
        format!("{}（校内直连）", masked)
    } else {
        format!("{}{}", masked, cfg.suffix)
    }
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
    // 读配置走 legacy 兼容层（审计 P1-3）：3.0 没有配置、2.x 有时**只读**沿用 2.x 的 ✓
    let read = match drcom_core::legacy::load_config() {
        Ok(read) => read,
        Err(e) => return ("danger".to_string(), e),
    };
    // 两边都没有配置 = **首次运行的正常状态** ✓ → 给可执行的指引，而不是抛系统错误 ✗
    if read.from == drcom_core::LoadedFrom::Default {
        return (
            "warn".to_string(),
            format!(
                "还没初始化配置（首次运行正常 ✓）\n需要先在下面这个文件里填认证网关与账号：\n{}\n字段：host（网关地址）、account（账号）、suffix（后缀）",
                cfg_path.display()
            ),
        );
    }
    // 2.x 只读兼容：在结果里写明来源，别让用户以为已经迁移到 3.0 了 ✗
    let legacy_note = if read.is_legacy() {
        format!(
            "来源：{}（只读兼容，2.x 的文件不会被改动 ✓）\n",
            read.legacy_hint()
        )
    } else {
        String::new()
    };
    let mut cfg = read.config;

    // 位置自适应：Wi-Fi 命中自动方案就切过去（**尾缀一起换** ✓，这就是「校内公共场合不带 @yd」的落地）
    let mut adapt_note = String::new();
    let ssid_now = drcom_core::probe::current_ssid();
    match drcom_core::profiles::adapt(&mut cfg, ssid_now.as_deref()) {
        Ok(Some(name)) => {
            let _ = cfg.save(&cfg_path);
            adapt_note = format!(
                "位置自适应：已切到方案「{}」（后缀 {}）\n",
                name,
                drcom_core::profiles::suffix_label(&cfg.suffix)
            );
        }
        Ok(None) => {}
        Err(e) => adapt_note = format!("位置自适应失败：{}\n", e),
    }
    let errors = cfg.validate();
    if !errors.is_empty() {
        return ("danger".to_string(), format!("配置有问题：\n  - {}", errors.join("\n  - ")));
    }
    if !cfg.account_configured() {
        return (
            "warn".to_string(),
            format!(
                "{}账号还没填 —— 请在 {} 里写 account\n（密码写到 {}，一行密码；只有安装包模板提示行算注释）",
                legacy_note,
                cfg_path.display(),
                platform::password_path().display()
            ),
        );
    }
    // 唯一实现：drcom_core::password（UI 不再自己复制一份读法 ✗）
    let password = password::read();
    if password.is_empty() {
        return (
            "danger".to_string(),
            format!(
                "密码文件是空的：{}\n（一行密码；以 `#` 开头的密码也照原样读取）",
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
                "{}{}{}\n（当前 Wi-Fi: {} · 本机 IP: {}）\n—— 去「设置」里把白名单改对，或关掉网络位置守卫 ✓",
                legacy_note,
                adapt_note,
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
            "{}{}{}\n（在线探测: {:?} · 回调: {}）\n—— 3.0 预览版目前只做「一次检查」，常驻后台在 M3 ✓",
            legacy_note,
            adapt_note,
            report.outcome.summary_cn(),
            report.online,
            report.callback
        ),
    )
}

// ── M4：GUI 的「检查更新」入口 ─────────────────────────────────────────────────
//
// 界面只负责**显示与调用**（R7 ✓）：
//   · 判断 / 挑资产 / 拼错误全在 drcom_core::update（纯函数 + 假 HTTP 可单测 ✓）；
//   · 这里只做三件事：拼「更新」行的初始文案、把结果翻成（kind, 文案）、选传输实现；
//   · **启动时绝不联网** ✗：初始文案只读版本与配置 ✓，联网只发生在用户点「检查更新」后 ✓。

/// 「更新」行的初始文案（**纯函数，不联网** ✓）：当前版本 + 配置里的更新通道。
///
/// 通道取**配置里的 `update_channel`**（不是版本自带的通道 ✓）—— 与 CLI 的
/// `update check` 同一份语义；`auto_update_enabled` 只影响「自动」，
/// 手动检查始终可用（这个入口也**不看**它 ✓）。
pub fn update_idle_line_of(current: &Version, channel: Channel) -> String {
    format!(
        "当前版本 {}（{}）· 更新通道：{}（{}）· 启动不联网，点「检查更新」才查",
        current.to_string_full(),
        current.channel.label_cn(),
        channel.label_cn(),
        channel.as_str()
    )
}

/// 运行时入口：读版本与配置拼初始文案（**只读，不联网** ✓ —— 单测走上面那个纯函数）。
pub fn update_idle_line() -> String {
    let channel = update::channel_from_config(&drcom_core::legacy::load_config_or_default().config);
    update_idle_line_of(&drcom_core::app_version(), channel)
}

/// 检查结果 → （kind, 文案）—— **纯函数**：界面拿到的就是最终要显示的两样东西 ✓。
///
/// kind 沿用界面现有约定：`ok`（已最新）/ `accent`（有新版本）/ `warn`（跳过）/
/// `danger`（出错）；「检查中…」这种过渡态由界面自己标 `info` ✓。
///
/// 三种主结果（与 CLI 的口径一致）：
///   · 有更新 → 新版本号 + 本机资产名 + 大小 + **下载命令**；GUI 还不会自我替换，
///     所以**绝不放**点了没用的「下载」按钮 ✗；
///   · 已是最新 → 当前版本 + 版本所属通道 + 「已是最新」；
///     （`UpdateCheck::UpToDate` 不带通道字段 → 显示当前版本自己的通道；
///      配置里的更新通道在初始文案里已经展示过 ✓）
///   · 出错 → **原样**带出 update.rs 的可读中文错误（已脱敏 ✓），最多两行。
pub fn format_update_check(result: &UpdateCheck, current: &Version) -> (String, String) {
    match result {
        UpdateCheck::UpToDate => (
            "ok".to_string(),
            format!(
                "当前版本 {}（{}）· 已是最新（这条更新通道上没有比你更新的版本）",
                current.to_string_full(),
                current.channel.label_cn()
            ),
        ),
        UpdateCheck::Available { release, asset } => (
            "accent".to_string(),
            format!(
                "发现新版本 {}（本机资产：{}，大小 {}）\n点「下载并安装」即可：先备份旧程序，失败自动回滚",
                release.version.to_string_full(),
                asset.name,
                size_text(asset.size)
            ),
        ),
        UpdateCheck::Skipped(reason) => ("warn".to_string(), format!("跳过更新：{}", reason)),
        UpdateCheck::Error(error) => ("danger".to_string(), clamp_lines(error, 2)),
    }
}

/// 字节数 → 人话（只用于展示；下载体积的判断在 core ✓）。
fn size_text(bytes: u64) -> String {
    if bytes == 0 {
        return "大小未知".to_string();
    }
    const KB: f64 = 1024.0;
    const MB: f64 = 1024.0 * 1024.0;
    const GB: f64 = 1024.0 * 1024.0 * 1024.0;
    let value = bytes as f64;
    if value < KB {
        format!("{} B", bytes)
    } else if value < MB {
        format!("{:.1} KB", value / KB)
    } else if value < GB {
        format!("{:.1} MB", value / MB)
    } else {
        format!("{:.1} GB", value / GB)
    }
}

/// 最多保留 `max` 行（界面行高有限：错误文案超了就截断，并以「…」收尾 ✓）。
fn clamp_lines(text: &str, max: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    if lines.len() <= max {
        return text.to_string();
    }
    let mut out = lines[..max].join("\n");
    out.push('…');
    out
}

/// 「检查更新」的**可注入**入口：单测传假 HTTP 客户端 → **完全离线** ✓。
///
/// 当前版本取 `drcom_core::app_version()`，通道取配置 `update_channel`
/// （与 CLI `update check` 同一条链路：`update::check_for_update` ✓）。
/// 坏配置不阻塞检查（`load_config_or_default` 会退回默认值 ✓）。
pub fn update_check_with(http: &dyn HttpGet) -> UpdateCheck {
    let current = drcom_core::app_version();
    let channel = update::channel_from_config(&drcom_core::legacy::load_config_or_default().config);
    update::check_for_update(current, channel, http)
}

/// 真正联网的入口：**只被 worker 的后台线程调用** ✓（界面线程绝不直接调它 ✗）。
///
/// 走 `CurlHttp`（系统 curl，HTTPS）：它在后台线程里起自己的子进程，
/// 不共享可变状态 —— 跨线程使用是安全的 ✓（可读错误由 update.rs 统一给 ✓）。
pub fn update_check() -> UpdateCheck {
    update_check_with(&CurlHttp)
}

/// 「下载并安装」成功后界面要用的两样东西 ✓（**不含任何写盘逻辑**：都在 core ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstalledUpdate {
    /// 装上的版本（显示用 ✓）。
    pub version: String,
    /// 新程序的正式路径（重启 = 拉起它 ✓）。
    pub new_exe: std::path::PathBuf,
    /// 旧文件清理告警（删不掉不是错误 ✓）。
    pub warnings: Vec<String>,
}

/// 成功文案（纯函数 ✓）：**正在自动重启**，不是让用户自己找 ✓。
pub fn install_success_line(report: &InstalledUpdate) -> String {
    let mut line = format!("已安装 {}，需要重启生效 —— 正在重启新程序…", report.version);
    if !report.warnings.is_empty() {
        line.push_str(&format!(
            "（{} 条旧文件清理提示，下次启动会再清）",
            report.warnings.len()
        ));
    }
    line
}

/// 安装结果 → （kind, 文案）—— 纯函数，界面拿到的就是最终两样东西 ✓。
pub fn format_install_result(result: &Result<InstalledUpdate, String>) -> (String, String) {
    match result {
        Ok(report) => ("ok".to_string(), install_success_line(report)),
        Err(error) => ("danger".to_string(), clamp_lines(error, 2)),
    }
}

/// 进程内单调递增序号：让「下载并安装」的临时目录名在同一纳秒内也不会重复 ✓。
///
/// 为什么不能只靠 pid + 纳秒：macOS 上 `SystemTime::now()` 的纳秒值**可能在同一 tick 内
/// 被两个线程读到同一个**（preview.5 的 macOS CI 就是这样撞车：两个并列用例共用一个
/// staging 目录，先完成的 `remove_dir_all` 把另一个的 `.part` 删掉 → rename ENOENT）。
/// 序号用 `std::sync::atomic`（零新依赖 ✓）。
static STAGING_SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

fn next_staging_seq() -> u64 {
    STAGING_SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
}

/// **纯函数**：GUI「下载并安装」的临时目录名（pid + 纳秒 + 序号，任何一项不同都不同 ✓）。
fn staging_dir_name(pid: u32, nonce: u128, seq: u64) -> String {
    format!("sfl-gui-update-{}-{}-{}", pid, nonce, seq)
}

/// 「下载并安装」的**可注入**入口：单测传假 HTTP（同时实现 GET 与下载）→ **完全离线** ✓。
///
/// 流程全部复用 core（R7：界面不做判断 ✓）：
///   ① update::check_for_update（挑本机资产）；
///   ② update::download_and_verify（SHA256 fail-closed ✅）→ 落到**系统临时目录**
///      （**绝不写用户数据目录** ✓）；
///   ③ update_apply::install_zip（zipread → 备份 → rename 舞步 → 清理旧文件）。
pub fn install_update_with<H: HttpGet + HttpDownload>(
    http: &H,
    installed_exe: &std::path::Path,
) -> Result<InstalledUpdate, String> {
    let current = drcom_core::app_version();
    let channel = update::channel_from_config(&drcom_core::legacy::load_config_or_default().config);
    let check = update::check_for_update(current.clone(), channel, http);
    let (release, asset) = match check {
        UpdateCheck::Available { release, asset } => (release, asset),
        UpdateCheck::UpToDate => return Err("已经是最新版本，不需要安装".to_string()),
        other => return Err(other.summary_cn()),
    };
    if !release.version.is_newer_than(&current) {
        return Err(format!(
            "安装包里是 {}，不比当前版本 {} 新 —— 不安装",
            release.version,
            current
        ));
    }
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos())
        .unwrap_or(0);
    // 审计收尾（preview.5 补丁）：临时目录名除 pid + 纳秒外**再加进程内单调序号** ——
    // macOS 上 `SystemTime::now()` 在同一 tick 内可能被两个线程读到同一个值（真机 CI 踩过：
    // 两个并列用例撞同一个 staging 目录，先完成的那个 `remove_dir_all` 把另一个的 `.part`
    // 删掉 → rename ENOENT），加序号后**并列调用也一定互不干扰** ✓。
    let staging_dir = std::env::temp_dir().join(staging_dir_name(
        std::process::id(),
        nonce,
        next_staging_seq(),
    ));
    let dest = staging_dir.join(&asset.name);
    let result = (|| {
        update::download_and_verify(http, &asset, &dest)?;
        let metadata = std::fs::metadata(&dest)
            .map_err(|error| format!("读下载文件失败：{}", error))?;
        if metadata.len() > update_apply::MAX_PACKAGE_BYTES {
            return Err(format!(
                "下载的安装包太大（{} 字节，上限 {}）—— 不像是本项目的安装包",
                metadata.len(),
                update_apply::MAX_PACKAGE_BYTES
            ));
        }
        let zip_bytes =
            std::fs::read(&dest).map_err(|error| format!("读下载文件失败：{}", error))?;
        let install_dir = installed_exe
            .parent()
            .ok_or_else(|| "本程序没有所在目录 —— 无法判断安装目录".to_string())?;
        let backup_root = install_dir.join(update_apply::BACKUP_DIR_NAME);
        let outcome = update_apply::install_zip_into_dir(
            install_dir,
            &zip_bytes,
            &backup_root,
            &current.to_string_full(),
            Some(&release.version.to_string_full()),
            false,
        )?;
        // 重启目标是 **GUI 优先**（装完之后要拉起的是界面程序 ✓）
        let new_exe = outcome
            .restart_target()
            .ok_or_else(|| "内部错误：没有可重启的程序".to_string())?;
        Ok(InstalledUpdate {
            version: release.version.to_string_full(),
            new_exe,
            warnings: outcome.warnings.clone(),
        })
    })();
    let _ = std::fs::remove_file(&dest);
    let _ = std::fs::remove_dir_all(&staging_dir);
    result
}

/// 真正联网的「下载并安装」：**只被 worker 的后台线程调用** ✓。
///
/// 更新的是**当前正在运行的这个程序**（GUI 自己）✓ —— 换完之后主线程负责重启 ✓。
pub fn install_update() -> Result<InstalledUpdate, String> {
    let installed = std::env::current_exe()
        .map_err(|error| format!("拿不到本程序路径：{}", error))?;
    install_update_with(&CurlHttp, &installed)
}

// ── M4 收尾：「版本回滚」入口（列出备份 → 确认 → 恢复全部程序 ✓）──────────────

/// 「版本回滚」列表里的一行 ✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RollbackEntry {
    pub version: String,
    pub label: String,
}

/// 「版本回滚」区块要显示的东西 ✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RollbackView {
    pub available: bool,
    pub line: String,
    pub entries: Vec<RollbackEntry>,
}

/// 安装目录 = 本程序所在目录 ✓（便携版口径，与 CLI 一致 ✓）。
fn current_install_dir() -> Result<std::path::PathBuf, String> {
    let exe = std::env::current_exe().map_err(|error| format!("拿不到本程序路径：{}", error))?;
    exe.parent()
        .map(std::path::Path::to_path_buf)
        .ok_or_else(|| "本程序没有所在目录 —— 无法判断安装目录".to_string())
}

/// 程序文件 → 界面上的短名 ✓（纯函数，好测 ✓）。
pub fn program_label(name: &str) -> String {
    if name == update_apply::expected_exe_name() {
        "界面".to_string()
    } else if name == update_apply::cli_exe_name() {
        "命令行".to_string()
    } else {
        name.to_string()
    }
}

/// **纯函数**：备份列表 → 界面要显示的行与选项 ✓（只读，单测离线可跑 ✓）。
pub fn rollback_view_of(backup_root: &std::path::Path) -> RollbackView {
    let versions = update_apply::backup_versions(backup_root);
    if versions.is_empty() {
        return RollbackView {
            available: false,
            line: "还没有可回滚的版本（更新过一次之后会留备份）".to_string(),
            entries: Vec::new(),
        };
    }
    let total = versions.len();
    let entries: Vec<RollbackEntry> = versions
        .into_iter()
        .take(3)
        .map(|(version, files)| {
            let names: Vec<String> = files.iter().map(|(name, _)| program_label(name)).collect();
            RollbackEntry {
                version: version.clone(),
                label: format!("{}（{}）", version, names.join(" + ")),
            }
        })
        .collect();
    let line = if total > entries.len() {
        format!(
            "可回滚 {} 个版本（只列最新 {} 个；备份在安装目录的 backup/ 里 ✓）",
            total,
            entries.len()
        )
    } else {
        format!("可回滚 {} 个版本（备份在安装目录的 backup/ 里 ✓）", total)
    };
    RollbackView {
        available: true,
        line,
        entries,
    }
}

/// 界面读一次回滚状态 ✓（安装目录取当前程序所在目录 ✓）。
pub fn rollback_view() -> RollbackView {
    match current_install_dir() {
        Ok(dir) => rollback_view_of(&dir.join(update_apply::BACKUP_DIR_NAME)),
        Err(error) => RollbackView {
            available: false,
            line: error,
            entries: Vec::new(),
        },
    }
}

/// 回滚完成的结果：给界面一行文案 + 要不要重启（重启哪个程序）✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RollbackDone {
    pub line: String,
    pub restart: Option<std::path::PathBuf>,
}

/// **可注入安装目录**的回滚入口（单测用临时目录 → 完全离线 ✓）。
///
/// 流程全部复用 core（R7：界面不做判断 ✓）：plan_rollback_all → rollback_programs。
pub fn rollback_to_in(dir: &std::path::Path, version: &str) -> Result<RollbackDone, String> {
    let backup_root = dir.join(update_apply::BACKUP_DIR_NAME);
    let plan = update_apply::plan_rollback_all(dir, &backup_root, Some(version))?;
    let outcome = update_apply::rollback_programs(&plan, false)?;
    // 被换下来的新程序清一次（删不掉不是错误 ✓）
    for exe in &outcome.new_exes {
        if let Some(parent) = exe.parent() {
            let name = exe
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default();
            for warning in update_apply::cleanup_stale_files(parent, &name) {
                eprintln!("[回滚] {}", warning);
            }
        }
    }
    let names: Vec<String> = outcome
        .new_exes
        .iter()
        .map(|exe| {
            exe.file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default()
        })
        .collect();
    let mut line = format!(
        "已回滚到 {}（{} 个程序：{}）—— 正在重启…",
        outcome.version,
        names.len(),
        names.join(" + ")
    );
    if !plan.notes.is_empty() {
        line.push_str(&format!("；{} 条说明（见终端日志）", plan.notes.len()));
    }
    Ok(RollbackDone {
        line,
        restart: outcome.restart_target(),
    })
}

/// 真正的回滚入口：**只被 worker 的后台线程调用** ✓（会写安装目录 ✓）。
pub fn rollback_to(version: &str) -> Result<RollbackDone, String> {
    let dir = current_install_dir()?;
    rollback_to_in(&dir, version)
}


/// 「无人登录也自动登录」这一项要显示的数据 ✓
/// （语义 = **注册成系统服务** ✓：用户级自启要等有人登录才跑 ✗，只有服务是开机即起 ✓）
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceView {
    /// 是否已安装（驱动开关 ✓）
    pub installed: bool,
    /// 一行状态 ✓
    pub line: String,
    /// 代价与前提（要管理员 / 要 nssm / 与 2.x 同名 ✓）
    pub hint: String,
}

/// 纯函数：服务状态 → 一行中文 ✓（好测 ✓，不碰系统 ✓）。
pub fn service_line(state: &drcom_core::service::ServiceState) -> String {
    match state {
        drcom_core::service::ServiceState::Running => "已注册，正在运行 ✓".to_string(),
        drcom_core::service::ServiceState::Stopped => "已注册，但没在跑".to_string(),
        drcom_core::service::ServiceState::NotInstalled => "未注册（只在有人登录后运行）".to_string(),
        drcom_core::service::ServiceState::Unknown(reason) => {
            format!("查不出来：{}", reason)
        }
    }
}

/// 「与 2.x 同名服务」的提醒要不要显示 ✓（**纯函数**：三平台参数化单测 —— R2 / 审计 P2-10）。
///
/// 只有 Windows 且**已安装**时才显示：同名是 Windows 上 NSSM 与 2.x 共用服务名造成的，
/// Linux / macOS 用的是各自的用户级单元文件，不存在这个问题 ✓。
pub fn service_shared_name_note(os: platform::Os, installed: bool) -> Option<&'static str> {
    if installed && matches!(os, platform::Os::Windows) {
        Some("⚠ 服务名与 2.x 共用（DrcomAutoLogin）✓ —— 已存在时命令会给「接管」步骤，不会硬装 ✗")
    } else {
        None
    }
}

/// 读一次（只读 ✓；点「重新读取」也走它 ✓）。
pub fn service_view() -> ServiceView {
    let state = drcom_core::service::status();
    let installed = !matches!(state, drcom_core::service::ServiceState::NotInstalled);
    let mut hint = format!(
        "开启后：由 {} 托管，**开机即起、无人登录也在检查** ✓；\
         代价是注册时需要管理员权限，Windows 上还需要程序目录里有 nssm.exe ✓",
        drcom_core::service::flavor_name()
    );
    if !drcom_core::service::manager_available() {
        hint.push_str(&format!(
            "\n⚠ 现在找不到 {} ✗ —— 注册会失败，可以先用 `autostart on`（登录后自动跑 ✓）",
            drcom_core::service::manager_program()
        ));
    }
    if let Some(note) = service_shared_name_note(platform::Os::current(), installed) {
        hint.push('\n');
        hint.push_str(note);
    }
    ServiceView {
        installed,
        line: service_line(&state),
        hint,
    }
}

/// 开关动作 ✓：装 = 注册并启动 ✓；关 = 停止并注销 ✓。
pub fn service_set(enabled: bool) -> (String, String) {
    let exe = std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("stardust-flash-link"));
    let result = if enabled {
        drcom_core::service::install(&exe, &["run"])
    } else {
        drcom_core::service::uninstall()
    };
    match result {
        Ok(message) => (
            "ok".to_string(),
            format!(
                "{}\n（{} 现在会 {} 自己检查 ✓）",
                message,
                drcom_core::APP_NAME,
                if enabled { "交给服务" } else { "由界面自己" }
            ),
        ),
        Err(e) => ("danger".to_string(), e),
    }
}

/// 设置窗口收集到的原始输入（全是界面上的字符串/开关 ✓，解析与校验在下面 ✓）。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct FormInput {
    pub host: String,
    pub port_text: String,
    pub account: String,
    pub suffix: String,
    pub interval_text: String,
    pub wait_text: String,
    pub guard_enabled: bool,
    pub guard_ssids: String,
    pub guard_subnets: String,
    pub auto_update: bool,
    pub password: String,
}

impl FormInput {
    /// 从现有配置生成默认输入（**密码留空** ✓）
    pub fn from_config(cfg: &Config) -> FormInput {
        FormInput {
            host: cfg.host.clone(),
            port_text: cfg.port.to_string(),
            account: cfg.account.clone(),
            suffix: cfg.suffix.clone(),
            interval_text: cfg.auto_check_interval_min.to_string(),
            wait_text: cfg.network_wait_timeout_sec.to_string(),
            guard_enabled: cfg.network_guard_enabled,
            guard_ssids: cfg.guard_allowed_ssids.clone(),
            guard_subnets: cfg.guard_allowed_subnets.clone(),
            auto_update: cfg.auto_update_enabled,
            password: String::new(),
        }
    }

    /// 变成可保存的表单：数字字段解析失败就**回落到原配置的值**（不静默改成 0 ✗）
    pub fn to_form(&self, base: &Config) -> SettingsForm {
        let mut form = SettingsForm::from_config(base);
        form.host = self.host.clone();
        form.port = self.port_text.trim().parse::<u16>().unwrap_or(base.port);
        form.account = self.account.clone();
        form.suffix = self.suffix.clone();
        form.interval_min = self
            .interval_text
            .trim()
            .parse::<u32>()
            .unwrap_or(base.auto_check_interval_min);
        form.wait_timeout_sec = self
            .wait_text
            .trim()
            .parse::<u32>()
            .unwrap_or(base.network_wait_timeout_sec);
        form.guard_enabled = self.guard_enabled;
        form.guard_ssids = self.guard_ssids.clone();
        form.guard_subnets = self.guard_subnets.clone();
        form.auto_update_enabled = self.auto_update;
        form.password = Some(self.password.clone()); // 空串 = 不修改 ✓（由 SettingsForm 判定 ✓）
        form
    }
}

/// 设置窗口要显示的一屏数据。
#[derive(Debug, Clone, PartialEq)]
pub struct SettingsView {
    pub input: FormInput,
    pub interval_choices: Vec<String>,
    pub channel_label: String,
    pub password_hint: String,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}

/// 「测试连接」：用**表单里的值**跑一次检查（**不保存** ✓）。
pub fn settings_test(input: &FormInput) -> (String, String) {
    // 基线走 legacy 兼容层：3.0 还没配置、2.x 有时，表单里显示 2.x 的值（只读，不改 2.x ✓）
    let base = drcom_core::legacy::load_config_or_default().config;
    let form = input.to_form(&base);
    let errors = form.validate(&base);
    if !errors.is_empty() {
        return (
            "danger".to_string(),
            format!("配置有问题（已阻止保存）：\n  - {}", errors.join("\n  - ")),
        );
    }
    let cfg = form.to_config(&base);
    let typed_password = form.new_password().map(|v| v.to_string());
    let password = match &typed_password {
        Some(value) => value.clone(),
        None => password::read(),
    };
    if !cfg.account_configured() {
        return ("warn".to_string(), "账号还没填 —— 先填上学号再测 ✓".to_string());
    }
    if password.is_empty() {
        return ("warn".to_string(), "还没有密码：在「密码」里填一个（或先保存）✓".to_string());
    }
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(&cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        return (
            "warn".to_string(),
            format!(
                "网络位置守卫会拦下这次检查：{}\n（测试用的是表单里的白名单，**尚未保存** ✓）",
                verdict.reason
            ),
        );
    }
    let report = session::check_once(&cfg, &password, &PlainHttp, TIMEOUT);
    let kind = if report.outcome.is_ok() { "ok" } else { "danger" };
    (
        kind.to_string(),
        format!("{}（测试用的是表单里的值，**尚未保存** ✓）", report.outcome.summary_cn()),
    )
}

/// 读配置 → 设置窗口的显示数据 ✓（只读 ✓）
pub fn settings_view() -> SettingsView {
    let cfg = drcom_core::legacy::load_config_or_default().config;
    let input = FormInput::from_config(&cfg);
    let form = input.to_form(&cfg);
    let has_password = drcom_core::password::is_set();
    SettingsView {
        interval_choices: drcom_core::settings::interval_choices()
            .iter()
            .map(|v| v.to_string())
            .collect(),
        channel_label: format!(
            "{} · {}",
            cfg.update_channel.as_str(),
            cfg.update_channel.label_cn()
        ),
        password_hint: if has_password {
            "已设置（留空 = 不修改）".to_string()
        } else {
            "还没设置（填一个并保存即可）".to_string()
        },
        errors: form.validate(&cfg),
        warnings: form.warnings(&cfg),
        input,
    }
}

/// 保存设置（界面点「保存」）→（颜色, 文案）✓
pub fn settings_save(input: &FormInput) -> (String, String) {
    let config_path = platform::config_path();
    // 基线走 legacy 兼容层：3.0 还没配置、2.x 有时，表单里显示 2.x 的值（只读，不改 2.x ✓）
    let base = drcom_core::legacy::load_config_or_default().config;
    let form = input.to_form(&base);
    match form.save_to(&base, &config_path, &drcom_core::password::path()) {
        Ok(()) => (
            "ok".to_string(),
            "已保存 ✓（守护进程与界面都会立刻用新配置；改密码不需要重启服务）".to_string(),
        ),
        Err(e) => ("danger".to_string(), format!("保存失败：{}", e)),
    }
}
/// 方案列表里的一行（给界面用 ✓）。
#[derive(Debug, Clone, PartialEq)]
pub struct ProfileRow {
    pub name: String,
    /// 一行说明（后缀 + 匹配几个 Wi-Fi ✓）
    pub detail: String,
    /// 是不是当前正在用的方案 ✓
    pub active: bool,
}

/// 方案页要显示的一屏数据 ✓。
#[derive(Debug, Clone, PartialEq)]
pub struct ProfilesView {
    pub auto_switch: bool,
    pub active: String,
    pub rows: Vec<ProfileRow>,
    /// 一句话说清现在是什么状态 / 该干什么 ✓
    pub summary: String,
}

/// 读配置 → 方案页数据 ✓（只读 ✓）
pub fn profiles_view() -> ProfilesView {
    let cfg = drcom_core::legacy::load_config_or_default().config;
    profiles_view_of(&cfg)
}

/// 从给定配置生成方案页数据（**纯函数** → 单测能覆盖 ✓）。
pub fn profiles_view_of(cfg: &Config) -> ProfilesView {
    let active = drcom_core::profiles::active(cfg).unwrap_or("").to_string();
    let rows: Vec<ProfileRow> = drcom_core::profiles::list(cfg)
        .into_iter()
        .map(|name| ProfileRow {
            detail: drcom_core::profiles::describe(cfg, &name),
            active: name == active,
            name,
        })
        .collect();
    let summary = if rows.is_empty() {
        "还没有方案 —— 把上面改成某个位置的设置（校内公共场合留空后缀、宿舍填 @yd），\
         再起个名字存下来 ✓"
            .to_string()
    } else if cfg.profiles_auto_switch {
        format!("共 {} 个方案 · 自动切换已开：走到命中 Wi-Fi 就自动切 ✓", rows.len())
    } else {
        format!("共 {} 个方案 · 自动切换关着（只能手动点「应用」✓）", rows.len())
    };
    ProfilesView {
        auto_switch: cfg.profiles_auto_switch,
        active,
        rows,
        summary,
    }
}

/// 方案名校验（纯函数 ✓）→ `Err` 是给用户看的一句中文 ✓
fn profile_name_ready(name: &str) -> Result<String, String> {
    let normalized = drcom_core::profiles::normalize_name(name);
    if normalized.is_empty() {
        return Err(format!(
            "方案名不合法 ✗（不能为空、不超过 {} 字，且不能含路径字符）",
            drcom_core::profiles::MAX_NAME_LEN
        ));
    }
    Ok(normalized)
}

/// 把**当前表单里的值**存成一个新方案（名字 + 匹配 Wi-Fi 名来自界面 ✓）。
pub fn profile_save(input: &FormInput, name: &str, match_ssids: &str) -> (String, String) {
    let config_path = platform::config_path();
    // 基线走 legacy 兼容层：3.0 还没配置、2.x 有时，表单里显示 2.x 的值（只读，不改 2.x ✓）
    let base = drcom_core::legacy::load_config_or_default().config;
    let normalized = match profile_name_ready(name) {
        Ok(value) => value,
        Err(e) => return ("danger".to_string(), e),
    };
    // 表单必须先过关：半套配置绝不落盘 ✗
    let form = input.to_form(&base);
    let errors = form.validate(&base);
    if !errors.is_empty() {
        return (
            "danger".to_string(),
            format!("先把上面的配置改对再存方案：\n  - {}", errors.join("\n  - ")),
        );
    }
    let mut cfg = form.to_config(&base);
    let ssids: Vec<String> = match_ssids
        .split(',')
        .map(|part| part.trim().to_string())
        .filter(|part| !part.is_empty())
        .collect();
    match drcom_core::profiles::save_profile(&mut cfg, &normalized, &ssids) {
        Ok(msg) => match cfg.save(&config_path) {
            Ok(()) => (
                "ok".to_string(),
                format!("{}（值取的就是当前表单里的设置 ✓）", msg),
            ),
            Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
        },
        Err(e) => ("danger".to_string(), e),
    }
}

/// 应用（切到）某个方案 ✓
pub fn profile_activate(name: &str) -> (String, String) {
    let config_path = platform::config_path();
    let mut cfg = match drcom_core::legacy::load_config() {
        Ok(read) => read.config,
        Err(e) => return ("danger".to_string(), e),
    };
    match drcom_core::profiles::activate(&mut cfg, name) {
        Ok(()) => match cfg.save(&config_path) {
            Ok(()) => (
                "ok".to_string(),
                format!(
                    "已切到方案「{}」：后缀 {} ✓（表单已同步刷新 ✓）",
                    name,
                    drcom_core::profiles::suffix_label(&cfg.suffix)
                ),
            ),
            Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
        },
        Err(e) => ("danger".to_string(), e),
    }
}

/// 删除某个方案 ✓（删「当前方案」只清标记，**配置值不动** ✓）
pub fn profile_delete(name: &str) -> (String, String) {
    let config_path = platform::config_path();
    let mut cfg = match drcom_core::legacy::load_config() {
        Ok(read) => read.config,
        Err(e) => return ("danger".to_string(), e),
    };
    match drcom_core::profiles::delete(&mut cfg, name) {
        Ok(()) => match cfg.save(&config_path) {
            Ok(()) => ("ok".to_string(), format!("已删除方案「{}」✓", name)),
            Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
        },
        Err(e) => ("danger".to_string(), e),
    }
}

/// 打开 / 关闭「按 Wi-Fi 自动切换」✓
pub fn profile_set_auto(enabled: bool) -> (String, String) {
    let config_path = platform::config_path();
    let mut cfg = match drcom_core::legacy::load_config() {
        Ok(read) => read.config,
        Err(e) => return ("danger".to_string(), e),
    };
    cfg.profiles_auto_switch = enabled;
    match cfg.save(&config_path) {
        Ok(()) => (
            "ok".to_string(),
            if enabled {
                "自动切换已开启 ✓：走进命中方案的 Wi-Fi 就自动切（读不到 Wi-Fi 名时保持不动 ✓）"
                    .to_string()
            } else {
                "自动切换已关闭 ✓：方案只在点「应用」时生效".to_string()
            },
        ),
        Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
    }
}

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
    fn config_line_shows_the_legacy_source_instead_of_pretending_it_is_ours() {
        use drcom_core::LoadedFrom;
        // 2.x 只读兼容：必须显示来源，不许说「未初始化 / 正常」✗（审计 P1-3）
        assert_eq!(
            config_line_of(LoadedFrom::Legacy, false, 0),
            "来自 2.x 安装目录（只读兼容）"
        );
        let with_errors = config_line_of(LoadedFrom::Legacy, false, 2);
        assert!(with_errors.contains("2.x"), "{}", with_errors);
        assert!(with_errors.contains("2 个问题"), "{}", with_errors);
        assert!(!config_line_of(LoadedFrom::Legacy, false, 0).contains("未初始化"));
        // 3.0 自己的配置：原有口径不变 ✓
        assert_eq!(config_line_of(LoadedFrom::Own, true, 0), "正常");
        assert_eq!(config_line_of(LoadedFrom::Own, false, 0), "未初始化");
        assert_eq!(config_line_of(LoadedFrom::Own, true, 3), "3 个问题");
    }

    #[test]
    fn account_is_masked_on_screen() {
        // 界面上永远看不到完整账号 ✓
        assert_eq!(secret::MaskedAccount("2023001234").to_string(), "2023******");
    }

    #[test]
    fn account_display_shows_the_suffix_that_will_be_used() {
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        cfg.suffix = "@yd".to_string();
        assert_eq!(account_display(&cfg), "2023******@yd", "宿舍移动：带尾缀 ✓");
        cfg.suffix = String::new();
        assert_eq!(account_display(&cfg), "2023******（校内直连）", "校内公共场合：无尾缀 ✓");
        cfg.account = String::new();
        assert_eq!(account_display(&cfg), "未设置");
        // 任何情况下都不该出现完整账号 ✗
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        assert!(!account_display(&cfg).contains("2023001234"));
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
    fn form_input_round_trips_and_falls_back_on_bad_numbers() {
        let mut cfg = Config::default();
        cfg.host = "172.16.80.3".to_string();
        cfg.account = "2023001234".to_string();
        cfg.suffix = "@yd".to_string();
        let input = FormInput::from_config(&cfg);
        assert_eq!(input.password, "", "密码默认空 = 不修改 ✓");
        let form = input.to_form(&cfg);
        assert_eq!(form.host, cfg.host);
        assert_eq!(form.interval_min, cfg.auto_check_interval_min);
        assert!(form.new_password().is_none(), "空密码 = 不修改 ✓");

        // 数字框乱填 → **回落到原值**（不能静默变 0 ✗）
        let mut bad = input.clone();
        bad.port_text = "abc".to_string();
        bad.interval_text = String::new();
        bad.wait_text = "-5".to_string();
        let form = bad.to_form(&cfg);
        assert_eq!(form.port, cfg.port, "解析失败要保留原值 ✓");
        assert_eq!(form.interval_min, cfg.auto_check_interval_min);
        assert_eq!(form.wait_timeout_sec, cfg.network_wait_timeout_sec);

        // 给了密码 → 会被写 ✓
        let mut with_password = input.clone();
        with_password.password = "pw".to_string();
        assert_eq!(with_password.to_form(&cfg).new_password(), Some("pw"));
    }

    #[test]
    fn platform_commands_are_pure_and_parameterized() {
        use drcom_core::platform::Os;
        // 审计 P2-10：cfg!(target_os) 从界面业务代码里收走 → 三平台在本机都能断言 ✓
        assert_eq!(open_dir_command(Os::Windows, r"C:\data\sfl").0, "explorer");
        assert_eq!(open_dir_command(Os::MacOs, "/tmp/data").0, "open");
        assert_eq!(open_dir_command(Os::Linux, "/tmp/data").0, "xdg-open");
        for os in [Os::Windows, Os::MacOs, Os::Linux] {
            let (_, args) = open_dir_command(os, "/some/data");
            assert_eq!(args, vec!["/some/data".to_string()], "{:?}", os);
        }
        // 服务同名提示：只有「Windows + 已安装」才出现 ✓
        assert!(service_shared_name_note(Os::Windows, true).unwrap().contains("2.x"));
        assert!(service_shared_name_note(Os::Windows, false).is_none());
        assert!(service_shared_name_note(Os::Linux, true).is_none());
        assert!(service_shared_name_note(Os::MacOs, true).is_none());
    }

    #[test]
    fn service_lines_are_readable_and_never_lie() {
        use drcom_core::service::ServiceState;
        assert!(service_line(&ServiceState::Running).contains("正在运行"));
        assert!(service_line(&ServiceState::Stopped).contains("没在跑"));
        assert!(service_line(&ServiceState::NotInstalled).contains("未注册"));
        assert!(service_line(&ServiceState::Unknown("探测失败".into())).contains("探测失败"));
        // 真机只读取一次：字段非空，且**代价必须说清**（要管理员 ✓）
        let view = service_view();
        assert!(!view.line.is_empty() && !view.hint.is_empty());
        assert!(view.hint.contains("管理员"), "代价要讲明白 ✓: {}", view.hint);
        assert!(view.hint.contains("无人登录") || view.hint.contains("开机即起"), "{}", view.hint);
    }

    #[test]
    fn settings_view_has_choices_and_readable_hints() {
        let view = settings_view();
        assert!(!view.interval_choices.is_empty());
        assert!(view.interval_choices.contains(&"30".to_string()), "{:?}", view.interval_choices);
        assert!(!view.channel_label.is_empty());
        assert!(view.password_hint.contains("密码") || view.password_hint.contains("设置"));
        assert!(view.errors.len() < 5, "默认配置不该一堆错：{:?}", view.errors);
    }

    #[test]
    fn settings_test_refuses_incomplete_input_without_touching_network() {
        // 账号为空 → 直接提示（**不会发任何请求** ✓）
        let mut input = FormInput::default();
        input.host = "172.16.80.3".to_string();
        input.interval_text = "30".to_string();
        input.wait_text = "60".to_string();
        let (kind, text) = settings_test(&input);
        assert_eq!(kind, "warn", "{}", text);
        assert!(text.contains("账号"), "{}", text);

        // 非法后缀 → danger，且说明「已阻止保存」✓
        input.account = "2023001234".to_string();
        input.suffix = "@xx".to_string();
        let (kind, text) = settings_test(&input);
        assert_eq!(kind, "danger");
        assert!(text.contains("配置有问题"), "{}", text);
    }

    #[test]
    fn profiles_view_is_pure_and_readable() {
        let mut cfg = Config::default();
        let view = profiles_view_of(&cfg);
        assert!(view.rows.is_empty());
        assert!(view.summary.contains("还没有方案"), "{}", view.summary);

        cfg.suffix = "@yd".to_string();
        drcom_core::profiles::save_profile(&mut cfg, "宿舍", &["Dorm-WiFi".to_string()]).unwrap();
        let view = profiles_view_of(&cfg);
        assert_eq!(view.rows.len(), 1);
        assert_eq!(view.rows[0].name, "宿舍");
        assert!(view.rows[0].detail.contains("@yd"), "{}", view.rows[0].detail);
        assert!(!view.rows[0].active, "刚存下的方案不会自动成为「使用中」✓");
        assert!(view.summary.contains("自动切换关着"), "{}", view.summary);

        drcom_core::profiles::activate(&mut cfg, "宿舍").unwrap();
        let view = profiles_view_of(&cfg);
        assert_eq!(view.active, "宿舍");
        assert!(view.rows[0].active, "切过去之后要标「使用中」✓");
        assert!(view.summary.contains("共 1 个方案"), "{}", view.summary);
    }

    #[test]
    fn profile_name_validation_rejects_junk() {
        assert_eq!(profile_name_ready("  宿舍  ").unwrap(), "宿舍", "首尾空白要压掉 ✓");
        assert!(profile_name_ready("").is_err(), "空名字不行 ✗");
        assert!(profile_name_ready("   ").is_err());
        assert!(profile_name_ready("a/b").is_err(), "路径字符会搞坏配置 ✗");
        assert!(profile_name_ready("长".repeat(25).as_str()).is_err(), "超长要拦 ✗");
        assert!(profile_name_ready("长".repeat(24).as_str()).is_ok());
    }

    #[test]
    fn profile_save_refuses_junk_without_touching_disk() {
        // 后缀非法 → 直接拒绝（**不会走到写盘** ✓）
        let mut input = FormInput::default();
        input.host = "172.16.80.3".to_string();
        input.port_text = "80".to_string();
        input.interval_text = "30".to_string();
        input.wait_text = "60".to_string();
        input.suffix = "@xx".to_string();
        let (kind, text) = profile_save(&input, "坏方案", "");
        assert_eq!(kind, "danger");
        assert!(text.contains("先把上面的配置改对"), "{}", text);

        // 名字不合法 → 更早拒绝 ✓
        input.suffix = "@yd".to_string();
        let (kind, text) = profile_save(&input, "  ", "");
        assert_eq!(kind, "danger");
        assert!(text.contains("方案名"), "{}", text);
    }

    // ── M4：「检查更新」入口的纯函数单测 —— **全部离线**（假 HTTP 客户端，不联网 ✓）──

    use drcom_core::net::HttpReply;
    use drcom_core::update::{AssetInfo, ReleaseInfo};
    use std::cell::RefCell;

    /// 假 HTTP 客户端：把 GitHub 的各种响应喂进 `update_check_with`，**不联网** ✓。
    struct FakeUpdateHttp {
        status: u16,
        body: String,
        /// 「下载」时写进目标文件的字节（= 假的安装包 ZIP ✓）。
        data: Vec<u8>,
        error: Option<String>,
        seen_urls: RefCell<Vec<String>>,
        seen_headers: RefCell<Vec<(String, String)>>,
    }

    impl FakeUpdateHttp {
        fn ok(body: &str) -> FakeUpdateHttp {
            FakeUpdateHttp {
                status: 200,
                body: body.to_string(),
                data: Vec::new(),
                error: None,
                seen_urls: RefCell::new(Vec::new()),
                seen_headers: RefCell::new(Vec::new()),
            }
        }

        fn with_download(body: &str, data: &[u8]) -> FakeUpdateHttp {
            let mut fake = FakeUpdateHttp::ok(body);
            fake.data = data.to_vec();
            fake
        }

        fn status(status: u16, body: &str) -> FakeUpdateHttp {
            FakeUpdateHttp { status, ..FakeUpdateHttp::ok(body) }
        }

        fn failing(message: &str) -> FakeUpdateHttp {
            FakeUpdateHttp { error: Some(message.to_string()), ..FakeUpdateHttp::ok("") }
        }
    }

    impl HttpGet for FakeUpdateHttp {
        fn get(&self, url: &str, _timeout: Duration) -> Result<HttpReply, String> {
            self.seen_urls.borrow_mut().push(url.to_string());
            match &self.error {
                Some(error) => Err(error.clone()),
                None => Ok(HttpReply { status: self.status, location: None, body: self.body.clone() }),
            }
        }

        fn get_with_headers(
            &self,
            url: &str,
            headers: &[(&str, &str)],
            timeout: Duration,
        ) -> Result<HttpReply, String> {
            for (name, value) in headers {
                self.seen_headers.borrow_mut().push((name.to_string(), value.to_string()));
            }
            self.get(url, timeout)
        }
    }

    impl HttpDownload for FakeUpdateHttp {
        fn download(
            &self,
            _url: &str,
            dest: &std::path::Path,
            max_bytes: u64,
            _timeout: Duration,
        ) -> Result<u64, String> {
            if self.data.len() as u64 > max_bytes {
                return Err(format!("下载体积超过上限（{} 字节）", max_bytes));
            }
            if let Some(parent) = dest.parent() {
                std::fs::create_dir_all(parent)
                    .map_err(|error| format!("建下载目录失败：{}", error))?;
            }
            std::fs::write(dest, &self.data).map_err(|error| error.to_string())?;
            Ok(self.data.len() as u64)
        }
    }

    /// 造一条「比当前新」的 release：资产名按 CI 约定拼，本机平台一定能挑中 ✓。
    fn fresh_release_json() -> (String, String) {
        let asset_name =
            format!("stardust-flash-link-9.9.9.9-{}.zip", drcom_core::platform::target_label());
        let body = format!(
            "[{{\"tag_name\":\"v9.9.9.9\",\"prerelease\":false,\"draft\":false,\"published_at\":\"2026-11-17T00:00:00Z\",\"assets\":[{{\"name\":\"{asset_name}\",\"browser_download_url\":\"https://github.com/TSS-Small-sunshine/StardustFlashLink/releases/download/v9.9.9.9/{asset_name}\",\"digest\":\"{digest}\",\"size\":12345678}}]}}]",
            digest = "0".repeat(64)
        );
        (body, asset_name)
    }

    #[test]
    fn update_result_text_shows_up_to_date_with_version_and_channel() {
        let current = Version::parse("3.0.0.0-preview.3").unwrap();
        let (kind, text) = format_update_check(&UpdateCheck::UpToDate, &current);
        assert_eq!(kind, "ok", "{}", text);
        assert!(text.contains("3.0.0.0-preview.3"), "要显示当前版本: {}", text);
        assert!(text.contains("预览版"), "要显示通道: {}", text);
        assert!(text.contains("已是最新"), "{}", text);
    }

    #[test]
    fn update_result_text_shows_new_version_asset_and_size() {
        let current = Version::parse("3.0.0.0-preview.3").unwrap();
        let asset = AssetInfo {
            name: "stardust-flash-link-3.0.0.4-windows-x86_64.zip".to_string(),
            url: "https://github.com/TSS-Small-sunshine/StardustFlashLink/releases/download/v3/asset.zip".to_string(),
            size: 12_345_678,
            sha256: Some("0".repeat(64)),
        };
        let release = ReleaseInfo {
            version: Version::parse("3.0.0.4").unwrap(),
            tag: "v3.0.0.4".to_string(),
            prerelease: false,
            published_at: "2026-11-17T00:00:00Z".to_string(),
            assets: vec![asset.clone()],
        };
        let (kind, text) = format_update_check(&UpdateCheck::Available { release, asset }, &current);
        assert_eq!(kind, "accent", "{}", text);
        assert!(text.contains("3.0.0.4"), "新版本号: {}", text);
        assert!(text.contains("stardust-flash-link-3.0.0.4-windows-x86_64.zip"), "资产名: {}", text);
        assert!(text.contains("11.8 MB"), "大小: {}", text);
        // M4 第二块起 GUI **有**「下载并安装」按钮（不再是「去命令行下载」的提示 ✓）
        assert!(text.contains("下载并安装"), "要告诉用户点哪: {}", text);
        assert!(text.contains("回滚"), "要告诉用户失败了能回来: {}", text);
        assert_eq!(text.lines().count(), 2, "有更新就两行: {}", text);
    }

    #[test]
    fn update_result_text_keeps_skip_reason_and_error_verbatim() {
        let current = Version::parse("3.0.0.0-preview.3").unwrap();
        let skipped = UpdateCheck::Skipped("当前通道（正式版）上没有可用的发布版本".to_string());
        let (kind, text) = format_update_check(&skipped, &current);
        assert_eq!(kind, "warn");
        assert!(text.contains("跳过更新"), "{}", text);
        assert!(text.contains("当前通道（正式版）上没有可用的发布版本"), "原因要带出来: {}", text);

        // 错误文案由 update.rs 给（已脱敏、可读）→ **原样**带出，不吞不改写 ✓
        let error = "检查更新失败：HTTPS 请求失败：启动 curl 失败（请确认 curl 在 PATH 里）";
        let (kind, text) = format_update_check(&UpdateCheck::Error(error.to_string()), &current);
        assert_eq!(kind, "danger");
        assert_eq!(text, error, "错误要原样带出 ✓");
    }

    #[test]
    fn update_result_text_clamps_long_errors_to_two_lines() {
        let current = Version::parse("3.0.0.0-preview.3").unwrap();
        let error = UpdateCheck::Error("第一行：连不上\n第二行：检查代理\n第三行：重试".to_string());
        let (kind, text) = format_update_check(&error, &current);
        assert_eq!(kind, "danger");
        assert_eq!(text.lines().count(), 2, "错误最多两行: {}", text);
        assert!(text.starts_with("第一行：连不上\n第二行：检查代理"), "{}", text);
        assert!(text.ends_with('…'), "{}", text);
    }

    #[test]
    fn update_check_with_runs_offline_and_never_fakes_an_update() {
        // ① 空列表 ≠ 有更新：应报「这条通道没有可用发布版本」（跳过 ✓），而不是假装有更新 ✗
        let empty = FakeUpdateHttp::ok("[]");
        let check = update_check_with(&empty);
        assert!(matches!(check, UpdateCheck::Skipped(_)), "{:?}", check);
        assert!(
            empty.seen_urls.borrow().iter().any(|url| url == drcom_core::update::RELEASES_URL),
            "必须只请求 GitHub releases 接口"
        );
        assert!(
            empty
                .seen_headers
                .borrow()
                .iter()
                .any(|(name, value)| name == "User-Agent" && value == drcom_core::update::USER_AGENT),
            "GitHub 对无 UA 的请求会 403，UA 必须带上"
        );

        // ② 限流 403 → 可读中文错误（提示「过一会儿再试」），不是 panic、不是空字符串
        let limited = FakeUpdateHttp::status(403, "{\"message\":\"API rate limit exceeded\"}");
        match update_check_with(&limited) {
            UpdateCheck::Error(text) => {
                assert!(text.contains("限流"), "{}", text);
                assert!(text.contains("过一会儿再试"), "{}", text);
            }
            other => panic!("限流应报可读错误: {:?}", other),
        }

        // ③ 传输层失败（curl 不在 / 连不上）→ 原样可读错误
        let broken =
            FakeUpdateHttp::failing("HTTPS 请求失败：启动 curl 失败（请确认 curl 在 PATH 里）");
        match update_check_with(&broken) {
            UpdateCheck::Error(text) => assert!(text.contains("启动 curl 失败"), "{}", text),
            other => panic!("传输失败应报可读错误: {:?}", other),
        }
    }

    #[test]
    fn update_check_with_picks_the_local_asset_for_a_real_release_payload() {
        let (body, asset_name) = fresh_release_json();
        let fake = FakeUpdateHttp::ok(&body);
        let check = update_check_with(&fake);
        match &check {
            UpdateCheck::Available { release, asset } => {
                assert_eq!(release.version.to_string_full(), "9.9.9.9");
                assert_eq!(asset.name, asset_name, "必须挑本机平台的那个资产");
                assert_eq!(asset.size, 12_345_678);
            }
            other => panic!("应发现新版本并挑到本机资产: {:?}", other),
        }
        // 直接交给界面格式化 → 新版本号 / 资产名 / 大小都在 ✓
        let (kind, text) = format_update_check(&check, &drcom_core::app_version());
        assert_eq!(kind, "accent");
        assert!(text.contains("9.9.9.9"), "{}", text);
        assert!(text.contains(&asset_name), "{}", text);
        assert!(text.contains("11.8 MB"), "{}", text);
    }

    #[test]
    fn update_idle_line_is_offline_and_names_version_and_channel() {
        let current = Version::parse("3.0.0.0-preview.3").unwrap();
        let line = update_idle_line_of(&current, Channel::Release);
        assert!(line.contains("3.0.0.0-preview.3"), "{}", line);
        assert!(line.contains("预览版"), "当前版本的通道: {}", line);
        assert!(line.contains("正式版") && line.contains("release"), "配置里的更新通道: {}", line);
        assert!(line.contains("不联网"), "要说清启动不联网: {}", line);
        // 运行时入口也只读版本与配置（不联网）→ 至少要把「更新通道」说出来 ✓
        let runtime = update_idle_line();
        assert!(runtime.contains("更新通道"), "{}", runtime);
    }

    // ── M4 第二块：「下载并安装」的离线端到端单测（假 HTTP + 临时安装目录 ✓）──

    /// 一眼假的「可执行文件」：只保证平台魔数对（**绝不执行** ✓）。
    fn ui_fake_exe(tag: &[u8]) -> Vec<u8> {
        let mut bytes = match drcom_core::platform::Os::current() {
            drcom_core::platform::Os::Windows => b"MZ".to_vec(),
            drcom_core::platform::Os::Linux => vec![0x7f, b'E', b'L', b'F'],
            drcom_core::platform::Os::MacOs => vec![0xCF, 0xFA, 0xED, 0xFE],
        };
        bytes.extend_from_slice(b" ui-demo ");
        bytes.extend_from_slice(tag);
        bytes
    }

    /// 造一个「本项目 archive 打的包」：staging/README.txt + staging/<exe> ✓。
    fn ui_package_zip(exe_name: &str, exe_bytes: &[u8], version: &str) -> Vec<u8> {
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

    /// 造一个同时带 GUI 与 CLI 的「本项目 archive 包」：staging/README.txt + 两个 exe ✓。
    fn ui_multi_package_zip(files: &[(&str, &[u8])], version: &str) -> Vec<u8> {
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

    /// 一条「比当前新」的 release JSON（资产名按 CI 约定拼，本机平台一定能挑中 ✓）。
    fn release_body_with(asset_name: &str, size: u64, digest: &str) -> String {
        format!(
            "[{{\"tag_name\":\"v9.9.9.9\",\"prerelease\":false,\"draft\":false,\"published_at\":\"2026-11-18T00:00:00Z\",\"assets\":[{{\"name\":\"{asset_name}\",\"browser_download_url\":\"https://github.com/x/{asset_name}\",\"digest\":\"sha256:{digest}\",\"size\":{size}}}]}}]"
        )
    }

    fn ui_temp_dir(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("sfl-ui-{}-{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn install_update_downloads_verifies_and_replaces_offline() {
        let dir = ui_temp_dir("install-ok");
        let exe_name = update_apply::expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, ui_fake_exe(b"OLD")).unwrap();
        let new_bytes = ui_fake_exe(b"NEW");
        let zip = ui_package_zip(exe_name, &new_bytes, "9.9.9.9");
        let digest = drcom_core::checksum::sha256_hex(&zip);
        let asset_name =
            format!("stardust-flash-link-9.9.9.9-{}.zip", drcom_core::platform::target_label());
        let body = release_body_with(&asset_name, zip.len() as u64, &digest);
        let http = FakeUpdateHttp::with_download(&body, &zip);

        let report = install_update_with(&http, &installed).expect("离线安装必须成功");
        assert_eq!(report.version, "9.9.9.9");
        assert_eq!(report.new_exe, installed);
        assert_eq!(std::fs::read(&installed).unwrap(), new_bytes, "正式名必须换成新程序 ✓");
        let backup = dir
            .join(update_apply::BACKUP_DIR_NAME)
            .join(drcom_core::app_version_string())
            .join(exe_name);
        assert_eq!(std::fs::read(&backup).unwrap(), ui_fake_exe(b"OLD"), "必须留一份旧程序备份 ✓");
        // 成功文案：告诉用户「需要重启」且**正在自动重启** ✓
        let (kind, text) = format_install_result(&Ok(report));
        assert_eq!(kind, "ok");
        assert!(text.contains("已安装 9.9.9.9"), "{}", text);
        assert!(text.contains("重启"), "{}", text);
        // 请求确实走了 GitHub 检查接口 + 下载了那个资产 ✓
        assert!(http
            .seen_urls
            .borrow()
            .iter()
            .any(|url| url == drcom_core::update::RELEASES_URL));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn staging_dirs_are_unique_even_within_the_same_tick() {
        // preview.5 的 macOS CI 偶发：两个并列用例在同一个 tick 里拿到同一个纳秒值 →
        // 共用 staging 目录 → 先完成的 remove_dir_all 把另一个的 .part 删掉 → rename ENOENT。
        // 现在名字里叠了进程内单调序号，任何两项都不同 ✓。
        assert_eq!(staging_dir_name(1, 2, 3), "sfl-gui-update-1-2-3");
        assert_ne!(staging_dir_name(1, 2, 3), staging_dir_name(1, 2, 4));
        assert_ne!(staging_dir_name(1, 2, 3), staging_dir_name(1, 3, 3));
        assert_ne!(staging_dir_name(1, 2, 3), staging_dir_name(2, 2, 3));

        let mut handles = Vec::new();
        for _ in 0..8 {
            handles.push(std::thread::spawn(|| {
                let mut names = Vec::new();
                for _ in 0..64 {
                    // 故意用**同一个 nonce**：唯一性必须来自序号，而不是「运气好没撞纳秒」✓
                    names.push(staging_dir_name(std::process::id(), 123, next_staging_seq()));
                }
                names
            }));
        }
        let mut all: Vec<String> = Vec::new();
        for handle in handles {
            all.extend(handle.join().expect("线程不许 panic ✓"));
        }
        let mut unique = std::collections::HashSet::new();
        for name in &all {
            assert!(
                unique.insert(name.clone()),
                "同一 tick 下临时目录名重复 ✗: {}",
                name
            );
            assert!(
                name.starts_with(&format!("sfl-gui-update-{}-123-", std::process::id())),
                "命名格式不对 ✗: {}",
                name
            );
        }
        assert_eq!(unique.len(), 512, "512 个名字必须全部唯一 ✓");
    }

    /// 并列调用「下载并安装」：各自的 staging 目录、各自装到自己的安装目录、互不干扰 ✓。
    /// （preview.5 的 macOS CI 就是这样红的；修复后这条在任意并发调度下都应稳定通过。）
    #[test]
    fn concurrent_offline_installs_do_not_share_staging_dirs() {
        let mut handles = Vec::new();
        for index in 0..4 {
            handles.push(std::thread::spawn(move || {
                let dir = ui_temp_dir(&format!("install-parallel-{}", index));
                let exe_name = update_apply::expected_exe_name();
                let installed = dir.join(exe_name);
                std::fs::write(&installed, ui_fake_exe(b"OLD")).unwrap();
                let new_bytes = ui_fake_exe(b"NEW");
                let zip = ui_package_zip(exe_name, &new_bytes, "9.9.9.9");
                let digest = drcom_core::checksum::sha256_hex(&zip);
                let asset_name = format!(
                    "stardust-flash-link-9.9.9.9-{}.zip",
                    drcom_core::platform::target_label()
                );
                let body = release_body_with(&asset_name, zip.len() as u64, &digest);
                let http = FakeUpdateHttp::with_download(&body, &zip);
                let result = install_update_with(&http, &installed);
                let outcome = match result {
                    Ok(_) => std::fs::read(&installed).map(|bytes| bytes == new_bytes),
                    Err(_) => Ok(false),
                };
                let _ = std::fs::remove_dir_all(&dir);
                outcome.unwrap_or(false)
            }));
        }
        for (index, handle) in handles.into_iter().enumerate() {
            assert!(
                handle.join().expect("线程不许 panic ✓"),
                "并列第 {} 个安装失败 ✗（临时目录撞车 / 安装目录串了？）",
                index
            );
        }
    }

    #[test]
    fn install_update_refuses_bad_checksum_without_touching_the_program() {
        let dir = ui_temp_dir("install-bad-sha");
        let exe_name = update_apply::expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, ui_fake_exe(b"OLD")).unwrap();
        let zip = ui_package_zip(exe_name, &ui_fake_exe(b"NEW"), "9.9.9.9");
        let asset_name =
            format!("stardust-flash-link-9.9.9.9-{}.zip", drcom_core::platform::target_label());
        // 摘要故意写错 → download_and_verify 必须 fail-closed ✓
        let body = release_body_with(&asset_name, zip.len() as u64, &"0".repeat(64));
        let http = FakeUpdateHttp::with_download(&body, &zip);

        let err = install_update_with(&http, &installed).unwrap_err();
        assert!(err.contains("SHA256"), "{}", err);
        assert_eq!(std::fs::read(&installed).unwrap(), ui_fake_exe(b"OLD"), "失败不许动程序 ✗");
        assert!(
            !dir.join(update_apply::BACKUP_DIR_NAME).exists(),
            "失败不许留下备份目录 ✗"
        );
        let (kind, text) = format_install_result(&Err(err));
        assert_eq!(kind, "danger");
        assert!(!text.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_update_replaces_gui_and_cli_together_offline() {
        let dir = ui_temp_dir("install-multi");
        let gui_name = update_apply::expected_exe_name();
        let cli_name = update_apply::cli_exe_name();
        let gui = dir.join(gui_name);
        let cli = dir.join(cli_name);
        std::fs::write(&gui, ui_fake_exe(b"OLD-GUI")).unwrap();
        std::fs::write(&cli, ui_fake_exe(b"OLD-CLI")).unwrap();
        let zip = ui_multi_package_zip(
            &[
                (gui_name, &ui_fake_exe(b"NEW-GUI")),
                (cli_name, &ui_fake_exe(b"NEW-CLI")),
            ],
            "9.9.9.9",
        );
        let digest = drcom_core::checksum::sha256_hex(&zip);
        let asset_name =
            format!("stardust-flash-link-9.9.9.9-{}.zip", drcom_core::platform::target_label());
        let body = release_body_with(&asset_name, zip.len() as u64, &digest);
        let http = FakeUpdateHttp::with_download(&body, &zip);

        let report = install_update_with(&http, &gui).expect("离线安装必须成功");
        assert_eq!(report.version, "9.9.9.9");
        assert_eq!(report.new_exe, gui, "重启目标必须优先 GUI ✓");
        assert_eq!(std::fs::read(&gui).unwrap(), ui_fake_exe(b"NEW-GUI"));
        assert_eq!(
            std::fs::read(&cli).unwrap(),
            ui_fake_exe(b"NEW-CLI"),
            "同目录的 CLI 也要一起换（不许一旧一新 ✗）"
        );
        let backup = dir
            .join(update_apply::BACKUP_DIR_NAME)
            .join(drcom_core::app_version_string());
        assert_eq!(std::fs::read(backup.join(gui_name)).unwrap(), ui_fake_exe(b"OLD-GUI"));
        assert_eq!(std::fs::read(backup.join(cli_name)).unwrap(), ui_fake_exe(b"OLD-CLI"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rollback_view_lists_backups_newest_first_with_program_labels() {
        let dir = ui_temp_dir("rollback-view");
        let backup_root = dir.join(update_apply::BACKUP_DIR_NAME);
        let older = backup_root.join("3.0.0.0-preview.2");
        std::fs::create_dir_all(&older).unwrap();
        for name in [update_apply::expected_exe_name(), update_apply::cli_exe_name()] {
            std::fs::write(older.join(name), ui_fake_exe(b"OLD")).unwrap();
        }
        let newer = backup_root.join("3.0.0.0-preview.3");
        std::fs::create_dir_all(&newer).unwrap();
        std::fs::write(newer.join(update_apply::expected_exe_name()), ui_fake_exe(b"NEW")).unwrap();

        let view = rollback_view_of(&backup_root);
        assert!(view.available);
        assert_eq!(view.entries.len(), 2);
        assert_eq!(view.entries[0].version, "3.0.0.0-preview.3", "新的必须在前 ✓");
        assert!(view.entries[0].label.contains("界面"), "{}", view.entries[0].label);
        assert!(
            view.entries[1].label.contains("界面") && view.entries[1].label.contains("命令行"),
            "两个程序都要在标签里: {}",
            view.entries[1].label
        );
        // 空备份目录 → 不可用 + 一句说明（不给死按钮 ✓）
        let empty = rollback_view_of(&dir.join("no-backup"));
        assert!(!empty.available);
        assert!(empty.entries.is_empty());
        assert!(!empty.line.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rollback_to_in_restores_gui_and_cli_offline() {
        let dir = ui_temp_dir("rollback-run");
        let gui_name = update_apply::expected_exe_name();
        let cli_name = update_apply::cli_exe_name();
        let gui = dir.join(gui_name);
        let cli = dir.join(cli_name);
        std::fs::write(&gui, ui_fake_exe(b"CURRENT-GUI")).unwrap();
        std::fs::write(&cli, ui_fake_exe(b"CURRENT-CLI")).unwrap();
        let backup_dir = dir
            .join(update_apply::BACKUP_DIR_NAME)
            .join("3.0.0.0-preview.2");
        std::fs::create_dir_all(&backup_dir).unwrap();
        std::fs::write(backup_dir.join(gui_name), ui_fake_exe(b"OLD-GUI")).unwrap();
        std::fs::write(backup_dir.join(cli_name), ui_fake_exe(b"OLD-CLI")).unwrap();

        let done = rollback_to_in(&dir, "3.0.0.0-preview.2").expect("离线回滚必须成功");
        assert_eq!(done.restart, Some(gui.clone()), "回滚后要重启 GUI ✓");
        assert!(done.line.contains("3.0.0.0-preview.2"), "{}", done.line);
        assert_eq!(std::fs::read(&gui).unwrap(), ui_fake_exe(b"OLD-GUI"));
        assert_eq!(
            std::fs::read(&cli).unwrap(),
            ui_fake_exe(b"OLD-CLI"),
            "CLI 也要一起回滚（不许一旧一新 ✗）"
        );
        // 不存在的版本 → 可读错误（不动盘 ✓）
        let err = rollback_to_in(&dir, "9.9.9.9").unwrap_err();
        assert!(err.contains("没有找到") || err.contains("update backups"), "{}", err);
        assert_eq!(std::fs::read(&gui).unwrap(), ui_fake_exe(b"OLD-GUI"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_update_says_up_to_date_instead_of_faking_an_install() {
        // 空 releases 列表 → 跳过（不是假装有更新）→ 安装入口必须给可读错误 ✓
        let http = FakeUpdateHttp::ok("[]");
        let installed = std::env::temp_dir().join("sfl-ui-never-written");
        let err = install_update_with(&http, &installed).unwrap_err();
        assert!(err.contains("没有可用的发布版本") || err.contains("跳过"), "{}", err);
        assert!(!installed.exists());
    }
}

