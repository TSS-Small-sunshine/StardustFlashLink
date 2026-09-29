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
| **桌面界面（M2）** | **Slint 原生控件，零 WebView** ✓：深色星尘渐变 + 玻璃卡片 + 品牌色（`#0071e3` / `#2AA8FF→#3DDC97`）；主界面 5 个操作（立即检查 / 运行自检 / 打开数据目录 / 刷新状态 / **设置…**）；**设置窗口**：网关 / 端口 / 账号 / 后缀 / 密码 / 检查间隔（药丸选择）/ 等网络超时 / 位置守卫（开关 + 两个白名单）/ 自动升级 + 通道，带**实时校验与提示**、**测试连接**（用表单里的值试一次，不保存 ✓）、**密码留空 = 不修改** ✓ |
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
5. **存储形式（2026-09-29 议过，暂不实施）** ✓：配置与密码**继续用** `config.json` / `password.txt` ✗（它们是人可读、可手改、并且所有脱敏/导出能力都建立在「文本」上 ✓）；
   历史与指标若要长期保留，**优先上 JSONL 事件日志**（零依赖、两边通用 ✓），SQLite 只在「确实要 SQL 式查询」时再考虑 ✓
   —— 完整分析、代价清单与触发条件见 [`docs/STORAGE.md`](STORAGE.md) ✓。

---

## 6. 与 2.x 的关系

- **2.1.x（Vega）**：Windows 稳定线继续维护（bugfix / 安全），自动更新通道不变。
- **3.0.x（Altair）**：跨平台线，**只发预览版**；通道见 `docs/VERSIONING.md`。
- 配置格式、API 路径、账号/密码文件格式**保持向后兼容**，老用户升级不丢设置。

---

## 7. 实地情报：校园网门户（2026-09 实测）

未认证时访问普通 http 会被 AC **302 到登录页**。机房实拍（`wlanuserip=172.16.59.11`）：

```text
http://172.16.80.3/a79.htm?mac=241C-0408-BDD3&rul=http://9.9.9.9/
  &wlanacip=172%2e16%2e80%2e2&wlanacname=SR8806%2dX%2dS&wlanuserip=172.16.59.11
```

| 参数 | 含义 | 备注 |
| --- | --- | --- |
| `a79.htm` | 登录页 | **公共区域与宿舍是同一个页面** ✓（此前记成 a41 / a49 是错的 ✗） |
| `mac` | AC 看到的本机 MAC | 华为格式 `241C-0408-BDD3`（大写 + 连字符分组 ✓） |
| `rul` | 被拦下之前想去的地址 | 本样本是 `http://9.9.9.9/` |
| `wlanacip` / `wlanacname` | AC 地址 / 设备名 | `172.16.80.2` / `SR8806-X-S`（华为 SR8806 ✓） |
| `wlanuserip` | AC 认为的本机地址 | 与本机探测的出口地址**不一致就该报警** ✓ |

对我们的意义（已实现 → `core::portal` ✓）：

1. **「被门户拦」可精确判定** ✓：`302 + Location 含门户特征` 或 `200 + 正文含门户参数` → 需要登录；
   正常响应 → 已在线；**连不上 → 不能说「未认证」** ✗（部分 AC 是丢包而不是重定向）。
2. 顺手拿到 AC 名字、本机地址、本机 MAC → 排障时能直接点出**多网卡 / 代理**这个坑 ✓。
3. **协议不变** ✓：登录仍走 `:801/eportal/portal/login`，`a79.htm` 只是外壳 ✓。
---

## 8. 桌面壳（M2）现状与坑

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 主窗口（Slint 原生） | ✅ 完成 | 深色星尘主题，零 WebView ✓ |
| 设置窗口 | ✅ 完成 | 账号/后缀/密码/间隔/守卫/通道 + 实时校验 + 测试连接 ✓；**配置方案页**：列表（含「使用中」标记与后缀/匹配说明）、应用 / 删除、按 Wi-Fi 自动切换开关，**新建方案取的是表单当前值** ✓ |
| 配置方案 UI | ✅ 完成 | 核心逻辑沿用 2.x（`profiles.rs`：全量校验后才写、删当前方案只清标记 ✓）；界面只做展示与调用 ✓；空列表、名字非法、后缀非法都有对应中文提示 ✓ |
| **单实例** | ✅ 完成 | 占 `127.0.0.1:47653`：第二个实例把第一个的窗口叫到前面后自己退出 ✓（真机验证：进程数 1 ✓）。**不用锁文件** ✗ —— 进程崩溃不会留下假锁 ✓ |
| 开机自启 | ✅ 完成 | `autostart on\|off\|status [--dry-run]`：Windows `HKCU\...\Run` / Linux `systemd --user` / macOS LaunchAgent —— **全程当前用户级，一次管理员权限都不要** ✓；跑的是**无界面循环**（`run` ✓，不开机弹窗 ✗）。含「纯函数产出命令/文件内容 + 薄执行层」两层，命令逐字有单测 ✓（连「路径带空格要加引号」「plist 里 `&<>` 要转义」都测了 ✓） |
| **系统托盘** | ⛔ 暂时做不了 | Slint 1.18 有 `system-tray` feature，但**只有 Qt 等后端实现** —— 查过 `i-slint-backend-winit-1.18.1` 源码，**零处引用 SystemTray** ✗。我们用 winit 后端 ⇒ 做出来也是个摆设 ✗，**不能给用户一个点不动的托盘**，故推迟到 M3（届时用平台原生 API：Windows `Shell_NotifyIcon`、Linux `libayatana-appindicator`、macOS `NSStatusItem`） |

---

## 9. 服务化（M3 起步，已完成）

`stardust-flash-link service install|uninstall|start|stop|restart|status [--dry-run]` ✓

| 平台 | 常驻方式 | 落点 / 命令 |
| --- | --- | --- |
| Windows | **NSSM 托管的服务** | 服务名 **`DrcomAutoLogin`**（与 2.x **故意共用** ✓，存量安装/卸载/升级脚本都认它 ✓）；`nssm install/set/start/stop/remove` |
| Linux | `systemd --user` | `~/.config/systemd/user/stardust-flash-link.service` + `systemctl --user …`（**免 root** ✓） |
| macOS | LaunchAgent | `~/Library/LaunchAgents/com.stardust.flashlink.plist` + `launchctl load -w` |

设计要点（都写进代码注释与单测了 ✓）：
1. **遵守 2.x 不变量 I7**：NSSM 的 `AppExit` 写 `Default Ignore` + `0 Ignore`，**绝不写 `Restart`** ✗ ——
   重试由程序自己的退避负责 ✓（有单测断言这一点 ✓）。
2. **同名服务是常态** ✓：3.0 查到「正在运行」时，跑的大概率是 **2.x 那个** ✓ ——
   `service status` 会直接点明 ✓；`service install` 发现同名服务**不硬装** ✗，而是给出**接管**的
   三条 `nssm set …`（Application / AppDirectory / AppParameters）+ 另一条「先 uninstall 再 install」✓。
3. **不在 Windows 上自己写 SCM 调度器** ✗：那需要一大坨 `unsafe` FFI + 一个只在真机才能验的服务模型 ✓；
   `nssm.exe` 2.x 安装包里本来就随包分发 ✓ → 先复用 ✓。
4. **状态查询免管理员** ✓：Windows 用 `sc query`（普通用户可查 ✓，`1060` = 服务不存在 ✓ 比认中英文文案可靠 ✓）；
   Linux/macOS 先看单元文件在不在，再问 `systemctl is-active` / `launchctl list` ✓。
5. **没装管理工具时给两条可走的路** ✓：把 `nssm.exe` 放到程序目录旁 ✓，或先用 `autostart on` 顶上 ✓
   （登录时自动跑、免管理员 ✓，只是没有服务级「开机即起」✓）。
6. Linux / macOS 的单元文件与 `autostart` **共用同一份生成函数** ✓ —— 两条命令永远不会写出两份不一样的配置 ✓。

> 小坑记录：改完 UI 只跑 `cargo test` **不会刷新 GUI 二进制** ✗ ——
> 启动前要先 `cargo build -p drcom-ui` ✓（第一次真机验证单实例就是被旧二进制骗了 ✗）。

---

## 10. 打包地基（SHA-256 + 包内文件生成）

### SHA-256：自己实现（`core::checksum`）

**为什么不用 crate**：① 更新包校验要**每平台行为一致**，多一个依赖就多一处版本差异 ✗；
② 这段算法**公开、短、能对着官方向量逐条验** ✓，比引 crate 更好审计 ✓；③ 守住「零第三方依赖」✓。

验证做到了三层（都在单测/真机里 ✓）：
1. **NIST/FIPS 官方向量**逐字节对照 ✓（空串 / `abc` / 448 位 / 896 位 / 100 万个 `a` ✓）；
2. **切块一致性**：按 1/3/7/64/65/128/999 字节切着喂，结果必须与一次性完全一样 ✓；
3. **真机外部交叉验证** ✓：对一个真实 3MB 二进制，`stardust-flash-link checksum` 与
   Windows 自带 `Get-FileHash -Algorithm SHA256` **逐位一致** ✓（与 2.x 用 Python `zipfile` 互验同一思路 ✓）。

### 包内文件生成（`core::package`）

| 生成物 | 用在哪 |
| --- | --- |
| `.desktop` | Linux 桌面项（`Exec=… run`、`Terminal=false`、Network 分类 ✓） |
| `deb-control` | Debian 元数据（**架构名换算** `x86_64→amd64` / `aarch64→arm64` / `armv7→armhf` ✓，体积换算成 KB ✓） |
| `Info.plist` | macOS `.app`（BundleIdentifier / Executable / Version / `CFBundlePackageType=APPL` ✓） |
| `README.txt` | 包里那封「怎么用」说明（**首句就写清是预览版** ✓，命令清单 + 配置兼容说明 ✓） |
| 产物命名 | `stardust-flash-link-<版本>-<目标>.zip` ✓（无空格无斜杠 ✓ 有单测 ✓） |
| `SHA256SUMS` 行 | coreutils 格式（**两个空格** ✓），自动升级对着它校验 ✓ |
| ZIP 打包 | `archive`：用**自带的 ZIP 实现** ✓（CI 里不需要外部 `zip`/`tar` ✓，三平台行为一致 ✓）；写完会把文件**读回来解析中央目录**做自检 ✓ |

用法（也就是 CI 里跑的那三步 ✓）：

```bash
stardust-flash-link package-files --out pkg --target linux-aarch64 --exe stardust-flash-link
                 # ↑ 第一行输出产物基名；**交叉编译时必须显式 --target** ✗（宿主自报目标是错的 ✓）
stardust-flash-link archive --out dist/<基名>.zip pkg
stardust-flash-link checksum dist/<基名>.zip      # → "<hex>  <文件>" / sha256sum 兼容 ✓
```

**CI 已接入** ✓（`preview-3.0.yml` 的 build 矩阵）：每个目标都产出 `dist/<基名>.zip` +
`dist/SHA256SUMS`，与原始二进制一起挂 artifact ✓。交叉编译的行由**宿主编出的辅助程序**完成打包 ✓
（目标二进制在 runner 上跑不了 ✗，所以生成包内文件与打 zip 都用宿主那份 ✓，靠 `--target` 纠正标签 ✓）。
真机/CI 双重验证：Linux（含 aarch64/armv7 交叉）+ macOS（两架构）+ Windows 六行全绿 ✓；
本地还用 **.NET `ZipFile`** 打开过我们写的包 ✓（条目名与字节数对得上 ✓，与 2.x 用 Python `zipfile` 互验同一路子 ✓）。

> ⏭ 下一步：`.app` 包体 / `.dmg` / `.deb` 正式安装器（现在先是 ZIP + 校验和，够预览用 ✓）。
