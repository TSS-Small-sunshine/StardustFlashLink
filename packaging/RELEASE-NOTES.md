Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 卸载可预览：uninstall.bat --check 先看清单再决定卸不卸 -->

> **一句话**：卸载前终于可以「先看后删」—— `uninstall.bat --check`（兼容 `/check`）
> 只读打印「如果现在卸载会做 / 会删什么」：服务状态、NSSM、托盘自启项与偏好键、快捷方式、
> `config.json` / `password.txt` / `logs\` / `backup\`，不要求管理员、不弹 UAC、一个字都不改；
> 顺手把三个文件里写错的「UTF-8 BOM」编码注释改成事实。
> 接口、配置结构、服务名与 `AppId`、安装路径**都没动** ✓。

## 🛠 本版更新

### ① 卸载脚本新增只读预览（`uninstall.bat --check`，本轮重点）
- 以前想确认「卸载会删什么」，只能真跑一遍（不可逆），或者自己去敲 `sc query` / `reg query`。
- 现在在「自提升管理员」之前识别 `--check`（兼容 `/check`），打印清单后 `exit /b 0`：
  - 服务 `DrcomAutoLogin` 当前状态（`sc query` 只读）；
  - `tools\nssm.exe` 是否存在与大小；
  - 托盘自启值 / 托盘偏好键是否存在（`reg query` 只读）；
  - 桌面 / 开始菜单快捷方式是否存在（PowerShell `Test-Path` 只读）；
  - `启动UI.bat` / `config.json` / `password.txt` / `logs\` / `backup\` 是否存在与大小
    （目录还给出文件数与占用）；
  - 结尾明确「只读预览，没有修改任何东西」，并提示真卸载要跑不带参数的本脚本。
- 预览分支不要求管理员、不弹 UAC，不执行任何停止 / 删除 / 注册表写入命令；
  **不带参数运行时的行为与文案逐字不变**（只改版本号）。

### ② 编码注释与事实对齐（只改注释，不动字节）
- `install.bat` / `uninstall.bat` / `packaging/setup.iss` 的头注释此前写「UTF-8 BOM」，
  但三个文件**实际都没有 BOM**。
- 事实是：`.bat` 用「UTF-8（无 BOM）+ 首行 `chcp 65001`」才正确；给 bat 加 BOM 会让首行
  `@echo off` 变成乱码命令。`.iss` 无 BOM 时 ISCC 6.7.1 实测读取正常（安装后注册表
  `DisplayName` 中文正常）；将来若要加 BOM，必须先确认 ISCC 行为与 CRLF 规则。
- `.bat` / `.iss` 仍全部 **CRLF、无 BOM**（冒烟测试新增字节级断言）。

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
