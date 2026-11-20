Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 门户 AC 自动识别增强 + 文档链接修复 -->

> **一句话**：把「自动分析校园门户 AC」做得更稳、更可见 —— 探测地址扩到 5 个、两种门户吐参数形态都认、
> 发现/未发现都写日志、并支持 config.json 手动兜底；同时修掉 README 里 5 个「点了没反应」的目录锚点
> 和一个被行内 `|` 撑破的表格。接口、配置结构（只增两个可选键）、服务名与 `AppId`、安装路径**都没动** ✓。

## 🛠 本版修复

### ① 门户 AC 自动识别增强（学校里有多个版本的 AC）
- **探测地址扩到 5 个**：`msftconnecttest` / `connectivitycheck.gstatic.com` / `captive.apple.com` /
  `detectportal.firefox.com` / `9.9.9.9` —— 有的门户只拦其中某个域名，多试几个才稳。
- **两种形态都解析**：302 的 `Location` query，以及 200 页面里把参数写进 JS 变量的形态
  （`var wlanacip='…'` / `"wlanacname":"…"`）；值仍过 IP / 名称 / MAC 形状白名单。
- **日志可诊断**：发现时写一行 `门户参数发现（来源=location/body）：wlanacip=… wlanacname=… …`；
  找不到时写 `门户参数未发现（5 个探测地址都没拿到…）` 并提示手动兜底 —— 能分清「没发现参数」和「参数不对」。
- **手动兜底**：`config.json` 新增两个**可选**键 `wlan_ac_ip` / `wlan_ac_name`（默认空 = 自动发现）。
  按门户跳转 URL 填上即可自救；Web UI 保存配置不会清掉它们。

### ② README 文档修复
- 5 个目录锚点点了没反应：GitHub 会去掉标题里的 emoji 但保留空格，`## 📸 截图` 的锚点是 `#-截图`
  而目录写的是 `#截图` → 已去掉 10 个标题行首的装饰 emoji，锚点恢复正常。
- 版本记录表格里行内代码 `% ! ^ & | < >` 的裸 `|` 会把表格撑多一列 → 已转义；
  `[Run]` 改为行内代码；顶部 3.0 横幅状态更新到 M4 收尾完成 / preview.4。
- 新增文档自检断言（锚点全解析 / 表格列数一致），以后同类问题冒烟会直接拦下。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含方案专属 `password.<方案名>.txt`）与 `logs\` 都保留。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`。
- 默认行为与上一版一致（自动识别门户参数）；只有新增的两个可选配置键需要时才填。

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
