//! 本地控制 API（127.0.0.1 上的极简 HTTP 服务）—— 3.0 的「控制面」。
//!
//! 沿用 2.x 的两条安全教训（v2.0.13.0）：
//!   1. **只绑回环**：绝不 0.0.0.0（这是安全模型的底线）；
//!   2. **每个响应都带安全头**（CSP `default-src 'none'` / nosniff / DENY / no-referrer），
//!      JSON 响应也一样 ✓；请求体超限先**有界排空**再回 413，否则 Windows 上
//!      客户端会因为「没读完就断连」直接 RST，413 根本到不了对端 ✗。
//!
//! 只用标准库：一个监听线程 + 每连接一个线程，够用且零依赖 ✓。

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::thread::JoinHandle;

pub const DEFAULT_UI_PORT: u16 = 8848;
/// JSON / 上传体的上限（1 MB，与 2.x 一致 ✓）。
pub const MAX_REQUEST_BODY: usize = 1024 * 1024;
/// 请求行 + 头部的上限（防止有人拿超长头把内存打满 ✗）。
const MAX_HEADER_BYTES: usize = 16 * 1024;

/// 本地 API 服务。
pub struct ApiServer {
    listener: TcpListener,
}

impl ApiServer {
    /// 绑定回环地址（`port = 0` 时由系统分配，测试用 ✓）。
    pub fn bind(port: u16) -> std::io::Result<ApiServer> {
        let listener = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, port)))?;
        Ok(ApiServer { listener })
    }

    pub fn local_port(&self) -> u16 {
        self.listener.local_addr().map(|a| a.port()).unwrap_or(0)
    }

    /// 起一个线程跑服务（返回端口与句柄；测试里用它 ✓）。
    pub fn spawn(self) -> (u16, JoinHandle<()>) {
        let port = self.local_port();
        let handle = std::thread::spawn(move || {
            for stream in self.listener.incoming() {
                match stream {
                    Ok(stream) => {
                        std::thread::spawn(move || {
                            let _ = handle_connection(stream);
                        });
                    }
                    Err(_) => break,
                }
            }
        });
        (port, handle)
    }
}

/// 一次请求里我们关心的部分。
struct RequestHead {
    method: String,
    path: String,
    content_length: usize,
}

fn handle_connection(mut stream: TcpStream) -> std::io::Result<()> {
    // 读写超时：既防「连上就不说话」的白占线程，也防我们自己在写响应时对端消失 ✓
    let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(5)));
    let _ = stream.set_write_timeout(Some(std::time::Duration::from_secs(5)));
    let head = match read_head(&stream)? {
        Some(head) => head,
        None => return Ok(()),
    };

    // 只接受 GET（3.0 M0 的 API 全是只读的 ✓）
    if head.method != "GET" {
        // 超限：**先把声明长度的体排空（有界）**，再回 413 —— 顺序反了会被 RST 冲掉 ✗
        if head.content_length > MAX_REQUEST_BODY {
            drain_bounded(&mut stream, head.content_length.saturating_add(4096));
            return respond(&mut stream, 413, "text/plain; charset=utf-8", "请求体过大\n");
        }
        drain_bounded(&mut stream, head.content_length);
        return respond_405(&mut stream);
    }

    match head.path.as_str() {
        "/api/health" => {
            let body = format!(
                "{{\"ok\":true,\"app\":\"{}\",\"version\":\"{}\",\"channel\":\"{}\"}}\n",
                drcom_core::APP_NAME,
                drcom_core::app_version_string(),
                drcom_core::app_version().channel.as_str()
            );
            respond(&mut stream, 200, "application/json; charset=utf-8", &body)
        }
        "/api/status" => {
            let status = drcom_core::Status::collect();
            let body = match serde_json::to_string(&status) {
                Ok(json) => format!("{}\n", json),
                Err(e) => return respond(&mut stream, 500, "text/plain; charset=utf-8", &format!("序列化失败: {}\n", e)),
            };
            respond(&mut stream, 200, "application/json; charset=utf-8", &body)
        }
        _ => respond(&mut stream, 404, "text/plain; charset=utf-8", "没有这个接口\n"),
    }
}

fn read_head(stream: &TcpStream) -> std::io::Result<Option<RequestHead>> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut line = String::new();
    if reader.read_line(&mut line)? == 0 {
        return Ok(None);
    }
    let mut parts = line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let path = parts.next().unwrap_or("").to_string();
    let mut content_length = 0usize;
    let mut consumed = line.len();
    loop {
        let mut header = String::new();
        let read = reader.read_line(&mut header)?;
        if read == 0 || header == "\r\n" || header == "\n" {
            break;
        }
        consumed += read;
        if consumed > MAX_HEADER_BYTES {
            return Ok(None);
        }
        if let Some((name, value)) = header.split_once(':') {
            if name.trim().eq_ignore_ascii_case("content-length") {
                content_length = value.trim().parse::<usize>().unwrap_or(0);
            }
        }
    }
    Ok(Some(RequestHead { method, path, content_length }))
}

/// 有界排空请求体：最多读 `MAX + 64 KB`（留出余量，避免留一截没读完又断连 → RST ✗）。
fn drain_bounded(stream: &mut TcpStream, len: usize) {
    let mut remaining = len.min(MAX_REQUEST_BODY + 64 * 1024);
    let mut buf = [0u8; 8192];
    while remaining > 0 {
        let want = remaining.min(buf.len());
        match stream.read(&mut buf[..want]) {
            Ok(0) => break,
            Ok(n) => remaining -= n,
            // 读超时 / 连接断了：直接放弃（马上要关连接了 ✓）
            Err(_) => break,
        }
    }
}

fn security_headers(payload: &str) -> String {
    format!(
        "Content-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\
         Cache-Control: no-store\r\n\
         Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\n\
         X-Content-Type-Options: nosniff\r\nX-Frame-Options: DENY\r\n\
         Referrer-Policy: no-referrer\r\n",
        payload,
        payload.len()
    )
}

fn respond(stream: &mut TcpStream, status: u16, content_type: &str, body: &str) -> std::io::Result<()> {
    let reason = match status {
        200 => "OK",
        404 => "Not Found",
        405 => "Method Not Allowed",
        413 => "Payload Too Large",
        _ => "Internal Server Error",
    };
    let mut headers = security_headers(content_type);
    if status == 405 {
        headers.push_str("Allow: GET\r\n");
    }
    let response = format!("HTTP/1.1 {} {}\r\n{}\r\n{}", status, reason, headers, body);
    stream.write_all(response.as_bytes())?;
    stream.flush()
}

fn respond_405(stream: &mut TcpStream) -> std::io::Result<()> {
    respond(stream, 405, "text/plain; charset=utf-8", "这个接口只支持 GET\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 原样发一段请求，读回完整响应（服务端 `Connection: close` → 能读到 EOF ✓）。
    fn raw_request(port: u16, request: &[u8]) -> String {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(10)))
            .unwrap();
        stream.write_all(request).unwrap();
        stream.flush().unwrap();
        let mut out = Vec::new();
        let _ = stream.read_to_end(&mut out);
        String::from_utf8_lossy(&out).into_owned()
    }

    fn start() -> u16 {
        ApiServer::bind(0).unwrap().spawn().0
    }

    #[test]
    fn health_returns_json_with_security_headers() {
        let port = start();
        let raw = raw_request(port, b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n");
        assert!(raw.starts_with("HTTP/1.1 200 OK"), "响应: {}", raw);
        assert!(raw.contains("\"ok\":true"), "响应: {}", raw);
        assert!(raw.contains("Content-Security-Policy: default-src 'none'"), "缺 CSP: {}", raw);
        assert!(raw.contains("X-Content-Type-Options: nosniff"), "缺 nosniff: {}", raw);
        assert!(raw.contains("X-Frame-Options: DENY"), "缺 DENY: {}", raw);
        assert!(raw.contains("Referrer-Policy: no-referrer"), "缺 referrer: {}", raw);
        assert!(raw.contains("Cache-Control: no-store"));
    }

    #[test]
    fn status_returns_platform_and_version_fields() {
        let port = start();
        let raw = raw_request(port, b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n");
        assert!(raw.starts_with("HTTP/1.1 200 OK"));
        let body = raw.split("\r\n\r\n").nth(1).unwrap_or("");
        let value: serde_json::Value = serde_json::from_str(body.trim()).expect("必须是 JSON");
        assert_eq!(value["app"], drcom_core::APP_NAME);
        assert!(value["version"].as_str().unwrap().starts_with("3.0.0.0"));
        assert!(value["is_prerelease"].as_bool().unwrap(), "开发期是预览版 ✓");
        assert!(!value["target"].as_str().unwrap().is_empty());
        // 只读接口：数据目录只出现在响应里，不该被创建 ✗
        assert!(value["data_dir"].as_str().unwrap().len() > 3);
    }

    #[test]
    fn unknown_path_404_and_post_405() {
        let port = start();
        let not_found = raw_request(port, b"GET /api/nope HTTP/1.1\r\nHost: x\r\n\r\n");
        assert!(not_found.starts_with("HTTP/1.1 404 Not Found"), "响应: {}", not_found);
        let post = raw_request(
            port,
            b"POST /api/status HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nhello",
        );
        assert!(post.starts_with("HTTP/1.1 405 Method Not Allowed"), "响应: {}", post);
        assert!(post.contains("Allow: GET"), "405 要带 Allow: {}", post);
    }

    #[test]
    fn oversized_body_gets_413_after_bounded_drain() {
        let port = start();
        let len = MAX_REQUEST_BODY + 1;
        let mut request = format!(
            "POST /api/status HTTP/1.1\r\nHost: x\r\nContent-Length: {}\r\n\r\n",
            len
        )
        .into_bytes();
        request.extend(std::iter::repeat(b'x').take(len));
        let raw = raw_request(port, &request);
        assert!(raw.starts_with("HTTP/1.1 413 Payload Too Large"), "响应: {}", &raw[..60.min(raw.len())]);
    }

    #[test]
    fn empty_connection_is_ignored_without_panic() {
        let port = start();
        let stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
        drop(stream); // 连上就断 → 服务端应安静地关掉，不是 panic ✓
        let raw = raw_request(port, b"GET /api/health HTTP/1.1\r\nHost: x\r\n\r\n");
        assert!(raw.starts_with("HTTP/1.1 200 OK"), "服务端还活着 ✓");
    }
}

