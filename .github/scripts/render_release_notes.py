#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_release_notes.py — 把发布说明模板渲染成 GitHub Release 正文（零第三方依赖）。

设计原则（为什么要有这个脚本）：
1. **机器事实不进人手**：版本号 / 文件大小 / SHA-256 / 对比链接这类"必须准确"的内容，
   一律由 CI 注入，模板里只写占位符，避免像 v2.1.0.0 那样把旧代号写死在发布页 ✗；
2. **漏填就失败**：渲染后不允许残留 `{{...}}` 占位符 —— 宁可让 CI 红，也不把 `{{SIZE}}`
   发到用户看的发布页 ✗；
3. **发布前顺手做一次隐私检查**：正文里出现"URL 带密码 / 私钥 / 常见 token 形态"直接判失败；
4. **本地可复现**：`--self-test` 跑断言，`--dry-run` 只打印不写盘。

用法（CI 里就是这么调的，见 .github/workflows/build-installer.yml）：

    python .github/scripts/render_release_notes.py \
        --repo TSS-Small-sunshine/StardustFlashLink \
        --template packaging/RELEASE-NOTES.md \
        --channel versioned \
        --version 2.1.4.0 --codename Vega --codename-cn 织女星 \
        --asset StardustFlashLink-Setup-v2.1.4.0.exe --size 12345678 \
        --sha256 <hex> --commit <sha> --run 123 \
        --prev-tag v2.1.3.0 \
        --out-file "$env:RUNNER_TEMP/release-notes-versioned.md" \
        --title-out "$env:RUNNER_TEMP/release-title-versioned.txt"

支持的占位符（全部由本脚本注入）：
    {{VERSION}} {{CODENAME}} {{CODENAME_CN}} {{ASSET}} {{SIZE}} {{SIZE_MB}}
    {{SHA256}} {{SHA}} {{RUN}} {{DATE}} {{PREV_VERSION}} {{COMPARE_URL}}
    {{CHANGELOG_URL}} {{RELEASE_URL}} {{REPO}}
"""

import argparse
import datetime
import pathlib
import re
import sys

# 控制台编码兜底（与 _smoke_static.py / _smoke_http.py 同一套做法）：
# CI runner 的 Python stdout 是 cp1252、Windows 控制台可能是 GBK —— 打印中文/“✓”/emoji
# 会抛 UnicodeEncodeError，把「渲染成功」变成「脚本崩溃」，看起来像渲染器坏了 ✗。
# 只改错误处理方式，不强行改编码（否则 GBK 终端会变乱码）。
try:
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass

# ---- 常量 -----------------------------------------------------------------

#: 模板里允许出现的占位符；出现别的 `{{XXX}}` 一律判失败（多半是模板笔误或忘了填）。
PLACEHOLDER_NAMES = (
    "VERSION", "CODENAME", "CODENAME_CN", "ASSET", "SIZE", "SIZE_MB",
    "SHA256", "SHA", "RUN", "DATE", "PREV_VERSION", "COMPARE_URL",
    "CHANGELOG_URL", "RELEASE_URL", "REPO",
)

#: 发布标题标记：只写一行注释，渲染时抽出来并从正文里删掉。
TITLE_MARKER_RE = re.compile(r"<!--\s*RELEASE-TITLE:\s*(?P<title>.+?)\s*-->")

#: 渲染后残留的占位符。
LEFTOVER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")

#: 发布正文的隐私检查：命中任意一条就拒绝发布（宁可人工介入）。
SECRET_PATTERNS = (
    (re.compile(r"https?://[^\s\"']*password=[^&\s\"']+", re.I), "URL 里带 password= 参数"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥内容"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub personal access token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GitHub fine-grained token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "OpenAI 风格密钥"),
)

#: GitHub Release 正文上限是 125000 字符，留一点余量。
MAX_BODY_CHARS = 100000

#: Release 标题上限（GitHub 是 125 字符），留一点余量。
MAX_TITLE_CHARS = 120


def _shanghai_today():
    """返回 Asia/Shanghai 的今天（CI runner 是 UTC，凌晨发版日期会差一天 ✗）。"""
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    return now.strftime("%Y-%m-%d")


def extract_title(template):
    """从模板里抽出 `<!-- RELEASE-TITLE: ... -->`；返回 (标题, 去掉标记后的模板)。"""
    match = TITLE_MARKER_RE.search(template)
    if not match:
        return "", template
    title = match.group("title").strip()
    cleaned = TITLE_MARKER_RE.sub("", template)
    # 标记独占一行时，删掉会留下空行；最多收掉一行，保持正文紧凑。
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return title, cleaned.strip() + "\n"


def build_context(args):
    """把命令行参数换算成模板占位符字典。"""
    version = args.version.strip()
    tag = f"v{version}"
    repo = args.repo.strip()
    size = int(args.size) if str(args.size).strip() else 0
    size_mb = f"{size / (1024 * 1024):.2f}" if size else "—"
    prev_tag = (args.prev_tag or "").strip()
    if prev_tag:
        prev_version = prev_tag[1:] if prev_tag.startswith("v") else prev_tag
        compare_url = f"https://github.com/{repo}/compare/{prev_tag}...{tag}"
    else:
        prev_version = "—"
        compare_url = f"https://github.com/{repo}/releases/tag/{tag}"
    if args.channel == "installer":
        release_url = f"https://github.com/{repo}/releases/tag/installer"
    else:
        release_url = f"https://github.com/{repo}/releases/tag/{tag}"
    return {
        "VERSION": version,
        "CODENAME": args.codename.strip(),
        "CODENAME_CN": args.codename_cn.strip(),
        "ASSET": args.asset.strip(),
        "SIZE": str(size),
        "SIZE_MB": size_mb,
        "SHA256": args.sha256.strip().lower(),
        "SHA": args.commit.strip(),
        "RUN": str(args.run).strip(),
        "DATE": (args.date or "").strip() or _shanghai_today(),
        "PREV_VERSION": prev_version,
        "COMPARE_URL": compare_url,
        "CHANGELOG_URL": f"https://github.com/{repo}/blob/{tag}/CHANGELOG.md",
        "RELEASE_URL": release_url,
        "REPO": repo,
    }


def render(template, context):
    """替换占位符；返回 (正文, 未定义的占位符列表)。"""
    body = template
    for name, value in context.items():
        body = body.replace("{{" + name + "}}", value)
    leftovers = sorted(set(LEFTOVER_RE.findall(body)))
    return body, leftovers


def check_body(body):
    """发布前检查：长度 + 隐私。返回问题列表（空 = 通过）。"""
    problems = []
    if len(body) > MAX_BODY_CHARS:
        problems.append(f"正文 {len(body)} 字符，超过上限 {MAX_BODY_CHARS}")
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(body):
            problems.append(f"疑似泄露：{label}")
    return problems


def default_title(args):
    """模板没写 RELEASE-TITLE 时的兜底标题。"""
    if args.channel == "installer":
        return f"Windows 安装程序 v{args.version}（固定下载链接 · 跟随最新构建）"
    return f"v{args.version}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="渲染发布说明（模板 → GitHub Release 正文）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--template", default="", help="模板路径（UTF-8）")
    parser.add_argument("--repo", default="", help="owner/repo")
    parser.add_argument("--version", default="", help="版本号，如 2.1.4.0")
    parser.add_argument("--codename", default="", help="英文代号，如 Vega")
    parser.add_argument("--codename-cn", default="", help="中文代号，如 织女星")
    parser.add_argument("--channel", choices=("versioned", "installer"), default="versioned")
    parser.add_argument("--asset", default="", help="安装包文件名")
    parser.add_argument("--size", default="", help="安装包字节数")
    parser.add_argument("--sha256", default="", help="安装包 SHA-256")
    parser.add_argument("--commit", default="", help="构建提交 SHA")
    parser.add_argument("--run", default="", help="构建编号（GitHub run_number）")
    parser.add_argument("--date", default="", help="日期（默认 Asia/Shanghai 今天）")
    parser.add_argument("--prev-tag", default="", help="上一版 tag，如 v2.1.3.0（用于对比链接）")
    parser.add_argument("--out-file", default="", help="正文输出路径")
    parser.add_argument("--title-out", default="", help="标题输出路径（单行）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    parser.add_argument("--self-test", action="store_true", help="跑内置断言后退出")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    missing = [name for name in ("template", "repo", "version") if not getattr(args, name)]
    if missing:
        parser.error("缺少必填参数：" + "、".join("--" + name for name in missing))

    template_path = pathlib.Path(args.template)
    if not template_path.is_file():
        print(f"[错误] 模板不存在：{template_path}", file=sys.stderr)
        return 2
    template = template_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")

    title_from_template, cleaned = extract_title(template)
    context = build_context(args)
    body, leftovers = render(cleaned, context)
    if leftovers:
        print("[错误] 渲染后仍有未定义占位符：" + "、".join(leftovers), file=sys.stderr)
        print("       请检查模板里有没有拼错的占位符，或脚本的 PLACEHOLDER_NAMES 是否需要补。", file=sys.stderr)
        return 2
    problems = check_body(body)
    if problems:
        for problem in problems:
            print("[错误] " + problem, file=sys.stderr)
        return 2

    # 标题也要走同一套占位符替换：模板里写 v{{VERSION}} — 多网络多账号 时，
    # 以前只有正文被替换、标题原样带着 {{VERSION}} 发出去 ✗（本版新修）。
    if title_from_template:
        title, title_leftovers = render(title_from_template, context)
        if title_leftovers:
            print("[错误] 发布标题里仍有未定义占位符：" + "、".join(title_leftovers), file=sys.stderr)
            return 2
    else:
        title = default_title(args)
    if len(title) > MAX_TITLE_CHARS:
        print("[错误] 发布标题 {} 字符，超过上限 {}".format(len(title), MAX_TITLE_CHARS), file=sys.stderr)
        return 2

    if args.dry_run:
        print("=== TITLE ===")
        print(title)
        print()
        print("=== BODY ===")
        print(body)
        return 0

    if args.out_file:
        pathlib.Path(args.out_file).write_text(body, encoding="utf-8")
    if args.title_out:
        pathlib.Path(args.title_out).write_text(title + "\n", encoding="utf-8")
    print(f"[ok] 发布说明已渲染：{args.out_file or '(stdout)'} （{len(body)} 字符）")
    print(f"[ok] 发布标题：{title}")
    return 0


# ---- 自测 -----------------------------------------------------------------

def self_test():
    """内置断言：不进 CI 也能在本地证明渲染逻辑是好的。"""
    failures = []

    def check(name, condition, detail=""):
        if condition:
            print(f"PASS  {name}")
        else:
            failures.append(name)
            print(f"FAIL  {name}  {detail}")

    # 1) 标题标记抽取 + 删行
    tpl = "<!-- RELEASE-TITLE: 多网络多账号 -->\nWindows v{{VERSION}} {{CODENAME}} {{CODENAME_CN}}\n"
    title, cleaned = extract_title(tpl)
    check("标题标记能被抽出", title == "多网络多账号")
    check("标题标记不出现在正文里", "RELEASE-TITLE" not in cleaned)
    check("正文首行保留", cleaned.splitlines()[0].startswith("Windows v{{VERSION}}"), repr(cleaned))

    # 1b) 标题里的占位符也要替换（旧实现只渲染正文，标题会原样带 {{VERSION}} 发出去 ✗）
    _t2, _c2 = extract_title("<!-- RELEASE-TITLE: v{{VERSION}} — 多网络多账号 -->\n正文\n")
    _t2_rendered, _t2_left = render(_t2, {"VERSION": "9.9.9.9"})
    check("标题占位符会被替换", _t2_rendered == "v9.9.9.9 — 多网络多账号" and not _t2_left,
          repr((_t2_rendered, _t2_left)))

    # 2) 占位符替换
    ctx = {
        "VERSION": "2.1.4.0", "CODENAME": "Vega", "CODENAME_CN": "织女星",
        "ASSET": "StardustFlashLink-Setup-v2.1.4.0.exe", "SIZE": "12345678",
        "SIZE_MB": "11.77", "SHA256": "ab" * 32, "SHA": "c" * 40, "RUN": "42",
        "DATE": "2026-11-15", "PREV_VERSION": "2.1.3.0",
        "COMPARE_URL": "https://github.com/o/r/compare/v2.1.3.0...v2.1.4.0",
        "CHANGELOG_URL": "https://github.com/o/r/blob/v2.1.4.0/CHANGELOG.md",
        "RELEASE_URL": "https://github.com/o/r/releases/tag/v2.1.4.0",
        "REPO": "o/r",
    }
    body, leftovers = render(tpl, ctx)
    check("占位符全部替换", not leftovers and "{{VERSION}}" not in body)
    check("版本号写进正文", "2.1.4.0" in body and "Vega" in body)

    # 3) 漏填的占位符要被发现
    _body, leftovers = render("x {{NOT_A_REAL_KEY}} y", ctx)
    check("未定义占位符会被拒绝", leftovers == ["{{NOT_A_REAL_KEY}}"], repr(leftovers))

    # 4) 对比链接与安装器通道
    class _Args:
        version = "2.1.4.0"
        codename = "Vega"
        codename_cn = "织女星"
        repo = "o/r"
        size = "1048576"
        asset = "a.exe"
        sha256 = "AB" * 32
        commit = "c" * 40
        run = "7"
        date = ""
        prev_tag = "v2.1.3.0"
        channel = "versioned"
    _a = _Args()
    _ctx = build_context(_a)
    check("对比链接 = 上一版 tag...本版 tag",
          _ctx["COMPARE_URL"] == "https://github.com/o/r/compare/v2.1.3.0...v2.1.4.0")
    check("SIZE_MB 保留两位", _ctx["SIZE_MB"] == "1.00")
    check("SHA256 转小写", _ctx["SHA256"] == ("ab" * 32))
    _a.channel = "installer"
    _ctx_installer = build_context(_a)
    check("installer 通道指回固定下载链接",
          _ctx_installer["RELEASE_URL"] == "https://github.com/o/r/releases/tag/installer")
    class _NoPrev(_Args):
        prev_tag = ""
    _ctx_no_prev = build_context(_NoPrev())
    check("没有上一版时 PREV_VERSION 用 —", _ctx_no_prev["PREV_VERSION"] == "—")

    # 5) 隐私检查：正样本必须命中，负样本不能误报
    check("URL 带 password= 会被拦",
          bool(check_body("see https://x/y?password=abc123")))
    check("正常文字不误报", check_body("密码只存在 password.txt 里") == [])
    # 断言串拆开写：完整私钥头是机器人隐私规则的敏感形态，散在源码里会被当成真泄漏 ✗
    # （自测要证明的是"这种文本会被拦"，不是要把这串字面量提交进仓库 ✓）
    check("私钥会被拦", bool(check_body("-----BEGIN " + "RSA PRIVATE KEY-----")))
    check("超长正文会被拦", bool(check_body("a" * (MAX_BODY_CHARS + 1))))

    # 6) 常量与上下文必须一一对应：新增占位符只改一边 = 渲染后{{XXX}}残留或模板填不进值
    _ctx_keys = set(_ctx)
    check("PLACEHOLDER_NAMES 与 build_context 的键完全一致",
          set(PLACEHOLDER_NAMES) == _ctx_keys,
          "仅常量有=%s 仅上下文有=%s" % (sorted(set(PLACEHOLDER_NAMES) - _ctx_keys),
                                       sorted(_ctx_keys - set(PLACEHOLDER_NAMES))))

    # 7) 日期必须是上海时区（CI runner 是 UTC；凌晨发版会差一天）
    _expected = (datetime.datetime.now(datetime.timezone.utc)
                 + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")
    check("默认日期按 Asia/Shanghai 取", _shanghai_today() == _expected,
          "%s != %s" % (_shanghai_today(), _expected))

    # 8) 端到端：真实模板 + 假数据能渲染出可读正文
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    for rel, channel in (("packaging/RELEASE-NOTES.md", "versioned"),
                         ("packaging/RELEASE-NOTES-installer.md", "installer")):
        path = repo_root / rel
        if not path.is_file():
            check(f"模板存在：{rel}", False, "文件缺失")
            continue
        template = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        _title, cleaned = extract_title(template)
        _args = _Args()
        _args.channel = channel
        _body, _left = render(cleaned, build_context(_args))
        check(f"模板可渲染：{rel}", not _left, repr(_left[:3]))
        check(f"模板无隐私问题：{rel}", check_body(_body) == [])

    if failures:
        print()
        print(f"自测失败：{len(failures)} 项 —— " + "、".join(failures))
        return 1
    print()
    print("自测通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
