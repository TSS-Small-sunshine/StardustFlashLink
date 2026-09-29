<h1 align="center">星尘闪连 3.0 · Altair（牛郎星）</h1>

<p align="center">
  <b>Stardust Flash Link</b> · 跨平台校园网自动登录 —— Windows / Linux / macOS 同源
</p>

<p align="center">
  <img src="https://img.shields.io/badge/通道-预览版-orange?style=flat-square" alt="preview"/>
  <img src="https://img.shields.io/badge/平台-Windows%20%7C%20Linux%20%7C%20macOS-blue?style=flat-square" alt="platform"/>
  <img src="https://img.shields.io/badge/Rust-2021-dea584?style=flat-square&logo=rust&logoColor=white" alt="rust"/>
  <img src="https://img.shields.io/badge/UI-Slint%20原生-blueviolet?style=flat-square" alt="slint"/>
  <img src="https://img.shields.io/badge/依赖-零第三方-success?style=flat-square" alt="zero deps"/>
</p>

> ## 🚧 这是 3.0 的**开发线**（DEV）
>
> - 想要**稳定版**：请去 [`main` 分支](https://github.com/TSS-Small-sunshine/StardustFlashLink/tree/main)（2.1.x「Vega」，
>   Windows 版，带托盘 / Web UI / 自动升级）✓
> - 本分支（`dev/3.0-altair`）**只发预览版**：正式用户的自动升级**收不到它** ✓，别当日常主力 ✗
> - 根目录里那些 `.py`（`protocol.py` / `web_api.py` / `tray.py` …）是 **2.x 的源码对照**，3.0 的实现全在
>   [`desktop/`](desktop/) 里（Rust ✓）

## 📑 目录

- [这是什么](#这是什么)
- [平台矩阵（CI 实测）](#平台矩阵ci-实测)
- [快速开始](#快速开始)
- [命令一览](#命令一览)
- [桌面界面](#桌面界面)
- [配置与密码（与 2.x 兼容）](#配置与密码与-2x-兼容)
- [与 2.x 的关系](#与-2x-的关系)
- [开发](#开发)
- [文档索引](#文档索引)

---

## 这是什么

「星尘闪连」的**跨平台重写**：一套 **Rust 核心 + Slint 原生界面**，三条平台同源 ✓

- **零第三方依赖** ✓：连 SHA-256 都是自己的实现（对着 NIST 官方向量 + Windows `Get-FileHash` 双向验过 ✓）
- **零 WebView、零运行时解释器** ✓：单文件可执行，不需要装 Python / 浏览器内核
- **配置格式与登录协议沿用 2.x** ✓：`config.json` / `password.txt` / `/eportal/portal/login` 全都不变 → 老用户零迁移 ✓
- **同一套核心跑三处** ✓：命令行（可 headless）+ 桌面界面 + 系统服务，行为一致 ✓

已经做完的能力（对应 `docs/PLATFORMS.md` 的里程碑）：

| 里程碑 | 内容 |
| --- | --- |
| M0–M1 | 核心（协议 / 配置 / 守卫 / 退避 / 日志 / 诊断包）+ 命令行 + 三平台 CI 矩阵 |
| M2 | Slint 桌面界面、设置窗口、**配置方案页**（按 Wi-Fi 自动切）、单实例、开机自启、门户检测 |
| M3（进行中） | **服务化**（Windows NSSM / `systemd --user` / LaunchAgent ✓）、**打包**（ZIP + `SHA256SUMS`，CI 六行全绿 ✓）；余下：托盘、`.dmg`/`.deb`、ARMv7 GUI 的 sysroot |
| M4 | 通道感知自动更新 + 便携版单文件 |

## 平台矩阵（CI 实测）

每个目标在 CI 上**真编译**、命令行核心**真跑测试**，并产出一个 ZIP + `SHA256SUMS` ✓：

| 目标 | 命令行核心 | 桌面界面 | 典型机器 |
| --- | --- | --- | --- |
| `windows-x86_64` | ✅ | ✅ | Windows 10 / 11 |
| `linux-x86_64` | ✅ | ✅ | 常见 PC / 服务器 |
| `linux-aarch64` | ✅ | ⏳（需要目标 sysroot） | 服务器 / 香橙派 |
| `linux-armv7` | ✅ | ⏳（需要目标 sysroot） | 树莓派（32 位系统） |
| `macos-aarch64` | ✅ | ✅ | Apple Silicon |
| `macos-x86_64` | ✅ | ⏳（M 系 runner 上只编核心） | Intel Mac |

> 界面是**原生编译**：交叉编译 GUI 需要目标系统的 GL/X/Wayland sysroot，那是 M3 余下的活 ✓
> 命令行核心**六个目标全覆盖** ✓（服务器 / 树莓派这类场景本来就无界面 ✓）

---

## 快速开始

### 命令行（headless，六平台通用）

```bash
cd desktop
cargo build --release -p drcom-cli

./target/release/stardust-flash-link status        # 版本 / 通道 / 平台 / 配置状态
./target/release/stardust-flash-link login         # 跑一次登录检查
./target/release/stardust-flash-link run           # 常驻循环（按配置间隔检查）
./target/release/stardust-flash-link profile list  # 位置方案（校内公共场合 / 宿舍 …）
```

### 桌面界面

```bash
cd desktop
cargo run -p drcom-ui        # 主窗口：状态卡 + 立即检查 / 自检 / 打开目录 / 刷新 / 设置…
```

（Linux 上编译界面需要装 X11/Wayland 开发库：`libxkbcommon-dev libwayland-dev libx11-dev
libxcursor-dev libxrandr-dev libxi-dev libfontconfig1-dev` ✓ —— CI 里就是这么装的 ✓）

### 让它常驻 / 开机自启

```bash
stardust-flash-link autostart on --dry-run     # 先看要改什么（不动系统 ✓）
stardust-flash-link autostart on               # 登录时自动跑（免管理员 ✓）
stardust-flash-link service status             # 更正式：注册成系统服务
stardust-flash-link service install            # Windows 走 NSSM / Linux systemd --user / macOS LaunchAgent ✓
```

> ⚠️ Windows 上服务名**与 2.x 共用** `DrcomAutoLogin` ✓（存量安装 / 卸载 / 升级脚本都认它 ✓）。
> 所以 `service status` 显示「正在运行」时，跑的大概率是 **2.x 那个** ✓ ——
> 命令会点明这一点；`service install` 遇到同名服务不会硬装，而是给出**接管**的具体命令 ✓

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `version` | 版本 / 通道 / 平台 / 目标标签 |
| `status` | 状态 JSON（与本地 API 同一份数据 ✓） |
| `selfcheck` | 内置自检（版本一致性 / 配置 / 协议解析 / 路径可写 ✓） |
| `login` | 跑一次登录检查（`--dry-run` 只打印**脱敏后**的 URL ✓） |
| `run` | 常驻循环（失败指数退避 `5 → 10 → 20 → 40 → 60` 分钟封顶 ✓） |
| `profile` | 位置方案（`save` / `activate` / `delete` / `auto` ✓） |
| `diagnostics` | 生成**脱敏**诊断包（ZIP ✓，密码永不进包 ✓） |
| `serve` | 起本地控制 API（只绑 `127.0.0.1` ✓） |
| `portal` | 门户检测：是不是被校园网门户拦住了（未认证会被 302 到登录页 ✓） |
| `autostart` | 开机自启（`on` / `off` / `status`，**当前用户级、免管理员** ✓） |
| `service` | 常驻服务（`install` / `uninstall` / `start` / `stop` / `restart` / `status` ✓） |
| `checksum` | 算 SHA-256（不给文件就算自己 ✓）—— 打包校验 / 升级凭据 ✓ |
| `package-files` | 写出该平台的包内文件（`.desktop` / `Info.plist` / `deb-control` / README ✓） |
| `archive` | 把目录打成 ZIP（**自带实现** ✓，不需要外部 `zip` / `tar` ✓） |

---

## 桌面界面

Slint 原生控件（**零 WebView** ✓），深色星尘主题：

- **主窗口**：网关 / 账号（脱敏 ✓）/ 间隔 + 当前方案 / 平台 / 运行方式 / 配置状态；按钮：立即检查、运行自检、打开数据目录、刷新状态、**设置…**
- **设置窗口**：账号、后缀（留空 = 校内直连 ✓）、密码（**留空 = 不修改** ✓）、检查间隔、位置守卫与白名单、自动升级与通道 —— 带**实时校验**和「测试连接」（用表单里的值试一次，**不保存** ✓）
- **配置方案页**：列出所有方案（标出「使用中」✓ + 后缀 + 匹配几个 Wi-Fi ✓）、一键**应用** / **删除**、**按 Wi-Fi 自动切换**开关；新建方案时**值取表单当前的设置** ✓
- **单实例**：重复启动不会开出第二个窗口 ✓ —— 后启动的那个会把已有窗口**叫到前面**再自己退出 ✓

## 配置与密码（与 2.x 兼容）

| 文件 | 说明 |
| --- | --- |
| `config.json` | 键名与 2.x 一致 ✓（`host` / `port` / `account` / `suffix` / `auto_check_interval_min` / `network_guard_enabled` / `guard_allowed_ssids` / `guard_allowed_subnets` / `profiles_auto_switch` / `profiles` …） |
| `password.txt` | 一行密码；`#` 开头是注释 ✓（**密码永不出现在界面回显 / 日志 / 诊断包里** ✓） |

路径随平台不同（跑一次 `status` 就会打印真实路径 ✓）：Windows `%ProgramData%\StardustFlashLink`、
Linux `~/.local/share/stardust-flash-link`、macOS `~/Library/Application Support/com.stardust.flashlink` ✓。
在程序目录里放一个 `portable.marker` 即可切到**便携模式**（数据写在程序目录 ✓）。

## 与 2.x 的关系

- `main` = **2.1.x「Vega」稳定线**（Windows，Python + 内嵌运行时 + 托盘 + Web UI + 自动升级）✓ **继续正常维护** ✓
- 本分支 = **3.0「Altair」跨平台线** ✓ **只发预览版** ✓ —— 正式用户的自动升级**收不到它** ✓
- 两条线互不干扰 ✓：3.0 不会往 `main` 合，也**不会动你机器上的** `config.json` / `password.txt`（格式向后兼容 ✓）
- 唯一「故意共用」的地方：Windows 服务名 `DrcomAutoLogin` ✓（存量脚本依赖它，见上面那条提醒 ✓）

## 开发

```bash
cd desktop
cargo test --workspace                  # 186 项测试（核心 155 / 命令行 18 / 界面 12 / doctest 1 ✓）
cargo build --release -p drcom-cli      # 命令行
cargo build --release -p drcom-ui       # 桌面界面
```

- 结构：`crates/drcom-core`（核心，**零第三方依赖** ✓）/ `crates/drcom-cli`（命令行）/ `crates/drcom-ui`（Slint 界面，`ui/*.slint`）
- CI：[`.github/workflows/preview-3.0.yml`](.github/workflows/preview-3.0.yml) —— 三平台跑测试 + 六目标构建 + 打包（ZIP + `SHA256SUMS` ✓），每版还会打印体积 ✓
- 提交前跑 `cargo test --workspace` ✓；**改完界面记得 `cargo build -p drcom-ui`** ✗（只跑测试**不会**刷新 GUI 二进制 ✗）

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [`desktop/README.md`](desktop/README.md) | 怎么跑、每个命令的用法与排障（诊断包 / 方案 / 门户检测 / 自启 / 服务 ✓） |
| [`docs/PLATFORMS.md`](docs/PLATFORMS.md) | 路线图 / 平台矩阵 / 里程碑 / **校园网实测情报**（门户 `a79.htm` 那条 ✓） |
| [`docs/VERSIONING.md`](docs/VERSIONING.md) | 版本号与代号规则 ✓ |
| [`AGENTS.md`](AGENTS.md) §9 | **改这个仓库前必读**：3.0 的硬约束（R1–R8）/ 验证纪律 / 隐私红线 ✓（§1–§8 是 2.x 的规范 ✓） |
| 2.x（Python 版）的完整文档 | 在 [`main` 分支](https://github.com/TSS-Small-sunshine/StardustFlashLink/tree/main) ✓（本分支保留同名 `.py` 仅供对照 ✓） |


