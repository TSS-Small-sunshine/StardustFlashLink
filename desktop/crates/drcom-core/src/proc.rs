//! 跑外部命令的**唯一**入口：一律带硬超时 ✓。
//!
//! 为什么要单开一层 ✗：`std::process::Command::output()` **没有超时** ✗ ——
//! 外部工具只要卡住，调用方就跟着一起卡死。我们真在 CI 上被这个拖过 6 小时 ✗：
//! Windows runner 上 `cargo test` 跑到 `service_cmd(..)` 里的 `sc` / `nssm` 探测时，
//! 一个子进程不返回 → 整个测试二进制不返回 → 直到 job 撞上 6 小时上限被掐 ✗。
//!
//! 规则（都是血换的 ✓）：
//!   1. **任何**外部命令都走这里 ✓，不许再直接 `Command::output()` ✗；
//!   2. 超时到了就 **kill 并回收** ✓（不是放着不管 ✗），返回可读的错误 ✓；
//!   3. 输出走**临时文件**而不是管道 ✓ —— 子进程若把句柄传给了孙子进程，
//!      管道读端要等孙子退出才 EOF ✗，临时文件没这个坑 ✓；
//!   4. stdin 一律接空设备 ✓（免得命令反过来等我们输入 ✗）。
//!
//! 解析永远留在各自的模块里（纯函数、单测逐字覆盖 ✓）—— 这层只管「跑」✓。

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

/// 外部命令的默认硬超时 ✓（正常命令都是毫秒级 ✓，20 秒已经非常宽 ✓）。
pub const TIMEOUT_SECS: u64 = 20;

/// 一次外部命令的输出 ✓。
#[derive(Debug, Clone)]
pub struct Captured {
    /// 标准输出 ✓
    pub stdout: String,
    /// 标准错误 ✓
    pub stderr: String,
    /// 退出码（拿不到时 -1 ✓）
    pub code: i32,
}

impl Captured {
    /// 成功（退出码 0）吗 ✓。
    pub fn ok(&self) -> bool {
        self.code == 0
    }

    /// 报错信息里用的「现场」一行 ✓。
    pub fn detail(&self) -> String {
        let err = self.stderr.trim();
        if err.is_empty() {
            format!("退出码 {}", self.code)
        } else {
            err.to_string()
        }
    }
}

/// 跑一条外部命令并捕获输出 ✓（默认超时 ✓）。
pub fn run(program: &Path, args: &[String]) -> Result<Captured, String> {
    run_with_timeout(program, args, Duration::from_secs(TIMEOUT_SECS))
}

/// 跑一条外部命令并捕获输出，超时到了就掐掉 ✓。
///
/// 返回 `Err` 只有三种情况：起不来 / 等不到 / 超时 ✓——
/// 命令自己**非零退出不算错** ✗（查状态正要靠退出码 ✓，看 [`Captured::code`] ✓）。
pub fn run_with_timeout(
    program: &Path,
    args: &[String],
    timeout: Duration,
) -> Result<Captured, String> {
    let (out_path, err_path) = temp_paths();
    let out_file =
        std::fs::File::create(&out_path).map_err(|e| format!("建临时文件失败: {}", e))?;
    let err_file =
        std::fs::File::create(&err_path).map_err(|e| format!("建临时文件失败: {}", e))?;

    let spawned = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::from(out_file))
        .stderr(Stdio::from(err_file))
        .spawn();
    let mut child = match spawned {
        Ok(child) => child,
        Err(e) => {
            cleanup(&out_path, &err_path);
            return Err(format!("执行 {} 失败: {}", program.display(), e));
        }
    };

    // 边等边看表 ✓：到点了连子进程一起带走 ✗（不留孤儿 ✓）
    let deadline = Instant::now() + timeout;
    let mut timed_out = false;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    timed_out = true;
                    break None;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(e) => {
                cleanup(&out_path, &err_path);
                return Err(format!("等 {} 结束失败: {}", program.display(), e));
            }
        }
    };

    let stdout = std::fs::read_to_string(&out_path).unwrap_or_default();
    let stderr = std::fs::read_to_string(&err_path).unwrap_or_default();
    cleanup(&out_path, &err_path);

    if timed_out {
        return Err(format!(
            "{} 超过 {} 秒没返回 ✗（已经掐掉 ✓ —— 外部工具卡住不许把我们也拖住 ✗）",
            program.display(),
            timeout.as_secs()
        ));
    }
    Ok(Captured {
        stdout,
        stderr,
        code: status.and_then(|s| s.code()).unwrap_or(-1),
    })
}

/// 每次调用一对唯一路径 ✓（测试并发跑也不会撞 ✓）。
fn temp_paths() -> (PathBuf, PathBuf) {
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let stamp = format!(
        "{}-{}",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    );
    let dir = std::env::temp_dir();
    (
        dir.join(format!("drcom-proc-{}.out", stamp)),
        dir.join(format!("drcom-proc-{}.err", stamp)),
    )
}

fn cleanup(out_path: &Path, err_path: &Path) {
    let _ = std::fs::remove_file(out_path);
    let _ = std::fs::remove_file(err_path);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(args: &[&str]) -> Vec<String> {
        args.iter().map(|a| a.to_string()).collect()
    }

    /// 「打印一行再退出」：两个平台各一句 ✓（不引入新依赖 ✓）。
    fn echo_and_exit(code: i32) -> (String, Vec<String>) {
        if cfg!(target_os = "windows") {
            (
                "cmd".to_string(),
                s(&["/C", &format!("echo hello-{} & exit {}", code, code)]),
            )
        } else {
            (
                "sh".to_string(),
                s(&["-c", &format!("echo hello-{}; exit {}", code, code)]),
            )
        }
    }

    #[test]
    fn captures_output_and_exit_code() {
        let (program, args) = echo_and_exit(0);
        let got = run(Path::new(&program), &args).expect("正常命令必须能跑完 ✓");
        assert!(got.ok(), "退出码应当是 0：{}", got.detail());
        assert!(got.stdout.contains("hello-0"), "stdout: {:?}", got.stdout);

        // 非零退出**不算**调用错误 ✓（查状态正要靠退出码 ✓）
        let (program, args) = echo_and_exit(3);
        let got = run(Path::new(&program), &args).expect("非零退出也要拿到结果 ✓");
        assert_eq!(got.code, 3, "退出码要如实带回来 ✓");
        assert!(!got.ok());
    }

    #[test]
    fn missing_program_reports_instead_of_panicking() {
        let err = run(
            Path::new("drcom-there-is-no-such-program-xyz"),
            &s(&["--help"]),
        )
        .unwrap_err();
        assert!(err.contains("执行"), "错误信息要能看懂: {}", err);
    }

    #[test]
    fn a_stuck_program_gets_killed_at_the_deadline() {
        // 明摆着要睡 30 秒的命令 ✓：1 秒上限必须掐掉它 ✗
        //（这就是 CI 那次「跑了 6 小时」的形状 ✓ —— 那条路以后走不通了 ✓）
        let (program, args) = if cfg!(target_os = "windows") {
            ("cmd".to_string(), s(&["/C", "ping -n 30 127.0.0.1 > NUL"]))
        } else {
            ("sh".to_string(), s(&["-c", "sleep 30"]))
        };
        let started = Instant::now();
        let err = run_with_timeout(Path::new(&program), &args, Duration::from_secs(1))
            .expect_err("卡住的命令必须超时返回 ✗");
        let spent = started.elapsed();
        assert!(err.contains("秒没返回"), "错误要说清是超时: {}", err);
        assert!(
            spent < Duration::from_secs(15),
            "超时后必须立刻回来 ✓，实际花了 {:?}",
            spent
        );
    }
}
