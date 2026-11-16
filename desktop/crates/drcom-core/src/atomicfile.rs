//! 原子写盘（只依赖 std ✓）：**同目录唯一 tmp 名 → 写完 sync → rename 覆盖**。
//!
//! 为什么不能用固定 tmp 名：`config.json.tmp` / `password.txt.tmp` 这种固定名字下，
//! 两个写者（GUI 设置页 + 后台 worker / NSSM 服务进程）会往**同一个**文件里交错写，
//! 先 rename 的那个就把「另一个写者写了一半的内容」发布成正式文件 —— 落盘一个半截 JSON。
//! 2.x v2.0.6.2 已经修过同一个坑（`联网_service.py` 的 `_save_config_to_disk` /
//! `_save_password_to_disk`：tmp 名带 pid），3.0 必须跟上 ✓。
//!
//! tmp 名由三样一起构成，缺一不可 ✓：
//!   - **pid**：挡住「GUI 与服务的两个进程同时写」✓；
//!   - **纳秒时间戳**：挡住「同一进程两次写恰好落在同一毫秒」✓；
//!   - **进程内原子序号**：挡住「同一纳秒内连续调用」（时钟精度不够时的兜底）✓
//!     —— 这一条是纯函数测试能逐项验证的部分 ✓。

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// 进程内自增序号：同一进程的多个写者（多线程）也不会拿到同一个 tmp 名 ✓。
static NEXT_TMP_SEQ: AtomicU64 = AtomicU64::new(0);

/// 同目录唯一 tmp 路径：`<原名>.<pid>.<纳秒>.<序号>.tmp` ✓
///
/// 必须落在**目标文件所在目录**里 —— 只有同一文件系统内的 rename 才是原子替换 ✓
/// （跨目录 / 跨盘的 rename 不保证原子 ✓）。
pub fn unique_tmp_path(path: &Path) -> PathBuf {
    let seq = NEXT_TMP_SEQ.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos())
        .unwrap_or(0);
    let file_name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let tmp_name = format!(
        "{}.{}.{}.{}.tmp",
        file_name,
        std::process::id(),
        nanos,
        seq
    );
    match path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent.join(tmp_name),
        _ => PathBuf::from(tmp_name),
    }
}

/// 往 tmp 写内容并 `sync_all`（先落盘，再 rename —— 免得改名后内容还在页缓存里 ✓）。
pub fn write_tmp(tmp: &Path, bytes: &[u8]) -> Result<(), String> {
    let mut file =
        std::fs::File::create(tmp).map_err(|e| format!("写不了 {}: {}", tmp.display(), e))?;
    file.write_all(bytes)
        .map_err(|e| format!("写不了 {}: {}", tmp.display(), e))?;
    file.sync_all()
        .map_err(|e| format!("冲刷 {} 失败: {}", tmp.display(), e))
}

/// 把 tmp 改名覆盖目标；失败时清掉 tmp（**不留垃圾** ✓）。
///
/// Windows 上若有别的进程/线程正好打开着目标文件（读者 / 杀毒软件扫描），
/// `rename` 会短暂报「另一个程序正在使用此文件」—— 这是**占用**而不是内容错误，
/// 所以做一小段有界重试（10 次 × 5–50ms，总上限约 300ms），真实错误照样快速失败 ✓。
pub fn commit(tmp: &Path, path: &Path) -> Result<(), String> {
    let mut last_error = None;
    for attempt in 0..10 {
        match std::fs::rename(tmp, path) {
            Ok(()) => return Ok(()),
            Err(error) => {
                last_error = Some(error);
                // 只对「疑似占用」的错误重试；找不到 tmp / 路径非法重试也没意义。
                if !matches!(
                    last_error.as_ref().map(|e| e.kind()),
                    Some(std::io::ErrorKind::PermissionDenied) | Some(std::io::ErrorKind::Other)
                ) {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(5 + attempt * 5));
            }
        }
    }
    let _ = std::fs::remove_file(tmp);
    Err(format!(
        "替换 {} 失败: {}",
        path.display(),
        last_error
            .map(|e| e.to_string())
            .unwrap_or_else(|| "未知错误".to_string())
    ))
}

/// 一步到位：唯一 tmp → 写 → rename 覆盖 ✓（配置这类「整份内容一次写」的调用方用这个）。
pub fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let tmp = unique_tmp_path(path);
    if let Err(err) = write_tmp(&tmp, bytes) {
        // 写一半失败（磁盘满 / 权限）也要把 tmp 清掉，别留垃圾 ✓
        let _ = std::fs::remove_file(&tmp);
        return Err(err);
    }
    commit(&tmp, path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sfl-tmpfile-{}-{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn tmp_leftovers(dir: &Path) -> Vec<String> {
        std::fs::read_dir(dir)
            .map(|entries| {
                entries
                    .filter_map(|entry| entry.ok())
                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
                    .filter(|name| name.ends_with(".tmp"))
                    .collect()
            })
            .unwrap_or_default()
    }

    #[test]
    fn tmp_names_are_unique_per_call_and_sit_beside_the_target() {
        let dir = temp_dir("unique");
        let target = dir.join("config.json");
        let names: Vec<String> = (0..64)
            .map(|_| {
                unique_tmp_path(&target)
                    .file_name()
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        let unique: BTreeSet<&String> = names.iter().collect();
        assert_eq!(
            unique.len(),
            names.len(),
            "连续 64 次生成必须每次都不同（纳秒 + 进程内序号一起挡撞名 ✗）: {:?}",
            names
        );
        for name in &names {
            assert!(name.starts_with("config.json."), "tmp 名要基于目标名: {}", name);
            assert!(name.ends_with(".tmp"), "{}", name);
            assert_ne!(
                name.as_str(),
                "config.json.tmp",
                "不能再是固定 tmp 名 ✗（2.x v2.0.6.2 的教训）: {}",
                name
            );
            assert!(
                name.contains(&std::process::id().to_string()),
                "要带 pid（挡多进程撞名）: {}",
                name
            );
        }
        // 都落在**目标文件所在目录**里（同盘 rename 才是原子替换 ✓）
        for _ in 0..4 {
            assert_eq!(unique_tmp_path(&target).parent(), Some(dir.as_path()));
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_atomic_publishes_the_whole_file_and_leaves_no_tmp() {
        let dir = temp_dir("write");
        let target = dir.join("config.json");
        write_atomic(&target, br#"{"a":1}"#).unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), r#"{"a":1}"#);
        write_atomic(&target, br#"{"a":2}"#).unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), r#"{"a":2}"#);
        assert!(
            tmp_leftovers(&dir).is_empty(),
            "不该留下 tmp 垃圾 ✗: {:?}",
            tmp_leftovers(&dir)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn concurrent_writers_never_publish_a_torn_file() {
        let dir = temp_dir("concurrent");
        let target = dir.join("blob.json");
        // 两种**完整**内容且长度差很大：一旦 tmp 名撞了（交错写同一个文件再 rename），
        // 读者就会读到半截内容 —— 这个断言就是冲着那个 bug 去的 ✓。
        let big = format!("{{\"v\":\"{}\"}}", "A".repeat(32 * 1024));
        let small = "{\"v\":\"B\"}".to_string();
        let mut handles = Vec::new();
        for writer in 0..4 {
            let target = target.clone();
            let big = big.clone();
            let small = small.clone();
            handles.push(std::thread::spawn(move || {
                for round in 0..20 {
                    let payload = if (writer + round) % 2 == 0 { &big } else { &small };
                    // Windows 并发写：rename 偶尔遇到读者占用 → 有界重试（重试的是占用，不是内容 ✓）。
                    let mut attempts = 0;
                    while let Err(error) = write_atomic(&target, payload.as_bytes()) {
                        attempts += 1;
                        assert!(attempts < 500, "写入连续失败（环境问题，不是内容错误）: {}", error);
                        std::thread::sleep(std::time::Duration::from_millis(1));
                    }
                }
            }));
        }
        // 读侧用「总截止时间」而不是固定次数：CI Windows runner 上杀毒/索引会让
        // rename 瞬间的占用比本机长得多，固定 500ms 会偶发读不到（v2.1 前的教训）。
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        let mut reads = 0;
        for _ in 0..200 {
            let got = loop {
                match std::fs::read_to_string(&target) {
                    Ok(text) => break text,
                    Err(_) if std::time::Instant::now() < deadline => {
                        std::thread::sleep(std::time::Duration::from_millis(1));
                    }
                    Err(error) => panic!(
                        "10 秒内一直读不到文件（Windows 占用/环境问题，不是内容错误）: {}",
                        error
                    ),
                }
            };
            assert!(
                got == big || got == small,
                "读到了半截内容 ✗（长度 {}，期望 {} 或 {}）",
                got.len(),
                big.len(),
                small.len()
            );
            reads += 1;
        }
        for handle in handles {
            handle.join().unwrap();
        }
        let final_text = std::fs::read_to_string(&target).unwrap();
        assert!(final_text == big || final_text == small);
        assert!(reads > 0);
        assert!(
            tmp_leftovers(&dir).is_empty(),
            "并发写之后不该留下 tmp 垃圾 ✗: {:?}",
            tmp_leftovers(&dir)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
