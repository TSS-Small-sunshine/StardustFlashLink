//! 密码文件读写 —— 沿用 2.x 的格式与语义：
//!   - 纯文本，**一行一个密码**；`#` 开头是注释；只取**第一条有效行** ✓；
//!   - 文件不存在 / 只有注释 → 空串（表示「还没设密码」✓，**不报错** ✓）；
//!   - 写盘是**原子替换**（先写临时文件再改名 ✓）—— 免得写一半把用户的密码毁了 ✗。
//!
//! ⚠️ 「留空不修改」这条语义在 [`crate::settings`] 里（表单层 ✓），这里只管读写 ✓。

use std::fs;
use std::path::{Path, PathBuf};

/// 默认位置：数据目录下的 `password.txt` ✓
pub fn path() -> PathBuf {
    crate::platform::password_path()
}

/// 文件里第一条有效内容（跳过空行与 `#` 注释 ✓）。
pub fn read_from(path: &Path) -> String {
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(_) => return String::new(), // 不存在 = 还没设密码 ✓，不是错误 ✓
    };
    text.lines()
        .map(|line| line.trim())
        .find(|line| !line.is_empty() && !line.starts_with('#'))
        .unwrap_or("")
        .to_string()
}

/// 读默认位置的密码 ✓
pub fn read() -> String {
    read_from(&path())
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
    let tmp = path.with_extension("txt.tmp");
    fs::write(&tmp, format!("{}\n", password)).map_err(|e| format!("写不了 {}: {}", tmp.display(), e))?;
    // 权限：Unix 上收紧到 600（Windows 忽略 ✓）
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600));
    }
    fs::rename(&tmp, path).map_err(|e| format!("替换 {} 失败: {}", path.display(), e))?;
    Ok(())
}

/// 写默认位置 ✓
pub fn write(password: &str) -> Result<(), String> {
    write_to(&path(), password)
}

/// 首次创建时写的骨架（**全都是注释** → 读出来是空串 ✓，不会被当成密码 ✗）。
pub fn template() -> String {
    "# 一行一个密码；`#` 开头是注释\n# 只取第一条有效行；改完保存即可（服务会自动重读）\n".to_string()
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
        dir
    }

    #[test]
    fn missing_file_is_empty_not_an_error() {
        let dir = temp_dir("missing");
        assert_eq!(read_from(&dir.join("password.txt")), "");
        assert!(!read_from(&dir.join("password.txt")).contains("错"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn template_only_has_comments_so_it_reads_as_empty() {
        let dir = temp_dir("template");
        let path = dir.join("password.txt");
        fs::create_dir_all(&dir).unwrap();
        fs::write(&path, template()).unwrap();
        assert_eq!(read_from(&path), "", "模板不该被当成密码 ✓（2.x 的 P0-3 教训）");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn comments_and_blank_lines_are_skipped_first_valid_wins() {
        let dir = temp_dir("comments");
        let path = dir.join("password.txt");
        fs::create_dir_all(&dir).unwrap();
        fs::write(&path, "# 注释\n\n   \n真实密码\n第二个密码\n").unwrap();
        assert_eq!(read_from(&path), "真实密码");
        // 前后空白要被 trim 掉 ✓
        fs::write(&path, "  空格包起来的  \n").unwrap();
        assert_eq!(read_from(&path), "空格包起来的");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_is_atomic_and_round_trips() {
        let dir = temp_dir("write");
        let path = dir.join("password.txt");
        write_to(&path, "P@ss 词1+2").unwrap();
        assert_eq!(read_from(&path), "P@ss 词1+2");
        assert!(!path.with_extension("txt.tmp").exists(), "临时文件要改掉 ✗");
        // 覆盖写 ✓
        write_to(&path, "新的").unwrap();
        assert_eq!(read_from(&path), "新的");
        assert!(!path.with_extension("txt.tmp").exists());
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
