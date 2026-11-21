# desktop/ —— 3.0 跨平台核心（Rust，代号 Altair 牛郎星）

> 目标见 [`../docs/PLATFORMS.md`](../docs/PLATFORMS.md)：**一份核心，多端可用**
> （Windows / Linux AMD64·ARMv8·ARMv7 / macOS Intel·Apple Silicon），
> 并且**不依赖浏览器** —— GUI 外壳在 M2 接到这个核心上。

## 现在的状态（M4 收尾 · preview.6，2026-11-22）

| 项 | 值 |
| --- | --- |
| 单元测试 | **338 项全绿**（核心 274 + 可执行 29 + 界面 35；另有 1 项文档测试）—— Windows 实测；Unix 平台多 1 项文件权限测试（339）✓ |
| headless 可执行 | **0.32 MB**（preview.4 CI 实测；每版由 CI「体积报告」打印 ✓） |
| Slint GUI | **7.83 MB**（preview.4 CI 实测，Windows x86_64；零 WebView，真机可启动 ✓） |
| 依赖 | 核心只 2 个（`serde`/`serde_json`）；GUI 另加 Slint（软件渲染器 ✓） |
| 覆盖平台 | 交叉编译矩阵见 `.github/workflows/preview-3.0.yml`（6 目标；GUI 在原生 runner 上编译） |

## 本地怎么跑

```bash
cd desktop
cargo test --workspace          # 全部单测
cargo run -p drcom-cli -- version      # 版本 / 通道 / 平台 / 架构
cargo run -p drcom-cli -- selfcheck    # 内置自检（行为级，和 2.x 的 _smoke_*.py 一个思路）
cargo run -p drcom-cli -- status       # 状态 JSON（与本地 API 同一份数据）
cargo run -p drcom-cli -- serve        # 起本地控制 API（默认端口取配置 ui_port，默认 8848）
cargo run -p drcom-cli -- login --dry-run   # 只打印「脱敏后的」登录 URL ✓
cargo run -p drcom-cli -- run --once        # 跑一次守护循环（含位置自适应 + 写日志）
cargo run -p drcom-cli -- diagnostics        # 生成「脱敏诊断包」（ZIP；密码永不进包 ✓）
cargo run -p drcom-cli -- update check       # 检查更新（默认通道取配置 update_channel ✓）
cargo run -p drcom-cli -- update download out.zip   # 检查 + 下载 + SHA256 校验（只下载，不安装 ✓）
cargo run -p drcom-cli -- update apply out.zip --dry-run   # 预演自我替换（只打印计划，不动盘 ✓）
cargo run -p drcom-cli -- update apply out.zip             # 自我替换（备份 → 换 exe → 拉起新程序 ✓）
cargo run -p drcom-cli -- update rollback                  # 回滚到最新备份
cargo run -p drcom-cli -- update backups                   # 列出所有备份版本
```

> **密码只从 `password.txt` 读** ✓ —— CLI **不再接受 `--password`** ✗（写在命令行里会进 shell
> 历史与进程列表，同机其他用户/进程都能看到；审计 P2-11）。`login --password x` 会返回
> **用法错误（退出码 2）** 并提示密码文件路径 ✓。

### 退出码（脚本 / 服务入口依赖它）

| 退出码 | 含义 | 出现的命令 |
| --- | --- | --- |
| **0** | 成功（含「跳过」：不在校园网 / 账号未设置 —— 跳过不算失败 ✓） | 全部命令；`run --once` 的 Ok / Skip |
| **1** | 失败（登录被拒 / 网关不可达 / 文件读写失败 / 校验失败） | `login` 失败、`run --once` 的 Fail、`checksum` 等 |
| **2** | 用法错误（未知命令 / 参数写错 / 端口不合法 / 未知子命令） | 所有子命令 |

> `run --once`（审计 P3-1）：**成功 / 跳过 → 0，失败 → 1** ✓ —— 作为 systemd 单元、
> 计划任务或 CI 脚本入口时，「这次登录失败了」必须能传递出去 ✗（旧实现永远 0 ✗）。
> `login` 同样：成功 0 / 失败 1 / 守卫跳过 0；参数写错是 2 ✓。

### 桌面界面（GUI）

```bash
cargo run -p drcom-ui        # 主界面：状态卡 + 立即检查 / 自检 / 打开目录 / 刷新 / 设置…
```
- **版式对齐 2.x 的 Web UI**（v3.0 起）：默认「星尘风」浅色极光底 + 更白更厚的卡片 + 星尘紫强调，
  顶栏一键切深色 ✓；主界面按 2.x 状态页的信息架构摆成**两栏** ——
  左栏「状态与主操作同一张卡（2.x 里「当前状态」与「立即登录」也是一张卡 ✓）+ 日常 5 格 + 诊断 4 格」，
  右栏「账户与登录密码卡」，下面跨两栏放终端式结果窗（三个圆点 + 徽标 ✓）；默认 1280×800，可缩放 ✓
- 主界面：运行方式 / 账号（脱敏 ✓）/ 网关 / 自动检查间隔 / 平台 / 配置状态 / 数据目录
- **设置窗口**：按 2.x **配置页**的信息架构摆成**两栏**（左主右辅 ✓，不再是一根竖排长表单 ✗）——
  左栏「账户与登录」（**网关 + 端口 / 账号 + 后缀 一行两列** ✓，就是 2.x 的「两列表单」；密码**留空 = 不修改** ✓）
  与「配置方案」；右栏「检查节奏（间隔药丸 + 等网络超时）」「无人登录也自动登录（注册成服务）」「升级」
  「网络位置守卫（进阶）」。字段一律**标签在上、提示在下** ✓；**进阶项折叠**收起 ✓（新建 / 覆盖方案、
  网络位置守卫 —— 2.x 的 `<details class="fold">`：「展开会顶掉半屏」✗）；底部整宽**保存条**：
  左边实时校验结果 + 配置目录，右边「重新读取 / 测试连接 / 关闭 / 保存」✓
- **配置方案**（设置窗口左栏）：列出所有方案（标出「使用中」+ 后缀 + 匹配几个 Wi-Fi ✓），每行一键
  **应用** / **删除**，卡片标题右边是**按 Wi-Fi 自动切换**开关；新建方案时「方案名 + 匹配 Wi-Fi 名」
  由你填，**值取的就是表单里的当前设置** ✓（校内公共场合留空后缀、宿舍填 `@yd` ✓）
- 设置窗口默认 **1040×820**；正文两栏放在 `Flickable` 里（不引 std-widgets 的 `ScrollView` ✗）——
  **卡片高度是写死的常量**（`left-h` / `right-h` 加一加 ⇒ `body-h` ✓），改卡片内容时**必须同步改那两个常量** ✗，
  否则最后一张卡会被 `content-height` 裁掉（真踩过 ✓）；折叠展开后内容自然变高 ⇒ 出现滚动 ✓
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
stardust-flash-link autostart off                  # 关掉（幂等 ✓：本来就没开 → 直接成功、不跑删除命令 ✓）
```

自启跑的是**无界面循环**（`… run`）✓ —— 开机不弹窗 ✗；落点：Windows 注册表
`HKCU\...\Run`、Linux `systemd --user`、macOS `~/Library/LaunchAgents` ✓。

### 常驻服务（比自启更「正式」的做法）

```bash
stardust-flash-link service status                      # 常驻方式 + 状态 + 工具是否在手边
stardust-flash-link service install --dry-run           # 只打印将执行的命令（不动系统 ✓）
stardust-flash-link service install                     # 注册并启动
stardust-flash-link service start|stop|restart          # 启停 / 重启
stardust-flash-link service uninstall                   # 注销（幂等 ✓：没装 → 直接成功、不跑 nssm remove ✓）
```

- Windows 走 **NSSM 托管的服务**，服务名是 **`DrcomAutoLogin`** —— 与 2.x **故意共用** ✓，
  所以 `status` 显示「正在运行」时，**跑的可能就是 2.x 那个** ✓（命令会点明这一点 ✓）；
  `install` 遇到同名服务**不会硬装** ✗，会给出把服务指向 3.0 程序的「**接管**」命令 ✓。
- Linux / macOS 走 `systemd --user` / LaunchAgent，**全程免 root / 免管理员** ✓，
  并且与 `autostart` 写的是**同一份文件** ✓（不会有两套配置打架 ✗）。
- 状态查询**不需要管理员** ✓（Windows 用 `sc query` ✓）。

### 自动更新（M4 第二块：检查 / 下载 / **自我替换 / 回滚**）

```bash
stardust-flash-link update check                      # 通道取配置 update_channel（默认正式版 ✓）
stardust-flash-link update check --channel preview    # 临时看预览通道
stardust-flash-link update download out.zip           # 检查 + 下载 + SHA256 校验（只下载，不安装 ✓）
stardust-flash-link update apply out.zip --dry-run    # 预演替换：只打印计划，**不动任何文件** ✓
stardust-flash-link update apply out.zip              # 自我替换：备份 → 换文件 → 拉起新程序（不联网 ✓）
stardust-flash-link update apply out.zip --no-restart # 只换文件，提示「重启后生效」✓
stardust-flash-link update rollback                   # 回滚到**最新**备份（备份里有几个程序就恢复几个 ✓）
stardust-flash-link update rollback --version 3.0.0.0-preview.2   # 回滚到指定版本
stardust-flash-link update backups                    # 列出所有备份（backup/<版本>/<程序名>，含每个版本备份了哪些程序）
```

- **GUI（M4 第二块）**：主窗口「更新」一行 →「检查更新」发现新版本后出现「**下载并安装**」按钮
  （没更新时按钮不存在 = 不留死按钮 ✓）。点击后后台线程完成 下载 → SHA256 → 解包 → 备份 → 换 exe，
  然后自动重启（**先放单实例端口 → 拉起新程序 → 旧进程退出** ✓，顺序反了新的自己会被当成第二个实例踢回来 ✗）；
  失败给可读中文错误、按钮可重试 ✓。启动时还会清一次上次更新留下的 `.old-*` / `.incoming-*` ✓。
  「**回滚**」一行：安装目录里有 `backup/<版本>/` 时才出现（没有备份就不显示 = 不留死按钮 ✓），
  列出最新 3 个版本（标签带「界面 / 命令行」清单）；点版本只进入确认态，再点「确认回滚」才动盘，
  成功后同样自动重启 ✓。
- **同目录的程序一起换（M4 收尾）**：CI 的 zip 里同时有 GUI（`stardust-flash-link-app`）与 CLI
  （`stardust-flash-link`）；安装 / 回滚按**安装目录**工作 —— 目录里原本有几个本项目程序就一起换几个，
  **不会留下「GUI 是新版、CLI 还是旧版」的混合状态** ✗；也不凭空添加文件（只解压了 GUI 就只换 GUI ✓）。
  多个文件是**全有或全无**：先全部备份并校验 → 全部复制到临时名 → 逐个 rename；任一失败把已换的全部换回去 ✓。
  安装包必须同时带齐安装目录里的每个本项目程序 —— 只带一半的包整包拒绝（只换一半更糟 ✗）。
- **自我替换怎么做的（Windows 关键前提）**：运行中的 exe **不能被删除 / 覆盖**，但**可以被改名** →
  ① 备份当前 exe 到 `backup/<当前版本>/<exe 名>`（并做 SHA-256 校验）→ ② 新 exe 复制到同目录临时名
  → ③ 旧 exe 改名 `<exe>.old-<pid>-<序号>` → ④ 新 exe 改名成正式名 → ⑤ 拉起新进程 → ⑥ 旧进程退出。
  任一步失败**自动回滚**（旧 exe 改回原名 + 清临时文件）；连回滚都失败的极端情况，错误文案会写清
  「旧程序现在在哪个文件名」，**绝不静默丢程序** ✓。
- **只信自己的包**：`core::zipread` 只收本项目 `archive` 命令打的 **store ZIP**
  （method 8 压缩包 → 中文报错拒绝；条目数 / 单条目 / 总量都有上限），逐条 **CRC-32**，
  路径穿越 / 盘符 / 绝对路径 / 反斜杠 / `..` 段一律白名单拒绝；exe 还要过**平台魔数**
  （Windows `MZ` / Linux `ELF` / macOS Mach-O）✓。
- **安装目录 / 备份 / 用户数据**：安装目录 = 当前 exe 所在目录；备份放 `<安装目录>/backup/`；
  **绝不碰用户数据目录**（`platform::data_dir()`）✓。安装目录不可写（例如装在 Program Files 又没权限）时
  `apply` 会明确失败、**不动程序** ✓。
- **检查 / 下载（第一块，没变）**：解析 GitHub `/releases`（draft 忽略、正式通道只收非 prerelease ✓）、
  按产物名挑本机资产（只认 `stardust-flash-link-<版本>-<目标>.(zip|deb|dmg)`，**宁可不更新也不装错平台** ✓）、
  有界下载（`size × 1.5`）、SHA256 校验（**摘要缺失即拒绝**，与 2.x I10 / P0-2 同口径 ✓）；
  成功后原子 rename，失败清理 `.part` ✓。
- **通道语义**：`release` 只收正式版；`preview` 收 preview/rc/release；`snapshot` 全收（`rc` 收 rc/release）。
  实际通道 = 配置 `update_channel`（`--channel` 可临时覆盖）；`auto_update_enabled` 只影响「自动」，
  手动检查照跑 ✓。
- **HTTPS 怎么来的**：核心的 `net` 是手写明文 HTTP（零依赖），GitHub 必须 HTTPS →
  走**系统自带的 curl**（`drcom_core::net::CurlHttp`；std 没有 TLS，R1 又不许加 crate）。
  Windows 10 1803+ / macOS / 主流 Linux 都自带；找不到时给可读中文错误 ✓。
  （Windows 上若吊销检查服务器被校园网挡住，会自动重试一次 `--ssl-no-revoke`，证书链与域名仍然校验 ✓。）
- **已知限制**：GitHub API 匿名限流 **60 次/小时**（403 会提示「过一会儿再试」）；
  2.x 的 `*.exe` 资产不会被 3.0 认（前缀 / 平台标签都对不上 ✓）；
  **Linux / macOS 的自我替换只有单测 + 交叉编译覆盖**，GUI 就地升级尚未真机验证 ✗
  （多程序替换 / 回滚同样是单测覆盖，Windows 真机验证待做）。
- **预览版 Release**：由 CI 手动发布（Actions →「3.0 预览构建」→ Run workflow → 勾选 `publish_release`），
  产物是 prerelease `v3.0.0.0-preview.N`，资产名与 `pick_asset` 的匹配规则逐字对应 ✓；
  发布前需先把 `APP_CHANNEL_SEQ` +1（步骤见 `docs/VERSIONING.md` §4.6）。
  正式用户的自动更新**收不到**预览版（R6 / `is_prerelease`）✓。

### 排障：一键脱敏诊断包

出问题时不用截图 + 口述，直接生成一个包发给别人 ✓：

```bash
stardust-flash-link diagnostics --out diag.zip   # 忽略路径则放数据目录
```

包里是：`说明.txt`、`status.json`、`config.sanitized.json`（**账号只留前 4 位**）、
`metrics.json`（近 N 天成功率）、`logs/campus_login.log`（最多 400 行，**逐行脱敏** ✓）。

> **只用界面的用户现在也有这两份数据了** ✓（审计 P2-5）：GUI 自带的后台检查每次**真正检查**后
> 都会往 `logs/campus_login.log` 追加一行（与 CLI / `run` **逐字同格式**：
> `2026-11-22 03:12:00Z [ok] 已在线，无需登录`），`metrics.json` 就是从同一份日志现算的 ✓。
> 以前界面只把结果画在窗口里、从不落盘 ✗ —— 只开 GUI 的机器上诊断包里的日志永远是空的、
> 统计全是 0。现在两条路径都调 **同一个** `drcom_core::runtime::check_once`
> （守卫 → 账号 → 密码 → 一次检查），等级 / 文案不再有第二套口径 ✓。
> 注意：`auto_check_enabled` 关掉或配置读不出来时**不写日志**（那不是一次检查，别刷屏 ✗）。

铁律：**密码永远不进包** ✓；日志里任何 URL 都会被擦成 `<url>` ✓（有单测 + 真机双向验证 ✓；
包用第三方工具校验为**标准 ZIP** ✓：本机验证用 Python `zipfile.testzip()`，CI 里 Linux 用
`unzip -t`、Windows 用 `Expand-Archive`（.NET `ZipFile`）；`SHA256SUMS` 还会被 `sha256sum -c` /
`Get-FileHash` 外部复核 ✓ —— 自研 SHA-256 / ZIP 绝不在自家实现里自证 ✓（审计 P2-7）。

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
stardust-flash-link profile ssids  # 当前 / 保存过 / 附近可见的 Wi-Fi 名（纯只读 ✓，照抄用）
stardust-flash-link run --once     # 日志里会写：位置自适应：切到方案「…」（后缀 …）
```

> 💡 **Wi-Fi 名不用手打**：`profile ssids` 会列「当前 Wi-Fi / 本机保存过 / 附近可见」三个清单
> （**纯只读**：不读配置、不写盘 ✓；macOS 上「附近可见」读不到会如实写读不到 ✗ 不编造），
> 复制进 `--ssid` 或设置里即可。设置界面本身**没有**「点选 Wi-Fi」的控件（审计 P3-7 把
> 旧注释里那个不存在的承诺纠正了 ✗），所以这个只读命令就是正式出口 ✓。

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
  platform.rs          数据/配置/日志目录、服务方式、便携模式判定（**只认 portable.marker** ✓）
  runtime.rs           一次检查的**共享入口**（CLI / GUI 后台检查同一条链路 + 业务日志落点 ✓）
  net.rs               自写 HTTP（明文，零依赖）+ 系统 curl 的 HTTPS（更新用，见 CurlHttp）
  update.rs            自动更新第一块：解析 /releases、挑平台资产、有界下载 + SHA256 校验
  update_apply.rs      自动更新第二块 + M4 收尾：自我替换 / 回滚（多程序一起换、全有或全无、rename 舞步、stale 清理、拉起新进程）
  zipread.rs           只读 ZIP（zipwriter 的对偶）：只收 store、逐条 CRC + 路径白名单
crates/drcom-cli/      无界面可执行（headless 优先：Linux 服务器/树莓派要它 ✓）
  main.rs              CLI 子命令（version/status/login/update/…）
  server.rs            本地控制 API（只绑回环 + 安全头 + 1 MB 上限 + 有界排空）
```

## 硬规则（改代码前先读）

1. **核心不 import 任何 UI / 服务框架** —— 这样才能既跑在桌面 App 里，也跑在
   `systemd --user`（Linux）与 launchd（macOS）下 ✓；
2. **密码绝不进日志**：任何可能打印的字符串先过 `secret::scrub_url` ✓（有测试守着）；
3. **配置与 API 契约向后兼容**：3.0 直接读 2.x 的 `config.json` / `password.txt` ✓；
4. **只绑 127.0.0.1**：本地 API 永远不对外 ✓；
5. **开发期不升正式通道**：`APP_CHANNEL` 保持 `Preview`，等便携版成型再谈 ✓。
