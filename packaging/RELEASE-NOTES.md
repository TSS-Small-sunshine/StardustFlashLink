Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 供应链与方案凭据收尾 -->

> **一句话**：这是 2.1 线的一次**小修复版** —— 给构建/安装链路真正下载的上游二进制加上 SHA-256 锁
> （NSSM / 内嵌 Python），并修掉「删除配置方案后，同名重建会静默继承旧密码」的问题。
> 接口、配置结构、服务名 `AppId`、安装路径、密码文件格式**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## 🛡 本版修复

### ① 上游二进制固定 SHA-256（供应链）
- 新增 `tools.lock.json`：把 NSSM 2.24 与内嵌 Python（3.12.10 / 3.12.9 / 3.11.9）的
  URL 与 SHA-256 锁定在一处；CI 下载后立即校验，**哈希不符直接让构建红**。
- 本地构建脚本（`packaging/build.ps1`）与源码安装（`install.bat`）同样校验：
  源码安装时 NSSM 校验不符会**中止安装**并说明手动处置办法；lock 缺失只警告、不卡安装。
- 浮动内容（Inno Setup 官方安装器 / 中文语言文件 / choco 包）在 `not_locked` 里写明未锁定原因，
  CI 日志会明确打出未锁定告警，不假装已校验。

### ② 删除配置方案时一并清理专属密码
- 删方案时会一并删除 `password.<方案名>.txt`（接口返回只增字段 `password_removed`）。
  以前删完再建同名方案会静默继承旧密码；现在不会再有这种看不见的凭据残留。
- 删的若是当前方案，会自动重读密码文件回落到公共 `password.txt`。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）
  与 `logs\` 都保留；安装器只在文件不存在时创建默认值。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 本版只改构建/安装链路与方案删除行为，**配置格式与 API 字段没有变化**（仅新增 `password_removed`），无需迁移。

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
