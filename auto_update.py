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
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from version import VERSION


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
INSTALLER_FILENAME_PATTERN = "DrcomAutoLogin-Setup-v{ver}.exe"
NSSM_REGISTRY_PATH = r"HKLM\SYSTEM\CurrentControlSet\Services\DrcomAutoLogin"
NSSM_PARAMETERS_PATH = NSSM_REGISTRY_PATH + r"\Parameters"
SERVICE_NAME = "DrcomAutoLogin"
UPGRADE_HISTORY_MAX_LINES = 50
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
def _parse_version(s):
    """将 "1.2" / "v1.3.1" / "1.3-fix" / "1.3.1-hotfix" 解析为可比较元组。解析失败返回 ()。

    规则:
    - 忽略前缀 'v' / 'V'
    - '.' 分隔数字段;每个数字段必须是纯数字,或数字后带非数字后缀( '-fix' / '_hotfix' / '-rc1' 等)
    - 遇到非数字后缀时,追加 sentinel 999 让补丁版本严格大于同主版本号 (1.3-fix > 1.3)
      但又人工高于任意后续小版本号( 1.3-fix > 1.3.1 );遇到后缀后立即停止解析,
      防止 "1.3-fix.5" 这类异常输入被错误地拆出更多段
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
        # 同一段里剩余字符(如 "-fix" / "_hotfix" / "-rc1" 等)→ 追加 sentinel
        if len(part) > len(digits):
            # 任何非数字后缀都让补丁版 > 同主版本(且人工高于任意后续小版本)
            out.append(999)
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


def _ensure_upgrade_log():
    """确保升级日志文件存在并返回句柄。每次追加写。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if not os.path.isfile(UPGRADE_LOG_FILE):
            # 原子创建（utf-8 + LF）
            with open(UPGRADE_LOG_FILE, "a", encoding="utf-8") as f:
                pass
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
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
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


def _download_installer(url, dest_path, expected_size, progress_callback=None):
    """按顺序尝试主源 + 镜像下载 installer；任一成功即返回字节数。
    所有镜像都失败则抛最后一次异常。

    progress_callback(downloaded_bytes, total_bytes_or_None) 每 ~200ms 触发。
    """
    primary_url = url
    last_exc = None
    for mirror in GITHUB_DOWNLOAD_MIRRORS:
        target = primary_url if mirror is None else mirror.rstrip("/") + "/" + primary_url
        try:
            return _do_download_installer(
                target, dest_path, expected_size, progress_callback,
                timeout=DOWNLOAD_TIMEOUT_SEC if mirror is None else GITHUB_DOWNLOAD_REQUEST_TIMEOUT_SEC,
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.warning(
                "下载镜像 %s 失败: %s",
                "primary" if mirror is None else mirror,
                exc,
            )
            last_exc = exc
            continue
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
    except (urllib.error.URLError, OSError, TimeoutError):
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


def _backup_service_py():
    """复制联网_service.py 到 %TEMP%，返回 backup 路径。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(os.environ.get("TEMP", "."), "drcom_backup_{}.py".format(ts))
    try:
        shutil.copy2(os.path.join(BASE_DIR, "联网_service.py"), backup)
        return backup
    except OSError as exc:
        logger.error("备份联网_service.py 失败: %s", exc)
        return None


def _read_nssm_appexit():
    """读 nssm 的 AppExit 策略（Default 子值）；不存在 / 为空 / 权限不足返回 None。

    v2.0.4.2 修：nssm 的存储结构是**子键 + 子值** ——

        HKLM\\SYSTEM\\CurrentControlSet\\Services\\<svc>\\Parameters\\AppExit
            (默认)  REG_SZ  Ignore     ← nssm set <svc> AppExit Default Ignore
            0       REG_SZ  Ignore     ← nssm set <svc> AppExit 0 Ignore

    v2.0.4.1 及以前读的是 `Parameters` 下的**同名值** `AppExit` —— 那儿根本没有这个值，
    于是每次启动都误报「AppExit 自愈失败（仍是空值）」，并且白白往 `Parameters`
    写了一个 nssm 不认的非法值。真机日志里那行 WARN 就是这么来的。
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _hklm_subpath(APPEXIT_SUBKEY)) as k:
            value, _ = winreg.QueryValueEx(k, APPEXIT_DEFAULT_VALUE)
    except (OSError, ImportError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


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
    """AppExit 为空 / 缺失时自愈为与 setup.iss 一致的值（v2.0.4.0；v2.0.4.2 修读取方式）。

    返回当前（修复后）的值，失败返回 None。
    """
    cur = _read_nssm_appexit()
    if cur:
        # 已有合法策略（Ignore）→ 什么都不做，也**不该**报"自愈失败"（v2.0.4.2 修的误报）
        return cur
    if not os.path.isfile(NSSM_PATH):
        _log_upgrade("WARN", "AppExit 无效（空/缺失）且找不到 nssm.exe，跳过自愈")
        return None
    ok_default = _set_nssm_appexit("Default", "Ignore")
    ok_zero = _set_nssm_appexit("0", "Ignore")
    fixed = _read_nssm_appexit()
    if fixed:
        _log_upgrade("INFO", "AppExit 自愈完成：此前为空/缺失 → 现为 {}（Default Ignore={} / 0 Ignore={}）".format(
            fixed, ok_default, ok_zero))
    else:
        _log_upgrade("WARN", "AppExit 自愈失败（仍是空值），请手动执行：nssm set {} AppExit Default Ignore".format(SERVICE_NAME))
    return fixed


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


def _auto_retry_blocked(remote_ver):
    """同一版本自动升级失败过 → 返回 (是否阻断, 原因)。"""
    rec = _read_update_attempt()
    if rec.get("target") != remote_ver:
        return False, ""
    if rec.get("state") == "success":
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
                          ui_port=None):
    """生成升级执行器的 .cmd 内容（纯函数，便于单测）。

    包装脚本要在一个「不在 NSSM Job 里」的进程里完成这些事：
      1. 静默跑 installer —— **任务列表必须带上 desktopicon**（只传 startservice 时
         Inno 会认为公共桌面快捷方式那个任务未选中，桌面图标就不会被更新）；
      2. 把 installer 退出码落盘（此前完全丢失，失败时无从查起）；
      3. 把 installer 日志复制到安装目录 logs\\ 下，方便用户 / Web UI 查看；
      4. **看门狗 A（服务状态）**：`sc query` 没 RUNNING 就 `sc start`
         （v2.0.4.1 真机：装不上 + 服务停在 StopPending，自动登录直接停摆）；
      5. **看门狗 B（HTTP 健康，v2.0.10.0 新增）**：探 `/api/health`，探不到就
         「停 + 起」重启一次再给一次窗口。`sc query` 只看 nssm 的 wrapper，
         AppExit=Ignore 下里面的 Python 崩了 wrapper 照样 RUNNING —— 真机两次事故的根因；
      6. 收尾：删计划任务、删探针、删自己。

    新增参数都已给默认值（老调用点与老断言不受影响）：
        python_path / probe_path  显式指定内嵌解释器与探针脚本（默认取实际路径）
        ui_port                   要探的 Web UI 端口（非法值退回 8848）
    """
    sc = sc_path or SC_PATH
    schtasks = schtasks_path or SCHTASKS_PATH
    py = python_path if python_path is not None else _embedded_python_path()
    probe = probe_path if probe_path is not None else _health_probe_path()
    port = _normalize_ui_port(ui_port)
    lines = [
        "@echo off",
        "rem === 星尘闪连 自动升级执行器（由 auto_update.py 生成；跑完自删）===",
        "rem 由「任务计划程序」以 SYSTEM 身份拉起：不在 NSSM 的 Job Object 里，",
        "rem 所以安装器停掉服务时不会被连带杀死（v2.0.4.2 修）。",
        "setlocal",
        'set "LOG=' + log_path + '"',
        'set "RC=' + rc_path + '"',
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
        ":after_health",
        "rem ---- 5) 服务状态回写（放在健康探测之后，取的是最终状态）----",
        '"' + sc + '" query ' + svc_name + ' | find /i "RUNNING" >nul 2>&1',
        'if errorlevel 1 (echo service=STOPPED>>"%RC%") else (echo service=RUNNING>>"%RC%")',
        "rem ---- 6) 收尾：删任务、删探针、删自己 ----",
        '"' + schtasks + '" /delete /tn "' + task_name + '" /f >nul 2>&1',
        'del /f /q "' + probe + '" >nul 2>&1',
        'del /f /q "%~f0" >nul 2>&1',
    ]
    return "\r\n".join(lines) + "\r\n"


def _launch_installer(installer_path, ui_port=None):
    """启动 installer，返回 {"mode": ..., "pid": ...}；抛 OSError 表示全都失败。

    v2.0.4.2 修（真机证据见文件头注释）：优先用**任务计划程序**拉起包装脚本 ——
    installer 由 Task Scheduler 服务托管，不在 nssm 的 Job Object 里，所以
    setup.iss 在 ssInstall 阶段 `nssm stop` 服务时不会把它连带杀死。
    任务计划程序不可用时才退回旧的 DETACHED_PROCESS 直启（保持旧行为）。

    v2.0.10.0：顺带把健康探测脚本写到 %TEMP%（执行器在 installer 跑完后用它探
    `/api/health`）。写失败不致命 —— 执行器会落一行 `health=SKIP` 退回旧判据。
    """
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

    try:
        with open(wrapper, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(_build_update_wrapper(installer_path, BASE_DIR, log_path, rc_path, UPDATE_TASK_NAME,
                                          probe_path=probe, ui_port=port))
    except OSError as exc:
        _log_upgrade("WARN", "写升级执行器失败：{}（退回直启）".format(exc))
        wrapper = None

    if wrapper:
        try:
            create = subprocess.run(
                [SCHTASKS_PATH, "/create", "/tn", UPDATE_TASK_NAME, "/tr", wrapper,
                 "/sc", "once", "/st", "00:00", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f"],
                timeout=20,
                check=False,
            )
            if create.returncode == 0:
                run = subprocess.run(
                    [SCHTASKS_PATH, "/run", "/tn", UPDATE_TASK_NAME],
                    timeout=20,
                    check=False,
                )
                if run.returncode == 0:
                    return {"mode": "schtasks", "pid": None}
                _log_upgrade("WARN", "schtasks /run 返回 {}（退回直启）".format(run.returncode))
            else:
                _log_upgrade("WARN", "schtasks /create 返回 {}（退回直启）".format(create.returncode))
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log_upgrade("WARN", "任务计划程序拉起失败：{}（退回直启）".format(exc))

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
        return {"ok": False, "error": "升级正在进行中"}
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

        # 2c. GitHub API 可达
        latest = _check_github_latest()
        if latest is None:
            msg = "GitHub releases/latest 不可达或返回异常"
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}
        remote_ver, asset_url, digest, asset_size, published = latest

        # P1-3：远端版本串白名单（防 `..\` 穿越拼进 %TEMP% 文件名）
        if not re.fullmatch(r"[0-9A-Za-z._-]{1,32}", remote_ver):
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

        # —— 4. 下载 ——
        _set_update_state(update_state="downloading", update_progress=0,
                          update_progress_message="下载安装器（{}）...".format(remote_ver))

        temp_dir = os.environ.get("TEMP", ".")
        installer_name = INSTALLER_FILENAME_PATTERN.format(ver=remote_ver)
        installer_path = os.path.join(temp_dir, installer_name)

        def _on_progress(done, total):
            pct = int(done * 100 / total) if total and total > 0 else 0
            _set_update_state(update_progress=pct, update_progress_message="下载中 {}%".format(pct))

        try:
            written = _download_installer(asset_url, installer_path, asset_size, _on_progress)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            msg = "下载失败：{}".format(exc)
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            return {"ok": False, "error": msg}

        _log_upgrade("INFO", "下载完成：{} 字节".format(written))

        # —— 5. 校验 SHA256（P0-2：fail-closed）——
        # 旧行为是 fail-open：digest 为 None 时**不做任何校验**直接执行安装器。
        # 现在拿不到 digest 就拒绝安装 —— 宁可不升级，也不执行来路不明的 exe。
        if not digest:
            msg = "远端未返回 digest，无法校验完整性，拒绝安装"
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", msg)
            try:
                os.remove(installer_path)
            except OSError:
                pass
            return {"ok": False, "error": msg}
        if not _verify_sha256(installer_path, digest):
            try:
                os.remove(installer_path)
            except OSError:
                pass
            msg = "SHA256 校验失败，已删除安装器"
            _set_update_state(update_state="error", update_progress=0, update_progress_message=msg)
            _log_upgrade("ERROR", "{}（期望 {}）".format(msg, digest[:16] + "..."))
            return {"ok": False, "error": msg}
        _log_upgrade("INFO", "SHA256 校验通过")

        # —— 6. 备份当前脚本 ——
        backup = _backup_service_py()
        if backup is None:
            msg = "备份联网_service.py 失败"
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
            launch = _launch_installer(installer_path, cfg.get("ui_port"))
            _log_upgrade("INFO", "installer 已启动（方式={}，PID={}，第 {} 次尝试；收尾将探 http://{}:{}{}）".format(
                launch.get("mode"), launch.get("pid"), prev_count + 1,
                HEALTH_PROBE_HOST, _normalize_ui_port(cfg.get("ui_port")), HEALTH_PROBE_PATH))
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
        _log_upgrade("INFO", "升级触发完成，Python 进程立即退出（installer 独立运行）")
        os._exit(0)

    finally:
        # 若走到这里说明流程在 installer 启动前失败 / 或 stop 没杀掉我们
        # 正常路径下 NSSM stop 会让我们在执行 finally 前被 SIGKILL
        _release_update_lock()


def _do_check_now():
    """只跑 GitHub 检查 + 状态更新，不升级。"""
    if not _acquire_update_lock():
        return {"ok": False, "error": "升级正在进行中"}
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
        cmp = _compare_versions(VERSION, remote_ver)
        if cmp is None or cmp >= 0:
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
# 自动升级后台线程
# ============================================================
def _auto_update_loop():
    """后台线程：按 update_check_interval_hours 周期检查 GitHub 新版。
    注意：auto_update_enabled=False 时不主动检查，但 manual / 启动钩子仍可触发。
    """
    logger.info("自动升级后台线程启动")
    # 首次启动延迟 30 秒（让服务先稳定 + Web UI 就绪）
    if STOP_EVENT.wait(30):
        return
    while not STOP_EVENT.is_set():
        try:
            cfg = _load_config()
            if not cfg.get("auto_update_enabled", True):
                logger.info("auto_update_enabled=False，30s 后重新检查开关")
                if STOP_EVENT.wait(30):
                    return
                continue
            interval_sec = int(cfg.get("update_check_interval_hours", 6)) * 3600
        except (OSError, ValueError) as exc:
            logger.warning("读取更新配置失败: %s", exc)
            if STOP_EVENT.wait(60):
                return
            continue

        # 检查（不升级）：如果发现新版，写 STATE 但不触发 do_update_now
        # 真正的升级由后台线程检测到 update_available=True 后启动
        # 但用户明确要"静默"→ 这里直接触发升级（无需 Web UI 介入）
        result = _do_check_now()
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
            _do_update_now()
            # 升级完大概率进程被杀；即使没被杀，也等下次循环
            if STOP_EVENT.wait(60):
                return
            continue

        if STOP_EVENT.wait(interval_sec):
            return


def _post_upgrade_startup():
    """启动钩子：检查是否刚升级过（对比当前脚本与备份），写日志 + 清理。"""
    try:
        backups = []
        for name in os.listdir(os.environ.get("TEMP", ".")):
            if name.startswith("drcom_backup_") and name.endswith(".py"):
                backups.append(name)
        backups.sort(reverse=True)  # 最新在前
        if not backups:
            return
        # 最新备份
        newest = os.path.join(os.environ.get("TEMP", "."), backups[0])
        if not os.path.isfile(newest):
            return
        # 对比 hash
        def _h(p):
            hh = hashlib.sha256()
            try:
                with open(p, "rb") as f:
                    for c in iter(lambda: f.read(DOWNLOAD_CHUNK_BYTES), b""):
                        hh.update(c)
            except OSError:
                return None
            return hh.hexdigest()
        current_hash = _h(os.path.join(BASE_DIR, "联网_service.py"))
        backup_hash = _h(newest)
        if current_hash and backup_hash and current_hash != backup_hash:
            # v2.0.4.2：**不再**据此宣告"升级完成"。备份是升级前随手落的，
            # 拿它比 hash 会把"任何一次脚本改动（含手动热补丁）"误判成"升级成功" ——
            # 真机上就出现过"绿 banner 说已升级，实际还是旧版本"（22:41 那次日志）。
            # 成功与否一律由下面的尝试记录（目标版本 vs 当前版本）判定。
            _log_upgrade("INFO", "启动钩子：联网_service.py 与最近备份不同（可能是升级，也可能只是本地改动）")
        else:
            _log_upgrade("INFO", "启动钩子：未检测到脚本变更")

        # 清理 7 天前的旧备份
        cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
        for name in backups[1:]:
            p = os.path.join(os.environ.get("TEMP", "."), name)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    _log_upgrade("INFO", "清理过期备份：{}".format(name))
            except OSError:
                pass

        # —— v2.0.4.0：用尝试记录确认"上次自动升级到底装上没有" ——
        # 之前只比对备份 hash，无法区分"同版本重装"与"根本没装上"，
        # 于是装不上时既没有告警、也没有熔断。
        attempt = _read_update_attempt()
        target = attempt.get("target")
        if target and attempt.get("state") != "success":
            count = int(attempt.get("count") or 1)
            if _compare_versions(VERSION, target) == 0:
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

        # —— v2.0.4.2：读升级执行器留下的结果（退出码 / 服务状态）并清掉残留 ——
        # v2.0.4.3：带 8 秒宽限 —— 服务是安装器在 ssPostInstall 拉起来的，
        # 那一刻执行器还在等 installer 退出（随后才写 rc），立刻读会读空。
        _report_update_runner_result(wait_sec=8)
        _cleanup_update_leftovers()

        # AppExit 策略归**安装器**所有（P0-7）：setup.iss / install.bat 统一设
        # `Default Ignore` + `0 Ignore`（防端口冲突时 NSSM 死循环重启）。
        # 这里**不再写回 Restart** —— 旧行为每次启动都把 v2.0.2.3 刚修的
        #「端口占用无限重启循环」修复原样撤销，且与 setup.iss 的 Ignore 打架。
        _log_upgrade("INFO", "AppExit 策略由安装器维持（Default Ignore / 0 Ignore），启动钩子不再覆盖")
        # v2.0.4.0 例外：AppExit 被写成**空串**时（NSSM 合法值里没有空值，会让
        # NSSM 每次启停都报 "Parameter AppExit requires a subparameter"）自愈回合法值。
        appexit = _ensure_nssm_appexit_sane()
        if appexit:
            _log_upgrade("INFO", "AppExit 现状：{}".format(appexit))

        # v2.0.6.2：托盘自启项自愈 —— 新任务没法靠旧版本的 /TASKS 传，
        # 只能由服务每次启动自己对齐（详见 _ensure_tray_autostart_sane 的注释）
        tray = _ensure_tray_autostart_sane()
        _log_upgrade("INFO", "托盘自启项：action={} want={} 现值={}".format(
            tray.get("action"), tray.get("want"), tray.get("current") or "不存在"))
    except Exception as exc:  # noqa: BLE001
        _log_upgrade("WARN", "启动钩子异常: {}".format(exc))


def _report_update_runner_result(wait_sec=0):
    """读升级执行器（任务计划程序里的 .cmd）落盘的结果，写进 upgrade.log。

    v2.0.4.2 新增；v2.0.4.3 加 `wait_sec` —— 服务是由安装器在 `ssPostInstall` 拉起来的，
    那一刻执行器还在等 installer 进程退出（随后才写 rc），立刻读会读空。
    所以发现执行器还在时，最多等 `wait_sec` 秒（只在刚升级完的那一次启动发生）。
    """
    rc = _rc_path()
    deadline = time.time() + max(0, int(wait_sec or 0))
    while not os.path.isfile(rc) and os.path.isfile(_wrapper_path()) and time.time() < deadline:
        time.sleep(1.0)
    if not os.path.isfile(rc):
        return
    try:
        with open(rc, "r", encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if l.strip()]
    except OSError:
        return
    if not lines:
        return
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
    # 2) 包装脚本 / 结果文件 / 健康探测脚本：只清陈旧的（刚跑完那次的交给执行器自己收尾）
    for p in (_wrapper_path(), _rc_path(), _health_probe_path()):
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
    # 4) 已经装上的安装包
    try:
        for name in os.listdir(_update_tmp_dir()):
            if not (name.startswith("DrcomAutoLogin-Setup-v") and name.endswith(".exe")):
                continue
            ver = name[len("DrcomAutoLogin-Setup-v"):-len(".exe")]
            if _compare_versions(VERSION, ver) == 0:
                try:
                    os.remove(os.path.join(_update_tmp_dir(), name))
                    _log_upgrade("INFO", "清理已安装的安装包：{}".format(name))
                except OSError:
                    pass
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
