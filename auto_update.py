# -*- coding: utf-8 -*-
"""auto_update.py — 自动升级（v1.3 新增）业务层。

职责范围：
    - GitHub releases/latest 探测（主源 + 镜像 fallback）
    - 安装器下载（主源 + 镜像 fallback + 进度回调）
    - SHA256 校验 + 备份当前服务脚本
    - NSSM 注册表操作（备份 AppExit 等透明升级策略）
    - 后台线程 _auto_update_loop（按 cfg.update_check_interval_hours 周期检查）
    - 升级执行器 / 看门狗 _build_update_wrapper / _build_health_probe_script
      （v2.0.10.0：收尾判定从「nssm wrapper 活着」改成「探 HTTP /api/health 能答」）
    - 启动钩子 _post_upgrade_startup（升级完成后清理）
    - 自动升级状态机 _set_update_state / _acquire_update_lock / _release_update_lock

设计：
    - 不直接持有 STATE / LOCK；通过 _attach() 由 联网_service.py 在 main() 注入
    - 共享字典 / 锁 / 函数 / 路径常量挂到本模块 globals，原函数体裸名引用无须重写

依赖：仅 Python 3 标准库。
"""

import datetime as _dt  # noqa: F401  备用 datetime 别名
import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading   # 审计 G1：启动钩子的延迟复核线程（daemon）
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from version import VERSION

# 网络层可重试的错误集合（v2.x 维护 / 审计 U-1）：
# http.client.IncompleteRead / BadStatusLine / RemoteDisconnected 都是 HTTPException，
# **不是 OSError** —— 以前三个 except 都没收它们，断流时会穿出去：
# 镜像不回退、半截 .part 不清理，后台自动升级线程还会被异常直接打死。
_URL_ERRORS = (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException)


# ============================================================
# 升级常量（从 联网_service.py 逐字搬入）
# ============================================================
GITHUB_REPO = "TSS-Small-sunshine/StardustFlashLink"
GITHUB_RELEASES_API = "https://api.github.com/repos/{}/releases/latest".format(GITHUB_REPO)
GITHUB_API_VERSION = "2022-11-28"
GITHUB_UA = "DrcomAutoLogin-Windows/{}".format(VERSION)
HTTP_TIMEOUT_SEC = 8
DOWNLOAD_CHUNK_BYTES = 64 * 1024  # 64KB
DOWNLOAD_TIMEOUT_SEC = 300  # 5 分钟
# 审计 E4 / 不变量 I9：安装包产物名以 setup.iss 的 OutputBaseFilename 为准
#   （StardustFlashLink-Setup-v{ver}.exe）；以前这里写的是旧产品名 DrcomAutoLogin-Setup-v*。
# 只影响 %TEMP% 暂存名与 _cleanup_update_leftovers 的匹配 —— 两处必须**一起**改。
INSTALLER_FILENAME_PATTERN = "StardustFlashLink-Setup-v{ver}.exe"
INSTALLER_FILENAME_PREFIX = "StardustFlashLink-Setup-v"
# v2.0.3.0 之前的产物名：只用于清理历史残留（不参与本次下载的命名）
LEGACY_INSTALLER_FILENAME_PREFIX = "DrcomAutoLogin-Setup-v"
NSSM_REGISTRY_PATH = r"HKLM\SYSTEM\CurrentControlSet\Services\DrcomAutoLogin"
NSSM_PARAMETERS_PATH = NSSM_REGISTRY_PATH + r"\Parameters"
SERVICE_NAME = "DrcomAutoLogin"
UPGRADE_HISTORY_MAX_LINES = 50
# v2.0.12.0：升级日志轮转（以前无限追加；真机 9 天 533 KB，而「查看升级历史」只读尾部）
UPGRADE_LOG_MAX_BYTES = 2 * 1024 * 1024
UPGRADE_LOG_BACKUPS = 2
UPGRADE_SUCCESS_TTL_SEC = 5 * 60  # 成功后绿 banner 仅保留 5 分钟
BACKUP_RETENTION_DAYS = 7

# ============================================================
# v2.0.4.2：自动升级执行器（任务计划程序 + .cmd 包装脚本）
#
# 为什么不能再用 DETACHED_PROCESS 直启 installer：
#   setup.iss 的 CurStepChanged(ssInstall) 会 `nssm stop DrcomAutoLogin`；
#   nssm 关闭自己的 Job Object 时会把**同 Job 的子进程一起杀掉** ——
#   直启的 installer 会在复制文件前就消失。真机实测（v2.0.4.1 → 2.0.4.1 那次）：
#   installer-silent.log 压根没生成、版本号不变、服务还停在 StopPending。
#   同一条命令行由**不在该 Job 里**的进程拉起 → 7.1 秒装完。
# 于是改成：写一个 .cmd 包装脚本，注册成「一次性 + SYSTEM」计划任务再 /run。
#   任务由 Task Scheduler 服务托管 → 与 nssm 无 Job 关系 → 服务被停也不连坐。
#   包装脚本负责：跑 installer → 落盘退出码 → 看门狗拉起服务 → 收尾自删。
#
# v2.0.10.0 给包装脚本补了第二只看门狗（两个真机事故的根因，见 CHANGELOG v2.0.9.1）：
#   `sc query` 说的是 nssm **wrapper** 的状态，而 wrapper 活着 ≠ 里面的 Python 进程活着
#   （nssm 配的是 AppExit=Ignore：app 崩了它既不重启也不报错）—— 于是 v2.0.8.0
#   （安装器回滚）与 v2.0.9.0（服务启动即崩）都是「升完才发现」，期间 Web UI 整个消失。
#   现在判据落在 HTTP 层：能 GET 到 /api/health 且响应 {"ok": true} 才算「服务真的活了」；
#   探不到就自动「停 + 起」重试一次，并把结论写进 %TEMP%\drcom_apply_update.rc（机器读）
#   与 logs\upgrade.log（人读 —— 服务真起不来时那是唯一的告警面）。
# ============================================================
UPDATE_TASK_NAME = "DrcomAutoLogin-AutoUpdate"
UPDATE_WRAPPER_NAME = "drcom_apply_update.cmd"
UPDATE_RC_NAME = "drcom_apply_update.rc"
UPDATE_LOG_NAME = "drcom-installer-silent.log"
UPDATE_LEFTOVER_STALE_SEC = 5 * 60  # 只清"陈旧"的执行器残留（正在跑的那次绝不碰）
# 审计 J8：%TEMP% 里**旧版本**的安装包（换目标版本重试后留下的 12 MB 包）超过这个年龄才清，
# 免得删掉本轮刚下载完、还没装上 / 正被安装器占用着的包（正在使用的删不掉，也一律不动）。
OLD_INSTALLER_MAX_AGE_SEC = 24 * 3600
SCHTASKS_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "schtasks.exe")
SC_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "sc.exe")

# ---- v2.0.10.0：升级看门狗 B —— 探 HTTP /api/health ----
# 判据：能 GET 到 http://127.0.0.1:<ui_port>/api/health 且响应里 ok 为真。
# 探针脚本由本模块生成到 %TEMP%，用**随包发的内嵌 python** 跑（不依赖用户装没装 Python、
# 不依赖 PATH），且不 import 任何项目模块 —— 被升级搞坏的正是那些模块，探针要更「耐活」。
UPDATE_PROBE_NAME = "drcom_health_probe.py"
HEALTH_PROBE_HOST = "127.0.0.1"
HEALTH_PROBE_PATH = "/api/health"
HEALTH_PROBE_DEFAULT_PORT = 8848
HEALTH_PROBE_WAIT_SEC = 20          # 首次探测的等待窗口（冷启动：读配置 / 加载密码 / 绑端口）
HEALTH_PROBE_RETRY_WAIT_SEC = 12    # 「停 + 起」重启之后再给的窗口
HEALTH_PROBE_INTERVAL_SEC = 1.0
HEALTH_PROBE_TIMEOUT_SEC = 2.0
# 随包分发的内嵌解释器（setup.iss 把 python\ 整个拷进 {app}\python）
EMBEDDED_PYTHON_REL = os.path.join("python", "python.exe")

# ---- v2.0.11.0（P6-6 / T4.4）：升级备份与回滚 ----
# 背景（v2.0.10.0 的看门狗只解决了「发现」）：升级把服务弄挂了以后，用户手上没有退路 ——
# 旧实现 `_backup_service_py` 只复制 `联网_service.py` **一个**文件（v2.0.2 拆成 9 个模块后
# 就不够了 ✗），而且放在 %TEMP%（会被磁盘清理删掉 ✗）。
# 现在：
#   1) 升级前把「随包分发的代码文件」整目录备份到 `{app}\backup\<旧版本>\` + manifest.json
#      （文件清单 + 逐个 sha256 + 时间）—— 安装目录不归 Inno 管，升级不会动它 ✓；
#   2) 执行器在「探测两轮都不健康」时**自动回滚**：停服务 → 校验并还原这些文件 → 起服务 → 再探；
#   3) Web UI 也能手动回滚（同一份回滚脚本 —— 手测到的就是自动会跑的那条路径 ✓）。
# 边界：备份里**也存一份 config.json 仅作人工参考**，但回滚**只还原代码文件**，
#       **绝不碰 config.json / password.txt**（不变量 I6 / I8：升级与回滚都不该动用户数据）。
UPDATE_ROLLBACK_NAME = "drcom_rollback.py"
BACKUP_DIR_NAME = "backup"                 # {app}\backup\<版本>\
BACKUP_MANIFEST_NAME = "manifest.json"
BACKUP_NOTE_MAX = 120
BACKUP_KEEP_MIN = 2                        # 无论多旧，至少留最新 2 份备份
# 审计 D1：备份目录先建目录 + 拷文件、最后才写 manifest；中途失败会留下「有目录、无 manifest」
# 的半成品。它进不了 _list_backups，于是 _prune_backups 以前永远看不见。清理它时必须只删
# 「manifest 缺失且 mtime 超过 1 天」的目录 —— 正在备份的那一份 mtime 是新的，绝不能被误删。
BACKUP_ORPHAN_MIN_AGE_SEC = 24 * 3600
ROLLBACK_MODULES = (
    "联网_service.py",
    "version.py",
    "protocol.py",
    "web_api.py",
    "eula.py",
    "auto_update.py",
    "metrics.py",      # 可选模块（缺文件不致命，但装了就要能回滚）
    "profiles.py",
    "tray.py",
)
# 备份目录名 = 版本号 → 必须过白名单（防 `..\` 穿越到别的目录去 ✗）
ROLLBACK_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$")
# nssm 的 AppExit 是**子键 + 子值**：Default 存在子键的 (默认) 值里，0 存在名为 "0" 的值里
APPEXIT_SUBKEY = NSSM_PARAMETERS_PATH + r"\AppExit"
APPEXIT_DEFAULT_VALUE = ""
# winreg 的键路径**不能带 "HKLM\\" 前缀**（HKLM 是通过 HKEY_LOCAL_MACHINE 常量传的）。
# v2.0.4.3 修：v2.0.4.0～v2.0.4.2 一直把 "HKLM\\SYSTEM\\..." 原样传给 winreg.OpenKey →
# FileNotFoundError 被 `except OSError: return None` 吞掉 → `_read_nssm_appexit()` 永远返回 None
# → 每次开机误报「AppExit 自愈失败（仍是空值）」。
_HKLM_PREFIX = "HKLM\\"


def _hklm_subpath(key_path):
    """把 'HKLM\\SYSTEM\\...' 转成 winreg 需要的 'SYSTEM\\...'（大小写不敏感，幂等）。"""
    if key_path.upper().startswith(_HKLM_PREFIX.upper()):
        return key_path[len(_HKLM_PREFIX):]
    return key_path

# GitHub 镜像（国内加速；首个 None 表示主源，按顺序 fallback）
# 镜像格式：prefix + 原始 URL；原始 URL 必须是 https://... 开头以避免双斜杠。
#
# P1-2：**API 元数据只信主源 api.github.com**。
# 镜像能同时伪造 releases JSON、asset URL 和 digest —— 于是「SHA256 校验」校验的是
# 攻击者提供的值（校验值与被校验对象同源），等于没有校验。下载仍可用镜像，
# 但 digest 一律来自主源结果。
GITHUB_API_MIRRORS = (
    None,                    # 主源 api.github.com（唯一可信的元数据源）
)
GITHUB_DOWNLOAD_MIRRORS = (
    None,                    # 主源 objects.githubusercontent.com / github.com
    "https://gh-proxy.com",
    "https://ghfast.top",
    "https://mirror.ghproxy.com",
)
GITHUB_API_REQUEST_TIMEOUT_SEC = 15  # 镜像 fallback 时单次超时
GITHUB_DOWNLOAD_REQUEST_TIMEOUT_SEC = 60  # 镜像 fallback 时下载单段超时



# ============================================================
# 自动升级函数（从 联网_service.py 逐字搬入）
# ============================================================
# 审计 B1：后缀必须分类 —— 预发布（rc/alpha/beta/pre/dev）要**低于**同版本正式版；
# 只有 fix/hotfix/patch（以及未知后缀，保持旧口径）继续用 sentinel 999。
# 用法：suffix.startswith(_PRERELEASE_SUFFIXES) —— str.startswith 支持元组。
_PRERELEASE_SUFFIXES = ("rc", "alpha", "beta", "pre", "dev")


def _parse_version(s):
    """将 "1.2" / "v1.3.1" / "1.3-fix" / "1.3.1-rc2" 解析为可比较元组。解析失败返回 ()。

    规则:
    - 忽略前缀 'v' / 'V'
    - '.' 分隔数字段;每个数字段必须是纯数字,或数字后带非数字后缀( '-fix' / '_hotfix' / '-rc1' 等)
    - 后缀分两类（审计 B1）:
        * 预发布后缀 rc / alpha / beta / pre / dev → 追加 -1:
          `2.1.3.0-rc1` 与同版本正式版 `2.1.3.0` 比较时**更低**（补 0 后 -1 < 0）；
        * 其它（fix / hotfix / patch 与未知后缀）→ 追加 sentinel 999:
          让补丁版本严格大于同主版本号 (1.3-fix > 1.3)，但又人工高于任意后续小版本号
          ( 1.3-fix > 1.3.1 ) —— 这是既有设计意图，不改；
      遇到后缀后立即停止解析, 防止 "1.3-fix.5" 这类异常输入被错误地拆出更多段
    - 某段完全不包含数字 → 解析失败返回 ()
    """
    if not isinstance(s, str):
        return ()
    s = s.strip().lstrip("v").lstrip("V")
    if not s:
        return ()
    # 全无数字 → 拒绝
    if not any(c.isdigit() for c in s):
        return ()
    out = []
    for part in s.split("."):
        # 同一段内从头取连续数字;遇到第一个非数字即停
        digits = ""
        for c in part:
            if c.isdigit():
                digits += c
            else:
                break
        if not digits:
            return ()  # 该段没数字,解析失败
        out.append(int(digits))
        # 同一段里剩余字符(如 "-fix" / "_hotfix" / "-rc1" 等)→ 按后缀分类追加标记
        if len(part) > len(digits):
            suffix = part[len(digits):].lstrip("-_.").lower()
            if suffix.startswith(_PRERELEASE_SUFFIXES):
                out.append(-1)   # 预发布：低于同版本正式版
            else:
                out.append(999)  # fix / hotfix / patch / 未知后缀：保持旧口径
            # 后续段不再解析(防止 "1.3-fix.5" 被错误解析)
            break
    return tuple(out)


def _compare_versions(local, remote):
    """本地 vs 远程：返回 -1 / 0 / 1；不可比较返回 None。"""
    lv = _parse_version(local)
    rv = _parse_version(remote)
    if not lv or not rv:
        return None
    # 用 (差值列表) 比较
    n = max(len(lv), len(rv))
    lv = lv + (0,) * (n - len(lv))
    rv = rv + (0,) * (n - len(rv))
    if lv < rv:
        return -1
    if lv > rv:
        return 1
    return 0


# 审计 B2：远端版本串白名单（防 `..\` 穿越拼进 %TEMP% 文件名，也防把脏 tag 当版本）。
# 以前只有 _do_update_now 有这道闸，_do_check_now 没有 —— 两处共用本函数。
_REMOTE_VERSION_RE = re.compile(r"[0-9A-Za-z._-]{1,32}")


def _valid_remote_version(version):
    """远端 tag 解析出的版本串是否过白名单（纯函数；_do_update_now / _do_check_now 共用）。"""
    return isinstance(version, str) and _REMOTE_VERSION_RE.fullmatch(version) is not None


def _rotate_upgrade_log():
    """把超限的 upgrade.log 滚成 `.1` / `.2`（v2.0.12.0）。

    只有**当前文件**会被滚走，历史不会丢光：`upgrade.log.1` 是最新的旧档，
    `upgrade.log.2` 更老，再老的就删掉 —— 配合 `UPGRADE_LOG_MAX_BYTES`（2 MB）
    相当于「最多留 6 MB 升级流水」，对「查看升级历史」这种只读尾部的场景绰绰有余 ✓。
    """
    try:
        if os.path.getsize(UPGRADE_LOG_FILE) < UPGRADE_LOG_MAX_BYTES:
            return
    except OSError:
        return
    try:
        oldest = "{}.{}".format(UPGRADE_LOG_FILE, UPGRADE_LOG_BACKUPS)
        if os.path.isfile(oldest):
            os.remove(oldest)
        for i in range(UPGRADE_LOG_BACKUPS - 1, 0, -1):
            src = "{}.{}".format(UPGRADE_LOG_FILE, i)
            if os.path.isfile(src):
                os.replace(src, "{}.{}".format(UPGRADE_LOG_FILE, i + 1))
        os.replace(UPGRADE_LOG_FILE, UPGRADE_LOG_FILE + ".1")
        logger.info("upgrade.log 已轮转（超过 %d 字节）", UPGRADE_LOG_MAX_BYTES)
    except OSError as exc:
        logger.warning("轮转 upgrade.log 失败: %s", exc)


def _ensure_upgrade_log():
    """确保升级日志文件存在（并按需轮转）。每次追加写。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if not os.path.isfile(UPGRADE_LOG_FILE):
            # 原子创建（utf-8 + LF）
            with open(UPGRADE_LOG_FILE, "a", encoding="utf-8") as f:
                pass
        else:
            _rotate_upgrade_log()
    except OSError as exc:
        logger.warning("无法准备 upgrade.log: %s", exc)


def _log_upgrade(level, msg):
    """同时写到 logs/upgrade.log 与主 logger。"""
    _ensure_upgrade_log()
    line = "[{}] [{}] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, msg)
    try:
        with open(UPGRADE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logger.warning("写 upgrade.log 失败: %s", exc)
    if level == "ERROR":
        logger.error("UPGRADE: %s", msg)
    elif level == "WARN":
        logger.warning("UPGRADE: %s", msg)
    else:
        logger.info("UPGRADE: %s", msg)


def _check_disk_free_mb():
    """检查 BASE_DIR 所在磁盘剩余空间（MB），失败返回 None。"""
    try:
        usage = shutil.disk_usage(BASE_DIR)
        return int(usage.free / (1024 * 1024))
    except (OSError, AttributeError):
        return None


def _check_github_latest():
    """依次尝试 GitHub 主源 + 镜像拉 releases/latest API。

    返回 (version, asset_url, digest, size, published_at) 元组；全部失败返回 None。
    digest 形如 "sha256:abcd..."，已剥掉前缀。
    主源超时/失败时 fallback 到下一个镜像。
    """
    primary_url = GITHUB_RELEASES_API
    for mirror in GITHUB_API_MIRRORS:
        target = primary_url if mirror is None else mirror.rstrip("/") + "/" + primary_url
        try:
            req = urllib.request.Request(
                target,
                headers={
                    "User-Agent": GITHUB_UA,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                },
            )
            timeout = HTTP_TIMEOUT_SEC if mirror is None else GITHUB_API_REQUEST_TIMEOUT_SEC
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except _URL_ERRORS as exc:
            logger.warning(
                "GitHub 检查镜像 %s 失败: %s",
                "primary" if mirror is None else mirror,
                exc,
            )
            continue
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("GitHub API JSON 解析失败 (%s): %s", target, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("GitHub API 返回非 dict: %s", target)
            continue
        tag = data.get("tag_name")
        if not isinstance(tag, str) or not tag:
            logger.warning("GitHub API 返回无 tag_name: %s", target)
            continue
        version = tag.strip().lstrip("v").lstrip("V")
        assets = data.get("assets") or []
        asset_url = None
        digest = None
        size = None
        if isinstance(assets, list):
            for a in assets:
                if not isinstance(a, dict):
                    continue
                name = a.get("name") or ""
                if isinstance(name, str) and name.lower().endswith(".exe"):
                    asset_url = a.get("browser_download_url")
                    d = a.get("digest") or ""
                    if isinstance(d, str) and d.startswith("sha256:"):
                        digest = d.split(":", 1)[1]
                    s = a.get("size")
                    if isinstance(s, int):
                        size = s
                    break
        if not asset_url:
            logger.warning("GitHub API 返回无 .exe asset: %s", target)
            continue
        published = data.get("published_at")
        return version, asset_url, digest, size, published
    return None


def _set_update_state(**kwargs):
    """写 STATE 的 update_* 字段。线程安全。"""
    with STATE_LOCK:
        for k, v in kwargs.items():
            STATE[k] = v


def _get_update_field(key):
    with STATE_LOCK:
        return STATE.get(key)


def _acquire_update_lock():
    """原子检查并获取升级锁。返回 True=获得锁；False=已在升级。"""
    with UPDATE_LOCK:
        with STATE_LOCK:
            if STATE.get("update_lock"):
                return False
            STATE["update_lock"] = True
            STATE["update_state"] = "checking"
            STATE["update_progress"] = 0
            STATE["update_progress_message"] = "准备升级..."
            STATE["update_last_error"] = None
            return True


def _release_update_lock():
    with UPDATE_LOCK:
        with STATE_LOCK:
            STATE["update_lock"] = False


# ============================================================
# v2.0.6.3：忙状态查询（Web 层据此判断「现在能不能开始」）
# ============================================================
def is_update_busy():
    """是否有检查 / 下载 / 升级任务正在执行。只读、不阻塞、不改状态。

    只认**升级锁**本身（任务全程持有它），不认 update_state —— 状态可能是上一轮
    异常留下的残值（锁已由 finally 释放），拿状态判「忙」会把接口永久锁死。

    未注入时（单元测试 / 非 Windows 下 import）返回 False：宁可不拦，不误拦。
    """
    lock = globals().get("UPDATE_LOCK")
    state = globals().get("STATE")
    state_lock = globals().get("STATE_LOCK")
    if lock is None or state is None or state_lock is None:
        return False
    with lock:
        with state_lock:
            return bool(state.get("update_lock"))


def update_busy_message():
    """「忙」的可读原因（复用当前进度文案）；空闲时返回空串。

    v2.0.6.3：POST /api/update/check|install 会先问这个。此前两个入口一律先回
    {"ok": true, "已提交..."}，随后后台线程被 _acquire_update_lock 挡掉 ——
    用户看到「已提交」却什么都没发生（真机验证 v2.0.6.2 时踩到：先点「立即检查更新」、
    紧接着点「立即升级」，第二个请求被静默丢弃）。
    """
    if not is_update_busy():
        return ""
    with STATE_LOCK:
        msg = (STATE.get("update_progress_message") or "").strip()
    return msg or "已有升级任务在执行"


def _expected_size_bytes(value):
    """把候选里的 expected_size 归一成非负整数（缺失 / 非法 / <=0 都返回 0，纯函数）。

    审计 C2：expected_size 来自 GitHub releases 资产元数据，可能是 None / 字符串 / 0；
    只有当它给出**正整数**时才做下载后大小核对，缺失时保持旧行为。
    """
    if isinstance(value, bool):
        return 0
    try:
        size = int(value)
    except (TypeError, ValueError):
        return 0
    return size if size > 0 else 0


def _download_installer(url, dest_path, expected_size, progress_callback=None, expected_sha256=None):
    """按顺序尝试主源 + 镜像下载 installer；任一成功即返回字节数。
    所有镜像都失败则抛最后一次异常。

    审计 C1：**每个镜像下完立刻校验 SHA256**。镜像用 200 返回 HTML 错误页 / 限流页
    （校园网里极常见）时，以前要等全部下载完、在 _do_update_now 里才发现校验失败 ——
    主源本来可用却整次失败。现在校验不过就删掉**该镜像**的产物并 continue（换下一个），
    digest 仍只信主源（GITHUB_API_MIRRORS 只剩 None，见 P1-2）。
    没给 expected_sha256 时不校验：fail-closed 的「digest 缺失即拒绝」在 _do_update_now
    里、下载之前就把关。
    审计 C2：expected_size > 0 时**下载后核对实际大小**（截断包 / 限流页），不符 → 删文件、
    记 WARN、换下一个镜像；expected_size 缺失 / 非正数 → 不核对，保持旧行为。

    progress_callback(downloaded_bytes, total_bytes_or_None) 每 ~200ms 触发。
    """
    primary_url = url
    last_exc = None
    for mirror in GITHUB_DOWNLOAD_MIRRORS:
        target = primary_url if mirror is None else mirror.rstrip("/") + "/" + primary_url
        mirror_name = "primary" if mirror is None else mirror
        try:
            written = _do_download_installer(
                target, dest_path, expected_size, progress_callback,
                timeout=DOWNLOAD_TIMEOUT_SEC if mirror is None else GITHUB_DOWNLOAD_REQUEST_TIMEOUT_SEC,
            )
        except _URL_ERRORS as exc:
            logger.warning("下载镜像 %s 失败: %s", mirror_name, exc)
            last_exc = exc
            continue
        # 审计 C2：候选里带了 expected_size（>0 字节）就核对实际大小。
        # 镜像用 200 返回截断的包 / 限流页时，若没有 digest 可依赖（或 digest 校验之前），
        # 大小不符同样说明「下载不完整」→ 删掉该镜像的产物、换下一个（与 SHA256 失败同一路径）。
        # 没有 expected_size 时保持旧行为，不做任何核对。
        expected_bytes = _expected_size_bytes(expected_size)
        if expected_bytes > 0 and written != expected_bytes:
            logger.warning(
                "镜像 %s 下载的安装器大小不符（期望 %d 字节，实际 %d 字节），删除后换下一个镜像",
                mirror_name, expected_bytes, written)
            try:
                if os.path.isfile(dest_path):
                    os.remove(dest_path)
            except OSError as exc:
                logger.warning("删除大小不符的安装器失败: %s", exc)
            last_exc = OSError(
                "镜像 {} 下载的安装器大小不符（期望 {} 字节，实际 {} 字节）".format(
                    mirror_name, expected_bytes, written))
            continue
        if expected_sha256 and not _verify_sha256(dest_path, expected_sha256):
            logger.warning("镜像 %s 下载的安装器 SHA256 不符，删除后换下一个镜像", mirror_name)
            try:
                if os.path.isfile(dest_path):
                    os.remove(dest_path)
            except OSError as exc:
                logger.warning("删除校验失败的安装器失败: %s", exc)
            last_exc = OSError("镜像 {} 下载的安装器 SHA256 校验不符".format(mirror_name))
            continue
        return written
    raise last_exc if last_exc is not None else OSError("所有下载镜像都失败")


def _do_download_installer(url, dest_path, expected_size, progress_callback, timeout):
    """单镜像流式下载（_download_installer 的实际下载实现）。

    成功返回写入字节数；失败抛 (URLError / OSError / TimeoutError)。
    """
    last_report = [0.0]
    last_bytes = [0]

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": GITHUB_UA, "Accept": "application/octet-stream"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total_header = resp.headers.get("Content-Length")
            total = int(total_header) if (total_header and total_header.isdigit()) else None
            tmp = dest_path + ".part"
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    f.write(chunk)
                    last_bytes[0] += len(chunk)
                    now = time.time()
                    if progress_callback and (now - last_report[0] >= 0.2 or last_bytes[0] == total):
                        last_report[0] = now
                        try:
                            progress_callback(last_bytes[0], total or expected_size)
                        except Exception:  # noqa: BLE001
                            pass
            # 原子改名
            os.replace(tmp, dest_path)
            return last_bytes[0]
    except _URL_ERRORS:
        # 清理半成品
        for p in (dest_path + ".part", dest_path):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        raise


def _verify_sha256(path, expected_hex):
    """计算文件 SHA256，对比十六进制串（小写）。返回 bool。"""
    if not expected_hex:
        return False
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK_BYTES), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest().lower() == expected_hex.lower()


def _sha256_hex(path):
    """算文件 sha256（十六进制，小写）；读不了返回 None。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(DOWNLOAD_CHUNK_BYTES), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest().lower()


def _backup_root():
    """备份根目录：{app}\\backup（安装目录不归 Inno 管 → 升级不会动它）。"""
    return os.path.join(BASE_DIR, BACKUP_DIR_NAME)


def _backup_dir_for(version):
    """某个版本的备份目录；版本串非法（可能是穿越尝试）返回 None（纯函数）。"""
    if not isinstance(version, str) or not ROLLBACK_VERSION_RE.match(version):
        return None
    return os.path.join(_backup_root(), version)


def _write_backup_manifest(d, manifest):
    """原子写备份清单（审计 D1）：先写 manifest.json.tmp，再 os.replace 覆盖正式文件。

    只有正式 manifest.json 存在且可解析，_list_backups 才认为这份备份可用；
    所以清单要么完整出现、要么不出现 —— 不会出现「半截 JSON 让备份永久不可见」，
    也不会让 _prune_backups 把刚写完的半成品目录当成「合法备份」处理。
    写失败由调用方（_backup_modules）兜住并中止本次升级（宁可这次不升）。
    """
    path = os.path.join(d, BACKUP_MANIFEST_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _backup_modules(version=None, note=""):
    """把「随包分发的代码文件」整目录备份到 `{app}\\backup\\<版本>\\`，返回备份目录。

    v2.0.11.0（P6-6 / T4.4）替换掉旧的 `_backup_service_py`：
      旧：只复制 `联网_service.py` 一个文件（v2.0.2 拆模块后不够 ✗）+ 落在 %TEMP%（会被清理 ✗）；
      新：`ROLLBACK_MODULES` 全清单 + `manifest.json`（逐个 sha256）→ 回滚脚本据此校验，
          **备份被改坏就拒绝恢复**（fail-closed），不会把半套文件写回去 ✓。
    另外存一份 `config.json` **仅供人工比对**：回滚脚本只还原代码文件，绝不碰配置与密码 ✓。
    缺可选模块（metrics / profiles 没装上）不算失败 ✓。
    """
    ver = version or VERSION
    d = _backup_dir_for(ver)
    if d is None:
        _log_upgrade("ERROR", "备份失败：版本串非法 {!r}".format(ver))
        return None
    files = {}
    try:
        os.makedirs(d, exist_ok=True)
        for name in ROLLBACK_MODULES:
            src = os.path.join(BASE_DIR, name)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(d, name)
            shutil.copy2(src, dst)
            digest = _sha256_hex(dst)
            if digest is None:
                raise OSError("无法校验备份文件 {}".format(name))
            files[name] = digest
        cfg = os.path.join(BASE_DIR, "config.json")
        if os.path.isfile(cfg):
            shutil.copy2(cfg, os.path.join(d, "config.json"))
            files["config.json"] = _sha256_hex(os.path.join(d, "config.json"))
        manifest = {
            "version": ver,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "note": (note or "")[:BACKUP_NOTE_MAX],
            "rollback_files": [n for n in ROLLBACK_MODULES if n in files],
            "files": files,
        }
        # 审计 D1：manifest 是「这份备份可用」的唯一凭据，必须原子落盘 ——
        # 先写 manifest.json.tmp 再 os.replace；中途失败只会留下 .tmp（由 _prune_backups
        # 按「无合法 manifest + 超过 1 天」清理），不会留下半截正式清单。
        _write_backup_manifest(d, manifest)
    except OSError as exc:
        logger.error("备份当前版本失败: %s", exc)
        _log_upgrade("ERROR", "备份到 {} 失败：{}".format(d, exc))
        return None
    _log_upgrade("INFO", "已备份 {} 个文件到 {}（回滚用）".format(len(files), d))
    return d


def _parse_backup_manifest(d):
    """读备份目录的 manifest.json；缺失 / 坏掉返回 None。"""
    try:
        with open(os.path.join(d, BACKUP_MANIFEST_NAME), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def _list_backups():
    """列出可用备份（新的在前）：[{version, created_at, path, files, rollback_files}]。"""
    out = []
    root = _backup_root()
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        d = _backup_dir_for(name)
        if d is None or not os.path.isdir(d):
            continue
        man = _parse_backup_manifest(d)
        if man is None:
            continue
        out.append({
            "version": name,
            "created_at": man.get("created_at") or "",
            "path": d,
            "files": len(man.get("files") or {}),
            "rollback_files": len(man.get("rollback_files") or []),
            "current": name == VERSION,
        })
    out.sort(key=lambda b: b.get("created_at") or "", reverse=True)
    return out


def _prune_backups():
    """清掉过期备份：7 天前的删掉，但**最新的 2 份永远保留**（`BACKUP_KEEP_MIN`）。

    审计 D1：先清理「有目录、却没有合法 manifest」的半成品备份。旧实现最后一步才写
    manifest，备份中途失败（断电 / 杀软 / 磁盘满）就会留下这种目录；_list_backups 跳过它，
    于是 _prune_backups 以前永远看不见、也永远不清理（{app}\\backup 越积越多）。
    清理条件里必须有「mtime 超过 BACKUP_ORPHAN_MIN_AGE_SEC」——**绝不删刚建出来、
    可能正在拷贝文件的目录**，否则会把正在备份的那一份误删。

    审计 D2：先按 keep 过滤出「非最新 N 份」再逐个判断过期。旧代码 keep 只含前 2 份、
    循环又从 backups[2:] 开始，`if b["path"] in keep` 恒为 False，是死代码。
    """
    root = _backup_root()
    # —— 1) 半成品目录（目录在、没有合法 manifest）——
    valid = {os.path.normcase(b["path"]) for b in _list_backups()}
    cutoff_orphan = time.time() - BACKUP_ORPHAN_MIN_AGE_SEC
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    for name in names:
        d = _backup_dir_for(name)
        if d is None or not os.path.isdir(d) or os.path.normcase(d) in valid:
            continue
        try:
            mtime = os.path.getmtime(d)
            if mtime >= cutoff_orphan:
                continue  # mtime 新 = 可能正在备份，绝不动
            age_days = max(1, int((time.time() - mtime) / 86400))
            shutil.rmtree(d, ignore_errors=False)
            _log_upgrade("INFO", "清理半成品备份目录：{}（没有合法 manifest，已存在约 {} 天）".format(
                name, age_days))
        except OSError as exc:
            logger.warning("清理半成品备份目录 %s 失败: %s", d, exc)

    # —— 2) 过期备份：至少留最新 BACKUP_KEEP_MIN 份，其余按保留期（7 天）删 ——
    backups = _list_backups()
    keep = {os.path.normcase(b["path"]) for b in backups[:BACKUP_KEEP_MIN]}
    candidates = [b for b in backups if os.path.normcase(b["path"]) not in keep]
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
    for b in candidates:
        try:
            if os.path.getmtime(b["path"]) >= cutoff:
                continue
            shutil.rmtree(b["path"], ignore_errors=False)
            _log_upgrade("INFO", "清理过期备份：v{}（{}）".format(b["version"], b["created_at"]))
        except OSError as exc:
            logger.warning("清理备份 %s 失败: %s", b["path"], exc)


# 回滚脚本模板（自包含、只用标准库、不 import 任何项目模块 —— 被升级搞坏的正是那些模块）。
# 用 .replace 填参数（脚本里有 % 与花括号，别用 %-格式化 / .format）。
_ROLLBACK_TEMPLATE = '''# -*- coding: utf-8 -*-
"""drcom_rollback.py — 把「升级前备份的代码文件」还原回去（v2.0.11.0）。

由 auto_update.py 生成到 %TEMP%，两种调用者共用**同一份**脚本：
  1. 升级执行器（.cmd）：探两轮都探不到 /api/health 时自动回滚；
  2. Web UI 的「回滚」按钮（服务自己起一个子进程跑它，再重启服务）。
共用同一份的意义：**手测到的就是自动会跑的那条路径**。

它只碰 manifest 里 `rollback_files` 列出的**代码文件** ——
`config.json` / 密码文件 / 日志 / 备份目录本身都不动。

安全（fail-closed，三个都过才动手）：
  1. manifest.json 必须能读、且 `rollback_files` 里全是**裸 .py 文件名**（不许路径分隔符 ✗）；
  2. 备份目录里每个文件的 sha256 必须与 manifest 记录一致（备份被改坏就拒绝 ✗）；
  3. `--expect` 给的版本必须与 manifest 的版本一致（防拿错备份 ✗）。
全部校验通过之后才逐个复制，并把复制结果再校验一遍 sha256。

退出码：0 = 已回滚；1 = 没回滚（原因写在日志与 rc 里）。
"""
import hashlib
import json
import os
import shutil
import sys
import time

try:  # 控制台编码兜底
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass

DEFAULT_BACKUP = __RB_BACKUP__
DEFAULT_APP = __RB_APP__
DEFAULT_RC = __RB_RC__
DEFAULT_LOG = __RB_LOG__


def _parse_args(argv):
    opts = {"backup": DEFAULT_BACKUP, "app": DEFAULT_APP, "rc": DEFAULT_RC,
            "log": DEFAULT_LOG, "expect": "", "tag": "manual"}
    i = 0
    while i + 1 < len(argv):
        key, val = argv[i], argv[i + 1]
        i += 2
        if key in ("--backup", "--app", "--rc", "--log", "--expect", "--tag"):
            opts[key[2:]] = val
    return opts


def _sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(256 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest().lower()


def _append(path, line):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\\n")
    except OSError:
        pass


def _safe_name(name):
    """裸文件名白名单：不许目录分隔符 / 盘符 / 上跳（防写到 app 目录外面去）。"""
    if not name or name in (".", ".."):
        return False
    for ch in ("/", "\\\\", ":", "*", "?", '"', "<", ">", "|"):
        if ch in name:
            return False
    return name.endswith(".py")


def main(argv):
    o = _parse_args(argv)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    tag = o["tag"]

    def fail(reason):
        _append(o["rc"], "rollback=FAIL")
        _append(o["log"], "[%s] [ERROR] 版本回滚（%s）：未执行 —— %s" % (stamp, tag, reason))
        print("rollback=FAIL %s" % reason)
        return 1

    man_path = os.path.join(o["backup"], "manifest.json")
    try:
        with open(man_path, "r", encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError) as exc:
        return fail("读不了备份清单 %s（%s）" % (man_path, exc))
    if not isinstance(man, dict) or not isinstance(man.get("files"), dict):
        return fail("备份清单结构不对：%s" % man_path)
    version = str(man.get("version") or "")
    if o["expect"] and version != o["expect"]:
        return fail("备份版本是 %s，但期望 %s（拿错备份？）" % (version or "?", o["expect"]))
    names = man.get("rollback_files") or []
    if not names:
        return fail("备份清单里没有可回滚的代码文件")
    bad = [n for n in names if not _safe_name(n)]
    if bad:
        return fail("备份清单里有非法文件名：%s" % bad)
    if not os.path.isdir(o["app"]):
        return fail("安装目录不存在：%s" % o["app"])

    for n in names:                      # 先全量校验，再动手（all-or-nothing）
        src = os.path.join(o["backup"], n)
        if not os.path.isfile(src):
            return fail("备份里缺文件：%s" % n)
        if _sha256(src) != (man["files"].get(n) or "").lower():
            return fail("备份文件被改动过：%s（sha256 不符）" % n)

    done = []
    for n in names:
        src = os.path.join(o["backup"], n)
        dst = os.path.join(o["app"], n)
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            return fail("还原 %s 失败：%s" % (n, exc))
        if _sha256(dst) != (man["files"].get(n) or "").lower():
            return fail("还原后校验失败：%s" % n)
        done.append(n)

    _append(o["rc"], "rollback=OK")
    _append(o["rc"], "rollback_version=%s" % version)
    _append(o["log"], "[%s] [WARN] 版本回滚（%s）：已还原 v%s 的 %d 个代码文件到 %s"
            "（配置与密码未改动）" % (stamp, tag, version or "?", len(done), o["app"]))
    print("rollback=OK version=%s files=%d" % (version or "?", len(done)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def _build_rollback_script(backup_dir, app_dir, rc_path, log_path):
    """生成回滚脚本内容（纯函数，便于单测 / 行为级冒烟）。"""
    return (_ROLLBACK_TEMPLATE
            .replace("__RB_BACKUP__", repr(backup_dir or ""))
            .replace("__RB_APP__", repr(app_dir or ""))
            .replace("__RB_RC__", repr(rc_path or ""))
            .replace("__RB_LOG__", repr(log_path or "")))


def _rollback_script_path():
    """回滚脚本路径（%TEMP%，与执行器同级；执行器跑完会删掉它）。"""
    return os.path.join(_update_tmp_dir(), UPDATE_ROLLBACK_NAME)


def _write_rollback_script(backup_dir=None, rc_path=None, raise_on_invalid=False):
    """把回滚脚本落盘（自动回滚与手动回滚共用同一份），返回路径；失败返回 None。

    审计 D5：落盘后立刻用内嵌 Python（缺内嵌时退回当前解释器）跑 py_compile 校验语法。
    回滚脚本是「升级升坏了」时唯一的退路，模板改坏以前只有第一次真回滚才发现 ——
    那时往往正是最需要它工作、最没有人工介入机会的时刻，必须提前校验。

    校验不过的处理：
      - raise_on_invalid=False（手动回滚等调用）：删掉坏脚本、记 ERROR、返回 None；
      - raise_on_invalid=True（升级路径）：抛 _GeneratedScriptError 中止升级 ——
        绝不能「降级成 rollback=SKIP 继续装」，那等于在没有退路的情况下换代码。
    """
    path = _rollback_script_path()
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_build_rollback_script(
                backup_dir or os.path.join(_backup_root(), "<version>"),
                BASE_DIR,
                rc_path if rc_path is not None else _rc_path(),
                UPGRADE_LOG_FILE,
            ))
    except OSError as exc:
        logger.warning("写回滚脚本失败: %s", exc)
        return None
    ok, detail = _py_compile_script(path, "回滚脚本")
    if not ok:
        _log_upgrade("ERROR", "回滚脚本生成后语法校验失败：{}（{}）".format(
            detail, "本次升级中止" if raise_on_invalid else "已删除该脚本"))
        try:
            os.remove(path)
        except OSError:
            pass
        if raise_on_invalid:
            raise _GeneratedScriptError(detail)
        return None
    return path


def _run_rollback(backup_dir, expect=None, tag="manual"):
    """跑一次回滚（子进程，优先用**随包内嵌**解释器，缺内嵌时退回当前解释器）。

    审计 D3：旧实现注释写「随包内嵌解释器」、代码却用 sys.executable —— 服务被 NSSM 用
    {app}\\python\\python.exe 启动时两者等价，但换成 pythonw.exe 后 subprocess 拿不到输出，
    回滚「失败」的原因就查不出来了。现在统一走 _upgrade_python_executable()。

    返回 (ok, detail)。
    """
    rc_path = os.path.join(_update_tmp_dir(), "drcom_rollback.rc")
    path = _write_rollback_script(backup_dir=backup_dir, rc_path=rc_path)
    if path is None:
        return False, "无法写回滚脚本（写盘失败或脚本语法校验未通过）"
    try:
        if os.path.isfile(rc_path):
            os.remove(rc_path)
    except OSError:
        pass
    args = [_upgrade_python_executable(), path, "--backup", backup_dir, "--app", BASE_DIR,
            "--rc", rc_path, "--log", UPGRADE_LOG_FILE, "--tag", tag]
    if expect:
        args += ["--expect", str(expect)]
    try:
        proc = subprocess.run(args, timeout=180, check=False,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "回滚脚本执行失败：{}".format(exc)
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    detail = out.splitlines()[-1] if out else "无输出"
    return proc.returncode == 0, detail


def _read_nssm_appexit(subvalue=APPEXIT_DEFAULT_VALUE):
    """读 nssm 某个 AppExit 子值；不存在 / 为空 / 权限不足返回 None。

    v2.0.4.2 修：nssm 的存储结构是**子键 + 子值** ——

        HKLM\\SYSTEM\\CurrentControlSet\\Services\\<svc>\\Parameters\\AppExit
            (默认)  REG_SZ  Ignore     ← nssm set <svc> AppExit Default Ignore
            0       REG_SZ  Ignore     ← nssm set <svc> AppExit 0 Ignore

    v2.0.4.1 及以前读的是 `Parameters` 下的**同名值** `AppExit` —— 那儿根本没有这个值，
    于是每次启动都误报「AppExit 自愈失败（仍是空值）」，并且白白往 `Parameters`
    写了一个 nssm 不认的非法值。真机日志里那行 WARN 就是这么来的。

    审计 E2：subvalue 默认读 (默认) 值（沿用既有调用点），传 "0" 读退出码 0 的子值 ——
    I7 要求 Default 与 "0" **两个都是** Ignore，只读一个等于只兜住一半。
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _hklm_subpath(APPEXIT_SUBKEY)) as k:
            value, _ = winreg.QueryValueEx(k, subvalue)
    except (OSError, ImportError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _appexit_value_ok(value):
    """AppExit 子值是否是 nssm 合法值 Ignore（大小写不敏感；None / 空值都算不合规）。"""
    return isinstance(value, str) and value.strip().lower() == "ignore"


def _write_nssm_appexit(value):
    """直写 AppExit 子键的 (默认) 值（仅作为 nssm.exe 不可用时的兜底）。失败抛 OSError。"""
    if not isinstance(value, str) or not value.strip():
        # 绝不允许把空值写进注册表：NSSM 会因此报错并失去退出策略
        raise ValueError("AppExit 不能为空")
    import winreg
    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, _hklm_subpath(APPEXIT_SUBKEY)) as k:
        winreg.SetValueEx(k, APPEXIT_DEFAULT_VALUE, 0, winreg.REG_SZ, value.strip())


def _set_nssm_appexit(subparam, value):
    """设 AppExit 的某个子参数：`nssm set <svc> AppExit <subparam> <value>`。

    v2.0.4.2 修：subparam 与 value 必须是**两个独立 argv**。此前把 "Default Ignore"
    拼成单个参数传进去，nssm 会把整串当成子参数名 → 写进去的策略是错的（且返回码仍是 0，
    静默失败）。subparam 取 `Default` / `0`（与 setup.iss RegisterService 一致）。
    """
    if not isinstance(subparam, str) or not subparam.strip():
        return False
    if not isinstance(value, str) or not value.strip():
        return False
    if os.path.isfile(NSSM_PATH):
        try:
            proc = subprocess.run(
                [NSSM_PATH, "set", SERVICE_NAME, "AppExit", subparam.strip(), value.strip()],
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                return True
            logger.warning("nssm set AppExit %s %s 返回码 %s", subparam, value, proc.returncode)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("nssm set AppExit 调用失败: %s", exc)
    else:
        logger.warning("nssm.exe 不在 %s，改用注册表直写", NSSM_PATH)
    if subparam.strip().lower() != "default":
        return False  # 注册表兜底只能写 (默认) 值，子参数 0 罕见，跳过
    try:
        _write_nssm_appexit(value)
        return True
    except (OSError, ImportError, ValueError) as exc:
        logger.warning("注册表直写 AppExit 失败: %s", exc)
        return False


def _ensure_nssm_appexit_sane():
    """AppExit 必须是 Default Ignore + 0 Ignore，否则用 nssm set 纠偏（审计 E2 / 不变量 I7）。

    以前只认「空值」：Default 被旧版本 / 手工 / 第三方脚本设成 Restart 时不会纠偏，
    名为 "0" 的子值只写不读 —— I7（端口冲突时 NSSM 不得死循环重启）其实只兜住一半。
    现在判据是「两个子值都是 Ignore」，**绝不写 Restart**。

    返回当前（修复后）的 Default 值，失败返回 None。
    """
    cur_default = _read_nssm_appexit()
    cur_zero = _read_nssm_appexit("0")
    if _appexit_value_ok(cur_default) and _appexit_value_ok(cur_zero):
        # 已合规（Default Ignore + 0 Ignore）→ 什么都不做，也**不该**报"自愈失败"（v2.0.4.2 修的误报）
        return cur_default
    if not os.path.isfile(NSSM_PATH):
        _log_upgrade("WARN", "AppExit 不是 Default Ignore / 0 Ignore（现值 Default={!r} / 0={!r}）"
                             "且找不到 nssm.exe，跳过自愈".format(cur_default, cur_zero))
        return None
    ok_default = _set_nssm_appexit("Default", "Ignore")
    ok_zero = _set_nssm_appexit("0", "Ignore")
    fixed_default = _read_nssm_appexit()
    fixed_zero = _read_nssm_appexit("0")
    if _appexit_value_ok(fixed_default) and _appexit_value_ok(fixed_zero):
        _log_upgrade("INFO", "AppExit 自愈完成：此前 Default={!r} / 0={!r} → 现为 Default Ignore / 0 Ignore"
                             "（nssm set 结果 Default={} / 0={}）".format(
                                 cur_default, cur_zero, ok_default, ok_zero))
    else:
        _log_upgrade("WARN", "AppExit 自愈失败（现值 Default={!r} / 0={!r}），请手动执行："
                             "nssm set {} AppExit Default Ignore".format(
                                 fixed_default, fixed_zero, SERVICE_NAME))
    return fixed_default if _appexit_value_ok(fixed_default) else None


# ============================================================
# 托盘自启项自愈（v2.0.6.2）
#
# 为什么非得在服务里做：**新任务没法靠旧版本传** —— 自动升级的执行器是上一个版本自己的
# auto_update.py，v2.0.4.4 只会传 `/TASKS=desktopicon,startservice`；升级到 v2.0.6.x 时
# Inno 就把新加的 `trayicon` 当成"未选中"，`Tasks: trayicon` 的 Run 项根本不会写。
# 真机实测：从 2.0.4.4 升到 2.0.6.1 后，HKLM\...\Run 里没有 DrcomAutoLoginTray。
# 服务以 SYSTEM 运行、有管理员权限，于是每次启动对齐一次 —— 任何升级路径都能自愈。
# ============================================================
TRAY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TRAY_RUN_VALUE = "DrcomAutoLoginTray"
TRAY_PREF_KEY = r"SOFTWARE\DrcomAutoLogin"
TRAY_PREF_VALUE = "TrayAutostart"


def tray_autostart_action(want, script_exists, run_value, expected):
    """纯函数：算出「托盘自启项」该怎么对齐（便于单测，不碰注册表）。

    - ``want``          安装器记下的开关（读不到时按 True 处理 = 默认开）
    - ``script_exists`` `{app}\\tray.py` 在不在
    - ``run_value``     注册表现值（None = 不存在）
    - ``expected``      期望的命令行
    返回 ``"create"`` / ``"delete"`` / ``"keep"``。
    """
    if not want or not script_exists:
        return "keep" if run_value is None else "delete"
    return "keep" if run_value == expected else "create"


def _tray_autostart_pref():
    """读安装器写下的开关（HKLM\\SOFTWARE\\DrcomAutoLogin\\TrayAutostart）。读不到 → True。"""
    try:
        import winreg
    except ImportError:            # 与其它注册表代码同一套路：非 Windows 上也能 import 本模块
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, TRAY_PREF_KEY) as key:
            return bool(winreg.QueryValueEx(key, TRAY_PREF_VALUE)[0])
    except OSError:
        return True


def _tray_autostart_current():
    """读 HKLM Run 里托盘的现值（None = 不存在）。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, TRAY_RUN_KEY) as key:
            return winreg.QueryValueEx(key, TRAY_RUN_VALUE)[0]
    except OSError:
        return None


def _ensure_tray_autostart_sane(dry_run=False):
    """把「托盘随登录启动」项与安装器记下的开关对齐。返回 dict；``dry_run`` 只算不动手。"""
    script = os.path.join(BASE_DIR, "tray.py")
    exe = os.path.join(BASE_DIR, "python", "pythonw.exe")
    want = _tray_autostart_pref()
    expected = '"{}" "{}"'.format(exe, script)
    current = _tray_autostart_current()
    action = tray_autostart_action(want, os.path.isfile(script), current, expected)
    info = {"action": action, "want": want, "current": current, "expected": expected}
    if action == "keep" or dry_run:
        return info
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, TRAY_RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            if action == "create":
                winreg.SetValueEx(key, TRAY_RUN_VALUE, 0, winreg.REG_SZ, expected)
            else:
                winreg.DeleteValue(key, TRAY_RUN_VALUE)
    except (OSError, ImportError) as exc:
        info["error"] = str(exc)
        _log_upgrade("WARN", "托盘自启项对齐失败（{}）：{}".format(action, exc))
        return info
    _log_upgrade("INFO", "托盘自启项已{}（此前：{}）".format(
        "写入" if action == "create" else "移除", current or "不存在"))
    return info


# ============================================================
# 静默升级后跨会话拉起托盘（v2.1.3.7 / 审计 C-16、改进项 I-8）
#
# 背景（真机证据）：升级前安装器 PrepareToInstall 一定 KillTray()，而 setup.iss 的
# [Run] 起托盘那条带 skipifsilent —— 自动升级（schtasks /ru SYSTEM 静默安装）结束后
# 没有任何人把托盘拉回来。本机 %LOCALAPPDATA%\DrcomAutoLogin\tray.log 显示
# 2026-11-17 15:54:29 之后托盘就没再写日志，直到 2026-11-18 13:25:47 下一次登录才启动
# —— 中间约 21.5 小时没有断线气泡 / 状态图标 / 唤醒重连。
#
# 修法：服务以 LocalSystem 跑在 session 0，拿**当前活动控制台会话**的用户令牌，
# 用 CreateProcessAsUserW 把托盘拉进用户桌面（winsta0\default）。只在「本次启动确认刚
# 完成一次自动升级」时做（见 _post_upgrade_startup 的 just_upgraded），绝不放进每次
# 服务启动的路径 —— 用户从托盘菜单主动退出是产品行为，服务不能把它复活（否则关不掉）。
# ============================================================
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002
ERROR_NOT_ALL_ASSIGNED = 1300
MAXIMUM_ALLOWED = 0x02000000
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1


def tray_relaunch_command(pythonw_path, script_path):
    """纯函数：托盘命令行 = 双引号包裹的 pythonw + tray.py。

    与 setup.iss [Run] 那条（Filename=pythonw / Parameters=tray.py）同形；改一处必须
    同时改另一处，否则托盘在交互安装与静默升级两条路径里行为不一致。
    """
    return '"{}" "{}"'.format(pythonw_path, script_path)


def tray_relaunch_decision(os_name, tray_pref, pythonw_exists, script_exists):
    """纯函数：这次该不该跨会话把托盘拉起来。返回 (bool, 中文原因)。

    任一前提不满足都返回 (False, 可读原因)；原因直接写进 logs\\upgrade.log 给人看。
    """
    if os_name != "nt":
        return False, "当前不是 Windows 会话，不适合跨会话拉起托盘"
    if not tray_pref:
        return False, "用户已关闭「托盘随登录启动」（HKLM TrayAutostart=0），不自动拉起"
    if not pythonw_exists:
        return False, "安装目录里没有 python\\pythonw.exe，无法拉起托盘"
    if not script_exists:
        return False, "安装目录里没有 tray.py，无法拉起托盘"
    return True, ""


def _win32_libs():
    """加载跨会话拉起托盘要用的 Win32 库；非 Windows / 加载失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    except (ImportError, OSError, AttributeError):
        return None
    return ctypes, wintypes, kernel32, advapi32, wtsapi32


def _enable_privilege(name):
    """启用当前进程令牌里的某个特权。成功 True / 失败 False，**绝不抛异常**。

    审计 C-16：跨会话 WTSQueryUserToken 需要 SeTcbPrivilege —— 服务以 LocalSystem 运行时
    该特权在令牌里但是 disabled，不显式 AdjustTokenPrivileges 就拿不到用户令牌。
    非 Windows / 非 SYSTEM（无权启用）都只是返回 False，由调用方给出可读原因。
    """
    libs = _win32_libs()
    if libs is None:
        return False
    ctypes, wintypes, kernel32, advapi32, _wtsapi32 = libs

    class _LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class _LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Privileges", _LUID_AND_ATTRIBUTES * 1)]

    try:
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                   ctypes.POINTER(_LUID)]
        advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
        advapi32.AdjustTokenPrivileges.argtypes = [
            wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(_TOKEN_PRIVILEGES),
            wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                         TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                         ctypes.byref(token)):
            return False
        try:
            luid = _LUID()
            if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
                return False
            state = _TOKEN_PRIVILEGES()
            state.PrivilegeCount = 1
            state.Privileges[0].Luid = luid
            state.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            if not advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(state),
                                                  0, None, None):
                return False
            # AdjustTokenPrivileges 返回真但特权没给全时，GetLastError = ERROR_NOT_ALL_ASSIGNED
            return ctypes.get_last_error() != ERROR_NOT_ALL_ASSIGNED
        finally:
            kernel32.CloseHandle(token)
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def _wts_query_user_token():
    """取当前活动控制台会话的用户主令牌。返回 dict {ok, token, session_id, reason}。

    ok=True 时**调用方负责 CloseHandle(token)**。非 Windows / 没有活动控制台会话 /
    服务不是 LocalSystem（拿不到 SeTcbPrivilege）都会落在 ok=False + 可读中文原因上，
    绝不抛异常 —— 这段跑在服务启动钩子里，异常会让后面的自愈步骤静默失效。
    """
    info = {"ok": False, "token": None, "session_id": None, "reason": ""}
    libs = _win32_libs()
    if libs is None:
        info["reason"] = "当前不是 Windows（或加载不到 wtsapi32），无法查询登录会话"
        return info
    ctypes, wintypes, kernel32, _advapi32, wtsapi32 = libs
    try:
        # WTSGetActiveConsoleSessionId 由 **kernel32** 导出（Vista 起）；wtsapi32 里
        # 只有 WTSQueryUserToken 等会话函数。老系统 / 精简镜像上偶尔只在一处有，两边都试。
        get_session = getattr(kernel32, "WTSGetActiveConsoleSessionId", None) \
            or getattr(wtsapi32, "WTSGetActiveConsoleSessionId", None)
        if get_session is None:
            info["reason"] = "系统没有 WTSGetActiveConsoleSessionId（无法定位当前登录会话）"
            return info
        get_session.restype = wintypes.DWORD
        wtsapi32.WTSQueryUserToken.argtypes = [wintypes.ULONG,
                                               ctypes.POINTER(wintypes.HANDLE)]
        wtsapi32.WTSQueryUserToken.restype = wintypes.BOOL
        session_id = int(get_session())
        info["session_id"] = session_id
        if session_id in (0xFFFFFFFF, -1):
            info["reason"] = "当前没有活动的控制台登录会话（用户还没登录桌面），无法拉起托盘"
            return info
        token = wintypes.HANDLE()
        if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(token)):
            info["reason"] = ("WTSQueryUserToken 失败（Win32 错误 {}）："
                              "服务多半不是以 LocalSystem 身份运行").format(ctypes.get_last_error())
            return info
        info.update({"ok": True, "token": token, "reason": ""})
        return info
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        info["reason"] = "查询登录会话令牌失败：{!r}".format(exc)
        return info


def _relaunch_tray_in_user_session(dry_run=False):
    """把托盘重新拉进**当前登录会话**（审计 C-16 / 改进项 I-8）。返回 dict。

    {"ok": bool, "pid": int | None, "reason": str, "command": str}

    只在 _post_upgrade_startup() 确认「本次启动是刚完成的一次自动升级」时调用；
    **不要**放进每次服务启动的路径（用户从托盘菜单主动退出是正常操作，不能被复活）。
    dry_run=True 只算决策与命令行，不碰 Win32、不拉进程。

    任何失败都只返回结构化中文原因，绝不向上抛异常。
    """
    base_dir = globals().get("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
    pythonw = os.path.join(base_dir, "python", "pythonw.exe")
    script = os.path.join(base_dir, "tray.py")
    command = tray_relaunch_command(pythonw, script)
    info = {"ok": False, "pid": None, "reason": "", "command": command}

    should, reason = tray_relaunch_decision(
        os.name, _tray_autostart_pref(), os.path.isfile(pythonw), os.path.isfile(script))
    if not should:
        info["reason"] = reason
        return info
    if dry_run:
        info["would_relaunch"] = True
        info["reason"] = "dry_run：只计算决策与命令行，未真正拉起托盘"
        return info

    libs = _win32_libs()
    if libs is None:
        info["reason"] = "当前不是 Windows（或加载不到 Win32 库），无法跨会话拉起托盘"
        return info
    ctypes, wintypes, kernel32, advapi32, _wtsapi32 = libs
    try:
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
    except (AttributeError, TypeError):
        pass

    user_token = None
    primary_token = wintypes.HANDLE()
    env_block = ctypes.c_void_p()
    env_created = False
    process_handle = wintypes.HANDLE()
    thread_handle = wintypes.HANDLE()
    userenv = None
    try:
        if not _enable_privilege("SeTcbPrivilege"):
            info["reason"] = ("拿不到 SeTcbPrivilege（服务多半不是以 LocalSystem 身份运行），"
                              "无法跨会话拉起托盘")
            return info

        queried = _wts_query_user_token()
        if not queried.get("ok"):
            info["reason"] = queried.get("reason") or "查询当前登录会话令牌失败"
            return info
        user_token = queried.get("token")

        advapi32.DuplicateTokenEx.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.DuplicateTokenEx.restype = wintypes.BOOL
        if not advapi32.DuplicateTokenEx(user_token, MAXIMUM_ALLOWED, None,
                                         SECURITY_IMPERSONATION, TOKEN_PRIMARY,
                                         ctypes.byref(primary_token)):
            info["reason"] = "DuplicateTokenEx 失败（Win32 错误 {}）".format(ctypes.get_last_error())
            return info

        class _STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class _PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
            ]

        # 把**用户自己的**环境块交给新进程：tray.py 按 LOCALAPPDATA 决定 tray.log 写哪儿，
        # 继承 SYSTEM 的环境会写到 systemprofile 下（用户永远看不到那条日志）。拿不到就退回继承。
        try:
            userenv = ctypes.WinDLL("userenv", use_last_error=True)
            userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                                       wintypes.HANDLE, wintypes.BOOL]
            userenv.CreateEnvironmentBlock.restype = wintypes.BOOL
            userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
            userenv.DestroyEnvironmentBlock.restype = wintypes.BOOL
            env_created = bool(userenv.CreateEnvironmentBlock(
                ctypes.byref(env_block), user_token, False))
        except (OSError, AttributeError):
            env_created = False
        if not env_created:
            env_block = ctypes.c_void_p()

        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(_STARTUPINFOW)
        # 桌面名必须是用户会话的 winsta0\default：服务在 session 0，不指定桌面名
        # 新进程会落在服务的窗口站里 —— 图标在用户桌面上看不见。
        si.lpDesktop = "winsta0\\default"
        pi = _PROCESS_INFORMATION()
        # 规范导出在 **advapi32**（文档 Library 就是 Advapi32.dll）；个别系统 / 精简镜像上
        # kernel32 只有兼容转发，反过来没有。两个都试、优先 advapi32，免得在别的机器上
        # 撞 AttributeError 把整条自愈路径静默废掉（本轮真机自测发现的差异）。
        create_as_user = getattr(advapi32, "CreateProcessAsUserW", None) \
            or getattr(kernel32, "CreateProcessAsUserW", None)
        if create_as_user is None:
            info["reason"] = "系统里找不到 CreateProcessAsUserW（无法跨会话拉起托盘）"
            return info
        create_as_user.argtypes = [
            wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
            ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)]
        create_as_user.restype = wintypes.BOOL
        command_line = ctypes.create_unicode_buffer(command)
        created = create_as_user(
            primary_token,
            pythonw,                       # lpApplicationName
            command_line,                  # lpCommandLine（引号写法与 setup.iss [Run] 同形）
            None, None, False,             # 不继承句柄
            CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,   # 无控制台窗口
            env_block if env_created else None,
            base_dir,                      # 工作目录 = 安装目录
            ctypes.byref(si), ctypes.byref(pi))
        if not created:
            info["reason"] = "CreateProcessAsUserW 失败（Win32 错误 {}）".format(
                ctypes.get_last_error())
            return info
        process_handle = pi.hProcess
        thread_handle = pi.hThread
        info.update({"ok": True, "pid": int(pi.dwProcessId), "reason": ""})
        return info
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        info["ok"] = False
        info["pid"] = None
        info["reason"] = "跨会话拉起托盘时出现未预期异常：{!r}".format(exc)
        return info
    finally:
        # 所有句柄都必须关：这段每个升级周期只跑一次，但泄漏令牌是权限问题，不是性能问题。
        try:
            if process_handle:
                kernel32.CloseHandle(process_handle)
            if thread_handle:
                kernel32.CloseHandle(thread_handle)
            if env_created and userenv is not None:
                userenv.DestroyEnvironmentBlock(env_block)
            if primary_token:
                kernel32.CloseHandle(primary_token)
            if user_token:
                kernel32.CloseHandle(user_token)
        except (OSError, AttributeError, TypeError):
            pass


# ============================================================
# 升级尝试记录 + 熔断（v2.0.4.0）
#
# 背景：静默升级是"启动 installer → Python 立即退出"。若 installer 因任何
# 原因没有真正装上（权限、弹窗挂起、被安全软件拦），NSSM 会把服务拉起来，
# 新进程又检测到"还是旧版本"，于是再下载、再安装 —— 真机实测每 30~60 秒
# 一次、连刷 40 多次（logs/upgrade.log 可见）。这里落盘"尝试记录"：
#   - 同一目标版本失败后进入冷却（默认 6 小时）不再自动重试
#   - 失败累计 3 次后彻底停止该版本的自动升级，交给用户手动升级
#   - 服务启动后确认目标版本 == 当前版本 → 删除记录并记"升级成功"
# ============================================================
UPDATE_ATTEMPT_MAX = 3                 # 同一版本自动升级最多尝试次数
UPDATE_ATTEMPT_COOLDOWN_SEC = 6 * 3600  # 失败后的冷却时间（秒）


def _attempt_file():
    return os.path.join(LOG_DIR, "update_attempt.json")


def _read_update_attempt():
    """读升级尝试记录；不存在 / 损坏 → {}。"""
    try:
        with open(_attempt_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_update_attempt(target, count=1, state="attempted"):
    rec = {
        "target": target,
        "count": int(count),
        "state": state,
        "at": _now_iso(),
    }
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(_attempt_file(), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("写 update_attempt.json 失败: %s", exc)
    return rec


def _clear_update_attempt():
    try:
        os.remove(_attempt_file())
    except OSError:
        pass


def reset_update_attempt(reason="用户请求"):
    """重置自动升级熔断（审计 F2）：删除 logs/update_attempt.json，返回是否处理成功。

    背景：同一版本累计失败 UPDATE_ATTEMPT_MAX（3）次后，_auto_retry_blocked 会永久停止
    自动重试；旧版本没有任何 UI / API 入口，只能手工删 json。这里提供一个业务层小函数，
    由 web_api 接线：POST /api/update/install 的 payload 增加**只增**字段
    `reset_attempt`（bool）；为 true 时先调本函数（再走原有防并发逻辑）。

    契约（web_api 侧只需要一行）：
        if (payload or {}).get("reset_attempt") is True:
            _auto_update_mod.reset_update_attempt("用户点击「重置自动升级熔断」")

    **未 attach 时不炸**：本模块被单测 / 冒烟直接 import（没有 _attach 注入 LOG_DIR）时，
    取路径会 NameError —— 这属于「没有日志目录可清」，返回 False 即可，绝不向上抛异常。
    成功清除会落一条中文 upgrade.log；没有记录可清时也返回 True（幂等，调用方不必区分）。
    """
    try:
        path = _attempt_file()
    except NameError:
        return False
    existed = os.path.isfile(path)
    # 与成功升级 / 手动回滚共用同一个清理函数，语义不分叉；再复核一次，
    # 删除失败（权限 / 被占用）时如实返回 False，别让 UI 以为已经解了熔断。
    _clear_update_attempt()
    if os.path.isfile(path):
        return False
    try:
        _log_upgrade("INFO", "已重置自动升级熔断：清除升级尝试记录（{}，原因：{}）".format(
            "存在" if existed else "本就不存在", reason))
    except (NameError, OSError) as exc:   # 未 attach / 日志不可写都不影响熔断已清的事实
        log = globals().get("logger")
        if log is not None:
            log.warning("重置自动升级熔断后写 upgrade.log 失败: %s", exc)
    return True


def _auto_retry_blocked(remote_ver):
    """同一版本自动升级失败过 → 返回 (是否阻断, 原因)。

    审计 F2：旧代码这里还有 `if rec.get("state") == "success": return False, ""` ——
    那是死分支：升级成功由 `_clear_update_attempt()` 直接删除记录（见启动钩子 C 段与
    手动回滚），文件里永远写不进 "success"。删掉，避免以后误以为「写 success 能解熔断」。
    真正的人工重置入口是 `reset_update_attempt()`（web_api 的 install 端点可带
    reset_attempt=true 接线调用）。
    """
    rec = _read_update_attempt()
    if rec.get("target") != remote_ver:
        return False, ""
    count = int(rec.get("count") or 1)
    if count >= UPDATE_ATTEMPT_MAX:
        return True, "自动升级 v{} 已失败 {} 次，已停止自动重试，请手动下载安装".format(remote_ver, count)
    at = rec.get("at") or ""
    try:
        delta = (datetime.now() - datetime.fromisoformat(at)).total_seconds()
    except ValueError:
        return False, ""
    if delta < UPDATE_ATTEMPT_COOLDOWN_SEC:
        remain_min = max(1, int((UPDATE_ATTEMPT_COOLDOWN_SEC - delta) / 60))
        return True, "上次自动升级未生效（{}），{} 分钟内不再自动重试".format(at, remain_min)
    return False, ""


def _nssm_stop_service(timeout_sec=30):
    """通过 nssm.exe 停服务。timeout 后 fallback 不做（installer 会接管）。"""
    if not os.path.isfile(NSSM_PATH):
        _log_upgrade("WARN", "nssm.exe 不存在，无法显式 stop；依赖 installer / NSSM 接管")
        return False
    try:
        proc = subprocess.run(
            [NSSM_PATH, "stop", SERVICE_NAME],
            timeout=timeout_sec,
            check=False,
        )
        _log_upgrade("INFO", "nssm stop 返回码: {}".format(proc.returncode))
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        _log_upgrade("WARN", "nssm stop 超时（{}s），fallback 由 installer 接管".format(timeout_sec))
        return False
    except OSError as exc:
        _log_upgrade("WARN", "nssm stop 调用失败: {}".format(exc))
        return False


def _update_tmp_dir():
    """升级相关临时文件目录（服务的 TEMP = C:\\Windows\\TEMP，System 身份可写）。"""
    return os.environ.get("TEMP", ".") or "."


def _wrapper_path():
    return os.path.join(_update_tmp_dir(), UPDATE_WRAPPER_NAME)


def _rc_path():
    return os.path.join(_update_tmp_dir(), UPDATE_RC_NAME)


def _health_probe_path():
    """健康探测脚本路径（%TEMP%，与执行器同级；执行器跑完会删掉它）。"""
    return os.path.join(_update_tmp_dir(), UPDATE_PROBE_NAME)


def _embedded_python_path():
    """随包分发的内嵌解释器：{app}\\python\\python.exe。

    安装目录优先取 `_attach` 注入的 `BASE_DIR`；未注入时（单测 / 冒烟直接 import 本模块，
    不经过服务的 main()）退回本文件所在目录 —— 两者在实际部署里是同一个目录。
    """
    base = globals().get("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, EMBEDDED_PYTHON_REL)


# 审计 D5：生成脚本（回滚 / 健康探针）在交给 SYSTEM 执行前先跑 py_compile 校验语法。
PY_COMPILE_TIMEOUT_SEC = 30


class _GeneratedScriptError(OSError):
    """生成的升级脚本语法校验失败（审计 D5）。

    继承 OSError 是为了让既有 `except OSError` 兜底逻辑仍然有效；升级路径单独先接住它，
    给出「本次升级已中止」的明确中文错误，而不是悄悄降级继续装。
    """


# 审计 E5：升级执行器（.cmd）里的路径是**直接插值**进 "..." 的。cmd.exe 解析这一行时
# 仍会展开 %VAR%（以及延迟展开的 !VAR!），并把 ^ & | < > 当转义 / 命令分隔 / 重定向；
# 换行会把一条命令截成两条。这些字符在 Windows 目录名里是合法的，但自动升级的 .cmd
# 执行器表达不了 —— 生成执行器 / 回滚脚本前先检查，命中就**停止自动升级**，让用户手动跑安装包。
# 正常路径（盘符、空格、反斜杠、中文）一律放行，例如 D:\Program Files\DrcomAutoLogin。
_CMD_UNSAFE_PATH_CHARS = (
    ("%", "%"), ("!", "!"), ("^", "^"), ("&", "&"),
    ("|", "|"), ("<", "<"), (">", ">"), ("\r", "换行"), ("\n", "换行"),
)


def _cmd_path_unsafe_reason(path):
    """路径里是否含 cmd 插值危险字符：返回中文原因，安全时返回空串（纯函数）。

    只用于**会被拼进 .cmd 的路径**（安装目录 / 内嵌 Python / 安装包 / 脚本输出路径）；
    手动升级（用户自己双击安装包，不经过 cmd 插值）不受这层检查影响。
    """
    if not isinstance(path, str) or not path:
        return ""
    for ch, label in _CMD_UNSAFE_PATH_CHARS:
        if ch in path:
            return "路径包含 cmd 特殊字符「{}」".format(label)
    return ""


def _cmd_paths_unsafe_reason(labeled_paths):
    """一次检查多组 (标签, 路径)，返回第一条不安全项的中文原因；全安全返回空串（纯函数）。"""
    for label, path in labeled_paths:
        reason = _cmd_path_unsafe_reason(path)
        if reason:
            return "{}：{}".format(label, reason)
    return ""


class _CmdPathUnsafeError(OSError):
    """升级脚本里要插值的路径含 cmd 特殊字符，已拒绝生成脚本（审计 E5）。

    继承 OSError：既有 `except OSError` 兜底仍然接得住；升级路径单独先接住它，
    给用户明确的中文结论（停止自动升级、请手动运行安装包），而不是含糊的启动失败。
    """


def _upgrade_python_executable():
    """升级脚本（回滚 / 探针 / py_compile）用哪个解释器：内嵌优先，缺内嵌退回当前进程。

    审计 D3：回滚脚本以前用 sys.executable 却注释写「内嵌解释器」。服务由 NSSM 用
    {app}\\python\\python.exe 启动时二者等价；换成 pythonw.exe 后子进程 stdout 拿不到，
    回滚失败原因就查不出来。统一从这里取，语义与注释一致。
    """
    py = _embedded_python_path()
    return py if os.path.isfile(py) else sys.executable


def _py_compile_script(path, label="脚本"):
    """用内嵌 Python 跑 py_compile 校验生成脚本的语法（审计 D5）。返回 (ok, detail)。

    校验对象是**生成出来准备交给 SYSTEM 执行的脚本文件**（回滚脚本 / 健康探针），
    不是项目模块 —— 模板改坏时，以前只有第一次真回滚 / 真探测才发现，必须提前拦下。
    只用标准库 py_compile；子进程带超时；任何异常一律当「校验失败」，绝不放行升级。
    """
    if not path or not os.path.isfile(path):
        return False, "{}文件不存在：{}".format(label, path)
    py = _upgrade_python_executable()
    if not py:
        return False, "找不到可用的 Python 解释器"
    # 显式指定 cfile：别在 SYSTEM 的 %TEMP% 里留 __pycache__（没人清）。
    # doraise=True 让语法错误以非 0 退出码 + traceback 暴露，便于把原因写进 upgrade.log。
    cfile = path + ".pyc"
    code = ("import py_compile, sys\n"
            "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)\n")
    # 兼容替身/测试用 SimpleNamespace 版 subprocess（可能没有 PIPE 常量）：
    # 拿不到 PIPE 就不收 stderr，只按退出码判定 —— 校验结论不受影响。
    kwargs = {"timeout": PY_COMPILE_TIMEOUT_SEC, "check": False}
    pipe = getattr(subprocess, "PIPE", None)
    if pipe is not None:
        kwargs["stdout"] = pipe
        kwargs["stderr"] = pipe
    try:
        try:
            proc = subprocess.run([py, "-c", code, path, cfile], **kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, "调用 {} 校验{}失败：{}".format(os.path.basename(py or "python"), label, exc)
    finally:
        try:
            if os.path.isfile(cfile):
                os.remove(cfile)
        except OSError:
            pass
    if proc.returncode != 0:
        stderr = getattr(proc, "stderr", None) or b""
        lines = stderr.decode("utf-8", "replace").strip().splitlines()
        last = lines[-1] if lines else "退出码 {}".format(proc.returncode)
        return False, "{}语法校验失败（{}）".format(label, last)
    return True, "{}语法校验通过".format(label)


def _normalize_ui_port(value):
    """把配置里的 ui_port 归一成合法端口；拿不到 / 不合法就退回默认 8848（纯函数）。

    与 联网_service._validate_config 的口径一致（1024-65535 的 int，bool 不算）。
    """
    if isinstance(value, bool):
        return HEALTH_PROBE_DEFAULT_PORT
    try:
        port = int(value)
    except (TypeError, ValueError):
        return HEALTH_PROBE_DEFAULT_PORT
    if not (1024 <= port <= 65535):
        return HEALTH_PROBE_DEFAULT_PORT
    return port


def _probe_local_health(ui_port=None, timeout_sec=None):
    """探一次本机 Web UI 的 /api/health（升级前健康基线，审计 A2）。

    返回 (ok, detail)。**只有 ok=True 才允许开始升级**：当前服务本来就不健康
    （端口被占 / 配置非法 / 进程半死）时，装新版本也救不回来 —— 旧代码同样绑不上端口，
    执行器却会把「已经装好的新版」用备份覆盖回旧版，用户看到的是「升级失败」。
    真机最伤信心的故障就是这一类，所以升级前先要一个健康基线。

    口径与升级看门狗探针保持一致（复用端口规范化与超时常量）：
      - 只探 127.0.0.1 + /api/health；
      - 禁用系统代理（IE/环境变量里的代理可能不把 127.0.0.1 列入绕过表）；
      - 200 且响应 JSON 里 ok 为真才算健康。
    """
    port = _normalize_ui_port(ui_port)
    timeout = HEALTH_PROBE_TIMEOUT_SEC if timeout_sec is None else float(timeout_sec)
    url = "http://{}:{}{}".format(HEALTH_PROBE_HOST, port, HEALTH_PROBE_PATH)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return False, "HTTP {}".format(exc.code)
    except Exception as exc:  # noqa: BLE001  连接被拒 / 超时 / URLError 都算「不健康」
        return False, "{}: {}".format(type(exc).__name__, exc)
    if code != 200:
        return False, "HTTP {}".format(code)
    try:
        data = json.loads(body)
    except ValueError:
        return False, "响应不是 JSON"
    if not isinstance(data, dict) or not data.get("ok"):
        return False, "ok 不为真"
    return True, "http=200 ok=true"


def _installer_log_path():
    return os.path.join(_update_tmp_dir(), UPDATE_LOG_NAME)


# 健康探测脚本的模板（用 .replace 填参数，不用 %-格式化：脚本里有 strftime("%Y-...") 的 %）。
# 只依赖标准库；**不 import 任何项目模块** —— 服务崩了正是它要报告的情况。
# 结构：解析参数 → 轮询 /api/health → 结论写 rc（机器读）+ upgrade.log（人读）→ 退出码 0/1。
_HEALTH_PROBE_TEMPLATE = '''# -*- coding: utf-8 -*-
"""drcom_health_probe.py — 升级看门狗：探本机 Web UI 的 /api/health（v2.0.10.0）。

由 auto_update.py 生成到 %TEMP%，升级执行器（.cmd）在 installer 跑完后调用它，跑完即删。

为什么不能只看 `sc query`：那说的是 nssm 的 **wrapper** 状态，而 AppExit=Ignore 时里面的
Python 进程崩了 wrapper 照样 RUNNING —— 「升级成功」的判据必须落在 HTTP 层。

结论写两处：
  1) `--rc` 指的 rc 文件里追加一行 `health=OK|FAIL`（机器读；多行时以最后一条为准）；
  2) upgrade.log 追加一行带时间戳的中文结论（人读；服务真起不来时这是唯一的告警面）。

退出码：0 = 健康；1 = 不健康（结论已落盘）。
"""
import json
import sys
import time
import urllib.error
import urllib.request

try:  # 控制台编码兜底：非 UTF-8 终端下中文 print 不该把脚本打崩
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass

DEFAULT_HOST = __PROBE_HOST__
DEFAULT_PORT = __PROBE_PORT__
DEFAULT_RC = __PROBE_RC__
DEFAULT_LOG = __PROBE_LOG__
DEFAULT_WAIT = __PROBE_WAIT__
DEFAULT_INTERVAL = __PROBE_INTERVAL__
DEFAULT_TIMEOUT = __PROBE_TIMEOUT__

# 禁用系统代理：IE/环境变量里配的代理可能不把 127.0.0.1 列入绕过表，
# 那样探针会「连得上代理但连不上本机服务」→ 误报服务没起来。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _parse_args(argv):
    """极简参数解析：只认 `--key value`，认不出的键跳过。"""
    opts = {
        "host": DEFAULT_HOST, "port": DEFAULT_PORT, "rc": DEFAULT_RC, "log": DEFAULT_LOG,
        "wait": DEFAULT_WAIT, "interval": DEFAULT_INTERVAL, "timeout": DEFAULT_TIMEOUT,
        "tag": "probe",
    }
    i = 0
    while i + 1 < len(argv):
        key, val = argv[i], argv[i + 1]
        i += 2
        if key == "--host":
            opts["host"] = val
        elif key == "--port":
            try:
                opts["port"] = int(val)
            except ValueError:
                pass
        elif key == "--rc":
            opts["rc"] = val
        elif key == "--log":
            opts["log"] = val
        elif key == "--tag":
            opts["tag"] = val
        elif key in ("--wait", "--interval", "--timeout"):
            try:
                opts[key[2:]] = float(val)
            except ValueError:
                pass
    return opts


def _append(path, line):
    """追加一行（写不了就静默放弃：探针不能因为写日志失败而丢掉结论）。"""
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\\n")
    except OSError:
        pass


def _probe_once(host, port, timeout):
    """探一次 /api/health，返回 (ok, detail)。"""
    url = "http://%s:%d/api/health" % (host, port)
    try:
        with _OPENER.open(url, timeout=timeout) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return False, "HTTP %s" % exc.code
    except Exception as exc:  # noqa: BLE001  连接被拒 / 超时 / URLError 都算「没起来」
        return False, "%s: %s" % (type(exc).__name__, exc)
    if code != 200:
        return False, "HTTP %s" % code
    try:
        data = json.loads(body)
    except ValueError:
        return False, "响应不是 JSON: %s" % body[:60]
    if not isinstance(data, dict) or not data.get("ok"):
        return False, "ok 不为真: %s" % body[:60]
    return True, "http=200 ok=true version=%s" % (data.get("version") or "?")


def main(argv):
    opts = _parse_args(argv)
    port = int(opts["port"])
    url = "http://%s:%d/api/health" % (opts["host"], port)
    started = time.time()
    deadline = started + max(0.0, opts["wait"])
    ok, detail = False, "未开始"
    while True:
        ok, detail = _probe_once(opts["host"], port, opts["timeout"])
        if ok or time.time() >= deadline:
            break
        time.sleep(max(0.2, opts["interval"]))
    spent = time.time() - started
    _append(opts["rc"], "health=%s" % ("OK" if ok else "FAIL"))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if ok:
        _append(opts["log"], "[%s] [INFO] 升级看门狗（%s）：HTTP 健康检查通过 %s（%s，用时 %.1f 秒）"
                % (stamp, opts["tag"], url, detail, spent))
    else:
        _append(opts["log"], "[%s] [ERROR] 升级看门狗（%s）：HTTP 健康检查未通过 %s"
                "（等待 %.0f 秒，最后错误：%s）—— 服务可能没起来：请看 logs/service_stderr.log 与 "
                "logs/installer-silent.log，或重新运行安装包恢复"
                % (stamp, opts["tag"], url, opts["wait"], detail))
    print("health=%s %s" % ("OK" if ok else "FAIL", detail))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def _build_health_probe_script(rc_path, log_path, port, host=HEALTH_PROBE_HOST,
                               wait_sec=HEALTH_PROBE_WAIT_SEC,
                               interval_sec=HEALTH_PROBE_INTERVAL_SEC,
                               timeout_sec=HEALTH_PROBE_TIMEOUT_SEC):
    """生成 HTTP 健康探测脚本内容（纯函数，便于单测 / 行为级冒烟）。

    为什么需要它：
      - `sc query` 只是 nssm **wrapper** 的状态。AppExit=Ignore 下，里面的 Python 崩了
        wrapper 照样 RUNNING —— 这就是 v2.0.8.0 / v2.0.9.0 两次事故「升完才发现」的原因；
      - 判据放 HTTP 层（`/api/health` 返回 {"ok": true}）才算「服务真的活了」。

    参数：
        rc_path   结论落盘位置（执行器读它写进升级收尾记录；**多行时以最后一条为准**）
        log_path  人类可读结论（logs\\upgrade.log；服务起不来时这是唯一告警面）
        port      Web UI 端口（服务只监听 127.0.0.1）
    """
    return (_HEALTH_PROBE_TEMPLATE
            .replace("__PROBE_HOST__", repr(host))
            .replace("__PROBE_PORT__", repr(int(port)))
            .replace("__PROBE_RC__", repr(rc_path or ""))
            .replace("__PROBE_LOG__", repr(log_path or ""))
            .replace("__PROBE_WAIT__", repr(float(wait_sec)))
            .replace("__PROBE_INTERVAL__", repr(float(interval_sec)))
            .replace("__PROBE_TIMEOUT__", repr(float(timeout_sec))))


def _build_update_wrapper(installer_path, app_dir, log_path, rc_path, task_name, svc_name=SERVICE_NAME,
                          sc_path=None, schtasks_path=None, python_path=None, probe_path=None,
                          ui_port=None, backup_dir=None, rollback_path=None):
    """生成升级执行器的 .cmd 内容（纯函数，便于单测）。

    包装脚本要在一个「不在 NSSM Job 里」的进程里完成这些事：
      1. 静默跑 installer —— **任务列表必须带上 desktopicon**（只传 startservice 时
         Inno 会认为公共桌面快捷方式那个任务未选中，桌面图标就不会被更新）；
      2. 把 installer 退出码落盘（此前完全丢失，失败时无从查起）；
      3. 把 installer 日志复制到安装目录 logs\\ 下，方便用户 / Web UI 查看；
      4. **看门狗 A（服务状态）**：`sc query` 没 RUNNING 就 `sc start`
         （v2.0.4.1 真机：装不上 + 服务停在 StopPending，自动登录直接停摆）；
      5. **看门狗 B（HTTP 健康，v2.0.10.0）**：探 `/api/health`，探不到就
         「停 + 起」重启一次再给一次窗口。`sc query` 只看 nssm 的 wrapper，
         AppExit=Ignore 下里面的 Python 崩了 wrapper 照样 RUNNING —— 真机两次事故的根因；
      6. **自动回滚（v2.0.11.0）**：两轮都不健康 → 停服务 → 用升级前那份备份还原代码
         → 起服务 → 再探一次。这样「升坏了」不再需要用户重装安装包；
      7. 收尾：删计划任务、删探针与回滚脚本、删自己。

    审计 A2（回滚归因）：
      - 两轮都不健康时先读一眼 service_stderr.log：尾部有 FATAL / bind → 判「端口被占」，
        落 health=PORT_BUSY 并**跳过回滚**（旧代码同样绑不上端口，回滚零收益）；
      - 回滚后再探一次，若 rollback=OK 但最后一次 health 仍非 OK → 追加 rollback=USELESS，
        启动钩子据此报 error（不能宣称回滚成功）。

    新增参数都已给默认值（老调用点与老断言不受影响）：
        python_path / probe_path     内嵌解释器与探针脚本（默认取实际路径）
        ui_port                      要探的 Web UI 端口（非法值退回 8848）
        backup_dir / rollback_path   升级前的备份目录与回滚脚本（缺一个就落 rollback=SKIP）
    """
    sc = sc_path or SC_PATH
    schtasks = schtasks_path or SCHTASKS_PATH
    py = python_path if python_path is not None else _embedded_python_path()
    probe = probe_path if probe_path is not None else _health_probe_path()
    rollback = rollback_path if rollback_path is not None else _rollback_script_path()
    bk = backup_dir or ""
    bk_ver = os.path.basename(bk.rstrip("\\/")) if bk else ""
    # 备份目录名 = 版本号 → 只有过白名单才把它当 --expect 传下去（拿错备份要能拒绝）
    expect_arg = " --expect " + bk_ver if ROLLBACK_VERSION_RE.match(bk_ver) else ""
    port = _normalize_ui_port(ui_port)
    lines = [
        "@echo off",
        "rem === 星尘闪连 自动升级执行器（由 auto_update.py 生成；跑完自删）===",
        "rem 由「任务计划程序」以 SYSTEM 身份拉起：不在 NSSM 的 Job Object 里，",
        "rem 所以安装器停掉服务时不会被连带杀死（v2.0.4.2 修）。",
        "setlocal",
        'set "LOG=' + log_path + '"',
        'set "RC=' + rc_path + '"',
        'set "APP=' + app_dir + '"',
        "rem ---- 1) 静默安装（任务：桌面图标 + 启动服务）----",
        '"' + installer_path + '" /SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL'
        ' /CLOSEAPPLICATIONS /TASKS=desktopicon,startservice,trayicon /LOG="%LOG%"',
        # v2.0.10.0 修一个从 v2.0.4.2 起就在的陷阱：`echo installer_rc=0>"%RC%"` 会被 cmd
        # 当成「**句柄 0** 重定向」（数字紧贴 `>`）→ rc 文件被写成**空文件**、文本跑进 stdout ✗，
        # 于是 installer_rc= 从来没落盘（`_report_update_runner_result` 里的「非 0 退出码」
        # 告警一直是死代码）。把重定向写到命令**前面**即可彻底避开：此时行尾没有数字 ✓。
        '>>"%RC%" echo installer_rc=%ERRORLEVEL%',
        "rem ---- 2) 安装日志归档到安装目录 ----",
        'if exist "%LOG%" copy /y "%LOG%" "' + app_dir + '\\logs\\installer-silent.log" >nul 2>&1',
        "rem ---- 3) 看门狗 A：服务状态（用 ping 当 sleep：SYSTEM 会话里没有 timeout 的控制台）----",
        '"' + sc + '" query ' + svc_name + ' | find /i "RUNNING" >nul 2>&1',
        'if errorlevel 1 "' + sc + '" start ' + svc_name + ' >nul 2>&1',
        'ping -n 6 127.0.0.1 >nul 2>&1',
        "rem ---- 4) 看门狗 B：探 HTTP /api/health（v2.0.10.0）----",
        'rem nssm 的 wrapper 活着 != 里面的 Python 进程活着，能拿到 {"ok":true} 才算真的活了。',
        'if not exist "' + py + '" echo health=SKIP>>"%RC%"',
        'if not exist "' + py + '" goto after_health',
        'if not exist "' + probe + '" echo health=SKIP>>"%RC%"',
        'if not exist "' + probe + '" goto after_health',
        '"' + py + '" "' + probe + '" --port ' + str(port) + ' --rc "%RC%" --tag first'
        ' --wait ' + str(int(HEALTH_PROBE_WAIT_SEC)) + ' >nul 2>&1',
        "rem 探不到 -> 先「停 + 起」重启一次（AppExit=Ignore：服务崩了不会自己回来），再给一次窗口",
        "if not errorlevel 1 goto after_health",
        '"' + sc + '" stop ' + svc_name + ' >nul 2>&1',
        'ping -n 4 127.0.0.1 >nul 2>&1',
        '"' + sc + '" start ' + svc_name + ' >nul 2>&1',
        '"' + py + '" "' + probe + '" --port ' + str(port) + ' --rc "%RC%" --tag retry'
        ' --wait ' + str(int(HEALTH_PROBE_RETRY_WAIT_SEC)) + ' >nul 2>&1',
        "if not errorlevel 1 goto after_health",
        "rem ---- 4b) 归因：探针失败是不是「端口被占」？（审计 A2）----",
        "rem 端口冲突时旧代码同样绑不上，回滚零收益（还会把已经装好的新版覆盖回旧版）->",
        "rem 读 {app}\\logs\\service_stderr.log 尾部有没有 FATAL / bind 字样（cmd 用 find /i）。",
        'set "PORTBUSY="',
        'find /i "FATAL" "%APP%\\logs\\service_stderr.log" >nul 2>&1',
        'if not errorlevel 1 set "PORTBUSY=1"',
        'find /i "bind" "%APP%\\logs\\service_stderr.log" >nul 2>&1',
        'if not errorlevel 1 set "PORTBUSY=1"',
        'if defined PORTBUSY >>"%RC%" echo health=PORT_BUSY',
        'if defined PORTBUSY >>"%RC%" echo rollback=SKIP',
        'if defined PORTBUSY goto after_health',
        "rem ---- 4c) 两轮都不健康 -> 自动回滚到升级前那份代码（v2.0.11.0）----",
        'rem 回滚只还原代码文件；配置文件与密码文件一律不动。缺件就落 rollback=SKIP。',
        'if not exist "' + py + '" >>"%RC%" echo rollback=SKIP',
        'if not exist "' + py + '" goto after_health',
        'if not exist "' + rollback + '" >>"%RC%" echo rollback=SKIP',
        'if not exist "' + rollback + '" goto after_health',
        'if not exist "' + bk + '" >>"%RC%" echo rollback=SKIP',
        'if not exist "' + bk + '" goto after_health',
        '"' + sc + '" stop ' + svc_name + ' >nul 2>&1',
        'ping -n 4 127.0.0.1 >nul 2>&1',
        '"' + py + '" "' + rollback + '" --backup "' + bk + '" --app "' + app_dir + '" --tag auto'
        + expect_arg + ' --rc "%RC%" >nul 2>&1',
        '"' + sc + '" start ' + svc_name + ' >nul 2>&1',
        "rem 回滚后再探一次：结果会作为 rc 里最后一条 health= 落盘（多行以最后一条为准）",
        '"' + py + '" "' + probe + '" --port ' + str(port) + ' --rc "%RC%" --tag after-rollback'
        ' --wait ' + str(int(HEALTH_PROBE_RETRY_WAIT_SEC)) + ' >nul 2>&1',
        "rem 审计 A2：回滚=OK 但最后一次 health 仍非 OK -> 回滚无用（rollback=USELESS），",
        "rem 启动钩子据此把 update_state 落成 error，绝不宣称回滚成功。",
        "if not errorlevel 1 goto after_health",
        'find /i "rollback=OK" "%RC%" >nul 2>&1',
        "if errorlevel 1 goto after_health",
        '>>"%RC%" echo rollback=USELESS',
        ":after_health",
        "rem ---- 5) 服务状态回写（放在健康探测之后，取的是最终状态）----",
        '"' + sc + '" query ' + svc_name + ' | find /i "RUNNING" >nul 2>&1',
        'if errorlevel 1 (echo service=STOPPED>>"%RC%") else (echo service=RUNNING>>"%RC%")',
        "rem ---- 6) 收尾：删任务、删探针与回滚脚本、删自己 ----",
        '"' + schtasks + '" /delete /tn "' + task_name + '" /f >nul 2>&1',
        'del /f /q "' + probe + '" >nul 2>&1',
        'del /f /q "' + rollback + '" >nul 2>&1',
        'del /f /q "%~f0" >nul 2>&1',
    ]
    return "\r\n".join(lines) + "\r\n"


def _write_update_wrapper(path, script):
    """把执行器脚本落盘，返回实际使用的编码名（审计 A3 / G5）。

    - 优先按系统 ANSI（Python 的 mbcs）写：.cmd 由「任务计划程序」以 SYSTEM 身份拉起，
      cmd.exe 按**系统 ANSI 代码页**解析（无控制台、也没有机会 chcp）。以 UTF-8 落盘时，
      非 ASCII 安装目录（例如 D:\\工具\\Drcom）下的 if exist 会全部判 MISSING ✗；
    - 系统代码页表示不了中文时退回 UTF-8，并让脚本第一行先 chcp 65001（纯 ASCII，能执行）；
    - 审计 G5：newline 传空串 —— 脚本本身已经用 CRLF 拼好行尾，文本模式再按 CRLF 转换
      会生成 **CR CR LF**（实测 53 处）。cmd 能容忍，但生成物不符合预期，也容易误导排查。
    - 审计 E5：执行器**自身路径**也会被 schtasks /tr 拼进命令行 —— 含 cmd 特殊字符时
      直接拒绝生成（抛 _CmdPathUnsafeError），绝不落一个注定跑不对的脚本。
    """
    unsafe_reason = _cmd_path_unsafe_reason(path)
    if unsafe_reason:
        msg = ("安装路径包含 cmd 特殊字符，已停止自动升级；请手动运行安装包"
               "（升级执行器：{}）").format(unsafe_reason)
        _log_upgrade("WARN", msg)
        raise _CmdPathUnsafeError(msg)
    try:
        with open(path, "w", encoding="mbcs", newline="") as handle:
            handle.write(script)
        return "mbcs"
    except (LookupError, UnicodeEncodeError):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("@chcp 65001 >nul\r\n" + script)
        return "utf-8"


def _delete_update_task(reason="", warn=True):
    """删掉一次性升级计划任务（审计 E1）。

    /create 成功而 /run 失败时，任务会留在计划程序里（/sc once /st 00:00 → 次日 00:00
    再静默装一遍同一个安装包）。退回 DETACHED 直启之前必须先删掉它；删失败不致命，记 WARN。
    """
    if not os.path.isfile(SCHTASKS_PATH):
        return False
    try:
        proc = subprocess.run(
            [SCHTASKS_PATH, "/delete", "/tn", UPDATE_TASK_NAME, "/f"],
            timeout=15, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if warn:
            _log_upgrade("WARN", "清理一次性升级任务失败：{}（{}）".format(exc, reason or "退回直启前"))
        return False
    if proc.returncode == 0:
        _log_upgrade("INFO", "已清理一次性升级任务（{}）".format(reason or "退回直启前"))
        return True
    if warn:
        _log_upgrade("WARN", "清理一次性升级任务返回 {}（{}）".format(proc.returncode, reason or "退回直启前"))
    return False


def _launch_installer(installer_path, ui_port=None, backup_dir=None):
    """启动 installer，返回 {"mode": ..., "pid": ...}；抛 OSError 表示全都失败。

    v2.0.4.2 修（真机证据见文件头注释）：优先用**任务计划程序**拉起包装脚本 ——
    installer 由 Task Scheduler 服务托管，不在 nssm 的 Job Object 里，所以
    setup.iss 在 ssInstall 阶段 `nssm stop` 服务时不会把它连带杀死。
    任务计划程序不可用时才退回旧的 DETACHED_PROCESS 直启（保持旧行为）。

    v2.0.10.0：顺带写健康探测脚本（执行器用它探 `/api/health`）。写失败不致命 ——
    执行器会落一行 `health=SKIP` 退回旧判据。
    v2.0.11.0：再写一个回滚脚本并把 `backup_dir` 传下去 —— 两轮都不健康时执行器会
    自动还原升级前的代码（**写盘**失败只是落 `rollback=SKIP`，不影响升级本身）。

    审计 D5：生成的两个 .py 脚本（健康探针 / 回滚脚本）落盘后先用内嵌 Python 跑
    py_compile 校验语法；**校验失败抛 _GeneratedScriptError 中止升级，绝不启动安装器**。
    写盘失败仍按旧行为降级（探针 → health=SKIP）；校验失败不能降级 —— 那意味着
    升级后既没有健康判据、也没有可用的回滚脚本，等于在没有退路的情况下换代码。

    审计 E5：动手写任何脚本之前先检查**会被插值进 .cmd 的路径**（安装目录 / 内嵌 Python /
    安装包 / 执行器与结果文件路径）。含 cmd 特殊字符（% ! ^ & | < > 换行）时执行器一定
    跑不对 —— 抛 _CmdPathUnsafeError 中止本次自动升级，让用户手动运行安装包，绝不硬上。
    """
    unsafe_reason = _cmd_paths_unsafe_reason((
        ("安装目录", BASE_DIR),
        ("内嵌 Python", _embedded_python_path()),
        ("安装包", installer_path),
        ("升级执行器", _wrapper_path()),
        ("结果文件", _rc_path()),
        ("健康探测脚本", _health_probe_path()),
        ("回滚脚本", _rollback_script_path()),
        ("安装日志", _installer_log_path()),
    ))
    if unsafe_reason:
        msg = "安装路径包含 cmd 特殊字符，已停止自动升级；请手动运行安装包（{}）".format(unsafe_reason)
        _log_upgrade("WARN", msg)
        raise _CmdPathUnsafeError(msg)
    log_path = _installer_log_path()
    wrapper = _wrapper_path()
    rc_path = _rc_path()
    probe = _health_probe_path()
    port = _normalize_ui_port(ui_port)
    try:
        if os.path.isfile(rc_path):
            os.remove(rc_path)  # 清掉上次结果，避免下次启动读到陈旧数据
    except OSError:
        pass

    try:
        with open(probe, "w", encoding="utf-8", newline="\n") as f:
            f.write(_build_health_probe_script(rc_path, UPGRADE_LOG_FILE, port))
    except OSError as exc:
        _log_upgrade("WARN", "写健康探测脚本失败：{}（本次升级只看服务状态）".format(exc))
        probe = None

    # 审计 D5：探针脚本落盘后立刻 py_compile 校验 —— 探针语法坏了，执行器只会落
    # health=SKIP（安静退回旧判据），而用户以为「有 HTTP 健康判定」。提前拦下。
    if probe is not None:
        _probe_ok, _probe_detail = _py_compile_script(probe, "健康探测脚本")
        if not _probe_ok:
            _log_upgrade("ERROR", "健康探测脚本生成后语法校验失败：{}（本次升级中止）".format(
                _probe_detail))
            try:
                os.remove(probe)
            except OSError:
                pass
            raise _GeneratedScriptError(_probe_detail)

    # 审计 D5：回滚脚本必须「生成即校验」；语法不过就直接中止升级（raise_on_invalid=True），
    # 不能降级成 rollback=SKIP 继续装。
    rollback = None
    if backup_dir:
        rollback = _write_rollback_script(backup_dir=backup_dir, raise_on_invalid=True)

    try:
        script = _build_update_wrapper(installer_path, BASE_DIR, log_path, rc_path, UPDATE_TASK_NAME,
                                       probe_path=probe, ui_port=port,
                                       backup_dir=backup_dir, rollback_path=rollback)
        # 审计 A3 / G5：编码与行尾细节收进 _write_update_wrapper ——
        # 优先系统 ANSI（mbcs，cmd 无控制台时按 ANSI 代码页解析），
        # 表示不了中文时退回 UTF-8 + 首行 chcp 65001；newline="" 不再重复转换 CRLF。
        _write_update_wrapper(wrapper, script)
    except OSError as exc:
        _log_upgrade("WARN", "写升级执行器失败：{}（退回直启）".format(exc))
        wrapper = None

    if wrapper:
        created_task = False
        try:
            create = subprocess.run(
                [SCHTASKS_PATH, "/create", "/tn", UPDATE_TASK_NAME, "/tr", wrapper,
                 "/sc", "once", "/st", "00:00", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f"],
                timeout=20,
                check=False,
            )
            if create.returncode == 0:
                created_task = True
                run = subprocess.run(
                    [SCHTASKS_PATH, "/run", "/tn", UPDATE_TASK_NAME],
                    timeout=20,
                    check=False,
                )
                if run.returncode == 0:
                    return {"mode": "schtasks", "pid": None}
                _log_upgrade("WARN", "schtasks /run 返回 {}（先清理一次性任务，再退回直启）".format(run.returncode))
            else:
                _log_upgrade("WARN", "schtasks /create 返回 {}（退回直启）".format(create.returncode))
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log_upgrade("WARN", "任务计划程序拉起失败：{}（退回直启）".format(exc))
        if created_task:
            # 审计 E1：/run 没成功时任务还留在计划程序里（/sc once /st 00:00 → 次日 00:00
            # 会再静默装一遍同一个安装包）。退回 DETACHED 直启之前必须先删掉它。
            _delete_update_task(reason="退回 DETACHED 直启前")

    args = [
        installer_path,
        "/SP-",
        # v2.0.4.0：/SILENT → /VERYSILENT（连进度窗都不显示，服务会话里没有桌面）；
        # 补 /SUPPRESSMSGBOXES（否则 setup.iss 里任何 MsgBox 都会把静默安装挂死）、
        # /NORESTART、/NOCANCEL，并用 /LOG 落盘安装日志便于事后定位。
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCANCEL",
        "/CLOSEAPPLICATIONS",
        # v2.0.4.2：补 desktopicon —— 与计划任务路径保持一致（否则公共桌面快捷方式
        # 会被 Inno 当成「未选中」，升级后桌面图标还是旧的）
        # v2.0.6.0：补 trayicon —— 同理，否则升级会摘掉托盘的登录启动项
        "/TASKS=desktopicon,startservice,trayicon",
        "/LOG=" + log_path,
    ]
    # Windows 进程创建标志（详见 MSDN CreateProcess dwCreationFlags）
    DETACHED_PROCESS          = 0x00000008  # 子进程无控制台、不继承父 console
    CREATE_NEW_PROCESS_GROUP   = 0x00000200  # 子进程属于新 process group，不响应父 Ctrl+C/Ctrl+Break
    CREATE_BREAKAWAY_FROM_JOB  = 0x01000000  # 子进程脱离父进程的 Job Object（NSSM/服务宿主常用 Job）
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    proc = subprocess.Popen(args, close_fds=True, creationflags=flags)
    return {"mode": "detached", "pid": proc.pid}


# ============================================================
# 自动升级主流程（11 步）
# ============================================================
def _do_update_now():
    """完整执行一次升级检测 + 下载 + 升级。返回 dict（用于 HTTP 响应）。"""
    if not _acquire_update_lock():
        # 审计 F1：web_api 是「先判忙 → 回 200 → 起线程 → 线程里才取锁」。两次点击挤在
        # 线程调度间隙时，第二个请求已经拿到 200，却在这里被静默丢弃 —— 用户以为「已提交」。
        # 不能改 web_api，那就把「未执行」写进状态机与 upgrade.log，至少 UI 能看到原因，
        # 而不是停在上一轮的状态里。中文文案是契约的一部分（_smoke_static 有断言）。
        msg = "另一项升级任务正在进行，本次请求未执行"
        _set_update_state(update_state="error", update_progress=0,
                          update_progress_message=msg, update_last_error=msg)
        _log_upgrade("WARN", msg)
        return {"ok": False, "error": msg}
    try:
        # —— 1. 锁住：state=checking（acquire 时已设置）——
        _set_update_state(update_progress_message="检查 GitHub 最新版本...")
        cfg = _load_config()
        min_free_mb = int(cfg.get("update_min_free_disk_mb", 200))

        # —— 2. 校验前置条件 ——
        # 2a. 磁盘剩余
        free_mb = _check_disk_free_mb()
        if free_mb is not None and free_mb < min_free_mb:
            msg = "磁盘剩余空间不足：{} MB < {} MB".format(free_mb, min_free_mb)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        # 2b. nssm.exe 存在
        if not os.path.isfile(NSSM_PATH):
            msg = "找不到 nssm.exe：{}".format(NSSM_PATH)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        # 2c. 升级前健康基线（审计 A2）：当前服务都不健康时，先修服务再升级。
        # 否则「端口被占 / 配置非法 / 进程半死」这类问题会让新版本也起不来，
        # 执行器再把已经装好的新版用备份覆盖回旧版 —— 升级成功却被判失败。
        ui_port = cfg.get("ui_port")
        healthy, health_detail = _probe_local_health(ui_port)
        if not healthy:
            msg = "当前服务不健康（{}），已中止升级；请先修复服务（查看 logs\\service_stderr.log）后再升级".format(
                health_detail)
            _set_update_state(update_state="error", update_progress=0,
                              update_progress_message=msg, update_last_error=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        # 2d. GitHub API 可达
        latest = _check_github_latest()
        if latest is None:
            msg = "GitHub releases/latest 不可达或返回异常"
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}
        remote_ver, asset_url, digest, asset_size, published = latest

        # P1-3 / 审计 B2：远端版本串白名单（防 `..\` 穿越拼进 %TEMP% 文件名）；
        # 与 _do_check_now 共用 _valid_remote_version()，两处口径不再分叉。
        if not _valid_remote_version(remote_ver):
            msg = "远端版本串非法：{!r}".format(remote_ver)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        # —— 3. 比较版本 ——
        cmp = _compare_versions(VERSION, remote_ver)
        if cmp is None:
            msg = "无法比较版本：本机 {} vs 远程 {}".format(VERSION, remote_ver)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}
        if cmp >= 0:
            _set_update_state(update_state=None, update_progress=0, update_progress_message="")
            _log_upgrade("INFO", "已是最新版本：{}（远程 {}）".format(VERSION, remote_ver))
            return {"ok": False, "error": "已是最新版本 {}（远程 {}）".format(VERSION, remote_ver)}

        _log_upgrade("INFO", "检测到新版本 {}（当前 {}），开始下载".format(remote_ver, VERSION))
        _set_update_state(
            update_available=True,
            update_latest_version=remote_ver,
            update_latest_url=asset_url,
            update_target_version=remote_ver,
            update_last_check_at=_now_iso(),
        )

        # —— 4. 下载 + 逐镜像校验（审计 C1）——
        # P0-2 / fail-closed：digest 为 None 时**不下载、不安装** ——
        # 宁可不升级，也不执行来路不明的 exe；digest 只信主源（P1-2）。
        if not digest:
            msg = "远端未返回 digest，无法校验完整性，拒绝安装"
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg,
                              update_last_error=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        _set_update_state(update_state="downloading", update_progress=0,
                          update_progress_message="下载安装器（{}）...".format(remote_ver))

        temp_dir = os.environ.get("TEMP", ".")
        installer_name = INSTALLER_FILENAME_PATTERN.format(ver=remote_ver)
        installer_path = os.path.join(temp_dir, installer_name)

        def _on_progress(done, total):
            pct = int(done * 100 / total) if total and total > 0 else 0
            _set_update_state(update_progress=pct, update_progress_message="下载中 {}%".format(pct))

        try:
            # 审计 C1：把 digest 传进下载器 —— 每个镜像下完立刻校验，不符就换下一个镜像。
            written = _download_installer(asset_url, installer_path, asset_size, _on_progress,
                                          expected_sha256=digest)
        except _URL_ERRORS as exc:
            msg = "下载失败：{}".format(exc)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg,
                              update_last_error=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        _log_upgrade("INFO", "下载完成：{} 字节（SHA256 逐个镜像校验通过）".format(written))

        # —— 6. 备份当前代码（v2.0.11.0：整目录 + manifest）——
        # 备份失败就**不升级**：宁可这次不升，也不能在「回不去」的状态下换代码。
        backup = _backup_modules(version=VERSION, note="升级到 {}".format(remote_ver))
        if backup is None:
            msg = "备份当前版本 v{} 失败，已中止升级".format(VERSION)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            try:
                os.remove(installer_path)
            except OSError:
                pass
            return {"ok": False, "error": msg}
        _set_update_state(update_backup=backup)
        _log_upgrade("INFO", "备份到 {}".format(backup))

        # —— 7. 保存 AppExit 原值（升级透明，不修改）——
        # 之前尝试设 "Disabled" 但 NSSM 合法值是 Default/Exit/Success/Failure/Codes，
        # 写 "Disabled" 会让 nssm 服务无法启动（OpenService 0x424）。
        # 现在采用透明策略：原值存到 STATE，仅在 _post_upgrade_startup 做幂等恢复，
        # 升级期间不动注册表，避免污染用户 NSSM 配置。
        prev_appexit = _read_nssm_appexit()
        _set_update_state(update_prev_appexit=prev_appexit)
        _log_upgrade("INFO", "升级透明：AppExit 保持原值 {}（不修改注册表）".format(prev_appexit))

        # —— 8. 启动 installer（DETACHED_PROCESS 完全脱离父 Python；不 wait）——
        # v2.0.4.0：先把"这次尝试"落盘，服务下次启动时用它判断是否真的装上；
        # 失败/未生效的记录会触发 _auto_retry_blocked 的冷却，避免无限重试。
        prev_attempt = _read_update_attempt()
        prev_count = int(prev_attempt.get("count") or 0) if prev_attempt.get("target") == remote_ver else 0
        _write_update_attempt(remote_ver, count=prev_count + 1, state="attempted")
        try:
            launch = _launch_installer(installer_path, cfg.get("ui_port"), backup)
            _log_upgrade("INFO", "installer 已启动（方式={}，PID={}，第 {} 次尝试；收尾将探 http://{}:{}{}）".format(
                launch.get("mode"), launch.get("pid"), prev_count + 1,
                HEALTH_PROBE_HOST, _normalize_ui_port(cfg.get("ui_port")), HEALTH_PROBE_PATH))
        except _CmdPathUnsafeError as exc:
            # 审计 E5：路径含 cmd 插值危险字符 → 执行器 / 回滚脚本一律不生成，本次自动升级失败。
            # 手动升级（用户自己双击安装包）不经过 cmd 插值，提示语直接指向它，不误导用户去修路径。
            _write_update_attempt(remote_ver, count=prev_count + 1, state="failed")
            msg = str(exc)
            _set_update_state(update_state="error", update_progress=0,
                              update_progress_message=msg, update_last_error=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}
        except _GeneratedScriptError as exc:
            # 审计 D5：生成的脚本语法不过 → **必须中止升级**，绝不能启动安装器。
            # 单独接住（在 OSError 之前）给出明确中文原因，而不是含糊的「启动 installer 失败」。
            _write_update_attempt(remote_ver, count=prev_count + 1, state="failed")
            msg = "生成的升级脚本语法校验失败，已中止升级：{}".format(exc)
            _set_update_state(update_state="error", update_progress=0,
                              update_progress_message=msg, update_last_error=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}
        except OSError as exc:
            # 启动失败：不让 Python 退出，保持服务运行 + Web UI 显示 error
            _write_update_attempt(remote_ver, count=prev_count + 1, state="failed")
            msg = "启动 installer 失败：{}".format(exc)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        # —— 9. 升级中状态 ——
        _set_update_state(
            update_state="upgrading",
            update_progress=100,
            update_progress_message="正在升级到 {}（约 60 秒）...".format(remote_ver),
        )

        # —— 10. 立即退出 Python 进程（installer 独立接管后续安装）——
        # 关键：installer 已用 DETACHED_PROCESS 脱离父进程 + Python 用 os._exit(0) 立即
        # 终止，不调 _nssm_stop_service（之前那种调用会触发 NSSM 把我们和 installer 连带杀掉）。
        # installer 自身在 ssInstall 阶段会调 nssm stop（idempotent）→ 复制文件 → 
        # PostInstall 启动新服务。
        # v2.1.0.0（P7-1）：`os._exit(0)` **不走 atexit / finally** —— 这是**故意**的
        # （要立刻把进程让给安装器，别让 nssm 的 stop 卡在我们身上 ✓），但顺序要写明白：
        #   ① 该落盘的都已落盘（attempt 记录与升级日志在上面写完并 close 过 ✓）；
        #   ② 显式释放升级锁 —— 正常路径下进程就死了、锁随进程消失，但本模块若被
        #      嵌入方 / 测试复用，也不会留下「永远忙」的状态 ✓；
        #   ③ 最后才 _exit ✓。
        _release_update_lock()
        _log_upgrade("INFO", "升级触发完成，Python 进程立即退出（installer 独立运行）")
        os._exit(0)

    finally:
        # 若走到这里说明流程在 installer 启动前失败 / 或 stop 没杀掉我们
        # 正常路径下 NSSM stop 会让我们在执行 finally 前被 SIGKILL
        _release_update_lock()


def _do_check_now():
    """只跑 GitHub 检查 + 状态更新，不升级。"""
    if not _acquire_update_lock():
        # 审计 F1：与 _do_update_now 同一 TOCTOU 窗口 —— 检查请求也可能拿到 200 后被
        # 静默丢弃。写状态 + upgrade.log，让 UI 能解释「为什么没有任何变化」。
        msg = "另一项升级任务正在进行，本次请求未执行"
        _set_update_state(update_state="error", update_progress=0,
                          update_progress_message=msg, update_last_error=msg)
        _log_upgrade("WARN", msg)
        return {"ok": False, "error": msg}
    try:
        _set_update_state(update_progress_message="检查 GitHub 最新版本...")
        latest = _check_github_latest()
        if latest is None:
            _set_update_state(update_state="error", update_progress=0,
                              update_progress_message="GitHub 不可达或返回异常",
                              update_last_error="GitHub releases/latest 不可达",
                              update_last_check_at=_now_iso())
            _log_upgrade("WARN", "GitHub 检查失败")
            return {"ok": False, "error": "GitHub 不可达或返回异常"}

        remote_ver, asset_url, digest, size, published = latest
        # 审计 B2-①：远端版本串白名单（与 _do_update_now 共用 _valid_remote_version）。
        if not _valid_remote_version(remote_ver):
            # 注意：不把脏 tag 写进 update_latest_version / update_latest_url，
            # 否则 UI 会把它当成「最新版本」渲染出来。
            msg = "远端版本无法解析：{!r}".format(remote_ver)
            _set_update_state(
                update_state="error",
                update_progress=0,
                update_progress_message=msg,
                update_available=False,
                update_last_check_at=_now_iso(),
                update_last_error=msg,
            )
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        cmp = _compare_versions(VERSION, remote_ver)
        # 审计 B2-②：以前把「不可比较」静默当「已是最新」（与 _do_update_now 的口径相反），
        # 远端元数据坏掉时 UI 会显示「已是最新」且没有任何错误 —— 这里必须报 error。
        if cmp is None:
            msg = "远端版本无法解析：本机 {} vs 远程 {}".format(VERSION, remote_ver)
            _set_update_state(
                update_state="error",
                update_progress=0,
                update_progress_message=msg,
                update_available=False,
                update_last_check_at=_now_iso(),
                update_last_error=msg,
            )
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        if cmp >= 0:
            _set_update_state(
                update_state=None,
                update_progress=0,
                update_progress_message="",
                update_available=False,
                update_latest_version=remote_ver,
                update_latest_url=asset_url,
                update_last_check_at=_now_iso(),
                update_last_error=None,
            )
            _log_upgrade("INFO", "检查完成：已是最新 {}".format(VERSION))
            return {"ok": True, "update_available": False, "latest_version": remote_ver}

        _set_update_state(
            update_state=None,
            update_progress=0,
            update_progress_message="有新版本可用：{}".format(remote_ver),
            update_available=True,
            update_latest_version=remote_ver,
            update_latest_url=asset_url,
            update_last_check_at=_now_iso(),
            update_last_error=None,
        )
        _log_upgrade("INFO", "检查完成：发现新版本 {}".format(remote_ver))
        return {"ok": True, "update_available": True, "latest_version": remote_ver}
    finally:
        _release_update_lock()


# ============================================================
# v2.0.11.0：版本回滚（P6-6 / T4.4）
# ============================================================
def api_get_rollback():
    """GET /api/rollback —— 可回滚的版本列表 + 当前版本（只读，不改任何状态）。"""
    backups = _list_backups()
    return 200, {
        "ok": True,
        "current_version": VERSION,
        "retention_days": BACKUP_RETENTION_DAYS,
        "keep_min": BACKUP_KEEP_MIN,
        "backups": [{k: v for k, v in b.items() if k != "path"} for b in backups],
    }


def api_post_rollback(payload):
    """POST /api/rollback —— 把代码回滚到指定（默认最新）备份。

    只还原 `ROLLBACK_MODULES` 里的**代码文件**：`config.json` / `password.txt` 一律不动 ✓。
    跑的是**与自动回滚同一份脚本**（手测到的就是自动会跑的那条路径 ✓）。
    成功后调用方（web_api）负责重启服务 —— 当前进程内存里还是新代码，不重启不生效。
    """
    if is_update_busy():
        return 409, {"ok": False, "error": "任务进行中（{}），请稍候再试".format(update_busy_message())}
    if not _acquire_update_lock():
        return 409, {"ok": False, "error": "任务进行中，请稍候再试"}
    try:
        want = str((payload or {}).get("version") or "").strip()
        if want and not ROLLBACK_VERSION_RE.match(want):
            return 400, {"ok": False, "error": "版本串非法：{!r}".format(want)}
        backups = _list_backups()
        if not backups:
            return 404, {"ok": False, "error": "没有可用备份（还没成功备份过，或备份已被清理）"}
        target = want or backups[0]["version"]
        if target == VERSION:
            return 400, {"ok": False, "error": "当前已运行 v{}，无需回滚".format(VERSION)}
        chosen = next((b for b in backups if b["version"] == target), None)
        if chosen is None:
            return 404, {"ok": False, "error": "找不到 v{} 的备份".format(target)}
        _log_upgrade("WARN", "用户请求回滚：v{} -> v{}（备份时间 {}）".format(
            VERSION, target, chosen.get("created_at") or "?"))
        _set_update_state(update_progress_message="正在回滚到 v{}...".format(target))
        ok, detail = _run_rollback(chosen["path"], expect=target, tag="manual")
        if not ok:
            _set_update_state(update_state="error", update_progress_message="回滚失败",
                              update_last_error="回滚到 v{} 失败：{}".format(target, detail))
            return 500, {"ok": False, "error": "回滚失败：{}".format(detail), "version": target}
        # 回滚成功 → 清掉「升级尝试记录」：服务重启后（跑的是旧代码）启动钩子
        # 不该再报「上次自动升级未生效」，那会让人以为回滚没成功。
        _clear_update_attempt()
        _set_update_state(update_state=None, update_progress=0, update_progress_message="",
                          update_last_error=None)
        return 200, {"ok": True, "version": target, "restarting": True,
                     "message": "已回滚到 v{}，服务正在重启（约 15 秒后刷新页面）".format(target)}
    finally:
        _release_update_lock()


# ============================================================
# 自动升级后台线程
# ============================================================
# 审计 F3：默认配置就是关闭（联网_service / config.json.template / 冒烟都断言 false），
# 旧实现每 30 秒写一条 INFO —— 每机每天 2880 行，把 RotatingFileHandler 的排障窗口挤掉。
# 现在只在开关**状态变化**时记一次，并把复核间隔放宽到 300 秒（从关闭改为启用最多 5 分钟生效）。
AUTO_UPDATE_DISABLED_WAIT_SEC = 300
_auto_update_disabled_logged = False


def _auto_update_loop():
    """后台线程：按 update_check_interval_hours 周期检查 GitHub 新版。
    注意：auto_update_enabled=False 时不主动检查，但 manual / 启动钩子仍可触发。
    """
    global _auto_update_disabled_logged
    logger.info("自动升级后台线程启动")
    # 首次启动延迟 30 秒（让服务先稳定 + Web UI 就绪）
    if STOP_EVENT.wait(30):
        return
    while not STOP_EVENT.is_set():
        try:
            cfg = _load_config()
            if not cfg.get("auto_update_enabled", True):
                # 审计 F3：只在「关 → 关」的第一次记日志；等待改 300 秒（启用时行为不变）。
                if not _auto_update_disabled_logged:
                    logger.info("auto_update_enabled=False（默认关闭），每 %d 秒复核一次开关",
                                AUTO_UPDATE_DISABLED_WAIT_SEC)
                    _auto_update_disabled_logged = True
                if STOP_EVENT.wait(AUTO_UPDATE_DISABLED_WAIT_SEC):
                    return
                continue
            _auto_update_disabled_logged = False   # 开关重新打开 → 下次变化再记一次
            interval_sec = int(cfg.get("update_check_interval_hours", 6)) * 3600
        except (OSError, ValueError) as exc:
            logger.warning("读取更新配置失败: %s", exc)
            if STOP_EVENT.wait(60):
                return
            continue

        # 检查（不升级）：如果发现新版，写 STATE 但不触发 do_update_now
        # 真正的升级由后台线程检测到 update_available=True 后启动
        # 但用户明确要"静默"→ 这里直接触发升级（无需 Web UI 介入）
        # 审计 U-1：后台线程绝不能因为一次异常就死掉（NSSM AppExit=Ignore 不会拉起它，
        # 之后所有自动检查/升级都静默失效）。这里把整轮检查兜住，下轮继续。
        try:
            result = _do_check_now()
        except Exception as exc:  # noqa: BLE001
            logger.exception("检查更新抛出未捕获异常（后台线程继续）: %s", exc)
            if STOP_EVENT.wait(interval_sec):
                return
            continue
        if isinstance(result, dict) and result.get("update_available"):
            remote = result.get("latest_version") or ""
            # v2.0.4.0：失败熔断 —— 同一目标版本自动升级失败过就在冷却期内跳过，
            # 避免"装不上 → 服务被拉起 → 再检测到新版 → 再装"的 30 秒死循环。
            blocked, reason = _auto_retry_blocked(remote)
            if blocked:
                _log_upgrade("WARN", "自动升级已熔断：{}".format(reason))
                _set_update_state(update_state="error", update_progress=0,
                                  update_progress_message=reason,
                                  update_last_error=reason)
                if STOP_EVENT.wait(max(interval_sec, 3600)):
                    return
                continue
            # 静默升级：立即进入升级流程
            _log_upgrade("INFO", "后台线程检测到新版本，触发静默升级")
            try:
                _do_update_now()
            except Exception as exc:  # noqa: BLE001
                logger.exception("静默升级抛出未捕获异常（后台线程继续）: %s", exc)
            # 升级完大概率进程被杀；即使没被杀，也等下次循环
            if STOP_EVENT.wait(60):
                return
            continue

        if STOP_EVENT.wait(interval_sec):
            return


def _post_upgrade_startup():
    """启动钩子：确认上次升级结果 + 报告回滚 + 清理残留 + 自愈 AppExit / 托盘自启。

    v2.0.11.0 修一个**只有新备份方案才会暴露**的陷阱：旧实现开头写着
    「%TEMP% 里没有 `drcom_backup_*.py` 就直接 return」—— 备份改成整目录
    （`{app}\\backup\\<旧版本>\\`）之后 %TEMP% 里再也不会出现那种文件，于是
    「尝试确认 / 执行器结果 / AppExit 自愈 / 托盘自启」**会被整段静默跳过** ✗。
    现在旧式备份只剩「提示脚本变过」这一个用途（且只影响那一行日志），其余步骤一律执行 ✓。
    """
    # —— A) 旧式 %TEMP% 备份（v2.0.11.0 之前）：只用来提示 + 顺手清理 ——
    # 审计 G2：这一段**单独** try，而且先判 TEMP 是否可用。以前整段一个 try，
    # TEMP 不存在 / 无权限时 listdir 抛异常 → 后面的备份清理 / 升级结论 /
    # AppExit 自愈 / 托盘自启对齐**全部不执行**，只留一行 WARN（失败也不会被记 failed）。
    try:
        temp_dir = os.environ.get("TEMP") or ""
        if temp_dir and os.path.isdir(temp_dir):
            legacy = []
            for name in os.listdir(temp_dir):
                if name.startswith("drcom_backup_") and name.endswith(".py"):
                    legacy.append(name)
            legacy.sort(reverse=True)  # 最新在前
            if legacy:
                newest = os.path.join(temp_dir, legacy[0])
                if os.path.isfile(newest):
                    # v2.0.4.2：**不再**据此宣告"升级完成"。备份是升级前随手落的，
                    # 拿它比 hash 会把"任何一次脚本改动（含手动热补丁）"误判成"升级成功" ——
                    # 真机上就出现过"绿 banner 说已升级，实际还是旧版本"（22:41 那次日志）。
                    # 成功与否一律由下面的尝试记录（目标版本 vs 当前版本）判定。
                    current_hash = _sha256_hex(os.path.join(BASE_DIR, "联网_service.py"))
                    backup_hash = _sha256_hex(newest)
                    if current_hash and backup_hash and current_hash != backup_hash:
                        _log_upgrade("INFO", "启动钩子：联网_service.py 与最近备份不同（可能是升级，也可能只是本地改动）")
                    else:
                        _log_upgrade("INFO", "启动钩子：未检测到脚本变更")
                cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
                for name in legacy[1:]:
                    p = os.path.join(temp_dir, name)
                    try:
                        if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                            os.remove(p)
                            _log_upgrade("INFO", "清理过期备份：{}".format(name))
                    except OSError:
                        pass
        else:
            _log_upgrade("WARN", "启动钩子：%TEMP% 不可用（{!r}），跳过旧式备份清理".format(temp_dir))
    except Exception as exc:  # noqa: BLE001
        _log_upgrade("WARN", "启动钩子：扫旧式备份异常（{}），继续后面的步骤".format(exc))

    # —— B) 整目录备份（v2.0.11.0 起）：清过期（最新 2 份永远保留）+ 报可用清单 ——
    try:
        _prune_backups()
    except Exception as exc:  # noqa: BLE001  清理失败不该拖垮后面的自愈步骤
        logger.warning("清理过期备份异常: %s", exc)
    try:
        _log_upgrade("INFO", "可用回滚备份：{}".format(
            "、".join("v{}".format(b["version"]) for b in _list_backups()) or "无"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("列备份失败: %s", exc)

    # —— C) v2.0.4.0：用尝试记录确认"上次自动升级到底装上没有" ——
    # 之前只比对备份 hash，无法区分"同版本重装"与"根本没装上"，
    # 于是装不上时既没有告警、也没有熔断。
    #
    # v2.1.3.7（审计 C-16）：本段同时是「托盘要不要跨会话拉回来」的唯一开关 ——
    # just_upgraded 只在**本次启动确实刚完成一次自动升级**（目标版本 == 当前版本）时为真，
    # F2 段据此决定拉不拉托盘。每次服务启动都拉是不行的：用户从托盘菜单主动退出是
    # 产品行为，服务不能把它复活。
    just_upgraded = False
    try:
        attempt = _read_update_attempt()
        target = attempt.get("target")
        if target and attempt.get("state") != "success":
            count = int(attempt.get("count") or 1)
            if _compare_versions(VERSION, target) == 0:
                just_upgraded = True
                _log_upgrade("INFO", "升级成功确认：已运行 v{}（目标 {}，尝试 {} 次）".format(VERSION, target, count))
                _clear_update_attempt()
                # v2.0.4.2：成功 banner 改由「真装上了」点亮（此前由备份 hash 误判点亮）
                _set_update_state(
                    update_state="success",
                    update_progress=100,
                    update_progress_message="已升级到 v{}".format(VERSION),
                    update_target_version=VERSION,
                    update_success_at=_now_iso(),
                    update_last_error=None,
                )
            else:
                _log_upgrade("WARN", "上次自动升级未生效：目标 {}，当前仍为 {}（installer 被拦截/挂起？见 logs/installer-silent.log）".format(target, VERSION))
                _write_update_attempt(target, count=count, state="failed")
                _set_update_state(update_last_error="上次自动升级未生效（目标 {}）".format(target))
    except Exception as exc:  # noqa: BLE001
        _log_upgrade("WARN", "启动钩子：确认上次升级结果异常（{}）".format(exc))

    # —— D) v2.0.4.2：读升级执行器留下的结果（退出码 / 服务状态）并清掉残留 ——
    # v2.0.4.3：带 8 秒宽限 —— 服务是安装器在 ssPostInstall 拉起来的，
    # 那一刻执行器还在等 installer 退出（随后才写 rc），立刻读会读空。
    # 审计 G1：同步等待保留（防与探针互相等），更晚落盘的 health= / rollback= 由 G) 延迟复核。
    try:
        _report_update_runner_result(wait_sec=8)
        _cleanup_update_leftovers()
    except Exception as exc:  # noqa: BLE001
        _log_upgrade("WARN", "启动钩子：读执行器结果 / 清理残留异常（{}）".format(exc))

    # —— E) AppExit ——
    try:
        # AppExit 策略归**安装器**所有（P0-7）：setup.iss / install.bat 统一设
        # Default Ignore + 0 Ignore（防端口冲突时 NSSM 死循环重启）。
        # 这里**不再写回 Restart** —— 旧行为每次启动都把 v2.0.2.3 刚修的
        #「端口占用无限重启循环」修复原样撤销，且与 setup.iss 的 Ignore 打架。
        _log_upgrade("INFO", "AppExit 策略由安装器维持（Default Ignore / 0 Ignore），启动钩子不再覆盖")
        # v2.0.4.0 例外 / 审计 E2：不是「Default Ignore + 0 Ignore」就纠偏（绝不写 Restart）。
        appexit = _ensure_nssm_appexit_sane()
        if appexit:
            _log_upgrade("INFO", "AppExit 现状：{}".format(appexit))
    except Exception as exc:  # noqa: BLE001
        _log_upgrade("WARN", "启动钩子：AppExit 自愈异常（{}）".format(exc))

    # —— F) 托盘自启 ——
    try:
        # v2.0.6.2：托盘自启项自愈 —— 新任务没法靠旧版本的 /TASKS 传，
        # 只能由服务每次启动自己对齐（详见 _ensure_tray_autostart_sane 的注释）
        tray = _ensure_tray_autostart_sane()
        _log_upgrade("INFO", "托盘自启项：action={} want={} 现值={}".format(
            tray.get("action"), tray.get("want"), tray.get("current") or "不存在"))
    except Exception as exc:  # noqa: BLE001
        _log_upgrade("WARN", "启动钩子：托盘自启对齐异常（{}）".format(exc))

    # —— F2) v2.1.3.7 / 审计 C-16：静默升级后把托盘拉回**当前登录会话** ——
    # 为什么需要：升级前 setup.iss 的 PrepareToInstall 一定 KillTray()，而 [Run] 起托盘
    # 那条带 skipifsilent —— 静默升级结束后没有任何人把托盘拉回来。真机证据（tray.log）：
    # 2026-11-17 15:54:29 之后停写，直到 2026-11-18 13:25:47 下一次登录才启动（约 21.5 小时）。
    #
    # 产品行为红线：**只在 just_upgraded 为真时拉**。绝不放进 _auto_update_loop / 每次服务
    # 启动的路径 —— 用户从托盘菜单主动退出托盘是正常操作，服务不能把它「复活」。
    # 成功 / 失败都记一行中文 upgrade.log：失败也不影响其他自愈，下次登录仍由自启项启动。
    if just_upgraded:
        try:
            relaunch = _relaunch_tray_in_user_session()
            if relaunch.get("ok"):
                _log_upgrade("INFO", "托盘已在当前登录会话重新拉起（pid={}）；静默升级会杀掉托盘（C-16）".format(
                    relaunch.get("pid")))
            else:
                _log_upgrade("WARN", "托盘未能在当前会话自动拉起（{}），下次登录会由自启项启动".format(
                    relaunch.get("reason") or "未知原因"))
        except Exception as exc:  # noqa: BLE001  拉托盘失败绝不能拖垮启动钩子
            _log_upgrade("WARN", "托盘未能在当前会话自动拉起（{}），下次登录会由自启项启动".format(exc))

    # —— G) 延迟复核执行器结果（审计 G1）——
    # 同步只做「看一眼」（上面最多等 8 秒）；health= 与 rollback= 都更晚才落盘，
    # 这里起 daemon 线程 75 秒后再读一次，读完删 rc 防重复上报（不阻塞 HTTP 绑定）。
    try:
        _start_deferred_runner_recheck()
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动升级结果延迟复核线程失败: %s", exc)


def _report_update_runner_result(wait_sec=0):
    """读升级执行器（任务计划程序里的 .cmd）落盘的结果，写进 upgrade.log。

    v2.0.4.2 新增；v2.0.4.3 加 `wait_sec` —— 服务是由安装器在 `ssPostInstall` 拉起来的，
    那一刻执行器还在等 installer 进程退出（随后才写 rc），立刻读会读空。
    所以发现执行器还在时，最多等 `wait_sec` 秒（只在刚升级完的那一次启动发生）。

    审计 G1：返回 True 表示这次真的读到了 rc（延迟复核线程据此决定是否删除 rc）。
    审计 A2：rollback 结论区分 OK / FAIL / SKIP / USELESS 四种 —— USELESS 表示回滚脚本
    成功了但回滚后仍不健康，**绝不能**当成「回滚成功」上报。
    """
    rc = _rc_path()
    deadline = time.time() + max(0, int(wait_sec or 0))
    while not os.path.isfile(rc) and os.path.isfile(_wrapper_path()) and time.time() < deadline:
        time.sleep(1.0)
    if not os.path.isfile(rc):
        return False
    try:
        with open(rc, "r", encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if l.strip()]
    except OSError:
        return False
    if not lines:
        return False
    detail = " / ".join(lines)
    failed = any(("installer_rc=" in l and not l.endswith("=0")) for l in lines)
    if failed:
        _log_upgrade("WARN", "升级执行器结果：{}（installer 非 0 退出码，详见 logs/installer-silent.log）".format(detail))
    else:
        _log_upgrade("INFO", "升级执行器结果：{}".format(detail))

    # v2.0.10.0：HTTP 健康探测的结论（多行时**以最后一条为准** —— 先 FAIL 后 OK
    # 表示「停 + 起」重启后服务自己活了过来，那是正常收尾，不是故障）。
    #
    # 注意：这里**只等 rc 文件出现**，绝不等 `health=` 那一行 —— 探针探的正是本进程
    # 正在启动的 HTTP 服务，若在此阻塞等探测结论就会互相等成死锁（探针 20 秒窗口
    # 内服务一直不答 → 误报失败）。结论的「人读」那半由探针**自己**写进 upgrade.log。
    health = None
    for l in lines:
        if l.startswith("health="):
            health = l.split("=", 1)[1].strip()
    if health == "OK":
        _log_upgrade("INFO", "升级看门狗：HTTP 健康探测通过（/api/health 答了 {\"ok\": true}，服务真的活了）")
    elif health == "FAIL":
        _log_upgrade("WARN", "升级看门狗：HTTP 健康探测未通过（{}）；本进程现在已经跑起来了，"
                             "多半只是启动慢于探测窗口。若反复出现，请看 logs\\service_stderr.log".format(detail))
    elif health == "SKIP":
        _log_upgrade("WARN", "升级看门狗：本次没做 HTTP 健康探测（缺内嵌 python 或探测脚本），只看服务状态")
    elif health == "PORT_BUSY":
        # 审计 A2：探针两轮都失败，但 service_stderr.log 里有 FATAL / bind —— 端口被占。
        # 旧代码同样绑不上，回滚零收益，执行器已跳过回滚（rollback=SKIP）。
        _log_upgrade("WARN", "升级看门狗：探测失败疑似**端口被占用**（service_stderr.log 里有 FATAL/bind 字样）；"
                             "旧代码同样绑不上端口，自动回滚零收益，已跳过（rollback=SKIP）。"
                             "请先解决端口冲突，或改配置里的 ui_port 后重启服务")

    # v2.0.11.0：自动回滚结论。执行器在「两轮都不健康」时会用升级前那份备份还原代码 ——
    #   成功：本进程跑的是**回滚后**的代码（若回滚回了旧版本，那个旧版本不认识这行，
    #         所以真正的「人读」告警由回滚脚本自己写进 upgrade.log）；
    #   失败：本进程仍是新代码 → 这里必须给出明确指引，不能让它静默变砖 ✗。
    rollback = None
    for l in lines:
        if l.startswith("rollback="):
            rollback = l.split("=", 1)[1].strip()
    if rollback == "OK":
        _log_upgrade("WARN", "升级看门狗：上次升级探测失败后**已自动回滚**到备份版本 —— "
                             "请看 logs\\upgrade.log 里的「版本回滚」那行，确认现在跑的是哪一版")
        _set_update_state(
            update_last_error="上次升级后服务没起来，已自动回滚（详见 logs\\upgrade.log）",
        )
    elif rollback == "FAIL":
        _log_upgrade("ERROR", "升级看门狗：自动回滚**失败**（{}）—— 服务现在虽然起来了，但代码可能处于"
                              "「半新半旧」；请重新运行安装包，或在 Web UI「配置 → 自动升级」里手动回滚".format(detail))
        _set_update_state(
            update_state="error",
            update_progress_message="自动回滚失败，建议手动回滚或重装",
            update_last_error="上次升级失败且自动回滚失败（详见 logs\\upgrade.log）",
        )
    elif rollback == "SKIP":
        _log_upgrade("WARN", "升级看门狗：没做自动回滚（缺备份 / 内嵌 python / 回滚脚本），只做了重启重试")
    elif rollback == "USELESS":
        # 审计 A2：回滚脚本成功（rollback=OK 已落盘），但回滚后最后一次 health 仍非 OK ——
        # 回滚没有救回来，可能要继续人工处理。绝不宣称「回滚成功」。
        _log_upgrade("ERROR", "升级看门狗：自动回滚**没有奏效**（rollback=USELESS：回滚脚本成功，"
                              "但回滚后服务仍不健康）—— 可能需要人工处理：请重新运行安装包，"
                              "或在 Web UI「配置 → 自动升级」里手动回滚")
        _set_update_state(
            update_state="error",
            update_progress_message="回滚后仍不健康，可能需要人工处理",
            update_last_error="上次升级失败，回滚后仍不健康，可能需要人工处理（详见 logs\\upgrade.log）",
        )
    return True


# 审计 G1：同步钩子只能等 8 秒（那时 rc 里通常只有 installer_rc 一行），
# health=（5 秒 ping + 20 秒探测）与 rollback=（32 秒+）都还没落盘；但又不能同步等 ——
# 探针探的正是本进程的 HTTP 端口，两边互等就是死锁。
# 所以：同步「看一眼」，75 秒后再复核一次并把结论写进状态机（daemon 线程，不影响启动）。
DEFERRED_RECHECK_DELAY_SEC = 75


def _deferred_runner_recheck(delay_sec=None):
    """延迟复核执行器结果（审计 G1）。返回是否真的读到并上报了。

    服务是安装器在 ssPostInstall 里拉起的：同步钩子只能等 8 秒，那时执行器通常才刚写完
    installer_rc=；health= 与 rollback= 更晚落盘。本函数 sleep ~75 秒后再读一次，
    把 health= / rollback= 的结论真正写进状态机（UI 才看得到「回滚了 / 回滚失败 / 回滚无用」）。

    读完**删除 rc**，防止同一份结果被下一次启动重复上报；陈旧清理是 5 分钟
    （UPDATE_LEFTOVER_STALE_SEC），75 秒的间隔来得及，不会被提前删掉。
    """
    wait = DEFERRED_RECHECK_DELAY_SEC if delay_sec is None else delay_sec
    try:
        time.sleep(max(0.0, float(wait)))
    except (TypeError, ValueError):
        time.sleep(DEFERRED_RECHECK_DELAY_SEC)
    if not os.path.isfile(_rc_path()):
        return False
    try:
        reported = _report_update_runner_result(wait_sec=0)
    except Exception as exc:  # noqa: BLE001  延迟线程不能因为一次异常静默消失
        logger.warning("延迟复核升级结果异常: %s", exc)
        return False
    if not reported:
        return False
    try:
        os.remove(_rc_path())
        _log_upgrade("INFO", "延迟复核完成：执行器结果已写入状态并清理（防重复上报）")
    except OSError as exc:
        logger.warning("清理执行器结果文件失败: %s", exc)
    return True


def _start_deferred_runner_recheck(delay_sec=None):
    """起一个 daemon 线程做延迟复核（不阻塞启动钩子 / 不拖慢 HTTP 绑定，审计 G1）。"""
    wait = DEFERRED_RECHECK_DELAY_SEC if delay_sec is None else delay_sec
    thread = threading.Thread(target=_deferred_runner_recheck, args=(wait,),
                              name="drcom-upgrade-recheck", daemon=True)
    thread.start()
    return thread


def _cleanup_update_leftovers():
    """清掉升级残留：包装脚本 / 结果文件 / 计划任务 / 已装上的安装包。

    v2.0.4.2 新增；v2.0.4.3 修一个"自己造出来"的 bug：服务是安装器在 `ssPostInstall`
    就拉起来的，此时执行器 `.cmd` **还在跑**（它在等 installer 进程退出，然后才归档日志、
    写 rc）。旧实现上来就把执行器删掉 → 它后面的步骤全部没执行（真机实测：rc 与
    `logs\\installer-silent.log` 都没了）。现在只清**陈旧**残留，并顺手把安装日志归档回安装目录。
    """
    stale_before = time.time() - UPDATE_LEFTOVER_STALE_SEC
    # 1) 安装日志归档回安装目录（执行器也做这件事；两边都做保证不丢）
    try:
        src = _installer_log_path()
        if LOG_DIR:
            dst = os.path.join(LOG_DIR, "installer-silent.log")
            if os.path.isfile(src) and (
                not os.path.isfile(dst) or os.path.getmtime(src) > os.path.getmtime(dst)
            ):
                shutil.copy2(src, dst)
                _log_upgrade("INFO", "已归档安装日志：{}".format(dst))
    except OSError as exc:
        logger.warning("归档 installer 日志失败: %s", exc)
    # 2) 包装脚本 / 结果文件 / 探针 / 回滚脚本：只清陈旧的（刚跑完那次的交给执行器自己收尾）
    for p in (_wrapper_path(), _rc_path(), _health_probe_path(), _rollback_script_path(),
              os.path.join(_update_tmp_dir(), "drcom_rollback.rc")):
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < stale_before:
                os.remove(p)
        except OSError:
            pass
    # 3) 计划任务：只有执行器已经不在了（跑完自删）才兜底删；正在跑就别动
    if os.path.isfile(SCHTASKS_PATH) and not os.path.isfile(_wrapper_path()):
        try:
            subprocess.run(
                [SCHTASKS_PATH, "/delete", "/tn", UPDATE_TASK_NAME, "/f"],
                timeout=15, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    # 4) 已经装上的安装包（审计 E4 / I9：产物名以 setup.iss 为准）
    # 新前缀 StardustFlashLink-Setup-v* + 历史前缀 DrcomAutoLogin-Setup-v* 都认，
    # 否则改名之后旧残留永远清不掉（12 MB × 每次升级）。
    # 审计 J8：以前只删「刚好是当前版本」的包 —— 换目标版本重试后，上一个目标版本的
    # 12 MB 包会一直躺在 %TEMP% 里。现在再清**旧版本**的残留，规则（宁可不删，绝不误删）：
    #   - 只看 _update_tmp_dir() 根目录（不递归，别处的同名文件一律不碰）；
    #   - 文件名能解析出**严格四段**版本，且该版本 < 当前版本；
    #   - mtime 超过 OLD_INSTALLER_MAX_AGE_SEC（24 小时）——刚下好 / 正被安装器占用的包不动；
    #   - 解析不出 / 版本更高 / 删不掉（正在使用）→ 一律保留；删除失败只记 DEBUG。
    try:
        tmp_dir = _update_tmp_dir()
        old_before = time.time() - OLD_INSTALLER_MAX_AGE_SEC
        for name in os.listdir(tmp_dir):
            matched_prefix = None
            for prefix in (INSTALLER_FILENAME_PREFIX, LEGACY_INSTALLER_FILENAME_PREFIX):
                if name.startswith(prefix) and name.endswith(".exe"):
                    matched_prefix = prefix
                    break
            if matched_prefix is None:
                continue
            ver = name[len(matched_prefix):-len(".exe")]
            full_path = os.path.join(tmp_dir, name)
            cmp = _compare_versions(VERSION, ver)
            if cmp == 0:
                # 当前版本：已经装上，照旧立刻删（不看 mtime，保持旧行为）
                try:
                    os.remove(full_path)
                    _log_upgrade("INFO", "清理已安装的安装包：{}".format(name))
                except OSError:
                    pass
                continue
            # 旧版本：版本必须严格小于当前版本，且文件名是严格四段数字
            if cmp != 1 or len(_parse_version(ver)) != 4:
                continue
            try:
                if os.path.getmtime(full_path) >= old_before:
                    continue  # 24 小时内：可能是本轮刚下载的包 / 正在使用
                os.remove(full_path)
                _log_upgrade("INFO", "清理旧版本安装包：{}".format(name))
            except OSError as exc:
                logger.debug("清理旧版本安装包失败（保留）：%s（%s）", name, exc)
    except OSError:
        pass


def _schedule_success_clear():
    """5 分钟后清掉绿 banner（避免每次启动都显示）。"""
    success_at = _get_update_field("update_success_at")
    if not success_at:
        return
    try:
        sa = datetime.fromisoformat(success_at)
        delta = (datetime.now() - sa).total_seconds()
        if delta >= UPGRADE_SUCCESS_TTL_SEC:
            _set_update_state(update_state=None, update_progress_message="", update_success_at=None)
    except ValueError:
        _set_update_state(update_state=None, update_progress_message="", update_success_at=None)




# ============================================================
# 运行时引用注入（由 联网_service.py 在 import 时调用）
# ============================================================
def _attach(*, logger, state, state_lock, update_lock,
            tools_dir, nssm_path, upgrade_log_file, log_dir, base_dir,
            load_config, save_config, now_iso,
            stop_event):
    """由 联网_service.py 调用，注入共享对象到本模块 globals。

    与 web_api 的 _attach 策略一致：所有共享名字直接绑到本模块 globals，
    让原 auto_update 函数体的 _load_config / STATE_LOCK / NSSM_PATH 等
    裸名引用无须重写即可工作。
    """
    g = globals()
    g["logger"] = logger
    g["STATE"] = state
    g["STATE_LOCK"] = state_lock
    g["UPDATE_LOCK"] = update_lock
    g["TOOLS_DIR"] = tools_dir
    g["NSSM_PATH"] = nssm_path
    g["UPGRADE_LOG_FILE"] = upgrade_log_file
    g["LOG_DIR"] = log_dir
    g["BASE_DIR"] = base_dir
    g["_load_config"] = load_config
    g["_save_config"] = save_config
    g["_now_iso"] = now_iso
    g["STOP_EVENT"] = stop_event


def _log(msg, *args, level=logging.INFO):
    """薄薄的 logger 包装；logger 未注入时静默。"""
    log = globals().get("logger")
    if log is None:
        return
    if level == logging.INFO:
        log.info(msg, *args)
    elif level == logging.WARNING:
        log.warning(msg, *args)
    elif level == logging.ERROR:
        log.error(msg, *args)
    elif level == logging.DEBUG:
        log.debug(msg, *args)
    else:
        log.log(level, msg, *args)
