//! Dr.COM 协议层（纯函数，不碰网络、不碰文件 —— 好测、跨平台零负担）。
//!
//! 与 2.x（`protocol.py`）的契约一致：
//!   - 在线检查：`GET http://<host>/drcom/chkstatus?callback=cb&jsVersion=4.X` → `cb({"result":1,...})`
//!   - 登录请求：`GET http://<host>:801/eportal/portal/login?...&user_password=...` → `dr1234({...})`
//!
//! ⚠️ 登录是 GET，**密码在 URL 里** —— 这个模块产出的 URL 绝不能进日志，
//! 记录/回显一律走 [`crate::secret::scrub_url`]。

use serde_json::Value;

/// 登录端口固定 801（与 2.x 一致；`config.port` 指的是网关 HTTP 端口，不参与登录）。
pub const LOGIN_PORT: u16 = 801;

/// 一次登录请求所需的字段。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoginRequest<'a> {
    pub host: &'a str,
    pub account: &'a str,
    pub suffix: &'a str,
    pub password: &'a str,
    pub ip: &'a str,
    pub mac: &'a str,
}

/// 解析失败的原因（细分几类，便于界面给出可读提示）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// 响应里找不到 JSONP 外壳 `cb({...})`
    NotJsonp,
    /// JSONP 里的 JSON 坏了
    BadJson,
    /// JSON 合法但不是预期结构（如缺 `result`）
    UnexpectedShape,
}

impl ParseError {
    pub fn label_cn(&self) -> &'static str {
        match self {
            ParseError::NotJsonp => "响应不是 JSONP",
            ParseError::BadJson => "JSON 解析失败",
            ParseError::UnexpectedShape => "响应结构不符合预期",
        }
    }
}

/// 在线状态。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OnlineState {
    Online,
    Offline,
    /// 拿不到结果（网络异常 / 响应无法解析）
    Unknown,
}

impl OnlineState {
    /// 从 `result` 字段推状态（1 = 在线，0 = 未在线，其它 = 未知）。
    pub fn from_result(value: Option<&Value>) -> OnlineState {
        match value.and_then(|v| v.as_i64()) {
            Some(1) => OnlineState::Online,
            Some(0) => OnlineState::Offline,
            _ => OnlineState::Unknown,
        }
    }
}

/// 登录结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoginReply {
    pub success: bool,
    pub msg: String,
}

/// `query` 值的百分号编码（unreserved 之外全部转义）。
///
/// 不引第三方库：这个函数只有十几行，且必须与 Python `urlencode` 行为一致 ✓
/// （`@`、`#`、`+`、空格 这类在密码里很常见的字符都要转义）。
pub fn percent_encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for byte in value.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char)
            }
            other => out.push_str(&format!("%{:02X}", other)),
        }
    }
    out
}

/// 拼登录 URL（字段顺序与 2.x 一致，便于抓包比对 ✓）。
pub fn build_login_url(req: &LoginRequest<'_>, callback: &str, nonce: &str) -> String {
    let mut q: Vec<String> = Vec::with_capacity(13);
    q.push(format!("callback={}", percent_encode(callback)));
    q.push("login_method=1".to_string());
    q.push(format!(
        "user_account={}",
        percent_encode(&format!("{}{}", req.account, req.suffix))
    ));
    q.push(format!("user_password={}", percent_encode(req.password)));
    q.push(format!("wlan_user_ip={}", percent_encode(req.ip)));
    q.push("wlan_user_ipv6=".to_string());
    q.push(format!("wlan_user_mac={}", percent_encode(req.mac)));
    q.push("wlan_ac_ip=".to_string());
    q.push("wlan_ac_name=".to_string());
    q.push("terminal_type=1".to_string());
    q.push("jsVersion=4.1.3".to_string());
    q.push("lang=zh-cn".to_string());
    q.push(format!("v={}", percent_encode(nonce)));
    format!("http://{}:{}/eportal/portal/login?{}", req.host, LOGIN_PORT, q.join("&"))
}

/// 拼在线检查 URL（端口 80，路径与 2.x 一致）。
pub fn build_online_check_url(host: &str, callback: &str) -> String {
    format!("http://{}/drcom/chkstatus?callback={}&jsVersion=4.X", host, callback)
}

/// 从 `cb({...})` / `dr1234({...});` 里取出最外层花括号的内容。
pub fn extract_jsonp_payload(text: &str) -> Result<&str, ParseError> {
    let open = text.find('(').ok_or(ParseError::NotJsonp)?;
    let close = text.rfind(')').ok_or(ParseError::NotJsonp)?;
    if close <= open {
        return Err(ParseError::NotJsonp);
    }
    let inner = text[open + 1..close].trim();
    let inner = inner.trim_end_matches(';').trim();
    if !inner.starts_with('{') || !inner.ends_with('}') {
        return Err(ParseError::NotJsonp);
    }
    Ok(inner)
}

/// 解析 JSONP 响应 → `serde_json::Value`。
pub fn parse_jsonp(text: &str) -> Result<Value, ParseError> {
    let payload = extract_jsonp_payload(text)?;
    serde_json::from_str(payload).map_err(|_| ParseError::BadJson)
}

/// 解析登录响应。
pub fn parse_login_reply(text: &str) -> Result<LoginReply, ParseError> {
    let value = parse_jsonp(text)?;
    let result = value
        .get("result")
        .ok_or(ParseError::UnexpectedShape)?
        .as_i64()
        .ok_or(ParseError::UnexpectedShape)?;
    let msg = value.get("msg").and_then(|v| v.as_str()).unwrap_or("").to_string();
    Ok(LoginReply { success: result == 1, msg })
}

/// 解析在线状态响应。
pub fn parse_online_state(text: &str) -> Result<OnlineState, ParseError> {
    let value = parse_jsonp(text)?;
    if value.get("result").is_none() {
        return Ok(OnlineState::Unknown);
    }
    Ok(OnlineState::from_result(value.get("result")))
}

#[cfg(test)]
mod tests {
    use super::*;

    const PWD: &str = "#Fake Pwd+7777#";

    fn req() -> LoginRequest<'static> {
        LoginRequest {
            host: "172.16.80.3",
            account: "2023001234",
            suffix: "@yd",
            password: PWD,
            ip: "10.20.30.40",
            mac: "E25B367B8DAC",
        }
    }

    #[test]
    fn login_url_shape_matches_v2() {
        let url = build_login_url(&req(), "dr1234", "5678");
        assert!(url.starts_with("http://172.16.80.3:801/eportal/portal/login?"));
        assert!(url.contains("login_method=1"));
        assert!(url.contains("user_account=2023001234%40yd"), "账号+后缀要合并并转义: {}", url);
        assert!(url.contains("jsVersion=4.1.3"));
        assert!(url.contains("terminal_type=1"));
        assert!(url.contains("lang=zh-cn"));
        assert!(url.contains("v=5678"));
        // 密码必须被转义（空格 → %20，+ → %2B —— 表单里两者语义不同 ✗）
        assert!(url.contains("user_password=%23Fake%20Pwd%2B7777%23"), "密码转义不对: {}", url);
        assert!(!url.contains(' '), "URL 里不该有裸空格");
    }

    #[test]
    fn online_check_url_shape_matches_v2() {
        assert_eq!(
            build_online_check_url("172.16.80.3", "cb"),
            "http://172.16.80.3/drcom/chkstatus?callback=cb&jsVersion=4.X"
        );
    }

    #[test]
    fn jsonp_parsing_handles_real_shapes() {
        let ok = parse_login_reply("dr1234({\"result\":1,\"msg\":\"登录成功\",\"ret_code\":0});")
            .unwrap();
        assert!(ok.success);
        assert_eq!(ok.msg, "登录成功");
        let fail = parse_login_reply("dr1234({\"result\":0,\"msg\":\"密码错误\"})").unwrap();
        assert!(!fail.success);
        assert_eq!(fail.msg, "密码错误");
        // 前后空白 / 分号 / BOM 都能忍
        let messy = parse_login_reply("  \u{feff}cb({ \"result\" : 1 , \"msg\" : \"ok\" });  ")
            .unwrap();
        assert!(messy.success);
        assert_eq!(parse_login_reply("<html>portal</html>"), Err(ParseError::NotJsonp));
        assert_eq!(parse_login_reply("cb({oops})"), Err(ParseError::BadJson));
        assert_eq!(
            parse_login_reply("cb({\"msg\":\"no result\"})"),
            Err(ParseError::UnexpectedShape)
        );
        // 空 msg 不炸（2.x 里 msg 可能缺）
        assert_eq!(parse_login_reply("cb({\"result\":1})").unwrap().msg, "");
    }

    #[test]
    fn online_state_mapping() {
        assert_eq!(
            parse_online_state("cb({\"result\":1,\"uid\":\"x\"})").unwrap(),
            OnlineState::Online
        );
        assert_eq!(parse_online_state("cb({\"result\":0})").unwrap(), OnlineState::Offline);
        assert_eq!(parse_online_state("cb({\"result\":\"weird\"})").unwrap(), OnlineState::Unknown);
        assert_eq!(OnlineState::from_result(None), OnlineState::Unknown);
    }

    #[test]
    fn percent_encode_matches_python_urlencode_for_common_secrets() {
        assert_eq!(percent_encode("abc123-_.~"), "abc123-_.~");
        assert_eq!(percent_encode("a b"), "a%20b");
        assert_eq!(percent_encode("a+b&c=d"), "a%2Bb%26c%3Dd");
        assert_eq!(percent_encode("2023001234@yd"), "2023001234%40yd");
        assert_eq!(percent_encode("中文"), "%E4%B8%AD%E6%96%87");
    }
}

