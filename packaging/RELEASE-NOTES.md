Windows 安装程序 **v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）** · {{DATE}} · Inno Setup 自动构建（内嵌 Python，目标机无需预装）
<!-- RELEASE-TITLE: v{{VERSION}} — 多网络多账号 -->

> **一句话**：配置方案现在可以带**自己的账号与密码** —— 宿舍 / 教学楼各用各的，切方案时连密码一起换；
> 顺带把「状态页凭据卡存不了账号」这个毛病修了。
> 接口、配置结构、服务名 `AppId`、安装路径**都没动** ✓ —— 老配置与升级路径不受影响 ✓。

## ✨ 本版亮点

- **方案可以带自己的密码**：`password.<方案名>.txt`（一行密码）。没设专属密码的方案照旧用公共
  `password.txt`，**老用户零迁移**。
- **配置页新增「选中方案的账号与密码」**：选方案 → 填账号 / 运营商 / 该方案密码 → 「保存到选中方案」；
  勾「删除该方案的专属密码」即可改回共用公共密码。
- **切方案连密码一起换**：以前密码只在服务启动时读一次，切完方案还在用旧密码 —— 现在切方案会重载密码
  （重载失败只记日志、不回滚配置）。
- **状态页凭据卡修好了**：按钮改为「保存账号与密码」，一次存下账号 + 运营商 + 密码；
  账号 / 运营商走**部分更新**，不会再把网关、间隔、守卫设置重置回默认值。
- **密码仍然只在文件里**：不进 `config.json`、不进诊断包、界面不回显。

## 🔧 修复

- 兜底异常处理里的日志调用改用模块自带 logger —— 以前 logger 没注入时会先 `NameError`，
  把真正的异常盖住（客户端只看到「连接被重置」）。
- 若保存的正是**当前方案**，保存凭据后自动重新应用一次，状态页立刻显示新账号。
- 界面标注更明确：方案下拉带「专属密码」标记，编辑区写明当前方案使用的密码文件。

## ⚠️ 升级前须知

- **升级不覆盖用户数据**：`config.json`、`password.txt`（含各方案的 `password.<方案名>.txt`）
  与 `logs\` 都保留；安装器只在文件不存在时创建默认值。
- **服务名与安装路径不变**：仍是 `DrcomAutoLogin` 与 `%ProgramFiles%\DrcomAutoLogin`，
  存量安装 / 卸载 / 升级链路继续可用。
- 若本机 `nssm` 的 `AppExit` 曾被写成空值，服务下次启动会尝试自愈；也可用管理员执行一次
  `nssm set DrcomAutoLogin AppExit Default Ignore`。

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
> 从 Releases 页面下载时，请确认文件名与上面一致。

## 📥 安装 / 升级

1. 下载 `{{ASSET}}`，双击运行（需要管理员权限：要注册系统服务）。
2. 首次使用：浏览器打开 `http://127.0.0.1:8848`，在「配置」页填写账号、后缀与密码。
3. 已有旧版：直接运行新安装包即可覆盖升级，配置与密码保留；
   也可以等客户端**自动升级**（本 Release 是正式版，走 `/releases/latest` 通道）。

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
