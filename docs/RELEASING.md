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

## 6. CI-only 修复怎么发版（`workflow_dispatch` / 手动发版）

`build-installer.yml` 的触发条件是 **main 分支的 push**，但 `paths-ignore` 里有
`.github/**`、`docs/**`、`**.md` —— 也就是说：**只改工作流 / CI 脚本 / 文档的合并不会自动构建**。
这是有意为之（省 runner、避免无关提交挪动发版 tag），代价是「CI 修复合并了」≠「安装包重建了」。

只改 CI 的修复，合并 main 后必须手动触发一次构建：

1. 打开仓库 **Actions → Build Windows Installer → Run workflow**，
   Branch 选 **main**（不要用功能分支，原因见下），按需填 `dry_run` / `allow_republish`；
2. 或者用 gh CLI：

   ```powershell
   gh workflow run "Build Windows Installer" -R TSS-Small-sunshine/StardustFlashLink --ref main
   # 只想验证 workflow 改动、不发布 release：加 -f dry_run=true
   gh workflow run "Build Windows Installer" -R TSS-Small-sunshine/StardustFlashLink --ref main -f dry_run=true
   ```

注意：

- **只能从 main 手动发版**。从功能分支 dispatch 时，构建会照常跑完并上传 artifact，但两个发布 step
  都会打 warning 后跳过（`GIT_REF != refs/heads/main`，审计 P2-6）——**不会**创建/覆盖任何 release；
- 手动触发走同一把 concurrency 锁（`build-installer-${{ github.ref }}`）。如果 main 上恰好有构建在跑，
  等它结束再触发，不要并发；
- **CI-only 修复通常不需要 bump 版本号**：`v{版本}` 已存在时构建只更新 `installer` 固定链接，
  跳过正式版发布（打 warning，见 §1 的 P1-1 说明）。此时普通用户/自动升级客户端**不会**收到新包；
  如果这次 CI 修复必须让已装用户升级到，仍然要 bump `version.py` 再合并（走常规发版流程）；
- 触发后到 run 页面确认所有下载 / 校验 step 都是绿的（尤其是 `Locate ISCC and ensure Chinese language file`
  与 `Prepare NSSM`），再确认 `installer` release 的更新时间变了。

---

## 7. 上游下载失败怎么办（nssm.cc 503 等）

发版构建与本地 `packaging/build.ps1` 都会从上游下载固定版本文件（nssm / 内嵌 Python / Inno 中文语言文件）。
上游偶尔抽风（实测 **nssm.cc 会偶发 503**，v2.1.3.5 首次构建就踩过），因此下载已经全部带**有界重试**
（`Invoke-DownloadWithRetry`：多次尝试 + 间隔等待，CI 里 nssm 是 4 次 / 8 秒）。一次临时故障只会多等一会儿，
不会直接打红构建。

仍然失败时，按日志里的关键字定位（`下载失败（已重试 N 次）` / `下载失败（第 x/y 次）`），然后：

| 场景 | 处置 |
| --- | --- |
| GitHub Actions 上偶发失败 | run 页面 **Re-run failed jobs**；不要改 workflow / 不要跳过校验。重跑还是失败 → 看是不是上游真的挂了（浏览器打开对应 URL 确认） |
| 本地构建临时下载失败 | 重新跑 `packaging\build.ps1`；网络/代理问题先修网络。已存在的 `tools\nssm.exe` 会被跳过下载（只做大小检查），**手动放文件时请自己用 `Get-FileHash` 对照 `tools.lock.json`** |
| 中文语言文件（`raw.githubusercontent.com`）下载失败 | CI 会 warning 后回退 `Default.isl`（安装向导英文、功能正常）；本地同理。要恢复中文：修网络后重跑，或按 `tools.lock.json` 的 `chinese-simplified-isl.url` 手动下载后覆盖 `<Inno Setup>\Languages\ChineseSimplified.isl` |
| 上游确实发新版 / 旧文件被撤下（404） | 不要临时改 URL 绕过：按 §8「工具锁维护」实际下载新文件 → 算 SHA256 → 更新 `tools.lock.json`。旧版本文件被撤时优先换新版本并锁新哈希 |
| 上游只是重传了同 URL 的文件（哈希变了） | 视为供应链事件：先对比内容差异，确认是官方行为，再更新 lock 的 `sha256` / `size_bytes`，并在 CHANGELOG 记一笔 |

> 任何情况下都不要为了让构建变绿而：删掉 lock 条目、把 `Assert-LockedDownload` 的校验注释掉、
> 或改用未经校验的镜像地址。构建红是信号，不是障碍。

---

## 8. 工具锁维护（`tools.lock.json`）

`tools.lock.json` 是**唯一事实来源**：CI（`build-installer.yml`）与本地构建（`packaging/build.ps1`）
都从它读 URL / SHA256 / 版本，YAML / PS1 / bat 里不再出现哈希字面量（`_smoke_static.py` 的
R4 / R6 断言会检查这一点）。`not_locked` 只允许记录**真正无法固定**的上游（当前是 Inno 官方安装器与
choco 的 innosetup 包），CI 会对其打 `::warning::`，不会假装校验过。

### 8.1 升级 nssm / 内嵌 Python（固定版本条目）

1. 先实际下载新文件（不要从网页/别人报告里抄哈希）：

   ```powershell
   $url = 'https://nssm.cc/release/nssm-2.24.zip'      # 换成新版本 URL
   $tmp = Join-Path $env:TEMP 'locked-download.bin'
   Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
   (Get-Item $tmp).Length
   (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
   Remove-Item $tmp
   ```

2. 更新 `tools.lock.json` 对应条目的 `version` / `url` / `sha256` / `size_bytes`，
   并把顶层 `updated_at` 改成当天（`YYYY-MM-DD`）；
3. 跑 `python _smoke_static.py`（R4 / R6 会校验 lock 结构），再把 CI 跑一次 `workflow_dispatch`（`dry_run=true` 即可）
   走完真实下载 + 校验链路；
4. `CHANGELOG.md` 记一笔「升级了哪个锁定版本、哈希前 12 位」。

### 8.2 更新固定到 commit 的中文语言文件（`chinese-simplified-isl`）

Inno 官方仓库 [jrsoftware/issrc](https://github.com/jrsoftware/issrc) 的语言文件还在更新，
所以它**固定到某个 commit**（`version` = commit 前 12 位），而不是浮动引用 main 分支。更新方法：

```powershell
# 1) 取 issrc 当前 main 的 commit（40 位；version 字段填前 12 位）
git ls-remote https://github.com/jrsoftware/issrc refs/heads/main

# 2) 用该 commit 的 raw 地址下载（URL 里必须是完整 commit，不能是 main）
$commit = '<40 位 commit>'
$url = "https://raw.githubusercontent.com/jrsoftware/issrc/$commit/Files/Languages/ChineseSimplified.isl"
$tmp = Join-Path $env:TEMP 'ChineseSimplified.isl'
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
(Get-Item $tmp).Length
(Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
Remove-Item $tmp
```

然后把 `tools.lock.json` 的 `chinese-simplified-isl` 条目改成：
`version` = commit 前 12 位、`url` = 上面的 raw 地址、`sha256` / `size_bytes` = 实测值，
并更新 `updated_at`。**不要**把它挪回 `not_locked`，也**不要**改回 `.../main/...`。

> 为什么固定 commit：main 分支内容随时会变，写死哈希 = 上游一提交就让构建红；
> 固定 commit 后内容可复现，且 commit（几乎）不会被改写。极端情况上游 force-push 删掉该 commit 时，
> 下载会 404 —— 此时 CI / 本地都会降级为 warning 并回退 `Default.isl`（英文界面，构建不中断），
> 按上面的步骤换一个新 commit 即可恢复中文。

### 8.3 升级 ISCC（Inno Setup 编译器，CI 钉死 `6.7.1`）

CI 主路径用 `choco install innosetup --version=6.7.1` 钉死版本；装完后会读注册表
`HKLM\SOFTWARE[\WOW6432Node]\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1`
的 `DisplayVersion` 与 6.7.1 比对，不一致直接让构建失败（主路径版本必须可控）。
只有 choco 失败时才退回官方静默安装器 —— 那条路径的 ISCC 版本**未钉死**，CI 会打
`::warning::` 明确说明，而不是假装可复现。升级步骤：

1. 改 `.github/workflows/build-installer.yml` 里 `choco install ... --version=` 与
   「Record and verify ISCC version」步骤的 `$pinned`（两处必须一致，否则核对必红）；
2. 本地实测一次构建：`packaging\build.ps1` 能正常编译出安装包（ISCC 只参与编译）；
3. 合并后手动 `workflow_dispatch` 一次（`dry_run=true` 也行），确认「记录并核对 ISCC 版本」步骤为绿；
4. `CHANGELOG.md` 记一笔「ISCC 升级到哪个版本」。

> 为什么不锁内容哈希：choco / nupkg 不提供稳定内容哈希（包体由上游重打包），写死哈希会把
> 「上游正常重传」变成「构建必红」；版本钉死 + 注册表核对已经能保证「用的编译器是哪一版」。

### 8.4 不允许的「收口」方式

- 把哈希写回 YAML / PS1 / bat（R4 会红）；
- 把 `Assert-LockedDownload` 改成永远放行，或把 `-Required` / 哈希校验删掉；
- 只改 `not_locked` 的说明而不真正固定版本；
- 从网页、Issue、聊天记录里抄一个哈希进 lock（必须是本机 `Get-FileHash` 实测值）。

---

## 9. 隐私红线（P0）

1. 模板 / 发布页里**绝不**出现真机账号、密码、网关、学号；示例一律打码或合成值。
2. 发布说明**不解释**密码存放格式的细节以外的凭据信息；密码永远只在 `password.txt`。
3. 渲染器会在发布前扫「URL 带 password=、私钥、常见 token 形态」—— 命中即失败。
   这条规则是**兜底**，不是许可：不要故意在模板里放接近真凭据的示例。
