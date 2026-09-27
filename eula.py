# -*- coding: utf-8 -*-
"""eula.py — EULA / CHANGELOG 等静态文本文件 IO。

职责范围：
    - api_get_changelog() — 读取仓库根 CHANGELOG.md 并包装成 HTTP 响应
    - read_eula()        — EULA 文件 IO 钩子（当前未启用，留接口）

设计：
    - 与 web_api.py 解耦：文件 IO 错误统一转成 (status_code, payload) 元组
    - 由 web_api.py 在收到 GET /api/changelog 时调用 api_get_changelog()
    - 路径来自 BASE_DIR，与原版保持一致
"""

import os

from version import VERSION


# ============================================================
# 共享路径（由 联网_service.py 在 import 时注入 BASE_DIR）
# ============================================================
_BASE_DIR = None


def _attach(*, base_dir):
    """由 联网_service.py 调用，注入 BASE_DIR。

    解耦目标：eula.py 不直接 import 联网_service（避免循环）。
    """
    global _BASE_DIR
    _BASE_DIR = base_dir


# ============================================================
# CHANGELOG
# ============================================================
# 仓库在线版（本地文件缺失时给用户一个可点的去处，而不是裸 Errno 报错）
CHANGELOG_GITHUB_URL = (
    "https://github.com/TSS-Small-sunshine/StardustFlashLink/blob/main/CHANGELOG.md"
)


def _changelog_candidates():
    """按优先级返回可能的 CHANGELOG.md 路径（都是绝对路径）。

    为什么要有多个候选：v2.0.4.0 之前的安装包**没有**把 CHANGELOG.md 打进去，
    真机上点「关于 → 查看更新日志」必然弹
    `read changelog failed: [Errno 2] No such file or directory`。
    除了在安装包里补上该文件（`packaging/setup.iss`），这里也按
    「安装目录 → 安装目录\\docs → 上一级目录」逐个探测，兼容各种分发形态。
    """
    if _BASE_DIR is None:
        return []
    base = os.path.normpath(_BASE_DIR)
    return [
        os.path.join(base, "CHANGELOG.md"),              # 正常安装 / 源码运行
        os.path.join(base, "docs", "CHANGELOG.md"),      # 若将来挪进 docs\
        os.path.normpath(os.path.join(base, "..", "CHANGELOG.md")),  # 从子目录运行
    ]


def api_get_changelog():
    """GET /api/changelog — 返回 CHANGELOG.md 内容（UTF-8 文本）。

    路径说明：CHANGELOG.md 与 联网_service.py 同在仓库根目录（即 BASE_DIR）。
    安装场景下 Inno Setup 把 CHANGELOG.md 复制到 {app}（与 联网_service.py 同级），
    所以生产环境也是 BASE_DIR/CHANGELOG.md，与开发环境一致。

    返回 (status_code, dict) 元组 — 便于 web_api.py 直接转发。
    找不到文件时返回**中文可读提示 + url 字段**（安装包漏打包时用户能自助），
    但绝不把裸路径拼进报错以外的任何用户可见响应。
    """
    if _BASE_DIR is None:
        return 500, {"error": "eula module not attached", "content": ""}
    candidates = _changelog_candidates()
    for cl_path in candidates:
        if not os.path.isfile(cl_path):
            continue
        try:
            with open(cl_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            # 权限 / 占用等 OS 级错误
            return 500, {
                "error": "读取更新日志失败：{}".format(exc.strerror or exc),
                "content": "",
                "url": CHANGELOG_GITHUB_URL,
            }
        return 200, {
            "content": content,
            "size": len(content),
            "version": VERSION,
            "path": cl_path,
        }
    return 500, {
        "error": ("本机没有更新日志文件（CHANGELOG.md 未随安装包分发）。"
                  "可在仓库查看完整更新日志。"),
        "content": "",
        "url": CHANGELOG_GITHUB_URL,
    }



# ============================================================
# EULA（预留接口；当前未挂到 HTTP 路由）
# ============================================================
def read_eula():
    """读取 BASE_DIR/EULA.md（如存在）。文件不存在 → 返回 None。

    后续如需在 Web UI 展示 EULA，可在 web_api.py 加 /api/eula 路由
    直接 return read_eula()。
    """
    if _BASE_DIR is None:
        return None
    eula_path = os.path.join(_BASE_DIR, "EULA.md")
    eula_path = os.path.normpath(eula_path)
    if not os.path.isfile(eula_path):
        return None
    try:
        with open(eula_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None