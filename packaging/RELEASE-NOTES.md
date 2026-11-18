Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 静默升级后托盘自愈版 -->

> **一句话**：这是 2.1 线的一次**修复版** —— 治掉「自动升级之后托盘不见了、要等下次登录才回来」
> （服务在确认刚升级成功后，跨会话把托盘拉回当前登录会话），顺手收掉托盘三个 P3 与 ISCC 版本漂移。
> 接口、配置结构、服务名与 `AppId`、安装路径、密码文件格式**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## 🛡 本版修复

### ① 静默升级后托盘跨会话自愈（用户可感知）
- 自动升级是静默安装：升级前安装器会杀掉托盘，而安装器「装完启动托盘」那条带 `skipifsilent`
  —— 升级结束后托盘一直不回来（真机实测约 **21.5 小时**无托盘：断线气泡 / 状态图标 / 唤醒重连全失效）。
- 现在服务在**确认刚完成一次自动升级**时，用当前登录会话的用户令牌把托盘重新拉进桌面
  （`CreateProcessAsUserW` + `winsta0\default`，无控制台窗口）；失败只记一行日志
  （下次登录仍由自启项启动），不影响升级本身。
- 用户从托盘菜单主动退出**不会**被服务复活 —— 只在「刚升级成功」这一次触发。

### ② 托盘健壮性三连
- `tray.py --self-test` 在托盘已运行时返回 **7（SKIP）** 并说明原因，不再「假通过」；
- 单实例互斥体创建失败时宁可不启动也不双开；退出时统一关句柄；
- `tray.log` 轮转失败改为截断重写（不再无界增长）；脚本指纹加头部 SHA-256，同长度、同秒内完成的替换也能识别。

### ③ 构建：ISCC 版本钉死
- CI 主路径 `choco install innosetup --version=6.7.1`，并核对注册表 `DisplayVersion` —— 编译器版本可控、构建可复现；
  官方安装器兜底路径行为不变，但如实打 `::warning::` 说明「兜底路径版本未钉死」。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）
  与 `logs\` 都保留；安装器只在文件不存在时创建默认值。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 本版只改升级后的托盘拉起、托盘自身健壮性与构建链路，**配置格式与 API 字段没有变化**，无需迁移；
  升级完成后托盘会在当前登录会话里自动回来。

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
