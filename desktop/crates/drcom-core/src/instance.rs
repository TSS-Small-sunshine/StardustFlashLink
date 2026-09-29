//! 单实例：**同一个端口只允许一个进程占**。
//!
//! 为什么不用「锁文件 + PID」：进程崩了会留下假锁 ✗，Windows 上判断进程存活还得走 FFI ✗。
//! 这里用最结实的办法：**占住 `127.0.0.1:<port>`** ——
//!   - 进程活着 → 端口被占 ✓；
//!   - 进程死了 → 操作系统立刻释放 ✓（不会有假锁 ✓）；
//!   - 顺带就有了一条「通知第一个实例」的通道 ✓（不用额外找信号文件 ✗）。
//!
//! 第二个实例启动时：拿不到端口 → 往那个端口发一句 `GET /focus` → 老实例把窗口抬到前面 ✓。

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::time::Duration;

/// 第二个实例用来「敲门」的路径。
pub const FOCUS_PATH: &str = "/focus";

/// 拿端口的结果。
#[derive(Debug)]
pub enum Acquire {
    /// 我是第一个 → 拿着 guard（**丢掉它就释放** ✓）
    First(InstanceGuard),
    /// 已经有一个在跑了 → 调用方应该把它的窗口叫到前面然后自己退出 ✓
    AlreadyRunning,
}

/// 占着端口的凭证。
#[derive(Debug)]
pub struct InstanceGuard {
    listener: TcpListener,
    port: u16,
}

impl InstanceGuard {
    /// 本实例占用的端口 ✓。
    pub fn port(&self) -> u16 {
        self.port
    }

    /// 有没有第二个实例在敲门（**非阻塞** ✓，界面定时器里 200~500ms 调一次 ✓）。
    ///
    /// 收到 `/focus` 就走人；其它请求一律忽略（不是我们的 API ✗）。
    pub fn poll_focus_request(&self) -> bool {
        let mut hit = false;
        while let Ok((mut stream, _)) = self.listener.accept() {
            hit |= handle_probe(&mut stream);
        }
        hit
    }
}

/// 处理一次敲门：只认 `GET /focus` ✓。
fn handle_probe(stream: &mut TcpStream) -> bool {
    let _ = stream.set_read_timeout(Some(Duration::from_millis(200)));
    let mut buffer = [0u8; 512];
    let Ok(read) = stream.read(&mut buffer) else {
        return false;
    };
    let text = String::from_utf8_lossy(&buffer[..read]);
    let is_focus = text
        .lines()
        .next()
        .map(|line| line.contains(FOCUS_PATH) && line.starts_with("GET "))
        .unwrap_or(false);
    let body = if is_focus { "FOCUS\n" } else { "IGNORED\n" };
    let _ = stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).as_bytes());
    let _ = stream.flush();
    is_focus
}

/// 尝试成为唯一实例（端口冲突 → [`Acquire::AlreadyRunning`] ✓）。
pub fn acquire(port: u16) -> Result<Acquire, String> {
    match TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, port))) {
        Ok(listener) => {
            listener
                .set_nonblocking(true)
                .map_err(|e| format!("设置非阻塞失败: {}", e))?;
            Ok(Acquire::First(InstanceGuard { listener, port }))
        }
        Err(e) if e.kind() == std::io::ErrorKind::AddrInUse => Ok(Acquire::AlreadyRunning),
        Err(e) => Err(format!("绑定 127.0.0.1:{} 失败: {}", port, e)),
    }
}

/// 通知已经在跑的那个实例「把窗口抬到前面」。
///
/// 返回值 = **敲到门了**（连上并且请求发出去了 ✓）。
/// 不等回复：对方 400ms 才轮询一次 ✗，我们没必要把自己卡在这里 ✗。
pub fn request_focus(port: u16) -> bool {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(800)) else {
        return false;
    };
    let request = format!(
        "GET {} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
        FOCUS_PATH
    );
    stream.write_all(request.as_bytes()).is_ok() && stream.flush().is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 借一个刚释放的本地端口（大概率还空着 ✓，不依赖外网 ✓）。
    fn free_port() -> u16 {
        let probe = TcpListener::bind("127.0.0.1:0").expect("借用端口 ✓");
        let port = probe.local_addr().unwrap().port();
        drop(probe);
        port
    }

    #[test]
    fn second_instance_is_detected_and_lock_disappears_with_the_guard() {
        let port = free_port();
        let guard = match acquire(port).expect("第一个实例必须成功 ✓") {
            Acquire::First(guard) => guard,
            other => panic!("{:?}", other),
        };
        assert_eq!(guard.port(), port);
        // 端口被占 → 第二个实例被认出来 ✓（不靠锁文件 ✗ → 没有假锁 ✓）
        assert!(
            matches!(acquire(port).expect("第二次 acquire 不该报错 ✓"), Acquire::AlreadyRunning),
            "第二个实例必须被识别 ✗"
        );
        // guard 一丢，端口立刻可用 ✓（进程崩溃同样如此 ✓）
        drop(guard);
        assert!(
            matches!(acquire(port).expect("释放后应能再占 ✓"), Acquire::First(_)),
            "锁必须随 guard 消失 ✓"
        );
    }

    #[test]
    fn focus_knock_is_detected_once() {
        let port = free_port();
        let guard = match acquire(port).unwrap() {
            Acquire::First(guard) => guard,
            other => panic!("{:?}", other),
        };
        assert!(!guard.poll_focus_request(), "没人敲门时不该误报 ✓");
        assert!(request_focus(port), "敲门应得到回复 ✓");
        assert!(guard.poll_focus_request(), "敲门必须被识别 ✓");
        assert!(!guard.poll_focus_request(), "识别过一次就不再重复 ✓");
    }

    #[test]
    fn focus_request_to_dead_port_is_false_not_panic() {
        let port = free_port();
        assert!(!request_focus(port), "没人监听 → false ✓（不 panic ✗）");
    }
}
