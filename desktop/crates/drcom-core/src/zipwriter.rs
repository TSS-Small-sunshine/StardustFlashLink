//! 零依赖的**最小 ZIP 写入器** —— 只做「store（不压缩）+ 正确目录结构」✓。
//!
//! 用途：脱敏诊断包（几十 KB）✓。刻意不引第三方 crate：
//!   - 体积优先（用户的硬要求）；
//!   - 结构小到能一眼看完、能被单测逐字段验证 ✓。
//!
//! 支持范围与边界：
//!   - 不使用 Zip64（单文件与总量都 < 4 GB ✓），超限直接报错而不是产出坏包 ✗；
//!   - 文件名统一按 **UTF-8 + 置 0x0800 标志**（Windows 资源管理器也认 ✓）；
//!   - 条目名一律用 `/` 分隔（ZIP 规范要求 ✓）。

use std::time::SystemTime;

/// 压缩方式：0 = store（不压缩 ✓）
pub const METHOD_STORE: u16 = 0;

/// 一个要写进包里的文件。
pub struct ZipEntry<'a> {
    pub name: &'a str,
    pub data: &'a [u8],
}

/// CRC-32（IEEE，多项式 0xEDB88320 反序表）—— ZIP 每个条目都要它 ✓。
pub fn crc32(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFF_FFFF;
    for byte in data {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask);
        }
    }
    !crc
}

/// 把若干条目打成 ZIP 字节流（全内存 ✓）。
pub fn build(entries: &[ZipEntry<'_>], modified: SystemTime) -> Result<Vec<u8>, String> {
    if entries.len() > u16::MAX as usize {
        return Err(format!("条目太多（{} 个，最多 {}）", entries.len(), u16::MAX));
    }
    let (dos_time, dos_date) = dos_datetime(modified);

    let mut out: Vec<u8> = Vec::with_capacity(1024);
    let mut central: Vec<u8> = Vec::new();

    for entry in entries {
        if entry.name.is_empty() || entry.name.len() > u16::MAX as usize {
            return Err(format!("条目名不合法：{:?}", entry.name));
        }
        if entry.data.len() > u32::MAX as usize {
            return Err(format!("条目太大（Zip64 不在支持范围内 ✗）：{}", entry.name));
        }
        let name = entry.name.as_bytes();
        let crc = crc32(entry.data);
        let size = entry.data.len() as u32;
        let local_offset = out.len();
        if local_offset > u32::MAX as usize {
            return Err("包太大（超过 4 GB，Zip64 不在支持范围内 ✗）".to_string());
        }

        // —— 本地文件头 ——
        push_u32(&mut out, 0x0403_4b50);
        push_u16(&mut out, 20); // 需要的解压版本
        push_u16(&mut out, 0x0800); // 文件名是 UTF-8 ✓
        push_u16(&mut out, METHOD_STORE);
        push_u16(&mut out, dos_time);
        push_u16(&mut out, dos_date);
        push_u32(&mut out, crc);
        push_u32(&mut out, size); // 压缩后大小（store = 一样 ✓）
        push_u32(&mut out, size);
        push_u16(&mut out, name.len() as u16);
        push_u16(&mut out, 0); // extra 长度
        out.extend_from_slice(name);
        out.extend_from_slice(entry.data);

        // —— 中央目录条目 ——
        push_u32(&mut central, 0x0201_4b50);
        push_u16(&mut central, 20); // version made by（2.0 ✓）
        push_u16(&mut central, 20); // version needed
        push_u16(&mut central, 0x0800);
        push_u16(&mut central, METHOD_STORE);
        push_u16(&mut central, dos_time);
        push_u16(&mut central, dos_date);
        push_u32(&mut central, crc);
        push_u32(&mut central, size);
        push_u32(&mut central, size);
        push_u16(&mut central, name.len() as u16);
        push_u16(&mut central, 0); // extra
        push_u16(&mut central, 0); // 注释
        push_u16(&mut central, 0); // 起始磁盘号
        push_u16(&mut central, 0); // 内部属性
        push_u32(&mut central, 0o100_644 << 16); // 外部属性：普通文件 644 ✓
        push_u32(&mut central, local_offset as u32); // **本地头的偏移**（不是数据的 ✓）
        central.extend_from_slice(name);
    }

    let cd_offset = out.len() as u32;
    let cd_size = central.len() as u32;
    out.extend_from_slice(&central);

    // —— 中央目录结束记录（EOCD）——
    push_u32(&mut out, 0x0605_4b50);
    push_u16(&mut out, 0);
    push_u16(&mut out, 0);
    push_u16(&mut out, entries.len() as u16);
    push_u16(&mut out, entries.len() as u16);
    push_u32(&mut out, cd_size);
    push_u32(&mut out, cd_offset);
    push_u16(&mut out, 0); // 注释长度
    Ok(out)
}

/// 列出 ZIP 里的条目名（只走中央目录 ✓）—— 给「诊断包里有什么」这类展示与测试用 ✓。
///
/// 结构不对（没有 EOCD / 中央目录签名不对）→ `None` ✓，不 panic ✓。
pub fn entry_names(zip: &[u8]) -> Option<Vec<String>> {
    let eocd = (0..zip.len().saturating_sub(4))
        .rev()
        .find(|idx| zip[*idx..*idx + 4] == [0x50, 0x4b, 0x05, 0x06])?;
    let count = u16::from_le_bytes([*zip.get(eocd + 10)?, *zip.get(eocd + 11)?]) as usize;
    let mut offset = u32::from_le_bytes([
        *zip.get(eocd + 16)?,
        *zip.get(eocd + 17)?,
        *zip.get(eocd + 18)?,
        *zip.get(eocd + 19)?,
    ]) as usize;
    let mut names = Vec::with_capacity(count);
    for _ in 0..count {
        if zip.get(offset..offset + 4)? != [0x50, 0x4b, 0x01, 0x02] {
            return None;
        }
        let name_len = u16::from_le_bytes([*zip.get(offset + 28)?, *zip.get(offset + 29)?]) as usize;
        let extra_len = u16::from_le_bytes([*zip.get(offset + 30)?, *zip.get(offset + 31)?]) as usize;
        let comment_len =
            u16::from_le_bytes([*zip.get(offset + 32)?, *zip.get(offset + 33)?]) as usize;
        let raw = zip.get(offset + 46..offset + 46 + name_len)?;
        names.push(String::from_utf8_lossy(raw).into_owned());
        offset += 46 + name_len + extra_len + comment_len;
    }
    Some(names)
}

/// `SystemTime` → ZIP 的 DOS 时间/日期字段（UTC ✓）。
pub fn dos_datetime(time: SystemTime) -> (u16, u16) {
    let (year, month, day, hour, minute, second) = crate::timefmt::parts_utc(time);
    let year = year.clamp(1980, 2107) as u16; // DOS 时间从 1980 起算 ✓
    let dos_date = ((year - 1980) << 9) | ((month as u16) << 5) | (day as u16);
    let dos_time = ((hour as u16) << 11) | ((minute as u16) << 5) | ((second as u16) / 2);
    (dos_time, dos_date)
}

fn push_u16(out: &mut Vec<u8>, value: u16) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn push_u32(out: &mut Vec<u8>, value: u32) {
    out.extend_from_slice(&value.to_le_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, UNIX_EPOCH};

    fn at(secs: u64) -> SystemTime {
        UNIX_EPOCH + Duration::from_secs(secs)
    }

    fn u16_at(buf: &[u8], idx: usize) -> u16 {
        u16::from_le_bytes([buf[idx], buf[idx + 1]])
    }

    fn u32_at(buf: &[u8], idx: usize) -> u32 {
        u32::from_le_bytes([buf[idx], buf[idx + 1], buf[idx + 2], buf[idx + 3]])
    }

    /// 极简 ZIP 读取：从 EOCD 走到中央目录，返回 `(名字, crc, 大小, 本地头偏移)` ✓
    fn read_central(zip: &[u8]) -> Vec<(String, u32, u32, u32)> {
        let eocd = (0..zip.len().saturating_sub(4))
            .rev()
            .find(|i| zip[*i..*i + 4] == [0x50, 0x4b, 0x05, 0x06])
            .expect("找不到 EOCD ✗");
        let count = u16_at(zip, eocd + 10) as usize;
        let mut offset = u32_at(zip, eocd + 16) as usize;
        let mut out = Vec::new();
        for _ in 0..count {
            assert_eq!(&zip[offset..offset + 4], &[0x50, 0x4b, 0x01, 0x02], "中央目录签名");
            let crc = u32_at(zip, offset + 16);
            let size = u32_at(zip, offset + 24);
            let name_len = u16_at(zip, offset + 28) as usize;
            let extra_len = u16_at(zip, offset + 30) as usize;
            let comment_len = u16_at(zip, offset + 32) as usize;
            let local = u32_at(zip, offset + 42);
            let name = String::from_utf8(zip[offset + 46..offset + 46 + name_len].to_vec()).unwrap();
            out.push((name, crc, size, local));
            offset += 46 + name_len + extra_len + comment_len;
        }
        out
    }

    #[test]
    fn crc32_matches_known_check_values() {
        assert_eq!(crc32(b""), 0x0000_0000);
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926, "CRC-32 标准校验值 ✓");
        assert_eq!(
            crc32(b"The quick brown fox jumps over the lazy dog"),
            0x414F_A339,
            "常见测试向量 ✓"
        );
        assert_eq!(crc32("中文内容".as_bytes()), crc32("中文内容".as_bytes()), "稳定 ✓");
    }

    #[test]
    fn zip_structure_and_contents_round_trip() {
        let text = "第一行\n第二行\n";
        let entries = [
            ZipEntry { name: "README.txt", data: text.as_bytes() },
            ZipEntry { name: "logs/campus_login.log", data: b"hello\n" },
        ];
        let zip = build(&entries, at(1_774_732_800)).unwrap();
        let central = read_central(&zip);
        assert_eq!(central.len(), 2);
        assert_eq!(central[0].0, "README.txt");
        assert_eq!(central[0].1, crc32(text.as_bytes()));
        assert_eq!(central[0].2, text.len() as u32);
        assert_eq!(central[1].0, "logs/campus_login.log");

        // 每个中央目录条目指向的本地头必须合法，且数据能原样取回 ✓
        for (idx, (name, crc, size, local)) in central.iter().enumerate() {
            let local = *local as usize;
            assert_eq!(&zip[local..local + 4], &[0x50, 0x4b, 0x03, 0x04], "本地头签名");
            // 本地头布局：0 签名 / 4 版本 / **6 flags** / 8 方式 / 26 名字长度 ✓
            assert_eq!(u16_at(&zip, local + 6) & 0x0800, 0x0800, "UTF-8 标志要置上 ✓");
            assert_eq!(u16_at(&zip, local + 8), METHOD_STORE, "必须是不压缩 ✓");
            let name_len = u16_at(&zip, local + 26) as usize;
            let data_start = local + 30 + name_len;
            let stored = &zip[data_start..data_start + *size as usize];
            assert_eq!(stored, entries[idx].data, "数据要一模一样 ✓");
            assert_eq!(crc32(stored), *crc, "本地头与中央目录的 CRC 要一致 ✓");
            assert_eq!(String::from_utf8(stored.to_vec()).unwrap(), if idx == 0 { text } else { "hello\n" });
            assert_eq!(&zip[local + 30..local + 30 + name_len], name.as_bytes(), "名字一致 ✓");
        }
    }

    #[test]
    fn empty_file_and_deterministic_output() {
        let entries = [ZipEntry { name: "empty.txt", data: b"" }];
        let a = build(&entries, at(0)).unwrap();
        let b = build(&entries, at(0)).unwrap();
        assert_eq!(a, b, "同样输入必须产出同样字节 ✓（便于比对摘要）");
        let central = read_central(&a);
        assert_eq!(central[0].2, 0);
        assert_eq!(central[0].1, 0, "空文件 CRC = 0 ✓");
    }

    #[test]
    fn dos_datetime_matches_the_math() {
        // 2026-03-28 21:20:00Z → date = (46<<9)|(3<<5)|28 = 23676；time = (21<<11)|(20<<5)|0 = 43648
        let (time, date) = dos_datetime(at(1_774_732_800));
        assert_eq!(date, 23676);
        assert_eq!(time, 43648);
        // 1980 年之前会被夹到 1980（DOS 起点 ✓，不 panic ✓）
        let (_, ancient) = dos_datetime(UNIX_EPOCH);
        assert_eq!(ancient, (0 << 9) | (1 << 5) | 1, "1970 会被夹成 1980-01-01 ✓");
    }

    #[test]
    fn oversized_name_is_rejected_not_truncated() {
        let long = "a".repeat(70_000);
        let err = build(&[ZipEntry { name: &long, data: b"x" }], at(0)).unwrap_err();
        assert!(err.contains("条目名不合法"), "{}", err);
        assert!(build(&[ZipEntry { name: "", data: b"x" }], at(0)).is_err());
    }

    #[test]
    fn entry_names_helper_reads_central_directory() {
        let entries = [
            ZipEntry { name: "a.txt", data: b"a" },
            ZipEntry { name: "dir/b.json", data: b"{}" },
        ];
        let zip = build(&entries, at(0)).unwrap();
        assert_eq!(entry_names(&zip), Some(vec!["a.txt".to_string(), "dir/b.json".to_string()]));
        // 结构不对 → None（不 panic ✓）
        assert_eq!(entry_names(b"not a zip"), None);
        assert_eq!(entry_names(&[]), None);
    }
}

