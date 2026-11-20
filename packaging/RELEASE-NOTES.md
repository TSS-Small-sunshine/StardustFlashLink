Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 新 AC 登录修复 + 重启后托盘回归 -->

> **一句话**：本版修两条真机问题 —— ① 换到新 AC（`Ruijie_Ac_cc73fa`）后自动登录一直
> 「AC认证失败」（登录请求漏带 `wlanacip` / `wlanacname`）；② 点「重启服务」后**托盘又不见了**
> （服务重启会连带结束托盘，旧逻辑只在「刚升级」时才拉）。接口、配置结构、服务名与 `AppId`、
> 安装路径、密码文件格式**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## 🛠 本版修复

### ① 自动登录：新 AC 必须带 `wlanacip` / `wlanacname`
- **真机现象**：2026-11-20 新网络（SSID `FJNY`，AC `Ruijie_Ac_cc73fa`）上自动登录连续
  `登录失败: AC认证失败`；同一账号密码在门户网页**手动登录却可以**。
- **真机对照**：登录请求里 `wlan_ac_ip`/`wlan_ac_name` 一直是空串 —— 老 AC（`SR8806-X-S`）留空能过，
  新 Ruijie AC 留空直接判 AC认证失败；把门户跳转 URL 里的 `wlanacip=172.16.200.252` /
  `wlanacname=Ruijie_Ac_cc73fa` 补上重发 → 门户**立刻正常受理**。
- **修复**：未认证时先探一次门户跳转（读 302 的 `Location`），取出
  `wlanacip` / `wlanacname` / `wlanuserip` / `mac` / `ssid`（都过形状白名单）再登录；
  探不到就退回老行为（留空），不会因为探不到门户而让登录失败。
- **顺带**：门户回「已经在线」（`result=0`）现在按成功处理，不再反复重试 / 越退越久 / 界面一直显示「未登录」。

### ② 点「重启服务」后托盘回归
- **真机现象**：重启服务后 `tray.log` 停在重启前，托盘进程消失 —— 托盘由服务跨会话拉起，
  服务重启时被 NSSM 的进程树一起结束；旧逻辑只在「刚升级」时才拉托盘，重启这条路漏了。
- **修复**：点重启时写标记 `logs\.tray_relaunch_requested`，新进程启动钩子消费标记并把托盘
  拉回**当前登录会话**（与升级后拉起同一套会话令牌路径）。标记只在用户主动点重启时写，
  **用户从托盘菜单主动退出仍然不会被复活** ✓。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）与 `logs\` 都保留。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 升级完成后服务与托盘会自动回来（无需手动操作）；若托盘仍缺，可从开始菜单/桌面快捷方式打开一次，
  之后它仍会随登录自启。
- 本版只改登录参数发现与「重启后拉托盘」两条路径，**配置格式与 API 字段没有变化**，无需迁移。

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
