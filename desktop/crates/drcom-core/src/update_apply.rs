//! M4 第二块：**自我替换 / 回滚**（单文件便携 exe 的「就地升级」）✓。
//!
//! 前提（Windows 的关键事实，先写清楚）：
//!   - Windows 上**正在运行的 exe 不能被删除 / 覆盖**（映像被锁 ✗），但**可以被改名**（MoveFile ✓）。
//!     所以替换舞步是：**备份 → 改名旧 exe → 放入新 exe → 拉起新进程 → 旧进程退出** ✓；
//!   - 安装目录 = **当前 exe 所在的目录**（便携版口径 ✓）；用户数据在 platform::data_dir()，
//!     本模块**绝不碰用户数据目录**（只动安装目录里的 exe 与 backup/ 子目录 ✓）。
//!
//! 备份布局：`<安装目录>/backup/<当前版本>/<exe 名>` —— 回滚按版本目录取 ✓
//! （`update backups` 列出来就是这些目录 ✓）。
//!
//! 失败必须能回来：
//!   - 备份先落盘并**逐字节校验**（SHA-256），备份失败 → 中止，当前程序一点没动 ✓；
//!   - 新程序先复制到**同目录**临时名（同盘 rename 才是原子的 ✓），复制后也校验；
//!   - 旧 exe 改名成功、新 exe 改名失败 → **自动把旧 exe 改回去**并清理临时文件 ✓；
//!   - 连改回去都失败（极端：杀毒软件锁死 / 权限）→ 错误文案直接说清「旧程序现在在哪个文件名」，
//!     让用户能手动救回来（**绝不静默丢程序** ✗）。
//!
//! 所有会写盘的入口都带 `dry_run`：true 时只做**只读校验**，不创建备份、不动任何文件 ✓
//! （CLI 的 `--dry-run` 直接用它 ✓）。
//!
//! R1：只用 std + 本仓库自己的 zipread / checksum / atomicfile / platform，**零新增 crate** ✓。

use crate::atomicfile;
use crate::channel::Version;
use crate::checksum;
use crate::platform::{self, Os};
use crate::zipread::{self, ZipReadEntry};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// 命令行程序名（CI 的 `stardust-flash-link` / Windows 带 .exe ✓）。
pub const CLI_EXE_NAME: &str = "stardust-flash-link";
/// 桌面程序名（CI 的 `stardust-flash-link-app` / Windows 带 .exe ✓）；
/// 详见 .github/workflows/preview-3.0.yml 的 `APP_NAME` ✓。
pub const APP_EXE_NAME: &str = "stardust-flash-link-app";
/// 备份目录名（安装目录下 ✓）。
pub const BACKUP_DIR_NAME: &str = "backup";
/// 单个程序文件的上限：64 MiB（GUI 目前 8 MB 量级；zipread 单条目上限同值 ✓）。
pub const MAX_EXE_BYTES: u64 = 64 * 1024 * 1024;
/// 安装包 ZIP 的上限（读进内存前先量文件大小 ✓）。
pub const MAX_PACKAGE_BYTES: u64 = 256 * 1024 * 1024;
/// 待清理的旧文件前缀（`<exe>.old-…` / `.incoming-…` / `.rollback-…` ✓）。
const OLD_TAG: &str = "old";
const INCOMING_TAG: &str = "incoming";
const ROLLBACK_TAG: &str = "rollback";

/// 进程内序号：同一进程连续做两次替换也不会撞上同一个临时名 ✓。
static NEXT_SWAP_SEQ: AtomicU64 = AtomicU64::new(0);

/// CI 里**桌面程序**的真实文件名（按平台 ✓）：Windows 带 .exe，其它平台不带 ✓。
pub fn expected_exe_name_for(os: Os) -> &'static str {
    match os {
        Os::Windows => "stardust-flash-link-app.exe",
        Os::MacOs | Os::Linux => "stardust-flash-link-app",
    }
}

/// 当前平台的桌面程序名（= CI 产物里 GUI 的文件名 ✓）。
pub fn expected_exe_name() -> &'static str {
    expected_exe_name_for(Os::current())
}

/// CI 里**命令行程序**的真实文件名（按平台 ✓）。
pub fn cli_exe_name_for(os: Os) -> &'static str {
    match os {
        Os::Windows => "stardust-flash-link.exe",
        Os::MacOs | Os::Linux => "stardust-flash-link",
    }
}

/// 当前平台的命令行程序名（= CI 产物里的 CLI 文件名 ✓）。
pub fn cli_exe_name() -> &'static str {
    cli_exe_name_for(Os::current())
}

/// **纯函数**：程序名安全吗（会当文件名用，所以走白名单 ✓）。
pub fn is_safe_exe_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 160
        && !name.contains('/')
        && !name.contains('\\')
        && !name.contains("..")
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '+'))
}

/// **纯函数**：可执行文件魔数校验（按平台 ✓ —— 平台差异只在这个纯函数里 ✓）。
///
///   - Windows：PE 一定以 `MZ` 开头 ✓；
///   - Linux：ELF `\x7fELF` ✓；
///   - macOS：Mach-O 0xFEEDFACF / 0xFEEDFACE（大小端两种落盘形态都认 ✓）
///     与通用二进制 0xCAFEBABE（fat，同样两种字节序 ✓）。
pub fn exe_magic_ok(bytes: &[u8], os: Os) -> bool {
    match os {
        Os::Windows => bytes.starts_with(b"MZ"),
        Os::Linux => bytes.starts_with(&[0x7f, b'E', b'L', b'F']),
        Os::MacOs => {
            const MAGICS: [[u8; 4]; 6] = [
                [0xFE, 0xED, 0xFA, 0xCF], // Mach-O 64（大端写法）
                [0xCF, 0xFA, 0xED, 0xFE], // Mach-O 64（小端，实际 x86_64 / arm64 就是它）
                [0xFE, 0xED, 0xFA, 0xCE], // Mach-O 32（大端）
                [0xCE, 0xFA, 0xED, 0xFE], // Mach-O 32（小端）
                [0xCA, 0xFE, 0xBA, 0xBE], // 通用二进制（fat，大端）
                [0xBE, 0xBA, 0xFE, 0xCA], // 通用二进制（fat，小端）
            ];
            bytes.len() >= 4 && MAGICS.iter().any(|magic| bytes.starts_with(magic))
        }
    }
}

/// **纯函数**：从 ZIP 条目里挑出「本机的那个 exe」✓。
///
/// 规则（宁可拒绝也不猜 ✓）：
///   - 允许**一层**目录（CI 打的包是 `staging/stardust-flash-link-app.exe` ✓），
///     再深就不认（那是别人家的包结构 ✗）；
///   - 文件名必须**逐字**等于 `expected`（来自安装目录里真实的程序名 ✓）；
///   - 恰好 1 个才接受；0 个 / 多个都给可读中文错误 ✓。
pub fn pick_exe_entry<'a>(
    entries: &'a [ZipReadEntry],
    expected: &str,
) -> Result<&'a ZipReadEntry, String> {
    if !is_safe_exe_name(expected) {
        return Err(format!("程序名「{}」不安全，拒绝从安装包里挑文件", expected));
    }
    let mut hits: Vec<&ZipReadEntry> = Vec::new();
    for entry in entries {
        if entry.name.matches('/').count() > 1 {
            continue; // 只允许一层目录 ✓
        }
        let file_name = entry.name.rsplit('/').next().unwrap_or("");
        if file_name == expected {
            hits.push(entry);
        }
    }
    match hits.as_slice() {
        [] => Err(format!(
            "安装包里没有「{}」（允许一层目录，例如 staging/{}）—— 可能下错了平台 / 打错了包",
            expected, expected
        )),
        [only] => Ok(only),
        many => Err(format!(
            "安装包里有 {} 份「{}」，不敢猜用哪个 —— 拒绝安装",
            many.len(),
            expected
        )),
    }
}

/// **纯函数**：从安装包自带的说明文件里读版本号（本地 zip 安装时用来显示 / 备份提示 ✓）。
///
/// 认 `README.txt` / `deb-control` / `Info.plist` 的前几行：挑出像 `3.0.0.0-preview.3`
/// 这样的四段版本 token（至少两个点，避免把 "1.0" 这种废话当版本 ✗）。
pub fn package_version_hint(entries: &[ZipReadEntry]) -> Option<Version> {
    for entry in entries {
        let file_name = entry.name.rsplit('/').next().unwrap_or("");
        let wanted = file_name.eq_ignore_ascii_case("README.txt")
            || file_name.eq_ignore_ascii_case("deb-control")
            || file_name.eq_ignore_ascii_case("Info.plist");
        if !wanted {
            continue;
        }
        let text = String::from_utf8_lossy(&entry.data);
        if let Some(version) = version_hint_from_text(&text) {
            return Some(version);
        }
    }
    None
}

fn version_hint_from_text(text: &str) -> Option<Version> {
    for line in text.lines().take(20) {
        for token in line.split(|c: char| {
            c.is_whitespace()
                || matches!(c, ',' | ':' | '(' | ')' | '（' | '）' | '"' | '<' | '>' | '=')
        }) {
            let candidate =
                token.trim_matches(|c: char| !c.is_ascii_alphanumeric() && c != '.' && c != '-');
            if candidate.matches('.').count() < 2 {
                continue;
            }
            if let Some(version) = Version::parse(candidate) {
                return Some(version);
            }
        }
    }
    None
}

/// 一次替换的**可读计划**（CLI 的 `--dry-run` 直接逐行打印 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApplyPlan {
    /// 要替换的程序（安装目录里的正式名 ✓）。
    pub installed_exe: PathBuf,
    /// 新程序（从安装包里解出来的临时文件 ✓；dry-run 时它还不存在 ✓）。
    pub staged_exe: PathBuf,
    /// 当前程序的备份（`backup/<当前版本>/<exe 名>` ✓）。
    pub backup_exe: PathBuf,
    /// 新程序的临时名（与正式名**同目录**，rename 才是原子的 ✓）。
    pub incoming_exe: PathBuf,
    /// 旧程序的临时名（成功后就是「待清理」的文件 ✓）。
    pub old_exe: PathBuf,
    /// 当前版本（备份目录名用它 ✓）。
    pub current_version: String,
    /// 目标版本（安装包带来的版本；本地 zip 读不出来时是「未知版本」✓）。
    pub target_version: String,
    /// 逐步骤的中文说明 ✓。
    pub steps: Vec<String>,
}

/// **纯函数**：算出一份替换计划（只拼路径与文案，**不碰磁盘** ✓）。
///
/// 备份目录用**当前版本**：回滚 = 从 `backup/<那个版本>/` 取回来 ✓。
pub fn plan_apply(
    installed_exe: &Path,
    staged_exe: &Path,
    backup_root: &Path,
    current_version: &str,
    target_version: &str,
) -> ApplyPlan {
    let exe_name = file_name_lossy(installed_exe);
    let version_dir = safe_version_segment(current_version).unwrap_or("unknown");
    let backup_exe = backup_root.join(version_dir).join(&exe_name);
    let incoming_exe = sibling_path(installed_exe, INCOMING_TAG);
    let old_exe = sibling_path(installed_exe, OLD_TAG);
    let steps = vec![
        format!("① 校验安装包：{} 是本平台的可执行文件（魔数）", staged_exe.display()),
        format!(
            "② 备份当前程序：{} → {}（并做 SHA-256 校验）",
            installed_exe.display(),
            backup_exe.display()
        ),
        format!(
            "③ 把新程序复制到安装目录的临时名：{}（与正式名同目录，rename 才原子）",
            incoming_exe.display()
        ),
        format!(
            "④ 把当前程序改名：{} → {}（Windows 上「运行中的 exe」可以改名，但不能删除 / 覆盖）",
            installed_exe.display(),
            old_exe.display()
        ),
        format!(
            "⑤ 把新程序改名成正式名：{} → {}",
            incoming_exe.display(),
            installed_exe.display()
        ),
        "   任一步失败 → 自动回滚（把旧程序改回原名、清掉临时文件）".to_string(),
        "⑥ 重新拉起新程序（除非 --no-restart；新进程脱离当前控制台 ✓）".to_string(),
    ];
    ApplyPlan {
        installed_exe: installed_exe.to_path_buf(),
        staged_exe: staged_exe.to_path_buf(),
        backup_exe,
        incoming_exe,
        old_exe,
        current_version: current_version.to_string(),
        target_version: target_version.to_string(),
        steps,
    }
}

/// 一次替换的结论 ✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApplyOutcome {
    /// 新的正式程序路径（= 计划里的 installed_exe ✓）。
    pub new_exe: PathBuf,
    /// 备份路径 ✓。
    pub backup: PathBuf,
    /// 旧程序改名后的位置（**本进程还占着它 → 通常删不掉，留给下次启动清理** ✓）。
    pub stale_old: PathBuf,
    /// 是不是 dry-run（true 表示什么都没动 ✓）。
    pub dry_run: bool,
}

/// 按计划执行替换（**dry_run = true 时只做只读校验** ✓）。
pub fn apply_plan(plan: &ApplyPlan, dry_run: bool) -> Result<ApplyOutcome, String> {
    let exe_name = file_name_lossy(&plan.installed_exe);
    if !is_safe_exe_name(&exe_name) {
        return Err(format!("安装目录里的程序名「{}」不合法 —— 拒绝替换", exe_name));
    }
    let installed_parent = plan
        .installed_exe
        .parent()
        .ok_or_else(|| "当前程序没有所在目录 —— 拒绝替换".to_string())?;
    // 防御：临时名必须和正式名**同目录**（跨盘 rename 不原子 ✗）
    if plan.incoming_exe.parent() != Some(installed_parent) || plan.old_exe.parent() != Some(installed_parent) {
        return Err("内部错误：临时文件名不在安装目录里 —— 拒绝替换".to_string());
    }

    // ① 校验（只读）
    validate_executable(&plan.installed_exe, "当前程序")?;
    validate_executable(&plan.staged_exe, "新程序")?;
    let head = read_head(&plan.staged_exe)?;
    if !exe_magic_ok(&head, Os::current()) {
        return Err(format!(
            "新程序「{}」不是本平台的可执行文件（魔数校验失败）—— 拒绝替换",
            plan.staged_exe.display()
        ));
    }
    if dry_run {
        return Ok(ApplyOutcome {
            new_exe: plan.installed_exe.clone(),
            backup: plan.backup_exe.clone(),
            stale_old: plan.old_exe.clone(),
            dry_run: true,
        });
    }

    // ② 备份（**先落盘并校验**，再动当前程序 ✓）
    if let Some(parent) = plan.backup_exe.parent() {
        std::fs::create_dir_all(parent).map_err(|error| {
            format!(
                "建备份目录 {} 失败：{}（安装目录没有写入权限？）—— 已中止，程序没有被改动",
                parent.display(),
                error
            )
        })?;
    }
    copy_atomic(&plan.installed_exe, &plan.backup_exe)?;
    verify_same_bytes(&plan.installed_exe, &plan.backup_exe, "备份")?;

    // ③ 新程序复制到同目录临时名 ✓
    copy_atomic(&plan.staged_exe, &plan.incoming_exe)?;
    verify_same_bytes(&plan.staged_exe, &plan.incoming_exe, "复制新程序")?;

    // ④⑤ 换名舞步（失败自动回滚 ✓）
    if let Err(error) = swap_incoming_into_place(&plan.incoming_exe, &plan.installed_exe, &plan.old_exe) {
        let _ = std::fs::remove_file(&plan.incoming_exe);
        return Err(error);
    }
    Ok(ApplyOutcome {
        new_exe: plan.installed_exe.clone(),
        backup: plan.backup_exe.clone(),
        stale_old: plan.old_exe.clone(),
        dry_run: false,
    })
}

/// 一步到位：计划 + 执行（CLI / GUI 都用它；单测也直接调 ✓）。
pub fn apply_replace(
    installed_exe: &Path,
    staged_exe: &Path,
    backup_root: &Path,
    current_version: &str,
    target_version: &str,
    dry_run: bool,
) -> Result<ApplyOutcome, String> {
    let plan = plan_apply(
        installed_exe,
        staged_exe,
        backup_root,
        current_version,
        target_version,
    );
    apply_plan(&plan, dry_run)
}

/// 默认的「要更新的程序」：**安装目录里的桌面程序**；没有它时退回当前 exe 自己 ✓。
///
/// 这样两种用法都成立：
///   - GUI 自己更新自己（current_exe 就是 stardust-flash-link-app ✓）；
///   - 只带了命令行单文件的人在 CLI 里更新命令行自身（目录里没有 GUI 程序 ✓）。
pub fn default_installed_exe() -> Result<PathBuf, String> {
    let current = std::env::current_exe().map_err(|error| format!("拿不到本程序路径：{}", error))?;
    let dir = current
        .parent()
        .ok_or_else(|| "本程序没有所在目录 —— 无法判断安装目录".to_string())?;
    let gui = dir.join(expected_exe_name());
    if gui.is_file() {
        return Ok(gui);
    }
    if current.is_file() {
        return Ok(current);
    }
    Err(format!("找不到要更新的程序（{}）", current.display()))
}

/// 默认备份根目录：`<安装目录>/backup` ✓（**不在用户数据目录** ✓）。
pub fn default_backup_root(installed_exe: &Path) -> PathBuf {
    match installed_exe.parent() {
        Some(dir) if !dir.as_os_str().is_empty() => dir.join(BACKUP_DIR_NAME),
        _ => PathBuf::from(BACKUP_DIR_NAME),
    }
}

/// 备份文件路径（`<backup_root>/<版本>/<exe 名>` ✓）；版本段与文件名都过白名单 ✓。
pub fn backup_exe_path(
    backup_root: &Path,
    version: &str,
    exe_name: &str,
) -> Result<PathBuf, String> {
    let version_dir = safe_version_segment(version)?;
    if !is_safe_exe_name(exe_name) {
        return Err(format!("程序名「{}」不安全，不能当备份文件名", exe_name));
    }
    Ok(backup_root.join(version_dir).join(exe_name))
}

/// 列出所有备份：`(版本, 备份文件路径)`，**新版本在前** ✓。
///
/// 备份目录布局就是 `backup/<版本>/<exe>`；版本目录名解析不出四段版本时排在最后 ✓。
pub fn list_backups(backup_root: &Path) -> Vec<(String, PathBuf)> {
    let Ok(entries) = std::fs::read_dir(backup_root) else {
        return Vec::new();
    };
    let mut found: Vec<(Option<Version>, String, PathBuf)> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some(exe) = backup_file_in(&path) else { continue };
        found.push((Version::parse(&name), name, exe));
    }
    found.sort_by(|left, right| match (&left.0, &right.0) {
        (Some(a), Some(b)) => b.cmp(a), // 新的在前 ✓
        (Some(_), None) => std::cmp::Ordering::Less,
        (None, Some(_)) => std::cmp::Ordering::Greater,
        (None, None) => left.1.cmp(&right.1),
    });
    found
        .into_iter()
        .map(|(_, version, path)| (version, path))
        .collect()
}

/// 一次回滚的**可读计划**（`--dry-run` 逐行打印 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RollbackPlan {
    pub installed_exe: PathBuf,
    pub restore_from: PathBuf,
    pub incoming_exe: PathBuf,
    pub old_exe: PathBuf,
    pub version: String,
    pub steps: Vec<String>,
}

/// 一次回滚的结论 ✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RollbackOutcome {
    pub new_exe: PathBuf,
    pub restored_from: PathBuf,
    pub stale_old: PathBuf,
    pub dry_run: bool,
}

/// **纯函数 + 只读查盘**：算出一份回滚计划（`version = None` 时取最新的备份 ✓）。
pub fn plan_rollback(
    installed_exe: &Path,
    backup_root: &Path,
    version: Option<&str>,
) -> Result<RollbackPlan, String> {
    let exe_name = file_name_lossy(installed_exe);
    if !is_safe_exe_name(&exe_name) {
        return Err(format!("安装目录里的程序名「{}」不合法 —— 拒绝回滚", exe_name));
    }
    let restore_from = match version {
        Some(raw) => {
            let version_dir = safe_version_segment(raw)?;
            let candidate = backup_root.join(version_dir).join(&exe_name);
            if !candidate.is_file() {
                return Err(format!(
                    "没有找到 {} 的备份（期望 {}）—— 用 update backups 看看有哪些版本",
                    raw,
                    candidate.display()
                ));
            }
            candidate
        }
        None => pick_latest_backup_for(backup_root, &exe_name)?,
    };
    let version_label = restore_from
        .parent()
        .and_then(|dir| dir.file_name())
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| "未知版本".to_string());
    let incoming_exe = sibling_path(installed_exe, INCOMING_TAG);
    let old_exe = sibling_path(installed_exe, ROLLBACK_TAG);
    let steps = vec![
        format!(
            "① 校验备份：{}（非空 + 本平台魔数）",
            restore_from.display()
        ),
        format!(
            "② 把备份复制到安装目录的临时名：{}（同目录，rename 才原子）",
            incoming_exe.display()
        ),
        format!(
            "③ 把当前程序改名：{} → {}",
            installed_exe.display(),
            old_exe.display()
        ),
        format!(
            "④ 把备份改名成正式名：{} → {}",
            incoming_exe.display(),
            installed_exe.display()
        ),
        "   任一步失败 → 自动回滚（把当前程序改回原名、清掉临时文件）".to_string(),
        "⑤ 重新拉起回滚后的程序（除非 --no-restart / --dry-run）".to_string(),
    ];
    Ok(RollbackPlan {
        installed_exe: installed_exe.to_path_buf(),
        restore_from,
        incoming_exe,
        old_exe,
        version: version_label,
        steps,
    })
}

/// 按计划执行回滚（`dry_run = true` 时只做只读校验 ✓）。
pub fn rollback_plan(plan: &RollbackPlan, dry_run: bool) -> Result<RollbackOutcome, String> {
    let installed_parent = plan
        .installed_exe
        .parent()
        .ok_or_else(|| "当前程序没有所在目录 —— 拒绝回滚".to_string())?;
    if plan.incoming_exe.parent() != Some(installed_parent) || plan.old_exe.parent() != Some(installed_parent) {
        return Err("内部错误：临时文件名不在安装目录里 —— 拒绝回滚".to_string());
    }
    validate_executable(&plan.installed_exe, "当前程序")?;
    validate_executable(&plan.restore_from, "备份程序")?;
    let head = read_head(&plan.restore_from)?;
    if !exe_magic_ok(&head, Os::current()) {
        return Err(format!(
            "备份「{}」不是本平台的可执行文件（魔数校验失败）—— 拒绝回滚",
            plan.restore_from.display()
        ));
    }
    if dry_run {
        return Ok(RollbackOutcome {
            new_exe: plan.installed_exe.clone(),
            restored_from: plan.restore_from.clone(),
            stale_old: plan.old_exe.clone(),
            dry_run: true,
        });
    }
    copy_atomic(&plan.restore_from, &plan.incoming_exe)?;
    verify_same_bytes(&plan.restore_from, &plan.incoming_exe, "复制备份")?;
    if let Err(error) = swap_incoming_into_place(&plan.incoming_exe, &plan.installed_exe, &plan.old_exe)
    {
        let _ = std::fs::remove_file(&plan.incoming_exe);
        return Err(error);
    }
    Ok(RollbackOutcome {
        new_exe: plan.installed_exe.clone(),
        restored_from: plan.restore_from.clone(),
        stale_old: plan.old_exe.clone(),
        dry_run: false,
    })
}

/// 回滚到指定版本（`None` = 最新备份 ✓）。
pub fn rollback_replace(
    install_dir: &Path,
    exe_name: &str,
    backup_root: &Path,
    version: Option<&str>,
    dry_run: bool,
) -> Result<RollbackOutcome, String> {
    if !is_safe_exe_name(exe_name) {
        return Err(format!("程序名「{}」不安全 —— 拒绝回滚", exe_name));
    }
    let installed_exe = install_dir.join(exe_name);
    let plan = plan_rollback(&installed_exe, backup_root, version)?;
    rollback_plan(&plan, dry_run)
}

/// **清理上次更新留下的旧文件**：`<exe>.old-*` / `.incoming-*` / `.rollback-*` ✓。
///
/// 本进程正在运行的那个旧 exe **删不掉是正常的**（Windows 映像被锁 ✗）——
/// 所以这里不返回 Err，只把「暂时删不掉」的文件名收成**告警字符串**，留给下次启动再清 ✓。
pub fn cleanup_stale_files(install_dir: &Path, exe_name: &str) -> Vec<String> {
    let mut warnings = Vec::new();
    if !is_safe_exe_name(exe_name) {
        return warnings;
    }
    let Ok(entries) = std::fs::read_dir(install_dir) else {
        return warnings;
    };
    let prefixes = [
        format!("{}.{}-", exe_name, OLD_TAG),
        format!("{}.{}-", exe_name, INCOMING_TAG),
        format!("{}.{}-", exe_name, ROLLBACK_TAG),
    ];
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !prefixes.iter().any(|prefix| name.starts_with(prefix)) {
            continue;
        }
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        if let Err(error) = std::fs::remove_file(&path) {
            warnings.push(format!(
                "旧文件「{}」暂时删不掉（{}）—— 下次启动会自动再清一次",
                name, error
            ));
        }
    }
    warnings
}

/// 启动时清理：按**当前 exe** 推安装目录与文件名 ✓（GUI 启动时调一次 ✓）。
pub fn cleanup_stale_for_exe(exe: &Path) -> Vec<String> {
    let Some(dir) = exe.parent() else {
        return Vec::new();
    };
    cleanup_stale_files(dir, &file_name_lossy(exe))
}

/// **拉起新进程**（自我替换之后用 ✓）：脱离当前控制台，不等它退出 ✓。
pub fn restart_detached(exe_path: &Path) -> Result<(), String> {
    if !exe_path.is_file() {
        return Err(format!(
            "要启动的新程序不存在：{} —— 程序已安装，请手动启动",
            exe_path.display()
        ));
    }
    platform::spawn_detached(exe_path, &[])
        .map_err(|error| format!("{}（程序已安装，请手动启动它）", error))
}

/// 从本地安装包（ZIP 字节）到「装好」的完整流程结论 ✓。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallOutcome {
    /// 替换计划（CLI 的 --dry-run 直接打印它 ✓）。
    pub plan: ApplyPlan,
    /// 安装包带来的版本（读不出来时是「未知版本」✓）。
    pub target_version: String,
    /// 旧程序待清理的位置 ✓。
    pub stale_old: PathBuf,
    /// 清理旧文件时的告警（删不掉不是错误 ✓）。
    pub warnings: Vec<String>,
    /// 是不是 dry-run（true = 什么都没动 ✓）。
    pub dry_run: bool,
}

/// **本地 ZIP → 校验 → 备份 → 替换 → 清理** 一条龙（不联网 ✓）。
///
/// 步骤与 apply_plan 一致；额外做三件事：
///   1. 用 zipread 解析安装包（**只信本项目 archive 打的 store ZIP** ✓）；
///   2. 按安装目录里真实的程序名挑 exe 条目，并校验平台魔数 ✓；
///   3. 解包出来的新程序先写进**系统临时目录**（绝不碰用户数据目录 ✓），用完即删 ✓。
pub fn install_zip(
    installed_exe: &Path,
    zip_bytes: &[u8],
    backup_root: &Path,
    current_version: &str,
    target_version: Option<&str>,
    dry_run: bool,
) -> Result<InstallOutcome, String> {
    let entries = zipread::read_entries(zip_bytes)?;
    let exe_name = file_name_lossy(installed_exe);
    if !is_safe_exe_name(&exe_name) {
        return Err(format!("安装目录里的程序名「{}」不合法 —— 拒绝安装", exe_name));
    }
    let entry = pick_exe_entry(&entries, &exe_name)?;
    if entry.data.is_empty() {
        return Err(format!("安装包里的「{}」是空文件 —— 拒绝安装", exe_name));
    }
    if entry.data.len() as u64 > MAX_EXE_BYTES {
        return Err(format!(
            "安装包里的「{}」太大（{} 字节，上限 {}）—— 不像是本项目的程序",
            exe_name,
            entry.data.len(),
            MAX_EXE_BYTES
        ));
    }
    if !exe_magic_ok(&entry.data, Os::current()) {
        return Err(format!(
            "安装包里的「{}」不是本平台的可执行文件（魔数校验失败）—— 可能下错了平台，拒绝安装",
            exe_name
        ));
    }
    let target = target_version
        .map(str::to_string)
        .or_else(|| package_version_hint(&entries).map(|version| version.to_string_full()))
        .unwrap_or_else(|| "未知版本".to_string());
    let staged = staged_temp_path(&exe_name);
    let plan = plan_apply(installed_exe, &staged, backup_root, current_version, &target);
    if dry_run {
        return Ok(InstallOutcome {
            plan: plan.clone(),
            target_version: target,
            stale_old: plan.old_exe.clone(),
            warnings: Vec::new(),
            dry_run: true,
        });
    }
    write_staged(&staged, &entry.data)?;
    let applied = apply_plan(&plan, false);
    // 临时文件无论成败都清掉（清不掉也不影响结论 ✓）
    let _ = std::fs::remove_file(&staged);
    if let Some(parent) = staged.parent() {
        let _ = std::fs::remove_dir(parent);
    }
    let outcome = applied?;
    let install_dir = plan
        .installed_exe
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let mut warnings = cleanup_stale_files(&install_dir, &exe_name);
    if outcome.stale_old.exists() && warnings.is_empty() {
        warnings.push(format!(
            "旧程序「{}」还占着（本进程正在用它）—— 下次启动会自动清掉",
            file_name_lossy(&outcome.stale_old)
        ));
    }
    Ok(InstallOutcome {
        plan,
        target_version: target,
        stale_old: outcome.stale_old,
        warnings,
        dry_run: false,
    })
}

// ── 下面的都是内部工具（薄、可单测）────────────────────────────────────────────

/// 文件名的 lossy 版本（路径不合法时给空串，由调用方判 ✓）。
fn file_name_lossy(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// 版本串能不能当目录名（白名单：字母数字与 . - _ +，不许 / \ .. ✓）。
fn safe_version_segment(version: &str) -> Result<&str, String> {
    let trimmed = version.trim();
    let ok = !trimmed.is_empty()
        && trimmed.len() <= 64
        && !trimmed.contains('/')
        && !trimmed.contains('\\')
        && !trimmed.contains("..")
        && trimmed
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_' | '+'));
    if !ok {
        return Err(format!("版本号「{}」不能当目录名用 —— 拒绝", version));
    }
    Ok(trimmed)
}

/// 与正式名**同目录**的临时名：`<exe>.<tag>-<pid>-<seq>` ✓
/// （带进程内序号：同一进程连续两次替换也不会撞名 ✓）。
fn sibling_path(exe: &Path, tag: &str) -> PathBuf {
    let seq = NEXT_SWAP_SEQ.fetch_add(1, Ordering::Relaxed);
    let name = format!(
        "{}.{}-{}-{}",
        file_name_lossy(exe),
        tag,
        std::process::id(),
        seq
    );
    match exe.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent.join(name),
        _ => PathBuf::from(name),
    }
}

/// **rename 舞步**：incoming → installed，旧 installed → old ✓。
///
/// 这是整个自我替换的**唯一换名点** —— 失败必须把旧程序改回去 ✓：
///   1. `rename(installed → old)` 失败 → 当前程序一点没动，直接报错 ✓；
///   2. `rename(incoming → installed)` 失败 → `rename(old → installed)` 回滚 ✓；
///   3. 连回滚都失败 → 错误里写清「旧程序现在在哪个文件名」，让用户能手动救 ✓。
///
/// Windows 前提：**运行中的 exe 可以被改名**（MoveFile ✓），但不能删除 / 覆盖 ✗ ——
/// 所以顺序必须是「先把旧的挪开（改名），再把新的放到正式名」✓。
fn swap_incoming_into_place(
    incoming: &Path,
    installed: &Path,
    old: &Path,
) -> Result<(), String> {
    if let Err(error) = std::fs::rename(installed, old) {
        return Err(format!(
            "把当前程序改名为「{}」失败：{}（可能被杀毒软件 / 权限挡住）—— 已中止，程序没有被改动",
            file_name_lossy(old),
            error
        ));
    }
    if let Err(error) = std::fs::rename(incoming, installed) {
        return match std::fs::rename(old, installed) {
            Ok(()) => Err(format!(
                "把新程序放到「{}」失败：{} —— 已回滚，当前程序完好无损",
                file_name_lossy(installed),
                error
            )),
            Err(restore_error) => Err(format!(
                "把新程序放到「{}」失败：{}；而且把旧程序改回原名也失败：{} —— 旧程序现在在「{}」，请手动把它改回「{}」",
                file_name_lossy(installed),
                error,
                restore_error,
                old.display(),
                file_name_lossy(installed)
            )),
        };
    }
    Ok(())
}

/// 原子复制：先写同目录唯一 tmp（atomicfile 的命名规矩 ✓），再 rename ✓。
fn copy_atomic(source: &Path, target: &Path) -> Result<(), String> {
    let tmp = atomicfile::unique_tmp_path(target);
    if let Err(error) = std::fs::copy(source, &tmp) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!(
            "复制 {} → {} 失败：{}",
            source.display(),
            target.display(),
            error
        ));
    }
    atomicfile::commit(&tmp, target)
}

/// 逐字节校验两份文件（走仓库自己的 SHA-256 ✓）。
fn verify_same_bytes(left: &Path, right: &Path, what: &str) -> Result<(), String> {
    let left_sum = checksum::sha256_file(left)
        .map_err(|error| format!("算 {} 的 SHA-256 失败：{}", left.display(), error))?;
    let right_sum = checksum::sha256_file(right)
        .map_err(|error| format!("算 {} 的 SHA-256 失败：{}", right.display(), error))?;
    if left_sum != right_sum {
        return Err(format!(
            "{} 校验失败（SHA-256 不一致）—— 磁盘可能出错，已中止",
            what
        ));
    }
    Ok(())
}

/// 校验「这是一个可以替换的程序文件」：存在、是普通文件、非空、不太大 ✓。
fn validate_executable(path: &Path, role: &str) -> Result<u64, String> {
    let metadata = std::fs::metadata(path)
        .map_err(|error| format!("找不到{}（{}）：{}", role, path.display(), error))?;
    if !metadata.is_file() {
        return Err(format!("{}「{}」不是普通文件 —— 拒绝", role, path.display()));
    }
    let length = metadata.len();
    if length == 0 {
        return Err(format!("{}「{}」是空文件 —— 拒绝", role, path.display()));
    }
    if length > MAX_EXE_BYTES {
        return Err(format!(
            "{}「{}」太大（{} 字节，上限 {}）—— 不像是本项目的程序",
            role,
            path.display(),
            length,
            MAX_EXE_BYTES
        ));
    }
    Ok(length)
}

/// 读文件开头几个字节（魔数校验用 ✓，不把整个 exe 读进内存）。
fn read_head(path: &Path) -> Result<Vec<u8>, String> {
    let mut file = std::fs::File::open(path)
        .map_err(|error| format!("打开 {} 失败：{}", path.display(), error))?;
    let mut head = vec![0u8; 16];
    let read = file
        .read(&mut head)
        .map_err(|error| format!("读 {} 失败：{}", path.display(), error))?;
    head.truncate(read);
    Ok(head)
}

/// 备份目录里挑一个可执行文件：优先本平台的 GUI 程序名，否则取排序后的第一个 ✓。
fn backup_file_in(dir: &Path) -> Option<PathBuf> {
    let entries = std::fs::read_dir(dir).ok()?;
    let mut files: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_file())
        .collect();
    if files.is_empty() {
        return None;
    }
    let expected = expected_exe_name();
    if let Some(hit) = files
        .iter()
        .find(|path| file_name_lossy(path).as_str() == expected)
    {
        return Some(hit.clone());
    }
    files.sort();
    files.into_iter().next()
}

/// 回滚默认取**最新的、且名字对得上**的备份 ✓。
fn pick_latest_backup_for(backup_root: &Path, exe_name: &str) -> Result<PathBuf, String> {
    let backups = list_backups(backup_root);
    if backups.is_empty() {
        return Err(format!(
            "还没有任何备份（{}）—— 只有做过一次 update apply 之后才有可回滚的版本",
            backup_root.display()
        ));
    }
    if let Some((_, path)) = backups
        .iter()
        .find(|(_, path)| file_name_lossy(path) == exe_name)
    {
        return Ok(path.clone());
    }
    let versions: Vec<String> = backups.iter().map(|(version, _)| version.clone()).collect();
    Err(format!(
        "备份里没有「{}」这个程序（现有版本：{}）—— 不能拿别的程序顶替",
        exe_name,
        versions.join(" / ")
    ))
}

/// 安装包解出来的新程序临时路径：`<系统临时目录>/sfl-update-<pid>-<seq>/<exe 名>` ✓
/// （**绝不写用户数据目录** ✓；同一次安装的临时目录用完即删 ✓）。
fn staged_temp_path(exe_name: &str) -> PathBuf {
    let seq = NEXT_SWAP_SEQ.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir()
        .join(format!("sfl-update-{}-{}", std::process::id(), seq))
        .join(exe_name)
}

/// 把安装包里取出来的 exe 写进临时路径（先建目录 ✓）。
fn write_staged(path: &Path, data: &[u8]) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| {
            format!("建临时目录 {} 失败：{}", parent.display(), error)
        })?;
    }
    std::fs::write(path, data)
        .map_err(|error| format!("写临时文件 {} 失败：{}", path.display(), error))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::zipwriter::{build, ZipEntry};
    use std::time::{Duration, UNIX_EPOCH};

    fn at(secs: u64) -> std::time::SystemTime {
        UNIX_EPOCH + Duration::from_secs(secs)
    }

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sfl-apply-{}-{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// 一眼假的「可执行文件」：只要求平台魔数对（**绝不执行它** ✓）。
    fn fake_exe(tag: &[u8]) -> Vec<u8> {
        let mut bytes = match Os::current() {
            Os::Windows => b"MZ".to_vec(),
            Os::Linux => vec![0x7f, b'E', b'L', b'F'],
            Os::MacOs => vec![0xCF, 0xFA, 0xED, 0xFE],
        };
        bytes.extend_from_slice(b" demo-exe ");
        bytes.extend_from_slice(tag);
        bytes
    }

    fn write_staged_file(dir: &Path, bytes: &[u8]) -> PathBuf {
        std::fs::create_dir_all(dir).unwrap();
        let path = dir.join(format!("staged-{}", std::process::id()));
        std::fs::write(&path, bytes).unwrap();
        path
    }

    fn entry(name: &str, data: impl AsRef<[u8]>) -> ZipReadEntry {
        ZipReadEntry { name: name.to_string(), data: data.as_ref().to_vec() }
    }

    fn package_zip(exe_name: &str, exe_bytes: &[u8], version: &str) -> Vec<u8> {
        let readme = format!("星尘闪连 {} · 预览版（test-target）\n", version);
        let exe_path = format!("staging/{}", exe_name);
        let entries = [
            ZipEntry { name: "staging/README.txt", data: readme.as_bytes() },
            ZipEntry { name: &exe_path, data: exe_bytes },
        ];
        build(&entries, at(0)).unwrap()
    }

    fn dir_names(dir: &Path) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(dir)
            .unwrap()
            .flatten()
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    }

    #[test]
    fn expected_names_match_ci_products_and_are_safe() {
        assert_eq!(expected_exe_name_for(Os::Windows), "stardust-flash-link-app.exe");
        assert_eq!(expected_exe_name_for(Os::Linux), "stardust-flash-link-app");
        assert_eq!(expected_exe_name_for(Os::MacOs), "stardust-flash-link-app");
        assert_eq!(expected_exe_name(), expected_exe_name_for(Os::current()));
        assert!(is_safe_exe_name("stardust-flash-link-app.exe"));
        assert!(is_safe_exe_name(CLI_EXE_NAME));
        assert!(!is_safe_exe_name("../stardust-flash-link"));
        assert!(!is_safe_exe_name("a/b"));
        assert!(!is_safe_exe_name(""));
    }

    #[test]
    fn exe_magic_is_checked_per_platform() {
        assert!(exe_magic_ok(b"MZ\x90\x00", Os::Windows));
        assert!(!exe_magic_ok(b"\x7fELF", Os::Windows), "ELF 不是 Windows 程序 ✗");
        assert!(exe_magic_ok(&[0x7f, b'E', b'L', b'F', 0x02, 0x01], Os::Linux));
        assert!(!exe_magic_ok(b"MZ", Os::Linux));
        // Mach-O 的两种落盘字节序 + 通用二进制都要认 ✓
        for magic in [
            [0xFEu8, 0xED, 0xFA, 0xCF],
            [0xCF, 0xFA, 0xED, 0xFE],
            [0xCE, 0xFA, 0xED, 0xFE],
            [0xCA, 0xFE, 0xBA, 0xBE],
        ] {
            assert!(exe_magic_ok(&magic, Os::MacOs), "Mach-O 魔数 {:02x?} 应被认出", magic);
        }
        assert!(!exe_magic_ok(b"MZ", Os::MacOs));
        // 空 / 短字节：一律 false（不 panic ✓）
        assert!(!exe_magic_ok(&[], Os::Windows));
        assert!(!exe_magic_ok(b"M", Os::Windows));
        assert!(!exe_magic_ok(b"garbage", Os::current()));
    }

    #[test]
    fn pick_exe_entry_allows_one_directory_and_requires_uniqueness() {
        let name = expected_exe_name();
        let entries = vec![entry(&format!("staging/{}", name), b"exe")];
        assert_eq!(pick_exe_entry(&entries, name).unwrap().data, b"exe");
        // 根目录直接放也认 ✓
        let flat = vec![entry(name, b"flat")];
        assert_eq!(pick_exe_entry(&flat, name).unwrap().data, b"flat");
        // 两层目录不认 ✗
        let deep = vec![entry(&format!("a/b/{}", name), b"deep")];
        let err = pick_exe_entry(&deep, name).unwrap_err();
        assert!(err.contains("没有"), "{}", err);
        assert!(err.contains("一层目录"), "{}", err);
        // 多份 → 不敢猜 ✗
        let dup = vec![entry(&format!("staging/{}", name), b"a"), entry(name, b"b")];
        let err = pick_exe_entry(&dup, name).unwrap_err();
        assert!(err.contains("多份") || err.contains("不敢猜"), "{}", err);
        // 不安全的名字 → 直接拒绝 ✓
        assert!(pick_exe_entry(&entries, "../evil").is_err());
    }

    #[test]
    fn package_version_hint_reads_our_own_manifests() {
        let readme = entry("staging/README.txt", "星尘闪连 3.0.0.0-preview.3 · 预览版（windows-x86_64）");
        assert_eq!(
            package_version_hint(&[readme]).unwrap().to_string_full(),
            "3.0.0.0-preview.3"
        );
        let control = entry("staging/deb-control", b"Package: x\nVersion: 2.9.9.9-rc.2\n");
        assert_eq!(package_version_hint(&[control]).unwrap().to_string_full(), "2.9.9.9-rc.2");
        // 没有可读版本 → None（调用方显示「未知版本」✓）
        assert!(package_version_hint(&[entry("a.txt", b"hello")]).is_none());
        assert!(package_version_hint(&[]).is_none());
    }

    #[test]
    fn plan_apply_lists_the_rename_dance_and_paths() {
        let install = Path::new("install");
        let plan = plan_apply(
            &install.join("stardust-flash-link-app"),
            Path::new("tmp/staged-app"),
            Path::new("install/backup"),
            "3.0.0.0-preview.2",
            "3.0.0.0-preview.3",
        );
        assert_eq!(plan.backup_exe, PathBuf::from("install/backup/3.0.0.0-preview.2/stardust-flash-link-app"));
        assert_eq!(plan.current_version, "3.0.0.0-preview.2");
        assert_eq!(plan.target_version, "3.0.0.0-preview.3");
        let text = plan.steps.join("\n");
        assert!(text.contains("备份"), "{}", text);
        assert!(text.contains("改名"), "{}", text);
        assert!(text.contains("回滚"), "{}", text);
        assert!(text.contains("--no-restart"), "{}", text);
        // 临时名必须与正式名同目录（rename 才是原子的 ✓）
        assert_eq!(plan.incoming_exe.parent(), Some(install));
        assert_eq!(plan.old_exe.parent(), Some(install));
        // 版本号不能当目录名 → 退回 unknown（不 panic ✓）
        let weird = plan_apply(Path::new("install/app"), Path::new("x"), Path::new("b"), "../evil", "v");
        assert!(weird.backup_exe.to_string_lossy().contains("unknown"));
    }

    #[test]
    fn apply_replace_swaps_backs_up_and_cleans_up() {
        let dir = temp_dir("apply-ok");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let staged = write_staged_file(&dir.join("staging"), &fake_exe(b"NEW"));
        let backup_root = dir.join(BACKUP_DIR_NAME);

        let outcome = apply_replace(
            &installed,
            &staged,
            &backup_root,
            "3.0.0.0-preview.2",
            "3.0.0.0-preview.3",
            false,
        )
        .expect("替换必须成功");
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"NEW"), "正式名必须变成新程序 ✓");
        assert_eq!(
            std::fs::read(&outcome.backup).unwrap(),
            fake_exe(b"OLD"),
            "备份内容必须逐字节等于旧程序 ✓"
        );
        assert!(
            outcome.backup.ends_with(format!("3.0.0.0-preview.2/{}", exe_name)),
            "备份布局: {}",
            outcome.backup.display()
        );
        assert!(outcome.stale_old.exists(), "旧程序先被改名留着 ✓");
        assert_eq!(std::fs::read(&outcome.stale_old).unwrap(), fake_exe(b"OLD"));

        // 启动清理：非运行中的 .old / .incoming 都要清掉 ✓
        let warnings = cleanup_stale_files(&dir, exe_name);
        assert!(warnings.is_empty(), "测试环境里不该删不掉: {:?}", warnings);
        assert!(!outcome.stale_old.exists());
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"NEW"), "清理不许碰正式名 ✓");
        assert!(outcome.backup.is_file(), "清理不许碰备份 ✓");
        let leftovers: Vec<String> = dir_names(&dir)
            .into_iter()
            .filter(|name| name.contains(".old-") || name.contains(".incoming-") || name.contains(".rollback-"))
            .collect();
        assert!(leftovers.is_empty(), "不该留下临时文件: {:?}", leftovers);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn dry_run_touches_nothing() {
        let dir = temp_dir("apply-dry");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let staged = write_staged_file(&dir.join("staging"), &fake_exe(b"NEW"));
        let backup_root = dir.join(BACKUP_DIR_NAME);
        let before = dir_names(&dir);

        let outcome = apply_replace(
            &installed,
            &staged,
            &backup_root,
            "3.0.0.0-preview.2",
            "3.0.0.0-preview.3",
            true,
        )
        .expect("dry-run 也要通过只读校验");
        assert!(outcome.dry_run);
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"OLD"), "dry-run 不许动程序 ✗");
        assert!(!backup_root.exists(), "dry-run 不许创建备份目录 ✗");
        assert_eq!(dir_names(&dir), before, "dry-run 不许产生任何新文件 ✗");
        assert_eq!(outcome.backup, backup_root.join("3.0.0.0-preview.2").join(exe_name));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn bad_magic_and_missing_staged_file_are_refused() {
        let dir = temp_dir("apply-bad");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let backup_root = dir.join(BACKUP_DIR_NAME);

        // ① 魔数不对 → 拒绝，且什么都没动 ✓
        let garbage = write_staged_file(&dir.join("staging"), b"not an executable at all");
        let err = apply_replace(&installed, &garbage, &backup_root, "v1", "v2", false).unwrap_err();
        assert!(err.contains("魔数"), "{}", err);
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"OLD"));
        assert!(!backup_root.exists());

        // ② 新程序不存在 → 可读错误，没动盘 ✓
        let missing = dir.join("staging").join("no-such-staged");
        let err = apply_replace(&installed, &missing, &backup_root, "v1", "v2", false).unwrap_err();
        assert!(err.contains("找不到新程序"), "{}", err);
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"OLD"));

        // ③ 空的新程序 → 拒绝 ✓
        let empty = write_staged_file(&dir.join("staging"), b"");
        let err = apply_replace(&installed, &empty, &backup_root, "v1", "v2", false).unwrap_err();
        assert!(err.contains("空文件"), "{}", err);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn failed_commit_restores_the_old_program() {
        let dir = temp_dir("apply-rollback-swap");
        let installed = dir.join("app.bin");
        std::fs::write(&installed, b"OLD-PROGRAM").unwrap();
        let old = dir.join("app.bin.old-1-1");
        let missing_incoming = dir.join("no-such-incoming");

        // 第 ④ 步成功、第 ⑤ 步失败 → 必须把旧程序改回原名 ✓
        let err = swap_incoming_into_place(&missing_incoming, &installed, &old).unwrap_err();
        assert!(err.contains("已回滚"), "{}", err);
        assert_eq!(std::fs::read(&installed).unwrap(), b"OLD-PROGRAM", "旧程序必须回到原名 ✓");
        assert!(!old.exists(), "回滚后不该留下 .old ✗");

        // 第 ④ 步就失败（当前程序不存在）→ 报「程序没有被改动」✓
        let ghost = dir.join("ghost.bin");
        let err = swap_incoming_into_place(&missing_incoming, &ghost, &old).unwrap_err();
        assert!(err.contains("没有被改动"), "{}", err);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn backups_are_listed_newest_first_and_rollback_restores_bytes() {
        let dir = temp_dir("rollback");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let backup_root = dir.join(BACKUP_DIR_NAME);
        let staged = write_staged_file(&dir.join("staging"), &fake_exe(b"NEW"));
        apply_replace(
            &installed,
            &staged,
            &backup_root,
            "3.0.0.0-preview.2",
            "3.0.0.0-preview.3",
            false,
        )
        .unwrap();

        // 再手工造一份更旧的备份（用来验证「按版本新旧排序」✓）
        let older = backup_exe_path(&backup_root, "3.0.0.0-preview.1", &exe_name).unwrap();
        std::fs::create_dir_all(older.parent().unwrap()).unwrap();
        std::fs::write(&older, fake_exe(b"ANCIENT")).unwrap();
        let backups = list_backups(&backup_root);
        assert_eq!(backups.len(), 2, "{:?}", backups);
        assert_eq!(backups[0].0, "3.0.0.0-preview.2", "新的必须在前: {:?}", backups);
        assert_eq!(backups[1].0, "3.0.0.0-preview.1");
        assert!(backups[0].1.ends_with(exe_name));

        // 回滚到最新备份 → 程序回到旧字节 ✓
        let outcome =
            rollback_replace(&dir, exe_name, &backup_root, None, false).expect("回滚必须成功");
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"OLD"), "回滚内容要逐字节正确 ✓");
        assert_eq!(std::fs::read(&outcome.restored_from).unwrap(), fake_exe(b"OLD"));
        assert!(outcome.stale_old.exists(), "被换下来的新程序留作待清理 ✓");
        assert_eq!(std::fs::read(&outcome.stale_old).unwrap(), fake_exe(b"NEW"));
        let warnings = cleanup_stale_files(&dir, exe_name);
        assert!(warnings.is_empty(), "{:?}", warnings);

        // 回滚到指定版本 → 拿到 ANCIENT ✓
        let outcome = rollback_replace(
            &dir,
            exe_name,
            &backup_root,
            Some("3.0.0.0-preview.1"),
            false,
        )
        .unwrap();
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"ANCIENT"));
        assert!(outcome.restored_from.ends_with("3.0.0.0-preview.1/".to_string() + exe_name));
        let _ = cleanup_stale_files(&dir, exe_name);

        // 指定不存在的版本 → 可读错误（不动盘 ✓）
        let before = std::fs::read(&installed).unwrap();
        let err = rollback_replace(&dir, exe_name, &backup_root, Some("9.9.9.9"), false).unwrap_err();
        assert!(err.contains("没有找到"), "{}", err);
        assert!(err.contains("update backups"), "{}", err);
        assert_eq!(std::fs::read(&installed).unwrap(), before);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rollback_dry_run_touches_nothing() {
        let dir = temp_dir("rollback-dry");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"CURRENT")).unwrap();
        let backup_root = dir.join(BACKUP_DIR_NAME);
        let backup = backup_exe_path(&backup_root, "3.0.0.0-preview.2", &exe_name).unwrap();
        std::fs::create_dir_all(backup.parent().unwrap()).unwrap();
        std::fs::write(&backup, fake_exe(b"BACKUP")).unwrap();
        let before = dir_names(&dir);

        let outcome = rollback_replace(&dir, exe_name, &backup_root, None, true).unwrap();
        assert!(outcome.dry_run);
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"CURRENT"));
        assert_eq!(dir_names(&dir), before, "dry-run 不许产生新文件 ✗");

        // 计划里要有可读步骤 ✓
        let plan = plan_rollback(&installed, &backup_root, None).unwrap();
        let text = plan.steps.join("\n");
        assert!(text.contains("校验备份"), "{}", text);
        assert!(text.contains("回滚"), "{}", text);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn cleanup_only_touches_our_stale_siblings() {
        let dir = temp_dir("cleanup");
        let exe_name = expected_exe_name();
        let stale = [
            dir.join(format!("{}.old-1-1", exe_name)),
            dir.join(format!("{}.incoming-1-2", exe_name)),
            dir.join(format!("{}.rollback-1-3", exe_name)),
        ];
        for path in &stale {
            std::fs::write(path, b"stale").unwrap();
        }
        let keep = dir.join(exe_name);
        std::fs::write(&keep, b"current").unwrap();
        let unrelated = dir.join("README.txt");
        std::fs::write(&unrelated, b"keep me").unwrap();
        let subdir = dir.join(format!("{}.old-dir-target", exe_name));
        std::fs::create_dir_all(&subdir).unwrap();

        let warnings = cleanup_stale_files(&dir, exe_name);
        assert!(warnings.is_empty(), "{:?}", warnings);
        for path in &stale {
            assert!(!path.exists(), "旧文件必须被清掉: {}", path.display());
        }
        assert!(keep.is_file(), "正式程序不许碰 ✗");
        assert!(unrelated.is_file(), "无关文件不许碰 ✗");
        assert!(subdir.is_dir(), "目录不许碰 ✗");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_zip_end_to_end_offline() {
        let dir = temp_dir("install-zip");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let backup_root = dir.join(BACKUP_DIR_NAME);
        let zip = package_zip(exe_name, &fake_exe(b"NEW"), "3.0.0.0-preview.3");

        let outcome = install_zip(
            &installed,
            &zip,
            &backup_root,
            "3.0.0.0-preview.2",
            None,
            false,
        )
        .expect("本地安装包必须能装上");
        assert!(!outcome.dry_run);
        assert_eq!(outcome.target_version, "3.0.0.0-preview.3", "版本要从 README.txt 读出来 ✓");
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"NEW"));
        let backup = backup_root.join("3.0.0.0-preview.2").join(exe_name);
        assert_eq!(std::fs::read(&backup).unwrap(), fake_exe(b"OLD"), "备份内容要正确 ✓");
        assert!(outcome.warnings.is_empty(), "{:?}", outcome.warnings);
        let leftovers: Vec<String> = dir_names(&dir)
            .into_iter()
            .filter(|name| name.contains(".old-") || name.contains(".incoming-"))
            .collect();
        assert!(leftovers.is_empty(), "install_zip 自己会清掉旧文件: {:?}", leftovers);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_zip_dry_run_writes_nothing() {
        let dir = temp_dir("install-zip-dry");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let backup_root = dir.join(BACKUP_DIR_NAME);
        let zip = package_zip(exe_name, &fake_exe(b"NEW"), "3.0.0.0-preview.3");
        let before = dir_names(&dir);

        let outcome = install_zip(
            &installed,
            &zip,
            &backup_root,
            "3.0.0.0-preview.2",
            Some("3.0.0.0-preview.3"),
            true,
        )
        .unwrap();
        assert!(outcome.dry_run);
        assert_eq!(outcome.target_version, "3.0.0.0-preview.3");
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"OLD"));
        assert!(!backup_root.exists());
        assert_eq!(dir_names(&dir), before, "dry-run 不许动盘 ✗");
        assert!(outcome.plan.steps.len() >= 5);
        assert_eq!(outcome.plan.staged_exe.parent().unwrap().parent(), Some(std::env::temp_dir().as_path()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_zip_refuses_unsafe_or_wrong_packages() {
        let dir = temp_dir("install-zip-bad");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let backup_root = dir.join(BACKUP_DIR_NAME);

        // ① 路径穿越条目 → zipread 拒绝（可读中文）✓
        let evil_path = format!("../{}", exe_name);
        let evil = build(
            &[ZipEntry { name: &evil_path, data: &fake_exe(b"EVIL") }],
            at(0),
        )
        .unwrap();
        let err = install_zip(&installed, &evil, &backup_root, "v1", None, false).unwrap_err();
        assert!(err.contains("穿越") || err.contains("路径"), "{}", err);

        // ② 包里有 exe 但魔数不对（下错平台）→ 拒绝 ✓
        let wrong = package_zip(exe_name, b"this is not an executable", "3.0.0.0-preview.3");
        let err = install_zip(&installed, &wrong, &backup_root, "v1", None, false).unwrap_err();
        assert!(err.contains("魔数"), "{}", err);

        // ③ 包里根本没有这个程序 → 拒绝 ✓
        let missing = build(
            &[ZipEntry { name: "staging/README.txt", data: b"hi" }],
            at(0),
        )
        .unwrap();
        let err = install_zip(&installed, &missing, &backup_root, "v1", None, false).unwrap_err();
        assert!(err.contains("没有"), "{}", err);

        // ④ 同一个程序在包里出现两遍 → 拒绝（不敢猜）✓
        let exe_path = format!("staging/{}", exe_name);
        let dup = build(
            &[
                ZipEntry { name: &exe_path, data: &fake_exe(b"A") },
                ZipEntry { name: &exe_path, data: &fake_exe(b"B") },
            ],
            at(0),
        )
        .unwrap();
        let err = install_zip(&installed, &dup, &backup_root, "v1", None, false).unwrap_err();
        assert!(err.contains("多份") || err.contains("不敢猜"), "{}", err);

        // 以上失败都不许动当前程序 / 不产生备份 ✓
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"OLD"));
        assert!(!backup_root.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn defaults_and_restart_errors_are_readable() {
        // 默认安装程序：一定是个真实存在的文件（GUI 或 CLI 自身 ✓）
        let installed = default_installed_exe().expect("单测进程自己一定存在");
        assert!(installed.is_file(), "{}", installed.display());
        let current = std::env::current_exe().unwrap();
        assert!(
            installed == current || file_name_lossy(&installed) == expected_exe_name(),
            "默认要更新 GUI 程序；没有它时退回当前 exe 自身: {}",
            installed.display()
        );
        // 默认备份根 = 安装目录/backup ✓
        let backup_root = default_backup_root(&installed);
        assert_eq!(backup_root.file_name().unwrap(), BACKUP_DIR_NAME);
        assert_eq!(backup_root.parent(), installed.parent());
        // 拉起一个不存在的程序 → 可读中文错误，不 panic ✓
        let missing = std::env::temp_dir().join("sfl-no-such-new-exe");
        let err = restart_detached(&missing).unwrap_err();
        assert!(err.contains("不存在"), "{}", err);
        assert!(err.contains("手动启动"), "{}", err);
        // 版本段不能当目录名 ✓
        assert!(backup_exe_path(Path::new("b"), "../evil", "app").is_err());
        assert!(backup_exe_path(Path::new("b"), "3.0.0.0-preview.2", "../app").is_err());
        assert!(safe_version_segment("3.0.0.0-preview.2").is_ok());
        assert!(safe_version_segment("a/b").is_err());
        assert!(safe_version_segment("").is_err());
    }

    #[test]
    fn apply_plan_refuses_tampered_temporary_paths() {
        let dir = temp_dir("apply-tamper");
        let exe_name = expected_exe_name();
        let installed = dir.join(exe_name);
        std::fs::write(&installed, fake_exe(b"OLD")).unwrap();
        let staged = write_staged_file(&dir.join("staging"), &fake_exe(b"NEW"));
        let mut plan = plan_apply(
            &installed,
            &staged,
            &dir.join(BACKUP_DIR_NAME),
            "3.0.0.0-preview.2",
            "3.0.0.0-preview.3",
        );
        // 有人把临时名改到了别的目录 → 跨盘 rename 不原子，必须拒绝 ✓
        plan.incoming_exe = std::env::temp_dir().join("elsewhere-incoming");
        let err = apply_plan(&plan, false).unwrap_err();
        assert!(err.contains("内部错误"), "{}", err);
        assert_eq!(std::fs::read(&installed).unwrap(), fake_exe(b"OLD"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
