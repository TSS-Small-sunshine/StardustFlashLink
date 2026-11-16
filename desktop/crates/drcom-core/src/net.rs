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

    /// 带**自定义请求头**的 GET（例如检查更新要发 Accept: application/vnd.github+json）。
    ///
    /// 给了默认实现（忽略请求头、退回 HttpGet::get）—— 这样既有的假客户端 / 门户探测
    /// 实现**一行都不用改** ✓，新实现（PlainHttp / CurlHttp）再各自动真格 ✓。
    fn get_with_headers(
        &self,
        url: &str,
        _headers: &[(&str, &str)],
        timeout: Duration,
    ) -> Result<HttpReply, String> {
        self.get(url, timeout)
    }
}

/// 抽象的**流式下载**（自动更新用）—— 与 HttpGet 分开是必要的 ✓：
///
/// HttpReply.body 是 String（from_utf8_lossy），**二进制安装包过它会被改坏** ✗，
/// 所以下载必须另走一条「写文件」的路径；fake 实现也因此能精确模拟「中途失败」「超大小」✓。
pub trait HttpDownload {
    /// 把 url 的响应体写进 dest（由调用方给 .part 路径）。
    ///
    /// 约定：**最多写 max_bytes 字节**，超了必须中止并返回 Err（已写的半截由调用方清理 ✓）；
    /// 返回实际写入的字节数 ✓。
    fn download(
        &self,
        url: &str,
        dest: &std::path::Path,
        max_bytes: u64,
        timeout: Duration,
    ) -> Result<u64, String>;
}

/// 真实实现（走标准库 TcpStream，**只支持明文 http**）。
pub struct PlainHttp;

impl HttpGet for PlainHttp {
    fn get(&self, url: &str, timeout: Duration) -> Result<HttpReply, String> {
        get(url, timeout)
    }

    fn get_with_headers(
        &self,
        url: &str,
        headers: &[(&str, &str)],
        timeout: Duration,
    ) -> Result<HttpReply, String> {
        get_with_headers(url, headers, timeout)
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

/// 构建 GET 请求（纯函数 ✓）：请求头注射 / 覆盖 Host 一律拒绝 ✓。
///
/// 默认带 User-Agent 与 Accept: */*；调用方给了同名头就用调用方的（不重复发 ✓）。
pub fn build_get_request(
    path: &str,
    host: &str,
    headers: &[(&str, &str)],
) -> Result<String, String> {
    if host.is_empty() || host.contains(char::is_whitespace) || host.contains('\r') || host.contains('\n') {
        return Err("Host 不合法（空 / 空白 / 换行）".to_string());
    }
    if path.is_empty() || path.contains(char::is_whitespace) || path.contains('\r') || path.contains('\n') {
        return Err("URL 路径不合法（空 / 空白 / 换行）".to_string());
    }
    let mut extra = String::new();
    let mut has_user_agent = false;
    let mut has_accept = false;
    for (name, value) in headers {
        if name.is_empty()
            || name.contains(':')
            || name.contains(char::is_whitespace)
            || value.contains('\r')
            || value.contains('\n')
        {
            return Err("请求头里有非法字符（名字不能带冒号/空白，值不能带换行）".to_string());
        }
        if name.eq_ignore_ascii_case("host") {
            return Err("请求头里不能覆盖 Host".to_string());
        }
        has_user_agent |= name.eq_ignore_ascii_case("user-agent");
        has_accept |= name.eq_ignore_ascii_case("accept");
        extra.push_str(name);
        extra.push_str(": ");
        extra.push_str(value);
        extra.push_str("\r\n");
    }
    let mut request = format!("GET {} HTTP/1.1\r\nHost: {}\r\n", path, host);
    if !has_user_agent {
        request.push_str("User-Agent: StardustFlashLink/3.0\r\n");
    }
    if !has_accept {
        request.push_str("Accept: */*\r\n");
    }
    request.push_str(&extra);
    request.push_str("Connection: close\r\n\r\n");
    Ok(request)
}

/// 发一次 GET（只支持明文 http，够用 ✓）。
///
/// `timeout` 是**这一次请求的总预算**：连接 + 写请求 + 读响应头 + 读响应体 ✓。
/// 连接阶段有硬超时 → 目标被 DROP 时也会在 `timeout` 内返回 `Err` ✓（审计 P2-2）。
pub fn get(url: &str, timeout: Duration) -> Result<HttpReply, String> {
    get_with_headers(url, &[], timeout)
}

/// 带自定义请求头的 GET（只支持明文 http；检查更新走 HTTPS 时用 CurlHttp ✓）。
pub fn get_with_headers(
    url: &str,
    headers: &[(&str, &str)],
    timeout: Duration,
) -> Result<HttpReply, String> {
    let deadline = Instant::now() + timeout;
    let (host, port, path) =
        split_http_url(url).ok_or_else(|| "URL 形状不对（只支持 http://）".to_string())?;
    let request = build_get_request(&path, &host, headers)?;
    let stream = connect_with_timeout(&host, port, timeout)?;
    // 读 / 写超时用**剩下的**预算：connect 用掉多少就从后面扣掉 ✓
    let budget = remaining_budget(deadline, Instant::now(), timeout);
    stream.set_read_timeout(Some(budget)).map_err(|e| e.to_string())?;
    stream.set_write_timeout(Some(budget)).map_err(|e| e.to_string())?;
    let mut writer = stream.try_clone().map_err(|e| e.to_string())?;
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

/// JSON 响应体的上限（8 MB）—— GitHub releases 列表远小于它；防的是被中间设备塞超大响应 ✓。
const MAX_JSON_BYTES: usize = 8 * 1024 * 1024;

/// 系统 curl 的 HTTPS 传输（**不是 crate**；R1 不许加依赖，std 里也没有 TLS ✗）。
///
/// 为什么这么干（M4 的取舍）：
///   1. 检查更新 / 下载安装包只能走 HTTPS（GitHub：api.github.com / github.com），
///      而上面的 get 是手写的**明文** HTTP（连 TLS 握手都没有）✗；
///   2. 自己实现 TLS 1.3 需要 X25519 / AES-GCM / 证书链校验 —— 属于「自研密码学」，
///      审计上不可接受 ✗；
///   3. Windows 10 1803+ / macOS / 主流 Linux 都自带 curl ✓ —— 与「Windows 靠 NSSM、
///      CI 靠 dpkg-deb/hdiutil」同一定位：**系统工具，不是 crate 依赖** ✓；
///   4. 找不到 curl 时返回可读中文错误，绝不 panic ✓；校园网明文 HTTP 仍走 PlainHttp ✓。
pub struct CurlHttp;

impl CurlHttp {
    /// curl 在不在（跑一次 curl --version，只读 ✓）。
    pub fn available() -> bool {
        std::process::Command::new("curl")
            .arg("--version")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }

    /// 找不到 curl 时的**可读中文错误**（不要只报 io::Error）✓。
    fn missing_hint() -> String {
        "HTTPS 请求失败：启动 curl 失败。检查更新 / 下载安装包需要 HTTPS，而标准库没有 TLS，".to_string()
            + "这里用系统自带的 curl（Windows 10 1803+ / macOS / 主流 Linux 都自带 ✓）；"
            + "请确认 curl 在 PATH 里（Windows 自带 C:\\Windows\\System32\\curl.exe）"
    }

    /// 公共的 curl 安全参数（纯函数 ✓）：只允许 https，跟不跟重定向都只许 https ✓。
    fn common_args(url: &str, timeout: Duration) -> Result<Vec<String>, String> {
        if !url.starts_with("https://") {
            return Err("只允许 https:// 地址（明文 http 会被拒绝 ✓）".to_string());
        }
        let connect = timeout.min(Duration::from_secs(30));
        Ok(vec![
            "-sS".to_string(),
            "-L".to_string(),
            "--proto".to_string(),
            "=https".to_string(),
            "--proto-redir".to_string(),
            "=https".to_string(),
            "--max-time".to_string(),
            curl_timeout_arg(timeout),
            "--connect-timeout".to_string(),
            curl_timeout_arg(connect),
            "-A".to_string(),
            "StardustFlashLink/3.0".to_string(),
        ])
    }
}

/// curl 的超时参数（纯函数，好测 ✓）：--max-time 收秒（可带小数），且必须 > 0。
fn curl_timeout_arg(timeout: Duration) -> String {
    format!("{:.3}", timeout.as_secs_f64().max(0.001))
}

/// 截断到 max 个字符（错误文案别把整段 stderr 甩给用户 ✓）。
fn truncate_text(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let mut out: String = text.chars().take(max).collect();
    out.push('…');
    out
}

impl HttpGet for CurlHttp {
    fn get(&self, url: &str, timeout: Duration) -> Result<HttpReply, String> {
        self.get_with_headers(url, &[], timeout)
    }

    fn get_with_headers(
        &self,
        url: &str,
        headers: &[(&str, &str)],
        timeout: Duration,
    ) -> Result<HttpReply, String> {
        let mut args = CurlHttp::common_args(url, timeout)?;
        for (name, value) in headers {
            if name.is_empty()
                || name.contains(':')
                || name.contains(char::is_whitespace)
                || value.contains('\r')
                || value.contains('\n')
            {
                return Err("请求头里有非法字符（名字不能带冒号/空白，值不能带换行）".to_string());
            }
            args.push("-H".to_string());
            args.push(format!("{}: {}", name, value));
        }
        let body_path =
            crate::atomicfile::unique_tmp_path(&std::env::temp_dir().join("sfl-curl-body"));
        args.push("-o".to_string());
        args.push(body_path.to_string_lossy().into_owned());
        args.push("-w".to_string());
        args.push("%{http_code}".to_string());
        args.push(url.to_string());
        match CurlHttp::run_get(&args, &body_path) {
            Ok(reply) => Ok(reply),
            Err(failure) => {
                // Windows Schannel 的吊销检查在受限网络里会误报 → 只对这种失败重试一次 ✓
                if failure.retry_without_revocation && can_disable_revocation() {
                    let mut retry = args.clone();
                    retry.insert(retry.len() - 1, "--ssl-no-revoke".to_string());
                    return CurlHttp::run_get(&retry, &body_path).map_err(|failure| failure.message);
                }
                Err(failure.message)
            }
        }
    }
}

/// 一次 curl 取文本的失败：可读文案 + 是否值得关吊销检查重试 ✓。
struct CurlFailure {
    message: String,
    retry_without_revocation: bool,
}

impl CurlHttp {
    fn run_get(args: &[String], body_path: &std::path::Path) -> Result<HttpReply, CurlFailure> {
        let mut command = std::process::Command::new("curl");
        command.args(args);
        command.stdin(std::process::Stdio::null());
        let output = match command.output() {
            Ok(output) => output,
            Err(_) => {
                let _ = std::fs::remove_file(body_path);
                return Err(CurlFailure {
                    message: CurlHttp::missing_hint(),
                    retry_without_revocation: false,
                });
            }
        };
        let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
        let code_text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        // status 0 / 解析不出来 = 连不上（curl 失败时 %{http_code} 是 000）✗
        let status = code_text.parse::<u16>().ok().filter(|code| *code != 0);
        let Some(status) = status else {
            let _ = std::fs::remove_file(body_path);
            return Err(CurlFailure {
                message: format!(
                    "HTTPS 请求失败：{}",
                    curl_failure_text(&stderr, output.status.code())
                ),
                retry_without_revocation: is_revocation_check_failure(&stderr),
            });
        };
        let raw = std::fs::read(body_path).unwrap_or_default();
        let _ = std::fs::remove_file(body_path);
        if raw.len() > MAX_JSON_BYTES {
            return Err(CurlFailure {
                message: format!("响应体过大（{} 字节，上限 {}）", raw.len(), MAX_JSON_BYTES),
                retry_without_revocation: false,
            });
        }
        Ok(HttpReply { status, location: None, body: String::from_utf8_lossy(&raw).into_owned() })
    }

    /// 一次流式下载（调用方负责清理失败后的半成品 ✓）。
    fn stream_once(
        args: &[String],
        dest: &std::path::Path,
        max_bytes: u64,
    ) -> Result<u64, CurlFailure> {
        let mut command = std::process::Command::new("curl");
        command.args(args);
        command.stdin(std::process::Stdio::null());
        command.stdout(std::process::Stdio::piped());
        command.stderr(std::process::Stdio::piped());
        let mut child = command.spawn().map_err(|_| CurlFailure {
            message: CurlHttp::missing_hint(),
            retry_without_revocation: false,
        })?;
        let mut stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(CurlFailure {
                    message: "拿不到 curl 的输出流".to_string(),
                    retry_without_revocation: false,
                });
            }
        };
        let stderr = child.stderr.take();
        let drain = std::thread::spawn(move || {
            let mut text = String::new();
            if let Some(mut err) = stderr {
                let _ = err.read_to_string(&mut text);
            }
            text
        });
        let mut file = match std::fs::File::create(dest) {
            Ok(file) => file,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = drain.join();
                return Err(CurlFailure {
                    message: format!("写不了 {}: {}", dest.display(), error),
                    retry_without_revocation: false,
                });
            }
        };
        let mut buffer = [0u8; 64 * 1024];
        let mut written: u64 = 0;
        let mut failure: Option<CurlFailure> = None;
        loop {
            match stdout.read(&mut buffer) {
                Ok(0) => break,
                Ok(read) => {
                    written += read as u64;
                    if written > max_bytes {
                        let _ = child.kill();
                        let _ = child.wait();
                        failure = Some(CurlFailure {
                            message: format!("下载体积超过上限（{} 字节）—— 已中止", max_bytes),
                            retry_without_revocation: false,
                        });
                        break;
                    }
                    if let Err(error) = file.write_all(&buffer[..read]) {
                        let _ = child.kill();
                        let _ = child.wait();
                        failure = Some(CurlFailure {
                            message: format!("写文件失败: {}", error),
                            retry_without_revocation: false,
                        });
                        break;
                    }
                }
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    failure = Some(CurlFailure {
                        message: format!("读下载流失败: {}", error),
                        retry_without_revocation: false,
                    });
                    break;
                }
            }
        }
        let sync_result = file.sync_all();
        drop(file);
        let exit = match failure {
            Some(_) => None,
            None => child.wait().ok(),
        };
        let stderr_text = drain.join().unwrap_or_default();
        if let Some(failure) = failure {
            return Err(failure);
        }
        if let Err(error) = sync_result {
            return Err(CurlFailure {
                message: format!("冲刷下载文件失败: {}", error),
                retry_without_revocation: false,
            });
        }
        match exit {
            Some(status) if status.success() => Ok(written),
            other => {
                let code = other.as_ref().and_then(|status| status.code());
                Err(CurlFailure {
                    message: format!(
                        "下载失败（curl 退出码 {:?}）：{}",
                        code,
                        curl_failure_text(&stderr_text, code)
                    ),
                    retry_without_revocation: is_revocation_check_failure(&stderr_text),
                })
            }
        }
    }
}

impl HttpDownload for CurlHttp {
    fn download(
        &self,
        url: &str,
        dest: &std::path::Path,
        max_bytes: u64,
        timeout: Duration,
    ) -> Result<u64, String> {
        let base = CurlHttp::common_args(url, timeout)?;
        let build = |with_no_revoke: bool| {
            let mut args = base.clone();
            if with_no_revoke {
                args.push("--ssl-no-revoke".to_string());
            }
            args.push("-o".to_string());
            args.push("-".to_string());
            args.push(url.to_string());
            args
        };
        match CurlHttp::stream_once(&build(false), dest, max_bytes) {
            Ok(bytes) => Ok(bytes),
            Err(failure) => {
                if failure.retry_without_revocation && can_disable_revocation() {
                    let _ = std::fs::remove_file(dest);
                    return CurlHttp::stream_once(&build(true), dest, max_bytes)
                        .map_err(|failure| failure.message);
                }
                Err(failure.message)
            }
        }
    }
}

/// 是不是 Windows Schannel 的「证书吊销检查失败」？
///
/// 已知坑：代理 / 校园网挡住 CRL/OCSP 服务器时，**合法证书**也会报
/// CRYPT_E_NO_REVOCATION_CHECK（curl 退出码 35）✗ —— 这种特定失败才关掉吊销检查重试一次。
fn is_revocation_check_failure(stderr: &str) -> bool {
    stderr.contains("CRYPT_E_NO_REVOCATION_CHECK")
        || stderr.to_ascii_lowercase().contains("revocation")
}

/// 只有 Windows 的 Schannel 支持 --ssl-no-revoke（其它平台的 curl 会报未知参数 ✗）。
fn can_disable_revocation() -> bool {
    crate::platform::Os::current() == crate::platform::Os::Windows
}

/// curl 失败文案：优先用 stderr（已脱敏），没有就给退出码说明 ✓。
fn curl_failure_text(stderr: &str, exit_code: Option<i32>) -> String {
    let trimmed = stderr.trim();
    if !trimmed.is_empty() {
        return crate::secret::scrub_url(&truncate_text(trimmed, 300));
    }
    match exit_code {
        Some(code) => format!("curl 退出码 {}（网络不通或被拦截？）", code),
        None => "curl 异常结束（网络不通或被拦截？）".to_string(),
    }
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

    #[test]
    fn get_request_has_default_headers_and_honours_overrides() {
        let request = build_get_request("/x", "api.github.com", &[]).unwrap();
        assert!(request.starts_with("GET /x HTTP/1.1\r\nHost: api.github.com\r\n"), "{}", request);
        assert!(request.contains("User-Agent: StardustFlashLink/3.0\r\n"));
        assert!(request.contains("Accept: */*\r\n"));
        assert!(request.ends_with("Connection: close\r\n\r\n"), "请求头必须以空行结束 ✓");
        // 自定义 Accept：默认的 Accept 不能重复发（GitHub 只看得到第一个/合并结果 ✗）
        let request =
            build_get_request("/x", "api.github.com", &[("Accept", "application/vnd.github+json")])
                .unwrap();
        assert!(request.contains("Accept: application/vnd.github+json\r\n"), "{}", request);
        assert!(!request.contains("Accept: */*"), "默认 Accept 不该再出现: {}", request);
        // 自定义 UA：同理
        let request = build_get_request("/x", "h", &[("User-Agent", "Other/1")]).unwrap();
        assert!(request.contains("User-Agent: Other/1\r\n"));
        assert!(!request.contains("StardustFlashLink"), "默认 UA 不该再出现: {}", request);
    }

    #[test]
    fn get_request_rejects_header_injection_and_host_override() {
        // 值里塞 CRLF = 请求头注入（能伪造整条请求 ✗）
        assert!(build_get_request("/x", "h", &[("X", "a\r\nInjected: 1")]).is_err());
        assert!(build_get_request("/x", "h", &[("X", "a\nb")]).is_err());
        // 名字里带冒号 / 空白 = 改写头名
        assert!(build_get_request("/x", "h", &[("X: Y", "1")]).is_err());
        assert!(build_get_request("/x", "h", &[("X Y", "1")]).is_err());
        assert!(build_get_request("/x", "h", &[("", "1")]).is_err());
        // Host 不许被调用方覆盖（连到 A 却告诉服务器 B ✗）
        assert!(build_get_request("/x", "h", &[("Host", "evil")]).is_err());
        assert!(build_get_request("/x", "h", &[("host", "evil")]).is_err());
        // 路径里的空白 / 换行也要拒绝
        assert!(build_get_request("/a b", "h", &[]).is_err());
        assert!(build_get_request("/a\r\n", "h", &[]).is_err());
    }

    #[test]
    fn curl_timeout_argument_is_a_positive_second_value() {
        assert_eq!(curl_timeout_arg(Duration::from_secs(8)), "8.000");
        assert_eq!(curl_timeout_arg(Duration::from_millis(500)), "0.500");
        // 0 或极小值也要给正数（--max-time 0 在 curl 里是「不限制」✗）
        assert_eq!(curl_timeout_arg(Duration::ZERO), "0.001");
    }

    #[test]
    fn curl_transport_refuses_plain_http_and_never_touches_disk() {
        let dir = std::env::temp_dir().join(format!("sfl-curl-http-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("asset.part");
        let err = CurlHttp
            .download("http://api.github.com/repos/x", &target, 10, Duration::from_millis(50))
            .expect_err("明文 http 必须被拒绝");
        assert!(err.contains("https"), "错误要能看懂: {}", err);
        assert!(!target.exists(), "拒绝时不该落盘 ✗");
        let err = CurlHttp
            .get_with_headers("http://api.github.com/x", &[], Duration::from_millis(50))
            .expect_err("明文 http 必须被拒绝");
        assert!(err.contains("https"), "{}", err);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn curl_available_probe_is_safe_and_does_not_panic() {
        // 只要求「能探测、不 panic」；有没有 curl 由机器决定（Windows 10+ / macOS / 多数 Linux 自带 ✓）
        let _ = CurlHttp::available();
        let _ = truncate_text("abcdef", 3);
        assert_eq!(truncate_text("abcdef", 3), "abc…");
        assert_eq!(truncate_text("ab", 3), "ab");
    }

    #[test]
    fn revocation_detection_only_matches_the_schannel_failure() {
        // 真机上抓到的原文（Windows Schannel；中文是控制台编码问题，关键字不变 ✓）
        let real = "curl: (35) schannel: next InitializeSecurityContext failed: CRYPT_E_NO_REVOCATION_CHECK (0x80092012)";
        assert!(is_revocation_check_failure(real));
        assert!(is_revocation_check_failure("schannel: CERT_E_REVOCATION_FAILURE"));
        // 其它错误不许误触发「关掉吊销检查」重试 ✗
        assert!(!is_revocation_check_failure("curl: (6) Could not resolve host"));
        assert!(!is_revocation_check_failure("curl: (7) Failed to connect"));
        assert!(!is_revocation_check_failure(""));
    }

    #[test]
    fn curl_failure_text_is_readable_and_scrubbed() {
        // 有 stderr：用它（并擦掉 URL / 密码）
        let text = curl_failure_text("boom https://api.github.com/x?token=abc", Some(35));
        assert!(text.contains("<url>"), "{}", text);
        assert!(!text.contains("token=abc"), "{}", text);
        // 没有 stderr：退回退出码，也要可读
        let text = curl_failure_text("   ", Some(7));
        assert!(text.contains("curl 退出码 7"), "{}", text);
        assert!(text.contains("网络"), "{}", text);
        let text = curl_failure_text("", None);
        assert!(!text.is_empty());
    }
}
