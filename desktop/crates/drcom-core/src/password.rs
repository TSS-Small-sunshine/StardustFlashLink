//! 密码文件读写 —— 与 2.x **完全一致**的语义（R3 不变量）：
//!   - 纯文本，**一行一个密码**，只取**第一条有效行** ✓；
//!   - 空行跳过；**只有安装包模板提示行算注释**（2.x 的 `_PASSWORD_HINT_MARKERS` 那两行）✓ ——
//!     其它任何行都是密码原文，**包括以 `#` 开头的密码** ✓
//!     （2.x v2.0.4.0 修的真机回归：形如 `#xxxxxxxx#` 的密码曾被 `startswith("#")`
//!     整行吃掉 → 服务以为「密码未设置」，看起来就像「升级后密码丢了」✗）；
//!   - 按 `utf-8-sig` 的语义读：**开头的 BOM 要剥掉** ✓（2.x 有专门的 `pwd_with_bom` 夹具，
//!     否则密码首字符带 U+FEFF，网关判密码错 ✗）；
//!   - 文件不存在 / 只有空行与提示行 → 空串（表示「还没设密码」✓，**不报错** ✓）；
//!   - 写盘是**原子替换**（同目录唯一 tmp 名 → 写完 sync → rename ✓，见 [`crate::atomicfile`]）——
//!     固定 tmp 名会让两个写者交错写、落出半截文件 ✗（2.x v2.0.6.2 已修）。
//!
//! ⚠️ 「留空不修改」这条语义在 [`crate::settings`] 里（表单层 ✓），这里只管读写 ✓。
//! ⚠️ 3.0 的 `password.txt` **不存在**时，[`read`] 会**只读**回退 2.x 安装目录的那份
//!    （审计 P1-3；绝不写回 2.x ✓，见 [`crate::legacy`]）✓。
//! ⚠️ 读密码**只有这一份实现** ✓ —— CLI / GUI 都必须调 [`read`]（或测试用 [`read_from`]），
//!    不许再各自复制一份（3.0 早期三处复制正是 P1-1 语义漂移的来源 ✗）。

use crate::atomicfile;
use std::fs;
use std::path::{Path, PathBuf};

/// 安装包模板提示行的可识别片段 —— 与 2.x `_PASSWORD_HINT_MARKERS` **逐字相同** ✓
/// （改这里就等于改「什么算注释」，必须与 2.x 同步 ✓）。
///
/// 只有命中这些片段的行才被当成注释跳过；**绝不要**再用「`#` 开头」判断注释 ✗。
pub const HINT_MARKERS: [&str; 2] = ["在此行写入你的校园网账号密码", "去掉本注释行"];

/// 默认位置：数据目录下的 `password.txt` ✓
pub fn path() -> PathBuf {
    crate::platform::password_path()
}

/// 是否是安装包模板提示行（唯一会被当成注释跳过的行 ✓）。
pub fn is_hint(line: &str) -> bool {
    HINT_MARKERS.iter().any(|marker| line.contains(marker))
}

/// 剥掉文件**开头**的 UTF-8 BOM（等价 Python 的 `encoding="utf-8-sig"` ✓；只剥开头 ✓）。
pub fn strip_bom(text: &str) -> &str {
    text.strip_prefix('\u{feff}').unwrap_or(text)
}

/// 从整份文本里取第一条有效行（纯函数 ✓）：
/// 跳过空行与安装包模板提示行，**其余一律按密码原文返回**（`#` 开头也一样 ✓）。
pub fn first_effective_line(text: &str) -> String {
    strip_bom(text)
        .lines()
        .map(|line| line.trim())
        .find(|line| !line.is_empty() && !is_hint(line))
        .unwrap_or("")
        .to_string()
}

/// 文件里第一条有效内容（跳过空行与安装包模板提示行 ✓，剥开头的 BOM ✓）。
pub fn read_from(path: &Path) -> String {
    match fs::read_to_string(path) {
        Ok(text) => first_effective_line(&text),
        Err(_) => String::new(), // 不存在 = 还没设密码 ✓，不是错误 ✓
    }
}

/// 读默认位置的密码 ✓（**CLI / GUI 的唯一入口** ✓）。
///
/// 3.0 自己的 `password.txt` **不存在**时，会**只读**回退 2.x 安装目录的 `password.txt`
/// （R3「老用户零迁移」/ 审计 P1-3）；**绝不写回 / 移动 2.x 的文件** ✗。
/// 需要知道「这份密码是从哪读来的」（状态面板 / 日志）用 [`crate::legacy::read_password`] ✓。
pub fn read() -> String {
    crate::legacy::read_password().password
}

/// 写入（原子替换 ✓）。**拒绝写空密码** ✗ —— 那等于把用户密码清掉 ✓。
pub fn write_to(path: &Path, password: &str) -> Result<(), String> {
    if password.trim().is_empty() {
        return Err("拒绝写入空密码（要清空请直接删掉密码文件）".to_string());
    }
    if password.contains('\n') || password.contains('\r') {
        return Err("密码不能包含换行（一行一个密码 ✓）".to_string());
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("建不了目录 {}: {}", parent.display(), e))?;
    }
    // 同目录**唯一** tmp 名（pid + 纳秒 + 进程内序号 ✓）：并发写者不会互相踩 ✗
    let tmp = atomicfile::unique_tmp_path(path);
    atomicfile::write_tmp(&tmp, format!("{}\n", password).as_bytes())?;
    // 权限：Unix 上收紧到 600（Windows 忽略 ✓）
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600));
    }
    atomicfile::commit(&tmp, path)
}

/// 写默认位置 ✓
pub fn write(password: &str) -> Result<(), String> {
    write_to(&path(), password)
}

/// 首次创建时写的骨架：**只包含安装包模板提示行** ✓（与 2.x 同一套标记）
/// → 读出来是空串 ✓，不会被当成密码 ✗；2.x 读它同样得到「未设置」✓。
pub fn template() -> String {
    // 这行必须**只**由 HINT_MARKERS 里的片段构成：非提示行会被当密码原文读出来 ✗
    "# 在此行写入你的校园网账号密码，并去掉本注释行\n".to_string()
}

/// 是否已经设过密码 ✓
pub fn is_set() -> bool {
    !read().is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sfl-pwd-{}-{}", tag, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn tmp_leftovers(dir: &Path) -> Vec<String> {
        fs::read_dir(dir)
            .map(|entries| {
                entries
                    .filter_map(|entry| entry.ok())
                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
                    .filter(|name| name.ends_with(".tmp"))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// 并发读时要容忍 Windows 上 rename 瞬间的句柄占用：重试的是**占用**，不是内容 ✓。
    fn read_retry(path: &Path) -> String {
        let mut got = String::new();
        // 500 × 1ms：覆盖「写者线程刚启动」与 Windows rename 的瞬间占用 ✓
        for _ in 0..500 {
            got = read_from(path);
            if !got.is_empty() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        got
    }

    #[test]
    fn missing_file_is_empty_not_an_error() {
        let dir = temp_dir("missing");
        assert_eq!(read_from(&dir.join("password.txt")), "");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn template_only_has_installer_hint_lines_so_it_reads_as_empty() {
        let dir = temp_dir("template");
        let path = dir.join("password.txt");
        let text = template();
        // 模板里**不许**出现「不是提示行」的内容：那会被当成密码原文读出来 ✗
        assert!(
            text.lines()
                .all(|line| line.trim().is_empty() || is_hint(line)),
            "模板必须只由安装包提示行组成: {:?}",
            text
        );
        fs::write(&path, &text).unwrap();
        assert_eq!(read_from(&path), "", "模板不该被当成密码 ✓（2.x 的 P0-3 教训）");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn hash_prefixed_password_is_read_as_is_not_swallowed_as_comment() {
        // 2.x v2.0.4.0 的真机回归：密码本身以 # 开头（真机实测存在，形如 #xxxxxxxx#）
        let dir = temp_dir("hash-pwd");
        let path = dir.join("password.txt");
        fs::write(&path, "#Demo-Pwd-0000#\n").unwrap();
        assert_eq!(
            read_from(&path),
            "#Demo-Pwd-0000#",
            "以 # 开头的密码必须原样读出来 ✗（别再退回 startswith('#') 的写法）"
        );
        // 提示行后面的 # 开头密码也要能读出来 ✓
        fs::write(&path, "# 在此行写入你的校园网账号密码\n#Demo-Pwd-0000#\n").unwrap();
        assert_eq!(read_from(&path), "#Demo-Pwd-0000#");
        // 「去掉本注释行」也是提示行 ✓
        fs::write(&path, "# 去掉本注释行\nrealpass\n").unwrap();
        assert_eq!(read_from(&path), "realpass");
        // 2.x 口径：提示行**以外**的 # 行就是密码原文（不是注释）✓
        fs::write(&path, "# 随便写一句\n真密码\n").unwrap();
        assert_eq!(read_from(&path), "# 随便写一句");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn bom_is_stripped_like_pythons_utf8_sig() {
        let dir = temp_dir("bom");
        let path = dir.join("password.txt");
        // 2.x 夹具原文：write_bytes(b"\xef\xbb\xbfpwd_with_bom\n")
        fs::write(&path, b"\xef\xbb\xbfpwd_with_bom\n").unwrap();
        assert_eq!(
            read_from(&path),
            "pwd_with_bom",
            "BOM 不剥 → 密码首字符带 U+FEFF → 网关判密码错 ✗"
        );
        // BOM + # 开头的密码：两个修复要能叠加 ✓
        let mut bytes = b"\xef\xbb\xbf".to_vec();
        bytes.extend_from_slice("#Demo-Pwd-0000#\n".as_bytes());
        fs::write(&path, bytes).unwrap();
        assert_eq!(read_from(&path), "#Demo-Pwd-0000#");
        // 只剥开头：中间出现的 U+FEFF 不动 ✓
        assert_eq!(strip_bom("\u{feff}pwd"), "pwd");
        assert_eq!(strip_bom("pwd"), "pwd");
        assert_eq!(strip_bom("a\u{feff}b"), "a\u{feff}b");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn blank_lines_are_skipped_first_valid_line_wins() {
        let dir = temp_dir("blank");
        let path = dir.join("password.txt");
        fs::write(&path, "\n   \n第一条密码\n第二条密码\n").unwrap();
        assert_eq!(read_from(&path), "第一条密码");
        // 前后空白要被 trim 掉 ✓
        fs::write(&path, "  空格包起来的  \n").unwrap();
        assert_eq!(read_from(&path), "空格包起来的");
        // 纯空文本 → 空串 ✓
        assert_eq!(first_effective_line("\n \n"), "");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_is_atomic_and_round_trips() {
        let dir = temp_dir("write");
        let path = dir.join("password.txt");
        write_to(&path, "P@ss 词1+2").unwrap();
        assert_eq!(read_from(&path), "P@ss 词1+2");
        assert!(
            !path.with_extension("txt.tmp").exists(),
            "固定 tmp 名不许再用 ✗"
        );
        // 覆盖写 ✓
        write_to(&path, "新的").unwrap();
        assert_eq!(read_from(&path), "新的");
        assert!(
            tmp_leftovers(&dir).is_empty(),
            "不该留下 tmp 垃圾 ✗: {:?}",
            tmp_leftovers(&dir)
        );
        // # 开头的密码也能原样写回 / 读出 ✓
        write_to(&path, "#Demo-Pwd-0000#").unwrap();
        assert_eq!(read_from(&path), "#Demo-Pwd-0000#");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn concurrent_writes_never_leave_a_half_written_password() {
        let dir = temp_dir("concurrent");
        let path = dir.join("password.txt");
        // 两种长度差很大的密码：tmp 名固定时，两个写者交错写同一个 tmp 会 rename 出半截 ✓
        let long = "L".repeat(4096);
        let short = "S".to_string();
        let mut handles = Vec::new();
        for writer in 0..2 {
            let path = path.clone();
            let long = long.clone();
            let short = short.clone();
            handles.push(std::thread::spawn(move || {
                for round in 0..40 {
                    let payload = if (writer + round) % 2 == 0 { &long } else { &short };
                    write_to(&path, payload).unwrap();
                }
            }));
        }
        for _ in 0..100 {
            let got = read_retry(&path);
            assert!(
                got == long || got == short,
                "读到了半截密码 ✗（长度 {}，期望 {} 或 {}）",
                got.len(),
                long.len(),
                short.len()
            );
        }
        for handle in handles {
            handle.join().unwrap();
        }
        let final_pwd = read_from(&path);
        assert!(final_pwd == long || final_pwd == short);
        assert!(
            tmp_leftovers(&dir).is_empty(),
            "并发写之后不该留下 tmp 垃圾 ✗: {:?}",
            tmp_leftovers(&dir)
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn empty_or_multiline_password_is_rejected() {
        let dir = temp_dir("reject");
        let path = dir.join("password.txt");
        assert!(write_to(&path, "").is_err(), "空密码必须拒绝 ✗");
        assert!(write_to(&path, "   ").is_err());
        assert!(write_to(&path, "两行\n密码").is_err());
        assert!(write_to(&path, "回车\r结尾").is_err());
        assert!(!path.exists(), "被拒绝时不该留下文件 ✗");
        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn unix_permissions_are_tightened() {
        use std::os::unix::fs::PermissionsExt;
        let dir = temp_dir("perm");
        let path = dir.join("password.txt");
        write_to(&path, "secret").unwrap();
        let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "密码文件权限要是 600 ✓");
        let _ = fs::remove_dir_all(&dir);
    }
}
