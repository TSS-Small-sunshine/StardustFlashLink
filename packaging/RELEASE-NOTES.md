Windows 安装程序 **v{{VERSION}}**（Inno Setup 自动构建 · 安装包已内嵌 Python，无需预装）。

## 本版变更

### ✨ 界面（重做）
- **Web UI 换成 Apple 风格亚克力玻璃界面**：亮 / 暗两套设计令牌、`backdrop-filter: saturate(180%) blur(30px)` 毛玻璃材质 + 发丝描边 + 内侧高光
- 系统字体栈（`-apple-system` / `SF Pro Text` / `PingFang SC` / `Microsoft YaHei UI`），数字与日志用等宽 + `tabular-nums`（倒计时不抖动）
- 两行品牌锁排 + 分段控件（Segmented Control）+ 胶囊按钮 + iOS 样式开关 + 聚焦光圈
- 图标由 emoji 换成 1.7 描边线性 SVG，各系统渲染一致；新增 ≤720px / ≤480px 响应式与 `prefers-reduced-motion`

### 🔴 修复
- **顶栏 logo 与 favicon 404（破图）**：新增 `/branding/<name>` 静态路由 —— 文件名白名单
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.(png|ico|svg|jpe?g|webp|gif)$`（结构性阻断路径穿越）+ `nosniff` + 1h 缓存
- 安装包随包拷贝 `branding\web-logo-*.png` 到 `{app}\branding`，卸载时清理
- 前端 logo 加载失败时用内联星芒标记兜底，不再出现破图图标
- 修 `pollUpdate` 中 `banner` 未定义导致的 `ReferenceError`（升级成功横幅 7 秒自动隐藏此前一直失效）
- 修 `ensureChangelogShortcut` 引用不存在的 `--primary` 变量（快捷链接颜色）

### 其它
- 版本字面量统一到 `2.0.3.0`（`version.py` / `setup.iss` / NSSM 描述 / `install.bat` / `uninstall.bat` / `build.ps1`）
- README 补上真实 Web UI 截图（亮色 / 暗色），此前 `docs/screenshot-*.png` 缺失导致 README 图片也是破图

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
