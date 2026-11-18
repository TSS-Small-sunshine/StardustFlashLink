//! 零依赖的**只读 ZIP 解析器** —— crate::zipwriter 的对偶 ✓。
//!
//! 用途：自我替换 / 回滚时，**只信自己打包出来的安装包**（archive 命令产出的 store ZIP）✓。
//! 因此这里刻意**只收一个很窄的子集**，宁可拒绝也不猜 ✓：
//!   - 只接受 **method 0（store，不压缩）** —— method 8（deflate）直接给中文报错，
//!     提示「需由本项目的打包命令生成」（我们没有任何解压实现，R1 也不许加 crate ✗）；
//!   - 不用 Zip64（单条目 / 总量都 < 4 GB ✓；出现 0xFFFF / 0xFFFFFFFF 哨兵即拒绝 ✗）；
//!   - 单卷（多卷 / 分卷一律拒绝 ✗）；
//!   - 条目一多就拒绝（最多 MAX_ENTRIES 个），单个太大 / 总量太大也拒绝
//!     （MAX_ENTRY_BYTES / MAX_TOTAL_BYTES）—— 安装包不该有这些形态 ✓；
//!   - 名字过白名单：拒绝绝对路径 / 盘符 / 反斜杠 / .. 段 / 空段 / 空名
//!     （**结构性拒绝**，不是 replace("..", "") 这种黑名单 ✓）；
//!   - 每条数据逐字节校验 **CRC-32**（复用 crate::zipwriter::crc32），
//!     本地头与中央目录的名字 / 偏移必须自洽 ✓。
//!
//! 为什么必须自己写：发布资产里的 ZIP 就是本项目 archive 命令用 crate::zipwriter 打的
//! （GitHub 上的 stardust-flash-link-<版本>-<目标>.zip），格式完全由我们控制；
//! 读回来也用自己的代码，才能「零新增 crate」并且**每一处校验都能被单测逐条打到** ✓。

use crate::zipwriter::{crc32, METHOD_STORE};

/// 最多接受多少个条目（我们的安装包通常 2~5 个：exe + README + 桌面项 / plist / control ✓）。
pub const MAX_ENTRIES: usize = 1000;
/// 单个条目（store 下就是原始大小）的上限：64 MiB ✓（GUI 目前 8 MB 量级）。
pub const MAX_ENTRY_BYTES: u64 = 64 * 1024 * 1024;
/// 全包总大小的上限：256 MiB ✓。
pub const MAX_TOTAL_BYTES: u64 = 256 * 1024 * 1024;
/// 条目名长度上限（防御性：正常名字远小于它 ✓）。
const MAX_NAME_BYTES: usize = 512;

const EOCD_SIG: [u8; 4] = [0x50, 0x4b, 0x05, 0x06];
const CENTRAL_SIG: [u8; 4] = [0x50, 0x4b, 0x01, 0x02];
const LOCAL_SIG: [u8; 4] = [0x50, 0x4b, 0x03, 0x04];
const EOCD_LEN: usize = 22;
const CENTRAL_HEADER_LEN: usize = 46;
const LOCAL_HEADER_LEN: usize = 30;
/// ZIP 注释最长 65535 字节 → 反向找 EOCD 的范围就是 22 + 65535 ✓。
const MAX_COMMENT: usize = 65535;
/// Zip64 的哨兵值：看到就拒绝（我们明确不用 Zip64 ✓）。
const ZIP64_U16: u16 = 0xFFFF;
const ZIP64_U32: u32 = 0xFFFF_FFFF;
/// 通用标志位 bit0 = 条目已加密 ✓。
const FLAG_ENCRYPTED: u16 = 0x0001;

/// 读出来的一个条目（**全内存**：安装包只有几 MB，读进来比流式简单、也更好断言 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ZipReadEntry {
    /// 条目名（ZIP 规范用 / 分隔；已过白名单 ✓）。
    pub name: String,
    /// 条目数据（store = 原样字节 ✓）。
    pub data: Vec<u8>,
}

/// 解析一个 ZIP 的全部条目（**只读、全内存、fail-closed** ✓）。
///
/// 任何结构问题都返回**可读中文错误**（界面 / CLI 直接显示），不 panic ✓。
pub fn read_entries(zip: &[u8]) -> Result<Vec<ZipReadEntry>, String> {
    if zip.len() < EOCD_LEN {
        return Err(format!(
            "不是有效的 ZIP（只有 {} 字节，连中央目录结束记录都不够）—— 请确认下载没被截断",
            zip.len()
        ));
    }
    let eocd = find_eocd(zip).ok_or_else(|| {
        "找不到 ZIP 的中央目录结束记录（EOCD）—— 文件可能被截断，或不是本项目的打包命令生成的"
            .to_string()
    })?;
    let head = need(zip, eocd, EOCD_LEN, "中央目录结束记录")?;
    let disk_no = le16(head, 4);
    let cd_start_disk = le16(head, 6);
    let entries_on_disk = le16(head, 8);
    let entries_total = le16(head, 10);
    let cd_size = le32(head, 12);
    let cd_offset = le32(head, 16);
    if entries_total == ZIP64_U16
        || entries_on_disk == ZIP64_U16
        || cd_size == ZIP64_U32
        || cd_offset == ZIP64_U32
    {
        return Err(
            "安装包用了 ZIP64 格式（不支持）—— 请用本项目的打包命令（archive）重新生成".to_string(),
        );
    }
    if disk_no != 0 || cd_start_disk != 0 || entries_on_disk != entries_total {
        return Err("安装包是多卷 / 分卷 ZIP（不支持）—— 请用本项目的打包命令重新生成".to_string());
    }
    let count = entries_total as usize;
    if count > MAX_ENTRIES {
        return Err(format!(
            "安装包里的条目太多（{} 个，最多 {} 个）—— 这不像是安装包",
            count, MAX_ENTRIES
        ));
    }
    let cd_start = cd_offset as usize;
    let cd_end = cd_start
        .checked_add(cd_size as usize)
        .ok_or_else(|| "安装包的中央目录位置越界（可能被改坏了）".to_string())?;
    if cd_end > eocd || cd_start > eocd {
        return Err(format!(
            "安装包的中央目录位置不对（目录 {}..{} 越过了 EOCD {}）—— 文件可能被改坏",
            cd_start, cd_end, eocd
        ));
    }

    // ⓪ 先按中央目录把「总大小」算一遍：总量上限必须在读任何数据**之前**就拦住 ✓
    //   （否则第一条数据越界的报错会先冒出来，总上限就永远测不到 / 拦不住 ✓）
    {
        let mut probe = cd_start;
        let mut total: u64 = 0;
        for _ in 0..count {
            let Ok(header) = need(zip, probe, CENTRAL_HEADER_LEN, "中央目录条目") else { break };
            if header[0..4] != CENTRAL_SIG {
                break; // 主循环会给更准确的报错 ✓
            }
            total = total.saturating_add(u64::from(le32(header, 24)));
            if total > MAX_TOTAL_BYTES {
                return Err(format!(
                    "安装包总大小超过上限（累计 {} 字节，上限 {} 字节 ≈ 256 MiB）—— 这不像是安装包",
                    total, MAX_TOTAL_BYTES
                ));
            }
            let name_len = le16(header, 28) as usize;
            let extra_len = le16(header, 30) as usize;
            let comment_len = le16(header, 32) as usize;
            let Some(next) = probe.checked_add(CENTRAL_HEADER_LEN + name_len + extra_len + comment_len)
            else {
                break;
            };
            probe = next;
        }
    }

    let mut entries: Vec<ZipReadEntry> = Vec::with_capacity(count.min(64));
    let mut cursor = cd_start;
    for index in 0..count {
        let header = need(zip, cursor, CENTRAL_HEADER_LEN, "中央目录条目")?;
        if header[0..4] != CENTRAL_SIG {
            return Err(format!(
                "第 {} 个条目的中央目录头签名不对（可能被改坏）—— 请用本项目的打包命令重新生成",
                index + 1
            ));
        }
        let flags = le16(header, 8);
        let method = le16(header, 10);
        let expected_crc = le32(header, 16);
        let compressed_size = le32(header, 20);
        let uncompressed_size = le32(header, 24);
        let name_len = le16(header, 28) as usize;
        let extra_len = le16(header, 30) as usize;
        let comment_len = le16(header, 32) as usize;
        let local_offset = le32(header, 42) as usize;

        let name_bytes = need(zip, cursor + CENTRAL_HEADER_LEN, name_len, "条目名")?;
        let name = std::str::from_utf8(name_bytes).map_err(|_| {
            format!("第 {} 个条目的名字不是合法的 UTF-8 —— 拒绝解析", index + 1)
        })?;
        validate_entry_name(name)?;
        if flags & FLAG_ENCRYPTED != 0 {
            return Err(format!("安装包里的条目「{}」是加密的（不支持）—— 拒绝解析", name));
        }
        if method != METHOD_STORE {
            return Err(format!(
                "安装包使用了不支持的压缩方式（需由本项目的打包命令生成）：条目「{}」是 method {}，只接受 method 0（store）",
                name, method
            ));
        }
        if compressed_size != uncompressed_size {
            return Err(format!(
                "条目「{}」的压缩前后大小不一致（store 必须相等，{} != {}）—— 文件被改坏了",
                name, compressed_size, uncompressed_size
            ));
        }
        let size = u64::from(uncompressed_size);
        if size > MAX_ENTRY_BYTES {
            return Err(format!(
                "单条目太大（「{}」{} 字节，上限 {} 字节 ≈ 64 MiB）—— 这不像是安装包",
                name, size, MAX_ENTRY_BYTES
            ));
        }

        // —— 本地文件头：位置、名字都要与中央目录自洽 ✓ ——
        if local_offset
            .checked_add(LOCAL_HEADER_LEN)
            .map(|end| end > cd_start)
            .unwrap_or(true)
        {
            return Err(format!(
                "条目「{}」的本地头位置越界（{}，中央目录从 {} 开始）—— 文件被改坏了",
                name, local_offset, cd_start
            ));
        }
        let local = need(zip, local_offset, LOCAL_HEADER_LEN, "本地文件头")?;
        if local[0..4] != LOCAL_SIG {
            return Err(format!(
                "条目「{}」的本地文件头签名不对 —— 请用本项目的打包命令重新生成",
                name
            ));
        }
        let local_name_len = le16(local, 26) as usize;
        let local_extra_len = le16(local, 28) as usize;
        let local_name = need(
            zip,
            local_offset + LOCAL_HEADER_LEN,
            local_name_len,
            "本地文件头里的条目名",
        )?;
        if local_name != name_bytes {
            return Err(format!(
                "条目「{}」本地头与中央目录的名字不一致 —— 文件被改坏了",
                name
            ));
        }
        let data_start = local_offset
            .checked_add(LOCAL_HEADER_LEN)
            .and_then(|value| value.checked_add(local_name_len))
            .and_then(|value| value.checked_add(local_extra_len))
            .ok_or_else(|| format!("条目「{}」的数据位置越界 —— 文件被改坏了", name))?;
        let data_end = data_start
            .checked_add(compressed_size as usize)
            .ok_or_else(|| format!("条目「{}」的数据长度越界 —— 文件被改坏了", name))?;
        if data_end > cd_start {
            return Err(format!(
                "条目「{}」的数据越界 / 与中央目录重叠 —— 文件被改坏了",
                name
            ));
        }
        let data = zip[data_start..data_end].to_vec();
        let actual_crc = crc32(&data);
        if actual_crc != expected_crc {
            return Err(format!(
                "条目「{}」的 CRC32 校验失败（期望 {:08x}，实际 {:08x}）—— 安装包损坏，请重新下载",
                name, expected_crc, actual_crc
            ));
        }
        entries.push(ZipReadEntry { name: name.to_string(), data });
        cursor = cursor
            .checked_add(CENTRAL_HEADER_LEN + name_len + extra_len + comment_len)
            .ok_or_else(|| "中央目录长度越界 —— 文件被改坏了".to_string())?;
    }
    if cursor != cd_end {
        return Err(format!(
            "中央目录长度对不上（读到 {}，EOCD 说 {}）—— 文件被改坏了",
            cursor, cd_end
        ));
    }
    Ok(entries)
}

/// 从文件尾部反查 EOCD：签名要出现，且**注释长度必须与文件尾对齐**（挡住正文里的假签名 ✓）。
fn find_eocd(zip: &[u8]) -> Option<usize> {
    if zip.len() < EOCD_LEN {
        return None;
    }
    let lowest = zip.len().saturating_sub(EOCD_LEN + MAX_COMMENT);
    for index in (lowest..=zip.len() - EOCD_LEN).rev() {
        if zip[index..index + 4] != EOCD_SIG {
            continue;
        }
        let comment_len = usize::from(u16::from_le_bytes([zip[index + 20], zip[index + 21]]));
        if index + EOCD_LEN + comment_len == zip.len() {
            return Some(index);
        }
    }
    None
}

/// 条目名白名单（**结构性拒绝**，不是黑名单替换 ✓）。
///
/// 拒绝：空名 / 绝对路径 / 盘符 / 反斜杠 / .. 段 / 空段（含「以 / 结尾的目录条目」✓）。
pub fn validate_entry_name(name: &str) -> Result<(), String> {
    if name.is_empty() {
        return Err("安装包里有空名字的条目 —— 拒绝解析".to_string());
    }
    if name.len() > MAX_NAME_BYTES {
        return Err(format!(
            "条目名太长（{} 字节，上限 {}）—— 拒绝解析",
            name.len(),
            MAX_NAME_BYTES
        ));
    }
    if name.contains('\\') {
        return Err(format!("条目名用了反斜杠（ZIP 规范要求 /）：{}", name));
    }
    if name.starts_with('/') {
        return Err(format!("条目名是绝对路径（不允许）：{}", name));
    }
    let bytes = name.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' && bytes[0].is_ascii_alphabetic() {
        return Err(format!("条目名带盘符（不允许）：{}", name));
    }
    for segment in name.split('/') {
        if segment == ".." {
            return Err(format!("安装包里有路径穿越（.. 段）：{}", name));
        }
        if segment == "." {
            return Err(format!("安装包里有 . 路径段（不允许）：{}", name));
        }
        if segment.is_empty() {
            return Err(format!("安装包里的路径不合法（空路径段 / 目录条目）：{}", name));
        }
    }
    Ok(())
}

/// 取一段并做边界检查 —— 越界统一报「结构损坏」（绝不 panic ✓）。
fn need<'a>(zip: &'a [u8], start: usize, len: usize, what: &str) -> Result<&'a [u8], String> {
    start
        .checked_add(len)
        .and_then(|end| zip.get(start..end))
        .ok_or_else(|| {
            format!(
                "安装包结构损坏：{} 越界（{}..{}）",
                what,
                start,
                start.saturating_add(len)
            )
        })
}

fn le16(bytes: &[u8], index: usize) -> u16 {
    u16::from_le_bytes([bytes[index], bytes[index + 1]])
}

fn le32(bytes: &[u8], index: usize) -> u32 {
    u32::from_le_bytes([bytes[index], bytes[index + 1], bytes[index + 2], bytes[index + 3]])
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::zipwriter::{build, ZipEntry};
    use std::time::{Duration, UNIX_EPOCH};

    fn at(secs: u64) -> std::time::SystemTime {
        UNIX_EPOCH + Duration::from_secs(secs)
    }

    fn sample_zip() -> Vec<u8> {
        build(
            &[
                ZipEntry { name: "README.txt", data: "星尘闪连 3.0.0.0-preview.3".as_bytes() },
                ZipEntry { name: "staging/stardust-flash-link-app.exe", data: b"MZ-fake-exe" },
            ],
            at(1_774_732_800),
        )
        .unwrap()
    }

    /// 测试用：手工造**单条目** ZIP（能故意写坏；zipwriter::build 拒绝的坏包由它来造 ✓）。
    fn raw_zip(name: &[u8], flags: u16, method: u16, crc: u32, size: u32, data: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
        out.extend_from_slice(&20u16.to_le_bytes());
        out.extend_from_slice(&flags.to_le_bytes());
        out.extend_from_slice(&method.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&size.to_le_bytes());
        out.extend_from_slice(&size.to_le_bytes());
        out.extend_from_slice(&(name.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(name);
        out.extend_from_slice(data);
        let mut central = Vec::new();
        central.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
        central.extend_from_slice(&20u16.to_le_bytes());
        central.extend_from_slice(&20u16.to_le_bytes());
        central.extend_from_slice(&flags.to_le_bytes());
        central.extend_from_slice(&method.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&crc.to_le_bytes());
        central.extend_from_slice(&size.to_le_bytes());
        central.extend_from_slice(&size.to_le_bytes());
        central.extend_from_slice(&(name.len() as u16).to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&0u32.to_le_bytes());
        central.extend_from_slice(&0u32.to_le_bytes());
        central.extend_from_slice(name);
        let cd_offset = out.len() as u32;
        let cd_size = central.len() as u32;
        out.extend_from_slice(&central);
        out.extend_from_slice(&EOCD_SIG);
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&1u16.to_le_bytes());
        out.extend_from_slice(&1u16.to_le_bytes());
        out.extend_from_slice(&cd_size.to_le_bytes());
        out.extend_from_slice(&cd_offset.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out
    }

    /// 找到 EOCD 的偏移（测试里改字段用 ✓）。
    fn eocd_offset(zip: &[u8]) -> usize {
        find_eocd(zip).expect("测试包必须有 EOCD")
    }

    /// 把所有条目的「大小」字段改成同一个值（中央目录 + 本地头一起改，用来打大小上限 ✓）。
    fn patch_sizes(zip: &mut [u8], size: u32) {
        let eocd = eocd_offset(zip);
        let count = le16(&zip[eocd..], 10) as usize;
        let mut cursor = le32(&zip[eocd..], 16) as usize;
        for _ in 0..count {
            let local = le32(&zip[cursor..], 42) as usize;
            zip[local + 18..local + 22].copy_from_slice(&size.to_le_bytes());
            zip[local + 22..local + 26].copy_from_slice(&size.to_le_bytes());
            zip[cursor + 20..cursor + 24].copy_from_slice(&size.to_le_bytes());
            zip[cursor + 24..cursor + 28].copy_from_slice(&size.to_le_bytes());
            let name_len = le16(&zip[cursor..], 28) as usize;
            let extra_len = le16(&zip[cursor..], 30) as usize;
            let comment_len = le16(&zip[cursor..], 32) as usize;
            cursor += CENTRAL_HEADER_LEN + name_len + extra_len + comment_len;
        }
    }

    #[test]
    fn writer_and_reader_round_trip_byte_for_byte() {
        let text = "第一行\n第二行\n";
        let entries = [
            ZipEntry { name: "README.txt", data: text.as_bytes() },
            ZipEntry { name: "staging/stardust-flash-link-app.exe", data: b"MZ-demo" },
            ZipEntry { name: "empty.txt", data: b"" },
        ];
        let zip = build(&entries, at(0)).unwrap();
        let read = read_entries(&zip).expect("自己打的包当然要能读回来 ✓");
        assert_eq!(read.len(), entries.len());
        for (got, want) in read.iter().zip(entries.iter()) {
            assert_eq!(got.name, want.name, "名字要逐字一致 ✓");
            assert_eq!(got.data, want.data, "数据要逐字节一致 ✓");
        }
        assert!(read[2].data.is_empty(), "空文件也要能读 ✓");
    }

    #[test]
    fn deflate_entries_are_rejected_with_the_exact_hint() {
        let data = b"compressed? nope";
        let zip = raw_zip(b"README.txt", 0, 8, crc32(data), data.len() as u32, data);
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("不支持的压缩方式"), "{}", err);
        assert!(err.contains("本项目的打包命令"), "{}", err);
        assert!(err.contains("method 8"), "要点明是哪种方式: {}", err);
    }

    #[test]
    fn encrypted_entries_are_rejected() {
        let data = b"secret";
        let zip = raw_zip(b"a.txt", FLAG_ENCRYPTED, METHOD_STORE, crc32(data), data.len() as u32, data);
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("加密"), "{}", err);
    }

    #[test]
    fn path_traversal_and_absolute_names_are_all_rejected() {
        let cases: [&[u8]; 6] = [
            b"../evil.txt",
            b"/etc/passwd",
            b"C:/Windows/evil.exe",
            b"a\\b.txt",
            b"dir/../../evil.txt",
            b"dir//evil.txt",
        ];
        for name in cases {
            let data = b"x";
            let zip = raw_zip(name, 0, METHOD_STORE, crc32(data), 1, data);
            let err = read_entries(&zip)
                .err()
                .unwrap_or_else(|| panic!("必须拒绝路径穿越 / 绝对路径: {:?}", name));
            assert!(
                err.contains("路径")
                    || err.contains("穿越")
                    || err.contains("盘符")
                    || err.contains("反斜杠")
                    || err.contains("空路径段"),
                "错误要能看懂（{:?}）: {}",
                name,
                err
            );
        }
        let data = b"x";
        let zip = raw_zip(b"", 0, METHOD_STORE, crc32(data), 1, data);
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("空名字"), "{}", err);
    }

    #[test]
    fn truncated_zip_is_rejected_readably() {
        let zip = sample_zip();
        let cut = &zip[..zip.len() - 10];
        let err = read_entries(cut).unwrap_err();
        assert!(err.contains("EOCD") || err.contains("截断"), "{}", err);
        let err = read_entries(b"PK").unwrap_err();
        assert!(err.contains("不是有效的 ZIP"), "{}", err);
        let err = read_entries(&[]).unwrap_err();
        assert!(err.contains("不是有效的 ZIP"), "{}", err);
        let err = read_entries(&zip[..zip.len() - 60]).unwrap_err();
        assert!(!err.is_empty(), "{}", err);
    }

    #[test]
    fn crc_mismatch_is_rejected() {
        let mut zip = sample_zip();
        // 翻转第一个条目的一个数据字节（本地头 30 字节 + 名字 10 字节 → README.txt 的数据从 40 开始）
        let data_start = 30 + "README.txt".len();
        zip[data_start] ^= 0xFF;
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("CRC32"), "{}", err);
        assert!(err.contains("重新下载") || err.contains("损坏"), "{}", err);
    }

    #[test]
    fn too_many_entries_are_rejected() {
        let names: Vec<String> = (0..=MAX_ENTRIES).map(|index| format!("f{}.txt", index)).collect();
        let entries: Vec<ZipEntry<'_>> =
            names.iter().map(|name| ZipEntry { name, data: b"" }).collect();
        let zip = build(&entries, at(0)).unwrap();
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("条目太多"), "{}", err);
        assert!(err.contains(&MAX_ENTRIES.to_string()), "{}", err);
    }

    #[test]
    fn oversized_entry_and_total_are_rejected() {
        // ① 单条目 > 64 MiB
        let data = b"x";
        let big = (MAX_ENTRY_BYTES + 1) as u32;
        let zip = raw_zip(b"big.bin", 0, METHOD_STORE, crc32(data), big, data);
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("单条目太大") || err.contains("64 MiB"), "{}", err);
        // ② 总量 > 256 MiB（5 × 60 MiB，每个都在单条目上限之内）
        let names = ["a.bin", "b.bin", "c.bin", "d.bin", "e.bin"];
        let entries: Vec<ZipEntry<'_>> =
            names.iter().map(|name| ZipEntry { name, data: b"x" }).collect();
        let mut zip = build(&entries, at(0)).unwrap();
        patch_sizes(&mut zip, 60 * 1024 * 1024);
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("总大小超过上限") || err.contains("256 MiB"), "{}", err);
    }

    #[test]
    fn zip64_sentinels_are_rejected() {
        let mut zip = sample_zip();
        let eocd = eocd_offset(&zip);
        zip[eocd + 8..eocd + 10].copy_from_slice(&ZIP64_U16.to_le_bytes());
        zip[eocd + 10..eocd + 12].copy_from_slice(&ZIP64_U16.to_le_bytes());
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("ZIP64"), "{}", err);
        let mut zip = sample_zip();
        let eocd = eocd_offset(&zip);
        zip[eocd + 16..eocd + 20].copy_from_slice(&ZIP64_U32.to_le_bytes());
        let err = read_entries(&zip).unwrap_err();
        assert!(err.contains("ZIP64"), "{}", err);
    }

    #[test]
    fn name_policy_is_structural() {
        assert!(validate_entry_name("staging/stardust-flash-link-app.exe").is_ok());
        assert!(validate_entry_name("README.txt").is_ok());
        assert!(validate_entry_name("a..b/c.txt").is_ok(), "含 .. 的文件名不是穿越段 ✓");
        for bad in ["", "/x", "../x", "a/../x", "C:/x", "a\\b", "a//b", "dir/", "."] {
            assert!(validate_entry_name(bad).is_err(), "必须拒绝: {:?}", bad);
        }
    }
}
