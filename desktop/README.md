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
cargo run -p drcom-cli -- run --once        # 跑一次守护循环（含位置自适应 + 写日志）
cargo run -p drcom-cli -- diagnostics        # 生成「脱敏诊断包」（ZIP；密码永不进包 ✓）
```

### 桌面界面（GUI）

```bash
cargo run -p drcom-ui        # 主界面：状态卡 + 立即检查 / 自检 / 打开目录 / 刷新 / 设置…
```
- **版式对齐 2.x 的 Web UI**（v3.0 起）：默认「星尘风」浅色极光底 + 更白更厚的卡片 + 星尘紫强调，
  顶栏一键切深色 ✓；主界面按 2.x 状态页的信息架构摆成**两栏** ——
  左栏「状态与主操作同一张卡（2.x 里「当前状态」与「立即登录」也是一张卡 ✓）+ 日常 5 格 + 诊断 4 格」，
  右栏「账户与登录密码卡」，下面跨两栏放终端式结果窗（三个圆点 + 徽标 ✓）；默认 1280×800，可缩放 ✓
- 主界面：运行方式 / 账号（脱敏 ✓）/ 网关 / 自动检查间隔 / 平台 / 配置状态 / 数据目录
- **设置窗口**：改账号、后缀（留空 = 校内直连 ✓）、密码（**留空 = 不修改** ✓）、检查间隔、
  位置守卫与白名单、自动升级与通道 —— 有**实时校验**和「测试连接」（用表单里的值试一次，**不保存** ✓）
- **配置方案页**（在设置窗口里）：列出所有方案（标出「使用中」+ 后缀 + 匹配几个 Wi-Fi ✓）、
  一键**应用** / **删除**，以及**按 Wi-Fi 自动切换**开关；新建方案时「方案名 + 匹配 Wi-Fi 名」
  由你填，**值取的就是表单里的当前设置** ✓（校内公共场合留空后缀、宿舍填 `@yd` ✓）
- **单实例**：重复启动不会开出第二个窗口 ✓ —— 后启动的那个会把已有窗口**叫到前面**再自己退出 ✓

> **给 Slint 窗口截图 / 排查「内容被顶出窗口」时**：脚本必须先声明 DPI 感知
> （`SetProcessDPIAware()` ✓，并先把窗口挪到 `(0,0)` ✓）—— 否则 `GetWindowRect` 返回的是
> 被虚拟化的**逻辑**坐标、`CopyFromScreen` 却按**物理**像素抓，结果只拍到窗口左上角一块，
> **看起来就像布局把内容顶出了窗口 ✗**。真踩过一轮：据此把状态页退成单栏、把统计条压成 3+3 ✗，
> 其实布局一直是好的（Slint 的布局能正常缩放，格子里的长文本用 `overflow: elide` 就够 ✓）。

### 开机自启 / 后台常驻

```bash
stardust-flash-link autostart status               # 看现在开没开
stardust-flash-link autostart on --dry-run         # 只打印将要执行的命令（不动系统 ✓）
stardust-flash-link autostart on                   # 开启（**当前用户级，免管理员** ✓）
stardust-flash-link autostart off                  # 关掉（幂等 ✓）
```

自启跑的是**无界面循环**（`… run`）✓ —— 开机不弹窗 ✗；落点：Windows 注册表
`HKCU\...\Run`、Linux `systemd --user`、macOS `~/Library/LaunchAgents` ✓。

### 常驻服务（比自启更「正式」的做法）

```bash
stardust-flash-link service status                      # 常驻方式 + 状态 + 工具是否在手边
stardust-flash-link service install --dry-run           # 只打印将执行的命令（不动系统 ✓）
stardust-flash-link service install                     # 注册并启动
stardust-flash-link service start|stop|restart          # 启停 / 重启
stardust-flash-link service uninstall                   # 注销（幂等 ✓）
```

- Windows 走 **NSSM 托管的服务**，服务名是 **`DrcomAutoLogin`** —— 与 2.x **故意共用** ✓，
  所以 `status` 显示「正在运行」时，**跑的可能就是 2.x 那个** ✓（命令会点明这一点 ✓）；
  `install` 遇到同名服务**不会硬装** ✗，会给出把服务指向 3.0 程序的「**接管**」命令 ✓。
- Linux / macOS 走 `systemd --user` / LaunchAgent，**全程免 root / 免管理员** ✓，
  并且与 `autostart` 写的是**同一份文件** ✓（不会有两套配置打架 ✗）。
- 状态查询**不需要管理员** ✓（Windows 用 `sc query` ✓）。

### 排障：一键脱敏诊断包

出问题时不用截图 + 口述，直接生成一个包发给别人 ✓：

```bash
stardust-flash-link diagnostics --out diag.zip   # 忽略路径则放数据目录
```

包里是：`说明.txt`、`status.json`、`config.sanitized.json`（**账号只留前 4 位**）、
`metrics.json`（近 N 天成功率）、`logs/campus_login.log`（最多 400 行，**逐行脱敏** ✓）。

铁律：**密码永远不进包** ✓；日志里任何 URL 都会被擦成 `<url>` ✓（有单测 + 真机双向验证 ✓；
包用 Python `zipfile.testzip()` 校验为**标准 ZIP** ✓）。

### 看看「是不是被校园网门户拦住了」

登录走的是 Dr.COM 接口（`:801/eportal/portal/login`）✓，校园门户页**只是外壳**
（**公共区域与宿舍都是 `a79.htm`** ✓）。要确认当前到底卡在哪一步：

```bash
stardust-flash-link portal                       # 默认探 http://9.9.9.9/
stardust-flash-link portal --url http://example.com/
```

结论只有三种，且**不会乱猜**：
- **被门户拦** → 需要登录；打印门户页地址、AC 名字（如 `SR8806-X-S`）、
  AC 看到的本机地址与本机 MAC ✓
- **能正常访问** → 已在线 ✓
- **连不上** → 可能是没网；**这不等于「未认证」** ✗（有的 AC 是丢包而不是重定向）

顺手还会做一次**地址一致性检查**：门户看到的本机地址与本机探测的出口地址不一致时直接报警
—— 多网卡 / 代理最容易踩这个坑 ✗。

### 位置方案：校内公共场合不带运营商尾缀

同一张学号，**校内公共场合**（图书馆/教学楼 Wi-Fi）走校园网**不需要** `@yd`（移动）这类尾缀，
而宿舍/校外商用宽带通常要带 ✓ —— 用「配置方案 + 按 Wi-Fi 名自动切」搞定：

```bash
# 1) 宿舍：带移动尾缀
stardust-flash-link profile save 宿舍 --ssid Dorm-WiFi --suffix @yd
# 2) 校内公共场合：**清空**尾缀（`--no-suffix` 最稳，见下面「已知坑」）
stardust-flash-link profile save 校内公共场合 --ssid Campus-WiFi --no-suffix
# 3) 打开自动切换（走进匹配的 Wi-Fi 就自动切方案）
stardust-flash-link profile auto on
# 4) 看看现状
stardust-flash-link profile list
stardust-flash-link run --once     # 日志里会写：位置自适应：切到方案「…」（后缀 …）
```

> ⚠️ **已知坑**：`--suffix ""` 在 PowerShell 里**传不进去**（空参数被吃掉 ✗）——
> 所以清空后缀请用 `--no-suffix`，或 `--suffix 空` / `suffix none` / `--suffix -` ✓。
> 忘了这回事也没事：CLI 检测到「写了 `--suffix` 但没值」会**主动提示**你 ✓。


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
