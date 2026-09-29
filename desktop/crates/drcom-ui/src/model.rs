//! 界面与核心之间的一层「视图模型」：**界面不读文件、不碰网络**，只显示这里的字符串 ✓。
//! 好处：Slint 之外的逻辑全部能在单测里验证（`cargo test` 不弹窗口也能跑 ✓）。

use drcom_core::{
    config::Config, net::PlainHttp, password, platform, secret, session, settings::SettingsForm,
    Status,
};
use std::time::Duration;

const TIMEOUT: Duration = Duration::from_secs(12);

/// 界面要显示的整屏数据（全是「显示用字符串」，界面不做判断 ✓）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dashboard {
    pub version_line: String,
    pub channel_line: String,
    pub is_prerelease: bool,
    pub os_line: String,
    pub target_line: String,
    pub portable_line: String,
    pub gateway_line: String,
    pub account_line: String,
    pub interval_line: String,
    pub interval_note: String,
    pub data_dir_line: String,
    pub config_line: String,
    pub service_line: String,
    /// v2.1.1.0 风格：界面自带的后台检查状态（由 worker 每秒刷新 ✓）
    pub background_line: String,
}

/// 采集一次（只读 ✓；界面点「刷新」也是走它）。
pub fn collect() -> Dashboard {
    let status = Status::collect();
    let os = platform::Os::current();
    let cfg = Config::load(&platform::config_path()).unwrap_or_default();
    let errors = cfg.validate();

    Dashboard {
        version_line: status.version.clone(),
        channel_line: format!("{} · {}", status.channel_label, status.channel),
        is_prerelease: status.is_prerelease,
        os_line: os.label_cn().to_string(),
        target_line: status.target.clone(),
        portable_line: if status.portable { "便携模式".to_string() } else { "安装模式".to_string() },
        gateway_line: if cfg.host.is_empty() { "（未填）".to_string() } else { cfg.host.clone() },
        account_line: account_display(&cfg),
        interval_line: if cfg.auto_check_enabled {
            format!("每 {} 分钟", cfg.auto_check_interval_min)
        } else {
            "已关闭".to_string()
        },
        interval_note: match drcom_core::profiles::active(&cfg) {
            Some(name) => format!("方案：{}", name),
            None => "未启用方案".to_string(),
        },
        data_dir_line: shorten(&status.data_dir, 42),
        config_line: if !platform::config_path().exists() {
            "未初始化".to_string()
        } else if errors.is_empty() {
            "正常".to_string()
        } else {
            format!("{} 个问题", errors.len())
        },
        service_line: os.service_flavor().to_string(),
        // 只是初值 ✓ —— 真正的状态由 worker 线程每秒刷进界面 ✓
        background_line: "后台检查启动中…".to_string(),
    }
}

/// 读密码文件（跳过空行与 `#` 注释 ✓，与 CLI / 2.x 一致）。
pub fn read_password() -> String {
    std::fs::read_to_string(platform::password_path())
        .ok()
        .and_then(|text| {
            text.lines()
                .map(|l| l.trim().to_string())
                .find(|l| !l.is_empty() && !l.starts_with('#'))
        })
        .unwrap_or_default()
}

/// 「打开数据目录」：三平台各用系统命令（不引第三方 crate ✓）。
pub fn open_data_dir() -> Result<(), String> {
    let dir = platform::data_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("建不了目录 {}: {}", dir.display(), e))?;
    let path = dir.display().to_string();
    let result = if cfg!(target_os = "windows") {
        std::process::Command::new("explorer").arg(&path).spawn()
    } else if cfg!(target_os = "macos") {
        std::process::Command::new("open").arg(&path).spawn()
    } else {
        std::process::Command::new("xdg-open").arg(&path).spawn()
    };
    result.map(|_| ()).map_err(|e| format!("打不开文件管理器：{}", e))
}

/// 账号在界面上的显示：脱敏 + 后缀（`2023******@yd` / `2023******（校内直连）`）✓
pub fn account_display(cfg: &Config) -> String {
    if !cfg.account_configured() {
        return "未设置".to_string();
    }
    let masked = secret::MaskedAccount(&cfg.account).to_string();
    if cfg.suffix.trim().is_empty() {
        format!("{}（校内直连）", masked)
    } else {
        format!("{}{}", masked, cfg.suffix)
    }
}

/// 路径太长就把中间省掉（避免卡片里被截断得看不懂 ✓）。
pub fn shorten(text: &str, limit: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= limit {
        return text.to_string();
    }
    let head: String = chars.iter().take(limit / 2 - 1).collect();
    let tail: String = chars.iter().rev().take(limit / 2 - 2).rev().collect();
    format!("{}…{}", head, tail)
}

/// 「立即检查」：读配置 + 密码 → 跑一次 [`session::check_once`] → （颜色, 文本）✓
pub fn run_check() -> (String, String) {
    let cfg_path = platform::config_path();
    let mut cfg = match Config::load(&cfg_path) {
        Ok(cfg) => cfg,
        // 配置文件还不存在 = **首次运行的正常状态** ✓ → 给可执行的指引，而不是抛系统错误 ✗
        Err(_) if !cfg_path.exists() => {
            return (
                "warn".to_string(),
                format!(
                    "还没初始化配置（首次运行正常 ✓）\n需要先在下面这个文件里填认证网关与账号：\n{}\n字段：host（网关地址）、account（账号）、suffix（后缀）",
                    cfg_path.display()
                ),
            );
        }
        Err(e) => return ("danger".to_string(), e),
    };

    // 位置自适应：Wi-Fi 命中自动方案就切过去（**尾缀一起换** ✓，这就是「校内公共场合不带 @yd」的落地）
    let mut adapt_note = String::new();
    let ssid_now = drcom_core::probe::current_ssid();
    match drcom_core::profiles::adapt(&mut cfg, ssid_now.as_deref()) {
        Ok(Some(name)) => {
            let _ = cfg.save(&cfg_path);
            adapt_note = format!(
                "位置自适应：已切到方案「{}」（后缀 {}）\n",
                name,
                drcom_core::profiles::suffix_label(&cfg.suffix)
            );
        }
        Ok(None) => {}
        Err(e) => adapt_note = format!("位置自适应失败：{}\n", e),
    }
    let errors = cfg.validate();
    if !errors.is_empty() {
        return ("danger".to_string(), format!("配置有问题：\n  - {}", errors.join("\n  - ")));
    }
    if !cfg.account_configured() {
        return (
            "warn".to_string(),
            format!(
                "账号还没填 —— 请在 {} 里写 account\n（密码写到 {}，一行密码，`#` 开头是注释）",
                cfg_path.display(),
                platform::password_path().display()
            ),
        );
    }
    let password = read_password();
    if password.is_empty() {
        return (
            "danger".to_string(),
            format!(
                "密码文件是空的：{}\n（一行密码；`#` 开头的行会被跳过）",
                platform::password_path().display()
            ),
        );
    }

    // 网络位置守卫：不在校园网就不白跑一次认证 ✓（读不到 Wi-Fi/IP 时 fail-open ✓）
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(&cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        return (
            "warn".to_string(),
            format!(
                "{}{}\n（当前 Wi-Fi: {} · 本机 IP: {}）\n—— 去「设置」里把白名单改对，或关掉网络位置守卫 ✓",
                adapt_note,
                verdict.reason,
                probe.ssid.clone().unwrap_or_else(|| "读不到".to_string()),
                if probe.ips.is_empty() { "读不到".to_string() } else { probe.ips.join(", ") }
            ),
        );
    }

    let report = session::check_once(&cfg, &password, &PlainHttp, TIMEOUT);
    let kind = if report.outcome.is_ok() { "ok" } else { "danger" };
    (
        kind.to_string(),
        format!(
            "{}{}\n（在线探测: {:?} · 回调: {}）\n—— 3.0 预览版目前只做「一次检查」，常驻后台在 M3 ✓",
            adapt_note,
            report.outcome.summary_cn(),
            report.online,
            report.callback
        ),
    )
}

/// 「无人登录也自动登录」这一项要显示的数据 ✓
/// （语义 = **注册成系统服务** ✓：用户级自启要等有人登录才跑 ✗，只有服务是开机即起 ✓）
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceView {
    /// 是否已安装（驱动开关 ✓）
    pub installed: bool,
    /// 一行状态 ✓
    pub line: String,
    /// 代价与前提（要管理员 / 要 nssm / 与 2.x 同名 ✓）
    pub hint: String,
}

/// 纯函数：服务状态 → 一行中文 ✓（好测 ✓，不碰系统 ✓）。
pub fn service_line(state: &drcom_core::service::ServiceState) -> String {
    match state {
        drcom_core::service::ServiceState::Running => "已注册，正在运行 ✓".to_string(),
        drcom_core::service::ServiceState::Stopped => "已注册，但没在跑".to_string(),
        drcom_core::service::ServiceState::NotInstalled => "未注册（只在有人登录后运行）".to_string(),
        drcom_core::service::ServiceState::Unknown(reason) => {
            format!("查不出来：{}", reason)
        }
    }
}

/// 读一次（只读 ✓；点「重新读取」也走它 ✓）。
pub fn service_view() -> ServiceView {
    let state = drcom_core::service::status();
    let installed = !matches!(state, drcom_core::service::ServiceState::NotInstalled);
    let mut hint = format!(
        "开启后：由 {} 托管，**开机即起、无人登录也在检查** ✓；\
         代价是注册时需要管理员权限，Windows 上还需要程序目录里有 nssm.exe ✓",
        drcom_core::service::flavor_name()
    );
    if !drcom_core::service::manager_available() {
        hint.push_str(&format!(
            "\n⚠ 现在找不到 {} ✗ —— 注册会失败，可以先用 `autostart on`（登录后自动跑 ✓）",
            drcom_core::service::manager_program()
        ));
    }
    if installed && cfg!(target_os = "windows") {
        hint.push_str("\n⚠ 服务名与 2.x 共用（DrcomAutoLogin）✓ —— 已存在时命令会给「接管」步骤，不会硬装 ✗");
    }
    ServiceView {
        installed,
        line: service_line(&state),
        hint,
    }
}

/// 开关动作 ✓：装 = 注册并启动 ✓；关 = 停止并注销 ✓。
pub fn service_set(enabled: bool) -> (String, String) {
    let exe = std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("stardust-flash-link"));
    let result = if enabled {
        drcom_core::service::install(&exe, &["run"])
    } else {
        drcom_core::service::uninstall()
    };
    match result {
        Ok(message) => (
            "ok".to_string(),
            format!(
                "{}\n（{} 现在会 {} 自己检查 ✓）",
                message,
                drcom_core::APP_NAME,
                if enabled { "交给服务" } else { "由界面自己" }
            ),
        ),
        Err(e) => ("danger".to_string(), e),
    }
}

/// 设置窗口收集到的原始输入（全是界面上的字符串/开关 ✓，解析与校验在下面 ✓）。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct FormInput {
    pub host: String,
    pub port_text: String,
    pub account: String,
    pub suffix: String,
    pub interval_text: String,
    pub wait_text: String,
    pub guard_enabled: bool,
    pub guard_ssids: String,
    pub guard_subnets: String,
    pub auto_update: bool,
    pub password: String,
}

impl FormInput {
    /// 从现有配置生成默认输入（**密码留空** ✓）
    pub fn from_config(cfg: &Config) -> FormInput {
        FormInput {
            host: cfg.host.clone(),
            port_text: cfg.port.to_string(),
            account: cfg.account.clone(),
            suffix: cfg.suffix.clone(),
            interval_text: cfg.auto_check_interval_min.to_string(),
            wait_text: cfg.network_wait_timeout_sec.to_string(),
            guard_enabled: cfg.network_guard_enabled,
            guard_ssids: cfg.guard_allowed_ssids.clone(),
            guard_subnets: cfg.guard_allowed_subnets.clone(),
            auto_update: cfg.auto_update_enabled,
            password: String::new(),
        }
    }

    /// 变成可保存的表单：数字字段解析失败就**回落到原配置的值**（不静默改成 0 ✗）
    pub fn to_form(&self, base: &Config) -> SettingsForm {
        let mut form = SettingsForm::from_config(base);
        form.host = self.host.clone();
        form.port = self.port_text.trim().parse::<u16>().unwrap_or(base.port);
        form.account = self.account.clone();
        form.suffix = self.suffix.clone();
        form.interval_min = self
            .interval_text
            .trim()
            .parse::<u32>()
            .unwrap_or(base.auto_check_interval_min);
        form.wait_timeout_sec = self
            .wait_text
            .trim()
            .parse::<u32>()
            .unwrap_or(base.network_wait_timeout_sec);
        form.guard_enabled = self.guard_enabled;
        form.guard_ssids = self.guard_ssids.clone();
        form.guard_subnets = self.guard_subnets.clone();
        form.auto_update_enabled = self.auto_update;
        form.password = Some(self.password.clone()); // 空串 = 不修改 ✓（由 SettingsForm 判定 ✓）
        form
    }
}

/// 设置窗口要显示的一屏数据。
#[derive(Debug, Clone, PartialEq)]
pub struct SettingsView {
    pub input: FormInput,
    pub interval_choices: Vec<String>,
    pub channel_label: String,
    pub password_hint: String,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}

/// 「测试连接」：用**表单里的值**跑一次检查（**不保存** ✓）。
pub fn settings_test(input: &FormInput) -> (String, String) {
    let config_path = platform::config_path();
    let base = Config::load(&config_path).unwrap_or_default();
    let form = input.to_form(&base);
    let errors = form.validate(&base);
    if !errors.is_empty() {
        return (
            "danger".to_string(),
            format!("配置有问题（已阻止保存）：\n  - {}", errors.join("\n  - ")),
        );
    }
    let cfg = form.to_config(&base);
    let typed_password = form.new_password().map(|v| v.to_string());
    let password = match &typed_password {
        Some(value) => value.clone(),
        None => password::read(),
    };
    if !cfg.account_configured() {
        return ("warn".to_string(), "账号还没填 —— 先填上学号再测 ✓".to_string());
    }
    if password.is_empty() {
        return ("warn".to_string(), "还没有密码：在「密码」里填一个（或先保存）✓".to_string());
    }
    let probe = drcom_core::probe::snapshot();
    let verdict = drcom_core::guard_allows(&cfg, probe.ssid.as_deref(), &probe.ips);
    if !verdict.allowed {
        return (
            "warn".to_string(),
            format!(
                "网络位置守卫会拦下这次检查：{}\n（测试用的是表单里的白名单，**尚未保存** ✓）",
                verdict.reason
            ),
        );
    }
    let report = session::check_once(&cfg, &password, &PlainHttp, TIMEOUT);
    let kind = if report.outcome.is_ok() { "ok" } else { "danger" };
    (
        kind.to_string(),
        format!("{}（测试用的是表单里的值，**尚未保存** ✓）", report.outcome.summary_cn()),
    )
}

/// 读配置 → 设置窗口的显示数据 ✓（只读 ✓）
pub fn settings_view() -> SettingsView {
    let cfg = Config::load(&platform::config_path()).unwrap_or_default();
    let input = FormInput::from_config(&cfg);
    let form = input.to_form(&cfg);
    let has_password = drcom_core::password::is_set();
    SettingsView {
        interval_choices: drcom_core::settings::interval_choices()
            .iter()
            .map(|v| v.to_string())
            .collect(),
        channel_label: format!(
            "{} · {}",
            cfg.update_channel.as_str(),
            cfg.update_channel.label_cn()
        ),
        password_hint: if has_password {
            "已设置（留空 = 不修改）".to_string()
        } else {
            "还没设置（填一个并保存即可）".to_string()
        },
        errors: form.validate(&cfg),
        warnings: form.warnings(&cfg),
        input,
    }
}

/// 保存设置（界面点「保存」）→（颜色, 文案）✓
pub fn settings_save(input: &FormInput) -> (String, String) {
    let config_path = platform::config_path();
    let base = Config::load(&config_path).unwrap_or_default();
    let form = input.to_form(&base);
    match form.save_to(&base, &config_path, &drcom_core::password::path()) {
        Ok(()) => (
            "ok".to_string(),
            "已保存 ✓（守护进程与界面都会立刻用新配置；改密码不需要重启服务）".to_string(),
        ),
        Err(e) => ("danger".to_string(), format!("保存失败：{}", e)),
    }
}
/// 方案列表里的一行（给界面用 ✓）。
#[derive(Debug, Clone, PartialEq)]
pub struct ProfileRow {
    pub name: String,
    /// 一行说明（后缀 + 匹配几个 Wi-Fi ✓）
    pub detail: String,
    /// 是不是当前正在用的方案 ✓
    pub active: bool,
}

/// 方案页要显示的一屏数据 ✓。
#[derive(Debug, Clone, PartialEq)]
pub struct ProfilesView {
    pub auto_switch: bool,
    pub active: String,
    pub rows: Vec<ProfileRow>,
    /// 一句话说清现在是什么状态 / 该干什么 ✓
    pub summary: String,
}

/// 读配置 → 方案页数据 ✓（只读 ✓）
pub fn profiles_view() -> ProfilesView {
    let cfg = Config::load(&platform::config_path()).unwrap_or_default();
    profiles_view_of(&cfg)
}

/// 从给定配置生成方案页数据（**纯函数** → 单测能覆盖 ✓）。
pub fn profiles_view_of(cfg: &Config) -> ProfilesView {
    let active = drcom_core::profiles::active(cfg).unwrap_or("").to_string();
    let rows: Vec<ProfileRow> = drcom_core::profiles::list(cfg)
        .into_iter()
        .map(|name| ProfileRow {
            detail: drcom_core::profiles::describe(cfg, &name),
            active: name == active,
            name,
        })
        .collect();
    let summary = if rows.is_empty() {
        "还没有方案 —— 把上面改成某个位置的设置（校内公共场合留空后缀、宿舍填 @yd），\
         再起个名字存下来 ✓"
            .to_string()
    } else if cfg.profiles_auto_switch {
        format!("共 {} 个方案 · 自动切换已开：走到命中 Wi-Fi 就自动切 ✓", rows.len())
    } else {
        format!("共 {} 个方案 · 自动切换关着（只能手动点「应用」✓）", rows.len())
    };
    ProfilesView {
        auto_switch: cfg.profiles_auto_switch,
        active,
        rows,
        summary,
    }
}

/// 方案名校验（纯函数 ✓）→ `Err` 是给用户看的一句中文 ✓
fn profile_name_ready(name: &str) -> Result<String, String> {
    let normalized = drcom_core::profiles::normalize_name(name);
    if normalized.is_empty() {
        return Err(format!(
            "方案名不合法 ✗（不能为空、不超过 {} 字，且不能含路径字符）",
            drcom_core::profiles::MAX_NAME_LEN
        ));
    }
    Ok(normalized)
}

/// 把**当前表单里的值**存成一个新方案（名字 + 匹配 Wi-Fi 名来自界面 ✓）。
pub fn profile_save(input: &FormInput, name: &str, match_ssids: &str) -> (String, String) {
    let config_path = platform::config_path();
    let base = Config::load(&config_path).unwrap_or_default();
    let normalized = match profile_name_ready(name) {
        Ok(value) => value,
        Err(e) => return ("danger".to_string(), e),
    };
    // 表单必须先过关：半套配置绝不落盘 ✗
    let form = input.to_form(&base);
    let errors = form.validate(&base);
    if !errors.is_empty() {
        return (
            "danger".to_string(),
            format!("先把上面的配置改对再存方案：\n  - {}", errors.join("\n  - ")),
        );
    }
    let mut cfg = form.to_config(&base);
    let ssids: Vec<String> = match_ssids
        .split(',')
        .map(|part| part.trim().to_string())
        .filter(|part| !part.is_empty())
        .collect();
    match drcom_core::profiles::save_profile(&mut cfg, &normalized, &ssids) {
        Ok(msg) => match cfg.save(&config_path) {
            Ok(()) => (
                "ok".to_string(),
                format!("{}（值取的就是当前表单里的设置 ✓）", msg),
            ),
            Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
        },
        Err(e) => ("danger".to_string(), e),
    }
}

/// 应用（切到）某个方案 ✓
pub fn profile_activate(name: &str) -> (String, String) {
    let config_path = platform::config_path();
    let mut cfg = match Config::load(&config_path) {
        Ok(cfg) => cfg,
        Err(e) => return ("danger".to_string(), e),
    };
    match drcom_core::profiles::activate(&mut cfg, name) {
        Ok(()) => match cfg.save(&config_path) {
            Ok(()) => (
                "ok".to_string(),
                format!(
                    "已切到方案「{}」：后缀 {} ✓（表单已同步刷新 ✓）",
                    name,
                    drcom_core::profiles::suffix_label(&cfg.suffix)
                ),
            ),
            Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
        },
        Err(e) => ("danger".to_string(), e),
    }
}

/// 删除某个方案 ✓（删「当前方案」只清标记，**配置值不动** ✓）
pub fn profile_delete(name: &str) -> (String, String) {
    let config_path = platform::config_path();
    let mut cfg = match Config::load(&config_path) {
        Ok(cfg) => cfg,
        Err(e) => return ("danger".to_string(), e),
    };
    match drcom_core::profiles::delete(&mut cfg, name) {
        Ok(()) => match cfg.save(&config_path) {
            Ok(()) => ("ok".to_string(), format!("已删除方案「{}」✓", name)),
            Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
        },
        Err(e) => ("danger".to_string(), e),
    }
}

/// 打开 / 关闭「按 Wi-Fi 自动切换」✓
pub fn profile_set_auto(enabled: bool) -> (String, String) {
    let config_path = platform::config_path();
    let mut cfg = match Config::load(&config_path) {
        Ok(cfg) => cfg,
        Err(e) => return ("danger".to_string(), e),
    };
    cfg.profiles_auto_switch = enabled;
    match cfg.save(&config_path) {
        Ok(()) => (
            "ok".to_string(),
            if enabled {
                "自动切换已开启 ✓：走进命中方案的 Wi-Fi 就自动切（读不到 Wi-Fi 名时保持不动 ✓）"
                    .to_string()
            } else {
                "自动切换已关闭 ✓：方案只在点「应用」时生效".to_string()
            },
        ),
        Err(e) => ("danger".to_string(), format!("写入配置失败：{}", e)),
    }
}

pub fn run_selfcheck() -> (String, String) {
    let version = drcom_core::app_version_string();
    let version_ok = drcom_core::channel::Version::parse(&version).is_some();
    let prerelease = drcom_core::app_version().channel.is_prerelease();
    let cfg_errors = Config::default().validate();
    let sample = "dr1({\"result\":1,\"msg\":\"ok\"});";
    let jsonp_ok = matches!(drcom_core::protocol::parse_login_reply(sample), Ok(r) if r.success);
    let data_ok = std::fs::create_dir_all(platform::data_dir()).is_ok();
    let conf_ok = std::fs::create_dir_all(platform::config_dir()).is_ok();

    let mut lines: Vec<String> = Vec::new();
    let mut failed = 0usize;
    {
        let mut push = |name: &str, ok: bool, detail: String| {
            if ok {
                lines.push(format!("PASS  {}", name));
            } else {
                failed += 1;
                lines.push(format!("FAIL  {}  {}", name, detail));
            }
        };
        push("版本串可解析", version_ok, version.clone());
        push("开发期走预览通道（正式用户收不到 ✓）", prerelease, "release".to_string());
        push("默认配置校验通过", cfg_errors.is_empty(), cfg_errors.join("; "));
        push("JSONP 解析正常", jsonp_ok, sample.to_string());
        push("数据目录可写", data_ok, platform::data_dir().display().to_string());
        push("配置目录可写", conf_ok, platform::config_dir().display().to_string());
    }

    let kind = if failed == 0 { "ok" } else { "danger" };
    (kind.to_string(), format!("{}\n\n结果：{} 项失败", lines.join("\n"), failed))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dashboard_collects_without_panicking() {
        let dash = collect();
        assert!(dash.version_line.starts_with("3.0.0.0"), "{}", dash.version_line);
        assert!(dash.is_prerelease, "开发期必须是预览版 ✓");
        assert!(!dash.os_line.is_empty());
        assert!(!dash.target_line.is_empty());
        assert!(dash.channel_line.contains("预览版"), "{}", dash.channel_line);
        assert!(dash.service_line.len() > 3);
    }

    #[test]
    fn account_is_masked_on_screen() {
        // 界面上永远看不到完整账号 ✓
        assert_eq!(secret::MaskedAccount("2023001234").to_string(), "2023******");
    }

    #[test]
    fn account_display_shows_the_suffix_that_will_be_used() {
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        cfg.suffix = "@yd".to_string();
        assert_eq!(account_display(&cfg), "2023******@yd", "宿舍移动：带尾缀 ✓");
        cfg.suffix = String::new();
        assert_eq!(account_display(&cfg), "2023******（校内直连）", "校内公共场合：无尾缀 ✓");
        cfg.account = String::new();
        assert_eq!(account_display(&cfg), "未设置");
        // 任何情况下都不该出现完整账号 ✗
        let mut cfg = Config::default();
        cfg.account = "2023001234".to_string();
        assert!(!account_display(&cfg).contains("2023001234"));
    }

    #[test]
    fn shorten_keeps_both_ends() {
        let long = "C:/very/long/path/that/keeps/going/and/going/data";
        let short = shorten(long, 24);
        assert!(short.chars().count() <= 24, "{}", short);
        assert!(short.contains('…'));
        assert!(short.starts_with("C:/very"));
        assert_eq!(shorten("short", 40), "short");
    }

    #[test]
    fn selfcheck_is_green_on_a_clean_machine() {
        let (kind, text) = run_selfcheck();
        assert_eq!(kind, "ok", "自检必须全绿：\n{}", text);
        assert!(text.contains("结果：0 项失败"), "{}", text);
        assert!(text.contains("PASS"), "{}", text);
    }

    #[test]
    fn check_never_panics_and_gives_actionable_text() {
        // 开发机上通常没配账号/密码 → 应给可操作的提示，而不是崩溃 ✓
        let (kind, text) = run_check();
        assert!(["ok", "warn", "danger"].contains(&kind.as_str()), "kind={}", kind);
        assert!(!text.is_empty());
        assert!(
            text.contains("账号") || text.contains("密码") || text.contains("登录") || text.contains("在线"),
            "提示要能指导用户: {}",
            text
        );
    }

    #[test]
    fn form_input_round_trips_and_falls_back_on_bad_numbers() {
        let mut cfg = Config::default();
        cfg.host = "172.16.80.3".to_string();
        cfg.account = "2023001234".to_string();
        cfg.suffix = "@yd".to_string();
        let input = FormInput::from_config(&cfg);
        assert_eq!(input.password, "", "密码默认空 = 不修改 ✓");
        let form = input.to_form(&cfg);
        assert_eq!(form.host, cfg.host);
        assert_eq!(form.interval_min, cfg.auto_check_interval_min);
        assert!(form.new_password().is_none(), "空密码 = 不修改 ✓");

        // 数字框乱填 → **回落到原值**（不能静默变 0 ✗）
        let mut bad = input.clone();
        bad.port_text = "abc".to_string();
        bad.interval_text = String::new();
        bad.wait_text = "-5".to_string();
        let form = bad.to_form(&cfg);
        assert_eq!(form.port, cfg.port, "解析失败要保留原值 ✓");
        assert_eq!(form.interval_min, cfg.auto_check_interval_min);
        assert_eq!(form.wait_timeout_sec, cfg.network_wait_timeout_sec);

        // 给了密码 → 会被写 ✓
        let mut with_password = input.clone();
        with_password.password = "pw".to_string();
        assert_eq!(with_password.to_form(&cfg).new_password(), Some("pw"));
    }

    #[test]
    fn service_lines_are_readable_and_never_lie() {
        use drcom_core::service::ServiceState;
        assert!(service_line(&ServiceState::Running).contains("正在运行"));
        assert!(service_line(&ServiceState::Stopped).contains("没在跑"));
        assert!(service_line(&ServiceState::NotInstalled).contains("未注册"));
        assert!(service_line(&ServiceState::Unknown("探测失败".into())).contains("探测失败"));
        // 真机只读取一次：字段非空，且**代价必须说清**（要管理员 ✓）
        let view = service_view();
        assert!(!view.line.is_empty() && !view.hint.is_empty());
        assert!(view.hint.contains("管理员"), "代价要讲明白 ✓: {}", view.hint);
        assert!(view.hint.contains("无人登录") || view.hint.contains("开机即起"), "{}", view.hint);
    }

    #[test]
    fn settings_view_has_choices_and_readable_hints() {
        let view = settings_view();
        assert!(!view.interval_choices.is_empty());
        assert!(view.interval_choices.contains(&"30".to_string()), "{:?}", view.interval_choices);
        assert!(!view.channel_label.is_empty());
        assert!(view.password_hint.contains("密码") || view.password_hint.contains("设置"));
        assert!(view.errors.len() < 5, "默认配置不该一堆错：{:?}", view.errors);
    }

    #[test]
    fn settings_test_refuses_incomplete_input_without_touching_network() {
        // 账号为空 → 直接提示（**不会发任何请求** ✓）
        let mut input = FormInput::default();
        input.host = "172.16.80.3".to_string();
        input.interval_text = "30".to_string();
        input.wait_text = "60".to_string();
        let (kind, text) = settings_test(&input);
        assert_eq!(kind, "warn", "{}", text);
        assert!(text.contains("账号"), "{}", text);

        // 非法后缀 → danger，且说明「已阻止保存」✓
        input.account = "2023001234".to_string();
        input.suffix = "@xx".to_string();
        let (kind, text) = settings_test(&input);
        assert_eq!(kind, "danger");
        assert!(text.contains("配置有问题"), "{}", text);
    }

    #[test]
    fn profiles_view_is_pure_and_readable() {
        let mut cfg = Config::default();
        let view = profiles_view_of(&cfg);
        assert!(view.rows.is_empty());
        assert!(view.summary.contains("还没有方案"), "{}", view.summary);

        cfg.suffix = "@yd".to_string();
        drcom_core::profiles::save_profile(&mut cfg, "宿舍", &["Dorm-WiFi".to_string()]).unwrap();
        let view = profiles_view_of(&cfg);
        assert_eq!(view.rows.len(), 1);
        assert_eq!(view.rows[0].name, "宿舍");
        assert!(view.rows[0].detail.contains("@yd"), "{}", view.rows[0].detail);
        assert!(!view.rows[0].active, "刚存下的方案不会自动成为「使用中」✓");
        assert!(view.summary.contains("自动切换关着"), "{}", view.summary);

        drcom_core::profiles::activate(&mut cfg, "宿舍").unwrap();
        let view = profiles_view_of(&cfg);
        assert_eq!(view.active, "宿舍");
        assert!(view.rows[0].active, "切过去之后要标「使用中」✓");
        assert!(view.summary.contains("共 1 个方案"), "{}", view.summary);
    }

    #[test]
    fn profile_name_validation_rejects_junk() {
        assert_eq!(profile_name_ready("  宿舍  ").unwrap(), "宿舍", "首尾空白要压掉 ✓");
        assert!(profile_name_ready("").is_err(), "空名字不行 ✗");
        assert!(profile_name_ready("   ").is_err());
        assert!(profile_name_ready("a/b").is_err(), "路径字符会搞坏配置 ✗");
        assert!(profile_name_ready("长".repeat(25).as_str()).is_err(), "超长要拦 ✗");
        assert!(profile_name_ready("长".repeat(24).as_str()).is_ok());
    }

    #[test]
    fn profile_save_refuses_junk_without_touching_disk() {
        // 后缀非法 → 直接拒绝（**不会走到写盘** ✓）
        let mut input = FormInput::default();
        input.host = "172.16.80.3".to_string();
        input.port_text = "80".to_string();
        input.interval_text = "30".to_string();
        input.wait_text = "60".to_string();
        input.suffix = "@xx".to_string();
        let (kind, text) = profile_save(&input, "坏方案", "");
        assert_eq!(kind, "danger");
        assert!(text.contains("先把上面的配置改对"), "{}", text);

        // 名字不合法 → 更早拒绝 ✓
        input.suffix = "@yd".to_string();
        let (kind, text) = profile_save(&input, "  ", "");
        assert_eq!(kind, "danger");
        assert!(text.contains("方案名"), "{}", text);
    }
}

