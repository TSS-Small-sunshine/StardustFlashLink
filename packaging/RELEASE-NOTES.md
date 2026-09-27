Windows 安装程序 **v{{VERSION}} "Sirius"（天狼星）**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### 🔴 修复（v2.0.4.2 之后的两处收尾）
- **`AppExit` 自愈从 v2.0.4.0 起一直在误报**：`winreg.OpenKey` 的键路径**不能带 `HKLM\` 前缀**
  （HKLM 由 `HKEY_LOCAL_MACHINE` 常量给出），而代码一直传
  `HKLM\SYSTEM\...\Parameters\AppExit` → `FileNotFoundError` 被 `except OSError: return None`
  吞掉 → 每次开机都记一行「AppExit 自愈失败（仍是空值）」。真机对照：那个子键里
  `(默认)=Ignore`、`0=Ignore`，**策略一直是对的**。现在统一经 `_hklm_subpath()` 去前缀
  （幂等、大小写不敏感）。
- **升级执行器的结果不再被误删**：服务是安装器在 `ssPostInstall` 就拉起来的，那一刻 `.cmd`
  执行器**还在跑**（它在等 installer 进程退出，然后才归档日志、写 rc），而启动钩子上来就把它删掉
  → 后续步骤全部没执行（真机实测：`drcom_apply_update.rc` 与 `logs\installer-silent.log` 都没出现）。
  现在只清**陈旧**残留（默认 5 分钟），计划任务只在执行器已自删后才兜底删；执行器结果读取带
  8 秒宽限，并把安装日志**归档回 `{app}\logs\installer-silent.log`**。
- **上一版（v2.0.4.2）的自动升级链路已真机走通**：v2.0.4.1 → 2.0.4.2 一次成功 ——
  `方式=schtasks` → 4 秒装完 → `升级成功确认：已运行 v2.0.4.2`。
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
