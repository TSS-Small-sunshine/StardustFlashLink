Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 请求阶段断开也静默（v2.1.3.13 补丁） -->

> **一句话**：v2.1.3.13 把「写响应时客户端断开」的 `[ERROR]` 降噪了，但真机复验发现
> **读请求行 / 请求头时被连接重置**仍会让 `socketserver` 把 traceback 打进 `service_stderr.log` ——
> 本版复用同一个断开判据把这一半也收掉。接口、配置结构、服务名与 `AppId`、安装路径**都没动** ✓。

## 🛠 本版修复

### ① 请求读取阶段的断开不再打 traceback（真机复现）
- **v2.1.3.13 真机复验**：连续制造连接重置后，业务日志已正确记录
  `[INFO] 响应 200 时客户端提前断开（不是服务错误，已静默收尾）` ✓，
  但 `service_stderr.log` 仍出现 `handle_one_request` 里的 `ConnectionResetError` traceback ✗。
- **根因**：兜底只在路由之后；`BaseHTTPRequestHandler` 读请求行时就会抛 `ConnectionResetError`，
  异常冒到 `socketserver.process_request_thread` 的 `handle_error`，直接写 stderr。
- **修复**：`_Handler.handle_one_request()` 捕获断开类异常 → INFO 一行 + `close_connection = True`；
  非网络异常照旧抛出（不吞真正的 bug）。
- **验证**：静态冒烟 +3 条行为断言；发布后本机再跑同样探针，`service_stderr.log` 不再新增 traceback。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含方案专属 `password.<方案名>.txt`）与 `logs\` 都保留。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 默认行为与上一版一致；本版只是上一版的补丁，没有需要迁移的配置项。

## 📦 安装包

| 项 | 值 |
| --- | --- |
| 文件名 | `{{ASSET}}` |
| 大小 | {{SIZE}} 字节（约 {{SIZE_MB}} MB） |
| SHA-256 | `{{SHA256}}` |
| 构建提交 | `{{SHA}}` · [构建运行]({{RELEASE_URL}}) |

## 🔍 与上一版对比

- 完整对比：[{{PREV_VERSION}}…{{VERSION}}]({{COMPARE_URL}})
- 逐条变更：[CHANGELOG]({{CHANGELOG_URL}})

> 本发布说明由 CI 从仓库模板渲染（版本 / 大小 / SHA-256 / 日期均自动注入）；
> 发现问题请带 `logs\campus_login.log` 与 `logs\upgrade.log` 反馈。