Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 升级链防御细化版 -->

> **一句话**：这是 2.1 线的一次**小修复版** —— 升级包下载按声明的字节数核对、TEMP 里超过一天的旧安装包会清理、
> 安装路径含 cmd 特殊字符时不再自动升级（提示手动安装），并把本地构建脚本的版本号改成从 `version.py` 读取。
> 接口、配置结构、服务名 `AppId`、安装路径、密码文件格式**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## 🛡 本版修复

### ① 升级链防御
- **下载尺寸核对**：镜像声明了期望字节数时，下载完成后核对实际大小；不符就丢弃该镜像产物、
  记中文告警并换下一个镜像。没有期望值时行为不变。
- **TEMP 旧安装包清理**：超过 24 小时的旧版本 `StardustFlashLink-Setup-v*.exe` 会被清理
  （只清 TEMP 根、不递归；坏文件名 / 更高版本 / 正在使用的不动）。
- **危险安装路径拒绝自动升级**：安装目录 / 解释器 / 安装包路径里若含 `% ! ^ & | < >` 或换行，
  不再生成升级执行器与回滚脚本，本次升级按失败处理并提示「请手动运行安装包」；
  默认的 `%ProgramFiles%\DrcomAutoLogin` 不受影响，手动升级不受影响。

### ② 构建与文档
- `packaging/build.ps1` 的版本号改为**构建时从 `version.py` 读取**（读取失败会中文报错并停止构建），
  版本发布少一处手改同步点；`AGENTS.md` / `docs/VERSIONING.md` 的同步清单已同步口径。
- 发版 CI 与本地构建下载 NSSM / 内嵌 Python 时增加**有界重试**（3–4 次、递增等待），
  上游偶发 503 不再让整个构建直接失败。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）
  与 `logs\` 都保留；安装器只在文件不存在时创建默认值。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 本版只改升级判定与构建脚本，**配置格式与 API 字段没有变化**，无需迁移。

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
