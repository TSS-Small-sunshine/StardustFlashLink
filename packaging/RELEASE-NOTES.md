Windows 安装程序 **v{{VERSION}}**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### 🔴 P0（阻断级）
- **P0-1** `web_api.py` 6 个未定义裸名修复 —— 「立即检查更新 / 立即升级 / 升级开关 / 升级历史」4 个功能恢复可用（修前运行时必 `NameError`）
- **P0-2** 自动升级 SHA256 校验改 **fail-closed**：`digest` 缺失即拒绝安装并删除已下载的安装器
- **P0-3** `password.txt` 读取跳过 `#` 注释行 + 模板内容清空（新装机不再把模板提示当密码去登录）
- **P0-7** `AppExit` 三处统一为 `Ignore`；`_post_upgrade_startup` 不再写回 `Restart`；「重启服务」改走 `nssm restart`

### 🟠 安全（P1）
- **P1-1（部分）** Web UI 加 `Host` 白名单（挡 DNS rebinding）+ 写接口强制 `X-Requested-With: DrcomUI` + `Origin` 同源校验
- **P1-2** 升级 `digest` 只信主源 `api.github.com`（删除 API 镜像 fallback）
- **P1-3** 静默自动升级**默认关闭** + 远端版本串白名单
- **P1-4** `/api/config/export` 不再打包明文密码
- **P1-5** 发布改**版本化非 prerelease** release，修复自动升级可用性

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
