# AGENTS.md — AI 助手 / 协作者编辑规范

> 任何「会改这个仓库的智能体」在动手前都请先读这份文件；人类协作者同样适用。
> 它记录的是**这个仓库独有的约束**（不变量、隐私红线、验证方式），不是通用编码建议。

## 0. 30 秒速览

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
# 第二个参数 = 要预览的检出目录。本仓库是多 worktree 开发（主检出 + 各版本线的 worktree），
# 拍哪条线就指哪个 worktree，截图也要落到那个 worktree 的 docs\（否则拍出来的是另一条线的页面）。
$repo = 'd:\Student_Workstation\Using_Workstation\_wt_2x'
$srv  = Start-Process -FilePath 'python' -ArgumentList '_ui_redesign\preview.py','18899',$repo -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$docs = "$repo\docs"
& $edge --headless --disable-gpu --no-sandbox --hide-scrollbars --window-size=1280,720 --virtual-time-budget=4000 --screenshot=$docs\screenshot-webui.png      'http://127.0.0.1:18899/?theme=light'
& $edge --headless --disable-gpu --no-sandbox --hide-scrollbars --window-size=1280,720 --virtual-time-budget=4000 --screenshot=$docs\screenshot-webui-dark.png 'http://127.0.0.1:18899/?theme=dark'
Stop-Process -Id $srv.Id -Force
```

- `preview.py` 里的假数据**必须**保持脱敏（账号形如 `2023******@yd`）；它同时是 `?theme=light|dark|baka` / `?tab=config|log|about` / `?probe=1` 等预览变体的入口。
- **主题用 `?theme=` 显式指定**：v2.1.2 起默认主题是 `baka`（星尘风），不写就拍成默认主题，和 README 上「亮 / 暗」两句配文对不上。
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
