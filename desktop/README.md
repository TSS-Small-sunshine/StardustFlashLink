# desktop/ —— 3.0 跨平台核心（Rust，代号 Altair 牛郎星）

> 目标见 [`../docs/PLATFORMS.md`](../docs/PLATFORMS.md)：**一份核心，多端可用**
> （Windows / Linux AMD64·ARMv8·ARMv7 / macOS Intel·Apple Silicon），
> 并且**不依赖浏览器** —— GUI 外壳在 M2 接到这个核心上。

## 现在的状态（M0 完成 · M2 起步）

| 项 | 值 |
| --- | --- |
| 单元测试 | **43 项全绿**（核心 28 + 可执行 9 + 界面 6） |
| headless 可执行 | **0.32 MB**（单文件，无运行时依赖 ✓） |
| Slint GUI | **7.83 MB**（Windows x86_64；零 WebView，真机可启动 ✓） |
| 依赖 | 核心只 2 个（`serde`/`serde_json`）；GUI 另加 Slint（软件渲染器 ✓） |
| 覆盖平台 | 交叉编译矩阵见 `.github/workflows/preview-3.0.yml`（6 目标；GUI 在原生 runner 上编译） |

## 本地怎么跑

```bash
cd desktop
cargo test --workspace          # 全部单测
cargo run -p drcom-cli -- version      # 版本 / 通道 / 平台 / 架构
cargo run -p drcom-cli -- selfcheck    # 内置自检（行为级，和 2.x 的 _smoke_*.py 一个思路）
cargo run -p drcom-cli -- status       # 状态 JSON（与本地 API 同一份数据）
cargo run -p drcom-cli -- serve        # 起本地控制 API（http://127.0.0.1:8848）
cargo run -p drcom-cli -- login --dry-run   # 只打印「脱敏后的」登录 URL ✓
```

## 目录

```
crates/drcom-core/     平台无关核心（无 UI、无平台框架）
  channel.rs           版本 + 发行通道（snapshot/preview/rc/release）
  config.rs            配置（键名与 2.x 逐字对齐，未知字段原样保留）
  protocol.rs          Dr.COM 协议：拼 URL / 解析 JSONP（纯函数）
  secret.rs            凭据脱敏（2.1.0.0 P7-2 的 Rust 版）
  platform.rs          数据/配置/日志目录、服务方式、便携模式判定
crates/drcom-cli/      无界面可执行（headless 优先：Linux 服务器/树莓派要它 ✓）
  http.rs              标准库 HTTP 客户端（明文 http，够用且小）
  server.rs            本地控制 API（只绑回环 + 安全头 + 1 MB 上限 + 有界排空）
```

## 硬规则（改代码前先读）

1. **核心不 import 任何 UI / 服务框架** —— 这样才能既跑在桌面 App 里，也跑在
   `systemd --user`（Linux）与 launchd（macOS）下 ✓；
2. **密码绝不进日志**：任何可能打印的字符串先过 `secret::scrub_url` ✓（有测试守着）；
3. **配置与 API 契约向后兼容**：3.0 直接读 2.x 的 `config.json` / `password.txt` ✓；
4. **只绑 127.0.0.1**：本地 API 永远不对外 ✓；
5. **开发期不升正式通道**：`APP_CHANNEL` 保持 `Preview`，等便携版成型再谈 ✓。
