//! 日志轮转 —— 与 2.x 同一套口径：
//!   - 业务日志（`campus_login.log`）：5 MB × 3 份备份；
//!   - 升级日志（`upgrade.log`）：2 MB × 2 份备份；
//!   - 读取严格按「**最旧 → 最新**」（`.N … .2 .1 当前`）✓ —— 指标统计跨轮转也不会断档
//!     （2.1.0.0 / P7 那条修复的教训 ✗：只读 `.1` 会让近 7 天统计凭空变少）。

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

pub const BUSINESS_MAX_BYTES: u64 = 5 * 1024 * 1024;
pub const BUSINESS_BACKUPS: usize = 3;
pub const UPGRADE_MAX_BYTES: u64 = 2 * 1024 * 1024;
pub const UPGRADE_BACKUPS: usize = 2;

/// 一个会自我轮转的日志文件。
#[derive(Debug, Clone)]
pub struct RotatingLog {
    path: PathBuf,
    max_bytes: u64,
    backups: usize,
}

impl RotatingLog {
    pub fn new(path: impl Into<PathBuf>, max_bytes: u64, backups: usize) -> RotatingLog {
        RotatingLog { path: path.into(), max_bytes, backups }
    }

    /// 业务日志口径（5 MB × 3）✓
    pub fn business(path: impl Into<PathBuf>) -> RotatingLog {
        RotatingLog::new(path, BUSINESS_MAX_BYTES, BUSINESS_BACKUPS)
    }

    /// 升级日志口径（2 MB × 2）✓
    pub fn upgrade(path: impl Into<PathBuf>) -> RotatingLog {
        RotatingLog::new(path, UPGRADE_MAX_BYTES, UPGRADE_BACKUPS)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn max_bytes(&self) -> u64 {
        self.max_bytes
    }

    pub fn backups(&self) -> usize {
        self.backups
    }

    /// 备份文件路径：`campus_login.log.1` ✓
    pub fn backup_path(&self, index: usize) -> PathBuf {
        let mut name = self.path.file_name().unwrap_or_default().to_os_string();
        name.push(format!(".{}", index));
        self.path.with_file_name(name)
    }

    /// 当前文件大小（不存在算 0 ✓）
    pub fn size(&self) -> u64 {
        fs::metadata(&self.path).map(|m| m.len()).unwrap_or(0)
    }

    /// 当前 + 所有备份的总大小（「关于」面板显示占用 ✓）
    pub fn total_bytes(&self) -> u64 {
        let mut total = self.size();
        for index in 1..=self.backups {
            total += fs::metadata(self.backup_path(index)).map(|m| m.len()).unwrap_or(0);
        }
        total
    }

    /// 追加一行（自动补换行 ✓；写之前按需轮转 ✓）。父目录不存在会自动建 ✓。
    pub fn append_line(&self, line: &str) -> io::Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        self.rotate_if_needed()?;
        let mut file = fs::OpenOptions::new().create(true).append(true).open(&self.path)?;
        file.write_all(line.as_bytes())?;
        if !line.ends_with('\n') {
            file.write_all(b"\n")?;
        }
        file.flush()
    }

    /// 超过上限就轮转（返回是否真的转了 ✓）。空文件不轮转 ✗。
    pub fn rotate_if_needed(&self) -> io::Result<bool> {
        if self.size() < self.max_bytes || self.max_bytes == 0 {
            return Ok(false);
        }
        self.rotate()?;
        Ok(true)
    }

    /// 强制轮转一次：最旧的删掉，`N-1 → N`，`N → N+1`，当前 → `.1` ✓
    pub fn rotate(&self) -> io::Result<()> {
        if self.backups == 0 {
            // 不留备份 = 直接清空 ✓
            if self.path.exists() {
                fs::remove_file(&self.path)?;
            }
            return Ok(());
        }
        let oldest = self.backup_path(self.backups);
        if oldest.exists() {
            fs::remove_file(&oldest)?;
        }
        for index in (1..self.backups).rev() {
            let from = self.backup_path(index);
            if from.exists() {
                fs::rename(&from, self.backup_path(index + 1))?;
            }
        }
        if self.path.exists() {
            fs::rename(&self.path, self.backup_path(1))?;
        }
        Ok(())
    }

    /// 按「最旧 → 最新」读全部行（文件不存在 → 空列表，不报错 ✓）。
    pub fn read_all_lines(&self) -> io::Result<Vec<String>> {
        let mut lines: Vec<String> = Vec::new();
        let mut sources: Vec<PathBuf> = Vec::new();
        for index in (1..=self.backups).rev() {
            sources.push(self.backup_path(index));
        }
        sources.push(self.path.clone());
        for path in sources {
            if !path.exists() {
                continue;
            }
            let text = fs::read_to_string(&path)?;
            lines.extend(text.lines().map(|line| line.to_string()));
        }
        Ok(lines)
    }

    /// 一行描述（诊断包里用 ✓）
    pub fn describe(&self) -> String {
        format!(
            "{} · 单个上限 {:.1} MB · 备份 {} 份 · 当前 {:.1} MB · 合计 {:.1} MB",
            self.path.display(),
            self.max_bytes as f64 / 1_048_576.0,
            self.backups,
            self.size() as f64 / 1_048_576.0,
            self.total_bytes() as f64 / 1_048_576.0
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_log(tag: &str, max_bytes: u64, backups: usize) -> (RotatingLog, PathBuf) {
        let dir = std::env::temp_dir().join(format!("sfl-log-{}-{}", tag, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let log = RotatingLog::new(dir.join("campus_login.log"), max_bytes, backups);
        (log, dir)
    }

    #[test]
    fn appends_lines_and_creates_parent_dir() {
        let (log, dir) = temp_log("basic", 1_000_000, 3);
        log.append_line("第一行").unwrap();
        log.append_line("第二行\n").unwrap(); // 已经有换行就不再加 ✓
        let text = fs::read_to_string(log.path()).unwrap();
        assert_eq!(text, "第一行\n第二行\n");
        assert_eq!(log.size(), text.len() as u64);
        assert!(!log.backup_path(1).exists(), "没超上限不该轮转 ✓");
        assert!(log.describe().contains("备份 3 份"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn rotates_when_exceeding_limit_and_keeps_chronological_order() {
        let (log, dir) = temp_log("rotate", 60, 3);
        for i in 0..12 {
            log.append_line(&format!("line-{:02}", i)).unwrap();
        }
        assert!(log.backup_path(1).exists(), "应该转过至少一次 ✓");
        assert!(log.size() < 60, "轮转后当前文件是小文件 ✓");
        let all = log.read_all_lines().unwrap();
        // 最新的那一行一定在最后 ✓
        assert_eq!(all.last().map(String::as_str), Some("line-11"));
        // 顺序单调递增（取最后 8 行验证 ✓）
        let tail: Vec<String> = all.iter().rev().take(8).rev().cloned().collect();
        let mut sorted = tail.clone();
        sorted.sort();
        assert_eq!(tail, sorted, "跨轮转读出来必须是时间顺序 ✓: {:?}", all);
        assert!(log.total_bytes() >= log.size());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn oldest_backup_is_dropped() {
        let (log, dir) = temp_log("drop", 40, 2);
        for i in 0..40 {
            log.append_line(&format!("entry-{:03}", i)).unwrap();
        }
        assert!(!log.backup_path(3).exists(), "只留 2 份备份 ✗");
        assert!(log.backup_path(2).exists() || log.backup_path(1).exists());
        let all = log.read_all_lines().unwrap();
        assert!(all.len() < 40, "最旧的内容会被丢掉 ✓（这是设计）");
        assert!(all.last().unwrap().contains("entry-039"), "最新的必须在 ✓");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn zero_backups_truncates_instead_of_keeping_files() {
        let (log, dir) = temp_log("trunc", 30, 0);
        for i in 0..10 {
            log.append_line(&format!("x-{}", i)).unwrap();
        }
        assert!(!log.backup_path(1).exists());
        assert!(log.size() < 30);
        let all = log.read_all_lines().unwrap();
        assert!(all.last().map(|l| l.starts_with("x-")).unwrap_or(false));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn read_all_lines_on_missing_file_is_empty_not_error() {
        let (log, dir) = temp_log("missing", 100, 2);
        assert!(log.read_all_lines().unwrap().is_empty(), "文件不存在 → 空 ✓");
        assert_eq!(log.size(), 0);
        assert_eq!(log.total_bytes(), 0);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn limits_match_v2_contract() {
        assert_eq!(BUSINESS_MAX_BYTES, 5 * 1024 * 1024);
        assert_eq!(BUSINESS_BACKUPS, 3);
        assert_eq!(UPGRADE_MAX_BYTES, 2 * 1024 * 1024);
        assert_eq!(UPGRADE_BACKUPS, 2);
        assert_eq!(RotatingLog::business("a.log").max_bytes(), BUSINESS_MAX_BYTES);
        assert_eq!(RotatingLog::upgrade("u.log").backups(), UPGRADE_BACKUPS);
    }

    #[test]
    fn manual_rotate_shifts_chain_correctly() {
        let (log, dir) = temp_log("manual", 1_000_000, 3);
        log.append_line("v1").unwrap();
        log.rotate().unwrap();
        log.append_line("v2").unwrap();
        log.rotate().unwrap();
        log.append_line("v3").unwrap();
        let all = log.read_all_lines().unwrap();
        assert_eq!(all, vec!["v1", "v2", "v3"], "手动轮转也要保持顺序 ✓");
        assert_eq!(
            fs::read_to_string(log.backup_path(1)).unwrap().trim(),
            "v2",
            "最近的备份是次新 ✓"
        );
        assert_eq!(
            fs::read_to_string(log.backup_path(2)).unwrap().trim(),
            "v1",
            "越老的编号越大 ✓"
        );
        let _ = fs::remove_dir_all(&dir);
    }
}

