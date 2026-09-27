Windows 安装程序 **v{{VERSION}} "Sirius"（天狼星）**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### 🔴 修复（自动升级：installer 被 nssm 的 Job 连坐杀掉 —— 真机实证）
- **现象**：v2.0.4.1 发布后本机自动升级一路"成功"（发现新版本 → 下载 11,310,892 B →
  SHA256 通过 → `installer 已启动 PID=51704`），但 **`version.py` 不变、
  `installer-silent.log` 压根没生成、服务停在 `StopPending`** → 自动登录直接停摆。
- **根因**：`setup.iss` 的 `CurStepChanged(ssInstall)` 会 `nssm stop DrcomAutoLogin`，而 nssm
  关闭自己的 Job Object 时会把**同 Job 的子进程一起杀掉** —— 由服务直启（`DETACHED_PROCESS`
  `|CREATE_BREAKAWAY_FROM_JOB`）的 installer 在复制文件前就被带走。
  对照组：**同一条命令行由不在该 Job 里的进程拉起 → 退出码 0、7.1 秒装完**。
- **修复**：改为写一个 `.cmd` 执行器 + `schtasks /create /ru SYSTEM /rl HIGHEST` + `/run`
  （由 Task Scheduler 托管，与 nssm 没有 Job 关系）；任务计划程序不可用时才退回旧的直启路径。
- **顺带修好的三处**：
  - **看门狗**：installer 跑完先查服务，没 `RUNNING` 就 `sc start`（升级失败不再"没人管"）。
  - **可诊断性**：`%TEMP%\drcom_apply_update.rc` 落盘 installer 退出码，安装日志复制进
    `{app}\logs\installer-silent.log`，启动钩子把两者写进 `upgrade.log`（此前失败无从查起）。
  - **`desktopicon` 任务**：`/TASKS=` 补上，升级后公共桌面快捷方式才会刷新。
- **另修两处误报/误判**：`AppExit` 改按 nssm 的真实结构（`Parameters\AppExit` 子键的
  `(默认)` / `0` 子值）读写，不再每次开机误报「自愈失败」；升级成功只认
  「尝试记录目标版本 == 当前版本」，不再因为"备份 hash 不同"把任何脚本改动误报成升级成功。
- 接口、配置结构、服务名、`AppId` 未动，可直接覆盖安装（`SERIAL` +1）。

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
