# 发布与发布文案（Release Notes）

本文说明**发布动作本身**：两条 Release 通道怎么发、发布说明怎么生成、发版前必须改什么。
版本号规则与「改版本要同步哪些字面量」见 [VERSIONING.md](VERSIONING.md)；
PR / 合并策略见 [PR-WORKFLOW.md](PR-WORKFLOW.md)。

---

## 1. 两条 Release 通道（不变量 I10）

| 通道 | tag | 性质 | 谁在读 |
| --- | --- | --- | --- |
| 固定下载入口 | `installer` | **prerelease**，每次 main 构建都覆盖重建 | 人（发给同学 / 下载页），链接永远指向最新构建 |
| 正式版 | `v{VERSION}` | **非 prerelease**，自动升级客户端只认它 | `/releases/latest`（客户端取版本号 + 安装包 + digest） |

> 少了非 prerelease 的 `v{VERSION}` release，自动升级就取不到版本号与 digest —— 这是
> 不变量 I10，不要「先发 installer、过几天再补正式版」。

> **版本号必须递增才发正式版**（审计 P1-1）：同名 `v{VERSION}` release 已存在时，
> `build-installer.yml` 只更新 `installer` 固定链接并**跳过**正式版发布（打一条 GitHub
> warning）—— 因为覆盖同名 tag 会让 tag 漂移 / 同版本换安装包，而自动升级客户端拿
> `remote == local` 就判「已是最新」，补丁永远到不了已装用户 ✗。
> 确实要重发同一版本（例如修复上一次失败的构建）时，用 `workflow_dispatch` 且
> `allow_republish=true`。常规发版流程是：**先 bump `version.py`（+ CHANGELOG / 模板），
> 合并后再让 CI 发版**。

两个 release 的正文都**由 CI 从仓库里的模板渲染**，不在 GitHub 页面手改：

| 通道 | 模板 |
| --- | --- |
| `installer` | `packaging/RELEASE-NOTES-installer.md` |
| `v{VERSION}` | `packaging/RELEASE-NOTES.md` |

渲染器：`.github/scripts/render_release_notes.py`（纯标准库，零第三方依赖）。

---

## 2. 为什么要有渲染器

发布页上「必须准确」的内容一律由 CI 注入，模板里只写占位符：

- 版本号 / 代号 / 安装包文件名 / 字节数 / SHA-256 / 构建提交 / 构建编号；
- 「与上一版对比」链接（自动取最近一个非 installer 的版本 tag）；
- 日期（按 **Asia/Shanghai**，CI runner 是 UTC —— 凌晨发版不会差一天）。

而且它是**闸门**，不是排版工具：

| 检查 | 触发时 |
| --- | --- |
| 渲染后残留 `{{...}}` | 直接失败（模板笔误 / 漏填，宁可 CI 红，也不把占位符发给用户） |
| 正文疑似泄露（URL 带 `password=`、私钥、常见 token 形态） | 直接失败 |
| 正文超过 100000 字符（GitHub 上限 125000） | 直接失败 |
| 标题超过 120 字符 / 标题里残留占位符 | 直接失败 |
| 两套模板能否渲染、常量与上下文是否一一对应、日期时区 | `--self-test` 断言 |

> 历史教训：v2.1.0.0 换版本线（Sirius → Vega）后，发布页首行还写着旧星名，因为模板里是死字面量；
> 另一版把 `$env:APP_VERSION` 原样发到了发布页（here-string 里转义写错）。这两类问题现在由渲染器 + 占位符检查兜住。

---

## 3. 发版时怎么改模板

模板就是「**本版**发布页」，发版前**必须**改头部这一段（其余占位符不用动）：

1. 第 1 行：标题行（保持 `v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）` 不动，只改后面的日期说明如果有）；
2. 第 2 行：`<!-- RELEASE-TITLE: v{{VERSION}} — 一句话卖点 -->` —— 这是 Release 列表里显示的标题；
3. 「一句话」引用块 + 「本版亮点」+「修复」：按本版实际内容重写；
4. 「升级前须知」：不变量没变就保留（老用户零迁移是产品承诺）；
5. **不要在模板里写死**版本号、代号、文件名、大小、SHA-256、日期 —— 那些全是占位符。

模板里允许出现的占位符（出现别的 `{{XXX}}` 会渲染失败）：

```
{{VERSION}} {{CODENAME}} {{CODENAME_CN}} {{ASSET}} {{SIZE}} {{SIZE_MB}}
{{SHA256}} {{SHA}} {{RUN}} {{DATE}} {{PREV_VERSION}} {{COMPARE_URL}}
{{CHANGELOG_URL}} {{RELEASE_URL}} {{REPO}}
```

历史版本的发布页留在 GitHub Releases 里，**不往模板里堆**；逐条变更记录写 `CHANGELOG.md`。

---

## 4. 本地验证（发版前必跑）

```powershell
# 渲染器自测（20+ 项断言，会连两套模板一起渲染）
python .github/scripts/render_release_notes.py --self-test

# 用假数据预演正式版正文（不写盘、不碰网络）
# PowerShell 的续行符是反引号（不是 cmd 的 ^）
python .github/scripts/render_release_notes.py `
  --repo TSS-Small-sunshine/StardustFlashLink `
  --template packaging/RELEASE-NOTES.md `
  --channel versioned `
  --version 2.1.4.0 --codename Vega --codename-cn 织女星 `
  --asset StardustFlashLink-Setup-v2.1.4.0.exe --size 12345678 `
  --sha256 ('ab' * 32) --commit ('c' * 40) --run 123 `
  --prev-tag v2.1.3.0 --dry-run

# 常规冒烟（与发布无关，但发版前同样必须 0 失败）
python _smoke_static.py
python _smoke_http.py
```

> `_smoke_static.py` 里也有一条断言会调用渲染器自测 —— 渲染器或模板写坏了，冒烟直接红，
> 不用等 CI 跑到发布那一步才发现。

---

## 5. 手工验证 workflow（不发布）

`build-installer.yml` 支持 `workflow_dispatch` 的 `dry_run`：

```
gh workflow run "Build Windows Installer" -R TSS-Small-sunshine/StardustFlashLink `
  --ref main -f dry_run=true
```

它会照常构建安装包、跑渲染器，然后把**渲染出来的正文打印到日志**并跳过
`gh release create` —— 用来验证模板 / workflow 改动，不会覆盖线上 release。

> 只改 `.github/**`、`*.md`、`docs/**` 的提交不会触发本 workflow（`paths-ignore`）；
> 也就是说「落发布文案改造」本身不会顺手发一次版。

---

## 6. 隐私红线（P0）

1. 模板 / 发布页里**绝不**出现真机账号、密码、网关、学号；示例一律打码或合成值。
2. 发布说明**不解释**密码存放格式的细节以外的凭据信息；密码永远只在 `password.txt`。
3. 渲染器会在发布前扫「URL 带 password=、私钥、常见 token 形态」—— 命中即失败。
   这条规则是**兜底**，不是许可：不要故意在模板里放接近真凭据的示例。
