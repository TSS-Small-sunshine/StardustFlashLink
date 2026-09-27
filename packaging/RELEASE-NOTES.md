Windows 安装程序 **v{{VERSION}} "Sirius"（天狼星）**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### ✨ 新增：网络位置守卫（只在校园网里干活）
- **Wi-Fi 名（SSID）白名单** + **网段（CIDR）白名单**：开启后，只有命中其中之一才执行登录检查。
  带回家 / 连热点 / 挂 VPN 时不再空跑认证请求，日志也不再刷「校园网不可达」。
- 配置页「自动化」卡片新增开关与两个输入框；状态页「网络可达性」副标题会显示当前 Wi-Fi 名。
- **默认关闭** —— 不开启时行为与之前完全一致；**断网判断不了时一律放行**（fail-open），
  不会因为读不到 Wi-Fi 名就静默不登录。有线 / 宿舍网走网段白名单即可。
- 接口向后兼容：只是配置**新增**字段（`PATCH` +1），老配置自动补默认值。

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
