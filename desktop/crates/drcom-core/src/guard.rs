//! 网络位置守卫（v2.0.6.2 的功能，2.x 在 `protocol.guard_allows`）—— **判定原则是 fail-open**：
//! 一个自动登录工具宁愿多试一次，也不能因为读不到 Wi-Fi 名就静默不干活 ✓。
//!
//! 六条分支与 2.x 逐条对齐（见 `guard_allows`）：
//!   ① 守卫关着 → 放行；
//!   ② 守卫开着但两个白名单都没配 → 放行（配了守卫却没白名单 = 没意义）；
//!   ③ SSID 命中白名单 → 放行；
//!   ④ 任一本地 IP 落在任一白名单网段 → 放行；
//!   ⑤ SSID 与 IP 一个都没读到 → 放行（fail-open）；
//!   ⑥ 其余 → **拒绝**，理由固定为那句中文提示 ✓。

use crate::config::{split_csv, Config};

pub const DENY_REASON: &str = "不在校园网（SSID / 网段都不在白名单里）";

/// 守卫判定结果（`reason` 只在拒绝时非空，可直接写日志/界面 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GuardResult {
    pub allowed: bool,
    pub reason: String,
}

impl GuardResult {
    pub fn allow() -> GuardResult {
        GuardResult { allowed: true, reason: String::new() }
    }

    pub fn deny(reason: &str) -> GuardResult {
        GuardResult { allowed: false, reason: reason.to_string() }
    }
}

/// 判定是否允许在当前位置做检查。
///
/// `ssid`: 当前无线名（读不到传 `None`）；`ips`: 本机所有 IPv4/IPv6 地址（尽量多给 ✓）。
pub fn guard_allows(cfg: &Config, ssid: Option<&str>, ips: &[String]) -> GuardResult {
    if !cfg.network_guard_enabled {
        return GuardResult::allow();
    }
    let ssids = split_csv(&cfg.guard_allowed_ssids);
    let subnets = split_csv(&cfg.guard_allowed_subnets);
    if ssids.is_empty() && subnets.is_empty() {
        return GuardResult::allow();
    }
    let ssid = ssid.unwrap_or("").trim();
    if !ssid.is_empty() && ssids.iter().any(|allowed| allowed == ssid) {
        return GuardResult::allow();
    }
    for ip in ips {
        for net in &subnets {
            if crate::cidr::ip_in_cidr(ip, net) {
                return GuardResult::allow();
            }
        }
    }
    if ssid.is_empty() && ips.is_empty() {
        return GuardResult::allow();
    }
    GuardResult::deny(DENY_REASON)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg_on(ssids: &str, subnets: &str) -> Config {
        let mut cfg = Config::default();
        cfg.network_guard_enabled = true;
        cfg.guard_allowed_ssids = ssids.to_string();
        cfg.guard_allowed_subnets = subnets.to_string();
        cfg
    }

    #[test]
    fn branch1_guard_disabled_always_allows() {
        let cfg = Config::default(); // 默认关 ✓
        assert!(guard_allows(&cfg, Some("邻居家WiFi"), &["1.2.3.4".into()]).allowed);
    }

    #[test]
    fn branch2_enabled_but_no_whitelist_allows() {
        let cfg = cfg_on("", "");
        assert!(guard_allows(&cfg, Some("任意"), &["1.2.3.4".into()]).allowed);
        // 只写了逗号/空白也算没配 ✓
        let cfg = cfg_on(" , ， ", "  ");
        assert!(guard_allows(&cfg, Some("任意"), &[]).allowed);
    }

    #[test]
    fn branch3_ssid_hit_allows_even_from_foreign_subnet() {
        let cfg = cfg_on("Campus-WiFi, 宿舍楼", "");
        assert!(guard_allows(&cfg, Some("宿舍楼"), &["8.8.8.8".into()]).allowed);
        assert!(guard_allows(&cfg, Some("Campus-WiFi"), &[]).allowed);
    }

    #[test]
    fn branch4_subnet_hit_allows_even_with_foreign_ssid() {
        let cfg = cfg_on("Campus-WiFi", "172.16.0.0/12，10.0.0.0/8");
        assert!(guard_allows(&cfg, Some("星巴克"), &["10.1.2.3".into()]).allowed);
        // 多个 IP 里只要有一个命中 ✓
        let ips = vec!["192.168.1.5".to_string(), "172.20.0.9".to_string()];
        assert!(guard_allows(&cfg, None, &ips).allowed);
    }

    #[test]
    fn branch5_fail_open_when_nothing_readable() {
        let cfg = cfg_on("Campus-WiFi", "172.16.0.0/12");
        assert!(guard_allows(&cfg, None, &[]).allowed, "啥都读不到 → 放行 ✓");
        assert!(guard_allows(&cfg, Some(""), &[]).allowed);
    }

    #[test]
    fn branch6_denies_when_outside() {
        let cfg = cfg_on("Campus-WiFi", "172.16.0.0/12");
        let result = guard_allows(&cfg, Some("家里WiFi"), &["192.168.1.20".into()]);
        assert!(!result.allowed);
        assert_eq!(result.reason, DENY_REASON);
        // 读到了信息但不命中 → 拒绝（这是唯一会拒绝的分支 ✓）
        assert!(!guard_allows(&cfg, Some("家里WiFi"), &[]).allowed);
        assert!(!guard_allows(&cfg, None, &["192.168.1.20".into()]).allowed);
    }

    #[test]
    fn ssid_match_is_exact_like_python() {
        let cfg = cfg_on("Campus-WiFi", "");
        assert!(!guard_allows(&cfg, Some("campus-wifi"), &[]).allowed, "大小写敏感 ✓");
        assert!(!guard_allows(&cfg, Some("Campus-WiFi-5G"), &[]).allowed, "不做前缀匹配 ✓");
    }
}
