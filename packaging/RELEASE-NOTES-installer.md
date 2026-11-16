Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · 固定下载链接
<!-- RELEASE-TITLE: Windows 安装程序 v{{VERSION}}（跟随最新构建 · 固定下载链接） -->

> 这是**固定下载入口**：tag `installer` 永远指向最新一次构建 —— 发给别人这个链接就行，
> 不需要关心版本号。需要看「这一版改了什么」请去正式版：[v{{VERSION}}]({{RELEASE_URL}})。

## 📦 本次构建

| 项 | 值 |
| --- | --- |
| 应用版本 | {{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}） |
| 文件名 | {{ASSET}} |
| 大小 | {{SIZE}} bytes（约 {{SIZE_MB}} MB） |
| SHA256 | `{{SHA256}}` |
| 构建提交 | `{{SHA}}` |
| 构建编号 | #{{RUN}} |
| 构建时间 | {{DATE}} |
| 系统要求 | Windows 10 / 11 x64（已内嵌 Python 运行时，无需预装） |

## ✅ 校验

```powershell
certutil -hashfile {{ASSET}} SHA256
# 期望结果：{{SHA256}}
```

## 🔄 自动升级

- 本 Release 是 **prerelease**，只作固定下载用；自动升级客户端**不会**读它。
- 自动升级只认正式版：[v{{VERSION}}]({{RELEASE_URL}})（`/releases/latest`）。
- 升级 / 回滚规则见正式版说明；配置与密码在升级时不会被覆盖。

## 📜 说明

- 这一版改了什么：[CHANGELOG.md]({{CHANGELOG_URL}}) · 与上一版对比：{{COMPARE_URL}}
- 使用与排障：[README](https://github.com/{{REPO}}#快速开始)

<sub>本发布页由 CI 从 `packaging/RELEASE-NOTES-installer.md` 自动渲染。</sub>
