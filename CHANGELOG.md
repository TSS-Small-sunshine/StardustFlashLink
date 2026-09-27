# 更新日志

本项目的所有重要变更都会记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

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
