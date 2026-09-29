//! 极简 HTTP 客户端 —— **只用标准库**（TcpStream + 手写请求）。
//!
//! 为什么不用 reqwest：校园网认证是**明文 HTTP**、体积优先，
//! 而且这层逻辑小到可以一眼看完 ✓（真正需要 TLS 的只有「检查更新」，M4 再单独处理）。
//!
//! 超时策略：连接与读写各自设超时（`set_read_timeout`），避免在某些校园网里
//! 「连上了但永远不回包」把整条检查链路挂死 ✗。

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::time::Duration;

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

/// 发一次 GET（只支持明文 http，够用 ✓）。
pub fn get(url: &str, timeout: Duration) -> Result<HttpReply, String> {
    let (host, port, path) =
        split_http_url(url).ok_or_else(|| "URL 形状不对（只支持 http://）".to_string())?;
    let addr = format!("{}:{}", host, port);
    let stream = TcpStream::connect(&addr).map_err(|e| format!("连接 {} 失败: {}", addr, e))?;
    stream.set_read_timeout(Some(timeout)).map_err(|e| e.to_string())?;
    stream.set_write_timeout(Some(timeout)).map_err(|e| e.to_string())?;
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
}
