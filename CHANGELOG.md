# 更新日志

本项目的所有重要变更都会记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## v2.0.12.0 (feature) — 2026-09-28 · 代号 `Sirius`（天狼星）

> 前两版都在修「升级」这条链（先能**发现**坏、再能**回去**）。这一版补日常运维的两件小事：
> 日志不再无限长大 ✓；出问题时能一键导出**脱敏**诊断包，不用再截图 + 口述 ✓。

### ✨ 新增：日志自动轮转
- 业务日志 `logs\campus_login.log`：`logging.FileHandler`（无限追加）→
  `RotatingFileHandler`（**5 MB × 3 份**，合计 ≤ 20 MB）✓ ——
  README 里那句「持续追加**不自动轮转**，可随时手动清理」终于可以删掉了 ✓；
- 升级日志 `logs\upgrade.log`：新增轮转（**2 MB × 2 份**）✓ ——
  「查看升级历史」只读尾部，攒到 MB 级毫无意义 ✗；
- **统计不因轮转断档** ✓：`metrics.py` 的 `collect_lines()` 改成按
  「`.3 → .1 → 当前`」由老到新拼接 ✓（以前只认 `.1`）—— 否则轮转一发生，
  「近 7 天在线率 / 掉线次数」就会凭空少一截 ✗（有行为级断言钉住顺序 ✓）。

### ✨ 新增：一键诊断包（脱敏）
- 新增 `GET /api/logs`（逐个体积 + 合计 + 轮转提示）与
  `GET /api/diagnostics`（内存里生成 zip，直接下载）✓；
- 「关于」面板新增「日志与诊断」卡片：占用一目了然、「刷新占用」、
  **「下载诊断包（已脱敏）」** ✓；
- 包内：`README.txt`（写明包里有什么、脱敏了什么）、`summary.json`（版本 / 运行环境 /
  服务状态 / 配置摘要）、`config.json`（账号已打码）、`logs/*`（各日志尾部，每个 ≤ 512 KB）✓；
- **脱敏**（都有断言）：账号 → 「前 4 位 + ******」✓（配置与日志里都替换）；
  MAC → 前 4 位 + `********` ✓；密码 → `***`（正常情况下日志里不该有，这是兜底）✓；
  **`password.txt` 永不入包** ✓；内网 IP 与 Wi-Fi 名保留（排障必需）并在 `README.txt` 里写明 ✓。

### 🧪 自检
- `_smoke_static.py` **269/269** ✓：新增 **13 条**，其中 **7 条行为级** ——
  - 真写 30 行把 upgrade.log 顶过（临时调小的）上限 → `.1` / `.2` 如期出现、
    当前文件重新变小、**`.3` 不存在**（只留 2 份）✓；
  - 造 `x.log` / `.1` / `.3` 三个文件 → `collect_lines()` 顺序必须是 `['L3','L1','L0']` ✓；
  - 真跑一次 `_build_diagnostics_zip()`：断言 zip 组成 ✓、**账号打码**（配置与日志都替换）✓、
    MAC 打码 ✓、**密码明文不在包里且被替换成 `***`** ✓、`has_password` 为真 ✓、
    内网 IP 保留 ✓、README 写清脱敏范围 ✓；
  - `GET /api/logs` 形状（逐个体积 + 合计 + 轮转提示）✓；前端接线（卡片 / 下载链接 / 两个路由）✓；
- 又用**真实 HTTP 链**跑了一遍（临时起一个带 `_Handler` 的小服务，手工挂上生产里 `_attach`
  注入的那几个名字）：`GET /api/logs` 200 ✓、`GET /api/diagnostics` 200 +
  `Content-Type: application/zip` + `Content-Disposition: attachment; filename="drcom-diagnostics-vX-….zip"` ✓，
  解开 zip 后日志里 `账号 → 2023********` / `MAC → E25B********` / `密码 → ***`、
  内网 IP `172.16.80.3` 保留、**密码与真实账号都不在包里**（脚本自检输出 `密码泄漏: False`）✓；
- `_smoke_http.py` **27/27** ✓（请求拦截层未动；两个新端点都是 GET，写保护规则不变）。

## v2.0.11.0 (feature) — 2026-09-28 · 代号 `Sirius`（天狼星）

> v2.0.10.0 的看门狗解决了「**发现**」：升级把服务弄挂了，收尾会探 `/api/health` 并报出来 ✓。
> 这一版补上「**回得去**」：探测两轮都不通过时，执行器**自动**还原升级前的代码；
> Web UI 里也能手动一键回滚 ✓。

### ✨ 新增：整目录备份 + 自动/手动回滚（P6-6 / 路线图 #16）
- **备份从「一个文件」变成「整目录」**：旧实现只复制 `联网_service.py`（v2.0.2 拆成 9 个模块后
  另外 8 个回不去 ✗），而且落在 `%TEMP%`（会被磁盘清理删掉 ✗）。现在升级前把
  `ROLLBACK_MODULES`（= 随包分发的 9 个 `.py`，断言与 `setup.iss` 清单**双向一致** ✓）
  备份到 `{app}\backup\<旧版本>\`，并写 `manifest.json`（清单 + 逐个 sha256 + 时间）✓。
  **备份失败就不升级** —— 宁可这次不升，也不能在「回不去」的状态下换代码 ✓。
- **自动回滚**：执行器在「首次探测 + 重启重试」都不健康之后，再做一轮
  「停服务 → 校验并还原备份 → 起服务 → 再探」✓，结论落进 rc（`rollback=OK|FAIL|SKIP`）
  与 `upgrade.log`（中文，写清还原了哪一版、几个文件）✓。
  回滚后那次探测的 `health=` 会作为**最后一条**覆盖前面的结果 —— 一眼看得出「回滚之后活了没」✓。
- **手动回滚**：新增 `GET /api/rollback`（可用版本列表）与 `POST /api/rollback`
  （`{version?}`，默认最新）✓；Web UI「配置 → 自动升级 → 版本回滚」有下拉 + 一键回滚（带确认框）✓。
  接口与自动回滚**跑的是同一份回滚脚本** ✓ —— 手测到的就是自动会跑的那条路径 ✓。
  成功后服务自动重启（内存里还是新代码，不重启不生效）✓，并清掉「升级尝试记录」，
  免得旧代码的启动钩子误报「上次升级未生效」✓。

### 🔒 回滚的三条铁律（都有断言）
1. **只还原代码**：`config.json` / 密码 / 方案一律不动 ✓
   （备份里那份 `config.json` 只作人工比对，不进回滚清单 ✓）；
2. **fail-closed + all-or-nothing**：清单缺失 / 备份被改坏（sha256 不符）/ `--expect` 版本不符 /
   清单里出现带路径分隔符的文件名 → **全部拒绝**，且**一个文件都不写** ✓；
   校验通过后逐个复制，并再验一遍目标文件 sha256 ✓；
3. **保留策略**：7 天过期清理，但**最新 2 份永远保留** ✓；卸载时 `[UninstallDelete]` 一起清掉 ✓。

### 🔴 顺带修掉：启动钩子会被「换备份方案」整段卡死
旧启动钩子开头是「`%TEMP%` 里没有 `drcom_backup_*.py` 就直接 `return`」——
备份挪到 `{app}\backup\` 之后 `%TEMP%` 里再也不会出现那种文件 ✗，于是
**尝试确认 / 执行器结果 / AppExit 自愈 / 托盘自启会被整段静默跳过** ✗。
现在旧式备份只剩「提示脚本变过」一个用途，其余步骤一律执行 ✓（行为级断言钉住 ✓）。

### 🧪 自检
- `_smoke_static.py` **256/256** ✓：新增 **30 条**，其中 **20 条行为级** ——
  - 把生成的回滚脚本**真跑起来**：正常路径（文件内容逐一相等 + `rollback=OK` + 中文日志）、
    备份被改坏（拒绝 + app 原样未动）、`--expect` 不符、清单缺失、非法文件名
    （并检查 app 目录**外面**没有被写出文件）✓；
  - 接口层：`GET /api/rollback` 形状（不把本机绝对路径抛给前端）、忙时 409、版本串非法 400、
    找不到 404、目标=当前版本 400、默认取最新并**真的还原** ✓；
  - 整目录备份：落盘位置 / 清单 sha256 可校验 / `config.json` 不进回滚清单 / 可选模块缺失不算失败 ✓；
  - 执行器：回滚调用顺序（`retry` → `auto` → `after-rollback`）、缺件落 `rollback=SKIP`、
    收尾删回滚脚本、不外泄凭据 ✓；
  - 启动钩子：**没有旧式备份也照常跑完**（回滚清单 / AppExit / 托盘自启都写进日志）✓；
- 本地另做了**执行器全链路演练**（桩 installer / 桩 sc / 桩 schtasks + 真探针 + 真回滚脚本 +
  一个"服务探测不到"的死端口）：rc 依次落下
  `installer_rc=0 / health=FAIL / health=FAIL / rollback=OK / rollback_version=1.2.3 / health=FAIL / service=RUNNING` ✓，
  `upgrade.log` 留下「版本回滚（auto）：已还原 v1.2.3 的 7 个代码文件到 …（配置与密码未改动）」✓，
  代码**真的换回了旧版** ✓，执行器 / 探针 / 回滚脚本**三个都自删** ✓；
- `_smoke_http.py` **27/27** ✓（请求拦截层未动，本版只**新增**两个端点）。

## v2.0.10.0 (feature) — 2026-09-28 · 代号 `Sirius`（天狼星）

> 上一版最后自己写下的待办：**「升级执行器的看门狗改成探 HTTP 端口」** ✓。这一版做完了 ——
> 判据从「nssm 的 wrapper 活着」换成「`/api/health` 真的答话」✓，
> 并把「服务没起来」从**静默变砖**变成**自动重启重试 + 写明原因的告警** ✓。

### 🔴 修复：升级执行器的 `installer_rc=` 从来没落盘（cmd 的 `0>` 句柄陷阱）
写这一版的行为级验证时顺手挖出来的老坑 ✗ —— 从 v2.0.4.2 起这条一直是坏的：
`echo installer_rc=%ERRORLEVEL%>"%RC%"` 里的**数字紧贴 `>`**，cmd 会把 `0>` 当成
**句柄 0 重定向** ✗ → rc 文件被写成**空文件**、`installer_rc=` 的文本反而跑进了 stdout ✗，
于是启动钩子里那条「installer 非 0 退出码 → WARN」**一直是死代码** ✗
（真机 `upgrade.log` 里也只可能出现 `service=...`，不会出现 `installer_rc=...`）。
改成把重定向写到命令**前面**（`>>"%RC%" echo installer_rc=%ERRORLEVEL%`）✓ ——
行尾不再有数字，这类陷阱从语法上就不存在了 ✓；并补了断言（含一条**真跑 `cmd`** 的行为级断言）✓。

### ✨ 新增：升级收尾改探 HTTP 健康端点（看门狗 B）
- 执行器跑完 installer 后，用**随包分发的内嵌 Python** 探
  `http://127.0.0.1:<ui_port>/api/health` ✓（端口取自 `config.json` 的 `ui_port`，
  非法值/缺项退回 8848 ✓）：
  - 拿到 `{"ok": true}` → 落 `health=OK` ✓ —— **这才是「服务真的活了」的证据** ✓
    （`sc query` 说的只是 wrapper：nssm 配的是 `AppExit=Ignore`，
    里面的 Python 崩了它既不重启也不报错 ✗）；
  - 探不到 → **`sc stop` + `sc start` 重启一次**，再给一个窗口重探 ✓
    （真机上服务崩了没人管，这一步得由执行器来做 ✓）。
- 结论**写两处** ✓：
  - `%TEMP%\drcom_apply_update.rc` 追加 `health=OK|FAIL`（机器读；**多行时以最后一条为准** ——
    「先 FAIL 后 OK」= 重启之后自己活过来了 ✓）；
  - `logs\upgrade.log` 追加一行带时间戳的中文结论（人读）✓ ——
    **服务真起不来时 Web UI 也打不开，那行日志就是唯一的告警面** ✓
    （托盘右键「打开日志目录」即可看到 ✓）。
- 启动钩子把这行结论带回升级收尾记录 ✓：日志里现在一眼能看出
  `升级执行器结果：installer_rc=0 / service=RUNNING / health=OK` ✓。

### 🔒 边界（都写进断言了）
- 探针脚本**自包含**：只用标准库、**不 import 任何项目模块** ✓ ——
  被升级搞坏的正是那些模块，探针必须比被探的服务更「耐活」✓
  （行为级冒烟直接把它跑起来验证 ✓）；
- 探针**禁用系统代理**（`ProxyHandler({})`）✓：否则 IE / 环境变量里配的代理会让
  「连得上代理、连不上 127.0.0.1」被误判成服务没起来 ✗；
- **绝不在启动钩子里等 `health=` 那一行** ✓：探针探的正是本进程正在启动的 HTTP 服务，
  在这里等它就会**互相等成死锁** ✗ —— 所以「人读」那半由探针自己写 ✓（代码里留了注释防回归 ✓）；
- 内嵌 Python / 探针脚本缺失（安装目录被破坏等）→ 执行器落 `health=SKIP` 并
  **退回旧判据**（只看服务状态）✓，不误报失败 ✓；探针脚本由执行器收尾时自删 ✓，
  启动钩子也会清陈旧残留 ✓。

### 🧪 自检
- `_smoke_static.py` **226/226** ✓：新增 **23 条**，其中 **11 条是行为级** ——
  把生成的探针**真的跑起来**：对着一个在跑的假 `/api/health` 服务断言
  「退出码 0 + rc 落 `health=OK` + upgrade.log 有中文结论」✓；
  对着空端口断言「退出码 1 + `health=FAIL` + 中文告警 + 遵守 `--wait` 窗口不无限等」✓；
  再用合成 rc 跑一遍启动钩子的收尾读取（`FAIL` / `先 FAIL 后 OK` / `SKIP` / 没有 health 行）✓；
- 其余断言钉住执行器接线：「带端口调探针」「失败先停+起再探」「缺件落 SKIP」
  「收尾删探针」「重定向前置」「`service=` 格式不变」✓，以及 `ui_port` 归一（含 `bool` / 越界 / 非数字）✓；
- 另在本地做过**执行器全链路演练**（桩 installer / 桩 sc / 桩 schtasks + 真探针 + 一个真在跑的
  `/api/health` 服务）：rc 最终落 `installer_rc=0 / health=OK / service=RUNNING` ✓、
  安装日志归档回 `{app}\logs` ✓、执行器与探针双双自删 ✓（上面那条「重定向前置」的现场依据 ✓）；
- `_smoke_http.py` **27/27** ✓（本版没动请求拦截层与任何 API）。

### 🧪 真机验证（补记：2026-09-28，v2.0.9.1 → v2.0.10.0 实机升级）
本机（装的正是 v2.0.9.1）从 Web UI 点「立即检查更新 → 立即升级」跑了一次真实升级，
`logs\upgrade.log` 关键行（逐字）：

```
[10:31:13] [INFO] 检查完成：发现新版本 2.0.10.0
[10:31:20] [INFO] 检测到新版本 2.0.10.0（当前 2.0.9.1），开始下载
[10:31:23] [INFO] 下载完成：11898227 字节
[10:31:23] [INFO] SHA256 校验通过
[10:31:23] [INFO] 备份到 C:\Windows\TEMP\drcom_backup_20260928_103123.py
[10:31:23] [INFO] 升级透明：AppExit 保持原值 Ignore（不修改注册表）
[10:31:23] [INFO] installer 已启动（方式=schtasks，PID=None，第 1 次尝试）
[10:31:28] [INFO] 升级成功确认：已运行 v2.0.10.0（目标 2.0.10.0，尝试 1 次）
[10:31:30] [INFO] 清理已安装的安装包：DrcomAutoLogin-Setup-v2.0.10.0.exe
[10:32:01] [INFO] 检查完成：已是最新 2.0.10.0
```

- `logs\campus_login.log`：`[10:31:28] Dr.COM 自动登录服务启动（Web UI 配置版 v2.0.10.0 "Sirius"）` ✓
  （启动后 5 秒内即「已在线，无需登录」→ 登录链路照常 ✓）
- `logs\installer-silent.log`：`Installation process succeeded.` + `Need to restart Windows? No` ✓
- `Get-Service DrcomAutoLogin` → **Running / Automatic** ✓；安装目录 `version.py` → `2.0.10.0` ✓
- `logs\service_stderr.log` 本次启动**没有**新 traceback ✓（末尾只剩 schtasks
  `/create /sc once /st 00:00` 的两行 `警告: 因为 /ST 早于当前的时间…` —— 这是**设计如此**：
  该任务只靠 `/run` 立即执行、绝不自行触发；同批的 `成功: 成功创建计划任务 …` 落在
  `service_stdout.log` ✓）

**新看门狗在真机上的证据**（升级完成后，用装好的 2.0.10.0 代码现场生成探针，对**真在跑的服务**探一次）：

```
probe generated: 133 lines
health=OK http=200 ok=true version=2.0.10.0
```

> ⏱ **时序提醒**：这一次升级用的执行器**仍是 v2.0.9.1 那版**（执行器总是由「升级前那份代码」生成），
> 所以本次 `upgrade.log` 里**不会**出现 `health=`（新判据）与 `installer_rc=`（本版修的坑）——
> 二者会在**下一次**升级（2.0.10.0 → 更高版本）自动出现。
> 想在真机上立刻验到**执行器**这一层：用 `_ui_redesign\verify-executor-v2.0.10.0.ps1 -Run`
> （需管理员；走生产同一路径静默重装同版本，期望 rc = `installer_rc=0 / health=OK / service=RUNNING`）。

## v2.0.9.1 (hotfix) — 2026-09-28 · 代号 `Sirius`（天狼星）

> **v2.0.9.0 的用户如果 Web UI 打不开、托盘说「服务未响应」**：重新运行 v2.0.9.1 安装包即可恢复
> （配置、密码、方案都不动）。v2.0.9.1 之后的版本不会再犯这个错（已加静态守卫 ✓）。

### 🔴 紧急修复：函数内 import 遮蔽模块级名 → 服务启动即崩
v2.0.9.0 的真机升级把服务弄挂了 ✗，`logs/campus_login.log` 里一目了然：

```
[09:48:00] Dr.COM 自动登录服务启动（Web UI 配置版 v2.0.9.0 "Sirius"）
[09:48:00] [ERROR] main 未捕获异常: cannot access local variable '_protocol_mod'
  File "...\联网_service.py", line 651, in main
    _protocol_mod._attach(
UnboundLocalError: cannot access local variable '_protocol_mod'
```

**根因**：为了让协议层拿到「按 SSID 自动切方案」的回调 ✓，我在 `main()` 里写了一句
`import protocol as _protocol_mod` ✗ —— 而 **Python 里函数内任何 `import` 都会让该名字在
整个函数作用域内变成局部变量** ✗，于是同一函数里**更早**的那行
`_protocol_mod._attach(...)`（v2.0.2 起就有的老代码 ✓）变成"未赋值先使用" ✗。

**修法**：删掉那句 import ✓（`_protocol_mod` 在模块顶部已经导入过 ✓，直接用即可 ✓）。
另外在代码里留了 ⚠️ 注释，防止下次又有人"顺手补一句 import" ✗。

### 🛡 新增守卫（这类错静态检查能拦，就该拦）
`_smoke_static.py` 新增两条：
- **服务里不允许出现「函数内 import 模块别名」**（`^\s+import \w+ as _\w+`）✗ ——
  这条规则**正好命中本次事故** ✓；
- 自动切换回调必须挂在**模块级** `_protocol_mod` 上（不重复 import）✓。

### 🪞 顺带承认一个检测盲区（下一步要补）
两次事故（v2.0.8.0 回滚、v2.0.9.0 启动崩）之所以"升完才发现"，是因为自动升级的**收尾判定太弱** ✗：
它只看 `sc query` 的 **服务状态**（那是 nssm 的 wrapper ✓），而 wrapper 活着 ≠ 里面的 Python 进程活着 ✗
（nssm 配的是 `AppExit=Ignore`，app 死了它也不重启也不报错 ✗）。
**下一步（v2.0.10.x）**：升级执行器的看门狗改成**探 HTTP 端口**（`http://127.0.0.1:<ui_port>/api/health` ✓）——
那才是"服务真的活了"的证据 ✓，也才能让这类事故变成"自动重试 / 明确告警"而不是"静默变砖" ✗。

### 🧪 自检
`compileall` ✓ · `_smoke_static.py` **203/203** ✓ · `_smoke_http.py` **27/27** ✓ ·
ISCC `packaging/setup.iss` **Verification successful** ✓。

## v2.0.9.0 (feature) — 2026-09-28 · 代号 `Sirius`（天狼星）

> 笔记本的日常：教室、宿舍、家里三处跑，而**只有校园网里才该尝试登录** ——
> 可"现在在哪"这件事以前得手动去改守卫配置 ✗（或者干脆关掉守卫、让它到处空跑 ✗）。

### ✨ 新增（B5）：配置方案（一键切换 + 可按 Wi-Fi 名自动切）
- **方案 = 位置相关字段的快照**：认证网关 / 检查间隔 / 网络位置守卫（开关 + Wi-Fi 白名单 +
  网段白名单）。**账号、密码、升级设置不进方案** ✓（密码永远只在 `password.txt` ✓）。
- 配置页新增「配置方案」卡片：选中 → **应用** / **删除**；填名字 → **用当前配置保存**
  （最自然的用法：把当前调好的配置存成「教室」✓）；还能填「自动匹配的 Wi-Fi 名」✓。
- **自动方案**：给方案填了 Wi-Fi 名、并打开「按 Wi-Fi 名自动切换」开关 ✓ →
  每次检查发现当前 Wi-Fi 命中某个自动方案就自动切过去 ✓（日志留痕 ✓）。
  总开关**默认关** ✓ —— 免得"我手动选的方案被系统改掉" ✗。
- 接口：`GET /api/profiles`、`POST /api/profiles/save|activate|delete|auto` ✓
  （**新增**端点，老接口与老配置都不动 ✓）。

### 🔒 安全与边界（都写成断言了）
- 方案只允许 `PROFILE_KEYS` 这几个键 ✓ —— 想塞 `account` / 密码之类的直接报错 ✗；
- 切换是**先全量校验、再原子写盘** ✓：拿合并结果跑一遍 `_validate_config` ✓，
  不通过就整体拒绝、原配置一动不动 ✓；
- 方案名 ≤24 字符且不含 `\/:*?"<>|` ✓，最多 12 个方案 ✓，匹配名自动去重限量 ✓。

### 🧪 自检
- `_smoke_http.py` **27/27**：新增 9 条**端到端**（临时 config 真跑 save → 列表 → activate →
  auto → delete，断言"账号不动""删当前方案只清标记""坏名字 400"…✓）；
- `_smoke_static.py` **201/201**：新增 14 条（5 条接线 + 9 条纯逻辑：快照只含位置字段 /
  未知字段被拒 / 应用不改原对象 / 校验不过整体拒绝 / 按 SSID 挑方案大小写不敏感…✓）；
- 其中一条专门钉**顺序**：自动切换必须排在**守卫判定之前** ✓
  （否则本次检查仍按旧方案的白名单走 ✗）。

### 其它
- `protocol.py` 只认一个回调（`_set_auto_profile`）✓，不 import `profiles` ✓ —— 分层不变 ✓；
- 配置方案随 `config.json` 一起被导出 / 导入 ✓。

## v2.0.8.1 (hotfix) — 2026-09-28 · 代号 `Sirius`（天狼星）

> **如果你是 v2.0.8.0 的用户且 Web UI（`http://127.0.0.1:8848`）打不开、托盘提示「服务未响应」**：
> 直接**重新运行 v2.0.8.1 安装包**即可恢复 —— 它会停服务、补齐文件、再启动服务，配置与密码都不动。

### 🔴 紧急修复：安装器中途回滚 → 安装变「一半新一半旧」
v2.0.8.0 装到一半就中止并回滚了 ✗，`logs/installer-silent.log` 原文：

```
09:25:48.922  DeleteFile: The existing file appears to be in use (5). Retrying.
09:25:52.969  D:\...\python\libcrypto-3.dll  拒绝访问 → User canceled the installation process.
09:25:52.969  Rolling back changes.
09:25:52.971  Deleting file: D:\...\metrics.py        ← 本次新加的模块被回滚删掉
```

`logs/service_stderr.log`：`ModuleNotFoundError: No module named 'metrics'` → 服务主进程秒退，
而 nssm 配的是 `AppExit=Ignore`（不自动重启 ✗）→ **Web UI 端口整个消失** ✗。

**根因链**：
1. 托盘进程由 `{app}\python\pythonw.exe` 启动，它 `import urllib.request → ssl`，
   因此**锁住**了 `{app}\python\libcrypto-3.dll`；
2. 安装器要替换这个 DLL ✗ → 被占用 → 重试 5 秒仍然失败；
3. 静默安装（`/VERYSILENT`）遇到这种冲突默认 **Abort** ✗ → **回滚**；
4. 回滚只删「本次新装的文件」→ 新增的 `metrics.py` 被删 ✗，
   而 `联网_service.py` / `version.py` 已经换成新版 ✗ → 半新半旧 ✗；
5. 新服务在 **import 期**就崩 ✗ → 端口消失 ✗。

**两条腿修掉**：

- **装前请走托盘** ✓：新增 `packaging/stop-tray.ps1`，由 `PrepareToInstall` 在
  **写任何文件之前**调用（`ExtractTemporaryFile` 取脚本 → PowerShell 执行 ✓）。
  脚本只杀「命令行里同时含 `tray.py` 与 `{app}`」的 `pythonw.exe` ✓，**绝不按进程名一刀切** ✓
  （升级期间托盘图标会消失十几秒，随后由安装器重新拉起 ✓）。
- **服务侧可选模块降级** ✓：`import metrics` 包进 `try/except ImportError` ✓ ——
  缺文件时只记一行 WARNING、面板显示空白，**登录与 Web UI 照常工作** ✓✓。

### 🛡 新增守卫（这一类问题以后靠 CI 拦）
- **打包完整性** ✓：`_smoke_static.py` 把 `联网_service.py` 里 `import` 的本地模块与
  `setup.iss` 的 `Source: "..\X.py"` 清单**逐一比对**，漏一个就判失败 ✓
  （本次的 bug 恰好就是这个 ✓）；
- 装前请走托盘的接线（`dontcopy` + `ExtractTemporaryFile` + `PrepareToInstall` + 脚本过滤条件）✓。

### 🧪 自检
`compileall` ✓ · `_smoke_static.py` **187/187** ✓ · `_smoke_http.py` **18/18** ✓ ·
`ISCC packaging/setup.iss` **Verification successful** ✓（新增 Pascal 代码编译通过 ✓）。

## v2.0.8.0 (feature) — 2026-09-28 · 代号 `Sirius`（天狼星）

> 「最近网络到底稳不稳？」以前只能自己翻日志。这一版把它做成一块面板 ——
> 而且**不新增任何状态文件**：数据全部从已有的 `logs/campus_login.log` 现算。

### ✨ 新增（B4）：连接质量面板
状态页新增 4 个指标 + 一张近 7 天柱状图（纯 CSS，不引入图表库）：

| 指标 | 口径 |
| --- | --- |
| **在线率** | `(已在线 + 重登成功) / 有效周期`；**守卫跳过的周期不计入分母**（不在校园网不算不健康） |
| **掉线重登** | 近 7 天「登录成功」的次数 —— 每次成功都意味着此前掉过线 |
| **平均恢复耗时** | 重登周期「开始检查 → 登录成功」的均值（即**登录本身**要多久） |
| **当前延迟** | 现在到校园网关 `host:80` 的 **TCP 握手**耗时（不用 ICMP —— 校园网常禁 ping） |

数据来源与口径：

- 每个检查周期在日志里都有固定走向：`开始检查 (reason=…)` → `已在线，无需登录` /
  `登录成功: …` / `登录失败: …` / `等待 Ns 后 … 仍不可达` / 守卫跳过 → 解析成周期再统计；
- `GET /api/metrics?days=7`（1–30，默认 7）；只读日志**尾部**（≤4MB），
  日志涨到几百 MB 也不会把接口拖住；
- 新增 `metrics.py`：解析与统计**全是纯函数**，磁盘 / 网络只在两个薄壳里
  （读日志、量延迟），由 `联网_service.py` 注入；
- **优雅降级**：模块没注入 / 日志被删 / 统计抛异常 → 只回一份空指标 + 原因，
  绝不把状态页打成 500；
- `protocol.py` 的「网络已可达」补上耗时（`耗时 0.42s`），面板才能算「平均可达耗时」；
  **老日志没有这个字段也照常解析**（字段留 `null`，界面显示「可达耗时从本版起记录」）。

### 🧪 自检
- `_smoke_http.py` **18/18**：新增 9 条**端到端**断言 —— 合成一份最小日志后，
  直接断言接口算出来的数字：`checks/online/relogin/fail = 3/1/1/1`、
  `uptime_pct = 66.7`、`avg_recover_ms = 2000`、`avg_reach_ms = 250`、7 天序列、
  坏参数回退、日志缺失、模块未注入（全程离线 ✓）。
- `_smoke_static.py` **183/183**：新增 10 条（5 条接线 + 5 条行为级：三种走向、
  窗口过滤、老格式容忍、空输入）。
- 真机（本机 9 天日志）实算：**806 次检查 / 795 次已在线 / 10 次掉线重登 /
  1 次不可达 / 在线率 99.9% / 平均恢复 1.9s / 平均重试 1.01 次 / 当前延迟 27ms**。

### 🐞 顺手修掉两个「坏输入就崩」（都是真跑测试时才暴露的）
1. `metrics._day_of()`：Windows 上 `time.localtime()` 遇到越界时间戳会抛 `OSError`
   → 现在容错返回空串（少画一根柱子，好过整页 500）；
2. `metrics.summarize(days=…)`：`?days=abc` 直接 `int()` 抛 `ValueError`
   → 现在回退默认值并夹到 `[1, 30]`。

## v2.0.7.1 (fix) — 2026-09-28 · 代号 `Sirius`（天狼星）

> v2.0.7.0 的真机验证里抓到的：**升级完之后，托盘还在跑内存里的旧代码** ✗。
> 也就是说「唤醒 / 换网重连」这类新功能要等到用户下次注销 / 重启才生效 ——
> 明明安装器又拉了一次托盘，为什么没用？

### 🔧 修复：升级后托盘自己换上新代码
根因是**单实例互斥体**：安装器升级时确实会再拉一次托盘 ✓，但老实例还握着
`Local\DrcomAutoLoginTray` ✗ → 新实例起来发现"已经有一个在跑"，立刻自己退了 ✗。
于是「文件已换、代码没换」，而且**完全静默**（图标在、通知在、就是旧版本）。

修法：让托盘盯住自己的脚本 —— 每 10 秒轮询时顺手比一次 `tray.py` 的
**(mtime, size) 指纹**（纯函数 `code_signature()`，读不到就返回 `None`），一变就：

1. 先记下新指纹 —— 万一这次没起来，也不会每 10 秒刷一遍日志；
2. **先 `CloseHandle` 放手单实例互斥体**（不放手新实例必然秒退 ✗ —— 这正是问题的根）；
3. `subprocess.Popen(..., creationflags=DETACHED_PROCESS, stdin/out/err=DEVNULL)`
   起一个新实例；
4. 自己 `DestroyWindow` 退出（撤图标 → 消息循环结束）。

失败即退化：起不来就记一行日志、把互斥体拿回来、继续用旧代码跑（下次升级再试）✓。

### 🧪 自检
`_smoke_static.py` 新增 7 条：3 条 `code_signature()` 行为级（读得到 mtime+size ✓ /
文件不在返回 `None` 不误判 ✓ / mtime 一变指纹就变 ✓）+ 4 条接线（轮询里检查并重启 ✓ /
重启前放手互斥体 ✓ / 独立进程且不继承控制台 ✓ / 失败把互斥体拿回来 ✓）。

本地端到端实测（临时目录跑真进程，改 mtime 模拟升级替换文件）：

```
测试托盘 PID(旧) = 46880
（改 tray.py 的 mtime，等一个轮询周期）
测试托盘 PID(新) = 54540            ← 换成新进程了
[09:07:23] 检测到 tray.py 已被升级 → 换上新代码重启托盘
[09:07:23] 托盘已启动（每 10s 轮询 http://127.0.0.1:8848）
```

## v2.0.7.0 (feature) — 2026-09-28 · 代号 `Sirius`（天狼星）

> 以前的「回到网络」体验：合盖睡一觉、插上网线、从教室 Wi-Fi 切回宿舍 ——
> 服务下一次检查可能要等**一整个检查周期**（默认 30 分钟）才把登录补上 ✗。
> 而托盘一直醒着（它是用户会话里的 GUI 程序）✓。

### ✨ 新增（B3）：唤醒 / 换网 → 立刻重连
事件源放在 **`tray.py`**（服务跑在 session 0，既没有窗口，也不该为这种事加线程）：

| 事件 | 来源 | 触发时机 |
| --- | --- | --- |
| 睡眠唤醒 | `WM_POWERBROADCAST`（`APMRESUMESUSPEND` / `AUTOMATIC` / `CRITICAL`） | **延后 4 秒**（`WAKE_SETTLE_SEC`）—— 刚醒时网卡 / 无线还没连上 |
| 网络变化 | `NotifyAddrChange`（iphlpapi，后台线程异步等） | 立刻（插网线 / 换 Wi-Fi / DHCP 换地址 / 唤醒后重新拿到地址） |

事件一到就 `POST /api/login` —— 服务侧自己判断「该不该登、能不能登」（网络位置守卫照样生效）。

- **去抖**：`RECONNECT_MIN_GAP_SEC = 5`（纯函数 `should_reconnect()`）。唤醒时「电源广播 +
  无线重连 + DHCP 续租」会连着来，不去抖会连打三四个登录请求。
- **不占消息循环**：`NotifyAddrChange` 在**后台线程**里等，到了才 `PostMessage` 叫醒主循环
  （`WM_NETCHANGE`）；唤醒走一次性定时器（`TIMER_WAKE`）延后 —— 绝不在 `WndProc` 里发请求。
- **失败即退化**：事件句柄创建失败 / API 返回异常码 / 线程抛异常 → 只记一行日志并结束线程，
  行为退回 `v2.0.6.x` 的 10 秒轮询，托盘主体不受影响。
- **可实测**：`tray.py --test-event wake|net` 真投递那条消息、走真实处理路径
  （该入口**故意**绕开单实例互斥体 —— 真实托盘通常正在跑）。

### 🧪 自检
`_smoke_static.py` 新增 9 条（3 条纯函数行为级 + 6 条接线）。其中一条正是本次**真跑才抓到的坑**：
`ctypes.wintypes` **没有** `OVERLAPPED` —— 直接用会 `AttributeError`，而它发生在
`_declare_win32()` 里 → **托盘启动即崩** ✗。现在自带 `class OVERLAPPED(ctypes.Structure)`，
并加断言禁止 `wintypes.OVERLAPPED` 写法。

真机实跑（`--test-event`，日志到秒）：

```
[08:58:44] 事件：网络地址变化 → 立即重连：已触发一次登录检查
[08:59:00] 事件：从睡眠唤醒 → 4 秒后触发重连
[08:59:04] 事件：从睡眠唤醒 → 立即重连：已触发一次登录检查
```

### 其它
- 定时器改成具名 id（`TIMER_POLL` / `TIMER_WAKE`），不再是裸 `1`。
- 接口、配置结构、服务名、`AppId` 全未动（PATCH +1），老配置与升级路径不受影响。

## v2.0.6.3 (fix) — 2026-09-28 · 代号 `Sirius`（天狼星）

> 上一版做真机验证时自己踩到的坑：先点「立即检查更新」、紧接着点「立即升级」→
> 界面照常弹出「升级已启动」，但 `upgrade.log` 里只有一行
> 「检查完成：发现新版本 2.0.6.2」，**升级根本没开始**。

### 🔧 修复：升级 / 检查接口不再「假成功」
根因是**接口先回车、后判断**：`POST /api/update/check|install` 都是
「无条件开一个后台线程 + 立刻回 `{"ok": true, "已提交…"}`」。而后台线程里
`_do_update_now()` / `_do_check_now()` 的第一步是抢升级锁（`_acquire_update_lock`），
抢不到就直接 `return {"ok": False, "error": "升级正在进行中"}` ——
**这个返回值没有任何人看**，因为 HTTP 响应早就发出去了。于是锁被占时：
接口报「已提交」、UI 弹「升级已启动」，实际什么都没发生。

修法：两个入口**先探锁，再回车**。

- `auto_update.py` 新增 `is_update_busy()` / `update_busy_message()`：只读、不阻塞、不改状态；
  **只认升级锁本身**（任务全程持有它），不认 `update_state` —— 状态可能是上一轮异常留下的
  残值（锁早已由 `finally` 释放），拿状态判「忙」会把接口永久锁死。
- `web_api.py` 两个入口拿到「忙」就回 **409** + 真实原因（复用当前进度文案，例如
  「任务进行中（检查 GitHub 最新版本...），请稍候再试」），并且**不再白启后台线程**；
  空闲时才维持原来的「200 + 已提交…」异步路径。
- 前端无需改动：`postUpdateJson` 本来就会解析 409 的响应体，`data.ok === false` 时
  走已有的错误分支，把真正的原因弹出来。

### 🧪 自检
`_smoke_static.py` 新增 11 条断言（6 条 `auto_update` 行为级 + 5 条 `web_api` 行为级，
用替身模块驱动，全程不联网）：空闲 / 忙 / 状态残值三种判定、忙时两个入口都回 409
且不动后台线程、空闲时仍回 200 并真的开跑。

## v2.0.6.2 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 真机升级验证时抓到：从 **2.0.4.4 自动升级到 2.0.6.1** 后，托盘文件装上了，但
> `HKLM\...\Run` 里的启动项**没有落地** —— 也就是说：手动装的人有托盘，**自动升级上来的人没有**。

### 🔧 修复：托盘自启项改由服务启动时对齐
根因是「**新任务没法靠旧版本传**」：自动升级的执行器是**上一个版本自己的** `auto_update.py`
（v2.0.4.4 只会传 `/TASKS=desktopicon,startservice`），Inno 于是把 v2.0.6.0 新加的 `trayicon`
当成「未选中」→ `Tasks: trayicon` 的注册表项被直接跳过 ✗。（和 v2.0.4.2 时 `desktopicon`
被摘掉是同一个坑，只是方向反了。）

修法两条腿 ——
1. **安装器**在 `ssPostInstall` 把「是否随开机启动托盘」写进
   `HKLM\SOFTWARE\DrcomAutoLogin\TrayAutostart`（按 `WizardIsTaskSelected('trayicon')`，
   静默安装一样有效；卸载时整键清掉）；
2. **服务**每次启动读这个开关，把 `HKLM\...\Run\DrcomAutoLoginTray` 对齐：
   缺了建、路径不对修、开关关闭删、托盘文件不存在清 —— 于是**任何升级路径都能自愈**
   （包括从很老的版本升上来、以及这台机器上已经出现的那种状态）。

判定逻辑抽成纯函数 `tray_autostart_action(want, script_exists, run_value, expected)`，
create / keep / delete 三条走向都有单测；`_ensure_tray_autostart_sane(dry_run=True)`
可以只看结论不碰注册表（排障用）。

### 🧪 测试
- 冒烟新增 9 条（6 条纯函数行为 + dry-run + 安装器开关 + 启动钩子接线），合计 **150/150**。
- 真机：`_ensure_tray_autostart_sane(dry_run=True)`（BASE_DIR = 安装目录）判 `action=create`、
  `want=True`、`current=None` —— 与实测的「启动项确实没落地」完全一致。

## v2.0.6.1 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 修 v2.0.6.0 里一个**只有真机升级才会暴露**的漏洞：托盘装上了，但真实用户登录时不会自启。

### 🔧 修复
- `fix(installer)`: **托盘的登录启动项从 `HKCU` 改挂 `HKLM`**。
  原因：自动升级的执行器是 `schtasks /create /ru SYSTEM`（v2.0.4.2 为了绕开 nssm 的 Job Object
  连坐才这么设计的），安装器那时以 SYSTEM 身份运行 —— **`HKCU` 指的是 SYSTEM 的配置单元
  （`systemprofile`）**，写进去等于石沉大海，真实用户登录时托盘根本不会启动 ✗。
  改挂 `HKLM` 后对每个登录用户都生效，谁装的、以什么身份装的都不影响；
  多用户同时登录也不会起两个 —— `tray.py` 用 `Local\DrcomAutoLoginTray` 互斥体
  （`Local\` = 每个登录会话各一个）保证一个会话只有一个托盘。
- `fix(tray)`: **卸载后不再留孤儿图标** —— 每轮轮询检查安装目录里自己的 `tray.py` 还在不在，
  连续两次（≈20 秒）都找不到就撤掉图标自行退出。之所以要「连续两次」：升级时安装器会替换这个
  文件，撞上被删的那一瞬间就退出反而糟糕。

### 🧪 测试
- 冒烟新增 2 条：`[Registry]` 段必须是 `Root: HKLM`（且段内不得出现 `Root: HKCU`）、
  托盘自救逻辑在位。合计 **141/141**。

## v2.0.6.0 (feat) — 2026-09-27 · 代号 `Sirius`（天狼星）

> B2：**托盘小程序 + 断线通知**。新组件，向后兼容（`PATCH` +1）。

### ✨ 新增
- `feat(tray)`: 新增 `tray.py` —— **登录会话里的托盘小程序**（安装器默认勾选「开机自动启动托盘」）。
  为什么不能由服务直接弹通知：服务是 LocalSystem、跑在 **session 0**，与用户桌面会话隔离，
  `Shell_NotifyIcon` / 气泡从服务里调出去等于扔进黑洞。所以必须由一个随登录启动的**用户进程**来做。
- 行为：每 10 秒轮询本机 `http://127.0.0.1:<ui_port>/api/status`（端口从 `config.json` 读，
  改过端口也能连上），状态迁移时弹气泡：
  | 迁移 | 通知 |
  | --- | --- |
  | 首次轮询 | **不弹**（开机时服务可能还在启动，别吓人）|
  | 在线 → 掉线 | ⚠️「已掉线，服务正在尝试重新登录…」|
  | 掉线 → 在线 | ✅「已恢复登录」|
  | 连续 3 次（≈30s）拿不到服务 | ⚠️「服务未响应」（偶发抖动不弹）|
- 右键菜单：**打开配置页** / **立即登录**（`POST /api/login`，带 `X-Requested-With: DrcomUI`）/
  **打开日志目录** / **退出托盘**；双击图标 = 打开配置页；鼠标悬停显示当前状态（含 Wi-Fi 名）。
- 单实例：`CreateMutexW`（`Local\DrcomAutoLoginTray`）—— 句柄随进程退出由内核释放，
  被任务管理器强杀也不会留残留锁。
- 日志：`%LOCALAPPDATA%\DrcomAutoLogin\tray.log`（托盘是非管理员进程，写不了 Program Files）。
  超过 1 MB 自动轮转一份 `.1`。

### 🔧 实现（只用标准库，无第三方依赖）
- `ctypes` 直调 Win32：`RegisterClassExW` + 隐藏窗口 + `Shell_NotifyIconW(NIM_ADD/MODIFY/DELETE)` +
  `TrackPopupMenu(TPM_RETURNCMD)` + `SetTimer` 轮询。**所有句柄（HWND/HICON/HMENU/HMODULE）都显式
  声明 `argtypes`/`restype`** —— 64 位下不声明就会按 C int 传，句柄被截断后托盘静默消失
  （`--self-test` 第一版就撞上 `OverflowError`，已修）。
- 可测性：`classify()` / `status_text()` / `decide_events()` 都是**纯函数**，通知规则全部单测覆盖；
  另有 `--check`（单次判定，CI / 排障）与 `--self-test`（真加一次图标 + 弹一次气泡，退出码即结论）。
- `setup.iss`：新增 `[Tasks] trayicon`、`[Files] tray.py`、`[Registry]`（HKCU 的 Run 项，
  `uninsdeletevalue` + `Tasks: trayicon`）、`[Run]`（装完勾选即可立刻起，`skipifsilent` 保证
  静默升级不会在这里起进程）。
- `auto_update.py`：两处 `/TASKS=` 都补上 `trayicon` —— 否则升级会把登录启动项当成「未选中」摘掉
  （和 v2.0.4.2 补 `desktopicon` 同一个坑）。

### ⚠️ 已知取舍
- 自动升级后，**已经在跑的旧托盘**会继续用旧代码跑到下次注销/重启（它轮询的是同一个 HTTP 接口，
  功能不受影响）；新版本的文件已经就位，下次登录自动用新的。之所以不做「检测到升级就自重启」，
  是因为重启与互斥体释放之间有竞态，做不好会变成**托盘静默消失**，比多跑一会儿旧代码糟得多。

### 🧪 测试
- 冒烟新增 17 条：8 条接线 / 依赖检查 + 9 条**行为级**通知规则（首轮不弹、掉线、恢复、抖动抑制、去重、四态归类）。
- 真机：`python tray.py --check` 连上本机服务 → 判定 `ok（已登录 ✓）`；
  `python tray.py --self-test` → 退出码 0（`NIM_ADD`/`NIM_DELETE` 均被通知区接受）。

## v2.0.5.0 (feat) — 2026-09-27 · 代号 `Sirius`（天狼星）

> B1：**网络位置守卫** —— 只在校园网里才干活。接口向后兼容（`PATCH` +1，配置**新增**字段）。

### ✨ 新增
- `feat(guard)`: **Wi-Fi 名（SSID）+ 网段（CIDR）白名单**。开启后（`network_guard_enabled: true`），
  只有「当前 Wi-Fi 名命中 `guard_allowed_ssids`」**或**「本机 IP 落在 `guard_allowed_subnets`」时才
  执行登录检查；其余情况直接跳过并写一行日志，**且不计入失败、不触发退避**。
  典型收益：笔记本带回家 / 连手机热点 / 挂 VPN 时不再空跑认证，日志不再刷「校园网不可达」。
- 配置页（「自动化」卡片）新增开关 + 两个输入框（`cfg-guard-enabled` / `cfg-guard-ssids` /
  `cfg-guard-subnets`）；状态页「网络可达性」副标题会显示当前 Wi-Fi 名，被守卫拦下时补一句
  「不在校园网」。
- 状态 API 增加 `current_ssid` / `guard_allowed` 两个字段（老客户端忽略即可，向后兼容）。

### 🛡️ 判定原则（写在代码注释里，也是测试用例）
1. **默认关闭** —— 不开启则行为与 v2.0.4.x 完全一致；
2. **fail-open** —— 两个白名单都留空、或压根读不到 Wi-Fi 名与 IP（有线 / 无 WLAN 网卡）→ **放行**；
   对一个"自动登录"工具来说，宁可多试一次，也不能因为读不到 Wi-Fi 名就静默不干活；
3. 无线走 `netsh wlan show interfaces`（中文 Windows 的 GBK 输出也能解析，并排除 `BSSID` 行）；
   网段匹配用标准库 `ipaddress`，校验期就会拦下非法 CIDR。

### 🔧 实现
- `protocol.py`：新增 `get_current_ssid()` / `get_local_ips()` / `guard_allows()`（**纯函数**，便于单测）+ `run_once` 里第 1.5 步的守卫 hook。
- `联网_service.py`：`DEFAULT_CONFIG` 增 3 个字段；`_validate_config` 增类型 / 长度 / CIDR 合法性校验；`STATE` 增 `current_ssid` / `guard_allowed`。
- `web_api.py`：配置页表单 + 回填 / 收集 + 状态副标题（`api_get_config` 按 `DEFAULT_CONFIG` 整体返回，新字段自动生效）。

### 🧪 测试
- 冒烟新增 15 条：8 条**行为级**守卫判定（命中 / 不命中 / 空配置 / fail-open / 中文逗号 / 有线）+ 3 条配置校验 + 4 条接线检查。

## v2.0.4.5 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 技术债收口（C 组）：三处"不致命但迟早咬人"的小问题，外加把 PR 自动检查闸门装上。
> 接口、配置结构、服务名未动（`SERIAL` +1）。

### 🔧 修复
- `fix(protocol)`: **`run_once` 的"忙判定"是死逻辑** —— 原代码是 `with _RUN_LOCK:` 之后才判
  `_STATE["login_in_progress"]`，而 `_RUN_LOCK` 本身已经把并发串行化了，那个判断**永远为假**；
  于是连点「立即登录」会排队执行 N 次完整检查（每次都是等网络 + 查在线 + 登录）。
  现在改成**非阻塞抢锁**（`_RUN_LOCK.acquire(blocking=False)`）：抢不到就立即返回并记一行日志，
  真正的"忙"以锁为准；手工抢到的锁用 `try/finally` 保证任何分支（含中途 return）都会释放。
- `fix(联网_service)`: **`config.json` / `password.txt` 的原子写改用唯一 tmp 名** —— 原先固定
  `config.json.tmp` / `password.txt.tmp`，两个写者（服务 + 手动跑的实例，或两个并发 POST）会往
  同一个文件里交错写，极端情况下会落盘半截 JSON。现在 tmp 名带上 pid，并发只会"最后写入者胜"。
  （`web_api` 配置导入里写 `password.txt` 的那处同样处理。）
- `fix(installer)`: `IsTaskSelected` → `WizardIsTaskSelected`（ISCC 编译时一直提示的弃用项，Inno 6 新名字）。

### 🧪 测试 / 交付
- 冒烟新增 8 条：6 条静态 + 2 条**行为级**（同一线程里非阻塞抢锁：第一次 True、第二次立即 False
  —— "连点不再排队"靠的就是这条语义）。
- **新增 PR 检查闸门**（PR #12）：`.github/workflows/pr-checks.yml` 在 PR 上跑 `compileall` +
  静态冒烟 + HTTP 冒烟，**不发布任何东西**（发布链路仍只由 `push → main` 的
  `build-installer.yml` 负责）；顺带修掉 CI 上 cp1252 控制台让中文断言名崩掉的问题
  （job 级 `PYTHONIOENCODING=utf-8` + 两个脚本的 `reconfigure(errors="backslashreplace")` 兜底）。

## v2.0.4.4 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 安装向导外观收口：把 `_ui_redesign/flashlink-mock.iss` 上真机逐页验证过的多尺寸品牌图接进真包。
> 只动 `setup.iss` 的向导段与打包资源，接口 / 配置 / 服务名未动（`SERIAL` +1）。

### ✨ 安装向导
- `fix(installer)`: **向导图改成多尺寸 PNG** —— `WizardImageFile` 列 5 档左侧大图
  （202×386 → 430×824），并新增 `WizardSmallImageFile` 列 5 档右上角小图（58×58 → 124×124），
  Inno 6 会按当前 DPI 自动挑最合适的一张。此前只有一张 `wizard.bmp`，高 DPI 下是放大插值的，
  而且那张图里还印着过期的 `v2.0.0`。
- `fix(installer)`: **暗色向导 + 放大的整体尺寸** —— `WizardStyle=modern dark includetitlebar hidebevels`、
  `WizardSizePercent=110`（与 mock 上验证过的参数一致）。
- `fix(installer)`: **补回欢迎页** —— `DisableWelcomePage=no`（Inno 6 默认 `yes`，平时看不到那一页），
  并把 `[Messages]` 的欢迎 / 完成文案与 mock 对齐。
- `chore(installer)`: 删掉已被取代的单张 `branding\wizard.bmp`（154 KB）。
- `test`: 冒烟新增 4 条断言（大图 / 小图多尺寸列表、暗色 + 欢迎页、旧 bmp 不再被引用且已从仓库移除）。

### 🧪 编译校验
本机 ISCC 6 直接编译真包：`Successful compile (1.328 sec)` →
`StardustFlashLink-Setup-v2.0.4.4.exe`（2.76 MB，含内嵌占位的校验构建）。

## v2.0.4.3 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 承接 v2.0.4.2：升级链路已经真能装上了（真机 v2.0.4.1 → 2.0.4.2 一次成功），本版收掉它剩下的
> 两处尾巴 —— `AppExit` 自愈的**长期误报**，和升级执行器的**结果归档时序**。

### 🔴 修复
- `fix(auto_update)`: **`AppExit` 自愈从 v2.0.4.0 起一直在误报** —— `winreg.OpenKey` 的键路径
  **不能带 `HKLM\` 前缀**（HKLM 由 `HKEY_LOCAL_MACHINE` 常量给出），而代码一直传
  `HKLM\SYSTEM\...\Parameters\AppExit` → `FileNotFoundError` 被 `except OSError: return None`
  吞掉 → 每次开机都记一行「AppExit 自愈失败（仍是空值）」。真机对照：那个子键里
  `(默认)=Ignore`、`0=Ignore`，**策略一直是对的**。现在统一经 `_hklm_subpath()` 去前缀
  （幂等、大小写不敏感）。
- `fix(auto_update)`: **升级执行器的结果不再被误删** —— 服务是安装器在 `ssPostInstall` 就拉起来的，
  那一刻 `.cmd` 执行器**还在跑**（它等 installer 进程退出后才归档日志、写 rc），而启动钩子上来就把
  执行器删掉 → 它后面的步骤全部没执行（真机实测：`drcom_apply_update.rc` 与
  `logs\installer-silent.log` 都没出现）。现在只清**陈旧**残留（`UPDATE_LEFTOVER_STALE_SEC`，
  默认 5 分钟），计划任务也只在执行器已自删后才兜底删。
- `fix(auto_update)`: **执行器结果读取带 8 秒宽限**（`_report_update_runner_result(wait_sec=8)`），
  并在启动钩子里把 System TEMP 的安装日志**归档回 `{app}\logs\installer-silent.log`**，
  保证 Web UI / 用户随时找得到。
- `test`: 冒烟再加 6 条（4 条静态 + 2 条行为级：`_hklm_subpath` 去前缀、幂等、路径可用于 winreg）。

### 🧪 真机验证（v2.0.4.1 → v2.0.4.2，走修复后的升级链路）
```
[22:59:03] 检查完成：发现新版本 2.0.4.2
[22:59:06] 下载完成：11316847 字节 · SHA256 校验通过
[22:59:07] installer 已启动（方式=schtasks，第 1 次尝试）      ← v2.0.4.2 新路径
[22:59:11] 升级成功确认：已运行 v2.0.4.2（目标 2.0.4.2，尝试 1 次）   ← 4 秒装完
```
`version.py` → 2.0.4.2、服务 `Running`、`/api/config` 200、`update_attempt.json` 被清除。

## v2.0.4.2 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 本版只修**自动升级链路**。真机实测 v2.0.4.1 发布后：`发现新版本 → 下载 → SHA256 通过 →
> installer 已启动` 一路"成功"，**结果什么都没装上**，而且服务停在 `StopPending`、自动登录直接停摆。
> 接口、配置结构、服务名、`AppId` 一律未动（`SERIAL` +1）。

### 🔴 修复（自动升级：installer 被 nssm 的 Job 连坐杀掉）
- `fix(auto_update)`: **改用「任务计划程序」拉起 installer**。根因：`setup.iss` 的
  `CurStepChanged(ssInstall)` 会 `nssm stop DrcomAutoLogin`，而 nssm 关闭自己的 Job Object 时会把
  **同 Job 的子进程一起杀掉** —— 直启（`DETACHED_PROCESS|CREATE_BREAKAWAY_FROM_JOB`）的 installer
  在复制文件前就消失。真机证据：`installer-silent.log` 压根没生成、`version.py` 不变、服务停在
  `StopPending`；而**同一条命令行由不在该 Job 里的进程拉起 → 7.1 秒装完**。现在改为写一个 `.cmd`
  执行器（落在 `%TEMP%`）→ `schtasks /create /ru SYSTEM /rl HIGHEST` → `/run`；任务计划程序不可用
  时才退回旧的直启路径。
- `fix(auto_update)`: **升级执行器带看门狗** —— installer 跑完先查服务是否 `RUNNING`，没起来就
  `sc start`。升级失败时这是唯一兜底（此前服务就那样一直停着，直到人工发现）。
- `fix(auto_update)`: **installer 退出码不再丢失** —— 执行器把它写进
  `%TEMP%\drcom_apply_update.rc`，启动钩子读出来写进 `upgrade.log`（此前失败只有一句
  "installer 被拦截/挂起？"，无从查起）。
- `fix(auto_update)`: **静默安装补上 `desktopicon` 任务** —— `/TASKS=startservice` 等于告诉 Inno
  「公共桌面快捷方式那个任务未选中」，升级后桌面图标永远是旧的。
- `fix(auto_update)`: **`AppExit` 读写在 nssm 的真实结构上** —— nssm 存的是
  `Parameters\AppExit` 子键的 `(默认)` / `0` 子值，而代码读的是 `Parameters` 下的同名值（根本不存在）
  → 每次开机误报「AppExit 自愈失败」；`nssm set … AppExit "Default Ignore"` 还把子参数与值拼成了
  单个 argv（静默写错）。现在读子键、`set` 拆成 `subparam` + `value` 两个参数。
- `fix(auto_update)`: **不再用「备份 hash 不同」宣告升级成功** —— 那条会把任何脚本改动（含手动热补丁）
  误判成升级成功，真机上出现过"绿 banner 说已升级、实际还是旧版本"。成功一律以
  「尝试记录的目标版本 == 当前 `VERSION`」为准（此时才点亮成功 banner）。
- `chore(auto_update)`: 升级收尾清理 —— 删计划任务、执行器、结果文件，以及**已经装上的安装包**
  （顺带清掉历史上堆在 `%TEMP%` 的旧包）。
- `test`: 冒烟新增 11 条断言，其中 4 条是**行为级**：直接生成执行器脚本，断言「安装 + 落退出码 +
  看门狗 + 自删」四要素齐全、CRLF 行尾、且不含任何凭据。

## v2.0.4.1 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 本版两件事：**① 修一个会把 Web UI 配置页和自动登录一起卡死的死锁**；
> **② 打包 / 外观收口** —— 桌面、开始菜单与「应用和功能」里的图标换成品牌图。
> 接口、配置结构、服务名、`AppId` 一律未动（`SERIAL` +1）。

### 🔴 修复（PWD_LOCK 自锁死锁：配置页打不开 + 自动登录停摆）
- `fix(web_api)`: **`api_get_config()` 对不可重入锁 `PWD_LOCK` 拿了两次** —— `with PWD_LOCK:` 里
  又调了自带 `with PWD_LOCK` 的 `_get_password()`（归档的 v1.x 版本是直接读 `_PWD_VALUE`，
  模块化拆分时成了回归）。后果：`GET /api/config` **永久挂起** → 配置页字段全空、徽标显示
  「状态未知」、**保存请求也一起卡死**（`_save_password_to_disk` 抢同一把锁），刷新后看着像
  "编辑完又没了"；更严重的是周期性自检线程同样卡在 `_get_password()` 上 → **自动登录实际已停摆**
  （`last_check_at` 不再推进）。现在只调 `_get_password()`（加锁责任在它内部），并把这条约定写进注释；
  冒烟测试新增 3 条静态断言 + 真实调用 `api_get_config()` 的 3 秒超时回归（死锁即 FAIL）。

### ✨ 改进（安装器外观）
- `fix(installer)`: **桌面快捷方式不再是通用图标** —— `setup.iss` 的 `[Icons]` 原本把图标写成
  `{sys}\shell32.dll,13`（Windows 通用图标），现在改为品牌 `{app}\branding\app.ico`；
  `app.ico`（7 个尺寸，256 → 16 px）随包分发到 `{app}\branding`。
- `fix(installer)`: **开始菜单 `.url` 带上图标** —— `CreateURLFile` 生成的 `InternetShortcut`
  现在写 `IconFile={app}\branding\app.ico` + `IconIndex=0`，不再显示浏览器默认图标。
- `fix(installer)`: **「应用和功能」卸载项图标** —— `UninstallDisplayIcon` 从 `联网_service.py`
  （py 图标）改为 `{app}\branding\app.ico`。

### 🔧 开发工具（不在安装包里）
- `_ui_redesign/shot-wizard.ps1`：向导逐页截图（`PrintWindow` 渲染 + `BM_CLICK` 翻页，
  不抢前台焦点）；修掉「按 PID 找不到向导窗口」（Inno 是 loader + 向导子进程）、
  「误点说明文字而非按钮」、150% 缩放下截图被裁右下角、向导提前关闭后句柄失效等坑。
- `_ui_redesign/fix-desktop-icon.ps1`：给**已装机器**补品牌图标（公共桌面只有管理员能改，需提权一次）。

## v2.0.4.0 (fix) — 2026-09-27 · 代号 `Sirius`（天狼星）

> 本版的主题是「**自动升级到底装上了没有**」：真机上出现「每 30–60 秒一轮：发现新版本 →
> 下载 → 启动 installer → 服务退出」，几十轮下来版本号纹丝不动。本轮把这条链上的 5 个断点
> 全部修掉，并顺带修「`#` 开头的密码被当成注释整行吃掉」与「查看更新日志报缺文件」。
> 同时启用**版本线代号**机制（本版起 `2.0` 线 = `Sirius`）。

### 🔴 修复（升级链路）
- `fix(update)`: **启动钩子从未生效** —— `_post_upgrade_startup()` 写在
  `_auto_update_mod._attach()` **之前**，执行时 `LOG_DIR` 仍是 `None` → 每次开机
  `NameError` 被吞掉。连带后果：`AppExit` 自愈不跑、升级结果没人确认。已归位到 `_attach` 之后。
- `fix(update)`: **升级熔断** —— 新增 `logs\update_attempt.json`：落盘「目标版本 / 第几次尝试 /
  是否生效」；启动时确认目标版本是否真的成了（成了 → 记「升级成功确认」并清记录；没成 → 记 WARN +
  标 `failed`）。同版本连续 3 次未生效即**停止自动重试**并提示手动下载，失败后 **6 小时冷却**。
- `fix(installer)`: **静默安装不再弹窗** —— 服务未在 30 秒内停止时原本弹 `MsgBox`，而自动升级跑在
  服务会话里（无人在场）→ 安装**永久挂起** → 客户端以为没装上 → 再试 → 死循环。
  现在 `if WizardSilent then Log(...) else MsgBox(...)`。
- `fix(installer)`: 安装参数由 `/SILENT` 改为 **`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL`**，
  并加 `/LOG=%BASE%\logs\installer-silent.log`（"装不上"终于有据可查）。
- `fix(nssm)`: **`AppExit` 空字符串**不再被当成「已设置」—— 真机上注册表值为空时，
  自愈函数判定"无需修复"而空转，同时 `nssm` 在 `service_stderr.log` 里刷
  `Parameter "AppExit" requires a subparameter!`。现在空值视为无效，优先用 `nssm.exe`
  重写 `Default Ignore`。

### 🔴 修复（其它）
- `fix(password)`: **`#` 开头的密码被整行吃掉** —— 旧实现把任何 `#` 开头的行都当注释跳过；
  真机密码形如 `#xxxxxxxx#` 时整行被丢弃 → 服务认为「密码未设置」→ 登录失败 + Web UI 显示
  「未设置」，看起来就像「升级把配置弄丢了」。现在只跳过安装包自带的模板提示行
  （`_PASSWORD_HINT_MARKERS` / `_is_password_hint`），其余内容一律按密码原文处理；
  读取改用 `utf-8-sig`，容忍手工编辑留下的 BOM。
- `fix(changelog)`: **「关于 → 查看更新日志」报缺文件** —— 安装包从未打包 `CHANGELOG.md`，
  真机必然弹 `read changelog failed: [Errno 2] No such file or directory`。现在
  `setup.iss` 随包分发该文件，`eula.py` 按「安装目录 → `docs\` → 上一级」候选查找，
  缺失时只给中文提示 + 仓库链接（不再把裸路径异常抛到界面）。

### 🧹 文案 / 一致性
- `feat(version)`: 启用**版本线代号** —— `version.py` 新增 `CODENAME` / `CODENAME_CN` /
  `VERSION_FULL`（`2.0` 线 = `Sirius` / 天狼星）；Web UI 版本徽章与「关于」页显示 `v2.0.4.0 Sirius`；
  安装器 `AppVerName` = `星尘闪连 (Stardust Flash Link) 2.0.4.0 "Sirius"`。规则与候选星名表见
  `docs/VERSIONING.md`。
- `docs`: 新增 `AGENTS.md`（AI / 协作者编辑规范：不变量、隐私红线、验证纪律、截图规范、发布 checklist）。
- `docs`: README 版本记录改为**版本线摘要**（逐版本明细只留在 `CHANGELOG.md`）；运行期文件表补
  `update_attempt.json` / `installer-silent.log`；修正两处过时产物名。
- `fix(ui)`: 配置页账号提示由「例如 2023123456」改为「仅支持数字（学号 / 工号），不含运营商后缀」。
- `docs`: README 截图重拍（亮 / 暗），徽章同步到 v2.0.4.0，且**账号一律打码**（`2023******@yd`）。

### 🔒 隐私与安全
- `test`: 新增**隐私守卫**断言 —— 读取本机 `password.txt` / `config.json` 的凭据，与**所有入库文件**
  做精确比对，命中即测试失败；`DRCOM_DATA_DIR` 可指向安装目录（如
  `$env:DRCOM_DATA_DIR='D:\Program Files\DrcomAutoLogin'`）一并核查。CI 上没有这些文件会自动 SKIP。
- `chore`: `.gitignore` 补 `*.log` / `update_attempt.json` / `installer-silent.log` / `config-export-*.zip`。
- `test`: 密码相关测试夹具全部改为**合成值**，源码与注释中不再出现真机凭据片段。

### 其它
- 安装包产物名 `StardustFlashLink-Setup-v2.0.4.0.exe`（不变量 I9）。
- 冒烟结果：`_smoke_static.py` **68 项断言 0 失败**（新增 5 项 CHANGELOG 回归 + 9 项隐私守卫）、
  `_smoke_http.py` **9/9 通过**。

### ⚠️ 已知限制
- `AppExit` 若已被写成空值，**自愈需要管理员权限**；权限不足时只写日志、不影响登录。
  手动修复：`nssm set DrcomAutoLogin AppExit Default Ignore`（管理员 CMD）。
- 自动升级仍**默认关闭**（`auto_update_enabled: false`），需在 Web UI「配置 → 自动化」手动开启。

## v2.0.3.0 (feature) — 2026-09-27

> 本版把 Web UI 从「AI 味渐变卡片」换成 Apple 风格的亚克力玻璃界面，并修掉顶栏
> logo / favicon 一直 404 的破图问题。**前端仍然全部内联在 `web_api.py`（零新增静态文件依赖）**。

### ✨ 界面（重做）
- `feat(ui)`: 设计令牌化 —— 亮/暗两套语义变量（画布 / 亚克力材质 / 系统灰填充 /
  发丝描边 / 单一强调色 / 三级文字灰），层级改用「字号 + 字重 + 灰度」表达，
  不再靠高饱和渐变堆砌。
- `feat(ui)`: 亚克力材质统一为 `backdrop-filter: saturate(180%) blur(30px)` +
  1px 发丝描边 + 内侧高光；顶栏、卡片、说明条、保存条、弹窗、Toast 同一套语言。
- `feat(ui)`: 字体栈改为系统字体优先（`-apple-system` / `SF Pro Text` / `PingFang SC` /
  `Microsoft YaHei UI`），数字与日志用等宽字体 + `tabular-nums`（倒计时不再抖动）。
- `feat(ui)`: 顶栏品牌区改两行锁排（`星尘闪连` + 副标题），分段控件（Segmented Control）
  取代旧页签，图标由 emoji 换成 1.7 描边线性 SVG（各系统渲染一致）。
- `feat(ui)`: 按钮改为胶囊形（主操作 Apple 蓝、次操作系统灰填充、危险操作红字），
  开关改为 iOS 样式（48×29，绿色 on），输入框加聚焦光圈，弹窗/Toast 加升降动效。
- `feat(ui)`: 新增 ≤720px / ≤480px 两档响应式（窄屏单列 + 全宽主按钮 + 保存条竖排），
  并遵循 `prefers-reduced-motion`。

### 🔴 修复
- `fix(web_api)`: **顶栏 logo 与 favicon 404** —— 页面引用 `branding/*.png` 但服务端只有
  `/` 与 `/api/*` 路由，且这些 png 压根没进安装包。新增 `/branding/<name>` 静态路由：
  文件名白名单 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.(png|ico|svg|jpe?g|webp|gif)$`（结构性
  阻断路径穿越）+ 目录按「安装目录 → `packaging/branding`」顺序查找 + `nosniff` + 1h 缓存。
- `fix(packaging)`: `setup.iss` 新增 `branding\web-logo-*.png → {app}\branding`，
  卸载时一并清理；装完即能显示 logo / 站点图标。
- `fix(ui)`: logo 兜底 —— 图片加载失败时用内联星芒标记替换，杜绝破图图标。
- `fix(ui)`: `pollUpdate` 里 `banner.hidden` 的 `ReferenceError`（P3-4，被 catch 吞掉），
  升级成功横幅的 7 秒自动隐藏此前一直失效。
- `fix(ui)`: `ensureChangelogShortcut` 引用了不存在的 `--primary` 变量，快捷链接颜色改 `--accent`。

### 其它
- 版本字面量统一：`version.py` / `setup.iss MyAppVersion` / NSSM 描述 / `install.bat` /
  `uninstall.bat` / `packaging/build.ps1` / 模块 docstring。
- 安装包产物名 `StardustFlashLink-Setup-v2.0.3.0.exe`（不变量 I9）。
- 新增冒烟断言：品牌图片路由（200 / 404 / 路径穿越 / 非图片扩展名）、页面无 emoji、
  亚克力令牌在位、旧网格底纹已移除。

## v2.0.2.4 (fix/security) — 2026-09-27

> 本版把「4 个升级相关按钮点了就崩」和几处安全口子一起修掉，并改掉发布策略。

### 🔴 修复（P0）
- `fix(web_api)`: 修 6 个未定义裸名 —— 补 `import time` + 5 处跨模块调用加 `_auto_update_mod.` 前缀。
  修前「立即检查更新 / 立即升级 / 升级开关 / 升级历史」4 个功能**运行时必然 NameError**。（P0-1）
- `fix(auto_update)`: SHA256 校验改 **fail-closed** —— `digest` 缺失即拒绝安装并删除已下载安装器。（P0-2）
- `fix(password)`: `_load_password_from_disk` 跳过空行与 `#` 注释行；`password.txt.template` 内容清空。
  新装机不再把模板提示当成真实密码去登录。（P0-3）
- `fix(restart)`: 「重启服务」改为优先 `nssm restart DrcomAutoLogin`。AppExit=Ignore 时 `os._exit(0)`
  之后 NSSM 不会拉起，旧行为 = 点一次永久停机；找不到 nssm 才退回旧路径。（P0-7）
- `fix(installer)`: `install.bat` 的 `AppExit` 由 `Restart` 改为 `Ignore`，与 `setup.iss` 统一；
  `_post_upgrade_startup` **不再每次启动写回 `Restart`**（旧行为把 v2.0.2.3 的 `a77ea46`
  「端口占用不死循环重启」修复原样撤销）。（P0-7）

### 🟠 安全（P1）
- `security(web_api)`: Host 白名单（挡 DNS rebinding）+ 写接口强制 `X-Requested-With: DrcomUI`
  + `Origin` 同源校验；页面内 `fetch` 由注入脚本自动带头。**（P1-1 部分完成：token 鉴权仍待做）**
- `security(auto_update)`: `digest` **只信主源 `api.github.com`**，删除 API 镜像 fallback
  —— 镜像曾可同时伪造 releases JSON / asset URL / digest，等于校验同源。（P1-2）
- `security(auto_update)`: 静默升级**默认关闭**（`auto_update_enabled: false`）+ 远端版本串白名单
  （防 `..\` 穿越拼进 `%TEMP%` 文件名）。（P1-3）
- `security(web_api)`: `/api/config/export` 不再打包明文 `password.txt`，
  改为在 `manifest.json` 记录 `password_status`（`set` / `missing`）。（P1-4）

### 🚀 发布（P1-5）
- `ci(installer)`: 安装包发布改**版本化非 prerelease release**（tag `v{版本}`）——
  `/releases/latest` 只返回非 prerelease，此前自动升级拿不到版本；rolling `installer` prerelease
  仍保留作固定下载链接。
- 版本字面量统一：`version.py` / `setup.iss MyAppVersion` / NSSM 服务描述 / `install.bat` / 模块 docstring。

### 说明
- 自动升级默认关闭 ≠ 不可用：Web UI 里仍可手动「立即升级」；等真机验证过升级链路后再评估默认开启。
- 安装包产物名保持 `StardustFlashLink-Setup-v2.0.2.4.exe`（不变量 I9）。

## v2.0.2.3 (hotfix) — 2026-09-21

- chore: 删除 `_debug/` 6 个临时诊断脚本 + 加 `.gitignore` (`f632fc3`)
- fix(password): 移除 `pwd_value=` 注入, 改用 `get_password()` 函数; 同步清理 `web_api.py` line 166 死代码 + `protocol.py` `_attach` 签名 (`3021ad5`)
- fix(installer): `AppExit Default Restart` → `Default Ignore`, 端口冲突时 NSSM 不死循环重启 (`a77ea46`)
- fix(ci): `version.py` 缺 VERSION 时 `::error` + `exit 1`, 不再静默 fallback 2.0.1 (`b908e6c`)
- 版本号字面量 bump: `2.0.2.2` → `2.0.2.3` (4 处: `version.py`, `setup.iss` MyAppVersion, `setup.iss` NSSM Description, `联网_service.py` docstring)

4 个 P0 commit 已在 `hotfix/v2.0.2.2` 上, 此 commit 仅 bump 字面量 + CHANGELOG。

## v2.0.2.2 (hotfix) — 2026-09-21
- fix(web_api): `_schedule_success_clear` 裸名调用在解耦后 NameError，改为 `_auto_update_mod._schedule_success_clear()`
- web_api.py `_attach()` docstring 注释"占位"改为正确的"跨模块调用走 _auto_update_mod"约定
- packaging/version.py/setup.iss/联网_service.py docstring: 2.0.2.1 → 2.0.2.2

## v2.0.2.1 (hotfix — 装包 + 启动修) — 2026-09-21

- `fix(installer)`: v2.0.2 装包缺 5 个解耦模块 (`version.py` / `protocol.py` / `eula.py` / `web_api.py` / `auto_update.py`), 启动崩 `ModuleNotFoundError: No module named 'version'`。在 `packaging/setup.iss` [Files] 段加 5 行 `Source: "..\xxx.py"`, `version.py` VERSION 2.0.1 → 2.0.2.1。
- `fix(ci)`: `.github/workflows/build-installer.yml` 之前硬编码 `APP_VERSION="2.0.1"`, 改为从 `version.py` 读取 (`-c "import version; print(version.VERSION)"`)。
- `fix(startup)`: embeddable Python 默认 `sys.path[0]` 是 stdlib zip (`python312.zip`) 而不是脚本目录, 联网_service.py 头部加 `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` 3 行兜底, 跨部署环境都生效。
- 自启动不触发登录的退避算法 bug: `run_periodic` 计算 `wait_sec = max(interval_sec, delta)` 是反模式 (interval 永远 > backoff), 改为 `wait_sec = min(interval_sec, delta)`。`_startup_trigger` 强制退避 0, 开机早期网络未稳时**不**该有退避历史。
- 释出: 装包资产覆盖到 v2.0.2 release (exe 文件名 `StardustFlashLink-Setup-v2.0.2.1.exe`), git tag 仍 v2.0.2。
- 4 commits 领先 main (cf235a2 + 301e37e + 3b29087 + de0d0d7)。

## v2.0.2 (字面量升级) — 2026-09-21

- `version.py`: VERSION "2.0.1" → "2.0.2"
- `packaging/setup.iss`: MyAppVersion "2.0.1" → "2.0.2" + 注释 / 输出文件名 / NSSM Description
- `install.bat` / `uninstall.bat` / `packaging/build.ps1`: 注释 / echo / `$AppVersionText`
- `.github/workflows/build-installer.yml`: fallback `$v` "2.0.1" → "2.0.2"
- `README.md` / `packaging/README.md`: 当前版本 + 安装包文件名引用 (4 处)
- `联网_service.py`: 文件头 docstring v2.0.1 → v2.0.2
- `CHANGELOG.md`: 加 v2.0.2 段 (解耦 + 退避 max→min bug fix)
- 18 处字面量升级, 0 临时文件 / 0 `git add .` 误带

注: v2.0.2 release 资产后续被替换为 v2.0.2.1.exe (hotfix/v2.0.2.1 commit), git tag v2.0.2 保留指向 e0354df。

---

## [v2.0.0] - 2026-09-20

进入 2.0 时代。

### ✨ 新增
- **安装器视觉改版**：从用户自制 logo 改用纯生成的蔚蓝档案（Blue Archive）经典蓝渐变背景（`#A0D8EF` → `#3D7DC9` → `#1B3A6B`），164×314 24-bit BMP 嵌入安装器左侧品牌横幅
- **EULA 用户协议**（`packaging/branding/EULA.rtf`，ISCC `LicenseFile` 原生支持）：9 节完整条款 —— 服务范围 / 许可 / 用户责任 / 免责声明 / 隐私政策 / 第三方组件 / 协议修改 / 终止 / 适用法律
- **Web UI「关于」面板**新增 "📜 查看更新日志" 按钮 → 弹窗显示本文件内容
- **升级成功横幅**新增 "📋 查看更新日志" 快捷链接

### 🔧 变更
- 项目重命名为「星尘闪连 (Stardust Flash Link)」：setup.iss `MyAppName` 改；安装包文件名前缀仍为 `DrcomAutoLogin-Setup-v*`（保留历史 release URL 兼容）
- 内部版本号语义：从 1.x 公开版本线进入 2.x 公开版本线
- Web UI 顶部 `<title>` 与品牌标识改为 "星尘闪连"
- README 增加 logo + 5 徽章带 + 18 项 TOC

### 🐛 修复
- 无（2.0 主要是视觉 / 法务姿态升级，技术栈不变）

---

## [v1.4.0] - 2026-09-20

### ✨ 新增
- **项目重命名为「星尘闪连 (Stardust Flash Link)」**
- 安装包新增 `branding\app.ico`（应用图标，256/128/64/48/32/24/16 多尺寸 ICO）
- 安装包新增 `branding\wizard.bmp`（164×314 24-bit 安装器左侧品牌横幅）
- README 美化（顶部 logo + 5 徽章 + 18 项 TOC）
- 联网_service.py 嵌入 HTML `<title>` 与品牌标识改为 "星尘闪连"

### 🔧 变更
- `MyAppName` 改 "星尘闪连 (Stardust Flash Link)"
- `MyAppPublisher` 改 "星尘闪连"

### 🐛 修复
- EULA 文档新增（详见 v2.0.0）

---

## [v2.0.1] - 2026-09-20

Hotfix：安装包文件名 + Web UI 品牌图标升级。

### ✨ 新增
- **Web UI 品牌图标替换**：从「星」字占位换成 logo 图（`branding/web-logo-{16,32,64,128,256}.png`），由 `app.ico` 提取；同时 `<head>` 加 favicon (`web-logo-32.png`)
- **安装包文件名升级**：`OutputBaseFilename` 从 `DrcomAutoLogin-Setup-*` 改为 **`StardustFlashLink-Setup-*`**，与新仓库名 `StardustFlashLink` 一致

### 🔧 变更
- 全部版本字面量 2.0.0 → 2.0.1
- README 清理：去掉冗余「用户」措辞（CHANGELOG 历史记录保留）
- `OutputBaseFilename` 改 `StardustFlashLink-Setup-v{#MyAppVersion}`

### 保留（兼容性必需）
- NSSM 服务名 `DrcomAutoLogin`（已发布安装都依赖这个名字）
- 安装路径 `C:\Program Files\DrcomAutoLogin\`
- `AppId = {{A8F2E3D1-7C4B-4F89-9D5E-1A2B3C4D5E6F}}`（Windows 卸载注册表项）

---

## [v1.3.5] - 2026-09-20

### ✨ 新增
- **GitHub 国内镜像加速**：`_check_github_latest` 和 `_download_installer` 按 `GITHUB_API_MIRRORS` 列表（`None` 主源 + `https://gh-proxy.com` / `ghfast.top` / `mirror.ghproxy.com` 三个镜像）串行 fallback；主源超时/失败时自动尝试镜像，解决国内 `api.github.com` 不可达问题
- **手动触发更新按钮**：Web UI「配置」面板「自动化」card 末尾新增 "🔍 立即检查更新" 和 "⬆️ 立即升级" 按钮，调用 v1.3 已有的 `/api/update/check` 和 `/api/update/install` 端点（零新增 API）

---

## [v1.3.4] - 2026-09-20

### ✨ 新增
- **日志分级筛选**：Web UI「日志」面板顶部新增等级筛选 chip（全部 / INFO / WARN / ERROR）
- 后端 `GET /api/log_tail` 新增可选 `level` query 参数（`info` / `warning` / `error` / `debug` / `critical`），按等级过滤返回行
- 响应体新增 `level_filter` 字段（向后兼容：旧客户端忽略未知字段）

---

## [v1.3.3] - 2026-09-20

### ✨ 新增
- **配置导入/导出**：Web UI「配置」面板新增 "📤 导出配置" 和 "📥 导入配置" 按钮
- 导出：把 `config.json` + `password.txt`（如有）+ `manifest.json` 打包成 `config-export-<时间戳>.zip` 下载
- 导入：上传 zip 后做合法性校验（manifest schema_version、config 字段校验、password 非空），通过后原子写入磁盘并返回 `{need_restart: true}`

### 🔧 变更
- 纯标准库 `zipfile` + `io` 实现，不引入新依赖

---

## [v1.3.2] - 2026-09-20

### 🐛 修复
- **自动升级流程两个关键 bug**：
  1. `_launch_installer` 启动 installer 时未用 `DETACHED_PROCESS` flag —— installer 进程继承父 Python 的 console handle + process group，Python 被 NSSM 杀掉时 installer 被连带杀掉，升级半途而废。修复：用 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` 让 installer 完全脱离父进程生命周期
  2. 升级期间 `_set_nssm_appexit("Disabled")` 写了一个 NSSM 非法值（AppExit 合法值只有 `Default | Exit | Success | Failure | Codes`），导致升级后服务无法启动。修复：升级透明策略 —— 不再写 AppExit，保留用户原值

---

## [v1.3.1] - 2026-09-20

### 🐛 修复
- **v1.3 前后端字段没收口**：`_save_config` 在校验前 merge 默认值（兜底），老 config.json 缺 `update_min_free_disk_mb` 等 v1.3 字段时不再报错
- **版本比较 bug**：`_parse_version` 不识别 `-fix` / `-rc1` 等非数字后缀，`_parse_version("1.3-fix")` 与 `1.3` 比较时错误地返回 0（"已是最新"），导致 v1.3 服务无法识别并升级到 `v1.3-fix` / `v1.3.1`；重写解析逻辑，遇非数字后缀追加 sentinel `999`，使 hotfix 版本严格大于同主版本号

---

## [v1.3] - 2026-09-20

### ✨ 新增
- **静默自动升级**：服务后台定期检查 GitHub `/releases/latest` —— 本机版本落后则自动下载安装器、校验 SHA256、备份当前脚本、调 Inno Setup 静默安装、服务自动重启
- Web UI 状态面板顶部新增升级状态横幅（warn / success / error 三态）
- Web UI「配置」面板「自动化」card 新增「启用自动升级」开关与「检查间隔」下拉（6 / 12 / 24 小时）
- Web UI 关于面板新增「查看升级历史」按钮（弹窗显示 `logs/upgrade.log`）
- 后端 `GET /api/update/status` / `POST /api/update/check` / `POST /api/update/install` / `POST /api/update/toggle` / `GET /api/update/history` 5 个端点
- 后台线程 `_auto_update_loop` 周期检查 + 静默触发升级

### 🔧 变更
- 配置面板布局调整：「账户与登录密码」card（账号 + 运营商 + 密码）整体上移到顶部

---

## [v1.2] - 2026-09-20

### ✨ 新增
- **Web UI 重设计为 DeepSeek 风格**：极简、淡蓝/淡紫渐变背景、细腻网格底纹、大圆角、柔和阴影、大字号 KPI、pill 按钮、状态点呼吸动效、顶部细提示条
- **安装器在升级时自动先停服务再覆盖文件**——避免旧 Python 进程持有 `联网_service.py` 句柄导致新版本装不上

### 🐛 修复
- 修复 v1.1 的配置字段前后端没收口问题
- 修复安装器 NSSM AppParameters 引号问题（含空格路径下服务无法启动）

---

## [v1.1] - 2026-09-20

### ✨ 新增
- **安装包内嵌 Python 3.12 运行时**：终端用户无需预装 Python
- **GitHub Actions 自动构建流水线**：push 后自动产出 `.exe` 并发布到 `installer` Release
- **Web UI 现代化重设计**：亮 / 暗主题、KPI 卡片、分段控件、终端日志、Toast

### 🐛 修复
- 修复 `.panel.active` 白屏（动画未推进时永久停在 `opacity:0`，补静态兜底）

---

## [v1.0] - 2026-09-20

### 🎉 首个公开发布版本

- **Windows 校园网自动登录**：NSSM 服务托管、Web UI 配置、周期自检与指数退避、一键安装 / 卸载、可选 Inno Setup 打包
- 适配福建农业职业技术学院校园网认证网关
- MIT 许可证开源
