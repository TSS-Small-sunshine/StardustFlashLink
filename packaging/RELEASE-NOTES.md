Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 重启服务修复版（补丁） -->

> **一句话**：这是 v2.1.3.9 的**补丁版** —— 「重启服务」的修复在真机上还有一层坑：任务计划程序对
> **含空格的 .cmd 路径**会「创建成功、/run 成功、脚本却根本不执行」，结果服务被留在
> 「NSSM wrapper RUNNING、里面的 Python 没了」的假运行状态。本版把执行器改放无空格临时目录、
> **本进程不再主动退出**（fail-safe）、stop/start 也更稳，并在真机做了端到端复验。
> 接口、配置结构、服务名与 `AppId`、安装路径、密码文件格式**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## 🛠 本版修复

### ① v2.1.3.9 漏掉的那层坑（真机 A/B 证据）
- v2.1.3.9 把重启执行器写在 `安装目录\logs\restart_service.cmd`（**路径含空格**）：
  `schtasks /create` 成功、`/run` 成功，**但脚本从未被执行**（等 6 秒没有任何输出）。
- 同一份脚本放 `C:\Windows\Temp`（无空格）→ **1 秒内跑完**。原因：任务计划程序执行 .cmd 时会用
  `cmd /c <路径>` 之类包装，**含空格的 /tr 路径被拆开** → 找不到文件，却**不返回错误** ✗。
- 后果：点重启后本进程 `os._exit(0)` 退出，`AppExit=Ignore` 下 NSSM wrapper 保持 RUNNING（假运行），
  里面的 Python 已经没了 → `/api/health` 连不上（而 `sc query` 还显示 RUNNING）。
  该状态下 `sc stop` 能停掉 wrapper、`sc start` 能恢复（本机已手工恢复 RUNNING 后才会升级到本版）。

### ② 三个修法
- **执行器改放不含空格的临时目录**：优先 `%SystemRoot%\Temp`，其次无空格的 `%TEMP%`；
  路径含空格 / `& | < > ^ % !` 的候选一律跳过。
- **本进程不再主动退出**：由执行器里的 `sc stop` 停它（SIGINT → 正常收尾并退出）。即使任务没执行，
  服务也**保持原样**（fail-safe），不会「点一下服务就没了」✗。
- **执行器更稳**：`sc stop` 后等真的 `STOPPED`（最多 ~30 秒）→ `sc start` 有界重试 6 次 →
  还不行就按 PID **只杀本服务的 nssm** 再起一次；全过程写 `logs\restart_service.log`。
- 服务每次启动时清理上一次遗留的一次性任务（运行中删不掉，启动兜底 ✓）。
- 版本回滚同样走这条重启路径：调度失败会把警告带进响应。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）与 `logs\` 都保留。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 如果你现在正处在「服务显示 RUNNING 但打不开面板」的状态（踩到 v2.1.3.9 的坑）：先以管理员身份执行
  `sc stop DrcomAutoLogin` 再 `sc start DrcomAutoLogin`，或重启电脑（服务是自动启动），然后正常升级。
- 本版只改「重启服务」这一条路径，**配置格式与 API 字段没有变化**，无需迁移。

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
