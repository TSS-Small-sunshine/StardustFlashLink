# -*- coding: utf-8 -*-
"""web_api.py — HTTP 路由层（薄薄一层）。

职责范围：
    - HTTP 响应辅助（_send_json / _send_bytes / _read_json_body）
    - 所有 api_* 函数（HTTP 路由处理）
    - _Handler 类（do_GET / do_POST 调度）
    - _HTML_PAGE（单页应用，全部内联 CSS/JS）

设计：
    - 不持有业务逻辑；调用 protocol / auto_update / eula 模块
    - 与 main() 解耦：main() 通过 ThreadingHTTPServer((host, port), _Handler) 注入 handler
    - 共用共享资源（STATE / cfg / logger / 常量）通过 _attach() 由 联网_service.py 注入

依赖：仅 Python 3 标准库。
"""

import io
import json
import logging
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler

from version import VERSION, VERSION_FULL, CODENAME, CODENAME_CN
import eula as _eula_mod


# ============================================================
# 配置导入/导出常量
# ============================================================
CONFIG_EXPORT_SCHEMA_VERSION = 1
CONFIG_EXPORT_TOOL = "DrcomAutoLogin-Windows"
CONFIG_IMPORT_MAX_BYTES = 4 * 1024 * 1024  # 4MB 安全上限
# v2.0.14.0（P1-6）：**解压后**的体积也要限 —— 一个几百 KB 的 zip 可以解出几十 GB
# （zip 炸弹），只卡上传体积是拦不住的 ✗。
CONFIG_IMPORT_MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024    # 全部成员解压后之和
CONFIG_IMPORT_MAX_MEMBER_BYTES = 4 * 1024 * 1024          # 单个成员上限


# ============================================================
# 运行时引用（由 联网_service.py 在 import 时注入）
# ============================================================
def _attach(*, logger, run_lock, base_dir, log_file, log_dir,
            # 字典（直接挂到本模块 globals，API 代码用原名访问）
            state, state_lock, pwd_lock,
            config_file, password_file, upgrade_log_file,
            default_config,
            allowed_suffixes, allowed_intervals, allowed_update_intervals,
            # 函数（直接挂到本模块 globals，API 代码用原名访问）
            load_config, save_config, save_password_to_disk,
            validate_config, snapshot_state, get_password, now_iso,
            stop_event, run_once_fn,
            # 注入到 web_api 模块命名空间，标志 / 兼容别名
            eula_api_get_changelog=None,
            # auto_update 模块的可选注入（commit 5 才存在；现 commit 4 暂不引用）
            auto_update_mod=None,
            # metrics 模块的可选注入（v2.0.8.0 连接质量面板）
            metrics_mod=None,
            # profiles 模块的可选注入（v2.0.9.0 配置方案）
            profiles_mod=None):
    """由 联网_service.py 调用，注入共享对象到本模块命名空间。

    设计要点：
        - 把所有共享名字直接绑到本模块 globals，让原 API 函数体里的 STATE / STOP_EVENT
          / BASE_DIR / UPGRADE_LOG_FILE 等**裸名引用**无须重写即可工作。
        - 与 联网_service.py 的解耦只通过 _attach() 一个入口完成。
    """
    g = globals()
    # 日志
    g["logger"] = logger
    # 字典与锁
    g["STATE"] = state
    g["STATE_LOCK"] = state_lock
    g["BACKOFF"] = state.get  # 占位，web_api 不直接读 BACKOFF
    g["RUN_LOCK"] = run_lock
    g["PWD_LOCK"] = pwd_lock
    # 路径常量
    g["BASE_DIR"] = base_dir
    g["CONFIG_FILE"] = config_file
    g["PASSWORD_FILE"] = password_file
    g["LOG_FILE"] = log_file
    g["LOG_DIR"] = log_dir
    g["UPGRADE_LOG_FILE"] = upgrade_log_file
    # 字典常量
    g["DEFAULT_CONFIG"] = default_config
    g["ALLOWED_SUFFIXES"] = allowed_suffixes
    g["ALLOWED_INTERVALS"] = allowed_intervals
    g["ALLOWED_UPDATE_INTERVALS"] = allowed_update_intervals
    # 函数（保持原名以便 API 函数体无须改写）
    g["_load_config"] = load_config
    g["_save_config"] = save_config
    g["_save_password_to_disk"] = save_password_to_disk
    g["_validate_config"] = validate_config
    g["_snapshot_state"] = snapshot_state
    g["_get_password"] = get_password
    g["_now_iso"] = now_iso
    g["STOP_EVENT"] = stop_event
    g["run_once"] = run_once_fn
    # eula 模块的 api_get_changelog
    if eula_api_get_changelog is None:
        from eula import api_get_changelog as eula_api_get_changelog  # noqa: E402
    g["api_get_changelog"] = eula_api_get_changelog
    # auto_update 模块（v2.0.2 解耦后注入到 _auto_update_mod; 调用其函数请用 _auto_update_mod._func_name()）
    g["_auto_update_mod"] = auto_update_mod
    # metrics 模块（v2.0.8.0 连接质量面板；调用走 _metrics_mod.api_get_metrics()）
    if metrics_mod is None:
        try:
            import metrics as metrics_mod  # noqa: E402
        except ImportError:
            metrics_mod = None
    g["_metrics_mod"] = metrics_mod
    # profiles 模块（v2.0.9.0 配置方案；调用走 _profiles_mod.*）
    if profiles_mod is None:
        try:
            import profiles as profiles_mod  # noqa: E402
        except ImportError:
            profiles_mod = None
    g["_profiles_mod"] = profiles_mod


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


# ============================================================
# 原 Web API / _Handler（从 联网_service.py 逐字搬入）
# ============================================================
# ============================================================
# 安全响应头 + 请求体上限（v2.0.13.0 / P3-6）
# ============================================================
# Web UI 是**全内联**页面（无 CDN / 无外部字体 / 无第三方脚本），所以 CSP 可以收得很紧：
#   default-src 'none'                   默认什么都不许
#   script-src / style-src 'unsafe-inline'  页面就是内联的（唯一的放宽点）
#   img-src 'self' data:                 /branding/* 与内联小图标
#   connect-src 'self'                   fetch 只打本机
#   base-uri / form-action 'none'        禁基址注入、禁表单外发
#   frame-ancestors 'none'               禁被任何页面嵌框（配合 X-Frame-Options: DENY）
# JSON / zip 响应带这些头同样无害，所以统一加，不搞两套 ✓。
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "geolocation=(), camera=(), microphone=()"),
    ("Content-Security-Policy",
     "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
     "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
     "frame-ancestors 'none'"),
)
MAX_JSON_BODY_BYTES = 1024 * 1024        # JSON 端点 1 MB 上限（zip 导入另有 CONFIG_IMPORT_MAX_BYTES）
BODY_DRAIN_MAX_BYTES = 4 * 1024 * 1024   # 拒收时最多「排空」这么多字节（见 _drain_body 的注释）


def _add_security_headers(handler):
    for _name, _value in SECURITY_HEADERS:
        handler.send_header(_name, _value)


def _drain_body(handler, length, cap=BODY_DRAIN_MAX_BYTES):
    """拒收请求体前，把它先读掉（最多 cap 字节）。

    为什么必须读掉：Windows 上**带着未读数据关连接会发 RST**，那个 RST 会把刚写出去的
    响应本身一起冲掉 ✗ —— 表现就是「明明该收到 413，客户端却报连接被重置」。
    有界排空（cap）保证既不会把资源交出去，也能让 413 稳稳送达 ✓；
    超出 cap 的荒唐体积直接放弃（那已经不是正常客户端了）。
    """
    remaining = min(int(length or 0), cap)
    while remaining > 0:
        try:
            chunk = handler.rfile.read(min(65536, remaining))
        except OSError:
            return
        if not chunk:
            return
        remaining -= len(chunk)


def _send_json(handler, status, payload, close=False):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if close:
        handler.send_header("Connection", "close")   # 顺带把 close_connection 置上 ✓
    _add_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _send_bytes(handler, status, content_type, body, filename=None):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    if filename:
        handler.send_header("Content-Disposition", 'attachment; filename="{}"'.format(filename))
    handler.send_header("Cache-Control", "no-store")
    _add_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError("请求体过大（上限 {} 字节）".format(MAX_JSON_BODY_BYTES))
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("请求体不是合法 JSON")


def api_get_profiles():
    """GET /api/profiles — 配置方案列表（方案 = 位置相关字段的快照）。"""
    mod = globals().get("_profiles_mod")
    if mod is None:
        return {"ok": False, "error": "方案模块未就绪", "items": []}
    try:
        return mod.list_profiles()
    except Exception as exc:  # noqa: BLE001 —— 面板坏了不影响配置页其它部分
        logger.exception("读取配置方案失败: %s", exc)
        return {"ok": False, "error": "读取方案失败：{}".format(exc), "items": []}


def api_post_profiles_save(payload):
    """POST /api/profiles/save — {name, match_ssids?, values?}。

    不给 `values` 时 = **把当前配置存成方案**（最自然的用法：调好配置 → 存成「教室」）。
    """
    mod = globals().get("_profiles_mod")
    if mod is None:
        return 503, {"ok": False, "error": "方案模块未就绪"}
    payload = payload if isinstance(payload, dict) else {}
    values = payload.get("values")
    if not isinstance(values, dict):
        try:
            values = mod.snapshot(_load_config())
        except Exception as exc:  # noqa: BLE001
            return 400, {"ok": False, "error": "读取当前配置失败：{}".format(exc)}
    result = mod.save_profile(payload.get("name"), values, payload.get("match_ssids"))
    return (200 if result.get("ok") else 400), result


def api_post_profiles_activate(payload):
    """POST /api/profiles/activate — {name}：应用方案（先全量校验，再写盘）。"""
    mod = globals().get("_profiles_mod")
    if mod is None:
        return 503, {"ok": False, "error": "方案模块未就绪"}
    payload = payload if isinstance(payload, dict) else {}
    result = mod.activate(payload.get("name"))
    return (200 if result.get("ok") else 400), result


def api_post_profiles_delete(payload):
    """POST /api/profiles/delete — {name}：删方案（删当前方案只清标记，配置值不动）。"""
    mod = globals().get("_profiles_mod")
    if mod is None:
        return 503, {"ok": False, "error": "方案模块未就绪"}
    payload = payload if isinstance(payload, dict) else {}
    result = mod.remove(payload.get("name"))
    return (200 if result.get("ok") else 400), result


def api_post_profiles_auto(payload):
    """POST /api/profiles/auto — {enabled}：按 Wi-Fi 名自动切换方案的总开关。"""
    payload = payload if isinstance(payload, dict) else {}
    if not isinstance(payload.get("enabled"), bool):
        return 400, {"ok": False, "error": "enabled 必须是布尔值"}
    try:
        cfg = dict(_load_config() or {})
        cfg["profiles_auto_switch"] = payload["enabled"]
        errors = _validate_config(cfg)
        if errors:
            return 400, {"ok": False, "error": "；".join(errors)}
        _save_config(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("切换自动方案开关失败: %s", exc)
        return 500, {"ok": False, "error": "保存失败：{}".format(exc)}
    return 200, {"ok": True, "auto_switch": payload["enabled"]}


def api_get_status():
    return _snapshot_state()


def api_get_metrics(days=7):
    """GET /api/metrics — 连接质量（近 N 天）：从 logs/campus_login.log 现算。

    刻意不落新状态文件：日志本来就在记录每个检查周期的走向，重启不清零、升级不丢。
    面板属于「锦上添花」—— 日志被删、模块没注入、统计炸了，都只回一份空指标，
    绝不把状态页打成 500。
    """
    mod = globals().get("_metrics_mod")
    if mod is None:
        return {"ok": False, "error": "统计模块未就绪", "checks": 0, "series": []}
    try:
        data = mod.api_get_metrics(days=days)
    except Exception as exc:  # noqa: BLE001 —— 面板崩了不该影响状态页
        logger.exception("连接质量统计失败: %s", exc)
        return {"ok": False, "error": "统计失败：{}".format(exc), "checks": 0, "series": []}
    data["ok"] = True
    return data


def api_get_config():
    cfg = _load_config()
    cfg = {k: cfg[k] for k in DEFAULT_CONFIG if k in cfg}
    # ⚠️ 不要在这里套 `with PWD_LOCK:` —— _get_password() 内部已经加锁了。
    # PWD_LOCK 是不可重入的 threading.Lock，同一线程二次获取 = 永久死锁：
    #   GET /api/config 会挂住不返回（配置页字段全空、徽标「状态未知」、保存请求也一起卡住），
    #   而且周期性自检线程同样卡在 _get_password() 上 → 自动登录停摆。
    # v1.x 单文件版这里是直接读 _PWD_VALUE；模块化拆分后成了回归，v2.0.4.1 修。
    pwd_set = _get_password() is not None
    return {
        **cfg,
        "password_status": "set" if pwd_set else "missing",
    }


def api_post_config(payload):
    try:
        cfg_in = _read_json_body_safe(payload)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    if not isinstance(cfg_in, dict):
        return 400, {"ok": False, "error": "请求体必须是 JSON 对象"}
    try:
        _save_config(cfg_in)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    except OSError as exc:
        logger.error("写 config.json 失败: %s", exc)
        return 500, {"ok": False, "error": "写文件失败"}
    logger.info("配置已更新")
    return 200, {"ok": True}


def _read_json_body_safe(payload):
    """payload 已是 dict（do_POST 已解析）。重复保险：再校验一次。"""
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return payload


def api_post_password(payload):
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "请求体必须是 JSON 对象"}
    pwd = payload.get("password")
    if not isinstance(pwd, str) or len(pwd) < 1:
        return 400, {"ok": False, "error": "password 必须是非空字符串"}
    try:
        _save_password_to_disk(pwd)
    except (OSError, ValueError) as exc:
        logger.error("写 password.txt 失败: %s", exc)
        return 500, {"ok": False, "error": "写文件失败"}
    logger.info("密码已更新")
    return 200, {"ok": True}


def api_post_login():
    """异步触发 run_once('manual')。"""
    with STATE_LOCK:
        if STATE["login_in_progress"]:
            return 200, {"triggered": False, "reason": "already_in_progress"}
    t = threading.Thread(target=run_once, args=("manual",), daemon=True)
    t.start()
    return 200, {"triggered": True}


# —— 日志等级正则：从行内提取 [LEVEL] 前缀（用于按等级过滤 / 配色） ——
_LOG_LEVEL_RE = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")
_VALID_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


def _parse_log_level(line):
    """从日志行提取等级；不识别返回 None。"""
    if not isinstance(line, str):
        return None
    m = _LOG_LEVEL_RE.search(line)
    return m.group(1).lower() if m else None


def api_get_log_tail(offset, max_lines, level=None):
    """返回日志尾部。

    level: None/空 = 不过滤；否则必须是 debug/info/warning/error/critical 之一。
    返回 {"lines": [...], "next_offset": int, "total_size": int, "level_filter": str|None,
          "error"?: str}；非法 level 通过 raise ValueError 让上层 do_GET 返 400。
    """
    offset = max(0, int(offset or 0))
    max_lines = max(1, min(int(max_lines or 200), 5000))
    level_filter = None
    if level is not None:
        level_str = str(level).strip().lower()
        if level_str:
            if level_str not in _VALID_LOG_LEVELS:
                raise ValueError("level 必须是 {} 之一".format("/".join(_VALID_LOG_LEVELS)))
            level_filter = level_str
    if not os.path.isfile(LOG_FILE):
        return {"lines": [], "next_offset": 0, "total_size": 0, "level_filter": level_filter}
    total = os.path.getsize(LOG_FILE)
    if offset >= total:
        return {"lines": [], "next_offset": total, "total_size": total, "level_filter": level_filter}
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError as exc:
        return {"lines": [], "next_offset": offset, "total_size": total, "error": str(exc), "level_filter": level_filter}
    # 拆分行为保留最后 max_lines 行
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = data.decode("latin-1", errors="replace")
    lines = text.splitlines()
    if level_filter:
        lines = [ln for ln in lines if _parse_log_level(ln) == level_filter]
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        next_offset = total  # 已截断，next_offset 标记为文件末尾
    else:
        next_offset = offset + len(data)
    return {"lines": lines, "next_offset": next_offset, "total_size": total, "level_filter": level_filter}


def api_get_log_file_path():
    return {"path": LOG_FILE}


def api_get_log_download():
    if not os.path.isfile(LOG_FILE):
        return None, b""
    try:
        with open(LOG_FILE, "rb") as f:
            data = f.read()
    except OSError:
        return None, b""
    return "campus_login_{}.log".format(datetime.now().strftime("%Y%m%d_%H%M%S")), data


def api_get_about():
    s = _snapshot_state()
    return {
        "version": VERSION,
        "version_full": VERSION_FULL,
        "codename": CODENAME,
        "codename_cn": CODENAME_CN,
        "service_started_at": s.get("service_started_at"),
        "service_uptime_sec": s.get("service_uptime_sec"),
        "data_dir": BASE_DIR,
        "log_file": LOG_FILE,
        "config_file": CONFIG_FILE,
        "password_file": PASSWORD_FILE,
        "log_dir": str(LOG_DIR),
    }


def api_get_changelog():
    """CHANGELOG IO 已迁移到 eula.py（v2.0.2 解耦）。
    
    此函数由 _Handler.do_GET 调用，保留为薄壳以减少 do_GET 路由改动。
    commit 4 抽出 web_api.py 时将整体迁移。
    """
    return _eula_mod.api_get_changelog()


def _restart_service_soon():
    """稍后重启服务：优先 `nssm restart DrcomAutoLogin`，否则退回 `os._exit(0)`。

    P0-7：AppExit 策略是 `Ignore`（v2.0.2.3 起，防端口冲突时 NSSM 死循环重启），
    因此 `os._exit(0)` 之后 NSSM **不会**再拉起 —— 点「重启服务」会变成永久停机。
    v2.0.11.0 从 `api_post_restart` 里抽出来，让「回滚」也能复用同一条重启路径。
    """
    def _delayed_restart():
        nssm = os.path.join(BASE_DIR, "tools", "nssm.exe")
        if os.path.isfile(nssm):
            try:
                proc = subprocess.run([nssm, "restart", "DrcomAutoLogin"], timeout=30, check=False)
                if proc.returncode == 0:
                    logger.info("已通过 nssm restart 重启服务")
                    return
                logger.warning("nssm restart 返回码 %s，退回 os._exit(0)", proc.returncode)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("nssm restart 失败: %s，退回 os._exit(0)", exc)
        else:
            logger.warning("找不到 %s，退回 os._exit(0)", nssm)
        STOP_EVENT.set()
        time.sleep(0.3)
        os._exit(0)
    threading.Thread(target=_delayed_restart, daemon=True).start()


def api_post_restart(handler):
    """重启服务（P0-7）。"""
    _restart_service_soon()
    return 200, {"ok": True, "message": "服务正在重启"}


# ============================================================
# 版本回滚 API（v2.0.11.0 / P6-6）
# ============================================================
def api_get_rollback():
    """GET /api/rollback — 可回滚的版本列表（薄壳，业务逻辑在 auto_update）。"""
    try:
        return _auto_update_mod.api_get_rollback()
    except Exception as exc:  # noqa: BLE001  面板坏了不该拖垮配置页
        logger.exception("读取回滚备份失败: %s", exc)
        return 500, {"ok": False, "error": "读取备份列表失败：{}".format(exc), "backups": []}


def api_post_rollback(payload):
    """POST /api/rollback — {version?}：把代码回滚到备份版本，成功后重启服务。

    只还原代码文件（配置 / 密码不动）；回滚成功必须重启 —— 当前进程内存里还是新代码。
    """
    status, body = _auto_update_mod.api_post_rollback(payload)
    if status == 200 and isinstance(body, dict) and body.get("ok"):
        _restart_service_soon()
    return status, body


# ============================================================
# 自动升级 API（v1.3 新增）
# ============================================================
def api_get_update_status():
    """GET /api/update/status — 当前升级状态快照。"""
    _auto_update_mod._schedule_success_clear()  # 顺手清理过期绿 banner（v2.0.2.3.2 fix: 解耦后跨模块调用走 _auto_update_mod 注入）
    cfg = _load_config()
    snap = _snapshot_state()
    return {
        "enabled": bool(cfg.get("auto_update_enabled", True)),
        "local_version": VERSION,
        "latest_version": snap.get("update_latest_version"),
        "latest_url": snap.get("update_latest_url"),
        "update_available": bool(snap.get("update_available")),
        "state": snap.get("update_state"),
        "progress_pct": int(snap.get("update_progress") or 0),
        "progress_message": snap.get("update_progress_message") or "",
        "last_check_at": snap.get("update_last_check_at"),
        "last_error": snap.get("update_last_error"),
        "target_version": snap.get("update_target_version"),
        "check_interval_hours": int(cfg.get("update_check_interval_hours", 6)),
        "min_free_disk_mb": int(cfg.get("update_min_free_disk_mb", 200)),
    }


def api_post_update_check(payload):
    """POST /api/update/check — 立即触发一次 GitHub 检查（不等后台线程）。"""
    # v2.0.6.3：先看有没有任务在跑 —— 否则后台线程会被 _acquire_update_lock 挡掉，
    # 而 HTTP 早已回了「已提交检查任务」，用户看到成功、实际什么都没发生（假成功）
    _busy = _auto_update_mod.update_busy_message()
    if _busy:
        return 409, {"ok": False, "error": "任务进行中（{}），请稍候再试".format(_busy)}
    # 异步执行，避免阻塞 HTTP 响应
    # P0-1：解耦后跨模块调用必须走 _auto_update_mod（原裸名 _do_check_now 会 NameError）
    def _runner():
        try:
            _auto_update_mod._do_check_now()
        except Exception as exc:  # noqa: BLE001
            _auto_update_mod._log_upgrade("ERROR", "手动检查异常: {}".format(exc))
    threading.Thread(target=_runner, daemon=True).start()
    return 200, {"ok": True, "message": "已提交检查任务"}


def api_post_update_install(payload):
    """POST /api/update/install — 立即开始升级。"""
    # v2.0.6.3：同上 —— 升级锁被占时直接说清「谁在跑」，不再先回「已提交」再静默丢弃
    _busy = _auto_update_mod.update_busy_message()
    if _busy:
        return 409, {"ok": False, "error": "任务进行中（{}），请稍候再试".format(_busy)}
    # 异步执行完整升级流程（耗时较长，HTTP 先返回）
    # P0-1：同上，_do_update_now / _log_upgrade / _set_update_state 全走 _auto_update_mod
    def _runner():
        try:
            _auto_update_mod._do_update_now()
        except Exception as exc:  # noqa: BLE001
            _auto_update_mod._log_upgrade("ERROR", "手动升级异常: {}".format(exc))
            _auto_update_mod._set_update_state(
                update_state="error", update_progress=0,
                update_progress_message="升级异常：{}".format(exc),
                update_last_error=str(exc))
    threading.Thread(target=_runner, daemon=True).start()
    return 200, {"ok": True, "message": "已提交升级任务"}


def api_post_update_toggle(payload):
    """POST /api/update/toggle — 切换 auto_update_enabled（payload: {enabled}）。"""
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "请求体必须是 JSON 对象"}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return 400, {"ok": False, "error": "enabled 必须是布尔值"}
    try:
        cfg = _load_config()
        cfg["auto_update_enabled"] = enabled
        _save_config(cfg)
    except (ValueError, OSError) as exc:
        return 400, {"ok": False, "error": str(exc)}
    _auto_update_mod._log_upgrade("INFO", "auto_update_enabled 改为 {}".format(enabled))
    return 200, {"ok": True, "auto_update_enabled": enabled}


def api_get_update_history():
    """GET /api/update/history — 返回 logs/upgrade.log 最后 UPGRADE_HISTORY_MAX_LINES 行。"""
    lines = []
    if os.path.isfile(UPGRADE_LOG_FILE):
        try:
            with open(UPGRADE_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            lines = [ln.rstrip("\r\n") for ln in all_lines[-_auto_update_mod.UPGRADE_HISTORY_MAX_LINES:]]
        except OSError as exc:
            return {"lines": [], "error": str(exc), "path": UPGRADE_LOG_FILE}
    return {"lines": lines, "path": UPGRADE_LOG_FILE}


# ============================================================
# 配置导入/导出（zip）
# ============================================================
def _build_config_export_zip():
    """把当前 config.json + manifest.json 打包成 bytes。

    P1-4：**不再**打包明文 password.txt —— README 承诺「接口永不回传密码原文」，
    之前这个端点直接证伪了该承诺。改为在 manifest 里记录密码是否已设置。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "schema_version": CONFIG_EXPORT_SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "service_version": VERSION,
            "tool": CONFIG_EXPORT_TOOL,
        }
        pwd_path = os.path.join(BASE_DIR, "password.txt")
        manifest["password_status"] = (
            "set" if os.path.isfile(pwd_path) and os.path.getsize(pwd_path) > 0 else "missing"
        )
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        config_path = os.path.join(BASE_DIR, "config.json")
        if os.path.isfile(config_path):
            with open(config_path, "rb") as f:
                zf.writestr("config.json", f.read())
    return buf.getvalue()


# ============================================================
# 日志与诊断（v2.0.12.0）
# ============================================================
_LOG_NAMES = ("campus_login.log", "upgrade.log", "service_stderr.log",
              "service_stdout.log", "installer-silent.log")
_LOG_ROTATE_HINT = {"campus_login.log": "5 MB × 3", "upgrade.log": "2 MB × 2"}   # 展示用（实际值在各自模块里）
DIAGNOSTIC_TAIL_BYTES = 512 * 1024        # 诊断包里每个日志最多带 512 KB 尾部
DIAGNOSTIC_MAX_FILES = 12
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}\b|\b[0-9A-Fa-f]{12}\b")


def _log_paths():
    """要展示 / 打包的日志文件（名字 → 路径），含轮转出来的 `.1`~`.3`。

    取不到 `LOG_DIR`（未经 _attach 注入，例如前端预览）时返回空表 ——
    面板少一块，也不该把整个页面打成 500 ✓。
    """
    log_dir = globals().get("LOG_DIR") or ""
    if not log_dir:
        return []
    out = []
    for name in _LOG_NAMES:
        p = os.path.join(log_dir, name)
        if os.path.isfile(p):
            out.append((name, p))
        for i in range(1, 4):
            q = "{}.{}".format(p, i)
            if os.path.isfile(q):
                out.append(("{}.{}".format(name, i), q))
    return out


def _human_size(n):
    """1536 → '1.5 KB'（面板用）。"""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{:.0f} {}".format(n, unit) if unit == "B" else "{:.1f} {}".format(n, unit)
        n /= 1024.0


def _mask_account(text, account):
    """账号 → 「前 4 位 + ******」（太短的账号整体打码）。"""
    if not text or not account:
        return text
    keep = account[:4] if len(account) > 6 else ""
    return text.replace(account, keep + "*" * max(4, len(account) - len(keep)))


def _desensitize(text, account="", password=""):
    """诊断包 / 日志展示前的脱敏（v2.0.12.0）。

    - 账号 → 「前 4 位 + ******」✓
    - 密码 → `***`（正常情况下日志里不该有；这是**兜底**，且它永远不会被写进包 ✓）
    - MAC → 前 4 位 + `********` ✓
    - **IP 保留**：校园网内网地址（172.16.x.x 那类网关）是排障必需 ——
      打包说明 `README.txt` 里会写明「可能含内网 IP 与 Wi-Fi 名」，由用户自己决定要不要外发 ✓
    """
    if not text:
        return text
    if account:
        text = _mask_account(text, account)
    if password:
        text = text.replace(password, "***")
    return _MAC_RE.sub(lambda m: m.group(0)[:4] + "*" * (len(m.group(0)) - 4), text)


def api_get_logs():
    """GET /api/logs — 日志清单与体积（只读，供「关于」面板显示占用）。"""
    items = []
    total = 0
    for name, path in _log_paths():
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        total += size
        items.append({
            "name": name,
            "size": size,
            "size_h": _human_size(size),
            "rotate_hint": _LOG_ROTATE_HINT.get(name.split(".log")[0] + ".log", ""),
        })
    return 200, {
        "ok": True,
        "dir": globals().get("LOG_DIR") or "",
        "total_size": total,
        "total_h": _human_size(total),
        "items": items,
    }


def _diagnostic_read(path, limit=DIAGNOSTIC_TAIL_BYTES):
    """读日志尾部（最多 limit 字节），按整行切开。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
                f.readline()          # 丢掉可能被切断的半行
            data = f.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _diagnostics_readme(summary):
    """诊断包里的 README.txt（告诉用户包里有什么、脱敏了什么）。"""
    return (
        "星尘闪连 (Stardust Flash Link) — 诊断包\r\n"
        "版本：{version}    生成时间：{generated}\r\n"
        "\r\n"
        "包含：\r\n"
        "  summary.json   版本 / 运行环境 / 服务状态 / 配置摘要（账号已打码）\r\n"
        "  config.json    当前配置（账号已打码；不含任何密码字段）\r\n"
        "  logs\\*         各日志的尾部（每个最多 512 KB；已轮转的 .1~.3 也在内）\r\n"
        "\r\n"
        "已脱敏：账号（前 4 位 + ******）、MAC 地址（前 4 位 + ********）、"
        "密码（万一出现在日志里会替换成 ***）。\r\n"
        "未脱敏：校园网内网 IP、Wi-Fi 名称 —— 它们是排障的关键线索，"
        "但对外分享前请自行确认。\r\n"
        "不包含：password.txt、任何密码明文。\r\n"
        "\r\n"
        "用途：反馈问题时把它发给维护者，比截图和口述都准。\r\n"
    ).format(version=summary.get("version") or "?", generated=summary.get("generated_at") or "?")


def _build_diagnostics_zip():
    """组装诊断包（内存 zip）：摘要 + 脱敏配置 + 各日志尾部 + README。

    每个依赖单独兜底（「面板坏了不该影响主功能」的同一原则）：
    取不到配置 / 密码 / 状态也照样出包，只是那几项为空 ✓。
    """
    try:
        cfg = _load_config() or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    account = str(cfg.get("account") or "")
    password = ""
    try:
        password = _get_password() or ""   # 只为「万一被记进日志」兜底；它本身永不出现在包里
    except Exception:  # noqa: BLE001
        password = ""
    try:
        snap = _snapshot_state()
    except Exception:  # noqa: BLE001
        snap = {}
    masked_cfg = dict(cfg)
    if account:
        masked_cfg["account"] = _mask_account(account, account)
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "version_full": VERSION_FULL,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": platform.node(),
        "service": {
            "started_at": snap.get("service_started_at"),
            "uptime_sec": snap.get("service_uptime_sec"),
            "last_error": snap.get("last_error"),
            "current_ssid": snap.get("current_ssid"),
        },
        "update": {
            "state": snap.get("update_state"),
            "last_check_at": snap.get("update_last_check_at"),
            "last_error": snap.get("update_last_error"),
        },
        "has_password": bool(password),
        "config": masked_cfg,
        "logs": [{"name": name, "size_h": _human_size(os.path.getsize(path))}
                 for name, path in _log_paths()],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _diagnostics_readme(summary))
        zf.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        zf.writestr("config.json", json.dumps(masked_cfg, ensure_ascii=False, indent=2))
        for name, path in _log_paths()[:DIAGNOSTIC_MAX_FILES]:
            zf.writestr("logs/{}".format(name),
                        _desensitize(_diagnostic_read(path), account, password))
    return buf.getvalue()


def api_get_diagnostics(handler):
    """GET /api/diagnostics — 下载脱敏诊断包（zip）。"""
    try:
        data = _build_diagnostics_zip()
    except (OSError, zipfile.BadZipFile) as exc:
        logger.exception("diagnostics failed: %s", exc)
        _send_json(handler, 500, {"ok": False, "error": "生成诊断包失败：{}".format(exc)})
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    _send_bytes(handler, 200, "application/zip", data,
                filename="drcom-diagnostics-v{}-{}.zip".format(VERSION, ts))


def api_get_config_export(handler):
    """GET /api/config/export — 导出 zip。"""
    try:
        data = _build_config_export_zip()
    except (OSError, zipfile.BadZipFile) as exc:
        logger.exception("config export failed: %s", exc)
        _send_json(handler, 500, {"ok": False, "error": "export failed: {}".format(exc)})
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = "config-export-{}.zip".format(ts)
    _send_bytes(handler, 200, "application/zip", data, filename=fname)


def api_post_config_import(handler):
    """POST /api/config/import — 上传 zip 应用配置。"""
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        length = 0
    if length <= 0:
        _send_json(handler, 400, {"ok": False, "error": "empty body"})
        return
    if length > CONFIG_IMPORT_MAX_BYTES:
        _send_json(handler, 413, {"ok": False, "error": "body too large (>{} bytes)".format(CONFIG_IMPORT_MAX_BYTES)})
        return
    raw = handler.rfile.read(length)
    if not raw:
        _send_json(handler, 400, {"ok": False, "error": "empty body"})
        return
    applied = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            # v2.0.14.0（P1-6）：先看**解压后**的体积（zip 炸弹拦截）——在任何 read() 之前 ✓
            _total = 0
            for _info in zf.infolist():
                if _info.file_size > CONFIG_IMPORT_MAX_MEMBER_BYTES:
                    _send_json(handler, 413, {"ok": False, "error": "单个成员解压后过大（上限 {} 字节）：{}".format(
                        CONFIG_IMPORT_MAX_MEMBER_BYTES, _info.filename)})
                    return
                _total += _info.file_size
            if _total > CONFIG_IMPORT_MAX_UNCOMPRESSED_BYTES:
                _send_json(handler, 413, {"ok": False, "error": "解压后总体积过大（上限 {} 字节）：{}".format(
                    CONFIG_IMPORT_MAX_UNCOMPRESSED_BYTES, _total)})
                return
            if "manifest.json" not in names:
                _send_json(handler, 400, {"ok": False, "error": "missing manifest.json"})
                return
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            # v2.0.14.0（P1-6）：`schema_version` 可能是任何 JSON 类型 —— 旧实现直接 int()，
            # 传 `"abc"` / `[]` 会抛 TypeError/ValueError → 500（本该是 400 的「参数不合法」✗）
            try:
                _schema = int(manifest.get("schema_version", -1))
            except (TypeError, ValueError):
                _send_json(handler, 400, {"ok": False, "error": "schema_version 不是整数: {!r}".format(
                    manifest.get("schema_version"))})
                return
            if _schema != CONFIG_EXPORT_SCHEMA_VERSION:
                _send_json(handler, 400, {"ok": False, "error": "unsupported schema_version: {}".format(manifest.get("schema_version"))})
                return
            if "config.json" not in names:
                _send_json(handler, 400, {"ok": False, "error": "missing config.json"})
                return
            cfg_text = zf.read("config.json").decode("utf-8")
            cfg_data = json.loads(cfg_text)
            errors = _validate_config(cfg_data)
            if errors:
                _send_json(handler, 400, {"ok": False, "error": "config 校验失败: " + "; ".join(errors)})
                return
            _save_config(cfg_data)
            applied.append("config")
            if "password.txt" in names:
                pwd_bytes = zf.read("password.txt")
                if pwd_bytes.strip():
                    pwd_path = os.path.join(BASE_DIR, "password.txt")
                    # v2.0.4.5：tmp 名带 pid（同 _save_password_to_disk），避免并发写交错
                    tmp = "{}.{}.tmp".format(pwd_path, os.getpid())
                    with open(tmp, "wb") as f:
                        f.write(pwd_bytes)
                    os.replace(tmp, pwd_path)
                    applied.append("password")
                else:
                    logger.warning("import password.txt is empty, skipped")
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError,
            ValueError, TypeError, KeyError) as exc:
        # v2.0.14.0（P1-6）：把 ValueError / TypeError / KeyError 一起收进来 ——
        # zip 里塞任何奇怪类型的字段都不该变成 500（参数不合法就是 400）✓
        logger.exception("config import failed: %s", exc)
        _send_json(handler, 400, {"ok": False, "error": "import failed: {}".format(exc)})
        return
    _send_json(handler, 200, {"ok": True, "applied": applied, "need_restart": True})


# ============================================================
# HTTP Handler
# ============================================================
class _Handler(BaseHTTPRequestHandler):
    server_version = "DrcomAutoLogin/{}".format(VERSION)

    # 静默 BaseHTTPRequestHandler 默认的 stderr 访问日志（避免污染 service_stderr.log）
    def log_message(self, format, *args):
        pass

    def _parse_url(self):
        if "?" in self.path:
            path, qs = self.path.split("?", 1)
        else:
            path, qs = self.path, ""
        params = {}
        for kv in qs.split("&"):
            if not kv:
                continue
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    params[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
                except Exception:  # noqa: BLE001
                    params[k] = v
        return path, params

    def _check_request(self):
        """P1-1：Host 白名单 + 写请求自定义头 + Origin 同源校验。返回 True = 放行。

        - Host 校验挡 DNS rebinding（攻击者域名解析到 127.0.0.1 后绕过同源）；
        - 写请求要求 `X-Requested-With: DrcomUI`（页面内脚本自动带），
          跨站页面无法伪造该头 —— 非简单请求会触发预检，而服务端不返回任何
          CORS 允许头，因此纯 CSRF 被阻断；
        - Origin 存在时必须是本机同源。
        """
        port = self.server.server_address[1]
        allowed_hosts = ("127.0.0.1:%d" % port, "localhost:%d" % port)
        host = self.headers.get("Host", "")
        if host not in allowed_hosts:
            _log("拒绝非法 Host 请求：%r（%s）", host, self.path, level=logging.WARNING)
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        if self.command == "POST":
            if self.headers.get("X-Requested-With") != "DrcomUI":
                _log("拒绝缺少 X-Requested-With 的写请求：%s", self.path, level=logging.WARNING)
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            origin = self.headers.get("Origin")
            allowed_origins = ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port)
            if origin and origin not in allowed_origins:
                _log("拒绝跨源写请求：Origin=%r（%s）", origin, self.path, level=logging.WARNING)
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
        return True

    def _serve_html(self):
        """返回内嵌的 SPA HTML 页面（注入写请求头包装脚本）。"""
        body = _HTML_PAGE.replace("<head>", "<head>\n" + _FETCH_HEADER_JS, 1).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        _add_security_headers(self)          # v2.0.13.0：CSP 等（页面全内联，可收得很紧）
        self.end_headers()
        self.wfile.write(body)

    def _serve_branding(self, path):
        """GET /branding/<name> —— 只服务图片类扩展名的白名单文件名。"""
        name = path[len("/branding/"):]
        if not _BRANDING_NAME_RE.match(name):
            self._send_empty(404)
            return
        target = None
        for folder in _branding_dirs():
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                target = candidate
                break
        if target is None:
            self._send_empty(404)
            return
        try:
            with open(target, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send_empty(404)
            return
        ctype = _BRANDING_CTYPES.get(os.path.splitext(name)[1].lower(),
                                     "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._check_request():
            return
        path, params = self._parse_url()
        try:
            if path == "/" or path == "/index.html":
                self._serve_html()
                return
            if path.startswith("/branding/"):
                self._serve_branding(path)
                return
            if path == "/api/status":
                _send_json(self, 200, api_get_status())
                return
            if path == "/api/config":
                _send_json(self, 200, api_get_config())
                return
            if path == "/api/log_tail":
                offset = params.get("offset", "0")
                maxn = params.get("max", "200")
                level = params.get("level", "")
                try:
                    payload = api_get_log_tail(offset, maxn, level)
                except ValueError as exc:
                    _send_json(self, 400, {"error": str(exc)})
                    return
                _send_json(self, 200, payload)
                return
            if path == "/api/log_file_path":
                _send_json(self, 200, api_get_log_file_path())
                return
            if path == "/api/log_download":
                fname, data = api_get_log_download()
                if fname is None:
                    _send_json(self, 404, {"error": "log file not found"})
                else:
                    _send_bytes(self, 200, "text/plain; charset=utf-8", data, filename=fname)
                return
            if path == "/api/about":
                _send_json(self, 200, api_get_about())
                return
            # —— 更新日志（v2.0.0 新增）——
            if path == "/api/changelog":
                status, body = api_get_changelog()
                _send_json(self, status, body)
                return
            if path == "/api/health":
                _send_json(self, 200, {"ok": True, "version": VERSION})
                return
            # —— 自动升级（v1.3 新增）——
            if path == "/api/update/status":
                _send_json(self, 200, api_get_update_status())
                return
            if path == "/api/update/history":
                _send_json(self, 200, api_get_update_history())
                return
            if path == "/api/rollback":
                _status, _body = api_get_rollback()
                _send_json(self, _status, _body)
                return
            if path == "/api/logs":
                _status, _body = api_get_logs()
                _send_json(self, _status, _body)
                return
            if path == "/api/diagnostics":
                api_get_diagnostics(self)      # 直接回 zip 字节流（Content-Disposition: attachment）
                return
            if path == "/api/metrics":
                _days = 7
                if "days=" in self.path:
                    try:
                        _days = int(self.path.split("days=", 1)[1].split("&", 1)[0])
                    except (TypeError, ValueError, IndexError):
                        _days = 7
                _send_json(self, 200, api_get_metrics(_days))
                return
            # —— 配置方案（v2.0.9.0 / B5）——
            if path == "/api/profiles":
                _send_json(self, 200, api_get_profiles())
                return
            # —— 配置导入/导出（zip）——
            if path == "/api/config/export":
                api_get_config_export(self)
                return
            _send_json(self, 404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("GET %s 异常: %s", path, exc)
            _send_json(self, 500, {"error": "internal: {}".format(exc)})

    def do_POST(self):
        if not self._check_request():
            return
        path, _params = self._parse_url()
        # —— 二进制上传（zip）在 JSON 解析之前专路分发 —— 现有 JSON 类端点行为零变更
        if path == "/api/config/import":
            try:
                api_post_config_import(self)
            except Exception as exc:  # noqa: BLE001
                logger.exception("POST %s 异常: %s", path, exc)
                _send_json(self, 500, {"ok": False, "error": "internal: {}".format(exc)})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length > MAX_JSON_BODY_BYTES:      # v2.0.13.0：JSON 端点也有体积上限
            _drain_body(self, length)         # 先排空（有界）再拒，否则响应会被 RST 冲掉
            _send_json(self, 413, {"ok": False,
                                   "error": "请求体过大（上限 {} 字节）".format(MAX_JSON_BODY_BYTES)},
                       close=True)
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            _send_json(self, 400, {"ok": False, "error": "请求体不是合法 JSON"})
            return

        try:
            if path == "/api/login":
                status, body = api_post_login()
                _send_json(self, status, body)
                return
            if path == "/api/config":
                status, body = api_post_config(payload)
                _send_json(self, status, body)
                return
            if path == "/api/password":
                status, body = api_post_password(payload)
                _send_json(self, status, body)
                return
            if path == "/api/restart":
                status, body = api_post_restart(self)
                _send_json(self, status, body)
                return
            # —— 自动升级（v1.3 新增）——
            if path == "/api/update/check":
                status, body = api_post_update_check(payload)
                _send_json(self, status, body)
                return
            if path == "/api/update/install":
                status, body = api_post_update_install(payload)
                _send_json(self, status, body)
                return
            if path == "/api/update/toggle":
                status, body = api_post_update_toggle(payload)
                _send_json(self, status, body)
                return
            if path == "/api/rollback":
                status, body = api_post_rollback(payload)
                _send_json(self, status, body)
                return
            # —— 配置方案（v2.0.9.0 / B5）——
            if path == "/api/profiles/save":
                status, body = api_post_profiles_save(payload)
                _send_json(self, status, body)
                return
            if path == "/api/profiles/activate":
                status, body = api_post_profiles_activate(payload)
                _send_json(self, status, body)
                return
            if path == "/api/profiles/delete":
                status, body = api_post_profiles_delete(payload)
                _send_json(self, status, body)
                return
            if path == "/api/profiles/auto":
                status, body = api_post_profiles_auto(payload)
                _send_json(self, status, body)
                return
            _send_json(self, 404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            logger.exception("POST %s 异常: %s", path, exc)
            _send_json(self, 500, {"ok": False, "error": "internal: {}".format(exc)})


# ============================================================
# 品牌静态图片（v2.0.3.0）
# ============================================================
# Web UI 的 favicon / 顶栏 logo 指向 /branding/*.png；此前服务端只有 `/` 与
# `/api/*` 路由、安装包也没打这些 png，导致图片全 404（顶栏出现破图图标）。
# 这里只放行「字母数字._-」组成的文件名 + 图片类扩展名：文件名里不可能出现
# `/` 或 `..`，路径穿越被结构性阻断。
_BRANDING_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.(?:png|ico|svg|jpe?g|webp|gif)$")
_BRANDING_CTYPES = {
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _branding_dirs():
    """品牌图片查找目录（按优先级）。

    1. <数据目录>/branding           —— 安装包装完后的位置（setup.iss 会拷贝）
    2. <数据目录>/packaging/branding —— 仓库内直接跑源码时的位置
    3. <脚本目录>/packaging/branding —— sys.path 与数据目录不一致时的兜底
    """
    bases = []
    try:
        bases.append(BASE_DIR)
    except NameError:  # 未经 _attach 注入（例如单测直接 import）
        pass
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    out = []
    for base in bases:
        for sub_dir in ("branding", os.path.join("packaging", "branding")):
            d = os.path.join(base, sub_dir)
            if d not in out:
                out.append(d)
    return out


# P1-1 前端配合：给页面内所有 fetch 自动补 `X-Requested-With: DrcomUI`。
# 服务端 do_POST 强制要求该头；跨站页面发不出非简单请求（会触发预检，
# 而服务端不返回任何 CORS 允许头），因此纯 CSRF 被阻断。
_FETCH_HEADER_JS = (
    "<script>/* DrcomAutoLogin: 同源写请求自动带 X-Requested-With */\n"
    "(function(){var f=window.fetch;if(!f){return;}"
    "window.fetch=function(u,o){o=o||{};var h=o.headers||{};"
    "if(typeof Headers!=='undefined'&&h instanceof Headers){h.set('X-Requested-With','DrcomUI');}"
    "else{h['X-Requested-With']='DrcomUI';}"
    "o.headers=h;return f.call(window,u,o);};})();</script>\n"
)

_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f5f5f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">
<title>星尘闪连 (Stardust Flash Link) — Dr.COM 校园网自动登录</title>
<!-- 品牌图标：服务端 /branding/* 静态路由提供（安装包会随包拷贝 branding/*.png） -->
<link rel="icon" type="image/png" sizes="32x32" href="/branding/web-logo-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/branding/web-logo-16.png">
<link rel="apple-touch-icon" sizes="128x128" href="/branding/web-logo-128.png">
<style>
/* ============================================================
   星尘闪连 · Web UI 样式表（Apple 风格 / 亚克力玻璃）

   设计约定：
   1) 层级用「字号 + 字重 + 灰度」表达，不用高饱和色堆叠；
   2) 所有表面统一为半透明亚克力材质（透明底 + 背景模糊 + 发丝描边 + 内侧高光）；
   3) 只保留一个强调色（Apple 蓝），状态色仅用于结果反馈；
   4) 8pt 间距栅格 + 连续圆角，动效短促克制（150-260ms）。
   ============================================================ */

/* ============================================================
   1. 设计令牌 — 亮色
   ============================================================ */
:root {
  color-scheme: light;

  /* —— 画布与背景光晕 —— */
  --bg: #f5f5f7;
  --glow-a: rgba(0, 122, 255, 0.16);
  --glow-b: rgba(122, 92, 255, 0.13);
  --glow-c: rgba(0, 199, 190, 0.10);

  /* —— 亚克力材质 —— */
  --material: rgba(255, 255, 255, 0.68);
  --material-2: rgba(255, 255, 255, 0.52);
  --material-strong: rgba(255, 255, 255, 0.86);
  --material-solid: #ffffff;
  --blur: saturate(180%) blur(30px);
  --highlight: inset 0 1px 0 rgba(255, 255, 255, 0.75);

  /* —— 系统填充色（Apple 语义灰）—— */
  --fill: rgba(120, 120, 128, 0.10);
  --fill-2: rgba(120, 120, 128, 0.16);
  --fill-3: rgba(120, 120, 128, 0.24);
  --hairline: rgba(0, 0, 0, 0.09);
  --hairline-2: rgba(0, 0, 0, 0.16);

  /* —— 文字 —— */
  --text: #1d1d1f;
  --text-strong: #000000;
  --text-2: #6e6e73;
  --text-3: #86868b;

  /* —— 强调 / 状态 —— */
  --accent: #0071e3;
  --accent-hover: #0077ed;
  --accent-soft: rgba(0, 113, 227, 0.12);
  --accent-softer: rgba(0, 113, 227, 0.06);
  --ok: #1d8a3c;
  --ok-fill: rgba(48, 209, 88, 0.16);
  --warn: #a35200;
  --warn-fill: rgba(255, 159, 10, 0.18);
  --err: #d70015;
  --err-fill: rgba(255, 69, 58, 0.13);
  --track: rgba(120, 120, 128, 0.32);

  /* —— 终端配色 —— */
  --terminal: #1c1c1e;
  --terminal-text: #e8e8ea;
  --terminal-dim: #8e8e93;

  /* —— 形状 —— */
  --r-xl: 24px;
  --r-lg: 18px;
  --r-md: 14px;
  --r-sm: 10px;
  --r-xs: 8px;
  --r-pill: 980px;

  /* —— 阴影 —— */
  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.04), 0 10px 28px -14px rgba(0, 0, 0, 0.20);
  --shadow-2: 0 2px 4px rgba(0, 0, 0, 0.05), 0 20px 44px -20px rgba(0, 0, 0, 0.26);
  --shadow-lift: 0 16px 56px -14px rgba(0, 0, 0, 0.30);
  --shadow-pop: 0 24px 80px -16px rgba(0, 0, 0, 0.40);
}

/* ============================================================
   2. 设计令牌 — 深色（html[data-theme="dark"]）
   ============================================================ */
[data-theme="dark"] {
  color-scheme: dark;

  --bg: #000000;
  --glow-a: rgba(10, 132, 255, 0.20);
  --glow-b: rgba(140, 110, 255, 0.16);
  --glow-c: rgba(0, 199, 190, 0.10);

  --material: rgba(28, 28, 30, 0.72);
  --material-2: rgba(44, 44, 46, 0.60);
  --material-strong: rgba(28, 28, 30, 0.88);
  --material-solid: #1c1c1e;
  --highlight: inset 0 1px 0 rgba(255, 255, 255, 0.08);

  --fill: rgba(120, 120, 128, 0.24);
  --fill-2: rgba(120, 120, 128, 0.32);
  --fill-3: rgba(120, 120, 128, 0.44);
  --hairline: rgba(255, 255, 255, 0.10);
  --hairline-2: rgba(255, 255, 255, 0.18);

  --text: #f5f5f7;
  --text-strong: #ffffff;
  --text-2: #a1a1a6;
  --text-3: #8e8e93;

  --accent: #0a84ff;
  --accent-hover: #3d9bff;
  --accent-soft: rgba(10, 132, 255, 0.22);
  --accent-softer: rgba(10, 132, 255, 0.12);
  --ok: #30d158;
  --ok-fill: rgba(48, 209, 88, 0.20);
  --warn: #ff9f0a;
  --warn-fill: rgba(255, 159, 10, 0.20);
  --err: #ff453a;
  --err-fill: rgba(255, 69, 58, 0.20);
  --track: rgba(120, 120, 128, 0.44);

  --terminal: #0d0d0f;
  --terminal-text: #d8d8dc;
  --terminal-dim: #6e6e73;

  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.40), 0 10px 28px -14px rgba(0, 0, 0, 0.60);
  --shadow-2: 0 2px 4px rgba(0, 0, 0, 0.50), 0 20px 44px -20px rgba(0, 0, 0, 0.70);
  --shadow-lift: 0 16px 56px -14px rgba(0, 0, 0, 0.70);
  --shadow-pop: 0 24px 80px -16px rgba(0, 0, 0, 0.80);
}

/* ============================================================
   3. 基础层：画布 / 字体 / 背景光晕
   ============================================================ */
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  min-height: 100vh;
  background-color: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text",
               "Segoe UI", "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
  font-size: 15px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  position: relative;
  overflow-x: hidden;
  transition: background-color 0.3s ease, color 0.3s ease;
}
/* 背景：三处极淡色晕，为亚克力层提供可模糊的内容（取代旧版网格底纹） */
body::before {
  content: '';
  position: fixed;
  inset: -20% -10% -10% -10%;
  background:
    radial-gradient(46% 38% at 12% 0%, var(--glow-a) 0%, transparent 62%),
    radial-gradient(38% 34% at 88% 6%, var(--glow-b) 0%, transparent 60%),
    radial-gradient(50% 40% at 50% 104%, var(--glow-c) 0%, transparent 64%);
  filter: blur(8px);
  pointer-events: none;
  z-index: 0;
}
h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.02em; color: var(--text-strong); }
a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-hover); }
.mono, code, pre, .log-box, .update-modal-log, .changelog-body {
  font-family: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Consolas, monospace;
  font-variant-numeric: tabular-nums;
}
.muted { color: var(--text-2); }
.skip {
  position: absolute; left: -9999px; top: 0; z-index: 99;
  background: var(--material-solid); border: 1px solid var(--hairline);
  border-radius: 0 0 var(--r-sm) 0; padding: 8px 14px;
}
.skip:focus { left: 0; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 20px 24px 72px; position: relative; z-index: 1; }

/* 统一细滚动条，贴合玻璃表面 */
* { scrollbar-width: thin; scrollbar-color: var(--fill-3) transparent; }
*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-thumb { background: var(--fill-3); border-radius: 999px; border: 2px solid transparent; background-clip: content-box; }
*::-webkit-scrollbar-thumb:hover { background: var(--fill-2); background-clip: content-box; }
*::-webkit-scrollbar-track { background: transparent; }

/* ============================================================
   4. 顶栏（亚克力玻璃）+ 分段控件
   ============================================================ */
.topbar {
  position: sticky; top: 0; z-index: 40;
  background: var(--material-strong);
  backdrop-filter: var(--blur);
  -webkit-backdrop-filter: var(--blur);
  border-bottom: 1px solid var(--hairline);
}
.topbar-inner {
  max-width: 1080px; margin: 0 auto; padding: 14px 24px 10px;
  display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
}
.brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand-mark {
  width: 36px; height: 36px; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 11px; overflow: hidden;
  background: var(--fill);
  box-shadow: var(--highlight), 0 1px 3px rgba(0, 0, 0, 0.10);
}
.brand-mark img { width: 100%; height: 100%; display: block; object-fit: contain; }
.brand-text { display: flex; flex-direction: column; min-width: 0; line-height: 1.25; }
.brand-name {
  font-size: 16px; font-weight: 600; letter-spacing: -0.02em; color: var(--text-strong);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.brand-sub { font-size: 12px; color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ver-badge {
  font-size: 11.5px; font-weight: 500; letter-spacing: 0.01em;
  color: var(--text-2); background: var(--fill);
  padding: 3px 9px; border-radius: var(--r-pill); white-space: nowrap;
  border: 1px solid var(--hairline);
  font-variant-numeric: tabular-nums;
}
.topbar-actions { display: flex; align-items: center; gap: 8px; }
.liveness {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: 13px; color: var(--text-2);
  background: var(--fill); border: 1px solid var(--hairline);
  border-radius: var(--r-pill); padding: 6px 13px; white-space: nowrap;
  transition: background-color 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--text-3); transition: background-color 0.2s ease; }
.dot-ok { background: #30d158; box-shadow: 0 0 0 3px var(--ok-fill); }
.dot-err { background: #ff453a; box-shadow: 0 0 0 3px var(--err-fill); }
.dot-warn { background: #ff9f0a; box-shadow: 0 0 0 3px var(--warn-fill); }
.dot-unknown { background: var(--text-3); box-shadow: 0 0 0 3px var(--fill); }
.dot-pulse { animation: pulseDot 2s ease-in-out infinite; }
@keyframes pulseDot { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.45; transform: scale(0.86); } }
.icon-btn {
  width: 36px; height: 36px; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--hairline); border-radius: var(--r-pill);
  background: var(--fill); color: var(--text-2); cursor: pointer; padding: 0;
  transition: background-color 0.18s, color 0.18s, transform 0.12s;
}
.icon-btn:hover { background: var(--fill-2); color: var(--text); }
.icon-btn:active { transform: scale(0.94); }
.theme-icon { display: block; }
[data-theme="dark"] .theme-icon-sun { display: none; }
[data-theme="light"] .theme-icon-moon { display: none; }

.topbar-nav { max-width: 1080px; margin: 0 auto; padding: 0 24px 12px; overflow-x: auto; }
.tablist {
  display: inline-flex; gap: 2px; padding: 3px;
  background: var(--fill); border: 1px solid var(--hairline);
  border-radius: var(--r-pill);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
}
.tab {
  display: inline-flex; align-items: center; gap: 7px;
  border: 0; background: transparent; color: var(--text-2);
  font: inherit; font-size: 13.5px; font-weight: 500;
  padding: 7px 16px; border-radius: var(--r-pill); cursor: pointer; white-space: nowrap;
  transition: background-color 0.2s, color 0.2s, box-shadow 0.2s;
}
.tab svg { flex: none; opacity: 0.9; }
.tab:hover { color: var(--text); }
.tab[aria-selected="true"] {
  background: var(--material-solid); color: var(--text-strong); font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.10), var(--highlight);
}
:focus-visible { outline: 3px solid var(--accent-soft); outline-offset: 2px; }
.tab:focus-visible, .icon-btn:focus-visible, .btn:focus-visible { outline: 3px solid var(--accent-soft); outline-offset: 2px; }

/* ============================================================
   5. 顶部说明条（玻璃 notes bar）
   ============================================================ */
.hint-strip {
  display: flex; align-items: center; gap: 10px;
  margin: 20px auto 18px; padding: 11px 16px; max-width: 1080px;
  background: var(--material-2); border: 1px solid var(--hairline);
  border-radius: var(--r-md); color: var(--text-2);
  font-size: 13px;
  box-shadow: var(--highlight), var(--shadow-1);
  backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
  position: relative; z-index: 1;
  animation: riseIn 0.32s cubic-bezier(0.32, 0.72, 0, 1);
}
.hint-strip-icon { display: inline-flex; color: var(--text-3); flex: none; }
.hint-strip-text { color: var(--text-2); flex: 1 1 auto; min-width: 0; }
.hint-strip-link {
  display: inline-flex; align-items: center; gap: 3px; flex: none;
  color: var(--accent); font-weight: 500; white-space: nowrap;
  padding: 4px 10px; border-radius: var(--r-pill);
  background: var(--accent-softer);
  transition: background-color 0.18s;
}
.hint-strip-link:hover { background: var(--accent-soft); }
@keyframes riseIn { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: none; } }

/* ============================================================
   6. 面板 / 卡片 / KPI
   ============================================================ */
.panel { display: none; }
.panel.active {
  display: block; opacity: 1;
  animation: panelFadeIn 0.3s cubic-bezier(0.32, 0.72, 0, 1);
}
@keyframes panelFadeIn { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: none; } }

.grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(212px, 1fr)); }
.card {
  background: var(--material);
  border: 1px solid var(--hairline);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-1), var(--highlight);
  backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
  padding: 20px;
  transition: box-shadow 0.22s ease, transform 0.22s ease, border-color 0.22s ease;
}
.card:hover { box-shadow: var(--shadow-2), var(--highlight); transform: translateY(-1px); }
.kpi { display: flex; flex-direction: column; gap: 6px; min-height: 118px; }
.kpi-label { font-size: 12.5px; font-weight: 500; color: var(--text-2); }
.kpi-value {
  display: inline-flex; align-items: center; gap: 9px;
  font-size: 26px; font-weight: 600; letter-spacing: -0.021em; line-height: 1.25;
  color: var(--text-strong); word-break: break-word;
}
.kpi-value.small { font-size: 16px; font-weight: 500; letter-spacing: -0.01em; }
.kpi-value.mono { font-size: 24px; }
.kpi-value.small.mono { font-size: 15.5px; }
.kpi-sub { font-size: 12.5px; color: var(--text-3); }
.kpi-alert { border-color: rgba(255, 159, 10, 0.45); }
.kpi-alert-err { border-color: rgba(255, 69, 58, 0.45); }
/* v2.0.8.0：连接质量小柱图（纯 CSS，不引入图表库） */
.qbars { display: flex; align-items: flex-end; gap: 10px; height: 68px; margin: 6px 0 4px; }
.qbar { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; gap: 3px; min-width: 0; }
.qbar-fill { border-radius: 4px 4px 2px 2px; min-height: 2px;
  background: linear-gradient(180deg, rgba(10, 132, 255, 0.85), rgba(10, 132, 255, 0.40));
  transition: height 0.32s cubic-bezier(0.32, 0.72, 0, 1); }
.qbar-fill.qfail { background: linear-gradient(180deg, rgba(255, 159, 10, 0.9), rgba(255, 159, 10, 0.4)); }
.qbar-day { font-size: 10.5px; color: var(--text-3); text-align: center; white-space: nowrap; }
.tone-ok { color: var(--ok); }
.tone-err { color: var(--err); }
.tone-warn { color: var(--warn); }
.tone-muted { color: var(--text-3); }
.action-card {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  text-align: center; padding: 36px 24px; margin-top: 14px;
  background: var(--material-2);
}
.action-card .muted { max-width: 46ch; }

/* ============================================================
   7. 按钮 / 表单 / 开关 / 徽章
   ============================================================ */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  padding: 10px 20px; border-radius: var(--r-pill);
  border: 1px solid transparent; background: var(--accent); color: #fff;
  font: inherit; font-size: 14px; font-weight: 500; cursor: pointer; text-decoration: none;
  transition: background-color 0.18s, border-color 0.18s, color 0.18s, transform 0.12s, opacity 0.15s, box-shadow 0.18s;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
  white-space: nowrap;
}
.btn:hover:not(:disabled) { background: var(--accent-hover); }
.btn:active:not(:disabled) { transform: scale(0.98); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; box-shadow: none; }
.btn-secondary {
  background: var(--fill); color: var(--text); border-color: var(--hairline);
  box-shadow: var(--highlight);
}
.btn-secondary:hover:not(:disabled) { background: var(--fill-2); color: var(--text); }
.btn-danger { background: var(--fill); color: var(--err); border-color: var(--hairline); box-shadow: var(--highlight); }
.btn-danger:hover:not(:disabled) { background: var(--err-fill); color: var(--err); border-color: rgba(255, 69, 58, 0.35); }
.btn-lg { padding: 13px 32px; font-size: 15px; font-weight: 500; min-width: 190px; border-radius: var(--r-pill); }
.btn-spinner {
  display: none; width: 14px; height: 14px; border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.45); border-top-color: #fff;
  animation: spin 0.7s linear infinite;
}
.btn.loading .btn-spinner { display: inline-block; }
@keyframes spin { to { transform: rotate(360deg); } }
.btn-row { display: flex; gap: 10px; flex-wrap: wrap; }
.section { margin-bottom: 14px; }
.section-head { margin-bottom: 16px; }
.section-title { font-size: 17px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.section-desc { font-size: 13px; color: var(--text-2); margin-top: 5px; line-height: 1.5; }
.field { position: relative; margin-bottom: 18px; }
.field:last-child { margin-bottom: 0; }
.field > label:not(.switch) { display: block; font-size: 13px; font-weight: 500; margin-bottom: 7px; color: var(--text-2); }
.field input[type=text], .field input[type=number], .field input[type=password],
.field select, .log-toolbar input {
  width: 100%; padding: 11px 13px; font: inherit; font-size: 14.5px;
  color: var(--text); background: var(--fill);
  border: 1px solid var(--hairline); border-radius: var(--r-sm);
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.03);
  transition: border-color 0.18s, box-shadow 0.18s, background-color 0.18s;
}
.field select {
  appearance: none; -webkit-appearance: none; padding-right: 38px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1.5 6 6.5l5-5' fill='none' stroke='%2386868b' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 14px center;
}
.field input:focus, .field select:focus, .log-toolbar input:focus {
  outline: none; border-color: var(--accent); background: var(--material-solid);
  box-shadow: 0 0 0 4px var(--accent-soft);
}
.field input.is-invalid, .field select.is-invalid { border-color: var(--err); box-shadow: 0 0 0 4px var(--err-fill); }
.field input[type=number].cfg-lg, .field input[type=text].cfg-lg { padding: 13px 15px; font-size: 15px; }
.hint { font-size: 12.5px; color: var(--text-3); margin-top: 6px; line-height: 1.45; }
.err { font-size: 12.5px; color: var(--err); margin-top: 6px; }
.err:empty { display: none; }
.switch { position: relative; display: inline-flex; align-items: center; gap: 10px; cursor: pointer; user-select: none; }
.switch input { position: absolute; width: 1px; height: 1px; opacity: 0; margin: 0; }
.switch .track {
  position: relative; width: 48px; height: 29px; border-radius: var(--r-pill);
  background: var(--track); flex: none; transition: background-color 0.22s ease;
}
.switch .track::after {
  content: ''; position: absolute; top: 2px; left: 2px;
  width: 25px; height: 25px; border-radius: 50%; background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.28); transition: transform 0.22s cubic-bezier(0.32, 0.72, 0, 1);
}
.switch input:checked + .track { background: #30d158; }
.switch input:checked + .track::after { transform: translateX(19px); }
.switch input:focus-visible + .track { outline: 3px solid var(--accent-soft); outline-offset: 2px; }
.switch-label { font-size: 14px; font-weight: 500; color: var(--text); }
.badge {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; font-weight: 500; padding: 3px 9px;
  border-radius: var(--r-pill); border: 1px solid transparent; white-space: nowrap;
}
.badge-ok { background: var(--ok-fill); color: var(--ok); }
.badge-err { background: var(--err-fill); color: var(--err); }
.badge-muted { background: var(--fill); color: var(--text-2); border-color: var(--hairline); }
.card-alert { border-color: rgba(255, 69, 58, 0.40); }
.save-bar {
  position: sticky; bottom: 16px; z-index: 20;
  display: flex; justify-content: flex-end; gap: 12px; align-items: center; flex-wrap: wrap;
  padding: 12px 14px 12px 18px;
  background: var(--material-strong); border: 1px solid var(--hairline);
  border-radius: var(--r-lg); box-shadow: var(--shadow-lift), var(--highlight);
  backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
}
.save-bar .muted { margin-right: auto; font-size: 13px; }
.config-io-row { display: flex; gap: 10px; margin: 16px 0; flex-wrap: wrap; }

/* ============================================================
   8. 日志终端
   ============================================================ */
.log-toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
.log-toolbar .grow { flex: 1 1 220px; min-width: 180px; }
.log-box {
  background: var(--terminal); color: var(--terminal-text);
  border: 1px solid var(--hairline); border-radius: var(--r-md);
  padding: 16px 18px; height: min(56vh, 520px); overflow: auto;
  font-size: 12.5px; line-height: 1.7; white-space: pre-wrap; word-break: break-all;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.03);
}
.log-box.is-empty { color: var(--terminal-dim); font-style: italic; }
.log-meta {
  display: flex; gap: 16px; flex-wrap: wrap;
  font-size: 12.5px; color: var(--text-2); margin-top: 12px;
  padding: 10px 14px; background: var(--fill); border-radius: var(--r-sm);
  border: 1px solid var(--hairline);
}
.log-level-filter { display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 13px; border-radius: var(--r-pill);
  border: 1px solid var(--hairline); background: var(--fill); color: var(--text-2);
  font: inherit; font-size: 12.5px; font-weight: 500; line-height: 1.2;
  cursor: pointer; user-select: none;
  transition: background-color 0.16s, border-color 0.16s, color 0.16s, transform 0.1s;
}
.chip:hover { background: var(--fill-2); color: var(--text); }
.chip:active { transform: scale(0.97); }
.chip.chip-active { background: var(--material-solid); border-color: var(--hairline-2); color: var(--text-strong); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.10); }
.chip-level[data-level="info"] { color: var(--text-2); }
.chip-level.chip-active[data-level="info"] { color: var(--accent); }
.chip-level.chip-active[data-level="warning"] { color: var(--warn); }
.chip-level.chip-active[data-level="error"],
.chip-level.chip-active[data-level="critical"] { color: var(--err); }

/* ============================================================
   9. 关于面板
   ============================================================ */
.info { display: grid; grid-template-columns: 132px 1fr; gap: 12px 20px; font-size: 14px; margin: 0; }
.info dt { color: var(--text-2); }
.info dd { margin: 0; word-break: break-all; color: var(--text); }
code.path {
  background: var(--fill); border: 1px solid var(--hairline); border-radius: var(--r-xs);
  padding: 3px 9px; font-size: 12.5px; color: var(--text-2);
}
.link-row { display: flex; gap: 10px; flex-wrap: wrap; }

/* ============================================================
   10. Toast 通知
   ============================================================ */
.toasts {
  position: fixed; top: 18px; right: 18px; z-index: 100;
  display: flex; flex-direction: column; gap: 10px;
  max-width: min(92vw, 380px); pointer-events: none;
}
.toast {
  display: flex; gap: 10px; align-items: flex-start;
  background: var(--material-strong); border: 1px solid var(--hairline);
  border-radius: var(--r-md); box-shadow: var(--shadow-lift), var(--highlight);
  backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
  padding: 12px 15px; font-size: 13.5px; color: var(--text);
  animation: toastIn 0.26s cubic-bezier(0.32, 0.72, 0, 1);
}
.toast.out { animation: toastOut 0.24s ease forwards; }
.toast-icon { display: inline-flex; flex: none; margin-top: 1px; color: var(--accent); }
.toast.success .toast-icon { color: var(--ok); }
.toast.error .toast-icon { color: var(--err); }
.toast.warn .toast-icon { color: #ff9f0a; }
.toast-msg { word-break: break-word; }
@keyframes toastIn { from { opacity: 0; transform: translateX(20px) scale(0.98); } to { opacity: 1; transform: none; } }
@keyframes toastOut { to { opacity: 0; transform: translateX(20px); } }

/* ============================================================
   11. 升级横幅 — 中性 / 警示 / 危险 / 成功 四态
   ============================================================ */
.update-banner {
  display: flex; align-items: flex-start; gap: 14px;
  padding: 16px; margin-bottom: 14px;
  background: var(--material); border: 1px solid var(--hairline);
  border-radius: var(--r-lg); box-shadow: var(--shadow-1), var(--highlight);
  backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
  animation: panelFadeIn 0.26s cubic-bezier(0.32, 0.72, 0, 1);
}
.update-banner[hidden] { display: none; }
.update-banner-icon {
  width: 34px; height: 34px; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--fill); color: var(--text-2);
  border-radius: 50%;
}
.update-banner-body { flex: 1 1 auto; min-width: 0; }
.update-banner-title { font-size: 14.5px; font-weight: 600; color: var(--text-strong); margin-bottom: 3px; letter-spacing: -0.01em; }
.update-banner-desc { font-size: 13px; color: var(--text-2); word-break: break-word; }
.update-progress {
  margin-top: 10px; height: 5px; border-radius: var(--r-pill);
  background: var(--fill-2); overflow: hidden;
}
.update-progress[hidden] { display: none; }
.update-progress-bar {
  height: 100%; background: var(--accent); border-radius: var(--r-pill);
  transition: width 0.2s ease;
}
.update-banner-action {
  flex: none; padding: 7px 15px; border-radius: var(--r-pill);
  border: 1px solid var(--hairline); background: var(--fill); color: var(--text);
  font: inherit; font-size: 13px; font-weight: 500; cursor: pointer;
  transition: background-color 0.16s, color 0.16s;
}
.update-banner-action[hidden] { display: none; }
.update-banner-action:hover { background: var(--fill-2); }
.update-banner-dismiss {
  flex: none; width: 30px; height: 30px; padding: 0;
  display: inline-flex; align-items: center; justify-content: center;
  border: 0; background: transparent; color: var(--text-3);
  cursor: pointer; border-radius: 50%;
  font-size: 13px; line-height: 1;
  transition: background-color 0.16s, color 0.16s;
}
.update-banner-dismiss:hover { background: var(--fill-2); color: var(--text); }
.update-banner.warn { border-color: rgba(255, 159, 10, 0.45); }
.update-banner.warn .update-banner-icon { background: var(--warn-fill); color: var(--warn); }
.update-banner.warn .update-progress-bar { background: #ff9f0a; }
.update-banner.error { border-color: rgba(255, 69, 58, 0.45); }
.update-banner.error .update-banner-icon { background: var(--err-fill); color: var(--err); }
.update-banner.success { border-color: rgba(48, 209, 88, 0.45); }
.update-banner.success .update-banner-icon { background: var(--ok-fill); color: var(--ok); }
.update-banner-link { font-size: 13px; font-weight: 500; }

/* ============================================================
   12. 响应式（≤720px 平板；≤480px 手机）
   ============================================================ */
@media (max-width: 720px) {
  .wrap { padding: 16px 18px 56px; }
  .topbar-inner { padding: 12px 18px 8px; }
  .topbar-nav { padding: 0 18px 10px; }
  .hint-strip { margin: 16px 18px 14px; padding: 10px 14px; font-size: 12.5px; flex-wrap: wrap; }
  .grid { grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 12px; }
  .brand-name { font-size: 15px; }
  .kpi { min-height: 104px; }
  .kpi-value { font-size: 22px; }
  .kpi-value.small { font-size: 15px; }
  .info { grid-template-columns: 1fr; gap: 4px; }
  .info dt { margin-top: 10px; color: var(--text-3); font-size: 12.5px; }
  .btn-lg { width: 100%; }
  .save-bar { justify-content: stretch; bottom: 12px; }
  .save-bar .btn { flex: 1 1 auto; }
  .card { padding: 18px; }
}
@media (max-width: 480px) {
  body { font-size: 14.5px; }
  .wrap { padding: 14px 14px 48px; }
  .topbar-inner { padding: 10px 14px 8px; }
  .topbar-nav { padding: 0 14px 10px; }
  .hint-strip { margin: 14px 14px 12px; font-size: 12px; padding: 9px 12px; }
  .grid { grid-template-columns: 1fr; gap: 12px; }
  .kpi { min-height: 92px; padding: 16px; }
  .kpi-value { font-size: 21px; }
  .kpi-value.small { font-size: 14.5px; }
  .card { padding: 16px; border-radius: var(--r-md); }
  .action-card { padding: 24px 16px; }
  .tab { padding: 7px 13px; font-size: 13px; }
  .liveness { padding: 5px 11px; font-size: 12.5px; }
  .save-bar { padding: 12px; flex-direction: column; align-items: stretch; }
  .save-bar .muted { margin-right: 0; margin-bottom: 4px; text-align: center; }
  .save-bar .btn { width: 100%; }
  .log-meta { font-size: 12px; padding: 8px 12px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }
}
</style>
<script>
/* 首屏主题：localStorage 优先，否则跟随系统 prefers-color-scheme（在 <style> 之后、body 之前执行，避免闪烁） */
(function () {
  var theme = 'light';
  try {
    var saved = localStorage.getItem('drcom-theme');
    if (saved === 'dark' || saved === 'light') {
      theme = saved;
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      theme = 'dark';
    }
  } catch (e) { /* 隐私模式下 localStorage 不可用，回落亮色 */ }
  document.documentElement.setAttribute('data-theme', theme);
})();
</script>
</head>
<body>
<a class="skip" href="#main">跳到主要内容</a>

<header class="topbar">
  <div class="topbar-inner">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true"><img id="brand-logo" src="/branding/web-logo-64.png" alt=""></span>
      <span class="brand-text">
        <span class="brand-name">星尘闪连</span>
        <span class="brand-sub">Dr.COM 校园网自动登录</span>
      </span>
      <span class="ver-badge" id="ver-badge">v-</span>
    </div>
    <div class="topbar-actions">
      <span class="liveness" role="status" aria-live="polite" title="服务实时状态">
        <span class="dot dot-unknown dot-pulse" id="liveness-dot" aria-hidden="true"></span>
        <span id="liveness-text">连接中…</span>
      </span>
      <button class="icon-btn" id="btn-theme" type="button" aria-label="切换亮色 / 暗色主题" title="切换主题">
        <svg class="theme-icon theme-icon-sun" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4"></circle>
          <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"></path>
        </svg>
        <svg class="theme-icon theme-icon-moon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"></path>
        </svg>
      </button>
    </div>
  </div>
  <div class="topbar-nav">
    <div class="tablist" role="tablist" aria-label="功能分区">
      <button class="tab" id="tab-status" role="tab" type="button" aria-selected="true" aria-controls="panel-status" data-tab="status"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12h3.6l2.4-6.2 3.6 12.4 2.4-6.2H21"/></svg><span>状态</span></button>
      <button class="tab" id="tab-config" role="tab" type="button" aria-selected="false" tabindex="-1" aria-controls="panel-config" data-tab="config"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h8M17.5 8H20M4 16h4M13.5 16H20"/><circle cx="15" cy="8" r="2.2"/><circle cx="11" cy="16" r="2.2"/></svg><span>配置</span></button>
      <button class="tab" id="tab-log" role="tab" type="button" aria-selected="false" tabindex="-1" aria-controls="panel-log" data-tab="log"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5h12v17H6z"/><path d="M9 8.5h6M9 12.5h6M9 16.5h4"/></svg><span>日志</span></button>
      <button class="tab" id="tab-about" role="tab" type="button" aria-selected="false" tabindex="-1" aria-controls="panel-about" data-tab="about"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.6"/><path d="M12 11.2v5.4M12 7.9v.1"/></svg><span>关于</span></button>
    </div>
  </div>
</header>

<div class="hint-strip" role="note">
  <span class="hint-strip-icon" aria-hidden="true"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4.6" y="10.2" width="14.8" height="10.2" rx="3.2"/><path d="M8.6 10.2V7.9a3.4 3.4 0 0 1 6.8 0v2.3"/></svg></span>
  <span class="hint-strip-text">本服务仅监听 127.0.0.1，所有数据保存在本机；密码仅保存到 password.txt。</span>
  <a class="hint-strip-link" href="https://github.com/TSS-Small-sunshine/StardustFlashLink" target="_blank" rel="noopener noreferrer">查看源码 →</a>
</div>

<main class="wrap" id="main">

  <!-- ============ 状态 ============ -->
  <section class="panel active" id="panel-status" role="tabpanel" aria-labelledby="tab-status" tabindex="-1">
    <!-- v1.3 新增：自动升级横幅（默认 hidden，由 JS 按 state 控制显隐） -->
    <div id="update-banner" class="update-banner" hidden>
      <div class="update-banner-icon" id="update-banner-icon" aria-hidden="true"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19.5V6.9M6.2 12.4 12 6.6l5.8 5.8"/></svg></div>
      <div class="update-banner-body">
        <div class="update-banner-title" id="update-banner-title">检查更新...</div>
        <div class="update-banner-desc" id="update-banner-desc"></div>
        <div class="update-progress" id="update-progress" hidden>
          <div class="update-progress-bar" id="update-progress-bar" style="width:0%"></div>
        </div>
      </div>
      <button class="update-banner-action" id="update-banner-action" type="button" hidden></button>
      <button class="update-banner-dismiss" id="update-banner-dismiss" type="button" aria-label="关闭横幅"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6.4 6.4 17.6 17.6M17.6 6.4 6.4 17.6"/></svg></button>
    </div>

    <div class="grid">
      <article class="card kpi" id="card-net">
        <div class="kpi-label">网络可达性</div>
        <div class="kpi-value" id="kpi-net">
          <span class="dot dot-unknown" id="kpi-net-dot" aria-hidden="true"></span>
          <span id="kpi-net-text">未知</span>
        </div>
        <div class="kpi-sub" id="kpi-net-sub">等待首次检查</div>
      </article>

      <article class="card kpi" id="card-online">
        <div class="kpi-label">在线状态</div>
        <div class="kpi-value" id="kpi-online">
          <span class="dot dot-unknown" id="kpi-online-dot" aria-hidden="true"></span>
          <span id="kpi-online-text">未知</span>
        </div>
        <div class="kpi-sub" id="kpi-online-sub">登录结果：-</div>
      </article>

      <article class="card kpi">
        <div class="kpi-label">当前账号</div>
        <div class="kpi-value small mono" id="kpi-account">-</div>
        <div class="kpi-sub" id="kpi-account-sub">来自配置文件</div>
      </article>

      <article class="card kpi">
        <div class="kpi-label">上次登录时间</div>
        <div class="kpi-value small" id="kpi-lastlogin">从未</div>
        <div class="kpi-sub" id="kpi-lastlogin-sub">尚无登录记录</div>
      </article>

      <article class="card kpi" id="card-error">
        <div class="kpi-label">上次错误</div>
        <div class="kpi-value small" id="kpi-error">无</div>
        <div class="kpi-sub" id="kpi-error-sub">最近一次检查未报错</div>
      </article>

      <article class="card kpi">
        <div class="kpi-label">下次检查</div>
        <div class="kpi-value mono" id="kpi-next">未计划</div>
        <div class="kpi-sub" id="kpi-next-sub">-</div>
      </article>

      <!-- v2.0.8.0 / B4：连接质量（近 7 天，从日志现算） -->
      <article class="card kpi" id="card-uptime">
        <div class="kpi-label">在线率（近 7 天）</div>
        <div class="kpi-value" id="kpi-uptime">-</div>
        <div class="kpi-sub" id="kpi-uptime-sub">等待统计</div>
      </article>

      <article class="card kpi" id="card-relogin">
        <div class="kpi-label">掉线重登</div>
        <div class="kpi-value" id="kpi-relogin">-</div>
        <div class="kpi-sub" id="kpi-relogin-sub">近 7 天</div>
      </article>

      <article class="card kpi" id="card-recover">
        <div class="kpi-label">平均恢复耗时</div>
        <div class="kpi-value small" id="kpi-recover">-</div>
        <div class="kpi-sub" id="kpi-recover-sub">从开始检查到登录成功</div>
      </article>

      <article class="card kpi" id="card-latency">
        <div class="kpi-label">当前延迟</div>
        <div class="kpi-value" id="kpi-latency">-</div>
        <div class="kpi-sub" id="kpi-latency-sub">到校园网关的 TCP 握手</div>
      </article>
    </div>

    <!-- v2.0.8.0 / B4：近 7 天柱状图（纯 CSS，无图表库） -->
    <div class="card" id="card-quality">
      <div class="section-head">
        <h2 class="section-title">连接质量（近 7 天）</h2>
        <p class="section-desc" style="margin:0;">数字取自 <span class="mono">logs/campus_login.log</span>：每个检查周期都留下了走向，不再另外存状态文件（重启不清零、升级不丢）。</p>
        <button class="btn" id="btn-metrics-refresh" type="button">刷新</button>
      </div>
      <div class="qbars" id="qbars" aria-label="近 7 天掉线与失败次数柱状图"></div>
      <div class="kpi-sub" id="quality-note">加载中…</div>
    </div>

    <div class="card action-card">
      <button class="btn btn-lg" id="btn-login" type="button">
        <span class="btn-spinner" aria-hidden="true"></span>
        <span id="btn-login-label">立即登录</span>
      </button>
      <p class="muted" id="login-hint" style="margin:0;font-size:12.5px;">点击按钮立即触发一次完整的网络检查与登录流程。</p>
    </div>
  </section>

  <!-- ============ 配置 ============ -->
  <section class="panel" id="panel-config" role="tabpanel" aria-labelledby="tab-config" tabindex="-1">
    <!-- v2.0.9.0 / B5：配置方案（教室 / 宿舍 / 家里） -->
    <div class="card section" id="card-profiles">
      <div class="section-head">
        <h2 class="section-title">配置方案 <span class="badge badge-muted" id="profile-badge">未使用</span></h2>
        <p class="section-desc" style="margin:0;">一套方案 = 一组「位置相关设置」（认证网关 / 检查间隔 / 网络位置守卫白名单）。切换方案<b>只改这些</b>，账号、密码、升级设置一律不动。</p>
      </div>
      <div class="field">
        <label for="profile-select">选择方案</label>
        <select id="profile-select" class="cfg-lg"></select>
        <div class="hint" id="profile-hint">还没有方案 —— 调好配置后点下面的「用当前配置保存」建一个（例如「教室」「宿舍」「家里」）。</div>
      </div>
      <div class="field">
        <label for="profile-new-name">新建 / 覆盖方案</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <input type="text" id="profile-new-name" class="cfg-lg" style="flex:1 1 150px;" placeholder="方案名（例如 家里）" autocomplete="off" spellcheck="false" maxlength="24">
          <input type="text" id="profile-match-ssids" class="cfg-lg" style="flex:2 1 220px;" placeholder="自动匹配的 Wi-Fi 名（可选，逗号分隔）" autocomplete="off" spellcheck="false">
          <button class="btn" id="btn-profile-save" type="button">用当前配置保存</button>
        </div>
        <div class="hint">填了「自动匹配的 Wi-Fi 名」= <b>自动方案</b>：打开下面的自动切换后，一连上这个 Wi-Fi 就自动切过去。</div>
      </div>
      <div class="field">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
          <button class="btn" id="btn-profile-apply" type="button">应用选中方案</button>
          <button class="btn" id="btn-profile-delete" type="button">删除选中方案</button>
          <label style="display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--text-2);">
            <input type="checkbox" id="profile-auto-switch"> 按 Wi-Fi 名自动切换
          </label>
        </div>
        <div class="err" id="err-profile" role="alert"></div>
      </div>
    </div>

    <div class="card section" id="card-password">
      <div class="section-head">
        <h2 class="section-title">账户与登录密码 <span class="badge badge-muted" id="pwd-badge">状态未知</span></h2>
        <p class="section-desc" style="margin:0;">账号 + 运营商 + 密码构成本机登录校园网的完整凭据。密码仅保存于本机 password.txt，保存后立即生效，无需重启。</p>
      </div>
      <div class="field">
        <label for="cfg-account">账号</label>
        <input type="text" id="cfg-account" class="cfg-lg" placeholder="学号 / 工号（纯数字）" autocomplete="off" spellcheck="false" inputmode="numeric">
        <div class="hint">仅支持数字（学号 / 工号），不含运营商后缀</div>
        <div class="err" id="err-account" role="alert"></div>
      </div>
      <div class="field">
        <label for="cfg-suffix">运营商</label>
        <select id="cfg-suffix">
          <option value="">校园用户（无后缀）</option>
          <option value="@yd">中国移动 @yd</option>
          <option value="@dx">中国电信 @dx</option>
          <option value="@lt">中国联通 @lt</option>
        </select>
        <div class="hint">宽带运营商不同，认证域名后缀也不同</div>
      </div>
      <div class="field">
        <label for="pwd-new">账户登录密码</label>
        <input type="password" id="pwd-new" autocomplete="new-password">
        <div class="hint">至少 1 个字符</div>
      </div>
      <div class="field">
        <label for="pwd-confirm">再次输入账户登录密码</label>
        <input type="password" id="pwd-confirm" autocomplete="new-password">
        <div class="err" id="err-pwd" role="alert"></div>
      </div>
      <button class="btn btn-secondary" id="btn-save-pwd" type="button">保存账户登录密码</button>
    </div>

    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">认证服务器</h2>
        <p class="section-desc" style="margin:0;">校园网认证网关地址与端口。</p>
      </div>
      <div class="field">
        <label for="cfg-host">认证服务器地址</label>
        <input type="text" id="cfg-host" class="cfg-lg" placeholder="例如 172.16.80.3" autocomplete="off" spellcheck="false">
        <div class="hint">认证网关的 IP 或域名</div>
        <div class="err" id="err-host" role="alert"></div>
      </div>
      <div class="field">
        <label for="cfg-port">认证端口</label>
        <input type="number" id="cfg-port" class="cfg-lg" min="1" max="65535" step="1" inputmode="numeric">
        <div class="hint">取值 1-65535，通常为 80</div>
        <div class="err" id="err-port" role="alert"></div>
      </div>
    </div>

    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">自动化</h2>
        <p class="section-desc" style="margin:0;">后台线程按间隔自动检查网络，掉线即重新登录。</p>
      </div>
      <div class="field">
        <label class="switch" for="cfg-auto-enabled">
          <input type="checkbox" id="cfg-auto-enabled">
          <span class="track" aria-hidden="true"></span>
          <span class="switch-label">启用周期自检</span>
        </label>
        <div class="hint">关闭后仅在启动时与手动点击时检查</div>
      </div>
      <div class="field">
        <label for="cfg-auto-interval">检查间隔</label>
        <select id="cfg-auto-interval">
          <option value="5">5 分钟</option>
          <option value="15">15 分钟</option>
          <option value="30">30 分钟</option>
          <option value="60">60 分钟</option>
          <option value="120">120 分钟</option>
        </select>
        <div class="hint">连续失败时服务会自动退避（5 → 60 分钟）</div>
      </div>
      <div class="field">
        <label for="cfg-network-timeout">网络等待超时（秒）</label>
        <input type="number" id="cfg-network-timeout" class="cfg-lg" min="10" max="300" step="1" inputmode="numeric">
        <div class="hint">每次检查等待校园网可达的最长时间，10-300 秒</div>
        <div class="err" id="err-timeout" role="alert"></div>
      </div>
      <!-- v2.0.5.0 新增：网络位置守卫 -->
      <div class="field">
        <label class="switch" for="cfg-guard-enabled">
          <input type="checkbox" id="cfg-guard-enabled">
          <span class="track" aria-hidden="true"></span>
          <span class="switch-label">只在校园网内登录（网络位置守卫）</span>
        </label>
        <div class="hint">开启后，只有 Wi-Fi 名或本机网段命中下面任一白名单时才执行登录检查；带回家 / 连热点时不再白跑</div>
      </div>
      <div class="field">
        <label for="cfg-guard-ssids">允许的 Wi-Fi 名称（SSID）</label>
        <input type="text" id="cfg-guard-ssids" class="cfg-lg" placeholder="例如 Campus-WiFi,Dorm-WiFi" autocomplete="off">
        <div class="hint">逗号分隔（中英文逗号都认）；留空 = 不按 Wi-Fi 判断</div>
      </div>
      <div class="field">
        <label for="cfg-guard-subnets">允许的网段（CIDR）</label>
        <input type="text" id="cfg-guard-subnets" class="cfg-lg" placeholder="例如 172.16.0.0/12,10.0.0.0/8" autocomplete="off">
        <div class="hint">逗号分隔；宿舍有线也适用。留空 = 不按网段判断</div>
        <div class="err" id="err-guard" role="alert"></div>
      </div>
      <!-- v1.3 新增：自动升级字段 -->
      <div class="field">
        <label class="switch" for="cfg-auto-update-enabled">
          <input type="checkbox" id="cfg-auto-update-enabled">
          <span class="track" aria-hidden="true"></span>
          <span class="switch-label">启用自动升级（GitHub 检测）</span>
        </label>
        <div class="hint">关闭后仅在启动时与手动点击时检查 GitHub 新版</div>
      </div>
      <div class="field">
        <label for="cfg-update-interval">自动升级检查间隔</label>
        <select id="cfg-update-interval">
          <option value="6">6 小时</option>
          <option value="12">12 小时</option>
          <option value="24">24 小时</option>
        </select>
        <div class="hint">服务会定期访问 GitHub API 检查新版（未认证 60 req/h）</div>
      </div>
      <div class="field">
        <label for="cfg-update-disk">下载前最小剩余磁盘（MB）</label>
        <input type="number" id="cfg-update-disk" class="cfg-lg" min="50" max="10240" step="1" inputmode="numeric">
        <div class="hint">下载安装包前要求磁盘剩余 ≥ 此值（50-10240 MB，默认 200）</div>
      </div>
      <div class="field">
        <div class="hint" style="margin-bottom:8px;">手动触发</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn btn-secondary" id="btn-update-check-now" type="button">立即检查更新</button>
          <button class="btn btn-secondary" id="btn-update-install-now" type="button">立即升级</button>
        </div>
        <div class="hint" style="margin-top:6px;">点「立即检查更新」拉 GitHub；发现新版再点「立即升级」（升级前自动停服务，约 30-60 秒）</div>
      </div>

      <div class="field">
        <div class="hint" style="margin-bottom:8px;">版本回滚（升级把服务弄挂时的退路）</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
          <select id="rollback-version" style="min-width:210px;"></select>
          <button class="btn btn-secondary" id="btn-rollback-now" type="button">回滚到该版本</button>
          <button class="btn btn-secondary" id="btn-rollback-refresh" type="button">刷新列表</button>
        </div>
        <div class="hint" id="rollback-hint" style="margin-top:6px;">正在读取备份列表...</div>
      </div>
    </div>

    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">Web UI</h2>
        <p class="section-desc" style="margin:0;">管理页面本身的服务端口，修改后需重启服务生效。</p>
      </div>
      <div class="field">
        <label for="cfg-ui-port">监听端口</label>
        <input type="number" id="cfg-ui-port" class="cfg-lg" min="1024" max="65535" step="1" inputmode="numeric">
        <div class="hint">取值 1024-65535，仅监听 127.0.0.1，默认 8848</div>
        <div class="err" id="err-ui-port" role="alert"></div>
      </div>
    </div>

    <div class="config-io-row">
      <button class="btn btn-secondary" id="btn-config-export" type="button">导出配置</button>
      <button class="btn btn-secondary" id="btn-config-import" type="button">导入配置</button>
      <input type="file" id="config-import-file" accept=".zip" style="display:none">
    </div>

    <div class="save-bar">
      <span class="muted" id="config-state">配置在打开本页时读取</span>
      <button class="btn btn-lg" id="btn-save-config" type="button">保存配置</button>
    </div>
  </section>

  <!-- ============ 日志 ============ -->
  <section class="panel" id="panel-log" role="tabpanel" aria-labelledby="tab-log" tabindex="-1">
    <div class="card">
      <div class="log-toolbar">
        <input type="text" class="grow" id="log-filter" placeholder="过滤关键字（留空显示全部）" aria-label="日志关键字过滤" autocomplete="off" spellcheck="false">
        <div class="log-level-filter" id="log-level-filter" role="group" aria-label="日志等级筛选">
          <button class="chip chip-level chip-active" data-level="" type="button" aria-pressed="true">全部</button>
          <button class="chip chip-level" data-level="info" type="button" aria-pressed="false">INFO</button>
          <button class="chip chip-level" data-level="warning" type="button" aria-pressed="false">WARN</button>
          <button class="chip chip-level" data-level="error" type="button" aria-pressed="false">ERROR</button>
        </div>
        <button class="btn btn-secondary" id="btn-log-refresh" type="button">刷新</button>
        <button class="btn btn-secondary" id="btn-log-download" type="button">下载日志</button>
        <label class="switch" for="log-autoscroll">
          <input type="checkbox" id="log-autoscroll" checked>
          <span class="track" aria-hidden="true"></span>
          <span class="switch-label">自动滚动</span>
        </label>
      </div>
      <div class="log-box is-empty" id="log-box" role="log" aria-live="off" tabindex="0">暂无日志</div>
      <div class="log-meta">
        <span id="log-count">0 行</span>
        <span id="log-offset">offset 0</span>
        <span id="log-size">0 字节</span>
        <span id="log-updated">未更新</span>
      </div>
    </div>
  </section>

  <!-- ============ 关于 ============ -->
  <section class="panel" id="panel-about" role="tabpanel" aria-labelledby="tab-about" tabindex="-1">
    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">版本信息</h2>
      </div>
      <dl class="info">
        <dt>版本号</dt><dd id="about-version">-</dd>
        <dt>服务启动时间</dt><dd id="about-started">-</dd>
        <dt>已运行时长</dt><dd id="about-uptime" class="mono">-</dd>
      </dl>
    </div>

    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">文件位置</h2>
      </div>
      <dl class="info">
        <dt>配置文件</dt><dd id="about-config"><code class="path">-</code></dd>
        <dt>日志文件</dt><dd id="about-log"><code class="path">-</code></dd>
        <dt>数据目录</dt><dd id="about-data"><code class="path">-</code></dd>
      </dl>
    </div>

    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">访问入口</h2>
      </div>
      <div class="link-row">
        <a class="btn btn-secondary" id="about-local" href="http://127.0.0.1:8848" target="_blank" rel="noopener">本机管理页面</a>
        <a class="btn" href="https://github.com/TSS-Small-sunshine/StardustFlashLink" target="_blank" rel="noopener noreferrer">在 GitHub 上查看</a>
      </div>
      <p class="hint" style="margin-top:14px;">
        本页面仅监听本机回环地址（127.0.0.1），局域网内其他设备无法访问；所有配置、密码与日志文件都保存在程序所在的数据目录中，删除目录即彻底清除。
      </p>
    </div>

    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">管理操作</h2>
      </div>
      <div class="btn-row">
        <button class="btn btn-secondary" id="btn-restart" type="button">重启服务</button>
        <button class="btn btn-danger" id="btn-uninstall" type="button">卸载服务</button>
        <!-- v1.3 新增：升级历史按钮 -->
        <button class="btn btn-secondary" id="btn-update-history" type="button">查看升级历史</button>
        <!-- v2.0.0 新增：查看更新日志按钮 -->
        <button class="btn btn-secondary" id="btn-changelog" type="button">查看更新日志</button>
      </div>
      <p class="hint" id="admin-hint" style="margin-top:14px;"></p>
    </div>

    <div class="card section">
      <div class="section-head">
        <h2 class="section-title">日志与诊断</h2>
        <p class="section-desc" style="margin:0;">业务日志超过 5 MB、升级日志超过 2 MB 会自动轮转（各留几份），不必再手动清理。</p>
      </div>
      <div id="log-list" class="hint" style="margin:0 0 12px 0;">正在读取日志占用...</div>
      <div class="btn-row">
        <button class="btn btn-secondary" id="btn-logs-refresh" type="button">刷新占用</button>
        <a class="btn btn-secondary" href="/api/diagnostics">下载诊断包（已脱敏）</a>
      </div>
      <p class="hint" style="margin-top:10px;">
        诊断包 = 版本 / 运行环境 / 服务状态 + 脱敏后的配置 + 各日志尾部（每个 ≤ 512 KB）。
        账号已打码、MAC 已打码、<strong>不含密码</strong>；校园网内网 IP 与 Wi-Fi 名会保留（排障需要）。
        反馈问题时把它发给维护者即可。
      </p>
    </div>
  </section>
</main>

<div class="toasts" id="toasts" role="region" aria-live="polite" aria-label="通知"></div>

<!-- v1.3 新增：升级历史弹窗（默认隐藏，由 JS 控制） -->
<div id="update-history-modal" class="update-modal" hidden role="dialog" aria-modal="true" aria-labelledby="update-history-title">
  <div class="update-modal-backdrop" id="update-history-backdrop"></div>
  <div class="update-modal-card card">
    <div class="section-head" style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
      <h2 class="section-title" id="update-history-title">升级历史</h2>
      <button class="icon-btn" id="update-history-close" type="button" aria-label="关闭"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6.4 6.4 17.6 17.6M17.6 6.4 6.4 17.6"/></svg></button>
    </div>
    <p class="hint" id="update-history-path" style="margin:0 0 10px;"></p>
    <pre class="update-modal-log" id="update-history-log">加载中…</pre>
    <div class="btn-row" style="margin-top:14px;justify-content:flex-end;">
      <button class="btn btn-secondary" id="update-history-refresh" type="button">刷新</button>
      <button class="btn" id="update-history-close-btn" type="button">关闭</button>
    </div>
  </div>
</div>

<!-- v2.0.0 新增：更新日志弹窗（默认隐藏，由 JS 控制） -->
<div class="changelog-modal" id="changelog-modal" hidden role="dialog" aria-modal="true" aria-labelledby="changelog-title">
  <div class="changelog-backdrop" id="changelog-backdrop"></div>
  <div class="changelog-dialog" role="document">
    <div class="changelog-header">
      <h2 class="changelog-title" id="changelog-title">更新日志</h2>
      <button class="changelog-close" id="changelog-close" type="button" aria-label="关闭"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6.4 6.4 17.6 17.6M17.6 6.4 6.4 17.6"/></svg></button>
    </div>
    <pre class="changelog-body" id="changelog-body">加载中...</pre>
  </div>
</div>

<style>
/* ============================================================
   弹窗（升级历史 / 更新日志）— 与主样式同一套亚克力语言
   放在 body 末尾，避免影响首屏关键样式解析
   ============================================================ */
.update-modal { position: fixed; inset: 0; z-index: 200; display: flex; align-items: center; justify-content: center; padding: 24px; }
.update-modal[hidden] { display: none; }
.update-modal-backdrop {
  position: absolute; inset: 0;
  background: rgba(0, 0, 0, 0.32);
  backdrop-filter: saturate(140%) blur(16px);
  -webkit-backdrop-filter: saturate(140%) blur(16px);
  animation: modalFade 0.22s ease;
}
[data-theme="dark"] .update-modal-backdrop { background: rgba(0, 0, 0, 0.58); }
.update-modal-card {
  position: relative; z-index: 1;
  width: min(760px, 100%); max-height: 82vh;
  display: flex; flex-direction: column;
  background: var(--material-solid);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-pop);
  animation: modalRise 0.26s cubic-bezier(0.32, 0.72, 0, 1);
}
.update-modal-log {
  flex: 1 1 auto; min-height: 260px; max-height: 58vh; overflow: auto;
  background: var(--terminal); color: var(--terminal-text);
  border: 1px solid var(--hairline); border-radius: var(--r-md);
  padding: 14px 16px;
  font-size: 12.5px; line-height: 1.65; white-space: pre-wrap; word-break: break-all;
  margin: 0;
}
@keyframes modalFade { from { opacity: 0; } to { opacity: 1; } }
@keyframes modalRise { from { opacity: 0; transform: translateY(10px) scale(0.99); } to { opacity: 1; transform: none; } }

.changelog-modal { position: fixed; inset: 0; z-index: 300; display: flex; align-items: center; justify-content: center; padding: 24px; }
.changelog-modal[hidden] { display: none; }
.changelog-backdrop {
  position: absolute; inset: 0;
  background: rgba(0, 0, 0, 0.32);
  backdrop-filter: saturate(140%) blur(16px);
  -webkit-backdrop-filter: saturate(140%) blur(16px);
  animation: modalFade 0.22s ease;
}
[data-theme="dark"] .changelog-backdrop { background: rgba(0, 0, 0, 0.58); }
.changelog-dialog {
  position: relative; width: min(760px, 100%); max-height: 82vh;
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--material-solid);
  border: 1px solid var(--hairline);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-pop);
  animation: modalRise 0.26s cubic-bezier(0.32, 0.72, 0, 1);
}
.changelog-header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 16px 18px; border-bottom: 1px solid var(--hairline);
}
.changelog-title { font-size: 16px; font-weight: 600; margin: 0; color: var(--text-strong); letter-spacing: -0.01em; }
.changelog-close, .update-modal-card .icon-btn {
  width: 32px; height: 32px; padding: 0;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--fill); border: 0; border-radius: 50%;
  color: var(--text-2); font-size: 13px; cursor: pointer;
  transition: background-color 0.16s, color 0.16s;
}
.changelog-close:hover, .update-modal-card .icon-btn:hover { background: var(--fill-2); color: var(--text); }
.changelog-body {
  flex: 1 1 auto; overflow: auto; padding: 18px 20px; margin: 0;
  font-size: 12.5px; line-height: 1.65; white-space: pre-wrap;
  color: var(--text); background: transparent;
}
/* v2.0.4.0：更新日志缺失时的兜底链接（后端返回 url 字段时才出现） */
.changelog-link {
  display: inline-block; margin-top: 12px;
  color: var(--accent); font-weight: 500; text-decoration: none;
}
.changelog-link:hover { text-decoration: underline; }
</style>

<script>
(function () {
  'use strict';

  /* ============================================================
     分区 1/6 · 工具函数
     ============================================================ */
  var THEME_KEY = 'drcom-theme';
  var POLL_STATUS_MS = 3000;
  var POLL_LOG_MS = 2000;
  var LOG_MAX_LINES = 4000;

  function $(id) { return document.getElementById(id); }

  /* —— 图标（内联 SVG，替代 emoji：风格与系统线性图标一致，且不受 emoji 字体影响）—— */
  var _ICO = {
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6.4 9.6 16.8 4 11.2"/></svg>',
    cross: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6.2 6.2 17.8 17.8M17.8 6.2 6.2 17.8"/></svg>',
    warn: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.4 2.6 20h18.8L12 3.4z"/><path d="M12 10.2v4.4M12 17.6v.1"/></svg>',
    info: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11.2v5.4M12 7.8v.1"/></svg>',
    down: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4.5v12.6M6.2 11.6 12 17.4l5.8-5.8"/></svg>',
    search: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.4"/><path d="M15.6 15.6 20.4 20.4"/></svg>',
    gear: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3.3"/><path d="M12 2.6v3M12 18.4v3M2.6 12h3M18.4 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1"/></svg>'
  };

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function text(el, v) { if (el) el.textContent = v; }

  function fmtIso(iso) {
    if (!iso) return '-';
    return String(iso).replace('T', ' ');
  }

  function fmtTimeOnly(iso) {
    if (!iso || typeof iso !== 'string') return '-';
    var i = iso.indexOf('T');
    var s = i >= 0 ? iso.slice(i + 1) : iso;
    return s.length >= 8 ? s.slice(0, 8) : s;
  }

  function fmtDuration(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var d = Math.floor(sec / 86400);
    var h = Math.floor((sec % 86400) / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    var out = [];
    if (d) out.push(d + ' 天');
    if (d || h) out.push(h + ' 小时');
    out.push(m + ' 分');
    out.push(s + ' 秒');
    return out.join(' ');
  }

  function fmtCountdown(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return (h > 0 ? pad(h) + ':' : '') + pad(m) + ':' + pad(s);
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' 字节';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(2) + ' MB';
  }

  function nowClock() {
    var d = new Date();
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  /* Toast：右上角浮出，默认 3 秒自动消失 */
  function toast(msg, type, ms) {
    var box = $('toasts');
    if (!box) return;
    var el = document.createElement('div');
    var icon = type === 'success' ? _ICO.check : (type === 'error' ? _ICO.cross : (type === 'warn' ? _ICO.warn : _ICO.info));
    el.className = 'toast ' + (type || 'info');
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.innerHTML = '<span class="toast-icon" aria-hidden="true">' + icon + '</span>' +
                   '<span class="toast-msg">' + esc(msg) + '</span>';
    box.appendChild(el);
    var life = ms || 3000;
    setTimeout(function () { el.classList.add('out'); }, life);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, life + 260);
  }

  /* ============================================================
     分区 2/6 · API 客户端（路径 / 方法 / 字段严格对应后端）
     ============================================================ */
  function getJson(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) { return r.json(); });
  }

  function postJson(url, body) {
    var opt = { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    return fetch(url, opt).then(function (r) { return r.json(); });
  }

  var API = {
    status: getJson.bind(null, '/api/status'),
    config: getJson.bind(null, '/api/config'),
    about: getJson.bind(null, '/api/about'),
    logPath: getJson.bind(null, '/api/log_file_path'),
    logTail: function (offset, max, level) {
      var url = '/api/log_tail?offset=' + encodeURIComponent(offset) + '&max=' + encodeURIComponent(max);
      if (level) url += '&level=' + encodeURIComponent(level);
      return getJson(url);
    },
    login: function () { return postJson('/api/login'); },
    saveConfig: function (cfg) { return postJson('/api/config', cfg); },
    savePassword: function (pwd) { return postJson('/api/password', { password: pwd }); },
    restart: function () { return postJson('/api/restart'); }
  };

  /* ============================================================
     分区 3/6 · 主题
     ============================================================ */
  function systemTheme() {
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }

  function applyTheme(theme, persist) {
    document.documentElement.setAttribute('data-theme', theme);
    var btn = $('btn-theme');
    if (btn) btn.setAttribute('aria-label', theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题');
    if (persist) {
      try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* 忽略隐私模式限制 */ }
    }
  }

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function bindTheme() {
    var btn = $('btn-theme');
    if (btn) {
      btn.addEventListener('click', function () {
        var next = currentTheme() === 'dark' ? 'light' : 'dark';
        applyTheme(next, true);
        toast(next === 'dark' ? '已切换到暗色主题' : '已切换到亮色主题', 'info', 2000);
      });
    }
    if (window.matchMedia) {
      var mq = window.matchMedia('(prefers-color-scheme: dark)');
      var onChange = function () {
        var saved = null;
        try { saved = localStorage.getItem(THEME_KEY); } catch (e) { saved = null; }
        if (saved !== 'dark' && saved !== 'light') applyTheme(systemTheme(), false);
      };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  }

  /* ============================================================
     分区 4/6 · 状态刷新
     ============================================================ */
  var statusTimer = null;
  var countdownDeadline = null;
  var countdownAt = '';
  var loginWatch = null;

  function setDot(id, tone, pulse) {
    var el = $(id);
    if (!el) return;
    el.className = 'dot dot-' + tone + (pulse ? ' dot-pulse' : '');
  }

  function setLiveness(tone, msg, pulse) {
    setDot('liveness-dot', tone, pulse);
    text($('liveness-text'), msg);
  }

  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  function renderStatus(s) {
    if (!s || typeof s !== 'object') { setLiveness('unknown', '状态未知', true); return; }

    /* —— 顶栏实时圆点 —— */
    if (s.online === true) setLiveness('ok', '已登录', false);
    else if (s.online === false) setLiveness('err', '未登录', false);
    else setLiveness('unknown', '状态未知', true);

    /* —— 网络可达性 —— */
    if (s.network_reachable === true) {
      setDot('kpi-net-dot', 'ok', false);
      text($('kpi-net-text'), '可达');
      $('kpi-net').className = 'kpi-value tone-ok';
    } else if (s.network_reachable === false) {
      setDot('kpi-net-dot', 'err', false);
      text($('kpi-net-text'), '不可达');
      $('kpi-net').className = 'kpi-value tone-err';
    } else {
      setDot('kpi-net-dot', 'unknown', true);
      text($('kpi-net-text'), '未知');
      $('kpi-net').className = 'kpi-value tone-muted';
    }
    var _netSub = '上次检查 ' + fmtTimeOnly(s.last_check_at);
    if (s.current_ssid) _netSub += ' · Wi-Fi: ' + s.current_ssid;
    if (s.guard_allowed === false) _netSub += ' · 不在校园网';
    text($('kpi-net-sub'), _netSub);

    /* —— 在线状态 —— */
    if (s.online === true) {
      setDot('kpi-online-dot', 'ok', false);
      text($('kpi-online-text'), '已登录');
      $('kpi-online').className = 'kpi-value tone-ok';
    } else if (s.online === false) {
      setDot('kpi-online-dot', 'err', false);
      text($('kpi-online-text'), '未登录');
      $('kpi-online').className = 'kpi-value tone-err';
    } else {
      setDot('kpi-online-dot', 'unknown', true);
      text($('kpi-online-text'), '未知');
      $('kpi-online').className = 'kpi-value tone-muted';
    }
    text($('kpi-online-sub'), '登录结果：' + (s.last_login_success === true ? '成功' : (s.last_login_success === false ? '失败' : '-')));

    /* —— 当前账号 —— */
    text($('kpi-account'), s.current_account ? s.current_account : '未配置');
    $('kpi-account').className = 'kpi-value small mono' + (s.current_account ? '' : ' tone-muted');
    text($('kpi-account-sub'), s.current_account ? '账号 + 运营商后缀' : '请到「配置」页填写账号');

    /* —— 上次登录时间 —— */
    text($('kpi-lastlogin'), s.last_login_at ? fmtIso(s.last_login_at) : '从未');
    $('kpi-lastlogin').className = 'kpi-value small' + (s.last_login_at ? '' : ' tone-muted');
    text($('kpi-lastlogin-sub'), s.last_login_at ? '最近一次登录尝试' : '尚无登录记录');

    /* —— 上次错误（有值 → 警示色边框） —— */
    var cardErr = $('card-error');
    if (s.last_error) {
      text($('kpi-error'), s.last_error);
      $('kpi-error').className = 'kpi-value small tone-warn';
      cardErr.className = 'card kpi kpi-alert';
      text($('kpi-error-sub'), '发生于 ' + fmtTimeOnly(s.last_check_at));
    } else {
      text($('kpi-error'), '无');
      $('kpi-error').className = 'kpi-value small tone-muted';
      cardErr.className = 'card kpi';
      text($('kpi-error-sub'), '最近一次检查未报错');
    }

    /* —— 下次检查：记录截止时间，由前端每秒倒计时（不再请求后端） —— */
    if (s.next_check_at && isNum(s.next_check_in_sec)) {
      countdownDeadline = Date.now() + Math.max(0, s.next_check_in_sec) * 1000;
      countdownAt = s.next_check_at;
    } else {
      countdownDeadline = null;
      countdownAt = '';
    }
    tickCountdown();

    /* —— 登录按钮 —— */
    if (loginWatch) {
      if (s.login_in_progress) loginWatch.sawProgress = true;
      else if (loginWatch.sawProgress || (s.last_check_at && s.last_check_at !== loginWatch.baseCheckAt)) {
        finishLogin('登录流程已完成，请查看上方状态与日志。', 'success');
      } else if (Date.now() > loginWatch.deadline) {
        finishLogin('等待登录结果超时，请查看日志确认。', 'warn');
      }
    }
    var btn = $('btn-login');
    if (btn) btn.disabled = !!s.login_in_progress || !!loginWatch;
  }

  function tickCountdown() {
    var el = $('kpi-next');
    if (!el) return;
    if (countdownDeadline === null) {
      el.textContent = '未计划';
      el.className = 'kpi-value mono tone-muted';
      text($('kpi-next-sub'), '周期自检未启用或尚未排期');
      return;
    }
    var left = Math.max(0, Math.round((countdownDeadline - Date.now()) / 1000));
    el.textContent = fmtCountdown(left);
    el.className = 'kpi-value mono' + (left <= 10 ? ' tone-warn' : '');
    text($('kpi-next-sub'), '剩余 ' + left + ' 秒 · 预计 ' + fmtTimeOnly(countdownAt) + ' 执行');
  }

  function pollStatus() {
    if (document.hidden) return;
    API.status().then(renderStatus).catch(function () {
      setLiveness('unknown', '服务无响应', true);
    });
  }

  function finishLogin(msg, type) {
    loginWatch = null;
    var btn = $('btn-login');
    if (btn) { btn.classList.remove('loading'); btn.disabled = false; }
    text($('btn-login-label'), '立即登录');
    text($('login-hint'), msg || '点击按钮立即触发一次完整的网络检查与登录流程。');
    if (type) toast(msg, type);
  }

  function bindLogin() {
    var btn = $('btn-login');
    if (!btn) return;
    btn.addEventListener('click', function () {
      if (loginWatch) return;
      btn.classList.add('loading');
      btn.disabled = true;
      text($('btn-login-label'), '正在登录…');
      text($('login-hint'), '已提交登录请求，正在等待服务端返回结果…');
      loginWatch = {
        deadline: Date.now() + 120000,
        sawProgress: false,
        baseCheckAt: null
      };
      API.status().then(function (s) { loginWatch && (loginWatch.baseCheckAt = s.last_check_at); }).catch(function () {});
      API.login().then(function (r) {
        if (r && r.triggered) {
          toast('已触发登录检查', 'success');
        } else if (r && r.reason === 'already_in_progress') {
          finishLogin('服务端已有登录流程在执行，请稍候。', 'warn');
        } else {
          finishLogin('登录请求被忽略：' + ((r && r.reason) || '未知原因'), 'warn');
        }
      }).catch(function () {
        finishLogin('登录请求失败，请检查服务是否在运行。', 'error');
      });
    });
  }

  function startStatusPolling() {
    if (statusTimer) return;
    pollStatus();
    statusTimer = setInterval(pollStatus, POLL_STATUS_MS);
  }

  /* ============================================================
     分区 5/6 · 配置读写
     ============================================================ */
  function clearErrors() {
    ['err-host', 'err-port', 'err-account', 'err-timeout', 'err-ui-port', 'err-pwd'].forEach(function (id) {
      text($(id), '');
    });
    ['cfg-host', 'cfg-port', 'cfg-account', 'cfg-network-timeout', 'cfg-ui-port', 'pwd-new', 'pwd-confirm'].forEach(function (id) {
      var el = $(id);
      if (el) el.classList.remove('is-invalid');
    });
  }

  function setFieldError(fieldId, errId, msg) {
    text($(errId), msg);
    var el = $(fieldId);
    if (el) el.classList.toggle('is-invalid', !!msg);
  }

  function isIntString(v) { return /^\d+$/.test(v); }
  function inRange(v, lo, hi) { var n = Number(v); return isFinite(n) && n >= lo && n <= hi; }

  function setPwdBadge(status) {
    var badge = $('pwd-badge');
    var card = $('card-password');
    if (!badge) return;
    if (status === 'set') {
      badge.textContent = '已设置';
      badge.className = 'badge badge-ok';
      if (card) card.className = 'card section';
    } else {
      badge.textContent = '未设置';
      badge.className = 'badge badge-err';
      if (card) card.className = 'card section card-alert';
    }
  }

  function loadConfig() {
    return API.config().then(function (c) {
      if (!c || typeof c !== 'object') throw new Error('bad payload');
      $('cfg-host').value = c.host || '';
      $('cfg-port').value = (typeof c.port === 'number') ? c.port : 80;
      $('cfg-account').value = c.account || '';
      $('cfg-suffix').value = c.suffix || '';
      $('cfg-auto-enabled').checked = !!c.auto_check_enabled;
      $('cfg-auto-interval').value = String(c.auto_check_interval_min || 30);
      $('cfg-network-timeout').value = c.network_wait_timeout_sec || 60;
      $('cfg-ui-port').value = c.ui_port || 8848;
      /* v2.0.5.0：网络位置守卫 */
      $('cfg-guard-enabled').checked = !!c.network_guard_enabled;
      $('cfg-guard-ssids').value = c.guard_allowed_ssids || '';
      $('cfg-guard-subnets').value = c.guard_allowed_subnets || '';
      setPwdBadge(c.password_status);
      clearErrors();
      text($('config-state'), '已从服务端读取，修改后点击保存');
    }).catch(function () {
      toast('读取配置失败，请稍后重试', 'error');
    });
  }

  function collectConfig() {
    return {
      host: $('cfg-host').value.trim(),
      port: parseInt($('cfg-port').value, 10),
      account: $('cfg-account').value.trim(),
      suffix: $('cfg-suffix').value,
      auto_check_enabled: $('cfg-auto-enabled').checked,
      auto_check_interval_min: parseInt($('cfg-auto-interval').value, 10),
      network_wait_timeout_sec: parseInt($('cfg-network-timeout').value, 10),
      ui_port: parseInt($('cfg-ui-port').value, 10),
      /* v2.0.5.0：网络位置守卫 */
      network_guard_enabled: $('cfg-guard-enabled').checked,
      guard_allowed_ssids: $('cfg-guard-ssids').value.trim(),
      guard_allowed_subnets: $('cfg-guard-subnets').value.trim()
    };
  }

  /* 纯前端校验，不通过则不发请求 */
  function validateConfig() {
    var ok = true;
    var host = $('cfg-host').value.trim();
    var port = $('cfg-port').value.trim();
    var account = $('cfg-account').value.trim();
    var timeout = $('cfg-network-timeout').value.trim();
    var uiPort = $('cfg-ui-port').value.trim();

    ['err-host', 'err-port', 'err-account', 'err-timeout', 'err-ui-port'].forEach(function (id) { text($(id), ''); });
    ['cfg-host', 'cfg-port', 'cfg-account', 'cfg-network-timeout', 'cfg-ui-port'].forEach(function (id) {
      var el = $(id); if (el) el.classList.remove('is-invalid');
    });

    if (!host) { setFieldError('cfg-host', 'err-host', '认证服务器地址不能为空'); ok = false; }
    if (!isIntString(port) || !inRange(port, 1, 65535)) {
      setFieldError('cfg-port', 'err-port', '端口必须是 1-65535 之间的整数'); ok = false;
    }
    if (!account) { setFieldError('cfg-account', 'err-account', '账号不能为空'); ok = false; }
    else if (!isIntString(account)) { setFieldError('cfg-account', 'err-account', '账号只能是数字（学号 / 工号）'); ok = false; }
    if (!isIntString(timeout) || !inRange(timeout, 10, 300)) {
      setFieldError('cfg-network-timeout', 'err-timeout', '超时必须是 10-300 之间的整数'); ok = false;
    }
    if (!isIntString(uiPort) || !inRange(uiPort, 1024, 65535)) {
      setFieldError('cfg-ui-port', 'err-ui-port', 'UI 端口必须是 1024-65535 之间的整数'); ok = false;
    }
    return ok;
  }

  function bindConfig() {
    var saveBtn = $('btn-save-config');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        if (!validateConfig()) {
          toast('表单校验未通过，请修正标红字段', 'warn');
          return;
        }
        saveBtn.disabled = true;
        API.saveConfig(collectConfig()).then(function (r) {
          if (r && r.ok) {
            toast('配置已保存', 'success');
            text($('config-state'), '已保存 · ' + nowClock());
          } else {
            toast('保存失败：' + ((r && r.error) || '未知错误'), 'error');
          }
        }).catch(function () {
          toast('保存请求失败，请检查服务状态', 'error');
        }).then(function () { saveBtn.disabled = false; });
      });
    }

    var pwdBtn = $('btn-save-pwd');
    if (pwdBtn) {
      pwdBtn.addEventListener('click', function () {
        var p1 = $('pwd-new').value;
        var p2 = $('pwd-confirm').value;
        text($('err-pwd'), '');
        if (!p1) { setFieldError('pwd-new', 'err-pwd', '账户登录密码不能为空'); return; }
        if (p1 !== p2) { setFieldError('pwd-confirm', 'err-pwd', '两次输入的账户登录密码不一致'); return; }
        pwdBtn.disabled = true;
        API.savePassword(p1).then(function (r) {
          if (r && r.ok) {
            toast('账户登录密码已更新', 'success');
            $('pwd-new').value = '';
            $('pwd-confirm').value = '';
            clearErrors();
            loadConfig();
          } else {
            setFieldError('pwd-new', 'err-pwd', (r && r.error) || '保存失败');
          }
        }).catch(function () {
          setFieldError('pwd-new', 'err-pwd', '请求失败，请检查服务状态');
        }).then(function () { pwdBtn.disabled = false; });
      });
    }
  }

  /* ============================================================
     分区 6/6 · 日志 / 关于 / 启动
     ============================================================ */
  var logOffset = 0;
  var logLines = [];
  var logTimer = null;
  var logUpdatedAt = null;
  var logLevel = '';  /* 当前等级筛选：'' 表示全部 */

  function renderLog() {
    var box = $('log-box');
    if (!box) return;
    var keep = box.scrollTop;
    var keyword = ($('log-filter').value || '').trim().toLowerCase();
    var view = logLines;
    if (keyword) {
      view = logLines.filter(function (l) { return l.toLowerCase().indexOf(keyword) !== -1; });
    }
    box.textContent = view.length ? view.join('\n') : '暂无日志';
    box.className = 'log-box' + (view.length ? '' : ' is-empty');
    if ($('log-autoscroll').checked) box.scrollTop = box.scrollHeight;
    else box.scrollTop = keep;
    text($('log-count'), (keyword ? view.length + ' / ' + logLines.length : view.length) + ' 行');
  }

  /* 增量拉取：只追加新内容，保留已有滚动位置 */
  function fetchLog(force) {
    if (document.hidden && !force) return Promise.resolve();
    if (force) { logOffset = 0; logLines = []; }
    return API.logTail(logOffset, 500, logLevel).then(function (r) {
      if (!r || typeof r !== 'object') return;
      var incoming = r.lines || [];
      if (incoming.length) {
        logLines = logLines.concat(incoming);
        if (logLines.length > LOG_MAX_LINES) logLines = logLines.slice(logLines.length - LOG_MAX_LINES);
      }
      if (isNum(r.next_offset)) logOffset = r.next_offset;
      logUpdatedAt = nowClock();
      renderLog();
      text($('log-offset'), 'offset ' + logOffset);
      text($('log-size'), fmtBytes(r.total_size || 0));
      text($('log-updated'), '更新于 ' + logUpdatedAt);
    }).catch(function () {
      text($('log-updated'), '拉取失败');
    });
  }

  function reloadLog() { return fetchLog(true); }

  function bindLog() {
    var filter = $('log-filter');
    if (filter) filter.addEventListener('input', renderLog);
    var auto = $('log-autoscroll');
    if (auto) auto.addEventListener('change', function () { if (auto.checked) renderLog(); });
    var refresh = $('btn-log-refresh');
    if (refresh) refresh.addEventListener('click', function () {
      reloadLog().then(function () { toast('日志已刷新', 'success', 2000); });
    });
    var dl = $('btn-log-download');
    if (dl) dl.addEventListener('click', function () {
      var a = document.createElement('a');
      a.href = '/api/log_download';
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      toast('已开始下载完整日志', 'info', 2000);
    });
    /* —— 等级 chip 筛选：切 chip → 重拉（level 不同了 offset 不能再叠加） —— */
    var group = $('log-level-filter');
    if (group) {
      var chips = Array.prototype.slice.call(group.querySelectorAll('.chip-level'));
      chips.forEach(function (chip) {
        chip.addEventListener('click', function () {
          var picked = chip.getAttribute('data-level') || '';
          if (picked === logLevel) return;
          chips.forEach(function (c) {
            var on = c === chip;
            c.classList.toggle('chip-active', on);
            c.setAttribute('aria-pressed', on ? 'true' : 'false');
          });
          logLevel = picked;
          reloadLog();
        });
      });
    }
  }

  function startLogPolling() {
    if (logTimer) return;
    fetchLog(true);
    logTimer = setInterval(function () { fetchLog(false); }, POLL_LOG_MS);
  }

  var uptimeBase = null; /* { sec: 服务端返回的秒数, at: 本地读取时刻 } */

  function loadAbout() {
    return API.about().then(function (r) {
      if (!r || typeof r !== 'object') return;
      text($('ver-badge'), 'v' + (r.version || '-') + (r.codename ? ' ' + r.codename : ''));
      if (r.version_full) $('ver-badge').setAttribute('title', r.version_full);
      text($('about-version'), r.version_full || r.version || '-');
      text($('about-started'), fmtIso(r.service_started_at));
      uptimeBase = { sec: Number(r.service_uptime_sec) || 0, at: Date.now() };
      text($('about-uptime'), fmtDuration(uptimeBase.sec));
      if (r.config_file) $('about-config').innerHTML = '<code class="path">' + esc(r.config_file) + '</code>';
      if (r.data_dir) $('about-data').innerHTML = '<code class="path">' + esc(r.data_dir) + '</code>';
      if (r.log_file) $('about-log').innerHTML = '<code class="path">' + esc(r.log_file) + '</code>';
    }).catch(function () {
      text($('about-version'), '读取失败');
    }).then(function () {
      /* /api/log_file_path 提供日志文件的绝对路径，作为权威来源优先展示 */
      return API.logPath().then(function (p) {
        if (p && p.path) $('about-log').innerHTML = '<code class="path">' + esc(p.path) + '</code>';
      }).catch(function () { /* 关于信息已由 /api/about 兜底 */ });
    });
  }

  function bindAbout() {
    var local = $('about-local');
    if (local && window.location && window.location.origin && window.location.origin.indexOf('http') === 0) {
      local.href = window.location.origin;
      local.textContent = '本机管理页面（' + window.location.origin + '）';
    }
    var btnRestart = $('btn-restart');
    if (btnRestart) btnRestart.addEventListener('click', function () {
      if (!window.confirm('确认重启服务？NSSM 将自动重新拉起进程。')) return;
      API.restart().then(function (r) {
        toast((r && r.message) || '服务正在重启，3 秒后请刷新页面', 'success');
      }).catch(function () { toast('重启请求失败', 'error'); });
    });
    var btnUninstall = $('btn-uninstall');
    if (btnUninstall) btnUninstall.addEventListener('click', function () {
      text($('admin-hint'), '请以管理员身份运行程序目录下的 uninstall.bat 完成卸载。');
    });
  }

  /* —— 标签页（分段控件，支持键盘左右方向键） —— */
  function activateTab(name, focus) {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
    tabs.forEach(function (t) {
      var on = t.dataset.tab === name;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      if (on && focus) t.focus();
    });
    ['status', 'config', 'log', 'about'].forEach(function (n) {
      var p = $('panel-' + n);
      if (p) p.classList.toggle('active', n === name);
    });
    if (name === 'config') loadConfig();
    else if (name === 'about') { loadAbout(); }
    else if (name === 'log') reloadLog();
  }

  function bindTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
    tabs.forEach(function (tab, idx) {
      tab.addEventListener('click', function () { activateTab(tab.dataset.tab, false); });
      tab.addEventListener('keydown', function (ev) {
        var next = null;
        if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') next = (idx + 1) % tabs.length;
        else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') next = (idx - 1 + tabs.length) % tabs.length;
        else if (ev.key === 'Home') next = 0;
        else if (ev.key === 'End') next = tabs.length - 1;
        if (next === null) return;
        ev.preventDefault();
        activateTab(tabs[next].dataset.tab, true);
      });
    });
  }

  /* 品牌 logo 兜底：/branding/* 取不到（手工拷贝文件等）时改用内联星芒标记，避免顶栏破图 */
  function bindBrand() {
    var img = $('brand-logo');
    if (!img || !img.addEventListener) return;
    img.addEventListener('error', function () {
      var mark = img.parentNode;
      if (!mark) return;
      mark.style.color = 'var(--accent)';
      mark.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        + '<path d="M12 2.2l1.9 6.1a2 2 0 0 0 1.3 1.3l6.1 1.9-6.1 1.9a2 2 0 0 0-1.3 1.3L12 20.8l-1.9-6.1a2 2 0 0 0-1.3-1.3L2.7 11.5l6.1-1.9a2 2 0 0 0 1.3-1.3L12 2.2z"/></svg>';
    }, { once: true });
  }

  function boot() {
    bindBrand();
    bindTheme();
    bindTabs();
    bindLogin();
    bindConfig();
    bindLog();
    bindAbout();
    bindUpdate();

    startStatusPolling();
    startLogPolling();
    startUpdatePolling();
    loadAbout();

    setInterval(tickCountdown, 1000);
    setInterval(function () {
      if (document.hidden) return;
      if (uptimeBase) text($('about-uptime'), fmtDuration(uptimeBase.sec + (Date.now() - uptimeBase.at) / 1000));
    }, 1000);

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { pollStatus(); fetchLog(false); pollUpdate(); }
    });
  }

  /* ============================================================
     分区 7/7 · 自动升级（v1.3 新增）— 状态轮询 / 横幅渲染 / 按钮
     ============================================================ */
  var updateTimer = null;
  var updateStateCache = null;
  var updateSuccessHideAt = 0;
  var POLL_UPDATE_MS = 5000;

  function getUpdateJson(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) { return r.json(); });
  }
  function postUpdateJson(url, body) {
    var opt = { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    return fetch(url, opt).then(function (r) { return r.json(); });
  }

  function renderUpdateBanner(data) {
    var banner = $('update-banner');
    if (!banner) return;
    var state = data && data.state;
    if (!state) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    banner.classList.remove('warn', 'error', 'success');
    var icon = $('update-banner-icon');
    var title = $('update-banner-title');
    var desc = $('update-banner-desc');
    var progress = $('update-progress');
    var pbar = $('update-progress-bar');
    var action = $('update-banner-action');
    action.hidden = true;
    progress.hidden = true;

    var ver = data.target_version || data.latest_version || '';
    var verStr = ver ? 'v' + ver : '';

    if (state === 'downloading') {
      banner.classList.add('warn');
      icon.innerHTML = _ICO.down;
      title.textContent = '正在下载新版本 ' + verStr;
      desc.textContent = data.progress_message || ('下载完成后将自动升级，服务将短暂中断约 60 秒');
      progress.hidden = false;
      pbar.style.width = (data.progress_pct || 0) + '%';
    } else if (state === 'checking') {
      icon.innerHTML = _ICO.search;
      title.textContent = '正在检查更新';
      desc.textContent = data.progress_message || '正在访问 GitHub API...';
    } else if (state === 'upgrading') {
      banner.classList.add('error');
      icon.innerHTML = _ICO.gear;
      title.textContent = '正在升级到 ' + verStr;
      desc.textContent = data.progress_message || '请稍候（约 60 秒）...';
    } else if (state === 'success') {
      banner.classList.add('success');
      icon.innerHTML = _ICO.check;
      title.textContent = '已升级到 ' + verStr;
      desc.textContent = data.progress_message || ('升级成功（' + fmtTimeOnly(data.last_check_at) + '）');
      action.hidden = false;
      action.textContent = '知道了';
      // v2.0.0 新增：在 action 按钮旁加一个「查看更新日志」快捷链接
      ensureChangelogShortcut(banner, action);
      // 7 秒后自动隐藏（也由 5 分钟服务端清理兜底）
      updateSuccessHideAt = Date.now() + 7000;
    } else if (state === 'error') {
      banner.classList.add('error');
      icon.innerHTML = _ICO.cross;
      title.textContent = '升级失败';
      desc.textContent = data.progress_message || data.last_error || '未知错误';
      action.hidden = false;
      action.textContent = '查看详情';
    } else {
      banner.hidden = true;
    }
  }

  function pollUpdate() {
    if (document.hidden) return;
    getUpdateJson('/api/update/status').then(function (r) {
      if (!r || typeof r !== 'object') return;
      updateStateCache = r;
      renderUpdateBanner(r);
      // 7 秒后自动隐藏绿 banner（前端兜底）
      if (r.state === 'success' && updateSuccessHideAt && Date.now() > updateSuccessHideAt) {
        var bnr = $('update-banner');
        if (bnr) bnr.hidden = true;
      }
    }).catch(function () { /* 静默：升级状态非关键 */ });
  }

  function startUpdatePolling() {
    if (updateTimer) return;
    pollUpdate();
    updateTimer = setInterval(pollUpdate, POLL_UPDATE_MS);
  }

  function bindUpdate() {
    // 关闭按钮（仅 success / error 时有效）
    var dismiss = $('update-banner-dismiss');
    if (dismiss) dismiss.addEventListener('click', function () {
      var b = $('update-banner'); if (b) b.hidden = true;
    });
    // 横幅 action 按钮（success=知道了 → 隐藏；error=查看详情 → 打开历史弹窗）
    var action = $('update-banner-action');
    if (action) action.addEventListener('click', function () {
      var s = updateStateCache && updateStateCache.state;
      if (s === 'success') {
        var b = $('update-banner'); if (b) b.hidden = true;
      } else if (s === 'error') {
        openUpdateHistoryModal();
      }
    });
    // 升级历史按钮（关于面板）
    var btnHistory = $('btn-update-history');
    if (btnHistory) btnHistory.addEventListener('click', openUpdateHistoryModal);
    // 更新日志按钮（关于面板，v2.0.0 新增）
    var btnCl = $('btn-changelog');
    if (btnCl) btnCl.addEventListener('click', openChangelogModal);
    // 弹窗关闭
    var modalClose = $('update-history-close');
    var modalCloseBtn = $('update-history-close-btn');
    var backdrop = $('update-history-backdrop');
    function _closeModal() {
      var m = $('update-history-modal'); if (m) m.hidden = true;
    }
    if (modalClose) modalClose.addEventListener('click', _closeModal);
    if (modalCloseBtn) modalCloseBtn.addEventListener('click', _closeModal);
    if (backdrop) backdrop.addEventListener('click', _closeModal);
    // 弹窗刷新
    var refreshBtn = $('update-history-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', function () {
      loadUpdateHistoryLines();
    });
    // ESC 关闭弹窗
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') {
        var m = $('update-history-modal');
        if (m && !m.hidden) m.hidden = true;
        var cm = $('changelog-modal');
        if (cm && !cm.hidden) cm.hidden = true;
      }
    });
    // 更新日志弹窗关闭按钮（v2.0.0 新增）
    var clClose = $('changelog-close');
    var clBackdrop = $('changelog-backdrop');
    if (clClose) clClose.addEventListener('click', closeChangelogModal);
    if (clBackdrop) clBackdrop.addEventListener('click', closeChangelogModal);
  }

  function loadUpdateHistoryLines() {
    var log = $('update-history-log');
    if (log) log.textContent = '加载中…';
    return getUpdateJson('/api/update/history').then(function (r) {
      if (!r || typeof r !== 'object') {
        if (log) log.textContent = '加载失败';
        return;
      }
      if (r.path) {
        var p = $('update-history-path');
        if (p) p.textContent = '日志路径：' + r.path;
      }
      var lines = r.lines || [];
      if (log) {
        log.textContent = lines.length ? lines.join('\n') : '（暂无升级日志）';
        log.scrollTop = log.scrollHeight;
      }
    }).catch(function () {
      if (log) log.textContent = '加载失败，请稍后重试';
    });
  }

  function openUpdateHistoryModal() {
    var m = $('update-history-modal'); if (!m) return;
    m.hidden = false;
    loadUpdateHistoryLines();
  }

  // —— 更新日志（v2.0.0 新增）——
  function openChangelogModal() {
    var modal = $('changelog-modal');
    var body = $('changelog-body');
    if (!modal || !body) return;
    body.textContent = '加载中...';
    modal.hidden = false;
    fetch('/api/changelog').then(function (r) { return r.json(); }).then(function (data) {
      if (data && data.content) {
        body.textContent = data.content;
        return;
      }
      // v2.0.4.0：安装包未随附 CHANGELOG.md 时给中文提示 + 可点的仓库链接
      // （后端 eula.py 在找不到文件时返回 url 字段；用 DOM API 拼装，不拼字符串 HTML）
      body.textContent = '加载失败：' + ((data && data.error) || '未知错误');
      var url = (data && data.url) || '';
      if (!url) return;
      var link = document.createElement('a');
      link.className = 'changelog-link';
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '在仓库查看完整更新日志 →';
      body.appendChild(link);
    }).catch(function () {
      body.textContent = '请求失败，请检查服务状态';
    });
  }

  function closeChangelogModal() {
    var modal = $('changelog-modal');
    if (modal) modal.hidden = true;
  }

  // 在升级成功横幅 action 按钮旁动态插入一个「查看更新日志」快捷链接
  function ensureChangelogShortcut(banner, action) {
    if (!banner || !action) return;
    var link = banner.querySelector('.changelog-shortcut');
    if (link) return;
    link = document.createElement('button');
    link.className = 'update-banner-link changelog-shortcut';
    link.type = 'button';
    link.textContent = '查看更新日志';
    link.style.cssText = 'margin-left:8px;background:transparent;border:0;color:var(--accent);cursor:pointer;font-size:13px;text-decoration:none;padding:6px 2px;';
    link.addEventListener('click', function (ev) {
      ev.stopPropagation();
      openChangelogModal();
    });
    if (action.parentNode) {
      action.parentNode.insertBefore(link, action.nextSibling);
    }
  }

  /* —— 把升级字段纳入 loadConfig / collectConfig —— */
  /* 复用原 loadConfig（已读 /api/config + 填好所有原表单），在它的 .then 末尾再补两项新字段。
     不再发额外的 /api/config 请求。 */
  var _origLoadConfig = loadConfig;
  loadConfig = function () {
    return _origLoadConfig().then(function () {
      return API.config().then(function (c) {
        if (!c || typeof c !== 'object') return;
        var au = $('cfg-auto-update-enabled');
        if (au) au.checked = !!c.auto_update_enabled;
        var iv = $('cfg-update-interval');
        if (iv) iv.value = String(c.update_check_interval_hours || 6);
        var diskEl = $('cfg-update-disk');
        if (diskEl) diskEl.value = String(c.update_min_free_disk_mb || 200);
      }).catch(function () { /* 配置页：忽略二次拉取失败 */ });
    });
  };

  var _origCollectConfig = collectConfig;
  collectConfig = function () {
    var cfg = _origCollectConfig();
    cfg.auto_update_enabled = !!(($('cfg-auto-update-enabled') || {}).checked);
    var iv = parseInt(($('cfg-update-interval') || {}).value, 10);
    cfg.update_check_interval_hours = (iv === 12 || iv === 24) ? iv : 6;
    var disk = parseInt(($('cfg-update-disk') || {}).value, 10);
    cfg.update_min_free_disk_mb = (typeof disk === 'number' && !isNaN(disk) && disk >= 50 && disk <= 10240) ? disk : 200;
    return cfg;
  };

  // —— 配置导入导出 ——
  (function () {
    var exportBtn = $('btn-config-export');
    if (exportBtn) exportBtn.addEventListener('click', function () {
      window.location.href = '/api/config/export';
    });
    var importBtn = $('btn-config-import');
    var fileInput = $('config-import-file');
    if (importBtn && fileInput) {
      importBtn.addEventListener('click', function () { fileInput.click(); });
      fileInput.addEventListener('change', function () {
        var f = fileInput.files && fileInput.files[0];
        if (!f) return;
        toast('正在导入配置...', 'warn', 4000);
        fetch('/api/config/import', { method: 'POST', body: f })
          .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
          .then(function (r) {
            if (r.ok && r.body && r.body.ok) {
              var applied = (r.body.applied || []).join(', ');
              toast('导入成功（' + applied + '）。需重启服务生效。', 'success', 6000);
            } else {
              toast('导入失败: ' + ((r.body && r.body.error) || '未知错误'), 'error', 6000);
            }
          })
          .catch(function () { toast('请求失败，请检查服务状态', 'error', 6000); })
          .then(function () { fileInput.value = ''; });
      });
    }
  })();

  // —— 手动触发更新（v1.3.5 新增）——
  // 复用 /api/update/check 与 /api/update/install 端点（v1.3 已有）。
  (function () {
    var checkBtn = $('btn-update-check-now');
    var installBtn = $('btn-update-install-now');
    if (checkBtn) checkBtn.addEventListener('click', function () {
      toast('正在检查 GitHub 最新版本...', 'warn');
      postUpdateJson('/api/update/check')
        .then(function (data) {
          if (data && data.ok) {
            toast('检查完成：' + (data.message || 'OK'), 'success', 4000);
          } else {
            toast('检查失败: ' + ((data && data.error) || '未知错误'), 'error', 6000);
          }
          if (typeof pollUpdate === 'function') pollUpdate();
        })
        .catch(function () { toast('请求失败，请检查服务状态', 'error'); });
    });
    if (installBtn) installBtn.addEventListener('click', function () {
      if (!confirm('确认立即升级？\n\n升级期间（约 30-60 秒）：\n- 服务会短暂停止\n- Web UI 不可用\n- 进度通过升级状态横幅实时显示\n\n继续？')) return;
      toast('正在触发升级...', 'warn', 4000);
      postUpdateJson('/api/update/install')
        .then(function (data) {
          if (data && data.ok) {
            toast('升级已启动，请稍候（约 60 秒）', 'success', 6000);
          } else {
            toast('升级启动失败: ' + ((data && data.error) || '未知错误'), 'error', 6000);
          }
          if (typeof pollUpdate === 'function') pollUpdate();
        })
        .catch(function () { toast('请求失败', 'error'); });
    });
  })();

  // —— 版本回滚（v2.0.11.0 / P6-6）：列出升级前的备份，一键回退 ——
  // 回滚只还原代码文件；config.json / password.txt 一律不动（服务端保证）。
  (function () {
    var sel = $('rollback-version');
    var hint = $('rollback-hint');
    if (!sel || !hint) return;

    function render(data) {
      var items = (data && data.ok && data.backups) ? data.backups : [];
      sel.innerHTML = '';
      if (!items.length) {
        var empty = document.createElement('option');
        empty.value = '';
        empty.textContent = '（暂无备份）';
        sel.appendChild(empty);
        hint.textContent = '暂无可用备份：第一次自动升级成功后才会生成（每份含全部代码文件 + 逐个 sha256）。';
        return;
      }
      items.forEach(function (b) {
        var opt = document.createElement('option');
        opt.value = b.version;
        opt.textContent = 'v' + b.version
          + (b.created_at ? '（' + String(b.created_at).replace('T', ' ') + '）' : '')
          + (b.current ? ' · 当前' : '');
        opt.disabled = !!b.current;
        sel.appendChild(opt);
      });
      var pick = items.filter(function (b) { return !b.current; })[0];
      if (pick) sel.value = pick.version;
      hint.textContent = '当前 v' + ((data && data.current_version) || '?')
        + '；备份保留 ' + ((data && data.retention_days) || 7) + ' 天（至少留 '
        + ((data && data.keep_min) || 2) + ' 份）。回滚只还原代码文件，配置与密码不动。';
    }

    function load() {
      hint.textContent = '正在读取备份列表...';
      fetch('/api/rollback', { cache: 'no-store', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(render)
        .catch(function () { hint.textContent = '读取备份列表失败（服务未响应？）'; });
    }

    var refreshBtn = $('btn-rollback-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', load);

    var rollbackBtn = $('btn-rollback-now');
    if (rollbackBtn) rollbackBtn.addEventListener('click', function () {
      var v = sel.value;
      if (!v) { toast('没有可回滚的版本', 'warn', 4000); return; }
      if (!confirm('确认回滚到 v' + v + '？\n\n- 只还原代码文件：配置、密码、方案都不动\n'
                 + '- 服务会重启，Web UI 约 15 秒不可用\n'
                 + '- 回滚后如需再升级，点「立即升级」即可\n\n继续？')) return;
      toast('正在回滚到 v' + v + '...', 'warn', 4000);
      postUpdateJson('/api/rollback', { version: v })
        .then(function (data) {
          if (data && data.ok) {
            toast(data.message || '已回滚，服务正在重启', 'success', 8000);
          } else {
            toast('回滚失败: ' + ((data && data.error) || '未知错误'), 'error', 8000);
          }
          if (typeof pollUpdate === 'function') pollUpdate();
        })
        .catch(function () { toast('请求失败（服务可能正在重启）', 'error', 6000); });
    });

    load();
  })();

  // —— 日志与诊断（v2.0.12.0）：显示日志占用；诊断包走 <a href> 直接下载（下面这个 IIFE 只管占用）——
  (function () {
    var box = $('log-list');
    if (!box) return;
    function render(data) {
      var items = (data && data.ok && data.items) || [];
      if (!items.length) { box.textContent = '暂无日志文件。'; return; }
      box.innerHTML = '共 ' + ((data && data.total_h) || '?') + ' —— ' + items.map(function (it) {
        return '<span style="margin-right:14px;white-space:nowrap;">' + it.name + '：' + it.size_h
          + (it.rotate_hint ? '（上限 ' + it.rotate_hint + '）' : '') + '</span>';
      }).join('');
    }
    function load() {
      box.textContent = '正在读取日志占用...';
      fetch('/api/logs', { cache: 'no-store', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(render)
        .catch(function () { box.textContent = '读取日志占用失败（服务未响应？）'; });
    }
    var refresh = $('btn-logs-refresh');
    if (refresh) refresh.addEventListener('click', load);
    load();
  })();

  /* ===== 配置方案（v2.0.9.0 / B5）：教室 / 宿舍 / 家里 ===== */
  function renderProfiles(p) {
    var sel = $('profile-select');
    var items = (p && p.ok && p.items) ? p.items : [];
    if (sel) {
      sel.innerHTML = '';
      if (!items.length) {
        var empty = document.createElement('option');
        empty.value = '';
        empty.textContent = '（还没有方案）';
        sel.appendChild(empty);
      }
      items.forEach(function (it) {
        var opt = document.createElement('option');
        opt.value = it.name;
        opt.textContent = it.name + (it.active ? '（当前）' : '') + (it.auto ? ' · 自动' : '');
        sel.appendChild(opt);
      });
    }
    var cur = null;
    items.forEach(function (it) { if (it.active) cur = it; });
    var badge = $('profile-badge');
    if (badge) {
      badge.textContent = cur ? ('当前：' + cur.name) : (items.length ? (items.length + ' 个方案') : '未使用');
      badge.className = 'badge ' + (cur ? 'badge-ok' : 'badge-muted');
    }
    text($('profile-hint'), cur
      ? ('当前方案：' + cur.name + ' —— ' + cur.desc
         + (cur.match_ssids.length ? (' · 自动匹配 ' + cur.match_ssids.join('、')) : ' · 手动方案'))
      : (items.length
         ? '在列表里选一个方案，点「应用选中方案」即可切换（账号密码不受影响）。'
         : '还没有方案 —— 调好配置后点下面的「用当前配置保存」建一个（例如「教室」「宿舍」「家里」）。'));
    var auto = $('profile-auto-switch');
    if (auto) auto.checked = !!(p && p.auto_switch);
  }

  function loadProfiles() {
    getJson('/api/profiles').then(renderProfiles).catch(function () { renderProfiles(null); });
  }

  function profileAction(path, body, reloadAfter) {
    text($('err-profile'), '');
    postJson(path, body || {}).then(function (r) {
      if (r && r.ok) {
        toast('配置方案已更新', 'success', 3000);
        loadProfiles();
        /* 切方案会改表单里的字段 → 刷新一次页面，免得界面上还是旧值 */
        if (reloadAfter) setTimeout(function () { location.reload(); }, 700);
      } else {
        text($('err-profile'), (r && r.error) || '操作失败');
      }
    }).catch(function () { text($('err-profile'), '请求失败，请检查服务状态'); });
  }

  (function () {
    var saveBtn = $('btn-profile-save');
    var applyBtn = $('btn-profile-apply');
    var delBtn = $('btn-profile-delete');
    var autoBox = $('profile-auto-switch');
    if (saveBtn) saveBtn.addEventListener('click', function () {
      var nameEl = $('profile-new-name'), matchEl = $('profile-match-ssids');
      var name = nameEl ? nameEl.value : '';
      if (!name.trim()) { text($('err-profile'), '请先填一个方案名'); return; }
      profileAction('/api/profiles/save', {
        name: name, match_ssids: matchEl ? matchEl.value : ''
      });
    });
    if (applyBtn) applyBtn.addEventListener('click', function () {
      var sel = $('profile-select');
      var name = sel ? sel.value : '';
      if (!name) { text($('err-profile'), '先在列表里选一个方案'); return; }
      profileAction('/api/profiles/activate', { name: name }, true);
    });
    if (delBtn) delBtn.addEventListener('click', function () {
      var sel = $('profile-select');
      var name = sel ? sel.value : '';
      if (!name) { text($('err-profile'), '先在列表里选一个方案'); return; }
      if (!confirm('删除方案「' + name + '」？\n\n只删这个名字，当前配置值保持不变。')) return;
      profileAction('/api/profiles/delete', { name: name });
    });
    if (autoBox) autoBox.addEventListener('change', function () {
      profileAction('/api/profiles/auto', { enabled: !!autoBox.checked });
    });
    loadProfiles();
  })();

  /* ===== 连接质量（v2.0.8.0 / B4）：进页面拉一次 + 手动刷新 =====
     数据 5 分钟才变一次，不参与 3 秒轮询（免得白算一遍 1MB 日志）。 */
  function fmtMs(ms) {
    if (!isNum(ms)) return '-';
    if (ms < 1000) return ms + ' ms';
    return (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + ' s';
  }

  function renderMetrics(m) {
    var d = (m && m.ok) ? m : null;
    var upt = d && isNum(d.uptime_pct) ? d.uptime_pct : null;
    text($('kpi-uptime'), upt === null ? '-' : upt + '%');
    $('kpi-uptime').className = 'kpi-value' + (upt === null ? ' tone-muted'
      : (upt >= 99 ? ' tone-ok' : (upt >= 95 ? ' tone-warn' : ' tone-err')));
    text($('kpi-uptime-sub'), d ? ('有效周期 ' + d.effective_checks + ' / 共 ' + d.checks
      + (d.skip > 0 ? '（跳过 ' + d.skip + '）' : '')) : '等待统计');

    text($('kpi-relogin'), d ? String(d.relogin) : '-');
    $('kpi-relogin').className = 'kpi-value' + (d && d.relogin > 0 ? '' : ' tone-muted');
    text($('kpi-relogin-sub'), d ? ('登录失败 ' + d.fail + ' · 网关不可达 ' + d.unreachable
      + (d.last_relogin_at ? ' · 最近 ' + fmtTimeOnly(d.last_relogin_at) : '')) : '近 7 天');

    text($('kpi-recover'), d ? fmtMs(d.avg_recover_ms) : '-');
    text($('kpi-recover-sub'), d && isNum(d.avg_attempts)
      ? ('平均 ' + d.avg_attempts + ' 次尝试可达' +
         (isNum(d.avg_reach_ms) ? ' · ' + fmtMs(d.avg_reach_ms) : ''))
      : '从开始检查到登录成功');

    text($('kpi-latency'), d && isNum(d.latency_ms) ? (d.latency_ms + ' ms') : '不可达');
    $('kpi-latency').className = 'kpi-value' + (!d || !isNum(d.latency_ms) ? ' tone-err'
      : (d.latency_ms < 120 ? ' tone-ok' : (d.latency_ms < 400 ? ' tone-warn' : ' tone-err')));
    text($('kpi-latency-sub'), d && d.last_unreachable_at
      ? ('最近一次不可达 ' + fmtTimeOnly(d.last_unreachable_at)) : '到校园网关的 TCP 握手');

    var bars = $('qbars');
    if (bars) {
      bars.innerHTML = '';
      var series = (d && d.series) ? d.series : [];
      var max = 1;
      series.forEach(function (s) { max = Math.max(max, (s.relogin || 0) + (s.fail || 0)); });
      series.forEach(function (s) {
        var n = (s.relogin || 0) + (s.fail || 0);
        var col = document.createElement('div'); col.className = 'qbar';
        var fill = document.createElement('div');
        fill.className = 'qbar-fill' + ((s.fail || 0) > 0 ? ' qfail' : '');
        fill.style.height = Math.round(n / max * 100) + '%';
        fill.title = s.date + '：掉线重登 ' + (s.relogin || 0) + ' · 失败 ' + (s.fail || 0)
          + ' · 检查 ' + (s.checks || 0) + ' 次';
        var lab = document.createElement('div'); lab.className = 'qbar-day';
        lab.textContent = String(s.date || '').slice(5);
        col.appendChild(fill); col.appendChild(lab);
        bars.appendChild(col);
      });
      text($('quality-note'), d
        ? ('检查 ' + d.checks + ' 次 · 统计于 ' + (d.generated_at || '-')
           + (d.avg_reach_ms ? '' : ' · 可达耗时从本版起记录'))
        : ('暂无数据' + (m && m.error ? '（' + m.error + '）' : '（日志为空）')));
    }
  }

  function loadMetrics() {
    getJson('/api/metrics?days=7').then(renderMetrics).catch(function () { renderMetrics(null); });
  }

  (function () {
    var btn = $('btn-metrics-refresh');
    if (btn) btn.addEventListener('click', loadMetrics);
    loadMetrics();
  })();

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
</script>
</body>
</html>
"""

