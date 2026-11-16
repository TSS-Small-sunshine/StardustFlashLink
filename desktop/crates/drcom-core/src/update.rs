//! M4 自动更新（第一块）：**检查 + 下载 + 校验**。**不做自我替换 / 安装**。
//!
//! 为什么先只做这一块（M4 分批）：
//!   - 检查 / 下载 / 校验是**纯逻辑**，可以完全不联网地用假 HTTP 客户端单测 ✓；
//!   - 自我替换在 Windows 上会撞「正在运行的 exe 被锁」，还要处理安装器 / 回滚 / 看门狗 ——
//!     那是 M4 的下一块，先把「能不能拿到一个**可信的**安装包」做扎实 ✓。
//!
//! 与 2.x 的口径对齐：
//!   - **通道语义**：正式通道只收非 prerelease；预览 / 快照的收法见 channel::Channel::accepts ✓；
//!   - **SHA256 fail-closed**：摘要缺失 / 格式不对 / 对不上 → 一律拒绝（2.x I10 / P0-2 同款 ✓）；
//!   - **错误文案统一过 secret::scrub_url**：密码、URL 里的 token 绝不进日志 / 界面 ✓。
//!
//! R1：只用 std + serde_json（核心已有依赖 ✓），**零新增 crate** ✓。
//! HTTPS 传输在 net::CurlHttp（系统 curl；std 没有 TLS，见那边的说明 ✓）。

use crate::channel::{Channel, Version};
use crate::checksum;
use crate::config::Config;
use crate::net::{HttpDownload, HttpGet};
use crate::platform::Os;
use crate::secret;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// GitHub 仓库（与 2.x auto_update.py 的 GITHUB_REPO 一字不差 ✓）。
pub const GITHUB_REPO: &str = "TSS-Small-sunshine/StardustFlashLink";
/// releases 列表接口（**多页取 30 条**：通道过滤后还够用 ✓；只信主源 api.github.com ✓）。
pub const RELEASES_URL: &str =
    "https://api.github.com/repos/TSS-Small-sunshine/StardustFlashLink/releases?per_page=30";
/// 请求 UA（GitHub 对无 UA 的请求会 403 ✓）。
pub const USER_AGENT: &str = "StardustFlashLink/3.0";
/// GitHub 推荐的媒体类型（只收 JSON v3 ✓）。
pub const API_ACCEPT: &str = "application/vnd.github+json";
/// GitHub API 版本（固定住字段语义，2.x 也用 2022-11-28 ✓）。
pub const API_VERSION: &str = "2022-11-28";
/// 产物名前缀（CI 打包用 package::artifact_base，即 stardust-flash-link-<版本>-<目标> ✓）。
pub const ASSET_PREFIX: &str = "stardust-flash-link-";
/// 检查更新的 HTTP 预算（与 2.x HTTP_TIMEOUT_SEC = 8 一致 ✓）。
pub const DEFAULT_API_TIMEOUT: Duration = Duration::from_secs(8);
/// 下载安装包的预算（与 2.x DOWNLOAD_TIMEOUT_SEC = 300 一致 ✓）。
pub const DEFAULT_DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(300);
/// 拿不到 GitHub 的 size 时的**绝对**上限（有界，绝不允许无限下载 ✓）。
pub const MAX_ASSET_BYTES_UNKNOWN: u64 = 512 * 1024 * 1024;
/// 有 size 时允许的松弛倍数：size * 3 / 2（超了直接中止 ✓）。
const SIZE_SLACK_NUM: u64 = 3;
const SIZE_SLACK_DEN: u64 = 2;

/// 一个 release 里的安装包（字段来自 GitHub Asset 对象 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AssetInfo {
    /// 产物名，如 stardust-flash-link-3.0.0.0-preview.3-linux-x86_64.zip ✓
    pub name: String,
    /// browser_download_url（**只收 https** ✓）。
    pub url: String,
    /// 字节数（GitHub 的 size；缺失时 0 = 未知 ✓）。
    pub size: u64,
    /// 摘要（已剥掉 sha256: 前缀、小写；缺失 = None → fail-closed 拒绝下载 ✓）。
    pub sha256: Option<String>,
}

/// 一个 release（通道过滤后的结果 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReleaseInfo {
    /// 从 tag 解析出来的版本（复用 channel::Version ✓）。
    pub version: Version,
    /// 原始 tag（如 v3.0.0.0-preview.3 ✓）。
    pub tag: String,
    /// GitHub 的 prerelease 标记（与 tag 里的通道后缀互为印证 ✓）。
    pub prerelease: bool,
    /// ISO8601 发布时间（只作展示 ✓）。
    pub published_at: String,
    /// 这个 release 挂的资产（已过滤掉不安全的 URL / 名字 ✓）。
    pub assets: Vec<AssetInfo>,
}

/// 一次检查更新的结论（四种，界面 / CLI 直接照着说 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UpdateCheck {
    /// 已是最新（这条通道上没有比当前更新的版本 ✓）。
    UpToDate,
    /// 有更新（release + 适配本机的资产 ✓）。
    Available { release: ReleaseInfo, asset: AssetInfo },
    /// 主动跳过（通道没版本 / 本机没有对应产物 —— 宁可不更新，也不装错平台 ✓）。
    Skipped(String),
    /// 检查失败（网络 / 限流 / 401 / 500 / 坏 JSON —— 文案可读、已脱敏 ✓）。
    Error(String),
}

impl UpdateCheck {
    /// 一句中文结论（CLI / 界面直接显示 ✓）。
    pub fn summary_cn(&self) -> String {
        match self {
            UpdateCheck::UpToDate => "已是最新（这条通道上没有比你更新的版本）".to_string(),
            UpdateCheck::Available { release, asset } => {
                format!("发现新版本 {}（{}，{} 字节）", release.version, asset.name, asset.size)
            }
            UpdateCheck::Skipped(reason) => format!("跳过更新：{}", reason),
            UpdateCheck::Error(error) => error.clone(),
        }
    }

    pub fn is_available(&self) -> bool {
        matches!(self, UpdateCheck::Available { .. })
    }
}

/// 从配置里取要订阅的通道（update_channel 字段，默认正式版 ✓）。
pub fn channel_from_config(config: &Config) -> Channel {
    config.update_channel.into()
}

/// 把 GitHub 的 digest 归一化：剥 sha256: 前缀、转小写、校验 64 位十六进制 ✓。
///
/// 归一化失败 / 缺失 → None；下载侧**见到 None 就拒绝**（fail-closed ✓）。
pub fn normalize_sha256(raw: &str) -> Option<String> {
    let lowered = raw.trim().to_ascii_lowercase();
    let value = lowered.strip_prefix("sha256:").unwrap_or(&lowered).trim();
    if value.len() == 64 && value.chars().all(|c| c.is_ascii_hexdigit()) {
        Some(value.to_string())
    } else {
        None
    }
}

/// **纯函数**：解析 GitHub /releases 数组，按通道过滤 ✓。
///
/// 规则（与 2.x 的「只认非 prerelease」同源、但分得更细 ✓）：
///   - draft 一律忽略（草稿还没发布 ✓）；
///   - tag 解析不出四段版本 → 忽略（2.x 的 installer / 杂 tag 不会被当成版本 ✓）；
///   - tag 与 GitHub prerelease 标记**打架**（tag 说正式、标记说预发布）→ 忽略（宁可不认 ✓）；
///   - 通道过滤走 Channel::accepts（正式只收正式；预览收 preview/rc/release；快照全收 ✓）；
///   - 结果按版本**从新到旧**排好（调用方取第一个比当前新的即可 ✓）。
pub fn parse_releases(json: &str, channel: Channel) -> Result<Vec<ReleaseInfo>, String> {
    let root: serde_json::Value = serde_json::from_str(json).map_err(|e| {
        format!("GitHub releases JSON 解析失败：{}（可能被网络中间设备改写了响应）", e)
    })?;
    let array = root.as_array().ok_or_else(|| {
        "GitHub releases 接口返回的不是 JSON 数组（可能被限流或被代理替换）".to_string()
    })?;
    let mut releases: Vec<ReleaseInfo> = Vec::new();
    for entry in array {
        let Some(object) = entry.as_object() else { continue };
        // ① draft 一律忽略
        if object.get("draft").and_then(|value| value.as_bool()).unwrap_or(false) {
            continue;
        }
        // ② tag 必须能解析成四段版本
        let Some(tag) = object
            .get("tag_name")
            .and_then(|value| value.as_str())
            .map(str::trim)
            .filter(|tag| !tag.is_empty())
        else {
            continue;
        };
        let Some(version) = Version::parse(tag) else { continue };
        let prerelease = object.get("prerelease").and_then(|value| value.as_bool()).unwrap_or(false);
        // ③ tag 与 prerelease 标记打架 → 不认（安全侧）
        if prerelease && version.channel == Channel::Release {
            continue;
        }
        // ④ 通道过滤（正式通道只收非 prerelease 的正式版 ✓）
        if !channel.accepts(version.channel) {
            continue;
        }
        if channel == Channel::Release && prerelease {
            continue;
        }
        let published_at = object
            .get("published_at")
            .and_then(|value| value.as_str())
            .unwrap_or("")
            .to_string();
        let assets = parse_assets(object.get("assets"));
        releases.push(ReleaseInfo {
            version,
            tag: tag.to_string(),
            prerelease,
            published_at,
            assets,
        });
    }
    // 从新到旧：调用方拿第一个「比当前新」的就是最高版本 ✓
    releases.sort_by(|left, right| right.version.cmp(&left.version));
    Ok(releases)
}

/// 解析 assets 数组（过滤 http（非 https）/ 不安全名字 / 缺失 URL 的条目 ✓）。
fn parse_assets(value: Option<&serde_json::Value>) -> Vec<AssetInfo> {
    let mut assets = Vec::new();
    let Some(items) = value.and_then(|value| value.as_array()) else {
        return assets;
    };
    for item in items {
        let Some(object) = item.as_object() else { continue };
        let Some(name) = object
            .get("name")
            .and_then(|value| value.as_str())
            .map(str::trim)
            .filter(|name| is_safe_asset_name(name))
        else {
            continue;
        };
        let Some(url) = object
            .get("browser_download_url")
            .and_then(|value| value.as_str())
            .map(str::trim)
            .filter(|url| url.starts_with("https://"))
        else {
            continue;
        };
        let size = object.get("size").and_then(|value| value.as_u64()).unwrap_or(0);
        let sha256 = object.get("digest").and_then(|value| value.as_str()).and_then(normalize_sha256);
        assets.push(AssetInfo { name: name.to_string(), url: url.to_string(), size, sha256 });
    }
    assets
}

/// 产物名安全吗（**结构性白名单**，不是黑名单 replace ✓）。
///
/// 产物名会被当成本地文件名用（download 的默认落点 / 展示），所以只允许
/// 字母数字与 . _ - +；带路径分隔符或在名字里塞 .. 的一律丢弃 ✓。
pub fn is_safe_asset_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 160
        && !name.contains('/')
        && !name.contains('\\')
        && !name.contains("..")
        && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '+'))
}

/// **纯函数**：本机对应 CI 的目标标签（与 preview-3.0.yml 的 matrix.label 逐字一致 ✓）。
///
/// 只认 CI **真实构建过**的六个目标；其它平台（Windows ARM64 / x86 32 位等）
/// 宁可返回 Err，也不去猜一个可能装错的产物 ✗（宁可不更新 ✓）。
pub fn target_label_for(os: Os, arch: &str) -> Result<&'static str, String> {
    let arch = arch.trim().to_ascii_lowercase();
    let label = match (os, arch.as_str()) {
        (Os::Windows, "x86_64") => "windows-x86_64",
        (Os::Linux, "x86_64") => "linux-x86_64",
        (Os::Linux, "aarch64") => "linux-aarch64",
        (Os::Linux, "armv7") => "linux-armv7",
        (Os::MacOs, "aarch64") => "macos-aarch64",
        (Os::MacOs, "x86_64") => "macos-x86_64",
        _ => {
            return Err(format!(
                "本机平台（{} / {}）没有对应的官方构建产物 —— 宁可不更新，也不装错平台",
                os.label_cn(),
                arch
            ))
        }
    };
    Ok(label)
}

/// 该平台产物的**扩展名优先级**（同一平台可能有 zip / deb / dmg ✓）。
pub fn asset_extensions(os: Os) -> &'static [&'static str] {
    match os {
        Os::Windows => &["zip"],
        Os::Linux => &["zip", "deb"],
        Os::MacOs => &["dmg", "zip"],
    }
}

/// **纯函数**：按产物名约定挑本机要下的那个资产（保守匹配 ✓）。
///
/// 匹配规则（**必须同时满足**，宁可漏也不装错 ✓）：
///   1. 名字以 stardust-flash-link- 开头（CI 的产物名前缀 ✓）；
///   2. 名字里带 -<目标标签>.（标签来自 target_label_for，如 linux-x86_64 ✓）；
///   3. 扩展名在该平台的优先级列表里（zip / deb / dmg ✓）；
///   4. 名字通过 is_safe_asset_name 白名单 ✓。
///
/// 找不到 → None（原因用 pick_asset_or_reason 取可读中文 ✓）。
pub fn pick_asset<'a>(assets: &'a [AssetInfo], os: Os, arch: &str) -> Option<&'a AssetInfo> {
    pick_asset_or_reason(assets, os, arch).ok()
}

/// 同 pick_asset，但**找不到时给出可读中文原因**（CLI / 界面直接显示 ✓）。
pub fn pick_asset_or_reason<'a>(
    assets: &'a [AssetInfo],
    os: Os,
    arch: &str,
) -> Result<&'a AssetInfo, String> {
    let label = target_label_for(os, arch)?;
    let extensions = asset_extensions(os);
    for extension in extensions {
        let wanted = format!("-{}.{}", label, extension);
        for asset in assets {
            if asset.name.starts_with(ASSET_PREFIX)
                && asset.name.contains(&wanted)
                && is_safe_asset_name(&asset.name)
            {
                return Ok(asset);
            }
        }
    }
    Err(format!(
        "这个版本没有适配本机（{}）的安装包 —— 按约定应形如 {}<版本>-{}.({})；宁可不更新，也不装错平台",
        label,
        ASSET_PREFIX,
        label,
        extensions.join(" / ")
    ))
}

/// **纯函数**：检查更新（用**当前平台**判定资产 ✓）。
///
/// http 一般是 net::CurlHttp（HTTPS）；单测里换成假客户端即可离线跑 ✓。
pub fn check_for_update(current: Version, channel: Channel, http: &dyn HttpGet) -> UpdateCheck {
    check_for_update_on(
        current,
        channel,
        http,
        crate::platform::Os::current(),
        crate::platform::arch(),
    )
}

/// 同 check_for_update，但**平台可注入**（单测要覆盖全部六个目标 ✓）。
pub fn check_for_update_on(
    current: Version,
    channel: Channel,
    http: &dyn HttpGet,
    os: Os,
    arch: &str,
) -> UpdateCheck {
    let headers: [(&str, &str); 3] = [
        ("Accept", API_ACCEPT),
        ("X-GitHub-Api-Version", API_VERSION),
        ("User-Agent", USER_AGENT),
    ];
    let reply = match http.get_with_headers(RELEASES_URL, &headers, DEFAULT_API_TIMEOUT) {
        Ok(reply) => reply,
        Err(error) => {
            return UpdateCheck::Error(scrub_for_log(&format!("检查更新失败：{}", error)))
        }
    };
    if reply.status != 200 {
        return UpdateCheck::Error(scrub_for_log(&format!(
            "检查更新失败（{}）：{} —— {}",
            secret::scrub_url(RELEASES_URL),
            describe_status(reply.status),
            status_hint(reply.status, &reply.body),
        )));
    }
    let releases = match parse_releases(&reply.body, channel) {
        Ok(releases) => releases,
        Err(error) => {
            return UpdateCheck::Error(scrub_for_log(&format!(
                "检查更新失败（{}）：{}",
                secret::scrub_url(RELEASES_URL),
                error
            )))
        }
    };
    // 列表已按版本从新到旧排序 → 第一个比当前新的就是最高版本 ✓
    let Some(latest) = releases.iter().find(|release| release.version.is_newer_than(&current)) else {
        if releases.is_empty() {
            return UpdateCheck::Skipped(format!(
                "当前通道（{}）上没有可用的发布版本",
                channel.label_cn()
            ));
        }
        return UpdateCheck::UpToDate;
    };
    match pick_asset_or_reason(&latest.assets, os, arch) {
        Ok(asset) => UpdateCheck::Available {
            release: latest.clone(),
            asset: asset.clone(),
        },
        Err(reason) => {
            UpdateCheck::Skipped(format!("最新版本 {} 跳过：{}", latest.version, reason))
        }
    }
}

/// HTTP 状态码的中文描述（可读 ✓）。
fn describe_status(status: u16) -> String {
    match status {
        401 => "凭据被拒绝（HTTP 401）".to_string(),
        403 | 429 => format!("请求被拒绝 / 限流（HTTP {}）", status),
        404 => "仓库或接口不存在（HTTP 404）".to_string(),
        500..=599 => format!("GitHub 服务端错误（HTTP {}）", status),
        200 => "成功（HTTP 200）".to_string(),
        other => format!("意外状态码（HTTP {}）", other),
    }
}

/// 状态码 + 响应体给一句**可操作**的建议 ✓。
fn status_hint(status: u16, body: &str) -> String {
    let lower = body.to_ascii_lowercase();
    match status {
        401 => "GitHub 拒绝了请求：仓库可能是私有的，或网络中间设备改写了请求".to_string(),
        403 | 429 => {
            if lower.contains("rate limit") {
                "GitHub API 限流了（匿名请求每小时 60 次）—— 过一会儿再试".to_string()
            } else {
                "GitHub 拒绝了这次请求（可能被限流，或网络中间设备拦截）".to_string()
            }
        }
        404 => "仓库地址或 releases 接口不存在（检查是否改名）".to_string(),
        500..=599 => "GitHub 暂时不可用，请稍后再试".to_string(),
        _ => "请稍后再试".to_string(),
    }
}

/// 可进日志 / 可回显的文本：先按 2.x 口径擦整条 URL（secret::scrub_url），
/// 再把 token= / api_key= 这类参数打码（spec 要求：URL 里的 token 参数也要擦除 ✓）。
pub fn scrub_for_log(text: &str) -> String {
    mask_token_params(&secret::scrub_url(text))
}

/// 把 token= / access_token= / api_key= / secret= 的值打码（保留参数名 ✓）。
fn mask_token_params(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(index) = find_token_param(rest) {
        let (head, tail) = rest.split_at(index);
        out.push_str(head);
        match tail.find('=') {
            Some(eq) => {
                out.push_str(&tail[..eq + 1]);
                out.push_str("***");
                let after = &tail[eq + 1..];
                let end = after
                    .find(|c: char| c == '&' || c.is_whitespace() || c == '"' || c == '\'')
                    .unwrap_or(after.len());
                rest = &after[end..];
            }
            None => {
                out.push_str(tail);
                return out;
            }
        }
    }
    out.push_str(rest);
    out
}

/// 找最靠前的敏感参数名 ✓。
fn find_token_param(text: &str) -> Option<usize> {
    let lower = text.to_ascii_lowercase();
    let mut found: Option<usize> = None;
    for needle in ["access_token=", "api_key=", "apikey=", "token=", "secret="] {
        if let Some(index) = lower.find(needle) {
            found = Some(found.map_or(index, |current: usize| current.min(index)));
        }
    }
    found
}

/// **纯函数**：有 size 时最多允许 1.5 倍；没有 size（0）时给绝对上限 ✓。
pub fn asset_size_limit(size: u64) -> u64 {
    if size == 0 {
        MAX_ASSET_BYTES_UNKNOWN
    } else {
        size.saturating_mul(SIZE_SLACK_NUM) / SIZE_SLACK_DEN
    }
}

/// 下载中的临时文件路径：dest.part（同目录 → rename 才是原子替换 ✓）。
pub fn part_path(dest: &Path) -> PathBuf {
    let mut name = dest.as_os_str().to_os_string();
    name.push(".part");
    PathBuf::from(name)
}

/// 下载 + 校验 + 原子落盘（**fail-closed**：任何一步不对都拒绝并清理 .part ✓）。
///
/// 顺序：
///   1. 摘要缺失 / 格式不对 → **立刻拒绝**（连下载都不开始 —— 2.x P0-2 同款 ✓）；
///   2. 下载到 dest.part（有界：size * 1.5，未知 size 用绝对上限 ✓）；
///   3. SHA256 比对（checksum::sha256_file 流式算 ✓）；
///   4. 一切通过才 rename 成 dest；失败一律删掉 .part，绝不留半成品 ✓。
///
/// 返回写入的字节数 ✓。
pub fn download_and_verify(
    http: &dyn HttpDownload,
    asset: &AssetInfo,
    dest: &Path,
) -> Result<u64, String> {
    let expected = match asset.sha256.as_deref().and_then(normalize_sha256) {
        Some(sha) => sha,
        None => {
            return Err(scrub_for_log(&format!(
                "拒绝下载 {}：这个安装包没有有效的 SHA256 摘要（fail-closed —— 宁可不更新，也不装没校验的包）",
                asset.name
            )))
        }
    };
    if let Some(parent) = dest.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("建下载目录 {} 失败: {}", parent.display(), e))?;
        }
    }
    let part = part_path(dest);
    let limit = asset_size_limit(asset.size);
    let written = match http.download(&asset.url, &part, limit, DEFAULT_DOWNLOAD_TIMEOUT) {
        Ok(written) => written,
        Err(error) => {
            let _ = std::fs::remove_file(&part);
            return Err(scrub_for_log(&format!("下载 {} 失败：{}", asset.name, error)));
        }
    };
    if written > limit {
        let _ = std::fs::remove_file(&part);
        return Err(format!(
            "下载体积（{} 字节）超过上限 {} —— 已删除半成品",
            written, limit
        ));
    }
    if asset.size > 0 && written != asset.size {
        let _ = std::fs::remove_file(&part);
        return Err(format!(
            "下载体积不对（期望 {} 字节，实际 {}）—— 已删除半成品",
            asset.size, written
        ));
    }
    let actual = match checksum::sha256_file(&part) {
        Ok(value) => value.to_ascii_lowercase(),
        Err(error) => {
            let _ = std::fs::remove_file(&part);
            return Err(scrub_for_log(&format!("读取下载文件失败：{}", error)));
        }
    };
    if actual != expected {
        let _ = std::fs::remove_file(&part);
        return Err(format!(
            "SHA256 校验失败（期望 {}，实际 {}）—— 已删除下载文件",
            expected, actual
        ));
    }
    if let Err(error) = std::fs::rename(&part, dest) {
        let _ = std::fs::remove_file(&part);
        return Err(format!("把下载文件改名为 {} 失败: {}", dest.display(), error));
    }
    Ok(written)
}

/// **纯函数**：检查结果的逐行文案（CLI 直接打印；测试逐字断言 ✓）。
pub fn report_lines(check: &UpdateCheck, channel: Channel) -> Vec<String> {
    let mut lines = vec![format!("通道: {}（{}）", channel.label_cn(), channel.as_str())];
    match check {
        UpdateCheck::UpToDate => {
            lines.push("结果: 已是最新（这条通道上没有比你更新的版本）".to_string())
        }
        UpdateCheck::Available { release, asset } => {
            lines.push(format!("结果: 发现新版本 {}（tag {}）", release.version, release.tag));
            if !release.published_at.is_empty() {
                lines.push(format!("发布时间: {}", release.published_at));
            }
            lines.push(format!("资产: {}", asset.name));
            lines.push(format!(
                "大小: {} 字节（{:.1} MB）",
                asset.size,
                asset.size as f64 / (1024.0 * 1024.0)
            ));
            match asset.sha256.as_deref() {
                Some(sha) => lines.push(format!("SHA256: {}", sha)),
                None => lines
                    .push("SHA256: （缺失 —— 按 fail-closed 规则不会下载）".to_string()),
            }
            lines.push(format!("下载: {}", asset.url));
        }
        UpdateCheck::Skipped(reason) => lines.push(format!("结果: 跳过更新 —— {}", reason)),
        UpdateCheck::Error(error) => lines.push(format!("结果: {}", error)),
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::net::HttpReply;
    use std::cell::RefCell;

    /// 假 HTTP 客户端：完全离线地把 GitHub 的各种响应（200 / 401 / 500 / 坏 JSON / 连不上）喂进来 ✓。
    struct FakeHttp {
        status: u16,
        body: String,
        error: Option<String>,
        seen_urls: RefCell<Vec<String>>,
        seen_headers: RefCell<Vec<(String, String)>>,
    }

    impl FakeHttp {
        fn ok(body: String) -> FakeHttp {
            FakeHttp {
                status: 200,
                body,
                error: None,
                seen_urls: RefCell::new(Vec::new()),
                seen_headers: RefCell::new(Vec::new()),
            }
        }

        fn status(status: u16, body: &str) -> FakeHttp {
            let mut fake = FakeHttp::ok(body.to_string());
            fake.status = status;
            fake
        }

        fn failing(message: &str) -> FakeHttp {
            let mut fake = FakeHttp::ok(String::new());
            fake.error = Some(message.to_string());
            fake
        }
    }

    impl HttpGet for FakeHttp {
        fn get(&self, url: &str, _timeout: Duration) -> Result<HttpReply, String> {
            self.seen_urls.borrow_mut().push(url.to_string());
            match &self.error {
                Some(error) => Err(error.clone()),
                None => Ok(HttpReply { status: self.status, location: None, body: self.body.clone() }),
            }
        }

        fn get_with_headers(
            &self,
            url: &str,
            headers: &[(&str, &str)],
            timeout: Duration,
        ) -> Result<HttpReply, String> {
            for (name, value) in headers {
                self.seen_headers
                    .borrow_mut()
                    .push((name.to_string(), value.to_string()));
            }
            self.get(url, timeout)
        }
    }

    /// 假下载器：模拟「成功」「写了一半再失败」「超上限」三种真实路径 ✓。
    struct FakeDownload {
        data: Vec<u8>,
        error: Option<String>,
    }

    impl FakeDownload {
        fn new(data: &[u8]) -> FakeDownload {
            FakeDownload { data: data.to_vec(), error: None }
        }

        fn failing(error: &str) -> FakeDownload {
            FakeDownload { data: b"half-written".to_vec(), error: Some(error.to_string()) }
        }
    }

    impl HttpDownload for FakeDownload {
        fn download(
            &self,
            _url: &str,
            dest: &std::path::Path,
            max_bytes: u64,
            _timeout: Duration,
        ) -> Result<u64, String> {
            if let Some(error) = &self.error {
                // 模拟「写了一半再失败」→ 调用方必须清掉半成品 ✓
                let _ = std::fs::write(dest, &self.data);
                return Err(error.clone());
            }
            if self.data.len() as u64 > max_bytes {
                let _ = std::fs::write(dest, &self.data);
                return Err(format!("下载体积超过上限（{} 字节）", max_bytes));
            }
            std::fs::write(dest, &self.data).map_err(|e| e.to_string())?;
            Ok(self.data.len() as u64)
        }
    }

    fn asset_entry(name: &str, size: u64, digest: Option<&str>) -> String {
        let digest_field = match digest {
            Some(digest) => format!("\"digest\":\"{}\",", digest),
            None => String::new(),
        };
        format!(
            "{{\"name\":\"{name}\",\"browser_download_url\":\"https://github.com/TSS-Small-sunshine/StardustFlashLink/releases/download/v3/{name}\",{digest_field}\"size\":{size}}}"
        )
    }

    fn release_entry(tag: &str, prerelease: bool, draft: bool, assets: &[String]) -> String {
        format!(
            "{{\"tag_name\":\"{tag}\",\"prerelease\":{prerelease},\"draft\":{draft},\"published_at\":\"2026-11-16T00:00:00Z\",\"assets\":[{}]}}",
            assets.join(",")
        )
    }

    fn releases(entries: &[String]) -> String {
        format!("[{}]", entries.join(","))
    }

    fn fake_asset(name: &str) -> AssetInfo {
        AssetInfo {
            name: name.to_string(),
            url: format!("https://github.com/TSS-Small-sunshine/StardustFlashLink/releases/download/v3/{}", name),
            size: 10,
            sha256: Some("0".repeat(64)),
        }
    }

    fn current_asset_name(version: &str) -> String {
        format!("stardust-flash-link-{}-{}.zip", version, crate::platform::target_label())
    }

    fn current_arch() -> &'static str {
        crate::platform::arch()
    }

    fn fresh_version() -> Version {
        Version::parse("3.0.0.0-preview.1").unwrap()
    }

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sfl-update-{}-{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn release_channel_accepts_only_non_prerelease_and_ignores_drafts() {
        let body = releases(&[
            release_entry("v3.0.0.0", false, false, &[]),
            release_entry("v3.0.0.0-preview.3", true, false, &[]),
            release_entry("v3.0.0.0-preview.4", true, true, &[]),
        ]);
        let stable = parse_releases(&body, Channel::Release).unwrap();
        assert_eq!(stable.len(), 1, "正式通道只收 v3.0.0.0 ✓: {:?}", stable);
        assert_eq!(stable[0].tag, "v3.0.0.0");
        assert!(!stable[0].prerelease);
        // 预览通道：非草稿的两条都要（preview 与正式版都算「比预览用户新」的候选 ✓）
        let preview = parse_releases(&body, Channel::Preview).unwrap();
        assert_eq!(preview.len(), 2);
        assert!(preview.iter().any(|release| release.tag == "v3.0.0.0-preview.3"));
        assert!(!preview.iter().any(|release| release.tag == "v3.0.0.0-preview.4"), "draft 必须忽略 ✗");
        // 排序：从新到旧（正式版 3.0.0.0 > preview.3 ✓）
        assert_eq!(preview[0].tag, "v3.0.0.0", "排序错了: {:?}", preview);
    }

    #[test]
    fn preview_takes_preview_rc_release_while_snapshot_takes_all() {
        let body = releases(&[
            release_entry("v3.0.1.0-snapshot.2", true, false, &[]),
            release_entry("v3.0.1.0-preview.3", true, false, &[]),
            release_entry("v3.0.1.0-rc.1", true, false, &[]),
            release_entry("v3.0.1.0", false, false, &[]),
        ]);
        let preview = parse_releases(&body, Channel::Preview).unwrap();
        assert_eq!(preview.len(), 3);
        assert!(!preview.iter().any(|release| release.tag.ends_with("snapshot.2")));
        let snapshot = parse_releases(&body, Channel::Snapshot).unwrap();
        assert_eq!(snapshot.len(), 4, "快照通道全收 ✓");
        let rc = parse_releases(&body, Channel::Rc).unwrap();
        assert_eq!(rc.len(), 2, "候选通道收 rc + 正式版 ✓");
    }

    #[test]
    fn version_comparison_uses_numbers_then_channel_then_sequence() {
        let preview2 = Version::parse("3.0.0.0-preview.2").unwrap();
        let preview3 = Version::parse("3.0.0.0-preview.3").unwrap();
        let preview9 = Version::parse("3.0.0.0-preview.9").unwrap();
        let stable = Version::parse("3.0.0.0").unwrap();
        assert!(preview3.is_newer_than(&preview2), "preview.3 > preview.2 ✓");
        assert!(stable.is_newer_than(&preview9), "3.0.0.0 > 3.0.0.0-preview.9 ✓");
        // 端到端：当前 preview.9 → 正式版 v3.0.0.0 必须被判成「有更新」
        let asset_name = current_asset_name("3.0.0.0");
        let body = releases(&[release_entry(
            "v3.0.0.0",
            false,
            false,
            &[asset_entry(&asset_name, 128, Some(&checksum::sha256_hex(b"x")))],
        )]);
        let check = check_for_update_on(
            preview9,
            Channel::Release,
            &FakeHttp::ok(body),
            crate::platform::Os::current(),
            current_arch(),
        );
        assert!(check.is_available(), "{:?}", check);
    }

    #[test]
    fn check_finds_update_for_current_platform_and_sends_github_headers() {
        let digest = checksum::sha256_hex(b"demo-package");
        let asset_name = current_asset_name("3.0.0.0-preview.3");
        let body = releases(&[release_entry(
            "v3.0.0.0-preview.3",
            true,
            false,
            &[asset_entry(&asset_name, 4_096, Some(&format!("sha256:{}", digest.to_uppercase())))],
        )]);
        let http = FakeHttp::ok(body);
        let check = check_for_update_on(
            fresh_version(),
            Channel::Preview,
            &http,
            crate::platform::Os::current(),
            current_arch(),
        );
        match &check {
            UpdateCheck::Available { release, asset } => {
                assert_eq!(release.version, Version::parse("3.0.0.0-preview.3").unwrap());
                assert_eq!(release.tag, "v3.0.0.0-preview.3");
                assert!(release.prerelease);
                assert_eq!(asset.name, asset_name);
                assert_eq!(asset.size, 4_096);
                assert_eq!(asset.sha256.as_deref(), Some(digest.as_str()), "摘要要归一化成小写 ✓");
                assert!(release.published_at.contains("2026-11-16"));
            }
            other => panic!("应当发现更新，实际: {:?}", other),
        }
        // 请求头：GitHub 要的 Accept / UA / API 版本一个都不能少 ✓
        let headers = http.seen_headers.borrow();
        assert!(headers
            .iter()
            .any(|(name, value)| name.eq_ignore_ascii_case("accept") && value == API_ACCEPT));
        assert!(headers
            .iter()
            .any(|(name, value)| name.eq_ignore_ascii_case("user-agent") && value == USER_AGENT));
        assert!(headers
            .iter()
            .any(|(name, _)| name.eq_ignore_ascii_case("x-github-api-version")));
        // 请求打的必须是 releases 列表（不是 latest）✓
        assert_eq!(http.seen_urls.borrow().len(), 1);
        assert_eq!(http.seen_urls.borrow()[0], RELEASES_URL);
    }

    #[test]
    fn same_version_or_older_is_up_to_date() {
        let asset_name = current_asset_name("3.0.0.0-preview.1");
        let body = releases(&[release_entry(
            "v3.0.0.0-preview.1",
            false,
            false,
            &[asset_entry(&asset_name, 1, Some(&checksum::sha256_hex(b"x")))],
        )]);
        let check = check_for_update_on(
            fresh_version(),
            Channel::Preview,
            &FakeHttp::ok(body),
            crate::platform::Os::current(),
            current_arch(),
        );
        assert_eq!(check, UpdateCheck::UpToDate);
    }

    #[test]
    fn empty_channel_is_skipped_not_a_fake_update() {
        let body = releases(&[release_entry("v3.0.0.0-preview.2", true, false, &[])]);
        let check = check_for_update_on(
            fresh_version(),
            Channel::Release,
            &FakeHttp::ok(body),
            crate::platform::Os::current(),
            current_arch(),
        );
        match check {
            UpdateCheck::Skipped(reason) => {
                assert!(reason.contains("正式版"), "原因要能看懂: {}", reason)
            }
            other => panic!("应当跳过而不是报更新: {:?}", other),
        }
    }

    #[test]
    fn newest_release_without_local_asset_is_skipped_instead_of_falling_back() {
        // 最新版只挂了别的平台的包，旧版才有本机的 —— 仍然跳过（宁可不更新 ✓）
        let wrong_label = ["windows-x86_64", "linux-x86_64", "linux-aarch64", "linux-armv7", "macos-aarch64", "macos-x86_64"]
            .iter()
            .find(|label| **label != crate::platform::target_label())
            .expect("总能找到一个别的目标标签")
            .to_string();
        let body = releases(&[
            release_entry(
                "v3.0.0.0-preview.3",
                true,
                false,
                &[asset_entry(
                    &format!("stardust-flash-link-3.0.0.0-preview.3-{}.zip", wrong_label),
                    1,
                    Some(&checksum::sha256_hex(b"a")),
                )],
            ),
            release_entry(
                "v3.0.0.0-preview.2",
                true,
                false,
                &[asset_entry(&current_asset_name("3.0.0.0-preview.2"), 1, Some(&checksum::sha256_hex(b"b")))],
            ),
        ]);
        let check = check_for_update_on(
            fresh_version(),
            Channel::Preview,
            &FakeHttp::ok(body),
            crate::platform::Os::current(),
            current_arch(),
        );
        match check {
            UpdateCheck::Skipped(reason) => {
                assert!(reason.contains("3.0.0.0-preview.3"), "要说明是哪一版跳过: {}", reason)
            }
            other => panic!("不许回退到旧版本: {:?}", other),
        }
    }

    #[test]
    fn unsupported_platform_is_skipped_with_a_readable_reason() {
        let asset_name = current_asset_name("3.0.0.0-preview.3");
        let body = releases(&[release_entry(
            "v3.0.0.0-preview.3",
            true,
            false,
            &[asset_entry(&asset_name, 1, Some(&checksum::sha256_hex(b"x")))],
        )]);
        // Windows ARM64 没有官方产物 → 宁可跳过，也不装 x64 包 ✗
        let check = check_for_update_on(
            fresh_version(),
            Channel::Preview,
            &FakeHttp::ok(body),
            crate::platform::Os::Windows,
            "aarch64",
        );
        match check {
            UpdateCheck::Skipped(reason) => {
                assert!(reason.contains("没有对应"), "{}", reason);
                assert!(reason.contains("宁可不更新"), "{}", reason);
            }
            other => panic!("未知平台不该给可用更新: {:?}", other),
        }
    }

    #[test]
    fn http_errors_are_readable_and_scrubbed() {
        for (status, needle) in [(401u16, "401"), (403, "403"), (429, "429"), (500, "500")] {
            let check = check_for_update_on(
                fresh_version(),
                Channel::Preview,
                &FakeHttp::status(status, "{\"message\":\"API rate limit exceeded\"}"),
                crate::platform::Os::current(),
                current_arch(),
            );
            match check {
                UpdateCheck::Error(error) => {
                    assert!(error.contains("检查更新失败"), "错误要能看懂: {}", error);
                    assert!(error.contains(needle), "要带状态码 {}: {}", needle, error);
                    assert!(error.contains("<url>"), "URL 必须被擦成 <url>: {}", error);
                    assert!(!error.contains("api.github.com"), "真实 URL 不许回显: {}", error);
                }
                other => panic!("HTTP {} 应当报错: {:?}", status, other),
            }
        }
    }

    #[test]
    fn bad_json_is_a_readable_error() {
        let err = parse_releases("{not json", Channel::Release).unwrap_err();
        assert!(err.contains("解析失败"), "{}", err);
        let err = parse_releases("{\"message\":\"API rate limit exceeded\"}", Channel::Release).unwrap_err();
        assert!(err.contains("不是 JSON 数组"), "{}", err);
        let check = check_for_update_on(
            fresh_version(),
            Channel::Preview,
            &FakeHttp::ok("not-json".to_string()),
            crate::platform::Os::current(),
            current_arch(),
        );
        match check {
            UpdateCheck::Error(error) => {
                assert!(error.contains("检查更新失败"), "{}", error);
                assert!(error.contains("<url>"), "{}", error);
            }
            other => panic!("坏 JSON 应当报错: {:?}", other),
        }
    }

    #[test]
    fn transport_errors_never_leak_credentials() {
        let check = check_for_update_on(
            fresh_version(),
            Channel::Preview,
            &FakeHttp::failing("boom https://api.github.com/x?access_token=SECRET-TOKEN-123&note=1"),
            crate::platform::Os::current(),
            current_arch(),
        );
        match check {
            UpdateCheck::Error(error) => {
                assert!(!error.contains("SECRET-TOKEN-123"), "token 泄漏: {}", error);
                assert!(error.contains("<url>"), "{}", error);
            }
            other => panic!("{:?}", other),
        }
        // 纯函数：没有 URL 外壳的裸参数也要打码 ✓
        let text = scrub_for_log("请求失败 token=abc123&x=1 password=pw");
        assert!(!text.contains("abc123"), "{}", text);
        assert!(!text.contains("pw"), "{}", text);
        assert!(text.contains("token=***"), "{}", text);
        assert!(text.contains("x=1"), "非敏感参数要保留: {}", text);
        assert_eq!(scrub_for_log("一切正常"), "一切正常");
    }

    #[test]
    fn platform_matrix_picks_exactly_one_matching_asset() {
        let labels = [
            "windows-x86_64",
            "linux-x86_64",
            "linux-aarch64",
            "linux-armv7",
            "macos-aarch64",
            "macos-x86_64",
        ];
        let assets: Vec<AssetInfo> = labels
            .iter()
            .map(|label| fake_asset(&format!("stardust-flash-link-3.0.0.0-{}.zip", label)))
            .collect();
        let cases = [
            (crate::platform::Os::Windows, "x86_64", "windows-x86_64"),
            (crate::platform::Os::Linux, "x86_64", "linux-x86_64"),
            (crate::platform::Os::Linux, "aarch64", "linux-aarch64"),
            (crate::platform::Os::Linux, "armv7", "linux-armv7"),
            (crate::platform::Os::MacOs, "aarch64", "macos-aarch64"),
            (crate::platform::Os::MacOs, "x86_64", "macos-x86_64"),
        ];
        for (os, arch, expected) in cases {
            let picked = pick_asset(&assets, os, arch)
                .unwrap_or_else(|| panic!("{} / {} 应当挑到资产", os.label_cn(), arch));
            assert!(picked.name.contains(expected), "挑错平台了: {}", picked.name);
            let labels_seen: Vec<&str> = labels
                .iter()
                .copied()
                .filter(|label| picked.name.contains(*label))
                .collect();
            assert_eq!(labels_seen, vec![expected], "一个名字只该命中一个平台: {:?}", labels_seen);
        }
        // 没构建过的平台：必须 Err / None（不猜）
        assert!(pick_asset(&assets, crate::platform::Os::Windows, "aarch64").is_none());
        let err = pick_asset_or_reason(&assets, crate::platform::Os::Windows, "aarch64").unwrap_err();
        assert!(err.contains("没有对应"), "{}", err);
        // 缺本机资产：None + 可读原因
        let only_mac = vec![fake_asset("stardust-flash-link-3.0.0.0-macos-x86_64.zip")];
        assert!(pick_asset(&only_mac, crate::platform::Os::Linux, "x86_64").is_none());
        let err = pick_asset_or_reason(&only_mac, crate::platform::Os::Linux, "x86_64").unwrap_err();
        assert!(err.contains("linux-x86_64"), "{}", err);
        assert!(err.contains("宁可不更新"), "{}", err);
        // 前缀 / 扩展名不对 → 不认（宁可漏也不装错 ✓）
        let junk = vec![
            fake_asset("installer-3.0.0.0-linux-x86_64.zip"),
            fake_asset("stardust-flash-link-3.0.0.0-linux-x86_64.exe"),
            fake_asset("stardust-flash-link-3.0.0.0-linux-x86_64.tar.gz"),
        ];
        assert!(pick_asset(&junk, crate::platform::Os::Linux, "x86_64").is_none());
    }

    #[test]
    fn asset_extension_priority_follows_the_platform() {
        let zip = fake_asset("stardust-flash-link-3.0.0.0-linux-x86_64.zip");
        let deb = fake_asset("stardust-flash-link-3.0.0.0-linux-x86_64.deb");
        let assets = vec![deb, zip.clone()];
        assert_eq!(pick_asset(&assets, crate::platform::Os::Linux, "x86_64").unwrap().name, zip.name);
        let dmg = fake_asset("stardust-flash-link-3.0.0.0-macos-aarch64.dmg");
        let mac_zip = fake_asset("stardust-flash-link-3.0.0.0-macos-aarch64.zip");
        let mac_assets = vec![mac_zip, dmg.clone()];
        assert_eq!(pick_asset(&mac_assets, crate::platform::Os::MacOs, "aarch64").unwrap().name, dmg.name);
    }

    #[test]
    fn helpers_are_pure_and_conservative() {
        let hex = "a".repeat(64);
        assert_eq!(normalize_sha256(&format!("sha256:{}", hex)).as_deref(), Some(hex.as_str()));
        assert_eq!(
            normalize_sha256(&hex.to_uppercase()).as_deref(),
            Some(hex.as_str()),
            "大写也要认，统一归一化成小写 ✓"
        );
        assert!(normalize_sha256("").is_none());
        assert!(normalize_sha256("sha256:xyz").is_none());
        assert!(normalize_sha256(&"a".repeat(63)).is_none());
        // 产物名白名单（结构性判断，不做 replace 黑名单 ✓）
        assert!(is_safe_asset_name("stardust-flash-link-3.0.0.0-linux-x86_64.zip"));
        assert!(!is_safe_asset_name("../evil.zip"));
        assert!(!is_safe_asset_name("a/b.zip"));
        assert!(!is_safe_asset_name("a\\b.zip"));
        assert!(!is_safe_asset_name(""));
        // 有界下载
        assert_eq!(asset_size_limit(100), 150);
        assert_eq!(asset_size_limit(0), MAX_ASSET_BYTES_UNKNOWN);
        // .part 必须和目标同目录（rename 才是原子替换 ✓）
        let dest = Path::new("some/dir/pkg.zip");
        let part = part_path(dest);
        assert_eq!(part.parent(), dest.parent());
        assert!(part.to_string_lossy().ends_with(".zip.part"));
        // 解析时把不安全 URL / 名字的资产过滤掉，digest 缺失保留 None（交给 fail-closed ✓）
        let body = releases(&[release_entry(
            "v3.0.0.0-preview.9",
            true,
            false,
            &[
                "{\"name\":\"../evil.zip\",\"browser_download_url\":\"https://github.com/x\",\"size\":1}".to_string(),
                "{\"name\":\"stardust-flash-link-3.0.0.0-preview.9-linux-x86_64.zip\",\"browser_download_url\":\"http://github.com/x\",\"size\":1}".to_string(),
                asset_entry("stardust-flash-link-3.0.0.0-preview.9-linux-x86_64.zip", 7, None),
            ],
        )]);
        let parsed = parse_releases(&body, Channel::Preview).unwrap();
        assert_eq!(parsed[0].assets.len(), 1, "不安全的 URL / 名字必须被过滤: {:?}", parsed[0].assets);
        assert!(parsed[0].assets[0].sha256.is_none(), "没有 digest 必须是 None ✓");
    }

    #[test]
    fn config_update_channel_is_the_default_channel() {
        let cfg = Config::default();
        assert_eq!(cfg.update_channel, crate::config::UpdateChannel::Release);
        assert_eq!(channel_from_config(&cfg), Channel::Release);
        let mut preview = Config::default();
        preview.update_channel = crate::config::UpdateChannel::Preview;
        assert_eq!(channel_from_config(&preview), Channel::Preview);
        // auto_update_enabled 是独立开关（2.x 默认关 ✓）：手动检查不受它影响，但界面/CLI 要如实显示
        assert!(!Config::default().auto_update_enabled);
    }

    #[test]
    fn download_verifies_sha_and_publishes_atomically() {
        let dir = temp_dir("download-ok");
        let dest = dir.join("pkg.zip");
        let data = b"pretend this is an installer".to_vec();
        let asset = AssetInfo {
            name: "stardust-flash-link-3.0.0.0-linux-x86_64.zip".to_string(),
            url: "https://github.com/TSS-Small-sunshine/StardustFlashLink/releases/download/v3/x.zip".to_string(),
            size: data.len() as u64,
            sha256: Some(checksum::sha256_hex(&data)),
        };
        let written = download_and_verify(&FakeDownload::new(&data), &asset, &dest)
            .expect("摘要对得上就该成功");
        assert_eq!(written, data.len() as u64);
        assert_eq!(std::fs::read(&dest).unwrap(), data, "落盘内容必须逐字节一致 ✓");
        assert!(!part_path(&dest).exists(), "成功后不许留下 .part ✗");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_or_invalid_sha256_is_refused_before_downloading() {
        let dir = temp_dir("download-no-sha");
        let dest = dir.join("pkg.zip");
        let data = b"payload".to_vec();
        for broken in [None, Some("sha256:not-a-real-digest".to_string())] {
            let asset = AssetInfo {
                name: "stardust-flash-link-3.0.0.0-linux-x86_64.zip".to_string(),
                url: "https://github.com/x/y.zip".to_string(),
                size: data.len() as u64,
                sha256: broken,
            };
            let err = download_and_verify(&FakeDownload::new(&data), &asset, &dest)
                .expect_err("缺摘要必须拒绝（fail-closed ✓）");
            assert!(err.contains("SHA256"), "{}", err);
            assert!(err.contains("fail-closed") || err.contains("没有有效"), "{}", err);
            assert!(!dest.exists() && !part_path(&dest).exists(), "拒绝时不该落盘 ✗");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn wrong_sha256_is_rejected_and_part_removed() {
        let dir = temp_dir("download-bad-sha");
        let dest = dir.join("pkg.zip");
        let data = b"tampered payload".to_vec();
        let asset = AssetInfo {
            name: "stardust-flash-link-3.0.0.0-linux-x86_64.zip".to_string(),
            url: "https://github.com/x/y.zip".to_string(),
            size: data.len() as u64,
            sha256: Some(checksum::sha256_hex(b"the real payload")),
        };
        let err = download_and_verify(&FakeDownload::new(&data), &asset, &dest)
            .expect_err("摘要对不上必须拒绝");
        assert!(err.contains("SHA256 校验失败"), "{}", err);
        assert!(!dest.exists(), "校验失败不许发布目标文件 ✗");
        assert!(!part_path(&dest).exists(), "校验失败必须删掉 .part ✗");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn transport_failure_cleans_the_part_file() {
        let dir = temp_dir("download-fail");
        let dest = dir.join("pkg.zip");
        let data = b"partial".to_vec();
        let asset = AssetInfo {
            name: "stardust-flash-link-3.0.0.0-linux-x86_64.zip".to_string(),
            url: "https://github.com/x/y.zip".to_string(),
            size: data.len() as u64,
            sha256: Some(checksum::sha256_hex(&data)),
        };
        let fake = FakeDownload::failing("network down https://github.com/x?token=SECRET-TOKEN");
        let err = download_and_verify(&fake, &asset, &dest).expect_err("下载失败要报错");
        assert!(err.contains("下载"), "{}", err);
        assert!(!err.contains("SECRET-TOKEN"), "token 泄漏: {}", err);
        assert!(!dest.exists(), "失败不许发布目标文件 ✗");
        assert!(!part_path(&dest).exists(), "中途失败必须清理 .part ✗");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn oversize_download_aborts_and_cleans_up() {
        let dir = temp_dir("download-oversize");
        let dest = dir.join("pkg.zip");
        let data = vec![7u8; 100];
        // 声称 10 字节 → 上限 15；实际 100 字节（1.5 倍上限直接被中止 ✓）
        let asset = AssetInfo {
            name: "stardust-flash-link-3.0.0.0-linux-x86_64.zip".to_string(),
            url: "https://github.com/x/y.zip".to_string(),
            size: 10,
            sha256: Some(checksum::sha256_hex(&data)),
        };
        let err = download_and_verify(&FakeDownload::new(&data), &asset, &dest)
            .expect_err("超过 1.5 倍必须中止");
        assert!(err.contains("上限") || err.contains("超过"), "{}", err);
        assert!(!part_path(&dest).exists(), "中止后必须清理 .part ✗");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn size_mismatch_is_rejected_and_cleaned_up() {
        let dir = temp_dir("download-size-mismatch");
        let dest = dir.join("pkg.zip");
        let data = b"12345".to_vec();
        let asset = AssetInfo {
            name: "stardust-flash-link-3.0.0.0-linux-x86_64.zip".to_string(),
            url: "https://github.com/x/y.zip".to_string(),
            size: 99,
            sha256: Some(checksum::sha256_hex(&data)),
        };
        let err = download_and_verify(&FakeDownload::new(&data), &asset, &dest)
            .expect_err("体积不对必须拒绝");
        assert!(err.contains("体积不对"), "{}", err);
        assert!(!dest.exists() && !part_path(&dest).exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn report_lines_cover_every_outcome() {
        let asset = fake_asset("stardust-flash-link-3.0.0.0-linux-x86_64.zip");
        let available = UpdateCheck::Available {
            release: ReleaseInfo {
                version: Version::parse("3.0.0.0").unwrap(),
                tag: "v3.0.0.0".to_string(),
                prerelease: false,
                published_at: "2026-11-16T00:00:00Z".to_string(),
                assets: vec![asset.clone()],
            },
            asset,
        };
        let text = report_lines(&available, Channel::Release).join("\n");
        assert!(text.contains("通道: 正式版（release）"), "{}", text);
        assert!(text.contains("发现新版本 3.0.0.0"), "{}", text);
        assert!(text.contains("资产: stardust-flash-link-3.0.0.0-linux-x86_64.zip"), "{}", text);
        assert!(text.contains("大小: 10 字节"), "{}", text);
        assert!(text.contains("SHA256: 0000"), "{}", text);
        assert!(text.contains("下载: https://"), "{}", text);
        assert!(text.contains("发布时间: 2026-11-16"), "{}", text);

        let up_to_date = report_lines(&UpdateCheck::UpToDate, Channel::Preview);
        assert!(up_to_date[0].contains("预览版（preview）"), "{:?}", up_to_date);
        assert!(up_to_date[1].contains("已是最新"), "{:?}", up_to_date);

        let skipped = report_lines(&UpdateCheck::Skipped("没资产".to_string()), Channel::Preview);
        assert!(skipped[1].contains("跳过更新") && skipped[1].contains("没资产"), "{:?}", skipped);

        let error = report_lines(&UpdateCheck::Error("检查更新失败：网络不通".to_string()), Channel::Release);
        assert!(error[1].contains("检查更新失败"), "{:?}", error);

        assert_eq!(
            UpdateCheck::UpToDate.summary_cn(),
            "已是最新（这条通道上没有比你更新的版本）"
        );
        assert!(available.summary_cn().contains("发现新版本"));
        assert!(available.is_available());
    }
}
