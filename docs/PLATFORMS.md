# 3.0 跨平台蓝图（Altair 牛郎星）

> 目标：**一份核心，多端可用** —— Windows / Linux / macOS 全覆盖，
> 不再依赖「打开浏览器访问 127.0.0.1」这种形态，而是**真·桌面软件**
> （托盘 / 独立窗口 / 自启 / 后台守护），并为**便携版**（免安装单文件）打基础。
>
> 状态：**开发线（`dev/3.0-altair`）**，只发**预览版**；正式版等便携版成型之后再谈。

---

## 1. 平台 × 架构矩阵（硬要求）

| 平台 | 架构 | 目标三元组 | 备注 |
| --- | --- | --- | --- |
| Windows | x86_64 | `x86_64-pc-windows-msvc` | 主战场，已有 2.1.x（Python）可平滑过渡 |
| Windows | ARM64 | `aarch64-pc-windows-msvc` | Surface 等 ARM 设备 |
| Linux | AMD64 | `x86_64-unknown-linux-gnu` | 发行版主力 |
| Linux | ARMv8 (aarch64) | `aarch64-unknown-linux-gnu` | 树莓派 4/5、ARM 服务器 |
| Linux | **ARMv7** | `armv7-unknown-linux-gnueabihf` | 树莓派 2/3/Zero 2、老路由/工控 |
| macOS | Apple Silicon | `aarch64-apple-darwin` | M 系列 |
| macOS | Intel | `x86_64-apple-darwin` | 老机器 |

**打包形态**
- Windows：`.exe` 安装器（Inno Setup，沿用现有）+ **便携版单文件**（后期）
- Linux：`AppImage`（免安装、可当便携版）+ `.deb` / `.rpm`
- macOS：`.app` + `.dmg`（Universal Binary 同时含 Intel + M 系列）

---

## 2. 技术选型

### 核心（core）：**Rust**（`desktop/` 工作区）
理由：单一静态二进制 / 无运行时依赖 / 内存占用小（典型 < 20 MB）/ 交叉编译到上面 7 个目标都有成熟路线
（`cargo-zigbuild`、`cross`）。相比 Python 版：便携版不再需要解释器，冷启动从秒级降到毫秒级。

### 外壳（UI）：**已定：Slint 原生控件（零 WebView）** ✅

| 方案 | 体积 | 内存 | UI 工作量 | 依赖 | 结论 |
| --- | --- | --- | --- | --- | --- |
| Tauri 2（系统 WebView + 复用现有 HTML） | ≈5–10 MB | 中 | 低（现有 UI 直接搬） | Linux 需 `webkit2gtk-4.1`；Windows 需 WebView2 | ✗ 未选 |
| **Slint（原生控件，零 WebView）** | **≈3–5 MB** | **低** | 中（UI 要重写） | 无（X11/Wayland 即可） | ✅ **选它** |

**为什么选 Slint**（2026-09-28 决策）：
1. 「便携版」要求**零系统依赖**：AppImage / 单文件拖到哪都能跑，不想依赖 webkit2gtk 这类运行时 ✓；
2. 「占用小」：没有 WebView 进程，常驻内存少一大截 ✓；
3. 用户明确「**不要那么依赖浏览器**」—— Slint 是原生渲染，跟浏览器彻底无关 ✓；
4. 代价：现有 HTML UI 要用 Slint 重写一遍（设计语言不变：深色星尘渐变、玻璃卡片、
   `#0071e3` 主色、`#2AA8FF → #3DDC97` 渐变；设计稿见 `_ui_redesign/` ✓）。

> 顺带一个好处：`desktop/crates/drcom-cli`（headless）仍然独立可用 ——
> Linux 服务器 / 树莓派上不需要图形栈就能跑 ✓。

---

## 3. 分层与目录（本仓库内）

```
DrcomAutoLogin-Windows/            # 仓库名沿用（历史原因），3.0 起是跨平台项目
├─ desktop/                        # ★ 3.0 新代码（Rust 工作区）
│   ├─ crates/drcom-core/          # 平台无关核心：配置 / 协议 / 通道 / 路径 / 脱敏
│   └─ crates/drcom-cli/           # 无界面可执行：serve（本地 API）/ login / version
│   ├─ ui/                         # （M2）桌面 UI：Tauri 的 HTML 资源或 Slint 界面
│   └─ packaging/                  # （M3）各平台打包脚本
├─ *.py                            # 2.1.x（Vega）Windows 稳定线：**只修 bug**
├─ packaging/                      # 现有 Inno Setup（2.1.x 与 3.0 Windows 安装器共用思路）
└─ .github/workflows/              # 2.1.x 走 main；3.0 预览走 dev/3.0-altair
```

**控制面（本地 API）**：3.0 继续提供 `127.0.0.1` 上的 HTTP JSON API，**路由与字段与 2.x 保持一致**，
这样：① 现有 `_smoke_http.py`（27 项）能直接当黑盒验收；② 现有 HTML UI 与 Android 端可继续复用。

---

## 4. 里程碑

| 阶段 | 内容 | 产出 |
| --- | --- | --- |
| **M0** ✅ | Rust 工作区 + 核心纯函数（配置/协议/通道/路径/脱敏）+ 本地 API 雏形 + 单元测试 | **29 项测试全绿**；release 单文件 **0.32 MB**；CI 矩阵覆盖 6 个目标 |
| **M1** | 完整核心：登录/在线探测/**位置守卫**/方案/退避/日志轮转/指标/诊断包 | 与 2.x 行为对齐（`_smoke_*` 通过） |
| **M2** | 桌面壳：托盘/窗口/单实例/自启/深色主题；UI 迁移 | 三平台可运行 App |
| **M3** | 平台守护：Windows 服务(NSSM) / **systemd --user** / **launchd LaunchAgent**；打包矩阵 | 各平台安装包 + AppImage/dmg |
| **M4** | 自动更新（通道感知）+ **便携版单文件** | 预览版发布 |

### M0 实测（2026-09-28）

| 指标 | 结果 | 说明 |
| --- | --- | --- |
| 单元测试 | **29/29 通过** | 核心 18 + 可执行 11（含本地 API 的 5 项行为级：健康检查/状态/404/405/413） |
| release 体积 | **0.32 MB** | Windows x86_64；对比 Electron ≈150 MB、Python 方案 ≈40 MB ✓ |
| release 构建耗时 | 13.2 s | 增量；CI 上会慢一些 |
| 依赖数 | **2** | `serde` + `serde_json`（配置与 API 的 JSON）；HTTP 客户端自己写了 120 行 |
| 内部自检 | 9/9 PASS | `cargo run -p drcom-cli -- selfcheck` |

> M0 就能 `login --dry-run`（打印脱敏后的登录 URL）、`serve`（本地只读 API）、
> `selfcheck`（行为级自检）——**Linux 服务器/树莓派上完全 headless** ✓。

### M1 / M2 进展（2026-09-28）

| 项 | 结果 |
| --- | --- |
| 核心行为搬移（M1） | `session::check_once`（在线探测 → 需要时登录，四条路径全有单测）；错误文本强制过脱敏器 ✓ |
| 网络层 | `net`（CLI 与 GUI 共用；`HttpGet` trait 可在测试里注入假客户端 ✓） |
| 位置守卫 | `guard` 六条分支与 2.x 逐条对齐（含 fail-open）；`probe` 探 Wi-Fi 名/本机 IP（三平台真实样本做单测 ✓）；`cidr` IPv4+IPv6 ✓ |
| 方案 / 退避 | `profiles`（**含 `suffix`**：校内公共场合清空尾缀、宿舍带 `@yd` ✓；先全量校验再写盘；`adapt` 按 Wi-Fi 名自动切）；`backoff`（5/10/20/40/60 封顶）✓ |
| 方案入口 | CLI `profile list/save/activate/delete/auto` —— 不用手改 `config.json` ✓（`--no-suffix` 规避 PowerShell 吃掉空参数的坑 ✗） |
| 调度 / 日志 | `scheduler`（退避优先、`next_check_at` 单写者、改间隔不跳倒计时）；`logfile`（业务 5 MB×3 / 升级 2 MB×2，读取最旧→最新）；`timefmt`（零依赖 UTC + 中文时长）✓ |
| 指标 / 诊断 | `metrics`（日志行带 `[ok]/[fail]/[skip]` 等级；成功率 = ok/(ok+fail)、按天分桶、无样本显示 `—`）；`diagnostics` + `zipwriter`（**零依赖 ZIP**：store + CRC32 + 中央目录 ✓，账号打码、密码永不进包、逐行脱敏 ✓）—— 真机包用 **Python `zipfile.testzip()`** 外部校验通过 ✓ |
| 守护循环 | CLI `run [--once]`：每轮重读配置 → 守卫 → 检查 → 写日志 → 按退避排下一次 ✓（服务化在 M3） |
| **桌面界面（M2）** | **Slint 原生控件，零 WebView** ✓：深色星尘渐变 + 玻璃卡片 + 品牌色（`#0071e3` / `#2AA8FF→#3DDC97`）；4 个操作（立即检查 / 运行自检 / 打开数据目录 / 刷新状态）；账号在界面上脱敏显示 ✓ |
| 界面可测性 | 界面**不做任何判断**：读配置、跑检查、自检都在 `drcom-ui::model` 里，`cargo test` 不弹窗口就能测 ✓ |
| 测试总数 | **92/92 全绿**（core 77 + cli 9 + ui 6） |
| **GUI release 体积** | **7.84 MB**（Windows x86_64，含 winit + 软件渲染器 + 全部界面） |
| 启动验证 | 真机启动 GUI，5 秒后进程仍存活（说明 winit 后端 + 软件渲染器初始化成功 ✓） |
| 真机行为验证 | 守卫按白名单拦下并写出日志：`2026-09-28 13:30:48Z 不在校园网（…）（Wi-Fi: 6#119）` ✓ |

> **体积说明**：GUI 7.83 MB vs headless 0.32 MB —— 差在窗口系统（winit）与渲染器。
> 仍远小于 Electron（≈150 MB）；M3 会再试：
> ① `panic="abort"` + 更激进的 LTO；② 评估 `renderer-software` 之外的更小组合；
> ③ 打包时用系统字体（不内嵌字体）。目标是把 GUI 压到 5 MB 以内。

---

## 5. 开放决策

1. ~~UI 壳：Tauri 还是 Slint？~~ **已定：Slint 原生控件**（2026-09-28，理由见第 2 节）✓
2. **配置迁移**：3.0 直接读 2.x 的 `config.json` / `password.txt`（推荐，老用户零迁移成本）？
3. **Linux 守护**：默认 `systemd --user`（无需 root）✓，是否需要额外支持 OpenRC（Alpine 等）？
4. **ARMv7 上的 GUI**：交叉编译 Slint（需要 GL/X 的 sysroot）打算放到 M3 收尾；
   ARMv7 在 M1–M2 阶段先保证 **headless 核心**可用（树莓派/服务器场景本来就不需要界面 ✓）。
   → 若你要「ARMv7 也要图形界面」，我会在 M3 里补 `armv7` 的交叉 sysroot 与 AppImage 打包。

---

## 6. 与 2.x 的关系

- **2.1.x（Vega）**：Windows 稳定线继续维护（bugfix / 安全），自动更新通道不变。
- **3.0.x（Altair）**：跨平台线，**只发预览版**；通道见 `docs/VERSIONING.md`。
- 配置格式、API 路径、账号/密码文件格式**保持向后兼容**，老用户升级不丢设置。
