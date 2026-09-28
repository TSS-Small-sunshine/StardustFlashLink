//! 版本与发行通道（学 Minecraft：Snapshot → Preview → RC → Release）。
//!
//! 两条硬规则（与 2.x 的自动更新语义保持一致）：
//!   1. **版本比较按四段数字**（`3.0.0.0`，沿用 2.x 的 `_compare_versions` 语义），
//!      避免 `2.0.10.0` vs `2.0.9.0` 这类字符串比较翻车 ✗；
//!   2. **自动更新只认 Release 通道**：预览版（snapshot/preview/rc）一律 `is_prerelease() == true`，
//!      默认配置的用户永远收不到它们 ✓（想收的人在配置里把 `update_channel` 改成 preview）。

use std::cmp::Ordering;
use std::fmt;

/// 发行通道。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Channel {
    /// 每次 push 自动构建、可能坏 —— 只给开发/尝鲜
    Snapshot,
    /// 功能齐、已知问题写在发布说明里
    Preview,
    /// 发布候选，只修 bug
    Rc,
    /// 正式版
    Release,
}

impl Channel {
    pub fn as_str(self) -> &'static str {
        match self {
            Channel::Snapshot => "snapshot",
            Channel::Preview => "preview",
            Channel::Rc => "rc",
            Channel::Release => "release",
        }
    }

    /// 中文名（界面上显示「预览版」这种）。
    pub fn label_cn(self) -> &'static str {
        match self {
            Channel::Snapshot => "快照版",
            Channel::Preview => "预览版",
            Channel::Rc => "候选版",
            Channel::Release => "正式版",
        }
    }

    /// 是否预发布 —— **自动更新通道的判据**（与 GitHub release 的 prerelease 标记一一对应）。
    pub fn is_prerelease(self) -> bool {
        self != Channel::Release
    }

    /// 同一版本号内部的先后：snapshot < preview < rc < release。
    fn rank(self) -> u8 {
        match self {
            Channel::Snapshot => 0,
            Channel::Preview => 1,
            Channel::Rc => 2,
            Channel::Release => 3,
        }
    }

    /// 从字符串解析（认中英别名，git tag 与配置文件两条入口都能用）。
    pub fn parse(text: &str) -> Option<Channel> {
        match text.trim().to_ascii_lowercase().as_str() {
            "snapshot" | "snap" => Some(Channel::Snapshot),
            "preview" | "beta" | "alpha" => Some(Channel::Preview),
            "rc" => Some(Channel::Rc),
            "release" | "stable" => Some(Channel::Release),
            _ => None,
        }
    }
}

impl fmt::Display for Channel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// 版本：四段数字 + 通道 + 通道内序号。
///
/// 文本形态：`3.0.0.0`（正式版）/ `3.0.0.0-preview.3`（预览第 3 个）/ `2.1.0.0`（2.x 线的写法）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Version {
    pub major: u32,
    pub minor: u32,
    pub patch: u32,
    pub build: u32,
    pub channel: Channel,
    /// 通道内序号（正式版固定 0）
    pub seq: u32,
}

impl Version {
    pub fn new(major: u32, minor: u32, patch: u32, build: u32) -> Version {
        Version { major, minor, patch, build, channel: Channel::Release, seq: 0 }
    }

    /// 解析 `v3.0.0.0` / `3.0.0-preview.2` / `v2.1.0.0` / `3.0.0`（缺段补 0）。
    pub fn parse(text: &str) -> Option<Version> {
        let raw = text.trim();
        let raw = raw.strip_prefix('v').or_else(|| raw.strip_prefix('V')).unwrap_or(raw);
        if raw.is_empty() {
            return None;
        }
        let (digits, suffix) = match raw.split_once('-') {
            Some((d, s)) => (d, Some(s)),
            None => (raw, None),
        };
        let mut nums = [0u32; 4];
        let parts: Vec<&str> = digits.split('.').collect();
        if parts.is_empty() || parts.len() > 4 {
            return None;
        }
        for (idx, part) in parts.iter().enumerate() {
            nums[idx] = part.parse::<u32>().ok()?;
        }
        let (channel, seq) = match suffix {
            None => (Channel::Release, 0),
            Some(s) => {
                let (name, num) = match s.split_once('.') {
                    Some((n, v)) => (n, v.parse::<u32>().unwrap_or(0)),
                    None => (s, 0),
                };
                (Channel::parse(name)?, num)
            }
        };
        Some(Version { major: nums[0], minor: nums[1], patch: nums[2], build: nums[3], channel, seq })
    }

    /// 四段数字元组（`3.0.0.0` → `(3,0,0,0)`）。
    pub fn numbers(&self) -> (u32, u32, u32, u32) {
        (self.major, self.minor, self.patch, self.build)
    }

    /// 完整文本：**永远带四段数字**（与 2.x 的 tag 习惯一致 ✓）。
    pub fn to_string_full(&self) -> String {
        let base = format!("{}.{}.{}.{}", self.major, self.minor, self.patch, self.build);
        match self.channel {
            Channel::Release => base,
            c => format!("{}-{}.{}", base, c.as_str(), self.seq),
        }
    }

    /// 升级判定：`self` 是否比 `other` 新。
    pub fn is_newer_than(&self, other: &Version) -> bool {
        self.cmp_rank(other) == Ordering::Greater
    }

    fn cmp_rank(&self, other: &Version) -> Ordering {
        self.numbers()
            .cmp(&other.numbers())
            .then_with(|| self.channel.rank().cmp(&other.channel.rank()))
            .then_with(|| self.seq.cmp(&other.seq))
    }
}

impl fmt::Display for Version {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_string_full())
    }
}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp_rank(other))
    }
}

impl Ord for Version {
    fn cmp(&self, other: &Self) -> Ordering {
        self.cmp_rank(other)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_four_segment_with_v_prefix_and_missing_segments() {
        assert_eq!(Version::parse("v2.1.0.0").unwrap().numbers(), (2, 1, 0, 0));
        assert_eq!(Version::parse("3.0.0").unwrap().numbers(), (3, 0, 0, 0));
        assert_eq!(Version::parse(" 3.0.0.0 ").unwrap().numbers(), (3, 0, 0, 0));
        assert!(Version::parse("").is_none());
        assert!(Version::parse("abc").is_none());
        assert!(Version::parse("1.2.3.4.5").is_none());
    }

    #[test]
    fn channel_suffix_parsed_and_default_is_release() {
        let p = Version::parse("3.0.0.0-preview.2").unwrap();
        assert_eq!(p.channel, Channel::Preview);
        assert_eq!(p.seq, 2);
        assert!(p.channel.is_prerelease());
        let r = Version::parse("v3.0.0.0").unwrap();
        assert_eq!(r.channel, Channel::Release);
        assert!(!r.channel.is_prerelease());
        assert_eq!(r.to_string_full(), "3.0.0.0");
        assert_eq!(p.to_string_full(), "3.0.0.0-preview.2");
        assert_eq!(Channel::Preview.label_cn(), "预览版");
    }

    #[test]
    fn numbers_first_then_channel_then_seq() {
        let v2100 = Version::parse("2.1.0.0").unwrap();
        let v3010 = Version::parse("3.0.1.0").unwrap();
        assert!(v3010.is_newer_than(&v2100));
        // 2.0.10.0 vs 2.0.9.0：字符串比会翻车 ✗，数字比才对 ✓
        assert!(Version::parse("2.0.10.0")
            .unwrap()
            .is_newer_than(&Version::parse("2.0.9.0").unwrap()));
        let p3 = Version::parse("3.0.0.0-preview.3").unwrap();
        let p10 = Version::parse("3.0.0.0-preview.10").unwrap();
        let rc1 = Version::parse("3.0.0.0-rc.1").unwrap();
        let rel = Version::parse("3.0.0.0").unwrap();
        assert!(p10.is_newer_than(&p3));
        assert!(rc1.is_newer_than(&p10));
        assert!(rel.is_newer_than(&rc1));
        assert!(!p3.is_newer_than(&rel));
        let snap = Version::parse("3.0.0.0-snapshot.9").unwrap();
        assert!(p3.is_newer_than(&snap));
    }

    #[test]
    fn channel_parse_accepts_english_aliases() {
        assert_eq!(Channel::parse("RC"), Some(Channel::Rc));
        assert_eq!(Channel::parse("stable"), Some(Channel::Release));
        assert_eq!(Channel::parse(" beta "), Some(Channel::Preview));
        assert_eq!(Channel::parse("???"), None);
    }
}
