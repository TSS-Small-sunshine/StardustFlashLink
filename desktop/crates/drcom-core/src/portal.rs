//! 门户（portal）检测：**「是不是被校园网门户拦住了」**。
//!
//! 实地情报（2026-09 实测，`wlanuserip=172.16.59.11`）：未认证时访问普通 http，
//! 会被华为 AC 重定向到登录页 ——
//!
//! ```text
//! http://172.16.80.3/a79.htm?mac=241C-0408-BDD3&rul=http://9.9.9.9/
//!   &wlanacip=172%2e16%2e80%2e2&wlanacname=SR8806%2dX%2dS&wlanuserip=172.16.59.11
//! ```
//!
//! 拆开看：
//!   - `a79.htm` 是登录页 —— **公共区域和宿舍用的是同一个页面** ✓（别再去找 a41/a49 ✗）；
//!   - `wlanacip` / `wlanacname` / `wlanuserip` / `mac` / `rul` 是华为 AC 的标准参数名；
//!   - `%2e`→`.`、`%2d`→`-`：参数值是**百分号编码**的，必须先解码再判定 ✓；
//!   - 真正的登录仍是 `:801/eportal/portal/login` ✓ —— `a79.htm` 只是壳，不改协议 ✗。
//!
//! 本模块只做**判定**：不代填表单、不驱动浏览器 ✓（登录一律走 [`crate::protocol`] ✓）。

use crate::net::{HttpGet, HttpReply};
use std::time::Duration;

/// 默认探测目标（公网明文地址，实测未认证时就是它被拦下的 ✓）。
pub const DEFAULT_PROBE_URL: &str = "http://9.9.9.9/";

/// 门户页特征词（出现在 URL 或正文里就认为「这是门户」✓）。
pub const PORTAL_MARKERS: &[&str] = &[
    "a79.htm",
    "/eportal/",
    "wlanuserip=",
    "wlanacip=",
    "wlanacname=",
];

/// 门户页给出的情报（华为 AC 的重定向参数字段 ✓）。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PortalHint {
    /// 门户页所在主机（如 `172.16.80.3`）
    pub portal_host: String,
    /// 门户页路径（如 `/a79.htm`）
    pub page: String,
    /// AC 认为我们的地址（`wlanuserip`）
    pub user_ip: Option<String>,
    /// 接入控制器地址（`wlanacip`）
    pub ac_ip: Option<String>,
    /// 接入控制器名字（`wlanacname`，如 `SR8806-X-S`）
    pub ac_name: Option<String>,
    /// AC 看到的本机 MAC（`mac`，华为格式 `241C-0408-BDD3`）
    pub mac: Option<String>,
    /// 被拦下之前想去的地址（`rul`）
    pub redirect: Option<String>,
}

/// 一次门户探测的结论。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PortalVerdict {
    /// 能正常访问 → 已认证（或本来就不需要认证 ✓）
    Online,
    /// 被门户拦下 → 需要登录（附门户页情报 ✓）
    Blocked(PortalHint),
    /// 连不上（超时 / 拒绝）→ 可能没网。
    /// **不能据此说「未认证」** ✗ —— 有些 AC 是直接丢包而不是重定向。
    Unreachable(String),
    /// 连上了、但既不像正常页面也不像门户页（别的中间设备？）
    Unknown(String),
}

impl PortalVerdict {
    /// 给界面 / 日志的一行中文结论 ✓。
    pub fn summary_cn(&self) -> String {
        match self {
            PortalVerdict::Online => "能正常访问外网 —— 已在线，不需要登录 ✓".to_string(),
            PortalVerdict::Blocked(hint) => {
                let mut text = format!(
                    "被校园网门户拦住了 → 需要登录（门户页 http://{}{}）",
                    hint.portal_host, hint.page
                );
                if let Some(ip) = hint.user_ip.as_deref() {
                    text.push_str(&format!("，门户看到的地址 {}", ip));
                }
                text
            }
            PortalVerdict::Unreachable(detail) => {
                format!("探测目标连不上：{}（这**不能**说明未认证 ✗）", detail)
            }
            PortalVerdict::Unknown(detail) => format!("探测目标有响应但看不懂：{}", detail),
        }
    }

    /// 是否「能上网」。
    pub fn is_online(&self) -> bool {
        matches!(self, PortalVerdict::Online)
    }

    /// 是否「被门户拦」。
    pub fn is_blocked(&self) -> bool {
        matches!(self, PortalVerdict::Blocked(_))
    }
}

/// 跑一次探测（注入 client → 单测不碰网络 ✓）。
pub fn probe(client: &dyn HttpGet, url: &str, timeout: Duration) -> PortalVerdict {
    match client.get(url, timeout) {
        Ok(reply) => classify(&reply),
        Err(e) => PortalVerdict::Unreachable(crate::secret::scrub_url(&e)),
    }
}

/// 从一次 HTTP 响应判定门户状态（**纯函数** ✓）。
pub fn classify(reply: &HttpReply) -> PortalVerdict {
    // ① 重定向到门户页 —— 最硬的证据 ✓
    if let Some(target) = reply.location.as_deref() {
        let normalized = normalize(target);
        if is_portal_text(&normalized) {
            let hint = parse_hint(&normalized).unwrap_or_else(|| PortalHint {
                portal_host: origin_host(&normalized).unwrap_or_default(),
                page: path_of(&normalized).unwrap_or_default(),
                ..PortalHint::default()
            });
            return PortalVerdict::Blocked(hint);
        }
    }
    // ② 直接 200 吐门户页的 AC（不重定向的那种 ✗）
    let body = normalize(&reply.body);
    if is_portal_text(&body) {
        if let Some(hint) = parse_hint(&body) {
            return PortalVerdict::Blocked(hint);
        }
        return PortalVerdict::Unknown("响应像门户页，但拿不到 portal 参数".to_string());
    }
    // ③ 正常响应 → 在线
    if (200..400).contains(&reply.status) {
        return PortalVerdict::Online;
    }
    PortalVerdict::Unknown(format!("HTTP {}", reply.status))
}

/// 这段文本像不像门户（URL / 正文都可用 ✓）。
pub fn is_portal_text(text: &str) -> bool {
    let lowered = text.to_ascii_lowercase();
    PORTAL_MARKERS.iter().any(|marker| lowered.contains(marker))
}

/// 从 URL / 正文里抽出门户参数（抽不到 → None ✓）。
pub fn parse_hint(text: &str) -> Option<PortalHint> {
    let normalized = normalize(text);
    let url = find_portal_url(&normalized).unwrap_or(normalized.as_str());
    let pairs = parse_query(url);
    let get = |name: &str| {
        pairs
            .iter()
            .find(|(key, _)| key.eq_ignore_ascii_case(name))
            .map(|(_, value)| value.clone())
    };
    let user_ip = get("wlanuserip");
    let ac_ip = get("wlanacip");
    let ac_name = get("wlanacname");
    // 这三个都没有 → 不是门户参数，别乱认 ✗
    if user_ip.is_none() && ac_ip.is_none() && ac_name.is_none() {
        return None;
    }
    Some(PortalHint {
        portal_host: origin_host(url).unwrap_or_default(),
        page: path_of(url).unwrap_or_default(),
        user_ip,
        ac_ip,
        ac_name,
        mac: get("mac"),
        redirect: get("rul").or_else(|| get("redirect_url")),
    })
}

/// 正文里常把门户地址嵌在 HTML 属性中 ✗ → 先把它抠出来再解析 ✓。
fn find_portal_url(text: &str) -> Option<&str> {
    let start = text.find("http://").or_else(|| text.find("https://"))?;
    let rest = &text[start..];
    let end = rest
        .find(|c: char| matches!(c, '"' | '\'' | '<' | '>' | ' ' | '\n' | '\r' | ')' | ';'))
        .unwrap_or(rest.len());
    Some(&rest[..end])
}

/// AC 说的地址（`wlanuserip`）与本机探测到的出口地址是否一致。
/// 两边任一缺失 → None（**不下结论** ✓）。
pub fn ip_matches(hint: &PortalHint, local_ip: Option<&str>) -> Option<bool> {
    let expected = hint.user_ip.as_deref()?.trim();
    let local = local_ip?.trim();
    if expected.is_empty() || local.is_empty() {
        return None;
    }
    Some(expected == local)
}

/// 把 HTML 里的 `&amp;` 还原成 `&`（正文里的门户链接是转义过的 ✗）。
fn normalize(text: &str) -> String {
    text.replace("&amp;", "&")
}

/// `k=v&k=v` 解析：**先切分再解码** ✓
/// （反过来会被值里的 `%26`（= `&`）骗到 ✗）。
pub fn parse_query(text: &str) -> Vec<(String, String)> {
    let query = match text.split_once('?') {
        Some((_, rest)) => rest,
        None => text,
    };
    query
        .split('&')
        .filter(|part| !part.is_empty())
        .filter_map(|pair| pair.split_once('='))
        .map(|(key, value)| (percent_decode(key), percent_decode(value)))
        .collect()
}

/// 百分号解码：`%2e`→`.`、`%2d`→`-`，外加 `+`→空格（query 里的老规矩 ✓）。
pub fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0usize;
    while index < bytes.len() {
        match bytes[index] {
            b'%' if index + 2 < bytes.len() => match hex_pair(bytes[index + 1], bytes[index + 2]) {
                Some(byte) => {
                    out.push(byte);
                    index += 3;
                }
                None => {
                    out.push(b'%'); // 坏的转义原样保留 ✓
                    index += 1;
                }
            },
            b'+' => {
                out.push(b' ');
                index += 1;
            }
            other => {
                out.push(other);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_pair(high: u8, low: u8) -> Option<u8> {
    let high = (high as char).to_digit(16)?;
    let low = (low as char).to_digit(16)?;
    Some((high * 16 + low) as u8)
}

/// `http://172.16.80.3/a79.htm?...` → `172.16.80.3`（相对跳转 → None ✓）。
fn origin_host(url: &str) -> Option<String> {
    crate::net::split_http_url(url).map(|(host, _, _)| host)
}

/// `http://172.16.80.3/a79.htm?a=1` → `/a79.htm`（不含 query ✓）。
fn path_of(url: &str) -> Option<String> {
    let path = match crate::net::split_http_url(url) {
        Some((_, _, path)) => path,
        None => {
            let trimmed = url.trim();
            if !trimmed.starts_with('/') {
                return None;
            }
            trimmed.to_string()
        }
    };
    let path = path.split('?').next().unwrap_or("").to_string();
    if path.is_empty() {
        None
    } else {
        Some(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::net::HttpReply;

    /// 机房实拍原样抄下来（含 `%2e` / `%2d` 编码 ✓）——
    /// 这是本模块最重要的测试样本：**真实流量的形状** ✓。
    const REAL_PORTAL: &str = "http://172.16.80.3/a79.htm?mac=241C-0408-BDD3&rul=http://9.9.9.9/\
         &wlanacip=172%2e16%2e80%2e2&wlanacname=SR8806%2dX%2dS&wlanuserip=172.16.59.11";

    fn reply(status: u16, location: Option<&str>, body: &str) -> HttpReply {
        HttpReply {
            status,
            location: location.map(|s| s.to_string()),
            body: body.to_string(),
        }
    }

    #[test]
    fn real_campus_redirect_is_parsed_field_by_field() {
        let hint = parse_hint(REAL_PORTAL).expect("实拍样本必须能解析 ✓");
        assert_eq!(hint.portal_host, "172.16.80.3");
        assert_eq!(hint.page, "/a79.htm", "公共区域与宿舍都是这个页面 ✓");
        assert_eq!(hint.user_ip.as_deref(), Some("172.16.59.11"));
        assert_eq!(hint.ac_ip.as_deref(), Some("172.16.80.2"), "%2e 要解码成 . ✓");
        assert_eq!(hint.ac_name.as_deref(), Some("SR8806-X-S"), "%2d 要解码成 - ✓");
        assert_eq!(hint.mac.as_deref(), Some("241C-0408-BDD3"), "华为格式的 MAC ✓");
        assert_eq!(hint.redirect.as_deref(), Some("http://9.9.9.9/"), "rul = 原本想去的地址 ✓");
    }

    #[test]
    fn redirect_to_portal_means_blocked() {
        let verdict = classify(&reply(302, Some(REAL_PORTAL), ""));
        assert!(verdict.is_blocked(), "302 到 a79.htm 必须判定为被拦 ✓: {:?}", verdict);
        assert!(!verdict.is_online());
        assert!(verdict.summary_cn().contains("a79.htm"), "{}", verdict.summary_cn());
        match verdict {
            PortalVerdict::Blocked(hint) => {
                assert_eq!(hint.user_ip.as_deref(), Some("172.16.59.11"))
            }
            other => panic!("{:?}", other),
        }
    }

    #[test]
    fn relative_redirect_is_still_blocked() {
        // 有些 AC 只给相对跳转 ✗ 也要认出来（参数能拿到多少算多少 ✓）
        let verdict = classify(&reply(302, Some("/a79.htm?wlanuserip=1.2.3.4"), ""));
        assert!(verdict.is_blocked(), "{:?}", verdict);
        match verdict {
            PortalVerdict::Blocked(hint) => {
                assert_eq!(hint.page, "/a79.htm");
                assert_eq!(hint.user_ip.as_deref(), Some("1.2.3.4"));
                assert!(hint.portal_host.is_empty(), "相对跳转没有主机名 ✓");
            }
            other => panic!("{:?}", other),
        }
    }

    #[test]
    fn portal_page_in_body_is_detected_even_html_escaped() {
        let escaped = REAL_PORTAL.replace('&', "&amp;");
        let body = format!("<html><body onload=\"location.href='{}'\"></body></html>", escaped);
        let verdict = classify(&reply(200, None, &body));
        assert!(verdict.is_blocked(), "正文里的门户也要认出 ✓: {:?}", verdict);
        match verdict {
            PortalVerdict::Blocked(hint) => {
                assert_eq!(hint.portal_host, "172.16.80.3");
                assert_eq!(hint.ac_name.as_deref(), Some("SR8806-X-S"));
            }
            other => panic!("{:?}", other),
        }
    }

    #[test]
    fn normal_replies_are_online() {
        assert!(classify(&reply(200, None, "<html>hello</html>")).is_online());
        assert!(classify(&reply(204, None, "")).is_online());
        assert!(
            classify(&reply(302, Some("http://example.com/"), "")).is_online(),
            "跳到非门户地址 → 不是校园门户拦截 ✓"
        );
        assert!(matches!(classify(&reply(500, None, "boom")), PortalVerdict::Unknown(_)));
    }

    #[test]
    fn unreachable_never_claims_offline() {
        struct Dead;
        impl HttpGet for Dead {
            fn get(&self, _url: &str, _timeout: Duration) -> Result<HttpReply, String> {
                Err("连接 9.9.9.9:80 失败: timed out".to_string())
            }
        }
        let verdict = probe(&Dead, DEFAULT_PROBE_URL, Duration::from_millis(50));
        assert!(matches!(verdict, PortalVerdict::Unreachable(_)), "{:?}", verdict);
        let text = verdict.summary_cn();
        assert!(text.contains("不能"), "必须说清「连不上 ≠ 未认证」✗: {}", text);
        assert!(!verdict.is_online() && !verdict.is_blocked());
    }

    #[test]
    fn percent_decoding_and_query_splitting() {
        assert_eq!(percent_decode("172%2e16%2e80%2e2"), "172.16.80.2");
        assert_eq!(percent_decode("SR8806%2dX%2dS"), "SR8806-X-S");
        assert_eq!(percent_decode("a+b"), "a b", "query 里 + 是空格 ✓");
        assert_eq!(percent_decode("100%"), "100%", "坏转义原样保留、不 panic ✓");
        assert_eq!(percent_decode("%zz"), "%zz");
        // 值里含**编码过**的 & → 不能被当成分隔符 ✗
        let pairs = parse_query("rul=http%3A%2F%2F9.9.9.9%2F%3Fa%3D1%26b%3D2&x=1");
        assert_eq!(pairs.len(), 2, "{:?}", pairs);
        assert_eq!(pairs[0].1, "http://9.9.9.9/?a=1&b=2");
        assert_eq!(pairs[1], ("x".to_string(), "1".to_string()));
        assert!(parse_query("没有等号").is_empty());
    }

    #[test]
    fn hints_need_real_portal_params() {
        assert!(parse_hint("http://172.16.80.3/普通页面.html").is_none(), "没参数别乱认 ✗");
        assert!(parse_hint("mac=241C-0408-BDD3&rul=http://x/").is_none(), "光有 mac 不算门户 ✗");
        assert!(is_portal_text(REAL_PORTAL));
        assert!(is_portal_text("<script src=\"/eportal/js/a.js\"></script>"));
        assert!(!is_portal_text("<html><title>百度一下</title></html>"));
    }

    #[test]
    fn ip_mismatch_is_reported_but_missing_side_stays_silent() {
        let hint = parse_hint(REAL_PORTAL).unwrap();
        assert_eq!(ip_matches(&hint, Some("172.16.59.11")), Some(true));
        assert_eq!(ip_matches(&hint, Some("10.0.0.5")), Some(false), "多网卡/代理会踩 ✗");
        assert_eq!(ip_matches(&hint, None), None, "拿不到本机地址就别下结论 ✓");
        assert_eq!(ip_matches(&PortalHint::default(), Some("1.2.3.4")), None);
    }
}
