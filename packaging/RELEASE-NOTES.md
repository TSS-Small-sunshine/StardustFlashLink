Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 客户端断开降噪 + Web 面板 P3 收尾 -->

> **一句话**：面板轮询时客户端提前断开（切页面 / 关标签）不再被当成服务错误刷 `[ERROR]`、
> 也不再在「写 500 失败」时把 traceback 打进 `service_stderr.log`；顺手清掉一批 P3 ——
> SyntaxWarning 清零、绝对路径只显示折叠路径、日志轮询按页启停、切配置页只发一次请求。
> 接口、配置结构、服务名与 `AppId`、安装路径**都没动** ✓。

## 🛠 本版修复

### ① 客户端提前断开不再当服务错误（真机日志降噪）
- **真机证据**：2026-11-21 本机 `logs\campus_login.log` 出现 3 次
  `[ERROR] GET /api/metrics 异常: [WinError 10053]` —— 面板每 2 秒轮询 /api/metrics，
  切走页面 / 关标签 / 浏览器提前释放连接时服务端写响应会抛 `WinError 10053`。
- **新判据**：`ConnectionAbortedError` / `ConnectionResetError` / `BrokenPipeError`，
  以及带 `WinError` 10053 / 10054 / 10058 / 10061 的 `OSError` → 判为「客户端提前断开」。
- **降级行为**：GET / POST 兜底分支记一行 INFO「客户端提前断开（不是服务错误）」后直接收尾，
  **不再写 500**；非断开异常仍是 ERROR + 500，且 500 写失败时静默，不把二次异常冒到 stderr。
- **成功响应**：`_send_json` / `_send_bytes` 写一半被断开也只记 INFO（正常响应行为不变）。

### ② SyntaxWarning 清零 + Web 面板 P3 收尾
- **非法转义**：`web_api.py` / `auto_update.py` 的 Windows 路径 docstring 改 raw string，
  全仓 `.py` 编译零 SyntaxWarning（冒烟新增扫描断言）。
- **P3-3 路径上屏**：前端只显示折叠路径（如 `…\logs\campus_login.log`），完整路径放进悬停提示；
  「关于」页、日志路径、升级历史路径统一走一个 helper；后端 JSON 字段名不变。
- **P3-7**：日志文件列表不再 innerHTML 裸拼，改 `createElement` + `textContent`。
- **P3-13**：日志轮询只在「日志」页启停（boot 不再无条件启动，离开页面即停）。
- **P3-14**：切配置页只发一次 `/api/config`（升级设置字段合进同一次请求）。
- **P3-10 / P3-1 / P3-11**：去掉未使用的接口形参；删除零调用死代码；配置「读-改-写」全部纳入
  同一把 `CONFIG_LOCK`（含方案自动切换的读-判-切）。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含方案专属 `password.<方案名>.txt`）与 `logs\` 都保留。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 默认行为与上一版一致；本版只是降噪与界面 / 并发收尾，没有需要迁移的配置项。

## 📦 安装包

| 项 | 值 |
| --- | --- |
| 应用版本 | {{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}） |
| 文件名 | {{ASSET}} |
| 大小 | {{SIZE}} bytes（约 {{SIZE_MB}} MB） |
| SHA256 | `{{SHA256}}` |
| 构建提交 | `{{SHA}}` |
| 构建编号 | #{{RUN}} |
| 构建时间 | {{DATE}} |
| 系统要求 | Windows 10 / 11 x64（安装包已内嵌 Python 运行时） |

## ✅ 校验

```powershell
certutil -hashfile {{ASSET}} SHA256
# 期望结果：{{SHA256}}
```

> 自动升级客户端也会用同一份 SHA-256 做 **fail-closed** 校验（对不上就拒绝安装）。

## 📥 安装 / 升级

1. 下载 `{{ASSET}}`，双击运行（需要管理员权限：要注册系统服务）。
2. 首次使用：浏览器打开 `http://127.0.0.1:8848`，在「状态」页的「账户与登录密码」卡里填写账号、运营商与密码。
3. 已有旧版：直接运行新安装包即可覆盖升级，配置与密码保留；也可以等客户端自动升级。

> 固定下载链接（永远指向最新构建）：[releases/tag/installer](https://github.com/{{REPO}}/releases/tag/installer)

## 🔄 回滚

- 升级前会把代码文件整目录备份到安装目录的 `backup\<旧版本>\`（保留 7 天、至少 2 份）。
- 新版本起不来时执行器会自动回滚；也可在 Web UI「关于 → 更新 → 版本回滚」手动一键回退。
- 回滚**只动代码**，`config.json` / 密码 / 配置方案都不动。

## 📜 完整变更

- 与上一版对比：{{COMPARE_URL}}
- 逐条完整日志：[CHANGELOG.md]({{CHANGELOG_URL}})
- 上一版：v{{PREV_VERSION}}

---

<sub>本发布页由 CI 从 `packaging/RELEASE-NOTES.md` 自动渲染；文案规则见 [docs/RELEASING.md](https://github.com/{{REPO}}/blob/main/docs/RELEASING.md)。</sub>
