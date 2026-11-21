Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 卸载与安装器卫生：补删备份配置、跳过商店假 python、清掉启动器残留 -->

> **一句话**：一次把「卸载 / 安装器」上的四个卫生问题收干净 ——
> 源码方式的「一并删除用户数据」漏删升级备份里的 `config.json` 副本（J2）；
> `install.bat` 可能把 Microsoft Store 的 `WindowsApps\python.exe` 假别名当解释器（J3）；
> 安装器里有一条没有任何进程创建的 `AppMutex` 死配置（J4）；卸载后 `{app}\启动UI.bat` 残留（J5）。
> 接口、配置结构、服务名与 `AppId`、安装路径**都没动** ✓。

## 🛠 本版修复

### ① 卸载「一并删除用户数据」补上 `backup\`（审计 J2）
- 自动升级每次把旧代码备份到 `{app}\backup\<旧版本>\`，里面固定放一份 `config.json` 副本；
  `uninstall.bat` 的 Y 分支只删根目录的 `config.json` / `password.txt` / `logs\`，
  备份目录整个留着 → 用户以为数据已删干净，配置副本还在 ✗。
- 现在 Y 分支一并 `rd /s /q` 掉 `backup\`；选「保留数据」时明确提示「备份目录里也有 config.json 副本」。
- 安装包卸载（Inno）本来就会清 `{app}\backup`，这条缺口只在源码方式的卸载脚本。

### ② `install.bat` 跳过 Microsoft Store 的假 `python.exe`（审计 J3）
- `where python` 可能第一个命中 `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`
  （App Execution Alias 占位存根，运行只会弹商店）→ 被当成解释器后服务反复启动失败 ✗。
- 候选路径带 `\WindowsApps\` 的一律跳过（`where python` 与 `py -3` 返回的 `sys.executable`
  两条路径同一策略）；全部落空时给可读中文提示（怎么装 Python / 用 `py` / 改用内嵌运行时安装包）。

### ③ 删掉 `setup.iss` 里的死 `AppMutex`（审计 J4）
- `AppMutex=DrcomAutoLogin-mutex-v2` 没有任何进程创建（服务用的是 `Local\DrcomAutoLoginService`），
  Inno 的「安装器已在运行」检查永远查不到它 → 删掉并就地注释说明原因。

### ④ 卸载不再残留 `{app}\启动UI.bat`（审计 J5）
- `启动UI.bat` 由安装器 `[Code]` 在安装时生成，不在 `[Files]` 清单 → Inno 默认不删；
  源码方式的 `uninstall.bat` 也不管它，卸载后会留下一个死启动器。
- 现在 `[UninstallDelete]` 显式登记该文件，`uninstall.bat` 也一并删除。

### ⑤ 文档：日志接口不脱敏是**有意设计**（审计 web §四）
- `/api/log_tail` 与 `/api/log_download` 返回未脱敏原始日志：只监听 `127.0.0.1` +
  三道请求校验，能读到的就是本机日志所有者本人；诊断包才脱敏。README 与 AGENTS.md 都写清，
  免得后人把这条当缺口去「修」。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含方案专属 `password.<方案名>.txt`）与 `logs\` 都保留。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 默认行为与上一版一致；本版只是上一版的补丁，没有需要迁移的配置项。

## 📦 安装包

| 项 | 值 |
| --- | --- |
| 文件名 | `{{ASSET}}` |
| 大小 | {{SIZE}} 字节（约 {{SIZE_MB}} MB） |
| SHA-256 | `{{SHA256}}` |
| 构建提交 | `{{SHA}}` · [构建运行]({{RELEASE_URL}}) |

## 🔍 与上一版对比

- 完整对比：[{{PREV_VERSION}}…{{VERSION}}]({{COMPARE_URL}})
- 逐条变更：[CHANGELOG]({{CHANGELOG_URL}})

> 本发布说明由 CI 从仓库模板渲染（版本 / 大小 / SHA-256 / 日期均自动注入）；
> 发现问题请带 `logs\campus_login.log` 与 `logs\upgrade.log` 反馈。