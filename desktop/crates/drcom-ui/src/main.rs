//! 星尘闪连 3.0 桌面壳（Slint 原生控件，零 WebView）。
//!
//! 分工很硬：**界面不做任何判断** —— 读配置、跑检查、自检都在 [`model`] 里，
//! 这样 Slint 之外的逻辑都能在 `cargo test` 里验证（不用弹窗口 ✓）。
//!
//! 线程模型：网络请求是阻塞的 → 放到工作线程，结果用 `invoke_from_event_loop`
//! 回主线程更新界面（Slint 的界面属性只能在事件循环线程改 ✓）。

mod model;

use slint::{ComponentHandle, SharedString};

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

    ui.run()
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
    ui.set_data_dir_line(SharedString::from(dash.data_dir_line.as_str()));
    ui.set_config_line(SharedString::from(dash.config_line.as_str()));
    ui.set_service_line(SharedString::from(dash.service_line.as_str()));
}

/// 更新结果区（颜色 + 文本一起给，省得界面自己判断 ✓）。
fn set_result(ui: &AppWindow, kind: &str, text: &str) {
    ui.set_result_kind(SharedString::from(kind));
    ui.set_result_text(SharedString::from(text));
}
