Windows 安装程序 **v{{VERSION}} "Sirius"（天狼星）**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### ✨ 安装向导外观（多尺寸品牌图，与验证用 mock 参数一致）
- **向导左侧大图改用多尺寸 PNG**：`WizardImageFile` 列 5 档（202×386 → 430×824），Inno 6 会按当前
  DPI 自动挑最合适的一张。此前只写了一张 `wizard.bmp`，2K/4K 屏是放大插值的，而且那张图里
  还印着过期的 `v2.0.0`。
- **新增右上角小图** `WizardSmallImageFile`，同样 5 档多尺寸（58×58 → 124×124）。
- **暗色向导 + 放大尺寸**：`WizardStyle=modern dark includetitlebar hidebevels`、`WizardSizePercent=110`。
- **补回欢迎页**（`DisableWelcomePage=no`），欢迎 / 完成页文案与验证用 mock 对齐。
- 只动向导弹与打包资源：接口、配置结构、服务名、`AppId` 未动，可直接覆盖安装（`SERIAL` +1）。
- 参数与 `_ui_redesign/flashlink-mock.iss` 上真机逐页验证过的一致；本机 ISCC 6 直接编译真包通过
  （`Successful compile`）。

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
