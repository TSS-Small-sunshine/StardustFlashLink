# AGENTS.md — AI 助手 / 协作者编辑规范

> ## ⚠️ 这个仓库有**两条线**，规范不一样，别拿错 ✓
>
> | 线 | 在哪 | 语言 / 界面 | 规范看哪 |
> | --- | --- | --- | --- |
> | **2.x「Vega」稳定线** | `main` 分支的仓库根（`*.py` / `packaging/` / `tray.py` …） | Python 3 + 内嵌运行时 + 托盘 + Web UI | **本文件 §1–§8** ✓ |
> | **3.0「Altair」跨平台线** | **本分支**（`dev/3.0-altair`）的 `desktop/` | **Rust + Slint 原生界面**（零第三方依赖 ✓） | **本文件 §9** ＋ [`desktop/README.md`](desktop/README.md) ＋ [`docs/PLATFORMS.md`](docs/PLATFORMS.md) ✓ |
>
> 在 3.0 分支上动 `desktop/` 时 **§9 优先** ✓；§1–§8 只在「对照 2.x 行为、翻根目录那些 `.py`」时才适用 ✓。
> 任何「会改这个仓库的智能体」动手前都请先读这份文件；人类协作者同样适用。

## 0. 30 秒速览（**2.x 线**，`main` 分支）

| 事项 | 结论 |
| --- | --- |
| 语言 / 依赖 | Python 3，**只用标准库，零第三方依赖**；目标机 Windows 10 / 11 x64 |
| 前端 | **全部内联在 `web_api.py` 的 `_HTML_PAGE`**：无 CDN、无构建步骤、无新增静态文件 |
| 版本 | 唯一来源 `version.py`；规则见 [`docs/VERSIONING.md`](docs/VERSIONING.md) |
| 改完必跑 | `python _smoke_static.py` + `python _smoke_http.py`（两个都必须 0 失败） |
| 隐私 | 真机 `password.txt` / `config.json` / `logs\` / 真机截图 **永不入库**（见 §5） |
| 提交 | 逐文件 `git add`，**禁止 `git add .`**（`tools\` / `python\` / `logs\` / `packaging\output\` 都在工作区里） |

## 1. 动手前必读

1. 本文件（§2 不变量、§5 隐私红线）
2. [`README.md`](README.md)：模块划分、线程模型、退避表、运行期文件表
3. [`docs/VERSIONING.md`](docs/VERSIONING.md)：改版本号时才需要，但**同步清单必须逐条对齐**
4. [`CHANGELOG.md`](CHANGELOG.md)：这个坑以前有没有栽过（历史修复都在里面）
5. `packaging/setup.iss`：安装 / 卸载 / 升级行为都在这里，改文件清单别漏 `[Files]`

## 2. 硬约束（不变量，改前先想清楚）

| # | 不变量 |
| --- | --- |
| I1 | NSSM 服务名 `DrcomAutoLogin`、`AppId`、默认安装路径 `%ProgramFiles%\DrcomAutoLogin` **不变**（存量升级依赖） |
| I2 | 版本号唯一来源 `version.py`；`setup.iss` / `install.bat` / `uninstall.bat` / `build.ps1` / docstring 必须同步 |
| I3 | 前端继续内联在 `web_api.py`；不引入 npm / CDN / 外部字体 / 新静态文件 |
| I4 | 不新增 Python 依赖（含「顺便用一下」的第三方包） |
| I5 | 既有 API 路径与字段名**只增不改不删**（Web UI 与自动升级客户端都依赖） |
| I6 | 密码只存在 `password.txt`；源码 / 注释 / 日志 / API 响应零明文凭据 |
| I7 | NSSM `AppExit` 单一口径 = `Default Ignore` + `0 Ignore`，**不写 `Restart`** |
| I8 | `password.txt` / `config.json` 安装时 `onlyifdoesntexist`，升级**不得覆盖**用户数据 |
| I9 | 安装包产物名固定 `StardustFlashLink-Setup-v{VERSION}.exe`（存量下载链接依赖） |
| I10 | 每次发版必须有**非 prerelease** 的 `v{VERSION}` release，否则自动升级取不到版本号与 `digest` |
| I11 | Web UI 只监听 `127.0.0.1`；Host 白名单 / `Origin` 同源 / `X-Requested-With: DrcomUI` 三道校验不得削弱 |
| I12 | 真机凭据与真机截图永不入库（§5）；测试夹具只用合成值 |

## 3. 代码风格

- **中文注释、中文日志、中文用户可见报错**（面向中文用户；模块 docstring 先写职责）。
- **结构性防御优先**：路径 / 文件名用白名单正则（如 `/branding/<name>` 的 `_BRANDING_NAME_RE`），不要用黑名单 `replace("..","")`。
- **读写文本显式编码**：文件用 `encoding="utf-8-sig"`（容忍 BOM）；JSON 同理。
- **面向用户的错误要能读懂**：写「本机没有更新日志文件…」而不是 `str(exc)`；不要把裸绝对路径抛给界面。
- **跨模块调用写全名**：`_auto_update_mod._do_update_now()`；`web_api.py` 里写裸名已经栽过两次（v2.0.2.2 / v2.0.2.4 的 `NameError`）。
- **并发沿用现有锁**：`STATE_LOCK` / `PWD_LOCK` / `RUN_LOCK` / `UPDATE_LOCK`，不要新造一套风格。
- **不改 `.gitattributes` 的 CRLF 规则**：`*.bat` / `*.ps1` / `*.iss` 必须 CRLF，否则用户解压 zip 直接跑会报错。

## 4. 验证纪律

- 改任何逻辑 → 跑 `python _smoke_static.py`、`python _smoke_http.py`；两个都必须 0 失败。
- **修 bug 必须顺手加断言**（否则下次回归没人拦得住）；断言优先测**行为**，其次才是字符串（例如测 `api_get_changelog()` 的返回状态与文案，而不是 grep 源码里有没有某句话）。
- 真机行为与代码推理冲突时 → **信真机**，并把真机证据（日志 / 截图 / 退出码）写进 CHANGELOG。
- 涉及服务 / 安装器 / NSSM 的改动，在真机上跑一遍：`Get-Content logs\service_stderr.log -Tail 30`、`logs\campus_login.log`、`logs\upgrade.log`。

## 5. 隐私红线（P0，违反即回滚）

1. **绝不**把真机 `password.txt` / `config.json` 的内容写进源码、注释、docstring、测试夹具、CHANGELOG、commit message、Release notes、截图。
2. 测试夹具用**一眼假的合成值**：例如 `#Demo-Pwd-0000#`、`pwd_with_bom`，不要照抄真机密码。
3. 日志 / API 响应永不返回密码（状态接口只回 `password_status: set|missing`）。
4. 提交前跑 `python _smoke_static.py` —— 里面的**隐私守卫**会把本机 `password.txt` / `config.json` 的账号拿去和所有入库文件比对，命中即失败。
   想连安装目录一起查：`$env:DRCOM_DATA_DIR = 'D:\Program Files\DrcomAutoLogin'`（CI 上无这些文件会自动 SKIP）。
5. 提交前 `git status --short` 逐行看过再 `git add <文件>`；**永远不要 `git add .`**。
6. 一旦凭据真的被推上远端：**先改密码**，再做历史清理（`git filter-repo` / BFG）并通知协作者重新克隆 —— 单纯 `git revert` 不解决问题（历史里还在）。

## 6. 截图规范

README 的截图必须由**假数据渲染**，账号一律打码。工具在仓库外的 `_ui_redesign\`（本地开发工具，不入库）：

```powershell
cd d:\Student_Workstation\Using_Workstation
$srv  = Start-Process -FilePath 'python' -ArgumentList '_ui_redesign\preview.py','18899' -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$docs = 'd:\Student_Workstation\Using_Workstation\DrcomAutoLogin-Windows\docs'
& $edge --headless --disable-gpu --no-sandbox --hide-scrollbars --window-size=1280,720 --virtual-time-budget=4000 --screenshot=$docs\screenshot-webui.png      'http://127.0.0.1:18899/'
& $edge --headless --disable-gpu --no-sandbox --hide-scrollbars --window-size=1280,720 --virtual-time-budget=4000 --screenshot=$docs\screenshot-webui-dark.png 'http://127.0.0.1:18899/?theme=dark'
Stop-Process -Id $srv.Id -Force
```

- `preview.py` 里的假数据**必须**保持脱敏（账号形如 `2023******@yd`）；它同时是 `?theme=dark` / `?tab=config|log|about` / `?probe=1` 等预览变体的入口。
- 拍完**肉眼看一眼**：账号打码了、版本徽章是当前版本、没有真实网关 / 账号 / 学号。
- 只提交 `docs\screenshot-webui*.png` 这两张（亮 / 暗）。

## 7. 发布 checklist

```
# 1) 版本字面量同步（§2 I2 + docs/VERSIONING.md §2 清单）
# 2) 写 CHANGELOG.md 新段落 + 更新 packaging/RELEASE-NOTES.md
# 3) 本地自检
cd DrcomAutoLogin-Windows
python _smoke_static.py
python _smoke_http.py
# 4) 逐文件提交（禁止 git add .）
git status --short && git diff --cached
# 5) 推分支 → PR → 合并 main → CI 构建（版本分支用 workflow_dispatch）
gh workflow run "Build Windows Installer" -R TSS-Small-sunshine/StardustFlashLink --ref main
# 6) CI 产出两条 release：installer（prerelease，固定链接）+ v{VERSION}（非 prerelease，升级通道）
```

## 8. 常见坑（都真实踩过）

| 坑 | 说明 |
| --- | --- |
| `--headless=new` 截图不落盘 | 用 `--headless`（旧无头模式）才写文件；必要时加 `--user-data-dir` 独立 profile |
| 静默安装里弹 `MsgBox` | 自动升级跑在服务会话（无人在场），弹窗会让安装**永久挂起** → 必须 `if WizardSilent then` 守卫 |
| 启动钩子拿不到 `LOG_DIR` | `_post_upgrade_startup()` 必须放在 `*_mod._attach()` **之后**，否则每次开机 `NameError` 且静默失效 |
| `#` 开头被当注释 | 密码可以以 `#` 开头；只有安装包模板提示行（`_PASSWORD_HINT_MARKERS`）才算注释 |
| `AppExit` 空字符串 | NSSM 会报 `Parameter "AppExit" requires a subparameter!`；空值必须视为无效并自愈 |
| PowerShell 看 UTF-8 文件乱码 | 是控制台编码问题，文件本身正常；用 `read_files` / 编辑器看 |
| `git add .` | 会带上 `tools\`、`python\`、`logs\`、`packaging\output\`、`__pycache__\` |
| 前端加 emoji | `_smoke_static.py` 有「页面内无 emoji」断言；图标统一用 1.7 描边线性 SVG |

---

## 9. 3.0「Altair」线的规范（`desktop/`，Rust + Slint）

> **只要你在动 `desktop/` 里的东西，§9 优先于 §1–§8** ✓
> （§1–§8 是 2.x 的规范；只有在「对照 2.x 行为、翻根目录那些 `.py`」时才适用 ✓）

### 9.1 硬约束（不变量）

| # | 不变量 |
| --- | --- |
| **R1** | **零功能型第三方依赖** ✓：`drcom-core` 只用 `std` ＋ 框架必需的那几个（`serde` / `serde_json`）；界面层只有 `slint`。想引入新的 crate 先讨论 ✗ —— 「就顺手用一下」也不行 ✗ |
| **R2** | **跨平台同源** ✓：平台差异一律收在 `core::platform`（`Os` 枚举）或各模块的纯函数里，**不许把 `cfg!(...)` 散落在业务逻辑中** ✗ |
| **R3** | **配置 / 密码格式与 2.x 完全一致** ✓：`config.json` 键名不变、`password.txt` 一行一密码 + `#` 注释 —— 「老用户零迁移」是产品承诺 ✓ |
| **R4** | **Windows 服务名固定 `DrcomAutoLogin`** ✓（`service::WINDOWS_SERVICE_NAME`，**有单测锁死** ✗ 别改 ✓）；它与 2.x 是**故意共用**的 ✓，所以「查到服务在跑」可能是 2.x 那个 ✓，遇到同名要**给接管路径**而不是硬装 ✗ |
| **R5** | **协议不变** ✓：登录仍是 `:801/eportal/portal/login`、在线探测 `/drcom/chkstatus`、在线判定用 JSONP `result` ✓ |
| **R6** | **3.0 只发预览版** ✓（`Channel::Preview`）：正式用户的自动升级**收不到它** ✓；**不许往 `main` 合** ✗；不许动 `version.py`（那是 2.x 的版本源 ✗） |
| **R7** | **界面只做展示与调用** ✓：所有判断 / 校验 / 拼装 / 写盘都在 `drcom-core` 的纯函数里（这样单测才覆盖得到 ✓，界面层尽量薄 ✓） |
| **R8** | **NSSM 口径沿用 2.x** ✓：`AppExit` 写 `Default Ignore` ＋ `0 Ignore`，**绝不写 `Restart`** ✗（重试归程序自己的退避管 ✓，有单测断言 ✓） |

### 9.2 动手前后必做

```bash
# 改完任何东西（哪怕只改注释）
cd desktop
cargo test --workspace          # 必须全绿 ✓（现在是 186 项）

# 改动过 .slint 或界面 Rust 代码 → 还必须重建 GUI 二进制 ✗
cargo build -p drcom-ui         # 只跑 cargo test **不会**刷新 GUI 二进制 ✗（真踩过 ✓）

# 提交前（沿用 §5 的谨慎 ✓）
cd ..
git status --short && git diff --cached
```

### 9.3 写单测的规矩（3.0 最值钱的就是测试 ✓）

- **纯函数才配有单测** ✓：命令拼装 / 解析 / 校验 / 生成物 → 一律写成纯函数，逐字断言 ✓
- **真机样本优先** ✓：解析类测试要用**真实抓到的样子**（例如校园门户那条完整的
  `a79.htm?mac=…&wlanacip=172%2e16%2e80%2e2&wlanacname=SR8806%2dX%2dS&wlanuserip=…` ✓）
- **外部互验** ✓：自己实现的东西必须拿第三方结果对一遍 ——
  SHA-256 对 NIST 官方向量 **＋** Windows `Get-FileHash` ✓；自研 ZIP 用 .NET `ZipFile` 打开 ✓
- **写盘类逻辑要给两个出口** ✓：`--dry-run` 只打印不动系统 ✓；查状态一律只读
  （Windows 用 `sc query`，**不需要管理员** ✓）
- **不做摆设** ✓：平台不支持的先查清（例如 Slint 1.18 的 winit 后端没实现托盘 ✗）——
  **宁可推迟并写清原因，也不给用户一个点不动的按钮** ✗

### 9.4 隐私红线（与 §5 同等严格 ✓）

1. 真机 `config.json` / `password.txt` 内容**永不**进源码、注释、测试夹具、CHANGELOG、
   commit message、release notes、截图 ✓
2. 测试夹具用一眼假的合成值（如 `2023001234` / `#Demo-Pwd-0000#` ✓）
3. **登录 URL 里带密码** ✗ → 任何日志 / 界面回显 / 诊断包都必须过 `secret::scrub_url` ✓
4. 诊断包里 `password.txt` **永不入包** ✓、账号只留前 4 位 ✓
5. 设置窗口的密码框：**永不回显** ✓（打开时是空的，空 = 不修改 ✓）

### 9.5 CI 与发布

- 这条线只由 [`.github/workflows/preview-3.0.yml`](.github/workflows/preview-3.0.yml) 管 ✓：
  三平台跑测试 → 六目标构建 → **打包**（自带 ZIP ＋ `SHA256SUMS` ✓，零外部工具 ✓）
- **不许动 `build-installer.yml` 的触发面** ✗（那是 `main` 的 2.x 发版链路 ✓）
- 打包命令：`package-files --out pkg --target <标签> --exe <名字>` →
  `archive --out dist/<基名>.zip pkg` → `checksum` ✓（交叉编译**必须显式 `--target`** ✗，
  宿主自报的目标是错的 ✓）
- 发预览版时按 [`docs/VERSIONING.md`](docs/VERSIONING.md) 递增 `APP_CHANNEL_SEQ` ✓
  （别碰 `main` 的 `version.py` ✗）

### 9.6 提交风格（两条线一致 ✓）

- 提交信息前缀用 `feat(3.0-mX)` / `fix(3.0)` / `docs(3.0)` / `ci(3.0-mX)` ✓
- **逐文件 `git add`**，`git add -A` 只在确认工作区干净时用 ✓
- 每完成一块就推 `dev/3.0-altair`，并确认 CI **六行全绿** ✓


