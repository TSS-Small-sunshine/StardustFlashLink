//! CIDR 判定（纯函数，无依赖）—— 与 2.x 里 `ipaddress.ip_network(..., strict=False)` 行为对齐：
//! **主机位非零的写法照样能匹配**（例如 `172.16.5.5/12` 按 `172.16.0.0/12` 处理 ✓）。

use std::net::IpAddr;

/// 解析 `a.b.c.d/n`；非法返回 `None`（前缀超范围、地址写错都算非法 ✓）。
pub fn parse_cidr(text: &str) -> Option<(IpAddr, u8)> {
    let (addr, prefix) = text.split_once('/')?;
    let ip: IpAddr = addr.trim().parse().ok()?;
    let bits: u8 = prefix.trim().parse().ok()?;
    let max = if ip.is_ipv4() { 32 } else { 128 };
    if bits > max {
        return None;
    }
    Some((ip, bits))
}

/// `ip` 是否落在 `cidr` 里（都按网段掩码比较，忽略主机位 ✓；跨协议族一律 false ✓）。
pub fn ip_in_cidr(ip: &str, cidr: &str) -> bool {
    let ip: IpAddr = match ip.trim().parse() {
        Ok(v) => v,
        Err(_) => return false,
    };
    let (net, bits) = match parse_cidr(cidr) {
        Some(v) => v,
        None => return false,
    };
    match (ip, net) {
        (IpAddr::V4(a), IpAddr::V4(n)) => {
            let mask: u32 = if bits == 0 { 0 } else { u32::MAX << (32 - u32::from(bits)) };
            (u32::from(a) & mask) == (u32::from(n) & mask)
        }
        (IpAddr::V6(a), IpAddr::V6(n)) => {
            let mask: u128 = if bits == 0 { 0 } else { u128::MAX << (128 - u128::from(bits)) };
            (u128::from(a) & mask) == (u128::from(n) & mask)
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ipv4_matching_like_python_strict_false() {
        assert!(ip_in_cidr("172.16.5.5", "172.16.0.0/12"), "校区常见网段");
        assert!(ip_in_cidr("172.31.255.254", "172.16.0.0/12"));
        assert!(!ip_in_cidr("172.32.0.1", "172.16.0.0/12"), "刚好出界");
        assert!(ip_in_cidr("192.168.1.10", "192.168.1.0/24"));
        assert!(!ip_in_cidr("192.168.2.10", "192.168.1.0/24"));
        // 主机位非零的写法：strict=False 语义 ✓
        assert!(ip_in_cidr("172.16.9.9", "172.16.5.5/12"));
        // 极端前缀
        assert!(ip_in_cidr("8.8.8.8", "0.0.0.0/0"));
        assert!(ip_in_cidr("203.0.113.7", "203.0.113.7/32"));
        assert!(!ip_in_cidr("203.0.113.8", "203.0.113.7/32"));
    }

    #[test]
    fn ipv6_and_mixed_families() {
        assert!(ip_in_cidr("fd00::5", "fd00::/8"));
        assert!(!ip_in_cidr("fe80::1", "fd00::/8"));
        assert!(ip_in_cidr("2001:db8::1", "2001:db8::/32"));
        // 跨协议族必须 false（Python 会抛 ValueError 被吞掉 → 也是不放行 ✓）
        assert!(!ip_in_cidr("10.0.0.1", "fd00::/8"));
        assert!(!ip_in_cidr("fd00::1", "10.0.0.0/8"));
    }

    #[test]
    fn invalid_inputs_are_false_not_panic() {
        assert!(!ip_in_cidr("not-an-ip", "10.0.0.0/8"));
        assert!(!ip_in_cidr("10.0.0.1", "not-a-net/8"));
        assert!(!ip_in_cidr("10.0.0.1", "10.0.0.1"));
        assert!(!ip_in_cidr("10.0.0.1", "10.0.0.0/33"), "前缀越界");
        assert!(!ip_in_cidr("10.0.0.1", "10.0.0.0/-1"));
        assert!(parse_cidr("10.0.0.0/33").is_none());
        assert!(parse_cidr("10.0.0.0/8").is_some());
    }
}
