Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 供应链可复现性收尾版 -->

> **一句话**：这是 2.1 线的一次**小修复版** —— 把安装器中文语言文件固定到指定 commit 并锁进 `tools.lock.json`
> （CI 与本地构建都校验哈希），同时补全发版手册：CI-only 修复怎么发版、下载失败怎么处置、工具锁怎么维护。
> 接口、配置结构、服务名 `AppId`、安装路径、密码文件格式**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## 🛡 本版修复

### ① 供应链可复现性
- 安装器中文语言文件 `ChineseSimplified.isl` 不再跟随 issrc `main` 分支浮动，而是**固定到 commit**
  （URL + SHA256 + 字节数写进 `tools.lock.json`）；CI 与本地构建脚本都从 lock 读条目下载并校验，
  哈希不符构建直接失败。
- 上游若删除该 commit：日志 warning 并回退英文 `Default.isl`（构建不中断），按发版手册换新 commit 即可。
- `not_locked` 只保留 Inno 官方安装器与 choco 两条真正浮动项，并写明风险边界（ISCC 只参与编译、不进安装包）。

### ② 发版手册（`docs/RELEASING.md`）
- **CI-only 修复怎么发版**：只改工作流/CI 的合并不会自动构建，需要手动 `workflow_dispatch`；
- **上游下载失败**：nssm.cc 偶发 503、有界重试、Re-run 与手动处置方式；
- **工具锁维护**：上游更新时「下载 → 算 SHA256 → 更新 lock」的完整步骤（禁止绕过校验）。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）
  与 `logs\` 都保留；安装器只在文件不存在时创建默认值。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 本版只改构建/发版链路与文档，**配置格式与 API 字段没有变化**，无需迁移。

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
