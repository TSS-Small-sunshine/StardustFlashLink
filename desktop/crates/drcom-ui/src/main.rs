//! 星尘闪连 3.0 桌面壳（Slint 原生控件，零 WebView）。
//!
//! 分工很硬：**界面不做任何判断** —— 读配置、跑检查、自检都在 [`model`] 里，
//! 这样 Slint 之外的逻辑都能在 `cargo test` 里验证（不用弹窗口 ✓）。
//!
//! 线程模型：网络请求是阻塞的 → 放到工作线程，结果用 `invoke_from_event_loop`
//! 回主线程更新界面（Slint 的界面属性只能在事件循环线程改 ✓）。

mod model;

use drcom_core::platform;
use slint::{ComponentHandle, ModelRc, SharedString, VecModel, Weak};
use std::cell::RefCell;
use std::rc::Rc;

slint::include_modules!();

fn main() -> Result<(), slint::PlatformError> {
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

    ui.run()
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

    win.on_dismiss(move || {
        // 丢掉句柄 = 关掉并释放 ✓（下次点「设置…」重建 ✓）
        let _ = holder.borrow_mut().take();
    });
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
}

/// 更新结果区（颜色 + 文本一起给，省得界面自己判断 ✓）。
fn set_result(ui: &AppWindow, kind: &str, text: &str) {
    ui.set_result_kind(SharedString::from(kind));
    ui.set_result_text(SharedString::from(text));
}
