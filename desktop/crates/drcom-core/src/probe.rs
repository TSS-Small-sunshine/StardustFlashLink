//! 平台探测：**当前 Wi-Fi 名**与**本机 IP 列表**（守卫判定的输入）。
//!
//! 为什么不用第三方 crate：①体积优先；②命令输出解析写成纯函数就能用**真实样本**做单测 ✓。
//!
//! 命令选择（都尽量挑「不管什么发行版/语言都能出同样的键名」的）：
//!   - Windows：`netsh wlan show interfaces`（键名是 `SSID`，与系统语言无关 ✓）
//!              `ipconfig`（行里含 `IPv4`/`IPv6` 标记，中文系统也认 ✓）
//!   - macOS：`networksetup -getairportnetwork en0`（`airport -I` 已被 Apple 移除 ✗）
//!            `ifconfig`（`inet `/`inet6 ` 前缀 ✓）
//!   - Linux：`iwgetid -r` → 退到 `nmcli -t -f active,ssid dev wifi`（`yes:名字` ✓）
//!            `ip -o addr`（`inet `/`inet6 ` 前缀 ✓）
//!
//! 读不到一律返回 `None` / 空列表 —— **守卫那边是 fail-open**，读不到不会误拦 ✓。

use std::process::Command;

/// 一次探测的快照。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Probe {
    pub ssid: Option<String>,
    pub ips: Vec<String>,
}

/// 探测一次（命令失败/超时/没输出都会安静降级 ✓）。
pub fn snapshot() -> Probe {
    let mut ips = local_ips();
    // 兜底：UDP connect 那条路（几乎总能拿到主用网卡的地址 ✓）
    if let Some(primary) = primary_local_ip() {
        if !ips.contains(&primary) {
            ips.push(primary);
        }
    }
    Probe { ssid: current_ssid(), ips }
}

/// 主用出口的源 IP（`UDP connect` 不发包，只让内核选路由 ✓）。
pub fn primary_local_ip() -> Option<String> {
    crate::net::local_ip_towards("1.1.1.1", 80).filter(|ip| ip != "0.0.0.0")
}

/// 当前 Wi-Fi 名。
pub fn current_ssid() -> Option<String> {
    let attempts: &[(&str, &[&str], fn(&str) -> Option<String>)] = if cfg!(target_os = "windows") {
        &[("netsh", &["wlan", "show", "interfaces"], parse_ssid_netsh as fn(&str) -> Option<String>)]
    } else if cfg!(target_os = "macos") {
        &[("networksetup", &["-getairportnetwork", "en0"], parse_ssid_networksetup as fn(&str) -> Option<String>)]
    } else {
        &[
            ("iwgetid", &["-r"], parse_ssid_iwgetid as fn(&str) -> Option<String>),
            ("nmcli", &["-t", "-f", "active,ssid", "dev", "wifi"], parse_ssid_nmcli as fn(&str) -> Option<String>),
        ]
    };
    for (program, args, parser) in attempts {
        if let Some(text) = run(program, args) {
            if let Some(ssid) = parser(&text) {
                return Some(ssid);
            }
        }
    }
    None
}

/// 本机 IP 列表（IPv4 + IPv6，去重、保序 ✓）。
pub fn local_ips() -> Vec<String> {
    let (program, args): (&str, &[&str]) = if cfg!(target_os = "windows") {
        ("ipconfig", &[])
    } else if cfg!(target_os = "macos") {
        ("ifconfig", &[])
    } else {
        ("ip", &["-o", "addr"])
    };
    let text = match run(program, args) {
        Some(text) => text,
        None => return Vec::new(),
    };
    let mut out: Vec<String> = Vec::new();
    for line in text.lines() {
        for ip in parse_ips_from_line(line) {
            if !out.contains(&ip) {
                out.push(ip);
            }
        }
    }
    out
}

// ---------------- Wi-Fi 名清单（设置界面「从列表里选」用 ✓） ----------------

/// 本机**保存过**的 Wi-Fi 名 ✓（跨多条网络用过的那几个 ✓）。
///
/// 用途：设置界面里点一下就能填进「允许的 Wi-Fi」或方案的匹配名 ✓ —— 不用手打 ✗
/// （手打最容易错一个字母，然后守卫就一直不生效 ✗）。
pub fn known_ssids() -> Vec<String> {
    let attempts: &[(&str, &[&str], fn(&str) -> Vec<String>)] = if cfg!(target_os = "windows") {
        &[(
            "netsh",
            &["wlan", "show", "profiles"],
            parse_netsh_profiles as fn(&str) -> Vec<String>,
        )]
    } else if cfg!(target_os = "macos") {
        &[(
            "networksetup",
            &["-listpreferredwirelessnetworks", "en0"],
            parse_networksetup_preferred as fn(&str) -> Vec<String>,
        )]
    } else {
        &[(
            "nmcli",
            &["-t", "-f", "NAME,TYPE", "connection", "show"],
            parse_nmcli_connections as fn(&str) -> Vec<String>,
        )]
    };
    collect_ssids(attempts)
}

/// 附近**当前可见**的 Wi-Fi 名 ✓。
///
/// macOS 从 14 起拿掉了 `airport` ✗，命令行没有官方入口 → 那边返回空 ✓（设置界面照常能用「当前 Wi-Fi」✓）。
pub fn visible_ssids() -> Vec<String> {
    let attempts: &[(&str, &[&str], fn(&str) -> Vec<String>)] = if cfg!(target_os = "windows") {
        &[(
            "netsh",
            &["wlan", "show", "networks"],
            parse_netsh_networks as fn(&str) -> Vec<String>,
        )]
    } else if cfg!(target_os = "macos") {
        &[]
    } else {
        &[(
            "nmcli",
            &["-t", "-f", "SSID", "dev", "wifi", "list"],
            parse_plain_ssid_lines as fn(&str) -> Vec<String>,
        )]
    };
    collect_ssids(attempts)
}

fn collect_ssids(attempts: &[(&str, &[&str], fn(&str) -> Vec<String>)]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for (program, args, parser) in attempts {
        if let Some(text) = run(program, args) {
            for ssid in parser(&text) {
                if !ssid.is_empty() && !out.contains(&ssid) {
                    out.push(ssid);
                }
            }
        }
        if !out.is_empty() {
            break;
        }
    }
    out
}

/// `netsh wlan show profiles` → 配置名 ✓。
///
/// **语言无关**：中英文都是 `标签 : 名字` ✓ —— 只取冒号右半边就够了 ✓。
pub fn parse_netsh_profiles(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|line| line.split_once(':'))
        .map(|(_, value)| value.trim().to_string())
        .filter(|value| !value.is_empty() && !value.starts_with('-'))
        .collect()
}

/// `netsh wlan show networks` → 可见 SSID ✓。
///
/// 只认 `SSID n :` 开头的行 ✓ —— `BSSID n :` 那些（MAC 地址）会被排除掉 ✓。
pub fn parse_netsh_networks(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|line| {
            let trimmed = line.trim_start();
            if !trimmed.to_ascii_uppercase().starts_with("SSID ") {
                return None;
            }
            trimmed
                .split_once(':')
                .map(|(_, value)| value.trim().to_string())
        })
        .filter(|value| !value.is_empty())
        .collect()
}

/// `networksetup -listpreferredwirelessnetworks en0` → 首行是标题（以 `:` 结尾 ✓）→ 跳过 ✓。
pub fn parse_networksetup_preferred(text: &str) -> Vec<String> {
    text.lines()
        .map(|line| line.trim())
        .filter(|line| {
            !line.is_empty()
                && !line.ends_with(':')
                && !line.to_ascii_lowercase().starts_with("preferred")
        })
        .map(|line| line.to_string())
        .collect()
}

/// `nmcli -t -f NAME,TYPE connection show` → 只留无线连接 ✓
/// （名字里可能含 `\:`，所以**从右**切一个冒号 ✓）。
pub fn parse_nmcli_connections(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|line| line.rsplit_once(':'))
        .filter(|(_, kind)| kind.contains("wireless"))
        .map(|(name, _)| name.replace("\\:", ":").trim().to_string())
        .filter(|name| !name.is_empty())
        .collect()
}

/// `nmcli -t -f SSID dev wifi list` → 一行一个名字 ✓（空行与分隔符要去掉 ✓）。
pub fn parse_plain_ssid_lines(text: &str) -> Vec<String> {
    text.lines()
        .map(|line| line.trim().to_string())
        .filter(|line| !line.is_empty() && line != "--")
        .collect()
}

fn run(program: &str, args: &[&str]) -> Option<String> {
    let output = Command::new(program).args(args).output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).to_string();
    if text.trim().is_empty() {
        None
    } else {
        Some(text)
    }
}

// ---------------- 解析（纯函数，单测覆盖真实样本 ✓） ----------------

/// `netsh wlan show interfaces` → `SSID` 行（**不是** `BSSID` 行 ✗）。
pub fn parse_ssid_netsh(text: &str) -> Option<String> {
    for line in text.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("BSSID") {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix("SSID") {
            if let Some((_, value)) = rest.split_once(':') {
                let value = value.trim();
                if !value.is_empty() {
                    return Some(value.to_string());
                }
            }
        }
    }
    None
}

/// `networksetup -getairportnetwork en0` → `Current Wi-Fi Network: X`。
pub fn parse_ssid_networksetup(text: &str) -> Option<String> {
    let marker = "Current Wi-Fi Network:";
    text.lines().find_map(|line| {
        let line = line.trim();
        line.strip_prefix(marker)
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
    })
}

/// `iwgetid -r` → 就一行名字。
pub fn parse_ssid_iwgetid(text: &str) -> Option<String> {
    let first = text.lines().map(|l| l.trim()).find(|l| !l.is_empty())?;
    Some(first.to_string())
}

/// `nmcli -t -f active,ssid dev wifi` → `yes:校园网`。
pub fn parse_ssid_nmcli(text: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let line = line.trim();
        let (active, ssid) = line.split_once(':')?;
        if active.trim() == "yes" && !ssid.trim().is_empty() {
            Some(ssid.trim().to_string())
        } else {
            None
        }
    })
}

/// 从一行里抠出 IP —— **三平台格式都认，且与运行平台无关** ✓（这样单测才能用别家的样本 ✓）：
///   - Windows：`IPv4 地址 . . . . . . . . . . . . : 172.16.1.5`（行里带 `IPv4`/`IPv6` 关键字 ✓）
///   - Linux：  `2: wlan0  inet 172.16.1.5/24 brd ... scope global`
///   - macOS：  `inet 172.16.1.5 netmask 0xffff0000 broadcast ...`
///
/// 带作用域的链路本地地址（`fe80::1%en0`）会被丢掉 —— 它不是合法地址字面量 ✓（与 2.x 相同）。
pub fn parse_ips_from_line(line: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let tokens: Vec<&str> = line.split_whitespace().collect();

    // ① Windows 风格：`标签 . . . : 值` —— 取**最后一个「冒号+空格」**之后的第一段
    //    （不能直接按 `:` 切 ✗：IPv6 地址自己带冒号，会把 2001:db8::5 切成 "5" ✗）
    if line.contains("IPv4") || line.contains("IPv6") {
        let tail = match line.rfind(": ") {
            Some(idx) => &line[idx + 1..],
            None => line,
        };
        if let Some(token) = tail.split_whitespace().next() {
            push_ip(&mut out, token);
        }
    }

    // ② Linux / macOS 风格：`inet` / `inet6` / `addr` 后面的那个字段（可能带 /前缀 ✓）
    for idx in 1..tokens.len() {
        if !matches!(tokens[idx - 1], "inet" | "inet6" | "addr") {
            continue;
        }
        let candidate = tokens[idx].split('/').next().unwrap_or("").trim();
        push_ip(&mut out, candidate);
    }
    out
}

fn push_ip(out: &mut Vec<String>, candidate: &str) {
    if candidate.parse::<std::net::IpAddr>().is_ok() && !out.iter().any(|ip| ip == candidate) {
        out.push(candidate.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // —— 真实样本（Windows）——
    const NETSH_CN: &str = "\r
接口名称                   : WLAN\r
    状态                              : 已连接\r
    SSID                              : Campus-WiFi\r
    BSSID                             : e2:5b:36:7b:8d:ac\r
    网络类型                          : 基础结构\r
    无线电类型                        : 802.11ac\r";

    const IPCONFIG_CN: &str = "\r
无线局域网适配器 WLAN:\r
   连接特定的 DNS 后缀 . . . . . . . : campus.edu\r
   IPv6 地址 . . . . . . . . . . . . : 2001:db8::5\r
   临时 IPv6 地址. . . . . . . . . . : 2001:db8::9\r
   IPv4 地址 . . . . . . . . . . . . : 172.16.80.77\r
   子网掩码  . . . . . . . . . . . . : 255.255.240.0\r
   默认网关. . . . . . . . . . . . . : 172.16.80.1\r";

    // —— 真实样本（macOS / Linux）——
    const NETWORKSETUP: &str = "Current Wi-Fi Network: Campus-WiFi\n";
    const NMCLI: &str = "no:CMCC\nno:CMCC-5G\nyes:Campus-WiFi\n";
    const IF_CONFIG_MAC: &str = "\
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 172.16.80.77 netmask 0xfffff000 broadcast 172.16.95.255
\tinet6 fe80::1c2f:8aff:fe1b:2c3d%en0 prefixlen 64 secured scopeid 0x6
";
    const IP_O_ADDR_LINUX: &str = "\
1: lo    inet 127.0.0.1/8 scope host lo
2: wlan0    inet 172.16.80.77/20 brd 172.16.95.255 scope global dynamic wlan0
2: wlan0    inet6 2001:db8::5/64 scope global dynamic
";

    #[test]
    fn netsh_ssid_is_parsed_but_not_bssid() {
        assert_eq!(parse_ssid_netsh(NETSH_CN).as_deref(), Some("Campus-WiFi"));
        // 只有 BSSID 没有 SSID 时不该把 MAC 当名字 ✗
        assert_eq!(parse_ssid_netsh("    BSSID : e2:5b:36:7b:8d:ac\n"), None);
        assert_eq!(parse_ssid_netsh("已断开连接\n"), None);
    }

    #[test]
    fn macos_and_linux_ssid_parsers() {
        assert_eq!(parse_ssid_networksetup(NETWORKSETUP).as_deref(), Some("Campus-WiFi"));
        assert_eq!(
            parse_ssid_networksetup("You are not associated with an AirPort network.\n"),
            None
        );
        assert_eq!(parse_ssid_nmcli(NMCLI).as_deref(), Some("Campus-WiFi"));
        assert_eq!(parse_ssid_nmcli("no:A\nno:B\n"), None, "一个都没标 yes → None");
        assert_eq!(parse_ssid_iwgetid("Campus-WiFi\n").as_deref(), Some("Campus-WiFi"));
        assert_eq!(parse_ssid_iwgetid("\n\n"), None);
    }

    #[test]
    fn windows_ipconfig_lines_are_parsed() {
        let mut ips: Vec<String> = Vec::new();
        for line in IPCONFIG_CN.lines() {
            for ip in parse_ips_from_line(line) {
                if !ips.contains(&ip) {
                    ips.push(ip);
                }
            }
        }
        assert!(ips.contains(&"172.16.80.77".to_string()), "{:?}", ips);
        assert!(ips.contains(&"2001:db8::5".to_string()), "{:?}", ips);
        assert!(ips.contains(&"2001:db8::9".to_string()), "临时 IPv6 也算 ✓: {:?}", ips);
        assert!(!ips.iter().any(|ip| ip == "255.255.240.0"), "子网掩码不是本机地址 ✗: {:?}", ips);
        assert!(!ips.iter().any(|ip| ip == "172.16.80.1"), "默认网关不是本机地址 ✗: {:?}", ips);
    }

    #[test]
    fn macos_lines_are_parsed() {
        let mac: Vec<String> = IF_CONFIG_MAC.lines().flat_map(parse_ips_from_line).collect();
        assert_eq!(
            mac,
            vec!["172.16.80.77".to_string()],
            "带 %作用域 的链路本地地址被丢掉（与 2.x 一致 ✓）"
        );
    }

    #[test]
    fn linux_lines_are_parsed_and_prefix_stripped() {
        let linux: Vec<String> = IP_O_ADDR_LINUX.lines().flat_map(parse_ips_from_line).collect();
        assert!(linux.contains(&"127.0.0.1".to_string()), "{:?}", linux);
        assert!(linux.contains(&"172.16.80.77".to_string()), "{:?}", linux);
        assert!(linux.contains(&"2001:db8::5".to_string()), "{:?}", linux);
        assert!(!linux.iter().any(|ip| ip.contains('/')), "前缀要去掉 ✗: {:?}", linux);
        assert!(!linux.iter().any(|ip| ip.contains("brd")), "别把 broadcast 当地址 ✗: {:?}", linux);
    }

    #[test]
    fn noise_lines_yield_nothing() {
        assert!(parse_ips_from_line("子网掩码  . . . . . . . . . . . . : 255.255.240.0").is_empty());
        assert!(parse_ips_from_line("   默认网关. . . . . . . . . . . . : 172.16.80.1").is_empty());
        assert!(parse_ips_from_line("连接特定的 DNS 后缀 . . . . . . . : campus.edu").is_empty());
        assert!(parse_ips_from_line("").is_empty());
        assert!(parse_ips_from_line("en0: flags=8863<UP,BROADCAST> mtu 1500").is_empty());
    }

    #[test]
    fn snapshot_never_panics_and_filters_garbage() {
        // 开发机上大概率没有校园网 Wi-Fi：读不到就是 None / 空列表 ✓（守卫那边 fail-open ✓）
        let probe = snapshot();
        if let Some(ssid) = probe.ssid.as_deref() {
            assert!(!ssid.is_empty());
        }
        assert!(
            probe.ips.iter().all(|ip| ip.parse::<std::net::IpAddr>().is_ok()),
            "只允许合法地址: {:?}",
            probe.ips
        );
        assert!(probe.ips.len() <= 32, "IP 列表不该爆炸: {:?}", probe.ips);
    }
}
