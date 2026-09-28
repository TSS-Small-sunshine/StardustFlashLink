# -*- coding: utf-8 -*-
"""protocol.py — Dr.COM 校园网登录协议层。

职责范围：
    - wait_network() — 探测校园网网关可达性
    - discover_network() — 抓取本机 IP / MAC（Dr.COM 表单字段）
    - is_online() — JSONP 查在线状态
    - login() — JSONP 发起登录
    - run_once() — 串行执行一次「等网络 → 查在线 → 登录」

设计：
    - 协议层不直接持有锁；通过 STATE / BACKOFF / RUN_LOCK 与主进程共享状态。
    - 期望被 联网_service.py 调用；亦可独立单元测试。

依赖：
    - 仅 Python 3 标准库。
"""

import ipaddress  # noqa: E402  v2.0.5.0：网络位置守卫的 CIDR 匹配
import json
import logging
import random
import re
import socket
import subprocess  # v2.0.5.0：读当前 Wi-Fi 名（netsh wlan）
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta


# ============================================================
# 运行时引用（由 联网_service.py 在 import 时注入）
# ============================================================
# 这些名字必须在 联网_service.py 中先于 "from protocol import run_once" 完成定义。
# 实际加载顺序：联网_service.py 定义常量 / 锁 / 状态后再 from protocol import ... → 这里就能用。
_logger = None  # 真实 logger 延迟到 _attach() 中注入
_STATE = None
_STATE_LOCK = None
_BACKOFF = None
_BACKOFF_LOCK = None
_RUN_LOCK = None
_PWD_VALUE = None
_PWD_LOCK = None
_load_password_from_disk = None
_get_password = None
_load_config = None
_set_state = None
_set_backoff = None
_reset_backoff = None
_backoff_until = None
_now_iso = None
_STOP_EVENT = None


def _attach(*,
            logger,
            state,
            state_lock,
            backoff,
            backoff_lock,
            run_lock,
            pwd_lock,
            load_password_from_disk,
            get_password,
            load_config,
            set_state,
            set_backoff,
            reset_backoff,
            backoff_until,
            now_iso,
            stop_event):
    """由 联网_service.py 在 import 此模块后调用一次，注入共享对象。

    解耦目标：
        - protocol.py 不知道 STATE 字典的具体字段；
        - 主进程持有 STATE 的所有权，协议层只读写函数调用。
    """
    global _logger, _STATE, _STATE_LOCK, _BACKOFF, _BACKOFF_LOCK, _RUN_LOCK
    global _PWD_VALUE, _PWD_LOCK, _load_password_from_disk, _get_password
    global _load_config, _set_state, _set_backoff, _reset_backoff
    global _backoff_until, _now_iso, _STOP_EVENT
    _logger = logger
    _STATE = state
    _STATE_LOCK = state_lock
    _BACKOFF = backoff
    _BACKOFF_LOCK = backoff_lock
    _RUN_LOCK = run_lock
    _PWD_LOCK = pwd_lock
    _load_password_from_disk = load_password_from_disk
    _get_password = get_password
    _load_config = load_config
    _set_state = set_state
    _set_backoff = set_backoff
    _reset_backoff = reset_backoff
    _backoff_until = backoff_until
    _now_iso = now_iso
    _STOP_EVENT = stop_event


def _log(msg, *args, level=logging.INFO):
    if _logger is None:
        return
    if level == logging.INFO:
        _logger.info(msg, *args)
    elif level == logging.WARNING:
        _logger.warning(msg, *args)
    elif level == logging.ERROR:
        _logger.error(msg, *args)
    elif level == logging.DEBUG:
        _logger.debug(msg, *args)
    else:
        _logger.log(level, msg, *args)


# ============================================================
# 网络操作（与上版一致 + 接受 cfg 参数）
# ============================================================
def wait_network(host, port, timeout):
    """每 2s 探测 host:port，最多 timeout 秒。返回 bool。"""
    _log("等待网络 %s:%s 可用（最长 %ss）...", host, port, timeout)
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=3):
                # v2.0.8.0：「连接质量」面板靠这行算「平均可达耗时」（老日志没有这个字段，
                # 解析器要能容忍 —— 见 metrics.py 的 _ATTEMPT_RE）
                _log("网络已可达（第 %s 次尝试，耗时 %.2fs）", attempt, time.time() - t0)
                return True
        except OSError as exc:
            _log("等待中 (%s): %s", attempt, exc)
            # 此处 sleep 用在 retry 循环，不是长循环
            time.sleep(2)
    _log("等待 %ss 后 %s:%s 仍不可达", timeout, host, port, level=logging.ERROR)
    return False


def discover_network(host):
    """获取本机在校园网段的 IP 和 MAC，用于登录表单。

    策略：
      1. 优先从 chkstatus 响应里拿 v4ip / olmac（最准，跟登录账号绑定）；
      2. fallback 用 UDP socket connect 拿本机 IP；
      3. MAC 拿不到就空字符串（Dr.COM 网关允许 MAC 占位）。

    返回 (ip, mac) 元组；任意拿不到就空字符串。
    """
    ip = ""
    mac = ""
    try:
        url = "http://{}/drcom/chkstatus?callback=cb&jsVersion=4.X".format(host)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            txt = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'"v4ip"\s*:\s*"([^"]*)"', txt)
        if m:
            ip = m.group(1)
        m2 = re.search(r'"olmac"\s*:\s*"([^"]*)"', txt, re.I)
        if m2:
            mac = m2.group(1).upper().replace(":", "").replace("-", "")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        pass
    # fallback：UDP connect 拿本机 IP
    if not ip:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((host, 80))
                ip = sock.getsockname()[0]
            finally:
                sock.close()
        except OSError:
            pass
    return ip, mac


def is_online(host):
    """通过 chkstatus JSONP 查询在线状态（端口 80）。

    请求：GET http://host/drcom/chkstatus?callback=cb&jsVersion=4.X
    响应：cb({"result":1,"uid":"...","AC":"...","oltime":N,...})

    返回：
        True   已在线（result == 1）
        False  未在线（result == 0）
        None   网络异常 / 解析失败
    """
    url = "http://{}/drcom/chkstatus?callback=cb&jsVersion=4.X".format(host)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            txt = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _log("在线检查网络异常: %s", exc, level=logging.DEBUG)
        return None
    m = re.search(r"\((\{.*?\})\s*\)", txt, re.S)
    if not m:
        _log("在线检查响应非 JSONP: %r", txt[:120], level=logging.DEBUG)
        return None
    try:
        data = json.loads(m.group(1))
    except (ValueError, json.JSONDecodeError):
        _log("在线检查 JSON 解析失败: %r", txt[:120], level=logging.DEBUG)
        return None
    result = data.get("result")
    if result == 1:
        return True
    if result == 0:
        return False
    return None


def login(host, account, suffix, password, wlan_user_ip, wlan_user_mac):
    """Dr.COM JSONP 登录（GET /eportal/portal/login，端口 801）。

    请求：GET http://host:801/eportal/portal/login?callback=dr1234&...
    响应：dr1234({"result":1,"msg":"...","ret_code":0})

    返回 (success, msg)：
        success  True=成功（result == 1）/ False=失败
        msg      服务端 msg 字段或本地诊断信息（用于日志）
    """
    callback = "dr{}".format(random.randint(1000, 9999))
    params = {
        "callback":       callback,
        "login_method":   "1",
        "user_account":   "{}{}".format(account, suffix),
        "user_password":  password,
        "wlan_user_ip":   wlan_user_ip or "",
        "wlan_user_ipv6": "",
        "wlan_user_mac":  wlan_user_mac or "",
        "wlan_ac_ip":     "",
        "wlan_ac_name":   "",
        "terminal_type":  "1",
        "jsVersion":      "4.1.3",
        "lang":           "zh-cn",
        "v":              str(random.randint(1000, 9999)),
    }
    url = "http://{}:801/eportal/portal/login?{}".format(host, urllib.parse.urlencode(params))
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            txt = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _log("登录请求异常: %s", exc, level=logging.ERROR)
        return False, "请求失败: {}".format(exc)
    # 解析 JSONP：dr1234({...});
    m = re.search(r"\((\{.*?\})\s*\)\s*;?\s*$", txt, re.S)
    if not m:
        return False, "login 接口返回非 JSONP: {}".format(txt[:80])
    try:
        data = json.loads(m.group(1))
    except (ValueError, json.JSONDecodeError):
        return False, "login JSON 解析失败: {}".format(txt[:80])
    success = data.get("result") == 1
    msg = data.get("msg", "") or ""
    return success, msg


# ============================================================
# 网络位置守卫（v2.0.5.0）
#
# 目的：笔记本带回家 / 连热点时不要白跑认证。只在「当前 Wi-Fi 名命中 SSID 白名单」**或**
# 「本机 IP 落在允许的网段」时才真正执行检查。
# 判定原则：**fail-open** —— 两个白名单都没配、或压根读不到 Wi-Fi 名/IP 时一律放行。
# 对一个"自动登录"工具来说，宁可多试一次，也不能因为读不到 Wi-Fi 名就静默不干活。
# 判定逻辑做成纯函数（guard_allows），方便单测。
# ============================================================
def _split_csv(value):
    """逗号分隔 → 列表（中英文逗号都认，去空白、去空项）。"""
    return [x.strip() for x in (value or "").replace("，", ",").split(",") if x.strip()]


def get_current_ssid():
    """当前无线 SSID；读不到（有线 / 无 WLAN 网卡 / 非 Windows）返回 None。"""
    try:
        proc = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = proc.stdout or b""
    text = None
    for enc in ("utf-8", "gbk"):          # 中文 Windows 的 netsh 输出是 GBK
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if "BSSID" in line:               # BSSID 行里也含 "SSID"，必须先排除
            continue
        m = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1)
    return None


def get_local_ips():
    """本机 IPv4 地址（含默认出口 IP），已排除回环。取不到返回 []。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))   # UDP connect 不发包，只为拿默认出口 IP
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return [ip for ip in ips if ip and not ip.startswith("127.")]


def guard_allows(cfg, ssid, ips):
    """网络位置守卫判定（纯函数）。返回 (是否放行, 原因文案)。"""
    if not cfg.get("network_guard_enabled"):
        return True, ""
    ssid_list = _split_csv(cfg.get("guard_allowed_ssids"))
    subnet_list = _split_csv(cfg.get("guard_allowed_subnets"))
    if not ssid_list and not subnet_list:
        return True, ""                    # 开了守卫却没配白名单 = 没意义 → 放行
    if ssid and ssid_list and ssid in ssid_list:
        return True, ""
    for ip in ips or []:
        for item in subnet_list:
            try:
                if ipaddress.ip_address(ip) in ipaddress.ip_network(item, strict=False):
                    return True, ""
            except ValueError:
                continue                   # 配置里写了非法网段时已在 _validate_config 拦下
    if not ssid and not ips:
        return True, ""                    # 判断信息一个都没有 → fail-open
    return False, "不在校园网（SSID / 网段都不在白名单里）"


# ============================================================
# run_once — 一次完整检查
# ============================================================
def run_once(reason):
    """串行执行一次：等网络 → 查在线 → 登录。reason: startup / periodic / manual。

    与原 联网_service.py:run_once 行为完全一致；仅将全局状态读写改为注入对象。
    """
    # v2.0.4.5 修：改成**非阻塞**抢锁 —— 原先写的是 `with _RUN_LOCK:` 之后再判
    # `login_in_progress`，而 _RUN_LOCK 本身已经把并发串行化了，那个判断**永远为假**
    # （死逻辑）→ 连点「立即登录」会排队执行 N 次完整检查。真正的"忙"应当以锁为准：
    # 抢不到就立即返回，Web UI 那边收到 already_in_progress。
    if not _RUN_LOCK.acquire(blocking=False):
        _log("已有检查在进行中，跳过本次 (reason=%s)", reason)
        return
    try:
        with _STATE_LOCK:
            _STATE["login_in_progress"] = True

        cfg = None
        try:
            _log("=" * 40)
            _log("开始检查 (reason=%s)", reason)
            cfg = _load_config()

            # 1.5 网络位置守卫（v2.0.5.0）：不在校园网就跳过本次，省掉无谓的认证请求。
            # 注意：这里**不算失败**，所以不调 _set_backoff（不会拉长退避）。
            _ssid = get_current_ssid()
            _ips = get_local_ips()
            _allowed, _why = guard_allows(cfg, _ssid, _ips)
            _set_state(current_ssid=_ssid, guard_allowed=_allowed)
            if not _allowed:
                _log("网络位置不在白名单（SSID=%s / IP=%s），跳过本次检查",
                     _ssid or "未知", ",".join(_ips) or "未知")
                _set_state(network_reachable=None, online=None, last_error=_why)
                return

            host = cfg["host"]
            port = cfg["port"]
            account = cfg["account"]
            suffix = cfg["suffix"]
            interval_min = cfg["auto_check_interval_min"]

            # 更新当前账号显示
            _set_state(current_account="{}{}".format(account, suffix))

            # 1. 等网络
            if not wait_network(host, port, cfg["network_wait_timeout_sec"]):
                _set_state(network_reachable=False, online=None, last_error="校园网不可达")
                _set_backoff()
                return

            _set_state(network_reachable=True)

            # 2. 查在线
            if is_online(host):
                _set_state(online=True, last_error=None)
                _reset_backoff()
                _log("已在线，无需登录")
                return

            _set_state(online=False)

            # 3. 登录
            pwd = _get_password()
            if pwd is None:
                _log("密码未设置，跳过登录。请通过 Web UI 设置密码。", level=logging.WARNING)
                _set_state(last_error="密码未设置")
                # 不计入退避（用户操作问题，不是网络问题）
                return

            wlan_ip, wlan_mac = discover_network(host)
            ok, msg = login(host, account, suffix, pwd, wlan_ip, wlan_mac)
            now_iso = _now_iso()
            if ok:
                _log("登录成功: %s", msg)
                _set_state(
                    online=True,
                    last_login_at=now_iso,
                    last_login_success=True,
                    last_error=None,
                )
                _reset_backoff()
            else:
                _log("登录失败: %s", msg, level=logging.ERROR)
                _set_state(
                    last_login_success=False,
                    last_error="登录失败: {}".format(msg) if msg else "登录失败（账号或密码错误或网络异常）",
                )
                _set_backoff()

        except Exception as exc:  # noqa: BLE001 — 兜底写日志
            _log("run_once 未捕获异常: %s", exc, level=logging.ERROR)
            _set_state(last_error="内部异常: {}".format(exc))
            _set_backoff()
        finally:
            with _STATE_LOCK:
                _STATE["login_in_progress"] = False
                _STATE["last_check_at"] = _now_iso()
            # 更新 next_check_at：退避优先，否则按 interval
            bu = _backoff_until()
            with _STATE_LOCK:
                if bu:
                    _STATE["next_check_at"] = bu
                else:
                    next_min = (cfg or {}).get("auto_check_interval_min", 30)
                    _STATE["next_check_at"] = (
                        datetime.now() + timedelta(minutes=next_min)
                    ).isoformat(timespec="seconds")
    finally:
        # v2.0.4.5：RUN_LOCK 现在是手工非阻塞 acquire 的，必须自己释放
        # （原来靠 `with _RUN_LOCK:` 自动释放；用 try/finally 保证任何 return / 异常都释放）
        _RUN_LOCK.release()