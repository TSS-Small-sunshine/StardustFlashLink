Windows 安装程序 **v{{VERSION}} "Sirius"（天狼星）**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### 🔴 修复（升级链路：不再「反复重装却装不上」）
- **启动钩子从未生效**：`_post_upgrade_startup()` 在 `_attach` 之前执行，`LOG_DIR` 还是 `None` → 每次开机 `NameError`，`AppExit` 自愈与升级结果确认全部失效。现在归位到 `_attach` 之后。
- **静默安装弹窗挂起**：服务未在 30 秒内停止时安装器弹 `MsgBox`，而自动升级无人在场 → 安装永久挂起。现在静默模式只写日志，绝不弹窗。
- **升级改用 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL`**，并落盘 `/LOG=%BASE%\logs\installer-silent.log`，装不上时有据可查。
- **新增升级熔断**：`logs\update_attempt.json` 记录「目标版本 / 尝试次数 / 是否生效」；同版本连续 3 次未生效即停止自动重试并提示手动安装，失败后 6 小时冷却。
- **`AppExit` 空值自愈**：空字符串不再被当成合法值（此前会让 `nssm` 刷 `Parameter "AppExit" requires a subparameter!`）；优先用 `nssm.exe` 重写 `Default Ignore`。

### 🔴 修复（其它）
- **`#` 开头的密码被整行吃掉**：`_load_password_from_disk` 不再把「`#` 开头」当注释，只跳过安装包自带的模板提示行；并用 `utf-8-sig` 读，容忍 BOM。修前表现为登录失败 + Web UI 显示「密码未设置」，看着像升级把配置弄丢了。
- **「关于 → 查看更新日志」报缺文件**：安装包补打包 `CHANGELOG.md`，服务端按「安装目录 → `docs\` → 上一级」查找，缺失时给中文提示 + 仓库链接（不再暴露裸路径异常）。

### 🧹 文案 / 一致性
- 启用版本线代号：`2.0` 线 = `Sirius` / 天狼星（见 `docs/VERSIONING.md`）；Web UI 徽章与「关于」页显示 `v2.0.4.0 Sirius`，安装器显示名为 `星尘闪连 (Stardust Flash Link) 2.0.4.0 "Sirius"`。
- README 版本记录改为版本线摘要；新增 `AGENTS.md`（编辑规范）与 `docs/VERSIONING.md`（版本号 / 代号规则）。
- README 截图重拍为当前版本，且**账号已打码**（`2023******@yd`）。

### 🔒 隐私与安全
- 新增**隐私守卫**冒烟断言：把本机 `password.txt` / `config.json` 的账号与所有入库文件比对，命中即失败（`DRCOM_DATA_DIR` 可指向安装目录）。
- `.gitignore` 补 `*.log` / `update_attempt.json` / `installer-silent.log` / `config-export-*.zip`；测试夹具全部改为合成值。

## 安装包信息

| 项 | 值 |
| --- | --- |
| 应用版本 | {{VERSION}} "Sirius" |
| 文件名 | {{ASSET}} |
| 大小 | {{SIZE}} bytes（约 {{SIZE_MB}} MB） |
| SHA256 | {{SHA256}} |
| 构建提交 | {{SHA}} |
| 构建编号 | #{{RUN}} |
| 构建时间 | {{DATE}} |
| 系统要求 | Windows 10/11 x64（安装包已内嵌 Python，无需预装） |

安装后会自动注册系统服务、创建桌面与开始菜单快捷方式。
首次使用请通过 Web UI（`http://127.0.0.1:8848`）填写账号与密码。

> **升级本版前请注意**：若你的机器上 `nssm` 的 `AppExit` 曾被写成空值，需要**管理员**执行一次
> `nssm set DrcomAutoLogin AppExit Default Ignore`；服务下次启动时也会尝试自愈。

## 用户机器验证步骤（装包后跑）

```
Get-Service DrcomAutoLogin | Select-Object Status, StartType
Get-Content "D:\Program Files\DrcomAutoLogin\logs\service_stderr.log" -Tail 30
Get-Content "D:\Program Files\DrcomAutoLogin\logs\campus_login.log" -Tail 50 | Select-String -Pattern "NameError|ModuleNotFound"
Get-Content "D:\Program Files\DrcomAutoLogin\logs\upgrade.log" -Tail 20
```

**预期**：`Status=Running`；stderr 无 `ModuleNotFoundError`；`campus_login.log` 无 `NameError`；
`upgrade.log` 不再出现「几十秒一轮的下载 → 装 → 重启」死循环。

## 校验

```
certutil -hashfile {{ASSET}} SHA256
```

> 本 release 是**自动升级通道**（非 prerelease）：客户端读 `/releases/latest` 取版本号与 asset `digest`（fail-closed 校验）。
> 固定下载链接仍见 prerelease `installer`。

## 安装包信息

| 项 | 值 |
| --- | --- |
| 应用版本 | {{VERSION}} |
| 文件名 | {{ASSET}} |
| 大小 | {{SIZE}} bytes（约 {{SIZE_MB}} MB） |
| SHA256 | {{SHA256}} |
| 构建提交 | {{SHA}} |
| 构建编号 | #{{RUN}} |
| 构建时间 | {{DATE}} |
| 系统要求 | Windows 10/11 x64（安装包已内嵌 Python，无需预装） |

安装后会自动注册系统服务、创建桌面与开始菜单快捷方式。
首次使用请通过 Web UI（`http://127.0.0.1:8848`）填写账号与密码。

## 用户机器验证步骤（装包后跑）

```
Get-Service DrcomAutoLogin | Select-Object Status, StartType
Get-Content "D:\Program Files\DrcomAutoLogin\logs\service_stderr.log" -Tail 30
Get-Content "D:\Program Files\DrcomAutoLogin\logs\campus_login.log" -Tail 50 | Select-String -Pattern "NameError|ModuleNotFound"
```

**预期**：`Status=Running`；stderr 无 `ModuleNotFoundError`；`campus_login.log` 无 `NameError`。

## 校验

```
certutil -hashfile {{ASSET}} SHA256
```

> 本 release 是**自动升级通道**（非 prerelease）：客户端读 `/releases/latest` 取版本号与 asset `digest`（fail-closed 校验）。
> 固定下载链接仍见 prerelease `installer`。
