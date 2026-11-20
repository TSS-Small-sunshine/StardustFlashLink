Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 重启服务修复版 -->

> **一句话**：这是 2.1 线的一次**紧急修复版** —— 修掉「Web UI 点『重启服务』后服务永久停止、
> 再也不会自己起来」的真机事故（2026-11-20 10:29 本机实测）。现在重启交给任务计划程序的
> 一次性任务在服务进程之外执行 `sc stop → sc start`，并带有界重试；提不起来会**明说原因**而不是假装成功。
> 接口、配置结构、服务名与 `AppId`、安装路径、密码文件格式**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## 🛠 本版修复

### ① 重启服务会「杀掉自己」（真机事故，P0）
- **现象**：Web UI「关于 → 重启服务」点下后，页面消失、服务再也没回来 ——
  `sc query DrcomAutoLogin` 一直是 STOPPED，自动登录与面板全部停摆。
- **真机日志**：`nssm restart 返回码 3221225786，退回 os._exit(0)`，随后进程收到 SIGINT 退出；
  3221225786 = 0xC000013A = **STATUS_CONTROL_C_EXIT**。
- **根因**：旧实现让服务**同步**跑 `nssm restart DrcomAutoLogin`。nssm restart 的 stop 半程会让 NSSM
  结束当前 Python 进程以及它拉起的 nssm.exe 子进程 —— `start` 半程永远跑不到。而 NSSM 的
  `AppExit` 策略是 **Ignore**（防端口冲突时死循环重启），进程退出后不会再被拉起 → 服务永久 STOPPED ✗。
- **修复**：把「等旧进程退出 → `sc stop` → `sc start`（有界重试）」交给**任务计划程序的一次性任务**
  （`DrcomAutoLogin-Restart`，SYSTEM 身份）。它不在 NSSM 的 Job Object 里，不会被「服务停自己」
  连带杀死；本进程只负责调度 + 正常退出。与自动升级执行器是同一套已被真机验证的机制 ✓。

### ② 不再静默失败
- 查不到服务状态 / 服务不是 RUNNING / 重启任务提不起来时：接口返回 **409 + 可读中文原因**，
  并且**不退出进程** —— 不会出现「按钮一按页面就没了、也不知道为什么」✗。
- 成功时明确告知：「服务将在约 16 秒内重启（页面会短暂不可用，之后自动恢复）」。
- 版本回滚（「关于 → 更新 → 版本回滚」）复用同一条重启路径：调度失败会把警告带进响应，不吞掉 ✓。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）
  与 `logs\` 都保留；安装器只在文件不存在时创建默认值。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 本版只改「重启服务」这一条路径（Web 按钮 + 回滚复用），**配置格式与 API 字段没有变化**，无需迁移。

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
   **如果服务当前是 STOPPED**（例如刚好踩到本版修复前的重启缺陷）：先到「服务」里启动
   `DrcomAutoLogin`，或在管理员 PowerShell 里执行 `sc start DrcomAutoLogin`。

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
