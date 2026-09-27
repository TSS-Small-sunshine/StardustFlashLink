Windows 安装程序 **v{{VERSION}} "Sirius"（天狼星）**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### 🔴 修复（PWD_LOCK 自锁死锁：Web UI 配置页打不开、自动登录停摆）
- **`GET /api/config` 会永久挂起**：`api_get_config()` 在 `with PWD_LOCK:` 里又调了自带
  `with PWD_LOCK` 的 `_get_password()` —— `PWD_LOCK` 是不可重入的 `threading.Lock`，同一线程
  二次获取即死锁（v1.x 单文件版这里是直接读 `_PWD_VALUE`，模块化拆分时成了回归）。
- 表现：配置页字段全空、徽标显示「状态未知」、**保存请求也一起卡死**（`_save_password_to_disk`
  抢同一把锁），刷新后看着像"编辑完又没了"；**周期性自检线程同样卡在 `_get_password()` 上
  → 自动登录实际已停摆**（`last_check_at` 不再推进）。
- 修复：只调 `_get_password()`（加锁责任在它内部），并把这条约定写进注释；冒烟测试新增
  3 条静态断言 + 真实调用 `api_get_config()` 的 3 秒超时回归（再犯即 FAIL）。
- 已装机器：把 `web_api.py` 换成本版并 `Restart-Service DrcomAutoLogin` 即可，
  `config.json` / `password.txt` 不受影响。

### ✨ 安装器外观收口
- **桌面快捷方式**：图标从 `{sys}\shell32.dll,13`（Windows 通用图标）换成品牌 `app.ico`
  （7 个尺寸，256 → 16 px）；安装包新增分发 `{app}\branding\app.ico`。
- **开始菜单项**：`.url` 补 `IconFile` / `IconIndex`，不再显示浏览器默认图标。
- **「应用和功能」卸载项**：`UninstallDisplayIcon` 改用品牌图标。
- 服务名、`AppId`、配置结构、CLI / API 一律未动，可直接覆盖安装（`SERIAL` +1）。

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

**预期**：`Status=Running`；`/api/config` 立刻 200（死锁已修，配置页能读到值、保存立即生效）；
stderr 无 `ModuleNotFoundError`；`campus_login.log` 无 `NameError`；
`upgrade.log` 不再出现「几十秒一轮的下载 → 装 → 重启」死循环。

## 校验

```
certutil -hashfile {{ASSET}} SHA256
```

> 本 release 是**自动升级通道**（非 prerelease）：客户端读 `/releases/latest` 取版本号与 asset `digest`（fail-closed 校验）。
> 固定下载链接仍见 prerelease `installer`。
