fn main() {
    // 编译 Slint 界面：语法错误会在编译期暴露（比运行期白屏好得多 ✓）
    slint_build::compile("ui/main.slint").expect("Slint 界面编译失败");
    println!("cargo:rerun-if-changed=ui/main.slint");
    println!("cargo:rerun-if-changed=ui/theme.slint");
    println!("cargo:rerun-if-changed=ui/settings.slint");
}
