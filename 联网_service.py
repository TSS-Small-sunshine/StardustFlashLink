# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""
联网_service.py — 星尘闪连 (Stardust Flash Link) — Dr.COM 校园网自动登录（Web UI 配置版 v2.1.2.0）

架构
    主线程：阻塞在 ThreadingHTTPServer 上，提供 Web UI 与 REST API。
    守护线程 1：启动触发 — service start 后立即执行一次 run_once('startup')。
    守护线程 2：周期自检 — auto_check_enabled=True 时按间隔（含退避）循环执行 run_once('periodic')。
    信号处理：SIGTERM / SIGBREAK / SIGINT 触发优雅退出（设置 STOP_EVENT）。

线程模型
    STATE / BACKOFF 由独立 Lock 保护；run_once 全程持 RUN_LOCK，保证周期与手动触发串行。

依赖
    仅 Python 3 标准库（http.server / json / threading / signal / urllib / re）。
    无第三方依赖。

文件布局（脚本所在目录）
    config.json         — 运行配置（自动生成，含默认值）
    password.txt        — 校园网密码（UTF-8，第一行；缺失则跳过登录）
    logs/campus_login.log  — 业务日志
    logs/service_stdout.log — NSSM stdout（由 install.bat 配置）
    logs/service_stderr.log — NSSM stderr（由 install.bat 配置）
"""

import ctypes            # v2.0.13.0：运行时单实例锁（命名互斥体）
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer


# ============================================================
# 常量
# ============================================================
import ipaddress  # noqa: E402  v2.0.6.2：校验 guard_allowed_subnets 里的 CIDR
from version import VERSION, CODENAME  # noqa: E402  保持原行号兼容：VERSION 原本在 line 51
BACKOFF_LEVELS = [5, 10, 20, 40, 60]  # 分钟，索引 = 连续失败次数，封顶 60

DEFAULT_CONFIG = {
    "host": "172.16.80.3",
    "port": 80,
    "account": "",
    "suffix": "",
    "auto_check_enabled": True,
    "auto_check_interval_min": 30,
    "network_wait_timeout_sec": 60,
    # —— 网络位置守卫（v2.0.6.2 新增；默认**关闭**，保持旧行为）——
    # 背景：笔记本带回家 / 连热点时，服务会照样去探测校园网关，白跑认证请求（日志刷"校园网不可达"）。
    # 开启后：只在「当前 Wi-Fi 名命中 SSID 白名单」**或**「本机 IP 落在允许的网段里」时才检查。
    # 两个白名单都留空 = 等于没配 → 放行（并写一行日志提示）。
    "network_guard_enabled": False,
    "guard_allowed_ssids": "",      # 逗号分隔（中英文逗号都认），如 Campus-WiFi,Dorm-WiFi
    "guard_allowed_subnets": "",    # 逗号分隔 CIDR，如 172.16.0.0/12,10.0.0.0/8（有线也适用）
    # —— 配置方案（v2.0.9.0 / B5）：教室 / 宿舍 / 家里各存一份「位置相关字段」——
    # profiles: {"方案名": {"values": {...PROFILE_KEYS...}, "match_ssids": ["Campus-WiFi"]}}
    # active_profile: 当前方案名（空 = 没用方案，配置就是手改的）
    # profiles_auto_switch: 默认**关** —— 免得"我手动选的方案被系统改掉" ✗
    "profiles": {},
    "active_profile": "",
    "profiles_auto_switch": False,
    "ui_port": 8848,
    # —— 自动升级字段（v1.3 新增）——
    # P1-3：默认关闭。升级链路走第三方镜像 + SHA256 可绕过（P1-2 已修），
    # 且 /releases/latest 之前拿不到版本（P1-5 已改版本化非 prerelease release）。
    # 修好并验证过真机升级后再评估默认开启。
    "auto_update_enabled": False,
    "update_check_interval_hours": 6,
    "update_min_free_disk_mb": 200,
}

ALLOWED_SUFFIXES = ("", "@yd", "@dx", "@lt")
ALLOWED_INTERVALS = (5, 15, 30, 60, 120)
ALLOWED_UPDATE_INTERVALS = (6, 12, 24)


# —— 配置导入/导出（zip）——
CONFIG_EXPORT_SCHEMA_VERSION = 1
CONFIG_EXPORT_TOOL = "DrcomAutoLogin-Windows"
CONFIG_IMPORT_MAX_BYTES = 4 * 1024 * 1024  # 4MB 安全上限


# ============================================================
# 路径解析
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PASSWORD_FILE = os.path.join(BASE_DIR, "password.txt")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "campus_login.log")
# —— 升级相关路径 ——
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
NSSM_PATH = os.path.join(TOOLS_DIR, "nssm.exe")
UPGRADE_LOG_FILE = os.path.join(LOG_DIR, "upgrade.log")

# 启动时确保日志目录存在（幂等）
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError as exc:
    sys.stderr.write("无法创建日志目录 {}: {}\n".format(LOG_DIR, exc))


# ============================================================
# 共享状态（所有访问须持对应 Lock）
# ============================================================
STATE = {
    "service_started_at": None,
    # v2.1.1.0：最近一次「网络变化触发的检查」（没触发过就是 None ✓）
    # 为什么留着它：用户看到日志里突然多了一次登录时，能回答「为什么」✓
    "last_net_change_at": None,
    "last_net_change_why": None,
    "network_reachable": None,
    "online": None,
    "last_login_at": None,
    "last_login_success": None,
    "last_error": None,
    "last_check_at": None,
    "next_check_at": None,
    "next_check_in_sec": None,
    "current_account": "",
    # —— 网络位置守卫（v2.0.6.2）：当前 Wi-Fi 名 / 本次是否通过守卫（None=未启用）——
    "current_ssid": None,
    "guard_allowed": None,
    "login_in_progress": False,
    "service_uptime_sec": 0,
    # —— 自动升级字段（v1.3 新增）——
    "update_state": None,           # None / checking / downloading / upgrading / success / error
    "update_progress": 0,           # 0-100
    "update_progress_message": "",  # 描述当前阶段
    "update_lock": False,           # 是否正在升级（防并发）
    "update_available": False,      # 是否发现新版
    "update_latest_version": None,  # 远程最新版本号（不含 v 前缀）
    "update_latest_url": None,      # 远程安装包直链
    "update_last_check_at": None,   # 上次检查时间
    "update_last_error": None,      # 上次错误信息
    "update_target_version": None,  # 正在升级到的版本号
    "update_success_at": None,      # 升级成功时间戳（用于自动清理绿 banner）
}

BACKOFF = {
    "consecutive_failures": 0,
    "current_minutes": BACKOFF_LEVELS[0],
    "until": None,
}

STATE_LOCK = threading.Lock()
BACKOFF_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()  # 串行化 run_once 多次调用
PWD_LOCK = threading.Lock()
UPDATE_LOCK = threading.Lock()  # 保护 update_lock 字段的并发读写


# ============================================================
# 密码（仅内存 + 文件，不入日志/响应）
# ============================================================
_PWD_VALUE = None  # None 表示未设置

# 安装包历史/现行模板提示行的可识别片段（v2.0.4.0）。
# 只有命中这些片段的行才被当作注释跳过；**不要**再用 `startswith("#")`
# 判断注释 —— 那会误杀以 `#` 开头的合法密码。
_PASSWORD_HINT_MARKERS = (
    "在此行写入你的校园网账号密码",
    "去掉本注释行",
)



def _is_password_hint(line):
    """True = 安装包自带模板提示行（唯一会被跳过的"注释"）。

    v2.0.4.0 回归修复：旧实现把**任何 `#` 开头**的行都当注释跳过。若用户密码
    本身以 `#` 开头（真机实测存在：形如 `#xxxxxxxx#`），整行会被吃掉 →
    服务认为"密码未设置" → 登录失败，且 Web UI 显示「未设置」，看起来就像
    「升级后配置文件丢了」。现在只按模板提示文本精确匹配，其余内容一律
    按密码原文处理。
    """
    return any(marker in line for marker in _PASSWORD_HINT_MARKERS)


# —— v2.1.1.0：按方案用不同的密码（多网络多账号）——
# 规则：
#   - 默认仍是 `password.txt` ✓（老用户零变化 ✓）
#   - 当前方案若存在 `password.<方案名>.txt`，**优先用它** ✓
#   - 界面上保存密码时写进「当前实际生效的那个文件」✓（不再让用户选 ✗）
# 密码**永远不会**被写进 config.json ✗ —— 配置文件会被导出、会进诊断包 ✓。
def password_file_for(profile_name):
    """给定方案名 → 它专属的密码文件路径（没方案名 → 默认 `password.txt` ✓）。"""
    name = ""
    try:
        mod = globals().get("_profiles_mod")
        if mod is not None and profile_name:
            name = mod.normalize_name(profile_name) or ""
    except Exception:  # noqa: BLE001 —— 方案名不合法就退回默认文件 ✓
        name = ""
    if not name:
        return PASSWORD_FILE
    return os.path.join(BASE_DIR, "password.{}.txt".format(name))


def active_password_file():
    """当前**实际生效**的密码文件：方案专属文件存在就用它，否则 `password.txt` ✓。"""
    try:
        cfg = _load_config() or {}
    except Exception:  # noqa: BLE001 —— 配置还没就绪时一律退回默认 ✓
        cfg = {}
    candidate = password_file_for(cfg.get("active_profile") or "")
    if candidate != PASSWORD_FILE and os.path.isfile(candidate):
        return candidate
    return PASSWORD_FILE


def _load_password_from_disk():
    """从「当前生效的密码文件」读入 _PWD_VALUE。文件不存在或内容全是模板提示 → None。

    规则（v2.0.4.0；v2.1.1.0 起文件可按方案不同 ✓）：
      - 空行        → 跳过
      - 模板提示行  → 跳过（见 _is_password_hint）
      - 其余任何行  → 视为密码原文（**包括以 `#` 开头的密码**）
      - 用 utf-8-sig 读，容忍手工编辑时留下的 BOM（否则首字符会带 \\ufeff）
    """
    global _PWD_VALUE
    path = active_password_file()
    with PWD_LOCK:
        if not os.path.isfile(path):
            _PWD_VALUE = None
            return None
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                line = ""
                for raw in f:
                    s = raw.strip()
                    if not s or _is_password_hint(s):
                        continue
                    line = s
                    break
        except (OSError, UnicodeDecodeError) as exc:
            logger.error("读取 %s 失败: %s", os.path.basename(path), exc)
            _PWD_VALUE = None
            return None
        pwd = line.strip()
        _PWD_VALUE = pwd if pwd else None
        return _PWD_VALUE


def _get_password():
    with PWD_LOCK:
        return _PWD_VALUE


def _save_password_to_disk(password, profile=None):
    """写入密码文件（原子写：唯一 tmp → replace，v2.0.6.2 起 tmp 名带 pid）。

    v2.1.3.0：`profile` 给定时写**那个方案的专属密码文件**（`password.<方案名>.txt`）✓；
    不给 = 写「当前生效的密码文件」（老行为，一个字符都没变 ✓）。

    ⚠️ 两个容易写错的点：
      1. 只有写进**当前生效的那个文件**时才更新内存里的 `_PWD_VALUE` —— 给别的方案存
         密码绝不该把正在用的密码换掉 ✗；
      2. 「是不是当前生效文件」必须**写完之后再算**：第一次给当前方案建专属密码时，
         写之前 `active_password_file()` 还会回退到公共文件 ✗（那时专属文件还不存在）。
    """
    global _PWD_VALUE
    if not isinstance(password, str) or len(password) < 1:
        raise ValueError("password 必须是非空字符串")
    if profile:
        path = password_file_for(profile)
        if path == PASSWORD_FILE:
            # fail-closed：方案名不合法就别悄悄写到公共文件上 ✗（那会改掉别人的密码）
            raise ValueError("方案名不合法：{!r}".format(profile))
    else:
        path = active_password_file()
    tmp = "{}.{}.tmp".format(path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(password.rstrip("\r\n") + "\n")
    os.replace(tmp, path)
    if os.path.abspath(path) == os.path.abspath(active_password_file()):
        with PWD_LOCK:
            _PWD_VALUE = password
    return path


def profile_has_own_password(profile_name):
    """该方案有没有**专属密码文件**（没有 = 用公共 `password.txt` ✓）。"""
    path = password_file_for(profile_name)
    return path != PASSWORD_FILE and os.path.isfile(path)


def remove_profile_password(profile_name):
    """删掉某个方案的专属密码文件（删完它回落公共 `password.txt` ✓）。返回是否真删了。

    ⚠️ 只删文件、**不动内存里的密码**：删的不是当前生效文件时，正在用的密码不该变 ✗；
    是当前生效文件的话，由调用方 `_load_password_from_disk()` 重新读一遍 ✓。
    """
    path = password_file_for(profile_name)
    if path == PASSWORD_FILE:
        return False
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("删除方案密码文件失败（%s）：%s", os.path.basename(path), exc)
        return False


# ============================================================
# 日志（统一走 logger，禁用 print）
# ============================================================
logger = logging.getLogger("campus_network")
logger.setLevel(logging.INFO)
logger.propagate = False  # 避免根 logger 重复输出

# v2.0.12.0：业务日志自动轮转。
# 以前是 `logging.FileHandler`（无限追加），README 里只能写「不自动轮转，可随时手动清理」——
# 真机 9 天就到 1.3 MB，一年下来十几 MB，纯属浪费（面板只读尾部）。
# 现在：单文件超过 5 MB 就滚成 `campus_login.log.1`，保留 `.1`~`.3`（合计 ≤ 20 MB）。
# metrics.py 会按 `.3 → .1 → 当前` 的顺序一起读，所以「近 7 天」的统计不会因为轮转断档 ✓。
LOG_ROTATE_MAX_BYTES = 5 * 1024 * 1024
LOG_ROTATE_BACKUPS = 3

_file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=LOG_ROTATE_MAX_BYTES, backupCount=LOG_ROTATE_BACKUPS, encoding="utf-8"
)
_file_handler.setFormatter(
    logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger.addHandler(_file_handler)


# ============================================================
# 配置：载入 / 保存 / 校验 / 默认
# ============================================================
def _default_config():
    return dict(DEFAULT_CONFIG)


def _validate_config(cfg):
    """返回错误信息列表。空列表 = 通过。"""
    errors = []
    if not isinstance(cfg, dict):
        return ["config 必须是对象"]

    host = cfg.get("host")
    if not isinstance(host, str) or not host.strip():
        errors.append("host 必须是非空字符串")

    port = cfg.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        errors.append("port 必须是 1-65535 之间的整数")

    account = cfg.get("account")
    # v2.0.14.0（P1-7）：**允许留空** —— 新装机还没填账号时，用户仍该能改「自动升级」这类
    # 与账号无关的开关（旧行为要求 account.isdigit()，于是**整个配置保存被挡住** ✗）；
    # 非空时仍必须是纯数字 ✓（协议层遇到空账号会跳过登录并提示，见 protocol.run_once）
    if not isinstance(account, str):
        errors.append("account 必须是字符串（可留空）")
    elif account and not account.isdigit():
        errors.append("account 必须是数字字符串（或留空）")

    suffix = cfg.get("suffix")
    if suffix not in ALLOWED_SUFFIXES:
        errors.append("suffix 必须是 空 / @yd / @dx / @lt 之一")

    enabled = cfg.get("auto_check_enabled")
    if not isinstance(enabled, bool):
        errors.append("auto_check_enabled 必须是布尔值")

    interval = cfg.get("auto_check_interval_min")
    if interval not in ALLOWED_INTERVALS:
        errors.append("auto_check_interval_min 必须是 5 / 15 / 30 / 60 / 120 之一")

    timeout = cfg.get("network_wait_timeout_sec")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not (10 <= timeout <= 300):
        errors.append("network_wait_timeout_sec 必须是 10-300 之间的整数")

    ui_port = cfg.get("ui_port")
    if not isinstance(ui_port, int) or isinstance(ui_port, bool) or not (1024 <= ui_port <= 65535):
        errors.append("ui_port 必须是 1024-65535 之间的整数")

    # —— 网络位置守卫（v2.0.6.2 新增）——
    guard_on = cfg.get("network_guard_enabled")
    if not isinstance(guard_on, bool):
        errors.append("network_guard_enabled 必须是布尔值")

    ssids = cfg.get("guard_allowed_ssids")
    if not isinstance(ssids, str):
        errors.append("guard_allowed_ssids 必须是字符串（逗号分隔的 Wi-Fi 名）")
    elif len(ssids) > 500:
        errors.append("guard_allowed_ssids 过长（≤500 字符）")

    subnets = cfg.get("guard_allowed_subnets")
    if not isinstance(subnets, str):
        errors.append("guard_allowed_subnets 必须是字符串（逗号分隔的 CIDR）")
    elif len(subnets) > 500:
        errors.append("guard_allowed_subnets 过长（≤500 字符）")
    else:
        for item in [x.strip() for x in subnets.replace("，", ",").split(",") if x.strip()]:
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                errors.append("guard_allowed_subnets 里有非法网段：{}".format(item))

    # —— 配置方案（v2.0.9.0 / B5）——
    # 方案内容交给 profiles.py 校验（形状 + 只允许 PROFILE_KEYS）；这里只管别把结构写坏。
    profs = cfg.get("profiles")
    if not isinstance(profs, dict):
        errors.append("profiles 必须是对象（方案名 → 方案内容）")
    elif _profiles_mod is not None and len(profs) > _profiles_mod.MAX_PROFILES:
        errors.append("方案数超过上限 {} 个".format(_profiles_mod.MAX_PROFILES))
    elif _profiles_mod is not None:
        for pname, pentry in profs.items():
            if not _profiles_mod.normalize_name(pname):
                errors.append("方案名不合法：{!r}".format(pname))
                continue
            if not isinstance(pentry, dict):
                errors.append("方案「{}」的内容必须是对象".format(pname))
                continue
            errors.extend("方案「{}」：{}".format(pname, e)
                          for e in _profiles_mod.validate_values(pentry.get("values") or {}))
    if not isinstance(cfg.get("active_profile", ""), str):
        errors.append("active_profile 必须是字符串")
    if not isinstance(cfg.get("profiles_auto_switch", False), bool):
        errors.append("profiles_auto_switch 必须是布尔值")

    # —— 自动升级字段（v1.3 新增）——
    auto_upd = cfg.get("auto_update_enabled")
    if not isinstance(auto_upd, bool):
        errors.append("auto_update_enabled 必须是布尔值")

    upd_intv = cfg.get("update_check_interval_hours")
    if upd_intv not in ALLOWED_UPDATE_INTERVALS:
        errors.append("update_check_interval_hours 必须是 6 / 12 / 24 之一")

    upd_disk = cfg.get("update_min_free_disk_mb")
    if not isinstance(upd_disk, int) or isinstance(upd_disk, bool) or not (50 <= upd_disk <= 10240):
        errors.append("update_min_free_disk_mb 必须是 50-10240 之间的整数")

    return errors


def _load_config():
    """读 config.json；缺失/损坏 → 写默认值并返回。"""
    if not os.path.isfile(CONFIG_FILE):
        cfg = _default_config()
        try:
            _save_config_raw(cfg)
            logger.warning("config.json 不存在，已生成默认值")
        except OSError as exc:
            logger.error("写入默认 config.json 失败: %s", exc)
        return cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config.json 根节点不是对象")
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.error("config.json 损坏或读取失败 (%s)，已回退默认值", exc)
        cfg = _default_config()
        try:
            _save_config_raw(cfg)
        except OSError:
            pass
        return cfg

    # 补齐缺失键（保留用户自定义键但保证默认值存在）
    merged = _default_config()
    for k, v in cfg.items():
        merged[k] = v
    return merged


def _save_config_raw(cfg):
    """原子写：唯一 tmp 名 → replace。

    v2.0.6.2：tmp 名带上 pid —— 原先固定用 `config.json.tmp`，两个写者（例如服务 + 手动
    跑的实例，或两个并发 POST）会往同一个文件里交错写，最终可能落盘一个半截 JSON。
    带上 pid + 原子 replace 后，并发只会是"最后写入者胜"，不会写出损坏文件。
    """
    tmp = "{}.{}.tmp".format(CONFIG_FILE, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, CONFIG_FILE)


def _save_config(cfg):
    """校验 + 写入。失败抛 ValueError。
    兜底：先按 DEFAULT_CONFIG 把缺失字段补全，再校验、再写盘。
    这样前端 collectConfig 漏字段或老 config.json 缺失 v1.3 字段时，
    仍能按默认值落盘而不是直接报错（前后端字段没收口的修复）。
    """
    merged = _default_config()
    for k, v in cfg.items():
        merged[k] = v
    cfg = merged
    errors = _validate_config(cfg)
    if errors:
        raise ValueError("; ".join(errors))
    _save_config_raw(cfg)
    # 更新 STATE.current_account
    with STATE_LOCK:
        STATE["current_account"] = "{}{}".format(cfg.get("account", ""), cfg.get("suffix", ""))


# ============================================================
# 状态/退避助手
# ============================================================
def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _set_state(**kwargs):
    with STATE_LOCK:
        STATE.update(kwargs)


def _snapshot_state():
    with STATE_LOCK:
        s = dict(STATE)
        # 实时计算 uptime
        started = s.get("service_started_at")
        if started:
            try:
                dt = datetime.fromisoformat(started)
                s["service_uptime_sec"] = int((datetime.now() - dt).total_seconds())
            except ValueError:
                s["service_uptime_sec"] = 0
        # next_check_in_sec 实时计算
        nxt = s.get("next_check_at")
        if nxt:
            try:
                dt = datetime.fromisoformat(nxt)
                s["next_check_in_sec"] = max(0, int((dt - datetime.now()).total_seconds()))
            except ValueError:
                s["next_check_in_sec"] = None
        else:
            s["next_check_in_sec"] = None
        return s


def _set_backoff():
    """失败时：递增退避级别，记录 until。"""
    with BACKOFF_LOCK:
        cur_failures = BACKOFF["consecutive_failures"] + 1
        idx = min(cur_failures - 1, len(BACKOFF_LEVELS) - 1)
        minutes = BACKOFF_LEVELS[idx]
        BACKOFF["consecutive_failures"] = cur_failures
        BACKOFF["current_minutes"] = minutes
        BACKOFF["until"] = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        until = BACKOFF["until"]
    logger.warning("登录失败，进入退避：连续 %s 次，下次重试 %s 分钟后（%s）",
                   cur_failures, minutes, until)


def _reset_backoff():
    with BACKOFF_LOCK:
        BACKOFF["consecutive_failures"] = 0
        BACKOFF["current_minutes"] = BACKOFF_LEVELS[0]
        BACKOFF["until"] = None


def _backoff_until():
    with BACKOFF_LOCK:
        return BACKOFF.get("until")


# ============================================================
# 网络操作 / run_once 已迁移到 protocol.py（v2.0.2 解耦）
# ============================================================
from protocol import run_once  # noqa: F401  协议层共享；main() 透传给 web_api
import protocol as _protocol_mod

# _attach 在 main() 里完成（STOP_EVENT 必须在 _attach 之前已存在）。

# ============================================================
# EULA / CHANGELOG IO 已迁移到 eula.py（v2.0.2 解耦）
# ============================================================
import eula as _eula_mod

# _eula_mod._attach() 在 main() 里调用（需要 BASE_DIR）。
# ============================================================
# Web API + _Handler + _HTML_PAGE 已迁移到 web_api.py（v2.0.2 解耦）
# ============================================================
import web_api as _web_api_mod

# _web_api_mod._attach() 在 main() 里调用（需要 BASE_DIR / STATE / cfg / logger 等）。
# ============================================================
# 自动升级（v1.3 新增）已迁移到 auto_update.py（v2.0.2 解耦）
# ============================================================
import auto_update as _auto_update_mod

# _auto_update_mod._attach() 在 main() 里调用（需要 STATE / LOCK / 路径 / 函数）。


# ============================================================
# 连接质量统计（v2.0.8.0 / B4）已进 metrics.py
#   —— 纯日志解析（campus_login.log），不落第二份状态文件：重启不清零、升级不丢
# ============================================================
# v2.0.8.1：**可选**模块 —— 缺了只是少一块面板，绝不能让整个服务起不来。
# 真机实测（2026-09-28）：安装回滚只删掉了 metrics.py，而 module 级 import 直接
# ModuleNotFoundError → 服务主进程秒退 → nssm（AppExit=Ignore）不重启 → Web UI 端口消失 ✗。
try:
    import metrics as _metrics_mod
except ImportError as _exc:      # 只可能是「文件没装上」这种情况
    _metrics_mod = None
    _METRICS_IMPORT_ERROR = "{}: {}".format(type(_exc).__name__, _exc)
else:
    _METRICS_IMPORT_ERROR = ""

# v2.0.9.0：配置方案模块（B5）—— 同样按「可选模块」对待（见上一条注释）
try:
    import profiles as _profiles_mod
except ImportError as _exc:
    _profiles_mod = None
    _PROFILES_IMPORT_ERROR = "{}: {}".format(type(_exc).__name__, _exc)
else:
    _PROFILES_IMPORT_ERROR = ""

# _metrics_mod._attach() 在 main() 里调用（只需 log_dir / base_dir / load_config / logger）。


# ============================================================
# 配置方案（v2.0.9.0 / B5）已进 profiles.py
#   —— 纯逻辑：方案 = 位置相关字段的快照；切换 = 合并进 config.json（先校验后写盘）
# ============================================================
import profiles as _profiles_mod

# _profiles_mod._attach() 在 main() 里调用（需要 load_config / save_config / validate_config）。


# ============================================================
# 后台线程
# ============================================================
def _startup_trigger():
    """启动后稍等几秒再跑首次检查（让 Web server 先就绪 + 网络稳定）。

    v2.0.2：包一层 try/except，避免 run_once 抛异常时 daemon 线程静默死亡
    （之前的写法如果 _load_config 或 run_once 出错，整个启动触发就废了，
    只能靠 run_periodic 的 30min interval 救场 —— 用户感知为「自启动不触发」）。
    """
    if STOP_EVENT.wait(3):
        return
    try:
        run_once("startup")
    except Exception as exc:  # noqa: BLE001
        logger.exception("startup trigger 未捕获异常: %s", exc)


# v2.0.14.0（P3-2）：周期自检循环体抛异常后的冷却时间（测试里会临时调小）
PERIODIC_ERROR_BACKOFF_SEC = 60

# —— v2.1.1.0：网络变化即触发（治「刚连上 Wi-Fi 要干等到下个周期才登录」）——
# 老行为：`STOP_EVENT.wait(wait_sec)` 一口气睡完整个间隔（最长 60 分钟）✗ ——
#   开机 / 睡醒 / 走到另一个 Wi-Fi 之后，哪怕网络早就通了，也要等下一个周期才登录 ✗
#   （用户感知：「Wi-Fi 连上了它却半天不登」）。
# 新行为：把长等待**拆成小步**，边走边看：
#   - 每跳（5 秒）做一次**不起进程**的探测：主用地址变没变（protocol.primary_local_ip ✓）
#   - 每 12 跳（约 1 分钟）才做一次要起进程的 Wi-Fi 名探测（protocol.get_current_ssid ✓）
#   - 地址变了 / Wi-Fi 名变了 → **立刻**检查（不退避语义、不动周期 ✓）
NETWATCH_TICK_SEC = 5
NETWATCH_SSID_EVERY_TICKS = 12


class NetworkWatcher:
    """记住上次看到的「地址 / Wi-Fi 名」，判断要不要提前检查 ✓。

    纯逻辑（不碰网络、不碰文件）→ 好测 ✓；真探测由调用方喂进来 ✓。
    """

    def __init__(self):
        self.address = None
        self.ssid = None

    def observe_address(self, address):
        """看主用地址变没变 → `(要立刻检查吗, 原因说明)` ✓。"""
        previous = self.address
        if previous == address:
            return False, ""
        self.address = address
        # 只有「拿到了地址」才算刚连上网 ✓；断网（变成空）不折腾（真查也会被守卫拦住 ✓）
        if address and address != previous:
            return True, "本机地址 {} → {}".format(previous or "（无）", address)
        return False, ""

    def observe_ssid(self, ssid):
        """看 Wi-Fi 名变没变（= 换场景 ✓）→ `(要立刻检查吗, 原因说明)` ✓。"""
        if not ssid:
            return False, ""            # 读不到名字就不乱判 ✓（与守卫的 fail-open 同口径 ✓）
        previous = self.ssid
        if previous == ssid:
            return False, ""
        self.ssid = ssid
        if previous is None:
            return False, ""            # 第一次探到名字不算「换场景」✓（启动那次已经查过 ✓）
        return True, "Wi-Fi {} → {}".format(previous, ssid)


def run_periodic():
    """周期自检：尊重 auto_check_enabled 与 BACKOFF.until。

    v2.0.14.0（P3-2）：循环体包一层兜底 —— 旧行为里 `_load_config()` 抛异常、
    或 `run_once()` 冒出未捕获异常，都会**直接杀掉这个线程** ✗：此后用户再也不会被
    自动登录，界面上也没有任何提示（日志里只有一行 traceback）。
    现在记一条 ERROR（带 traceback）+ 停 60 秒继续 —— 线程必须活着 ✓，也不刷屏 ✓。
    """
    logger.info("周期自检线程启动")
    while not STOP_EVENT.is_set():
        try:
            cfg = _load_config()
            if not cfg.get("auto_check_enabled", True):
                logger.info("auto_check_enabled=False，30s 后重新检查开关")
                if STOP_EVENT.wait(30):
                    return
                continue

            interval_sec = int(cfg.get("auto_check_interval_min") or 30) * 60

            # 计算 wait_sec：取 interval 与剩余退避时间的较小者。
            # 历史 bug（v2.0.2 修复）：原代码写 max(interval, delta)，导致
            #   - 开机早期网络未稳 → _startup_trigger 触发 run_once → wait_network 失败 → 5min 退避
            #   - run_periodic 第一次循环读 BACKOFF.until 后仍按 max 算出 30min interval
            #   - 用户感知：自启动后 30+ 分钟内没有任何登录尝试
            # 正确语义：退避已到期（delta<=0）→ 立即重试；否则取 min(interval, delta) 让退避生效
            bu = _backoff_until()
            wait_sec = interval_sec
            if bu:
                try:
                    bu_dt = datetime.fromisoformat(bu)
                    delta = (bu_dt - datetime.now()).total_seconds()
                    if delta <= 0:
                        # 退避到期：立即触发一次 run_once（不等 interval）
                        wait_sec = 0
                    elif delta < interval_sec:
                        # 退避 < interval：服从退避（按 delta 比 interval 大 → max 写法的旧 bug）
                        wait_sec = delta
                    # else: delta >= interval → 保持 interval（合理：周期性不能比 interval 还短）
                except ValueError:
                    pass

            # 写 next_check_at 用于 UI 显示
            with STATE_LOCK:
                STATE["next_check_at"] = (
                    datetime.now() + timedelta(seconds=wait_sec)
                ).isoformat(timespec="seconds")

            # —— v2.1.1.0：长等待拆成小步，边走边看网络变化 ✓ ——
            # （原来是 STOP_EVENT.wait(wait_sec) 一口气睡完，最长 60 分钟 ✗）
            watcher = NetworkWatcher()
            watcher.address = _protocol_mod.primary_local_ip(cfg["host"], cfg.get("port", 80))
            remaining = float(wait_sec)
            tick_index = 0
            net_change = ""
            while remaining > 0 and not STOP_EVENT.is_set():
                step = min(NETWATCH_TICK_SEC, remaining)
                if STOP_EVENT.wait(step):
                    return
                remaining -= step
                tick_index += 1

                changed, why = watcher.observe_address(
                    _protocol_mod.primary_local_ip(cfg["host"], cfg.get("port", 80))
                )
                # Wi-Fi 名探测要起进程（netsh / nmcli），别每 5 秒都来一次 ✗
                if not changed and tick_index % NETWATCH_SSID_EVERY_TICKS == 0:
                    changed, why = watcher.observe_ssid(_protocol_mod.get_current_ssid())
                if changed:
                    net_change = why
                    break

            if net_change:
                logger.info("网络变化（%s）→ 立刻检查，不等下一个周期", net_change)
                _set_state(
                    last_net_change_at=datetime.now().isoformat(timespec="seconds"),
                    last_net_change_why=net_change,
                )
                # reason 带 netwatch 前缀：日志里一眼能看出这次是「变化触发的」✓
                run_once("netwatch")
                continue

            if STOP_EVENT.is_set():
                return

            run_once("periodic")
        except Exception as exc:  # noqa: BLE001  周期线程绝不能死：停一会儿再来
            logger.exception("周期自检循环异常（线程继续活着，%s 秒后重试）: %s",
                             PERIODIC_ERROR_BACKOFF_SEC, exc)
            if STOP_EVENT.wait(PERIODIC_ERROR_BACKOFF_SEC):
                return



# ============================================================
# 启动 / 信号 / 主入口
# ============================================================
HTTP_SERVER = None  # 全局引用，便于信号处理关闭
STOP_EVENT = threading.Event()


def _on_signal(signum, frame):
    name = {signal.SIGTERM: "SIGTERM", signal.SIGBREAK: "SIGBREAK", signal.SIGINT: "SIGINT"}.get(signum, str(signum))
    logger.info("收到信号 %s，准备退出", name)
    STOP_EVENT.set()
    if HTTP_SERVER is not None:
        # HTTPServer.shutdown() 可从另一线程调用以唤醒 serve_forever()
        threading.Thread(target=HTTP_SERVER.shutdown, daemon=True).start()


# ============================================================
# 运行时单实例锁（v2.0.13.0 / P3-7）
# ============================================================
# `AppMutex` 只管安装器 GUI，服务自己一直没有锁 ✗ —— 手工再跑一份 联网_service.py 时，
# 线程与周期自检会**先跑起来**，直到绑 8848 失败才退出（那时已经干了不少事：
# 可能已经开始检查网络、甚至发起登录）✗。
# 现在用命名互斥体挡在真正干活之前；对**升级路径保持宽容**：
# 安装器刚 `nssm stop` 完、旧进程正在退出时，最多等 `SINGLETON_WAIT_SEC` 秒 ✓。
SINGLETON_MUTEX_NAME = "Local\\DrcomAutoLoginService"
SINGLETON_WAIT_SEC = 20
_SINGLETON_HANDLE = None      # 留住句柄：进程退出（或被系统回收）才释放
ERROR_ALREADY_EXISTS = 183


def _acquire_singleton(wait_sec=SINGLETON_WAIT_SEC):
    """拿运行时单实例锁。返回 True = 可以继续启动。

    拿不到互斥体本身（极罕见）按 fail-open 处理：宁可多跑一份，也不能让服务起不来 ✓。
    """
    global _SINGLETON_HANDLE
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (OSError, AttributeError):
        return True

    def _try():
        handle = kernel32.CreateMutexW(None, True, SINGLETON_MUTEX_NAME)
        if not handle:
            return None                    # 调不动 → fail-open
        if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            return handle                  # 新拿到（含首次创建）
        kernel32.CloseHandle(handle)
        return False                   # 已有人持有

    got = _try()
    if got is None:
        return True
    if got is not False:
        _SINGLETON_HANDLE = got
        return True
    deadline = time.time() + max(0, int(wait_sec))
    while time.time() < deadline:
        time.sleep(1.0)                    # 等旧实例退出（升级 / 重启时就是这个场景）
        got = _try()
        if got is None:
            return True
        if got is not False:
            _SINGLETON_HANDLE = got
            return True
    return False


def main():
    global HTTP_SERVER

    logger.info("=" * 60)
    logger.info("Dr.COM 自动登录服务启动（Web UI 配置版 v%s \"%s\"）", VERSION, CODENAME)

    # 0. 运行时单实例锁（v2.0.13.0 / P3-7）：拿不到就**什么都不做**直接退出 ✓
    #    （旧行为是先把线程/周期自检跑起来，直到绑 8848 失败才退出 ✗）
    if not _acquire_singleton():
        logger.error("已有 DrcomAutoLogin 服务实例在运行（互斥体 %s 被占）—— 本进程退出，"
                     "不再启动任何线程。若确认没有其它实例，请看任务管理器里的 python/pythonw；"
                     "升级 / 重启时这里最多等 %s 秒。",
                     SINGLETON_MUTEX_NAME, SINGLETON_WAIT_SEC)
        return 0

    # 1. 载入配置
    cfg = _load_config()
    _set_state(current_account="{}{}".format(cfg.get("account", ""), cfg.get("suffix", "")))

    # 2. 载入密码
    _load_password_from_disk()
    if _get_password() is None:
        logger.warning("password.txt 不存在或为空，请通过 Web UI (http://127.0.0.1:%s) 设置密码", cfg.get("ui_port", 8848))

    # 3. 注册信号
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _on_signal)

    # 4. 启动钩子**移到 4.9**（必须在 _auto_update_mod._attach 之后调用）：
    #    钩子内部走 _log_upgrade → _ensure_upgrade_log → LOG_DIR，
    #    而 LOG_DIR 由 _attach 注入。旧顺序（此处调用）每次都抛
    #    NameError: name 'LOG_DIR' is not defined → 钩子从未真正执行过。

    # 4.5 把共享状态注入 protocol 模块（必须在 run_once 被任何线程调用之前）
    _protocol_mod._attach(
        logger=logger,
        state=STATE,
        state_lock=STATE_LOCK,
        backoff=BACKOFF,
        backoff_lock=BACKOFF_LOCK,
        run_lock=RUN_LOCK,
        pwd_lock=PWD_LOCK,
        load_password_from_disk=_load_password_from_disk,
        get_password=_get_password,
        load_config=_load_config,
        set_state=_set_state,
        set_backoff=_set_backoff,
        reset_backoff=_reset_backoff,
        backoff_until=_backoff_until,
        now_iso=_now_iso,
        stop_event=STOP_EVENT,
    )
    # 4.6 把 BASE_DIR 注入 eula 模块（CHANGELOG / EULA IO 需要）
    _eula_mod._attach(base_dir=BASE_DIR)
    # 4.7 把共享状态注入 web_api 模块（HTTP 路由 / _Handler / _HTML_PAGE 需要）
    _web_api_mod._attach(
        logger=logger,
        run_lock=RUN_LOCK,
        base_dir=BASE_DIR,
        log_file=LOG_FILE,
        log_dir=LOG_DIR,
        state=STATE,
        state_lock=STATE_LOCK,
        pwd_lock=PWD_LOCK,
        config_file=CONFIG_FILE,
        password_file=PASSWORD_FILE,
        upgrade_log_file=UPGRADE_LOG_FILE,
        default_config=DEFAULT_CONFIG,
        allowed_suffixes=ALLOWED_SUFFIXES,
        allowed_intervals=ALLOWED_INTERVALS,
        allowed_update_intervals=ALLOWED_UPDATE_INTERVALS,
        load_config=_load_config,
        save_config=_save_config,
        save_password_to_disk=_save_password_to_disk,
        validate_config=_validate_config,
        snapshot_state=_snapshot_state,
        get_password=_get_password,
        now_iso=_now_iso,
        stop_event=STOP_EVENT,
        run_once_fn=run_once,
        auto_update_mod=_auto_update_mod,
        # v2.1.3.0：方案专属密码（/api/credentials 用）
        profile_has_own_password=profile_has_own_password,
        remove_profile_password=remove_profile_password,
        load_password_from_disk=_load_password_from_disk,
    )
    # 4.7b 连接质量统计（v2.0.8.0；v2.0.8.1 起带降级）：只要日志目录 + 配置读取 + logger
    if _METRICS_IMPORT_ERROR:
        logger.warning("连接质量面板不可用：metrics 模块导入失败（%s）—— 服务其它功能照常",
                       _METRICS_IMPORT_ERROR)
    if _metrics_mod is not None:
        _metrics_mod._attach(
            logger=logger,
            log_dir=LOG_DIR,
            base_dir=BASE_DIR,
            load_config=_load_config,
        )
    # 4.7c 配置方案（v2.0.9.0）：读写配置 + 复用同一套 _validate_config
    if _PROFILES_IMPORT_ERROR:
        logger.warning("配置方案不可用：profiles 模块导入失败（%s）—— 服务其它功能照常",
                       _PROFILES_IMPORT_ERROR)
    if _profiles_mod is not None:
        _profiles_mod._attach(
            logger=logger,
            load_config=_load_config,
            save_config=_save_config,
            validate_config=_validate_config,
            # v2.1.3.0：方案可以带自己的密码文件 → 切方案后要重读密码 ✓
            load_password_from_disk=_load_password_from_disk,
        )
        # 把「按 SSID 自动切方案」挂到协议层（protocol 只认回调，不 import profiles）。
        # ⚠️ 这里**绝对不能**再写 `import protocol as _protocol_mod` ✗ ——
        # 函数内的 import 会让该名字在**整个 main() 里**变成局部变量，
        # 于是上面更早的 `_protocol_mod._attach(...)` 直接 UnboundLocalError ✗
        # （v2.0.9.0 真机事故：服务启动即退、Web UI 端口消失）。
        # `_protocol_mod` 在模块顶部已经导入过，这里直接用即可 ✓。
        _protocol_mod._set_auto_profile(_profiles_mod.auto_switch)
    # 4.8 把共享状态注入 auto_update 模块（后台线程 / 升级流程需要）
    _auto_update_mod._attach(
        logger=logger,
        state=STATE,
        state_lock=STATE_LOCK,
        update_lock=UPDATE_LOCK,
        tools_dir=TOOLS_DIR,
        nssm_path=NSSM_PATH,
        upgrade_log_file=UPGRADE_LOG_FILE,
        log_dir=LOG_DIR,
        base_dir=BASE_DIR,
        load_config=_load_config,
        save_config=_save_config,
        now_iso=_now_iso,
        stop_event=STOP_EVENT,
    )

    # 4.9 启动钩子：检查是否刚升级过（升级结果确认 / AppExit 自愈 / 备份清理）。
    #     必须在 4.8 之后 —— 见上面第 4 步的说明。
    try:
        _auto_update_mod._post_upgrade_startup()
    except Exception as exc:  # noqa: BLE001
        logger.warning("启动钩子异常: %s", exc)

    # 5. 启动后台线程
    startup_thread = threading.Thread(target=_startup_trigger, name="startup-trigger", daemon=True)
    startup_thread.start()

    periodic_thread = threading.Thread(target=run_periodic, name="periodic-check", daemon=True)
    periodic_thread.start()

    # —— 自动升级后台线程（v1.3 新增）——
    auto_update_thread = threading.Thread(target=_auto_update_mod._auto_update_loop, name="auto-update", daemon=True)
    auto_update_thread.start()

    # 6. 启动 Web 服务器（主线程阻塞）
    ui_port = cfg.get("ui_port", 8848)
    try:
        HTTP_SERVER = ThreadingHTTPServer(("127.0.0.1", ui_port), _web_api_mod._Handler)
    except OSError as exc:
        logger.error("绑定 127.0.0.1:%s 失败: %s；请修改 config.json 的 ui_port 后重启", ui_port, exc)
        sys.stderr.write("FATAL: bind 127.0.0.1:{} failed: {}\n".format(ui_port, exc))
        return 1

    _set_state(service_started_at=_now_iso())
    logger.info("Web UI 已就绪: http://127.0.0.1:%s", ui_port)
    logger.info("启动线程与周期自检线程已启动")

    try:
        HTTP_SERVER.serve_forever()
    finally:
        STOP_EVENT.set()
        logger.info("HTTP server 已停止，等待后台线程退出...")
        # 等所有线程最多 3 秒
        for t in (startup_thread, periodic_thread, auto_update_thread):
            t.join(timeout=3)
        logger.info("服务退出")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 终极兜底
        try:
            logger.exception("main 未捕获异常: %s", exc)
        finally:
            sys.exit(1)
