# PR 与合并策略（2026-11 起）

> 适用分支：`main`（2.x Vega 稳定线）与 `dev/3.0-altair`（3.0 Altair 开发线）。
> 目标：**main 上的每一次改动都可追溯、可验证、可回滚** —— 机器人先审、CI 再验、只有 PR 能合。

## 0. 一句话规则

1. 任何改动 → 开分支 → 开 PR（标题用 Conventional Commits）→ **机器人审核 + CI 冒烟** → squash 合并 → 自动删分支。
2. **`main` 与 `dev/3.0-altair` 都不允许直接 push**（分支保护均已开，且对管理员同样生效）。
3. 两条线互不合：`dev/**` 不合 main、main 不往 `dev/**@@ 提 PR；2.x 代码只在 main 改，3.0 代码只在 dev 改。
4. 发版从 main 走：合并 → CI `build-installer.yml` 自动出 `installer` + `v{版本}` 两条 release。

## 1. 分支模型

| 分支 | 角色 | 谁能改 | 保护 |
| --- | --- | --- | --- |
| `main` | 2.x Vega 稳定线（正式发版） | 只能通过 PR | ✅ 已开（见 §5） |
| `dev/3.0-altair` | 3.0 Altair 开发线（只发预览） | 只能通过 PR | ✅ 已开（见 §5） |
| `feat/...` `fix/...` `docs/...` `ci/...` `hotfix/...` `release/...` | 临时分支 | 作者 | 合并后自动删除 |

分支命名：`<type>/<简短描述>`，type ∈ `feat` / `fix` / `hotfix` / `docs` / `chore` / `refactor` / `test` / `ci` / `build` / `perf` / `style` / `revert`，全小写，用 `-` / `.` / `_` 分隔。

## 2. PR 要求

- **标题**：`type(scope): 中文说明`（合并后会成为 squash 提交标题；禁止 WIP / 「更新一下」这类空标题）。
- **一条 PR 只做一件事、只属于一条线**；大改动拆开（>5000 行阻断，>1500 行提示）。
- **正文**：用 `.github/PULL_REQUEST_TEMPLATE.md`；`## 这是什么` 与 `## 怎么验证` 必填，**贴真实命令输出**。
- 本地先自己验证完：
  - 2.x：`python _smoke_static.py` + `python _smoke_http.py`（都必须 0 失败）
  - 3.0：`cargo test --workspace`；改过界面再 `cargo build -p drcom-ui`
- 隐私红线（`AGENTS.md` §5）：不带真机凭据 / 日志 / 真机截图。

## 3. 机器人审核（PR Bot）

实现：`.github/workflows/pr-bot.yml` + `.github/scripts/pr_review.py`（**纯标准库、零第三方依赖、本地可跑**）。

它做什么：读 PR 标题 / 正文 / diff，跑一组**确定性规则**，把结论贴成**一条评论**（每次更新原地改，不刷屏）；有阻断项时 job 失败 → 分支保护挡住合并。

它不做什么：不改代码、不 approve、不自动合并、不读凭据、不碰仓库外的东西。

### 3.1 阻断项（必须改）

| 规则 | 检查内容 |
| --- | --- |
| 标题 | 不符合 Conventional Commits / 带 WIP / 超过 100 字符 |
| 正文 | 太短（<80 字符）、缺 `## 这是什么` 或 `## 怎么验证` |
| 双线隔离 | `dev/**` 往 main 提；main 往 `dev/**@@ 合；main 的 PR 碰 `desktop/`；dev 的 PR 碰根目录 2.x 代码 / `packaging/` |
| 隐私 | 提交 `password.txt` / `config.json` / `logs/` / 私钥 / `.p12` / token 形态；URL 带 `password=` |
| 版本同步 | 改 `version.py` 但没同步 `setup.iss` / 批处理 / `build.ps1` / CHANGELOG |
| 发布模板 | 改 `packaging/RELEASE-NOTES.md` 但首行缺 `{{CODENAME}}` 占位，或出现渲染器不认识的占位符 |
| 大小 | 0 改动；>80 文件；>5000 行 |

### 3.2 提示项（可以合，但要确认）

PR 偏大（>30 文件 / >1500 行）、提交数 >8 条、改运行时代码但没动 CHANGELOG、标题 72–100 字符、正文缺可选小节。

### 3.3 豁免与误报

- 加标签 **`bot:override`** → 阻断项降级为提示（评论会标黄并写明「维护者必须人工复核」）；合并前请人工确认。
- 机器人规则本身有 bug：改 `.github/scripts/pr_review.py` 并补自测，走正常 PR。
- 本地复现：

```powershell
python .github/scripts/pr_review.py --self-test
# 或拿一个 fake event 本地预览结论（不联网、不评论）
python .github/scripts/pr_review.py --dry-run --event fake-pr.json --base-ref main
```

### 3.4 规则之外仍要人看

机器人只查「可机械判定」的东西。以下靠人工评审：设计取舍、真机行为、安装器 / 服务 / 升级链路、发布文案可读性、用户体验。

## 4. 合并策略

| 项 | 规定 |
| --- | --- |
| 合并方式 | **只允许 squash merge**：一条 PR = 一个提交，main 历史线性、好 revert |
| 合并后 | 自动删除头分支；允许 auto-merge（检查通过自动合） |
| 禁止 | force push main、直接 push main、merge commit、rebase merge、删除 main |
| 回滚 | `git revert <squash 提交>` → 走 PR（紧急时管理员可临时关保护，见 §5.2） |

## 5. 必需状态检查与分支保护

两条线的合并要求（必需检查）：

| 分支 | 必需检查 | 来源 | 大约耗时 |
| --- | --- | --- | --- |
| `main` | **PR Gate** | `.github/workflows/pr-checks.yml`（Python 语法 + 两个冒烟） | ~1 分钟 |
| `main` | **PR Bot** | `.github/workflows/pr-bot.yml`（机器人审核） | ~15 秒 |
| `dev/3.0-altair` | **PR Gate** + **PR Bot** | 同上 | ~1 分钟 |
| `dev/3.0-altair` | **测试（ubuntu-24.04）** / **测试（macos-14）** / **测试（windows-latest）** | `.github/workflows/preview-3.0.yml` 的三平台 cargo test | ~2–5 分钟 |

> **不要求人工 approve**：个人仓库只有一位维护者，自己不能批自己的 PR —— 机器人审核 + CI 就是闸门。
> 涉及发布 / 安装器的 PR 请自觉在正文写清真机验证步骤。

### 5.1 配置（作为代码放在 `.github/branch-protection.json`）

```powershell
# 仓库级：只留 squash，合并后删分支，允许 update branch
gh api -X PATCH repos/TSS-Small-sunshine/StardustFlashLink `
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false `
  -F delete_branch_on_merge=true -F allow_update_branch=true

# main 分支保护
gh api -X PUT repos/TSS-Small-sunshine/StardustFlashLink/branches/main/protection `
  --input .github/branch-protection.json
```

保护内容（两条线一致）：必须有 PR；必需检查见上表；分支必须与目标分支同步（strict）；线性历史；禁止 force push / 删除；对话必须解决；**规则对管理员同样生效**（`enforce_admins=true`）。

### 5.2 配置 dev 分支保护

配置文件：`.github/branch-protection-dev.json`（必需检查为 `PR Gate` + `PR Bot` + 三个 `测试（…）`）。

```powershell
gh api -X PUT repos/TSS-Small-sunshine/StardustFlashLink/branches/dev%2F3.0-altair/protection `
  --input .github/branch-protection-dev.json
```

### 5.3 应急通道（发版 / 安全修复被闸门卡住时，两条线通用）

只在确实需要时用，**用完立刻恢复**：

```powershell
# 临时关闭 main 保护（GitHub 审计日志会记录）
gh api -X DELETE repos/TSS-Small-sunshine/StardustFlashLink/branches/main/protection
# 处理完立刻恢复
gh api -X PUT repos/TSS-Small-sunshine/StardustFlashLink/branches/main/protection `
  --input .github/branch-protection.json
```

或者：先给 PR 加 `bot:override` 标签放行机器人（人再确认内容），再正常 merge。

## 6. 提交信息与合并后的提交

- 分支上可以有多次提交；**合并时 squash**，最终提交信息 = PR 标题（正文里的说明会拼进 commit body）。
- 因此 PR 标题要能独立说明这次改动；发版 PR 用 `chore(release): v{版本} {代号} —— 一句话`。
- 合并后不需要再手工改提交信息。

## 7. 发布链路与 PR 的关系

- 合并到 main 的 push 会触发 `build-installer.yml`：编译安装包并发布 `installer` + `v{版本}` 两条 release。
- `.github/**`、`docs/**`、`*.md`、`LICENSE` 的改动**不会**触发安装包构建（paths-ignore）—— 改 CI / 文档不会误发版、不会挪 tag。
- 3.0 的 push 到 `dev/**` 只触发 `preview-3.0.yml`（三平台测试 + 六目标构建 + artifact），不发正式 Release。

## 8. 日常操作示例

### 2.x 修 bug / 发版

```powershell
cd 01-基线-日常维护
git switch -c fix/v2.1.4.1
# …改代码 + 补断言…
python _smoke_static.py
python _smoke_http.py
git add <逐个文件>
git commit -m "fix(v2.1.4.1): …"
git push -u origin fix/v2.1.4.1
gh pr create --base main --title "fix(v2.1.4.1): …" --body-file pr-body.md
gh pr checks --watch
gh pr merge --squash --delete-branch
```

### 3.0 功能

```powershell
cd 02-DEV-开发测试
git switch -c feat/3.0-m3-xxx
# …改 desktop/…
cargo test --workspace
cargo build -p drcom-ui
git add <逐个文件>
git commit -m "feat(3.0-m3): …"
git push -u origin feat/3.0-m3-xxx
gh pr create --base dev/3.0-altair --title "feat(3.0-m3): …" --body-file pr-body.md
gh pr checks --watch               # PR Gate + PR Bot + 三平台 cargo test
gh pr merge --squash --delete-branch
```

（也可以开 PR：机器人会按 `dev/` 规则审核，`PR Gate` 仍会跑 2.x 冒烟，确保没碰坏根目录代码。）

## 9. 常见问题

- **机器人说正文太短 / 缺小节**：按模板补；纯文档 PR 也一样，写清楚改了哪份文档、怎么核对。
- **fork PR 不评论**：外部 fork 的 PR 机器人拿不到写权限，会直接判失败 —— 请维护者手动 review（个人项目一般用不到 fork）。
- **草稿 PR**：Draft 状态下机器人只做提示、不阻断；标记 Ready 后会重跑。
- **改了 `.github/workflows/*.yml` 怎么验证**：小步合并、观察下一次 PR / push 的实际运行；或本地用 `actionlint`（可选，不强制）。
- **机器人规则误报**：加 `bot:override` 放行并在 PR 里说明原因，然后另开 PR 修规则。
