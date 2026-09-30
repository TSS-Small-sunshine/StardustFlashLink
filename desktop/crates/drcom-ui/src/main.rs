//! 星尘闪连 3.0 桌面壳（Slint 原生控件，零 WebView）。
//!
//! 分工很硬：**界面不做任何判断** —— 读配置、跑检查、自检都在 [`model`] 里，
//! 这样 Slint 之外的逻辑都能在 `cargo test` 里验证（不用弹窗口 ✓）。
//!
//! 线程模型：网络请求是阻塞的 → 放到工作线程，结果用 `invoke_from_event_loop`
//! 回主线程更新界面（Slint 的界面属性只能在事件循环线程改 ✓）。

mod model;
mod worker;

use drcom_core::{instance, platform};
use slint::{ComponentHandle, ModelRc, SharedString, VecModel, Weak};
use std::cell::RefCell;
use std::rc::Rc;

slint::include_modules!();

/// 单实例占用的本地端口（回环地址，高位端口 ✓）。
const INSTANCE_PORT: u16 = 47653;

fn main() -> Result<(), slint::PlatformError> {
    // 单实例：已经有一个在跑时，把它的窗口叫到前面，自己安静退出 ✓
    let instance_guard = match instance::acquire(INSTANCE_PORT) {
        Ok(instance::Acquire::First(guard)) => Some(guard),
        Ok(instance::Acquire::AlreadyRunning) => {
            let reached = instance::request_focus(INSTANCE_PORT);
            println!(
                "{} 已经在运行了{}",
                drcom_core::APP_NAME,
                if reached { "，已把它的窗口叫到前面 ✓" } else { "（叫不动它，去任务栏看看 ✓）" }
            );
            return Ok(());
        }
        Err(e) => {
            // 拿不到端口也没关系：宁可多开一个，也不能不让用户用 ✗
            eprintln!("单实例检查跳过：{}", e);
            None
        }
    };

    let ui = AppWindow::new()?;
    apply(&ui, &model::collect());

    // 刷新状态
    let weak = ui.as_weak();
    ui.on_refresh(move || {
        if let Some(ui) = weak.upgrade() {
            apply(&ui, &model::collect());
            set_result(&ui, "ok", "已刷新状态 ✓");
        }
    });

    // 运行自检（很快，直接在主线程跑 ✓）
    let weak = ui.as_weak();
    ui.on_do_selfcheck(move || {
        let (kind, text) = model::run_selfcheck();
        if let Some(ui) = weak.upgrade() {
            set_result(&ui, &kind, &text);
        }
    });

    // 立即检查（会联网 → 工作线程 + 回主线程 ✓）
    let weak = ui.as_weak();
    ui.on_do_check(move || {
        let Some(ui) = weak.upgrade() else { return };
        ui.set_busy(true);
        set_result(&ui, "info", "检查中…（最多等 12 秒）");
        let weak_back = ui.as_weak();
        std::thread::spawn(move || {
            let (kind, text) = model::run_check();
            let _ = slint::invoke_from_event_loop(move || {
                if let Some(ui) = weak_back.upgrade() {
                    set_result(&ui, &kind, &text);
                    ui.set_busy(false);
                }
            });
        });
    });

    // 打开数据目录
    let weak = ui.as_weak();
    ui.on_open_data_dir(move || {
        if let Some(ui) = weak.upgrade() {
            match model::open_data_dir() {
                Ok(()) => set_result(&ui, "ok", "已打开数据目录 ✓"),
                Err(e) => set_result(&ui, "danger", &e),
            }
        }
    });

    // 设置窗口的句柄持有者（窗口必须一直有句柄，否则会被立刻释放 ✓）
    let settings_holder: Rc<RefCell<Option<SettingsWindow>>> = Rc::new(RefCell::new(None));
    let holder = settings_holder.clone();
    let weak_parent = ui.as_weak();
    ui.on_open_settings(move || {
        let Some(parent) = weak_parent.upgrade() else { return };
        open_settings(&holder, parent.as_weak());
    });

    // 主题两态：星尘风 ⇄ 深色（2.x 顶栏那个主题按钮，做成原生版 ✓）
    // 只切界面令牌，不落盘 —— 主题是纯外观，配置格式必须与 2.x 保持一致（R3 ✓）
    let weak = ui.as_weak();
    ui.on_toggle_theme(move || {
        if let Some(ui) = weak.upgrade() {
            let theme = ui.global::<Theme>();
            theme.set_dark(!theme.get_dark());
        }
    });

    // 第二个实例敲门 → 把窗口抬到前面 ✓（非阻塞轮询，不占线程池 ✗）
    let mut instance_timer: Option<slint::Timer> = None;
    if let Some(guard) = instance_guard {
        let weak = ui.as_weak();
        let timer = slint::Timer::default();
        timer.start(
            slint::TimerMode::Repeated,
            std::time::Duration::from_millis(400),
            move || {
                if !guard.poll_focus_request() {
                    return;
                }
                if let Some(ui) = weak.upgrade() {
                    let _ = ui.show(); // 再 show 一次 = 置顶 ✓
                    ui.window().request_redraw();
                }
            },
        );
        instance_timer = Some(timer); // 定时器必须活到 run() 结束（drop 即停 ✗）
    }
    let _keep_timer_alive = instance_timer;

    // —— v3.0：界面自带的后台检查（**不必装系统服务** ✓）；服务在跑时它会自动让位 ✓ ——
    let ui_weak_for_background = ui.as_weak();
    let background = worker::Worker::start();
    let background_for_ui = background.clone();
    let background_timer = slint::Timer::default();
    background_timer.start(
        slint::TimerMode::Repeated,
        std::time::Duration::from_millis(1000),
        move || {
            if let Some(ui) = ui_weak_for_background.upgrade() {
                ui.set_background_line(SharedString::from(
                    background_for_ui.snapshot().line.as_str(),
                ));
            }
        },
    );

    let result = ui.run();
    background.stop();
    drop(background_timer);
    result
}

/// 打开（或把已开着的抬到前面）设置窗口 ✓
fn open_settings(holder: &Rc<RefCell<Option<SettingsWindow>>>, parent: Weak<AppWindow>) {
    if let Some(existing) = holder.borrow().as_ref() {
        let _ = existing.show();
        return;
    }
    match SettingsWindow::new() {
        Ok(win) => {
            wire_settings(&win, holder.clone(), parent);
            apply_settings(&win, &model::settings_view());
            apply_profiles(&win, &model::profiles_view());
            apply_service(&win, &model::service_view());
            if let Err(e) = win.show() {
                eprintln!("打不开设置窗口：{}", e);
                return;
            }
            *holder.borrow_mut() = Some(win); // 句柄留着 → 窗口才活着 ✓
        }
        Err(e) => eprintln!("创建设置窗口失败：{}", e),
    }
}

/// 把视图模型写进设置窗口 ✓（密码**永不回显** ✓）
fn apply_settings(win: &SettingsWindow, view: &model::SettingsView) {
    win.set_host(SharedString::from(view.input.host.as_str()));
    win.set_port_text(SharedString::from(view.input.port_text.as_str()));
    win.set_account(SharedString::from(view.input.account.as_str()));
    win.set_suffix(SharedString::from(view.input.suffix.as_str()));
    win.set_interval_text(SharedString::from(view.input.interval_text.as_str()));
    win.set_wait_text(SharedString::from(view.input.wait_text.as_str()));
    win.set_guard_enabled(view.input.guard_enabled);
    win.set_guard_ssids(SharedString::from(view.input.guard_ssids.as_str()));
    win.set_guard_subnets(SharedString::from(view.input.guard_subnets.as_str()));
    win.set_auto_update(view.input.auto_update);
    win.set_password_text(SharedString::from(""));
    win.set_password_hint(SharedString::from(view.password_hint.as_str()));
    win.set_channel_label(SharedString::from(view.channel_label.as_str()));
    win.set_data_dir(SharedString::from(shorten_dir().as_str()));
    let choices: Vec<SharedString> = view
        .interval_choices
        .iter()
        .map(|choice| SharedString::from(choice.as_str()))
        .collect();
    win.set_interval_choices(ModelRc::new(VecModel::from(choices)));
    let (kind, text) = settings_message(view);
    win.set_message(SharedString::from(text.as_str()));
    win.set_message_kind(SharedString::from(kind.as_str()));
}

/// 校验/提示 → 设置窗口里那句话 ✓
fn settings_message(view: &model::SettingsView) -> (String, String) {
    if !view.errors.is_empty() {
        return (
            "danger".to_string(),
            format!(
                "有 {} 处要改：\n  - {}",
                view.errors.len(),
                view.errors.join("\n  - ")
            ),
        );
    }
    if !view.warnings.is_empty() {
        return (
            "warn".to_string(),
            format!("{}\n（这些只是提示，不阻断保存 ✓）", view.warnings.join("\n")),
        );
    }
    ("ok".to_string(), "看起来没问题 ✓ 改完记得点「保存」".to_string())
}

/// 从设置窗口读回表单 ✓
fn input_from(win: &SettingsWindow) -> model::FormInput {
    model::FormInput {
        host: win.get_host().to_string(),
        port_text: win.get_port_text().to_string(),
        account: win.get_account().to_string(),
        suffix: win.get_suffix().to_string(),
        interval_text: win.get_interval_text().to_string(),
        wait_text: win.get_wait_text().to_string(),
        guard_enabled: win.get_guard_enabled(),
        guard_ssids: win.get_guard_ssids().to_string(),
        guard_subnets: win.get_guard_subnets().to_string(),
        auto_update: win.get_auto_update(),
        password: win.get_password_text().to_string(),
    }
}

/// 设置窗口的按钮回调 ✓
fn wire_settings(
    win: &SettingsWindow,
    holder: Rc<RefCell<Option<SettingsWindow>>>,
    parent: Weak<AppWindow>,
) {
    let weak = win.as_weak();
    let parent_for_save = parent.clone();
    let parent_for_profile = parent.clone();
    win.on_save(move || {
        let Some(win) = weak.upgrade() else { return };
        let (kind, text) = model::settings_save(&input_from(&win));
        win.set_message(SharedString::from(text.as_str()));
        win.set_message_kind(SharedString::from(kind.as_str()));
        if kind == "ok" {
            win.set_password_text(SharedString::from("")); // 存了就清空输入框 ✓
            if let Some(parent) = parent_for_save.upgrade() {
                apply(&parent, &model::collect()); // 主界面立刻反映新配置 ✓
            }
        }
    });

    let weak = win.as_weak();
    win.on_test_connection(move || {
        let Some(win) = weak.upgrade() else { return };
        win.set_message(SharedString::from("测试中…（最多等 12 秒）"));
        win.set_message_kind(SharedString::from("info"));
        let (kind, text) = model::settings_test(&input_from(&win));
        win.set_message(SharedString::from(text.as_str()));
        win.set_message_kind(SharedString::from(kind.as_str()));
    });

    let weak = win.as_weak();
    win.on_refresh_view(move || {
        if let Some(win) = weak.upgrade() {
            apply_settings(&win, &model::settings_view());
            apply_profiles(&win, &model::profiles_view());
            apply_service(&win, &model::service_view());
        }
    });

    let weak = win.as_weak();
    win.on_open_data_dir(move || {
        if let Some(win) = weak.upgrade() {
            match model::open_data_dir() {
                Ok(()) => {
                    win.set_message(SharedString::from("已打开数据目录 ✓"));
                    win.set_message_kind(SharedString::from("ok"));
                }
                Err(e) => {
                    win.set_message(SharedString::from(e.as_str()));
                    win.set_message_kind(SharedString::from("danger"));
                }
            }
        }
    });

    // —— 方案页：存 / 应用 / 删 / 自动切换开关 ✓ ——
    let weak = win.as_weak();
    win.on_profile_save(move || {
        let Some(win) = weak.upgrade() else { return };
        let input = input_from(&win);
        let (kind, text) = model::profile_save(
            &input,
            &win.get_profile_new_name(),
            &win.get_profile_new_ssids(),
        );
        apply_profiles(&win, &model::profiles_view()); // 先刷新，再写提示（否则提示会被覆盖 ✗）
        win.set_message(SharedString::from(text.as_str()));
        win.set_message_kind(SharedString::from(kind.as_str()));
        if kind == "ok" {
            win.set_profile_new_name(SharedString::from(""));
            win.set_profile_new_ssids(SharedString::from(""));
        }
    });

    let weak = win.as_weak();
    win.on_profile_activate(move |name| {
        let Some(win) = weak.upgrade() else { return };
        let (kind, text) = model::profile_activate(name.as_str());
        // 切方案会改配置值 → 表单与列表都要跟着变 ✓
        apply_settings(&win, &model::settings_view());
        apply_profiles(&win, &model::profiles_view());
        win.set_message(SharedString::from(text.as_str()));
        win.set_message_kind(SharedString::from(kind.as_str()));
        if kind == "ok" {
            if let Some(parent) = parent_for_profile.upgrade() {
                apply(&parent, &model::collect());
            }
        }
    });

    let weak = win.as_weak();
    win.on_profile_delete(move |name| {
        let Some(win) = weak.upgrade() else { return };
        let (kind, text) = model::profile_delete(name.as_str());
        apply_profiles(&win, &model::profiles_view());
        win.set_message(SharedString::from(text.as_str()));
        win.set_message_kind(SharedString::from(kind.as_str()));
    });

    let weak = win.as_weak();
    win.on_profile_toggle_auto(move || {
        let Some(win) = weak.upgrade() else { return };
        let next = !win.get_profile_auto();
        let (kind, text) = model::profile_set_auto(next);
        apply_profiles(&win, &model::profiles_view());
        win.set_message(SharedString::from(text.as_str()));
        win.set_message_kind(SharedString::from(kind.as_str()));
    });

    let weak = win.as_weak();
    win.on_service_toggle(move || {
        let Some(win) = weak.upgrade() else { return };
        let enable = !win.get_service_installed();
        let (kind, text) = model::service_set(enable);
        apply_service(&win, &model::service_view()); // 先刷新真实状态（装没装上以系统为准 ✓）
        win.set_message(SharedString::from(text.as_str()));
        win.set_message_kind(SharedString::from(kind.as_str()));
    });

    win.on_dismiss(move || {
        // 丢掉句柄 = 关掉并释放 ✓（下次点「设置…」重建 ✓）
        let _ = holder.borrow_mut().take();
    });
}

/// 把方案页数据写进设置窗口 ✓
fn apply_profiles(win: &SettingsWindow, view: &model::ProfilesView) {
    let rows: Vec<ProfileRow> = view
        .rows
        .iter()
        .map(|row| ProfileRow {
            name: SharedString::from(row.name.as_str()),
            detail: SharedString::from(row.detail.as_str()),
            active: row.active,
        })
        .collect();
    win.set_profile_rows(ModelRc::new(VecModel::from(rows)));
    win.set_profiles_summary(SharedString::from(view.summary.as_str()));
    win.set_profile_auto(view.auto_switch);
}

/// 把「运行方式」一屏写进设置窗口 ✓
fn apply_service(win: &SettingsWindow, view: &model::ServiceView) {
    win.set_service_line(SharedString::from(view.line.as_str()));
    win.set_service_hint(SharedString::from(view.hint.as_str()));
    win.set_service_installed(view.installed);
}

/// 数据目录（路径太长就省略中间 ✓）
fn shorten_dir() -> String {
    let dir = platform::data_dir().display().to_string();
    model::shorten(&dir, 70)
}

/// 把视图模型写进界面属性。
fn apply(ui: &AppWindow, dash: &model::Dashboard) {
    ui.set_version_line(SharedString::from(dash.version_line.as_str()));
    ui.set_channel_line(SharedString::from(dash.channel_line.as_str()));
    ui.set_is_prerelease(dash.is_prerelease);
    ui.set_os_line(SharedString::from(dash.os_line.as_str()));
    ui.set_target_line(SharedString::from(dash.target_line.as_str()));
    ui.set_portable_line(SharedString::from(dash.portable_line.as_str()));
    ui.set_gateway_line(SharedString::from(dash.gateway_line.as_str()));
    ui.set_account_line(SharedString::from(dash.account_line.as_str()));
    ui.set_interval_line(SharedString::from(dash.interval_line.as_str()));
    ui.set_interval_note(SharedString::from(dash.interval_note.as_str()));
    ui.set_data_dir_line(SharedString::from(dash.data_dir_line.as_str()));
    ui.set_config_line(SharedString::from(dash.config_line.as_str()));
    ui.set_service_line(SharedString::from(dash.service_line.as_str()));
    ui.set_background_line(SharedString::from(dash.background_line.as_str()));
    // 两条统计条（2.x 状态页的「日常 / 诊断」两排）：
    // 内容全部来自上面那些字段 —— 界面层只是换个摆法，不加新判断 ✓（R7）
    // 每排只摆 3 格：Slint 布局按「内容最小宽度」兜底，格子多了会把整列顶出窗口 ✗（真踩过 ✓）
    ui.set_daily_cells(ModelRc::new(VecModel::from(vec![
        cell("运行方式", &model::shorten(&dash.service_line, 16), &model::shorten(&dash.background_line, 20)),
        cell("当前账号", &dash.account_line, "已脱敏"),
        cell("自动检查", &dash.interval_line, &dash.interval_note),
    ])));
    ui.set_diag_cells(ModelRc::new(VecModel::from(vec![
        cell("平台", &dash.os_line, &dash.target_line),
        cell("认证网关", &dash.gateway_line, "校园网 Dr.COM"),
        cell("配置", &dash.config_line, &model::shorten(&dash.data_dir_line, 22)),
    ])));
}

/// 统计条的一格 ✓（kind 留空 = 普通格；后面要标红某格时就给 "warn"/"ok" ✓）
fn cell(label: &str, value: &str, note: &str) -> StatCell {
    StatCell {
        label: SharedString::from(label),
        value: SharedString::from(value),
        note: SharedString::from(note),
        kind: SharedString::from(""),
    }
}

/// 更新结果区（颜色 + 文本 + 终端窗徽标一起给，省得界面自己判断 ✓）。
fn set_result(ui: &AppWindow, kind: &str, text: &str) {
    ui.set_result_kind(SharedString::from(kind));
    ui.set_result_text(SharedString::from(text));
    ui.set_result_badge(SharedString::from(badge_for(kind)));
}

/// 结果类型 → 终端窗右上角那枚徽标 ✓
fn badge_for(kind: &str) -> &'static str {
    match kind {
        "ok" => "成功",
        "warn" => "警告",
        "danger" => "错误",
        _ => "结果",
    }
}
