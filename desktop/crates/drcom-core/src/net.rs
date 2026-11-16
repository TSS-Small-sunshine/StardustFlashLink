//! 极简 HTTP 客户端 —— **只用标准库**（TcpStream + 手写请求）。
//!
//! 为什么不用 reqwest：校园网认证是**明文 HTTP**、体积优先，
//! 而且这层逻辑小到可以一眼看完 ✓（真正需要 TLS 的只有「检查更新」，M4 再单独处理）。
//!
//! 超时策略（审计 P2-2）：**连接**用 `TcpStream::connect_timeout` 给硬超时，
//! **读写**用 `set_read_timeout` / `set_write_timeout`，三段**共用同一条 deadline** ——
//! 这样界面 / README 承诺的「最多等 12 秒」才真的成立 ✓。
//! 原先直接 `TcpStream::connect`：目标被防火墙 DROP（校园网常见）时 Windows 要等约 21 秒、
//! Linux 约 130 秒，UI 一直转圈、worker 的 2 秒 tick 也被卡死 ✗。

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::time::{Duration, Instant};

/// 一次 HTTP GET 的结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpReply {
    pub status: u16,
    /// `Location:` 响应头。
    ///
    /// 2.x 不需要它（只看 JSONP 正文），3.0 的**门户检测**要靠它：
    /// 未认证时校园 AC 会 302 到登录页，目标地址就在这个头里 ✓。
    pub location: Option<String>,
    pub body: String,
}

/// 抽象的 GET —— 让上层逻辑（登录/在线检查）**能在单测里换成假客户端** ✓。
pub trait HttpGet {
    fn get(&self, url: &str, timeout: Duration) -> Result<HttpReply, String>;
}

/// 真实实现（走标准库 TcpStream）。
pub struct PlainHttp;

impl HttpGet for PlainHttp {
    fn get(&self, url: &str, timeout: Duration) -> Result<HttpReply, String> {
        get(url, timeout)
    }
}

/// 从 `http://host[:port]/path` 里拆出 (host, port, path)。
pub fn split_http_url(url: &str) -> Option<(String, u16, String)> {
    let rest = url.strip_prefix("http://")?;
    let (authority, path) = match rest.find('/') {
        Some(idx) => (&rest[..idx], &rest[idx..]),
        None => (rest, "/"),
    };
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) => (h.to_string(), p.parse::<u16>().ok()?),
        None => (authority.to_string(), 80),
    };
    if host.is_empty() {
        return None;
    }
    Some((host, port, path.to_string()))
}

/// 把 `host:port` 解析成一组 socket 地址（纯函数，IP 字面量**不查 DNS** ✓）。
///
/// ⚠️ std 的 DNS 解析本身没有超时（这是 std 的边界，不是我们不用 connect_timeout ✗）；
/// 校园网网关一般是 IP 字面量（如 `172.16.80.3`），走不到 DNS ✓。
pub fn resolve_addrs(host: &str, port: u16) -> Result<Vec<SocketAddr>, String> {
    let addrs: Vec<SocketAddr> = (host, port)
        .to_socket_addrs()
        .map_err(|e| format!("解析地址 {}:{} 失败: {}", host, port, e))?
        .collect();
    if addrs.is_empty() {
        return Err(format!("解析地址 {}:{} 没有结果", host, port));
    }
    Ok(addrs)
}

/// **纯函数**：剩余预算 —— connect 与后续读写共享同一条 deadline ✓。
///
/// 下限 1ms：`set_read_timeout(Some(Duration::ZERO))` 在 std 里会返回 `InvalidInput` ✗；
/// 上限 `ceiling`：多地址逐个试时不会借出超过总预算 ✓。
pub fn remaining_budget(deadline: Instant, now: Instant, ceiling: Duration) -> Duration {
    let left = deadline.saturating_duration_since(now);
    let floor = Duration::from_millis(1);
    if left < floor {
        floor
    } else if left > ceiling {
        ceiling
    } else {
        left
    }
}

/// 带**硬超时**的 TCP 连接（`TcpStream::connect_timeout`，只用 std ✓，审计 P2-2）。
///
/// 解析出多个地址时逐个试，每个地址只用**剩余预算** → 整个连接阶段不超过 `timeout` ✓。
pub fn connect_with_timeout(host: &str, port: u16, timeout: Duration) -> Result<TcpStream, String> {
    let addrs = resolve_addrs(host, port)?;
    let deadline = Instant::now() + timeout;
    let mut last_error = String::new();
    for addr in addrs {
        let budget = remaining_budget(deadline, Instant::now(), timeout);
        match TcpStream::connect_timeout(&addr, budget) {
            Ok(stream) => return Ok(stream),
            Err(e) => last_error = format!("{} → {}", addr, e),
        }
    }
    Err(format!(
        "连接 {}:{} 失败（最多等 {:?}；最后一次错误：{}）",
        host, port, timeout, last_error
    ))
}

/// 发一次 GET（只支持明文 http，够用 ✓）。
///
/// `timeout` 是**这一次请求的总预算**：连接 + 写请求 + 读响应头 + 读响应体 ✓。
/// 连接阶段有硬超时 → 目标被 DROP 时也会在 `timeout` 内返回 `Err` ✓（审计 P2-2）。
pub fn get(url: &str, timeout: Duration) -> Result<HttpReply, String> {
    let deadline = Instant::now() + timeout;
    let (host, port, path) =
        split_http_url(url).ok_or_else(|| "URL 形状不对（只支持 http://）".to_string())?;
    let stream = connect_with_timeout(&host, port, timeout)?;
    // 读 / 写超时用**剩下的**预算：connect 用掉多少就从后面扣掉 ✓
    let budget = remaining_budget(deadline, Instant::now(), timeout);
    stream.set_read_timeout(Some(budget)).map_err(|e| e.to_string())?;
    stream.set_write_timeout(Some(budget)).map_err(|e| e.to_string())?;
    let mut writer = stream.try_clone().map_err(|e| e.to_string())?;
    let request = format!(
        "GET {} HTTP/1.1\r\nHost: {}\r\nUser-Agent: StardustFlashLink/3.0\r\nAccept: */*\r\nConnection: close\r\n\r\n",
        path, host
    );
    writer
        .write_all(request.as_bytes())
        .and_then(|_| writer.flush())
        .map_err(|e| format!("发送请求失败: {}", e))?;
    let mut reader = BufReader::new(stream);
    let mut status_line = String::new();
    reader.read_line(&mut status_line).map_err(|e| format!("读响应失败: {}", e))?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| format!("响应头不合法: {:?}", status_line.trim()))?;
    // 跳过响应头（不解析 chunked：校园网关与本地服务都是 Identity ✓），
    // 顺手留下 `Location`（门户检测要用 ✓）。
    let mut location: Option<String> = None;
    loop {
        let mut line = String::new();
        let read = reader.read_line(&mut line).map_err(|e| e.to_string())?;
        if read == 0 || line == "\r\n" || line == "\n" {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            if location.is_none() && name.trim().eq_ignore_ascii_case("location") {
                let trimmed = value.trim();
                if !trimmed.is_empty() {
                    location = Some(trimmed.to_string());
                }
            }
        }
    }
    let mut raw = Vec::new();
    reader.read_to_end(&mut raw).map_err(|e| format!("读响应体失败: {}", e))?;
    Ok(HttpReply {
        status,
        location,
        body: String::from_utf8_lossy(&raw).into_owned(),
    })
}

/// 本机在「通往 `host`」这条路上使用的源 IP（Dr.COM 表单要填 ✓）。
///
/// 技巧：UDP `connect` 不发包，只让内核选好路由与源地址 ✓ —— 无权限、无依赖、跨平台 ✓。
pub fn local_ip_towards(host: &str, port: u16) -> Option<String> {
    let socket = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    socket.connect((host, port)).ok()?;
    socket.local_addr().ok().map(|addr| addr.ip().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_split_handles_port_and_path() {
        assert_eq!(
            split_http_url("http://172.16.80.3:801/eportal/portal/login?a=1"),
            Some(("172.16.80.3".to_string(), 801, "/eportal/portal/login?a=1".to_string()))
        );
        assert_eq!(
            split_http_url("http://example.com"),
            Some(("example.com".to_string(), 80, "/".to_string()))
        );
        assert!(split_http_url("https://example.com").is_none(), "只支持明文 http ✓");
        assert!(split_http_url("http://").is_none());
    }

    #[test]
    fn local_ip_probe_returns_something_on_a_routable_host() {
        // 用本机回环当目标：一定能拿到 127.0.0.1 ✓（不发包，不需要网络）
        let ip = local_ip_towards("127.0.0.1", 80);
        assert_eq!(ip.as_deref(), Some("127.0.0.1"));
    }

    #[test]
    fn get_reports_error_for_bad_url_instead_of_panicking() {
        let err = get("https://example.com", Duration::from_millis(200)).unwrap_err();
        assert!(err.contains("只支持 http"), "错误信息要能看懂: {}", err);
    }

    #[test]
    fn unreachable_address_fails_within_the_timeout() {
        // 10.255.255.1 是保留地址：不可路由、不需要外网、真实环境里也**连不上** ✓
        // 审计 P2-2 的原始问题：没有 connect 超时时这里会等 21 秒（Windows）/ 130 秒（Linux）✗
        let started = Instant::now();
        let err = get("http://10.255.255.1:9/x", Duration::from_millis(300))
            .expect_err("不可达地址必须返回错误");
        let elapsed = started.elapsed();
        assert!(
            elapsed < Duration::from_secs(2),
            "connect 必须有硬超时（实际等了 {:?}，错误: {}）✗",
            elapsed,
            err
        );
        assert!(err.contains("连接"), "错误要能看懂: {}", err);
    }

    #[test]
    fn connect_with_timeout_reaches_a_local_listener() {
        // 正常路径不能被超时改坏：本机监听必须连得上 ✓（不依赖外网）
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let stream = connect_with_timeout("127.0.0.1", port, Duration::from_secs(1))
            .expect("本机监听应当连得上 ✓");
        drop(stream);
    }

    #[test]
    fn get_still_reads_a_normal_http_response() {
        // 端到端：本机起一个只说一句话的 HTTP 服务，验证 get 没被 connect_timeout 改坏 ✓
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut request = [0u8; 1024];
                let _ = stream.read(&mut request);
                let _ = stream.write_all(
                    b"HTTP/1.1 302 Found\r\nLocation: http://portal.example/a79.htm\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok",
                );
                let _ = stream.flush();
            }
        });
        let reply = get(&format!("http://127.0.0.1:{}/probe", port), Duration::from_secs(3))
            .expect("本机回环请求应当成功 ✓");
        assert_eq!(reply.status, 302);
        assert_eq!(reply.body, "ok");
        assert_eq!(reply.location.as_deref(), Some("http://portal.example/a79.htm"));
        server.join().unwrap();
    }

    #[test]
    fn resolve_ip_literal_does_not_need_dns() {
        let addrs = resolve_addrs("127.0.0.1", 801).expect("IP 字面量必须直接解析出来 ✓");
        assert_eq!(addrs, vec!["127.0.0.1:801".parse::<SocketAddr>().unwrap()]);
        // 网关端口是 u16：801 这种值要原样带上 ✓
        assert!(resolve_addrs("10.255.255.1", 9).unwrap()[0].port() == 9);
    }

    #[test]
    fn remaining_budget_is_clamped_and_never_zero() {
        let now = Instant::now();
        // ① 还有余额 → 就用余额（不会借出超过总预算 ✓）
        let left = remaining_budget(now + Duration::from_secs(5), now, Duration::from_secs(9));
        assert!(left > Duration::ZERO && left <= Duration::from_secs(5), "{:?}", left);
        // ② 已经过点 → 保底 1ms（ZERO 会让 set_read_timeout 直接报错 ✗）
        assert_eq!(
            remaining_budget(now, now + Duration::from_secs(3), Duration::from_secs(3)),
            Duration::from_millis(1)
        );
        // ③ 余额超过上限 → 截到上限（多地址逐个试时总时长才守得住 ✓）
        assert_eq!(
            remaining_budget(now + Duration::from_secs(9), now, Duration::from_secs(2)),
            Duration::from_secs(2)
        );
    }
}
