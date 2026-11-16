//! 2.x「DrcomAutoLogin」数据的**只读兼容层**（审计 P1-3 / R3「老用户零迁移」）。
//!
//! 背景：3.0 的数据目录是 `%ProgramData%\StardustFlashLink`，2.x 却把 `config.json` /
//! `password.txt` 留在安装目录（默认 `%ProgramFiles%\DrcomAutoLogin`）。接管 2.x 服务之后，
//! 3.0 读不到账号/密码 → 每轮 `[skip] 账号未设置`，**看起来在跑其实什么都没做**，
//! 原本正常的机器静默掉线 ✗（审计 P1-3）。
//!
//! 这里只做**最小、只读**的兼容：
//!   1. 3.0 自己的文件**存在就用它**（不会因为 2.x 有数据就改口径 ✓）；
//!   2. 自己的文件**不存在**时才看 2.x 目录；读到就写一条中文日志说明「只读兼容载入」✓；
//!   3. **绝不写回 / 移动 / 删除 / 改名 2.x 的文件** ✗ —— 那是 2.x 的数据（R3）；
//!      所以 legacy 的 `config.json` 一律用 [`Config::load_readonly`] 读（坏了也不备份 ✓）。
//!
//! 平台差异只有一处：[`crate::platform::legacy_data_dir`]（**Windows 之外恒 `None`** ✓，R2）。

use crate::config::Config;
use crate::password;
use crate::platform;
use std::path::{Path, PathBuf};
use std::sync::Once;

/// 2.x 目录里的配置文件名（与 3.0 / 2.x 同名同格式 ✓，R3）。
pub const CONFIG_FILE_NAME: &str = "config.json";
/// 2.x 目录里的密码文件名（一行一密码、提示行语义与 2.x 一致 ✓，见 [`crate::password`]）。
pub const PASSWORD_FILE_NAME: &str = "password.txt";

/// 数据来源 —— 界面 / `/api/status` / 诊断包都要能说清「这份配置是哪来的」✓。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LoadedFrom {
    /// 3.0 自己的数据目录（正常情况 ✓）
    Own,
    /// 2.x 安装目录（**只读**兼容载入 ✓）
    Legacy,
    /// 两边都没有 → 默认配置 / 空密码（首次运行 ✓）
    Default,
}

impl LoadedFrom {
    /// 机器可读的来源标识（写进状态 JSON ✓）。
    pub fn as_str(self) -> &'static str {
        match self {
            LoadedFrom::Own => "own",
            LoadedFrom::Legacy => "legacy",
            LoadedFrom::Default => "default",
        }
    }

    /// 给界面 / 日志看的中文（**必须说清「只读」** ✓，别让用户以为已经迁移了 ✗）。
    pub fn label_cn(self) -> &'static str {
        match self {
            LoadedFrom::Own => "3.0 数据目录",
            LoadedFrom::Legacy => "2.x 安装目录（只读兼容）",
            LoadedFrom::Default => "默认值（还没初始化）",
        }
    }

    pub fn is_legacy(self) -> bool {
        matches!(self, LoadedFrom::Legacy)
    }
}

/// 一次「读配置」的结果（带来源 ✓）。
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigRead {
    pub config: Config,
    pub from: LoadedFrom,
    /// 实际读的那个文件（`Default` 时为 `None` ✓）
    pub path: Option<PathBuf>,
}

impl ConfigRead {
    pub fn is_legacy(&self) -> bool {
        self.from.is_legacy()
    }

    /// 来自 2.x 时给人看的一句中文（其它来源是空串 ✓，界面可以直接拼到状态行后面 ✓）。
    pub fn legacy_hint(&self) -> String {
        match (self.from, self.path.as_deref()) {
            (LoadedFrom::Legacy, Some(path)) => {
                format!("来自 2.x 安装目录（只读兼容）：{}", path.display())
            }
            _ => String::new(),
        }
    }
}

/// 一次「读密码」的结果（**只多一个来源，不含任何其它元信息** ✓ —— 密码永不进日志/状态 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PasswordRead {
    pub password: String,
    pub from: LoadedFrom,
    pub path: Option<PathBuf>,
}

impl PasswordRead {
    pub fn is_legacy(&self) -> bool {
        self.from.is_legacy()
    }
}

/// 2.x 目录里的 `config.json` 路径（纯函数 ✓）。
pub fn legacy_config_path_in(dir: &Path) -> PathBuf {
    dir.join(CONFIG_FILE_NAME)
}

/// 2.x 目录里的 `password.txt` 路径（纯函数 ✓）。
pub fn legacy_password_path_in(dir: &Path) -> PathBuf {
    dir.join(PASSWORD_FILE_NAME)
}

/// **纯函数**：读配置，规则是「自己的文件优先 → 2.x 只读回退 → 默认值」。
///
/// 关键边界（都有单测锁死 ✓）：
///   - 自己的文件**存在**时永远读自己的；哪怕内容是坏的也返回 `Err`，
///     让 P2-1 的「先备份再回落」逻辑生效，**绝不**偷偷换成 2.x 的配置 ✗；
///   - 自己的文件不存在 + 2.x 有 `config.json` → 只读解析并标 [`LoadedFrom::Legacy`] ✓；
///   - 两边都没有 → [`Config::default`] + [`LoadedFrom::Default`]（首次运行 ✓）。
pub fn load_config_from(own: &Path, legacy_dir: Option<&Path>) -> Result<ConfigRead, String> {
    if own.exists() {
        let config = Config::load(own)?;
        return Ok(ConfigRead {
            config,
            from: LoadedFrom::Own,
            path: Some(own.to_path_buf()),
        });
    }
    if let Some(dir) = legacy_dir {
        let legacy = legacy_config_path_in(dir);
        if legacy.exists() {
            // 严格只读：坏文件也不备份 / 不改名（2.x 的数据一个字节都不许动 ✗）
            let config = Config::load_readonly(&legacy)?;
            return Ok(ConfigRead {
                config,
                from: LoadedFrom::Legacy,
                path: Some(legacy),
            });
        }
    }
    Ok(ConfigRead {
        config: Config::default(),
        from: LoadedFrom::Default,
        path: None,
    })
}

/// **纯函数**：读密码，规则同上（自己的文件优先 → 2.x 只读回退 → 空 = 未设置 ✓）。
///
/// 注意：判断「有没有密码文件」用**文件存在性**，不用「读出来是否为空」——
/// 用户把 3.0 的密码文件清空是明确的「我要清掉密码」✓，那时不该再翻 2.x 的旧密码 ✗。
pub fn read_password_from(own: &Path, legacy_dir: Option<&Path>) -> PasswordRead {
    if own.exists() {
        return PasswordRead {
            password: password::read_from(own),
            from: LoadedFrom::Own,
            path: Some(own.to_path_buf()),
        };
    }
    if let Some(dir) = legacy_dir {
        let legacy = legacy_password_path_in(dir);
        if legacy.exists() {
            return PasswordRead {
                password: password::read_from(&legacy),
                from: LoadedFrom::Legacy,
                path: Some(legacy),
            };
        }
    }
    PasswordRead {
        password: String::new(),
        from: LoadedFrom::Default,
        path: None,
    }
}

/// 运行时入口：读配置（带 2.x 只读回退）—— **GUI / CLI 统一走它** ✓。
pub fn load_config() -> Result<ConfigRead, String> {
    let read = load_config_from(&platform::config_path(), platform::legacy_data_dir().as_deref())?;
    if read.is_legacy() {
        announce_legacy_once(read.path.as_deref());
    }
    Ok(read)
}

/// 运行时入口：`unwrap_or_default()` 那批调用点用的便捷版（失败 → 默认值 ✓）。
pub fn load_config_or_default() -> ConfigRead {
    load_config().unwrap_or(ConfigRead {
        config: Config::default(),
        from: LoadedFrom::Default,
        path: None,
    })
}

/// 运行时入口：读密码（带 2.x 只读回退）✓。
pub fn read_password() -> PasswordRead {
    let read = read_password_from(
        &platform::password_path(),
        platform::legacy_data_dir().as_deref(),
    );
    if read.is_legacy() {
        announce_legacy_once(read.path.as_deref());
    }
    read
}

/// **纯函数**：命中 2.x 时写进日志的那句中文（文案本身也有单测 ✓）。
///
/// 三个要点必须写清：**只读兼容**、**2.x 的文件不会被改**、**要迁移得用户自己复制** ✓。
pub fn notice_text(legacy_path: &Path, data_dir: &Path) -> String {
    format!(
        "检测到 2.x 安装目录的数据（{}），按只读兼容方式载入；如需迁移请手工把 config.json / password.txt 复制到 3.0 数据目录（{}）——3.0 不会改动 2.x 的文件 ✓",
        legacy_path.display(),
        data_dir.display()
    )
}

static ANNOUNCE_ONCE: Once = Once::new();

/// 每个进程只提示一次（守护循环每轮都读配置，不能刷屏 ✗）。
fn announce_legacy_once(legacy: Option<&Path>) {
    let Some(legacy) = legacy else {
        return;
    };
    ANNOUNCE_ONCE.call_once(|| {
        eprintln!("[提示] {}", notice_text(legacy, &platform::data_dir()));
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sfl-legacy-{}-{}", tag, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write_config(dir: &Path, account: &str) {
        fs::write(
            legacy_config_path_in(dir),
            format!("{{\"host\":\"10.9.9.9\",\"account\":\"{}\",\"suffix\":\"@yd\"}}", account),
        )
        .unwrap();
    }

    /// 目录里所有条目名（排序；用来断言「没多出 .bad-* / tmp」✓）。
    fn entries(dir: &Path) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(dir)
            .map(|it| {
                it.filter_map(|entry| entry.ok())
                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
                    .collect()
            })
            .unwrap_or_default();
        names.sort();
        names
    }

    #[test]
    fn empty_own_dir_plus_legacy_config_reads_legacy_and_marks_source() {
        let own_dir = temp_dir("cfg-own-empty");
        let legacy_dir = temp_dir("cfg-legacy");
        write_config(&legacy_dir, "2023009999");
        let own = own_dir.join("config.json");
        let bytes_before = fs::read(legacy_config_path_in(&legacy_dir)).unwrap();

        let read = load_config_from(&own, Some(&legacy_dir)).expect("legacy 配置应当读得出来");
        assert_eq!(read.from, LoadedFrom::Legacy, "来源要标成 Legacy ✓");
        assert!(read.is_legacy());
        assert_eq!(read.config.account, "2023009999", "读到的是 2.x 的账号 ✓");
        assert_eq!(read.config.host, "10.9.9.9");
        assert_eq!(read.config.suffix, "@yd");
        assert_eq!(
            read.path.as_deref(),
            Some(legacy_config_path_in(&legacy_dir).as_path()),
            "结果里要能看出实际读的是 2.x 的那个文件 ✓"
        );
        assert!(read.legacy_hint().contains("2.x"), "界面提示要说明来源: {}", read.legacy_hint());
        assert!(!own.exists(), "只读兼容**不许**在 3.0 目录里写出配置文件 ✗");
        assert_eq!(
            fs::read(legacy_config_path_in(&legacy_dir)).unwrap(),
            bytes_before,
            "2.x 配置文件读前读后必须逐字节一致 ✓"
        );
        assert_eq!(
            entries(&legacy_dir),
            vec![CONFIG_FILE_NAME.to_string()],
            "legacy 目录里不许出现 .bad-* / tmp / 新文件 ✗: {:?}",
            entries(&legacy_dir)
        );
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn own_config_wins_even_when_legacy_exists() {
        let own_dir = temp_dir("cfg-own-wins");
        let legacy_dir = temp_dir("cfg-legacy-wins");
        write_config(&legacy_dir, "2023009999");
        let own = own_dir.join("config.json");
        fs::write(&own, "{\"account\":\"2023001234\",\"host\":\"10.1.1.1\"}").unwrap();
        let legacy_bytes = fs::read(legacy_config_path_in(&legacy_dir)).unwrap();

        let read = load_config_from(&own, Some(&legacy_dir)).expect("自己的配置要优先 ✓");
        assert_eq!(read.from, LoadedFrom::Own);
        assert!(!read.is_legacy());
        assert_eq!(read.config.account, "2023001234", "有 3.0 配置时**绝不**去读 2.x ✗");
        assert_eq!(read.config.host, "10.1.1.1");
        assert_eq!(read.path.as_deref(), Some(own.as_path()));
        assert!(read.legacy_hint().is_empty(), "来源不是 legacy 时不该有兼容提示 ✓");
        assert_eq!(fs::read(legacy_config_path_in(&legacy_dir)).unwrap(), legacy_bytes, "没读它也照样不许动它 ✓");
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn corrupt_own_config_is_not_silently_replaced_by_legacy() {
        let own_dir = temp_dir("cfg-own-corrupt");
        let legacy_dir = temp_dir("cfg-legacy-behind");
        write_config(&legacy_dir, "2023009999");
        let own = own_dir.join("config.json");
        let broken = "{\"port\": \"八十\"}";
        fs::write(&own, broken).unwrap();
        let legacy_bytes = fs::read(legacy_config_path_in(&legacy_dir)).unwrap();

        // 自己的文件**存在**但坏了：照旧返回 Err（P2-1 的备份/拒绝覆盖逻辑要能生效），
        // **绝不**悄悄换成 2.x 的配置 ✗ —— 否则用户会在不知情下被换一套账号/网关
        let err = load_config_from(&own, Some(&legacy_dir))
            .expect_err("自己的坏配置必须照旧报错（P2-1），不许拿 2.x 的顶上 ✗");
        assert!(err.contains("解析失败"), "错误要可读: {}", err);
        assert!(!own.exists(), "3.0 自己的坏文件按 P2-1 改名备份（这是 3.0 的数据 ✓）");
        assert_eq!(
            fs::read(legacy_config_path_in(&legacy_dir)).unwrap(),
            legacy_bytes,
            "2.x 的文件依旧一个字节都不许动 ✓"
        );
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn missing_everywhere_returns_defaults_not_an_error() {
        let own_dir = temp_dir("cfg-none");
        let own = own_dir.join("config.json");
        // ① 连 legacy 目录都没有 → 默认值 ✓
        let read = load_config_from(&own, None).expect("首次运行不该报错");
        assert_eq!(read.from, LoadedFrom::Default);
        assert_eq!(read.config, Config::default());
        assert!(read.path.is_none());
        // ② legacy 目录存在但里面没有 config.json → 还是默认值 ✓
        let legacy_dir = temp_dir("cfg-legacy-empty");
        let read = load_config_from(&own, Some(&legacy_dir)).expect("空目录不该报错");
        assert_eq!(read.from, LoadedFrom::Default);
        assert_eq!(read.config, Config::default());
        assert!(!own.exists(), "只读路径不许创建文件 ✗");
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn corrupt_legacy_config_is_reported_without_touching_the_file() {
        let own_dir = temp_dir("cfg-corrupt-own");
        let legacy_dir = temp_dir("cfg-corrupt-legacy");
        let own = own_dir.join("config.json");
        // 2.x 那边写坏的类型（3.0 强类型反序列化读不出来）
        let broken = "{\"host\":\"10.9.9.9\",\"port\":\"八十\"}";
        fs::write(legacy_config_path_in(&legacy_dir), broken).unwrap();

        let err = load_config_from(&own, Some(&legacy_dir)).expect_err("坏配置要报错，不能静默当成默认值 ✗");
        assert!(err.contains("解析失败"), "错误要可读: {}", err);
        assert_eq!(
            fs::read_to_string(legacy_config_path_in(&legacy_dir)).unwrap(),
            broken,
            "2.x 的坏配置**不许**被改名/备份/修改 ✗（那是 2.x 的数据）"
        );
        assert_eq!(entries(&legacy_dir), vec![CONFIG_FILE_NAME.to_string()], "legacy 目录里不许出现 config.json.bad-* ✗: {:?}", entries(&legacy_dir));
        assert!(!own.exists(), "读失败也不许在 3.0 目录里写东西 ✗");
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn legacy_password_is_read_readonly_when_own_missing() {
        let own_dir = temp_dir("pwd-own-empty");
        let legacy_dir = temp_dir("pwd-legacy");
        fs::write(legacy_password_path_in(&legacy_dir), "#Demo-Pwd-0000#\n").unwrap();
        let own = own_dir.join("password.txt");
        let bytes_before = fs::read(legacy_password_path_in(&legacy_dir)).unwrap();

        let read = read_password_from(&own, Some(&legacy_dir));
        assert_eq!(read.from, LoadedFrom::Legacy, "密码来源也要标 Legacy ✓");
        assert_eq!(read.password, "#Demo-Pwd-0000#", "# 开头的密码按原文读（P1-1 语义）✓");
        assert_eq!(read.path.as_deref(), Some(legacy_password_path_in(&legacy_dir).as_path()));
        assert!(!own.exists(), "只读兼容**不许**把密码复制进 3.0 目录 ✗");
        assert_eq!(fs::read(legacy_password_path_in(&legacy_dir)).unwrap(), bytes_before, "2.x 密码文件读前读后必须逐字节一致 ✓");
        // 空模板 + BOM 之类也照 2.x 语义处理 ✓
        fs::write(legacy_password_path_in(&legacy_dir), b"\xef\xbb\xbfpwd_with_bom\n").unwrap();
        assert_eq!(read_password_from(&own, Some(&legacy_dir)).password, "pwd_with_bom");
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn own_password_wins_over_legacy() {
        let own_dir = temp_dir("pwd-own-wins");
        let legacy_dir = temp_dir("pwd-legacy-wins");
        fs::write(legacy_password_path_in(&legacy_dir), "旧密码\n").unwrap();
        let own = own_dir.join("password.txt");
        fs::write(&own, "新密码\n").unwrap();
        let legacy_bytes = fs::read(legacy_password_path_in(&legacy_dir)).unwrap();

        let read = read_password_from(&own, Some(&legacy_dir));
        assert_eq!(read.from, LoadedFrom::Own);
        assert_eq!(read.password, "新密码");
        assert_eq!(fs::read(legacy_password_path_in(&legacy_dir)).unwrap(), legacy_bytes);
        // 3.0 的文件存在但为空（用户主动清空）→ 就是空，**不再翻 2.x 旧密码** ✗
        fs::write(&own, "").unwrap();
        let read = read_password_from(&own, Some(&legacy_dir));
        assert_eq!(read.from, LoadedFrom::Own);
        assert!(read.password.is_empty(), "用户清空 3.0 密码后不许把 2.x 旧密码翻出来 ✗");
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn missing_everywhere_means_empty_password_default() {
        let own_dir = temp_dir("pwd-none");
        let own = own_dir.join("password.txt");
        let read = read_password_from(&own, None);
        assert_eq!(read.from, LoadedFrom::Default);
        assert!(read.password.is_empty(), "没有密码文件 = 还没设密码 ✓");
        assert!(read.path.is_none());
        let legacy_dir = temp_dir("pwd-legacy-empty");
        let read = read_password_from(&own, Some(&legacy_dir));
        assert_eq!(read.from, LoadedFrom::Default);
        assert!(read.password.is_empty());
        assert!(!own.exists());
        let _ = fs::remove_dir_all(&own_dir);
        let _ = fs::remove_dir_all(&legacy_dir);
    }

    #[test]
    fn legacy_paths_are_plain_joins() {
        let dir = Path::new("C:/Program Files/DrcomAutoLogin");
        assert_eq!(legacy_config_path_in(dir), dir.join("config.json"));
        assert_eq!(legacy_password_path_in(dir), dir.join("password.txt"));
        assert_eq!(CONFIG_FILE_NAME, "config.json", "与 2.x 同名 ✓");
        assert_eq!(PASSWORD_FILE_NAME, "password.txt", "与 2.x 同名 ✓");
    }

    #[test]
    fn source_labels_and_notice_say_readonly() {
        assert_eq!(LoadedFrom::Own.as_str(), "own");
        assert_eq!(LoadedFrom::Legacy.as_str(), "legacy");
        assert_eq!(LoadedFrom::Default.as_str(), "default");
        assert!(LoadedFrom::Legacy.is_legacy());
        assert!(!LoadedFrom::Own.is_legacy(), "自己的配置不算 legacy ✓");
        assert!(LoadedFrom::Legacy.label_cn().contains("只读"), "界面文案必须写清只读 ✓");

        let notice = notice_text(
            Path::new("C:/Program Files/DrcomAutoLogin/config.json"),
            Path::new("C:/ProgramData/StardustFlashLink"),
        );
        assert!(notice.contains("2.x"), "{}", notice);
        assert!(notice.contains("只读兼容"), "日志要写明是只读兼容方式载入 ✓: {}", notice);
        assert!(notice.contains("不会改动 2.x 的文件"), "要明确不写回 2.x ✓: {}", notice);
        assert!(notice.contains("如需迁移请手工"), "要给出迁移办法（用户自己复制）✓: {}", notice);
        assert!(notice.contains("ProgramData"), "要指出 3.0 的数据目录 ✓: {}", notice);
    }
}
