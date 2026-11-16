#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pr_review.py — PR 机器人审核（零第三方依赖，纯标准库）。

它做什么：
  在 PR 打开 / 更新 / 标记 ready 时，读 PR 的标题、正文、changed files 与 diff，
  跑一组**确定性规则**（不调 LLM、不联网猜），把结论作为一条评论贴回 PR；
  有阻断项时脚本退出码非 0 → 对应 job 变红 → 分支保护把 PR 挡在合并外 ✓。

为什么自己做（不装第三方 review bot）：
  仓库的立身之本是「零第三方依赖 / 可自审」；而且审核规则要写成这个项目自己的规矩
  （双线隔离 R6、版本字面量同步 I2、隐私红线 I12…），第三方 bot 不懂这些。

规则分三级：
  阻断（block）—— PR 不能合并，必须改；
  提示（warn） —— 可以合并，但请确认；
  信息（info） —— 给人看的上下文。

本地怎么跑（不连 GitHub）：
  python .github/scripts/pr_review.py --self-test          # 规则断言
  python .github/scripts/pr_review.py --dry-run --event fake.json --base-ref main
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

# ---- 常量 -----------------------------------------------------------------

MARKER = "<!-- pr-bot:review -->"
API_ROOT = "https://api.github.com"

TITLE_RE = re.compile(
    r"^(feat|fix|hotfix|docs|chore|refactor|test|ci|build|perf|style|revert)"
    r"(\([a-z0-9._/-]+\))?!?:\s+\S"
)

REQUIRED_SECTIONS = ("## 这是什么", "## 怎么验证")
OPTIONAL_SECTIONS = ("## 改动范围", "## 风险与回滚", "## 检查清单")

#: 2.x（Vega）运行时文件：只允许在 main 的 PR 里改。
TWO_X_PATHS = (
    re.compile(r"^(protocol|web_api|tray|auto_update|metrics|profiles|eula|version)\.py$"),
    # 审计 P2-12：主服务脚本是中文名，旧清单漏了它 —— 3.0 线 PR 可单改 2.x 主服务而不被拦 ✗
    # （必须配合 P2-11 的 core.quotePath=false，否则 git 把它转义成 "\350\201\224..." 根本匹配不上）
    re.compile(r"^联网_service\.py$"),
    re.compile(r"^(install|uninstall)\.bat$"),
    re.compile(r"^packaging/"),
    re.compile(r"^(_smoke_static|_smoke_http)\.py$"),
)

#: 新增行里的凭据形态：命中即阻断（宁可人工看，也不把密码发上 GitHub ✗）。
SECRET_LINE_PATTERNS = (
    # host 必须带「.」（真实域名 / IPv4），避免把 <http://a/b?password=...> 这种假样本当泄漏 ✗
    (re.compile(r"https?://[^\s\"'/]+\.[^\s\"']*password=[^\s&\"']+", re.I), "URL 里带 password= 参数"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥内容"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub personal access token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GitHub fine-grained token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "OpenAI 风格密钥"),
)

#: 不允许进入仓库的文件名 / 扩展名（模板文件除外）。
FORBIDDEN_NAMES = {"password.txt", "config.json", "secrets.json", ".env", "credentials.json"}
FORBIDDEN_EXTS = {".p12", ".pfx", ".pem", ".key", ".jks", ".keystore"}
ALLOW_TEMPLATE_SUFFIXES = (".template",)

#: 运行时目录：本来就被 .gitignore 覆盖；万一被 `git add -f` 带进来，机器人要拦住。
#: （审计 P1-3：以前只认文件名，logs/ 与方案专属密码文件都能混过去。）
FORBIDDEN_DIR_PREFIXES = ("logs/", "tools/", "python/", "packaging/output/")


def _is_profile_password_file(name):
    """方案专属密码文件 `password.<方案名>.txt`（v2.1.3.0 起，I6/I12）。"""
    return name.startswith("password.") and name.endswith(".txt")

#: 版本字面量同步清单（AGENTS.md §2 I2 / docs/VERSIONING.md §2）。
VERSION_SYNC_FILES = (
    "packaging/setup.iss",
    "联网_service.py",
    "install.bat",
    "uninstall.bat",
)
#: 版本号"自动读取"清单（v2.1.3.5 起）：这些脚本构建时从 version.py 读版本，不写字面量，
#: 所以只校验"确实有读取逻辑"，不再要求出现具体版本号（审计 J6）。
VERSION_AUTOREAD_FILES = (
    "packaging/build.ps1",
)
VERSION_SYNC_CHANGELOG = "CHANGELOG.md"
VERSION_SYNC_RELEASE_NOTES = "packaging/RELEASE-NOTES.md"

#: 发布模板允许的占位符（与 render_release_notes.py 保持一致）。
RELEASE_PLACEHOLDERS = {
    "VERSION", "CODENAME", "CODENAME_CN", "ASSET", "SIZE", "SIZE_MB", "SHA256",
    "SHA", "RUN", "DATE", "PREV_VERSION", "COMPARE_URL", "CHANGELOG_URL",
    "RELEASE_URL", "REPO",
}

FAIL = "block"
WARN = "warn"
INFO = "info"

# ---- 小工具 ---------------------------------------------------------------

def git(*args, cwd="."):
    """跑 git 命令并返回 stdout（失败抛 RuntimeError，让 CI 直接红）。"""
    # 审计 P2-11：必须显式关掉 core.quotePath —— 否则中文文件名（本仓库主服务脚本就是
    # 「联网_service.py」）会被 git 转义成 "\350\201\224..."，name-status / diff 的路径全部错位 ✗
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + " 失败：" + result.stderr.strip())
    return result.stdout


def git_show(path, cwd="."):
    """读 head 上的某个文件内容；文件不存在返回空串。"""
    # 与 git() 同口径：关掉 quotePath（P2-11），路径里的中文保持可读。
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "show", "HEAD:" + path], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout if result.returncode == 0 else ""


#: git 引号路径里的 C 风格转义（core.quotePath=true 时中文名会写成 "\350\201..."）。
_C_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    '"': '"', "\\": "\\",
}


def decode_git_path(token):
    """把 git 输出里的路径还原成真实文件名（处理 C 风格引号 + 八进制转义）。"""
    token = token or ""
    if len(token) < 2 or not (token.startswith('"') and token.endswith('"')):
        return token
    inner = token[1:-1]
    out = bytearray()
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\" or index + 1 >= len(inner):
            out += char.encode("utf-8")
            index += 1
            continue
        # 八进制转义：\350 → 0xE8（一个 UTF-8 字节）
        nxt = inner[index + 1]
        if nxt in "01234567":
            digits = ""
            cursor = index + 1
            while cursor < len(inner) and len(digits) < 3 and inner[cursor] in "01234567":
                digits += inner[cursor]
                cursor += 1
            out.append(int(digits, 8) & 0xFF)
            index = cursor
            continue
        out += _C_ESCAPES.get(nxt, nxt).encode("utf-8")
        index += 2
    return out.decode("utf-8", "replace")


def parse_name_status(output):
    """解析 git diff --name-status 的输出 → [(状态, 旧路径, 新路径)]。"""
    entries = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            entries.append(("R", decode_git_path(parts[1]), decode_git_path(parts[2])))
        elif len(parts) >= 2:
            path = decode_git_path(parts[1])
            entries.append((status, path, path))
    return entries


def parse_added_lines(diff_text):
    """解析 git diff（--unified=0）里的新增行 → [(路径, 新行号, 行内容)]。"""
    added = []
    current = ""
    new_line = 0
    for line in diff_text.splitlines():
        # 审计 P2-11：两种文件头都要认 —— 原文 `+++ b/x` 与带引号转义的 `+++ "b/x"`
        if line.startswith("+++ b/"):
            current = decode_git_path(line[6:])
        elif line.startswith('+++ "b/'):
            current = decode_git_path(line[4:])[2:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            new_line = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((current, new_line, line[1:]))
            new_line += 1
        elif not line.startswith("-"):
            new_line += 1
    return added


def changed_paths(entries):
    return [entry[-1] for entry in entries]


# ---- 规则：纯函数（可单测）------------------------------------------------

def check_title(title):
    """标题规范 + 长度。"""
    findings = []
    title = (title or "").strip()
    if not title:
        findings.append((FAIL, "title", "PR 标题为空"))
        return findings
    if re.match(r"(?i)^(wip|wip:|\[wip\])", title) or "WIP" in title:
        findings.append((FAIL, "title", "标题里带 WIP —— 请改成完成的标题（草稿请用 Draft PR）"))
    if not TITLE_RE.match(title):
        findings.append((FAIL, "title", "标题要符合 Conventional Commits：feat(scope): 中文说明 / fix: ..."))
    if len(title) > 100:
        findings.append((FAIL, "title", f"标题 {len(title)} 字符，超过 100（发布 / squash 后就是提交标题）"))
    elif len(title) > 72:
        findings.append((WARN, "title", f"标题 {len(title)} 字符，建议收敛到 72 以内"))
    return findings


def check_body(body):
    """PR 正文是否按模板填写。"""
    findings = []
    body = (body or "").strip()
    if len(body) < 80:
        findings.append((FAIL, "body", "PR 正文太短：请按模板写清「这是什么 / 怎么验证」（禁止只写一句话或空着）"))
        return findings
    for section in REQUIRED_SECTIONS:
        if section not in body:
            findings.append((FAIL, "body", f"缺少小节 {section} —— 请在正文里保留模板标题并填写"))
    for section in OPTIONAL_SECTIONS:
        if section not in body:
            findings.append((WARN, "body", f"缺少小节 {section}（建议保留，评审时好定位）"))
    return findings


def check_scope(base_ref, head_ref, paths):
    """双线隔离（R6 / R2）：main 只收 2.x 改动，dev/** 只收 3.0 改动。"""
    findings = []
    base_ref = (base_ref or "").strip()
    head_ref = (head_ref or "").strip()

    if base_ref in ("main", "master") and head_ref.startswith("dev/"):
        findings.append((FAIL, "scope", "3.0 的 dev/** 分支**不许**往 main 提 PR（R6：两线隔离，3.0 只发预览）"))
    if base_ref.startswith("dev/") and head_ref in ("main", "master"):
        findings.append((FAIL, "scope", "main 不许直接往 dev/** 合（先走各自 PR，别把两条线拧在一起）"))
    if base_ref in ("main", "master"):
        for path in paths:
            if path == "desktop" or path.startswith("desktop/"):
                findings.append((FAIL, "scope", f"{path} 是 3.0 代码，只能进 dev/3.0-altair，不能合 main（R6）"))
                break
    if base_ref.startswith("dev/"):
        for path in paths:
            if any(pattern.match(path) for pattern in TWO_X_PATHS):
                findings.append((FAIL, "scope", f"{path} 是 2.x（Vega）代码，请在 main 上开 PR 改，不要动 3.0 分支"))
                break
    return findings


def check_privacy(paths, added_lines):
    """隐私红线（I6 / I12）：凭据文件、私钥、token、URL 带密码。"""
    findings = []
    for path in paths:
        lower = path.lower()
        name = lower.rsplit("/", 1)[-1]
        if name in FORBIDDEN_NAMES and not lower.endswith(ALLOW_TEMPLATE_SUFFIXES):
            findings.append((FAIL, "privacy", f"{path} 是运行时凭据文件，永不入库（I6/I12）；模板请用 .template 后缀"))
        if any(lower.startswith(prefix) for prefix in FORBIDDEN_DIR_PREFIXES):
            findings.append((FAIL, "privacy", f"{path} 在运行时目录里（已 .gitignore，永不入库）"))
        if _is_profile_password_file(name) and not lower.endswith(ALLOW_TEMPLATE_SUFFIXES):
            findings.append((FAIL, "privacy", f"{path} 是方案专属密码文件，永不入库（I6/I12）"))
        suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        if suffix in FORBIDDEN_EXTS:
            findings.append((FAIL, "privacy", f"{path} 是私钥 / 证书文件，永不入库"))
    for path, lineno, text in added_lines:
        for pattern, label in SECRET_LINE_PATTERNS:
            if pattern.search(text):
                findings.append((FAIL, "privacy", f"{path}:{lineno} 疑似{label}：{text.strip()[:80]}"))
    return findings


def check_version_sync(version, files):
    """版本号同步（I2）：改了 version.py，就必须同步清单里每一处。"""
    findings = []
    if not version:
        findings.append((FAIL, "version", "version.py 里读不到 VERSION 字面量"))
        return findings
    for path in VERSION_SYNC_FILES:
        content = files.get(path, "")
        if not content:
            findings.append((FAIL, "version", f"读不到 {path}，无法确认版本号是否同步"))
        elif version not in content:
            findings.append((FAIL, "version", f"{path} 里没有 {version} —— 版本字面量没同步（I2）"))
    for path in VERSION_AUTOREAD_FILES:
        content = files.get(path, "")
        if not content:
            findings.append((FAIL, "version", f"读不到 {path}，无法确认它是否自动读取版本号"))
        elif "version.py" not in content or "VERSION" not in content:
            findings.append((FAIL, "version", f"{path} 没有从 version.py 读取版本号的逻辑（J6/I2）"))
    changelog = files.get(VERSION_SYNC_CHANGELOG, "")
    if f"## v{version}" not in changelog:
        findings.append((FAIL, "version", f"{VERSION_SYNC_CHANGELOG} 缺少 ## v{version} 段落（I2）"))
    release_notes = files.get(VERSION_SYNC_RELEASE_NOTES, "")
    if "{{VERSION}}" not in release_notes:
        findings.append((FAIL, "version", f"{VERSION_SYNC_RELEASE_NOTES} 必须用 {{{{VERSION}}}} 占位（CI 注入）"))
    return findings


def check_release_template(version_notes):
    """发布模板（docs/RELEASING.md）：首行必须带代号占位符，且不许出现未知占位符。"""
    findings = []
    if not version_notes:
        findings.append((FAIL, "release", "读不到 packaging/RELEASE-NOTES.md"))
        return findings
    lines = version_notes.splitlines()
    first_line = lines[0] if lines else ""
    if "{{CODENAME}}" not in first_line or "{{CODENAME_CN}}" not in first_line:
        findings.append((FAIL, "release", "RELEASE-NOTES 首行必须含 {{CODENAME}} 与 {{CODENAME_CN}} 占位（代号由 CI 从 version.py 注入）"))
    unknown = sorted({
        match.group(1) for match in re.finditer(r"\{\{([A-Z0-9_]+)\}\}", version_notes)
        if match.group(1) not in RELEASE_PLACEHOLDERS
    })
    if unknown:
        findings.append((FAIL, "release", "RELEASE-NOTES 里有渲染器不认识的占位符：" + "、".join(unknown)))
    return findings


def check_size(files_count, additions, deletions):
    """PR 大小：提醒拆小；特大 PR 需要说明。"""
    findings = []
    total = additions + deletions
    if files_count == 0:
        findings.append((FAIL, "size", "PR 没有任何文件改动"))
    elif files_count > 80:
        findings.append((FAIL, "size", f"改了 {files_count} 个文件 —— 请拆成多个 PR（一个 PR 一件事）"))
    elif files_count > 30:
        findings.append((WARN, "size", f"改了 {files_count} 个文件，评审成本偏大，能拆就拆"))
    if total > 5000:
        findings.append((FAIL, "size", f"改动 {total} 行 —— 请拆成多个 PR，否则审不动"))
    elif total > 1500:
        findings.append((WARN, "size", f"改动 {total} 行，建议在正文里说明为什么必须一次改这么多"))
    return findings


def check_changelog(paths):
    """用户可见的代码改动应同步 CHANGELOG / 发布说明。"""
    # .github/ 下是 CI / 审核脚本，不是用户可见的运行时代码，别误报 ✗
    code = [p for p in paths
            if (p.endswith(".py") and not p.startswith(".github/")) or p == "packaging/setup.iss"]
    docs = {VERSION_SYNC_CHANGELOG, VERSION_SYNC_RELEASE_NOTES}
    if code and not any(p in docs for p in paths):
        return [(WARN, "changelog", "改了运行时代码但没动 CHANGELOG.md / RELEASE-NOTES.md —— 用户可见改动请补上")]
    return []


# ---- 汇总渲染 -------------------------------------------------------------

LEVEL_ICON = {FAIL: "⛔", WARN: "⚠️", INFO: "ℹ️"}
LEVEL_NAME = {FAIL: "阻断", WARN: "提示", INFO: "信息"}


def verdict(findings, overridden=False):
    blocks = [item for item in findings if item[0] == FAIL]
    warns = [item for item in findings if item[0] == WARN]
    if blocks and overridden:
        return "warn", f"有 {len(blocks)} 项阻断被 bot:override 豁免 —— 请人工确认后再合并"
    if blocks:
        return "fail", f"有 {len(blocks)} 项阻断，改完会自动重跑"
    if warns:
        return "warn", f"通过（{len(warns)} 项提示，建议确认）"
    return "pass", "全部检查通过"


def render_comment(pr, findings, result, reason, overridden=False, repo=""):
    """生成贴回 PR 的 markdown（带隐藏标记，方便下次更新同一条评论）。"""
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}[result]
    if repo:
        policy_link = f"[docs/PR-WORKFLOW.md](https://github.com/{repo}/blob/main/docs/PR-WORKFLOW.md)"
    else:
        policy_link = "docs/PR-WORKFLOW.md"
    lines = [
        MARKER,
        "## 🤖 PR Bot 审核报告",
        "",
        f"**结论**：{icon} {reason}",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| 基线 | `{pr['base']}` ← `{pr['head']}` |",
        f"| 改动文件 | {pr['files']} 个 |",
        f"| 增删 | +{pr['additions']} / -{pr['deletions']} |",
        f"| 提交数 | {pr['commits']} |",
        "",
    ]
    if findings:
        lines += ["| 级别 | 规则 | 说明 |", "| --- | --- | --- |"]
        for level, rule, message in findings:
            lines.append(f"| {LEVEL_ICON[level]} {LEVEL_NAME[level]} | `{rule}` | {message} |")
    else:
        lines.append("没有发现问题。")
    lines += [
        "",
        "---",
        "本评论由 .github/workflows/pr-bot.yml 自动生成 / 更新（只读审核，不改代码）。",
        f"规则与合并策略见 {policy_link}；误报可加 `bot:override` 标签豁免（会在评论里标明）并请维护者人工确认。",
        "本地复现：python .github/scripts/pr_review.py --self-test。",
    ]
    if overridden:
        lines.insert(2, "> ⚠️ 本 PR 带有 `bot:override` 标签：阻断项已降级为提示，**维护者必须人工复核**。")
        lines.insert(3, "")
    return "\n".join(lines) + "\n"


# ---- GitHub API -----------------------------------------------------------

def api(method, path, token, data=None):
    url = API_ROOT + path
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def upsert_comment(repository, number, token, comment):
    """同一条评论原地更新，避免每个 commit 刷屏。"""
    existing = api("GET", f"/repos/{repository}/issues/{number}/comments?per_page=100", token)
    for item in existing:
        if MARKER in (item.get("body") or ""):
            return api("PATCH", f"/repos/{repository}/issues/comments/{item['id']}", token, {"body": comment})
    return api("POST", f"/repos/{repository}/issues/{number}/comments", token, {"body": comment})


# ---- 主流程 ---------------------------------------------------------------

def collect(repo_dir, base_ref):
    """从本地 checkout 收集 PR 的改动事实。"""
    base = "origin/" + base_ref
    entries = parse_name_status(git("diff", "--name-status", f"{base}...HEAD", cwd=repo_dir))
    paths = changed_paths(entries)
    numstat = git("diff", "--numstat", f"{base}...HEAD", cwd=repo_dir)
    additions = deletions = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit():
            additions += int(parts[0])
            deletions += int(parts[1])
    commits = int(git("rev-list", "--count", f"{base}..HEAD", cwd=repo_dir).strip() or 0)

    diff = git("diff", "--unified=0", f"{base}...HEAD", cwd=repo_dir)
    added = parse_added_lines(diff)  # (path, lineno, text)
    return {
        "paths": paths,
        "entries": entries,
        "additions": additions,
        "deletions": deletions,
        "commits": commits,
        "added": added,
    }


def review(pr, facts, repo_dir):
    """跑全部规则，返回 findings。"""
    findings = []
    findings += check_title(pr["title"])
    findings += check_body(pr["body"])
    findings += check_scope(pr["base"], pr["head"], facts["paths"])
    findings += check_privacy(facts["paths"], facts["added"])
    findings += check_size(len(facts["paths"]), facts["additions"], facts["deletions"])
    findings += check_changelog(facts["paths"])

    if "version.py" in facts["paths"]:
        head_version = git_show("version.py", repo_dir)
        match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', head_version, re.M)
        version = match.group(1) if match else ""
        files = {path: git_show(path, repo_dir) for path in (
            *VERSION_SYNC_FILES, VERSION_SYNC_CHANGELOG, VERSION_SYNC_RELEASE_NOTES)}
        findings += check_version_sync(version, files)

    if VERSION_SYNC_RELEASE_NOTES in facts["paths"]:
        findings += check_release_template(git_show(VERSION_SYNC_RELEASE_NOTES, repo_dir))

    if facts["commits"] > 8:
        findings.append((WARN, "commits", f"{facts['commits']} 个提交 —— 合并时会 squash 成一条，请确认标题能代表全部改动"))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description="PR 机器人审核（零第三方依赖）")
    parser.add_argument("--event", default="", help="GitHub event JSON 路径")
    parser.add_argument("--repo", default="", help="owner/repo")
    parser.add_argument("--token", default="", help="GitHub token（GITHUB_TOKEN）")
    parser.add_argument("--base-ref", default="", help="基线分支（默认取 event 里的）")
    parser.add_argument("--repo-dir", default=".", help="仓库 checkout 路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印结论，不联网、不评论")
    parser.add_argument("--self-test", action="store_true", help="跑内置断言后退出")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if not args.event:
        parser.error("--event 必填（或改用 --self-test）")
    # utf-8-sig：容忍 BOM（本地用 PowerShell 生成的 fake event 可能带 BOM）
    with open(args.event, "r", encoding="utf-8-sig") as handle:
        event = json.load(handle)
    pull = event.get("pull_request") or {}
    if not pull:
        print("[skip] 事件里没有 pull_request")
        return 0

    pr = {
        "number": pull.get("number", 0),
        "title": pull.get("title", ""),
        "body": pull.get("body") or "",
        "base": (pull.get("base") or {}).get("ref", ""),
        "head": (pull.get("head") or {}).get("ref", ""),
        "draft": bool(pull.get("draft")),
        "files": pull.get("changed_files", 0),
        "additions": pull.get("additions", 0),
        "deletions": pull.get("deletions", 0),
        "commits": pull.get("commits", 0),
    }
    labels = [item.get("name", "") for item in (pull.get("labels") or [])]
    override = "bot:override" in labels
    if args.base_ref:
        pr["base"] = args.base_ref

    facts = collect(args.repo_dir, pr["base"])

    if pr["draft"]:
        result, reason = "pass", "草稿 PR：只做提示，不阻断（标记 Ready 后重跑）"
        findings = []
    else:
        findings = review(pr, facts, args.repo_dir)
        result, reason = verdict(findings, overridden=override)
        if override:
            findings = [(WARN if level == FAIL else level, rule, message)
                        for level, rule, message in findings]

    comment = render_comment(pr, findings, result, reason, overridden=override, repo=args.repo)
    print(comment)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(comment)

    if args.dry_run:
        print("[dry-run] 未调用 GitHub API")
        return 1 if result == "fail" else 0

    if not (args.repo and args.token):
        print("[错误] 缺少 --repo / --token（或用 --dry-run）", file=sys.stderr)
        return 2
    if (pull.get("head") or {}).get("repo", {}).get("fork"):
        print("[错误] 来自 fork 的 PR：机器人无权评论，请维护者人工审核", file=sys.stderr)
        return 1
    try:
        upsert_comment(args.repo, pr["number"], args.token, comment)
        print("[ok] 审核评论已更新")
    except urllib.error.HTTPError as error:
        print(f"[错误] 评论 PR 失败：HTTP {error.code} {error.reason}", file=sys.stderr)
        return 1
    return 1 if result == "fail" else 0


# ---- 自测 -----------------------------------------------------------------

def self_test():
    failures = []

    def check(name, condition, detail=""):
        if condition:
            print(f"PASS  {name}")
        else:
            failures.append(name)
            print(f"FAIL  {name}  {detail}")

    # 标题
    check("标题：合规通过", not [f for f in check_title("feat(3.0-ui): 设置窗口重排") if f[0] == FAIL])
    check("标题：缺前缀被拦", any(f[0] == FAIL for f in check_title("改一下设置窗口")))
    check("标题：WIP 被拦", any(f[0] == FAIL for f in check_title("WIP 别合")))
    check("标题：过长被拦", any(f[0] == FAIL for f in check_title("fix: " + "长" * 110)))

    # 正文
    full = "## 这是什么\nx\n## 改动范围\nx\n## 怎么验证\nx\n## 风险与回滚\nx\n## 检查清单\nx\n" + "说明" * 20
    check("正文：完整通过", not [f for f in check_body(full) if f[0] == FAIL])
    check("正文：空的被拦", any(f[0] == FAIL for f in check_body("")))
    check("正文：缺验证小节被拦", any(f[0] == FAIL for f in check_body("## 这是什么\n" + "x" * 200)))

    # 双线隔离
    check("范围：main 收 desktop 被拦", any(f[0] == FAIL for f in check_scope("main", "feat/x", ["desktop/crates/lib.rs"])))
    check("范围：main 收 docs 放行", not check_scope("main", "docs/x", ["docs/STORAGE.md"]))
    check("范围：dev 收 2.x 代码被拦", any(f[0] == FAIL for f in check_scope("dev/3.0-altair", "feat/x", ["web_api.py"])))
    check("范围：dev 收 desktop 放行", not check_scope("dev/3.0-altair", "feat/x", ["desktop/crates/lib.rs"]))
    check("范围：dev 往 main 提被拦", any(f[0] == FAIL for f in check_scope("main", "dev/3.0-altair", [])))
    # 审计 P2-12：2.x 主服务脚本是中文名，必须被双线隔离拦住；main 上照常放行。
    check("范围：dev 收主服务脚本（联网_service.py）被拦",
          any(f[0] == FAIL for f in check_scope("dev/3.0-altair", "feat/x", ["联网_service.py"])))
    check("范围：dev 收 2.x 冒烟脚本被拦",
          any(f[0] == FAIL for f in check_scope("dev/3.0-altair", "feat/x", ["_smoke_static.py"])))
    check("范围：main 收主服务脚本放行", not check_scope("main", "fix/x", ["联网_service.py"]))
    check("范围：main 收 desktop 仍然被拦",
          any(f[0] == FAIL for f in check_scope("main", "feat/x", ["desktop/app/main.rs"])))

    # 路径解析（审计 P2-11：中文文件名不能被 core.quotePath 转义成 "\350\201..."）
    check("路径：name-status 中文名原样",
          changed_paths(parse_name_status("M\t联网_service.py\n")) == ["联网_service.py"])
    check("路径：name-status 八进制转义还原",
          changed_paths(parse_name_status('M\t"\\350\\201\\224\\347\\275\\221_service.py"\n'))
          == ["联网_service.py"])
    check("路径：重命名两侧都还原",
          parse_name_status('R100\t"a/\\350\\201\\224.txt"\tb/联网.txt\n')[0]
          == ("R", "a/联.txt", "b/联网.txt"))
    _diff = ("diff --git a/联网_service.py b/联网_service.py\n"
             "--- a/联网_service.py\n"
             "+++ b/联网_service.py\n"
             "@@ -10,0 +11 @@\n"
             "+password = \"x\"\n")
    check("diff：中文路径行归属正确",
          parse_added_lines(_diff) == [("联网_service.py", 11, 'password = "x"')])
    _diff_quoted = ('diff --git "a/\\350\\201\\224\\347\\275\\221_service.py" "b/\\350\\201\\224\\347\\275\\221_service.py"\n'
                    '--- "a/\\350\\201\\224\\347\\275\\221_service.py"\n'
                    '+++ "b/\\350\\201\\224\\347\\275\\221_service.py"\n'
                    "@@ -3,0 +4 @@\n"
                    "+x = 1\n"
                    "+y = 2\n")
    check("diff：带引号转义的路径行归属与行号都正确",
          parse_added_lines(_diff_quoted) == [("联网_service.py", 4, "x = 1"),
                                              ("联网_service.py", 5, "y = 2")])

    # 隐私
    check("隐私：password.txt 被拦", any(f[0] == FAIL for f in check_privacy(["password.txt"], [])))
    check("隐私：模板文件放行", not check_privacy(["packaging/password.txt.template"], []))
    check("隐私：方案专属密码文件被拦", any(f[0] == FAIL for f in check_privacy(["password.宿舍.txt"], [])))
    check("隐私：logs/ 下的文件被拦", any(f[0] == FAIL for f in check_privacy(["logs/campus_login.log"], [])))
    check("隐私：打包产物被拦", any(f[0] == FAIL for f in check_privacy(["packaging/output/x.exe"], [])))
    check("隐私：普通根目录文件不误报", not check_privacy(["profiles.py", "docs/RELEASING.md"], []))
    _leak = "u='http://172.16.80.3:801/login?pass" + "word=abc'"
    check("隐私：URL 带密码被拦", any(f[0] == FAIL for f in check_privacy(["x.py"], [("x.py", 3, _leak)])))
    check("隐私：普通行不误报", not check_privacy(["x.py"], [("x.py", 3, "password = read_password()")]))

    # 版本同步
    good = {path: "2.1.4.0" for path in VERSION_SYNC_FILES}
    good.update({path: "Select-String version.py VERSION" for path in VERSION_AUTOREAD_FILES})
    good.update({VERSION_SYNC_CHANGELOG: "## v2.1.4.0 (feature)", VERSION_SYNC_RELEASE_NOTES: "v{{VERSION}} {{CODENAME}}"})
    check("版本：全同步通过", not check_version_sync("2.1.4.0", good))
    good["install.bat"] = "v2.1.3.0"
    check("版本：漏一处被拦", any(f[0] == FAIL for f in check_version_sync("2.1.4.0", good)))
    _auto = dict(good, **{"install.bat": "2.1.4.0"})
    _auto["packaging/build.ps1"] = "$AppVersionText = 'v2.1.4.0'"
    check("版本：自动读取文件里写死字面量也不算通过（J6）",
          any(f[0] == FAIL for f in check_version_sync("2.1.4.0", _auto)))
    _auto["packaging/build.ps1"] = "$VersionFile = version.py; $ver = '''^VERSION'''"
    check("版本：自动读取文件有读取逻辑即通过（J6）",
          not check_version_sync("2.1.4.0", _auto))

    # 发布模板
    check("发布模板：合规通过", not check_release_template('v{{VERSION}} "{{CODENAME}}"（{{CODENAME_CN}}）\n'))
    check("发布模板：未知占位符被拦", any(f[0] == FAIL for f in check_release_template("v{{VERSION}} {{OOPS}}\n")))

    # 大小
    check("大小：空 PR 被拦", any(f[0] == FAIL for f in check_size(0, 0, 0)))
    check("大小：大 PR 提醒", all(f[0] == WARN for f in check_size(40, 900, 400)))

    # 结论 / 评论
    findings = [(FAIL, "title", "x")]
    check("结论：有阻断 → fail", verdict(findings)[0] == "fail")
    check("结论：override → warn", verdict(findings, overridden=True)[0] == "warn")
    comment = render_comment({"base": "main", "head": "feat/x", "files": 3, "additions": 10,
                              "deletions": 2, "commits": 1}, findings, "fail", "x")
    check("评论：带隐藏标记", MARKER in comment)
    check("评论：包含规则表", "title" in comment)

    if failures:
        print()
        print(f"自测失败：{len(failures)} 项 —— " + "、".join(failures))
        return 1
    print()
    print("自测通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
