Windows 安装程序 **v{{VERSION}} "Sirius"（天狼星）**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### 🔧 修复（相对 v2.0.6.0）
- **托盘自启项改挂 `HKLM`**：原来是 `HKCU`，而自动升级以 SYSTEM 身份运行安装器 →
  会写进 SYSTEM 的配置单元，真实用户登录时托盘**不会自启**。现在对每个登录用户都生效。
- **卸载后不再留孤儿图标**：安装目录里找不到自己的脚本（连续两次轮询）时托盘自行退出。

### ✨ 新增：托盘图标 + 断线通知
- 装完会多一个**通知区图标**（默认随开机启动，安装向导里可取消勾选）。掉线、恢复、
  服务未响应时会弹**气泡提醒**，不用再自己盯着网页看。
- 右键图标：**打开配置页** / **立即登录** / **打开日志目录** / **退出托盘**；双击图标打开配置页；
  鼠标悬停看当前状态（含当前 Wi-Fi 名）。
- 通知有**防刷屏**：刚开机不弹；偶发一次连不上服务不弹（连续 3 次 ≈30 秒才报「服务无响应」）；
  同一状态不重复弹。
- 它是个独立的小程序（`tray.py`，随登录会话运行）—— 服务本身跑在后台会话里，**没法直接弹通知**。
  只用管理员权限才能在 Program Files 写日志，所以托盘自己的日志在
  `%LOCALAPPDATA%\DrcomAutoLogin\tray.log`。
- 接口、配置结构、服务名、`AppId` 全未动（`PATCH` +1），老配置与升级路径不受影响。

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
(Invoke-WebRequest http://127.0.0.1:8848/api/config -UseBasicParsing -TimeoutSec 10).StatusCode   # 必须是 200，且不能挂住
Get-Content "D:\Program Files\DrcomAutoLogin\logs\service_stderr.log" -Tail 30
Get-Content "D:\Program Files\DrcomAutoLogin\logs\campus_login.log" -Tail 50 | Select-String -Pattern "NameError|ModuleNotFound"
Get-Content "D:\Program Files\DrcomAutoLogin\logs\upgrade.log" -Tail 20
```

**预期**：`Status=Running`；`/api/config` 立刻 200（配置页能读到值、保存立即生效）；
stderr 无 `ModuleNotFoundError`；`campus_login.log` 无 `NameError`；
`upgrade.log` 不再出现「几十秒一轮的下载 → 装 → 重启」死循环。
自动升级过的话，`upgrade.log` 里还应看到
`升级执行器结果：installer_rc=0 / service=RUNNING`（v2.0.4.2 新增的退出码与看门狗回报）。

## 校验

```
certutil -hashfile {{ASSET}} SHA256
```

> 本 release 是**自动升级通道**（非 prerelease）：客户端读 `/releases/latest` 取版本号与 asset `digest`（fail-closed 校验）。
> 固定下载链接仍见 prerelease `installer`。
