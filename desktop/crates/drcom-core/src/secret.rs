//! 凭据脱敏 —— 2.1.0.0（P7-2）那条修复的 Rust 版，规则一字不改：
//! 登录是 **GET**、`user_password` 就在 URL 的 query 里，**任何可能进日志 / 进界面的文本**
//! 都必须先过 [`scrub_url`]，否则一次网络异常就能把密码写进日志 ✗。

use std::fmt;

/// 把文本里的整条 URL 与密码参数擦掉：`http(s)://…` → `<url>`，`password=…` → `password=***`。
pub fn scrub_url(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(idx) = find_url_start(rest) {
        out.push_str(&rest[..idx]);
        let tail = &rest[idx..];
        let end = tail.find(char::is_whitespace).unwrap_or(tail.len());
        out.push_str("<url>");
        rest = &tail[end..];
    }
    out.push_str(rest);
    scrub_password_params(&out)
}

fn find_url_start(text: &str) -> Option<usize> {
    let lower = text.to_ascii_lowercase();
    match (lower.find("http://"), lower.find("https://")) {
        (Some(a), Some(b)) => Some(a.min(b)),
        (Some(a), None) => Some(a),
        (None, Some(b)) => Some(b),
        (None, None) => None,
    }
}

fn scrub_password_params(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(idx) = find_password_param(rest) {
        let (head, tail) = rest.split_at(idx);
        out.push_str(head);
        let after_eq = match tail.find('=') {
            Some(eq) => &tail[eq + 1..],
            None => {
                out.push_str(tail);
                return out;
            }
        };
        let end = after_eq
            .find(|c: char| c == '&' || c.is_whitespace())
            .unwrap_or(after_eq.len());
        // 保留参数名，只打码值 ✓
        out.push_str(&tail[..tail.len() - after_eq.len()]);
        out.push_str("***");
        rest = &after_eq[end..];
    }
    out.push_str(rest);
    out
}

fn find_password_param(text: &str) -> Option<usize> {
    let lower = text.to_ascii_lowercase();
    let a = lower.find("user_password=");
    let b = lower.find("password=").filter(|i| {
        // 别把 user_password= 的尾部再当成一个 password= ✗
        !lower[..*i].ends_with("user_")
    });
    match (a, b) {
        (Some(x), Some(y)) => Some(x.min(y)),
        (Some(x), None) => Some(x),
        (None, Some(y)) => Some(y),
        (None, None) => None,
    }
}

/// 可以进日志 / 可以回给界面的网络错误描述。
///
/// 规则（信息从多到少，与 2.1.0.0 的 `_safe_error_text` 一致）：
///   1. 有 `code`（HTTP 状态码）→ `HTTP 500`；
///   2. 否则 `异常种类` + `原因`；
///   3. 最后统一过 [`scrub_url`] —— 即使调用方把整条 URL 塞进 `detail` 也安全 ✓。
pub fn safe_error_text(kind: &str, detail: Option<&str>) -> String {
    const LIMIT: usize = 200;
    let text = match detail {
        Some(d) if !d.is_empty() => format!("{}: {}", kind, d),
        _ => kind.to_string(),
    };
    let scrubbed = scrub_url(&text);
    if scrubbed.chars().count() <= LIMIT {
        scrubbed
    } else {
        let mut s: String = scrubbed.chars().take(LIMIT).collect();
        s.push('…');
        s
    }
}

/// HTTP 状态码版本的便捷入口（`HTTPError` 走这条）。
pub fn safe_http_error(code: u16, detail: Option<&str>) -> String {
    safe_error_text(&format!("HTTP {}", code), detail)
}

/// 给「日志里打印配置对象」用的包装：只暴露非敏感字段。
pub struct MaskedAccount<'a>(pub &'a str);

impl fmt::Display for MaskedAccount<'_> {
    /// 账号脱敏：前 4 位 + `******`（与 2.x 诊断包同一口径 ✓）。
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = self.0;
        let head: String = s.chars().take(4).collect();
        write!(f, "{}******", head)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PWD: &str = "#Fake-Pwd-7777#";

    fn leaky() -> String {
        format!("connection failed for http://172.16.80.3:801/eportal/portal/login?user_password={}&callback=dr1", PWD)
    }

    #[test]
    fn url_and_password_never_survive_the_scrubber() {
        let out = scrub_url(&leaky());
        assert!(!out.contains(PWD), "密码泄漏: {}", out);
        assert!(!out.contains("http://"), "URL 泄漏: {}", out);
        assert!(out.contains("<url>"));
    }

    #[test]
    fn password_param_masked_but_name_kept() {
        let out = scrub_url("GET /x?user_password=abc&callback=dr1&password=xyz");
        assert!(!out.contains("abc"));
        assert!(!out.contains("xyz"));
        assert!(out.contains("user_password=***"));
        assert!(out.contains("password=***"));
        assert!(out.contains("callback=dr1"), "非敏感参数要保留: {}", out);
    }

    #[test]
    fn safe_error_text_keeps_useful_info() {
        assert_eq!(
            safe_error_text("OSError", Some("connection refused")),
            "OSError: connection refused"
        );
        // 最坏情况：异常文本里带完整 URL（含密码）→ 擦掉但保留可读原因 ✓
        let out = safe_error_text("OSError", Some(&leaky()));
        assert!(!out.contains(PWD) && !out.contains("http://"));
        assert!(out.starts_with("OSError:"));
        assert_eq!(safe_http_error(500, None), "HTTP 500");
        // 过长要截断
        let long = safe_error_text("OSError", Some(&"x".repeat(500)));
        assert!(long.chars().count() <= 201);
    }

    #[test]
    fn scrub_is_idempotent_on_clean_text() {
        let clean = "校园网不可达（172.16.80.3）";
        assert_eq!(scrub_url(clean), clean);
    }

    #[test]
    fn account_masked_in_logs() {
        assert_eq!(MaskedAccount("2023001234").to_string(), "2023******");
        assert_eq!(MaskedAccount("12").to_string(), "12******");
    }
}
