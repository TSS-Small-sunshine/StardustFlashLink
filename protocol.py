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

import http.client  # v2.x 维护：BadStatusLine / IncompleteRead 也是网络异常
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
_auto_profile = None      # v2.0.9.0（B5）：按 SSID 自动切配置方案的回调（可空）


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


def _set_auto_profile(fn):
    """v2.0.9.0（B5）：注入「按当前 Wi-Fi 名自动切换配置方案」的回调。

    协议层只认这个回调（不 import profiles）；回调返回切到的方案名或空串。
    """
    global _auto_profile
    _auto_profile = fn


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
def _interruptible_wait(seconds):
    """可被 STOP_EVENT 提前打断的短睡眠（审计 C-18）。返回 True = 收到停止信号。"""
    ev = globals().get("_STOP_EVENT")
    if ev is not None:
        try:
            return bool(ev.wait(seconds))
        except AttributeError:      # 注入的不是 Event（单测替身）→ 退回普通 sleep
            pass
    time.sleep(seconds)
    return False


def wait_network(host, port, timeout):
    """每 2s 探测 host:port，最多 timeout 秒。返回 bool。

    审计 C-18：旧实现不可中断 —— 收到 SIGTERM / 用户点停止后仍要等完最长 300s，
    NSSM 停止超时到点会强杀进程，可能正好截断在途登录请求；而且每 2 秒一条「等待中」
    日志，一次不可达循环能刷 150 行。
    现在：每步都可被 _STOP_EVENT 打断（返回 False）；等待日志改为「前 3 次 + 之后每 10 次」
    记一条，失败行照旧（排障要看）。
    """
    _log("等待网络 %s:%s 可用（最长 %ss）...", host, port, timeout)
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        if globals().get("_STOP_EVENT") is not None and _STOP_EVENT.is_set():
            _log("等待网络被停止信号打断（已等 %s 次）", attempt)
            return False
        attempt += 1
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=3):
                # v2.0.8.0：「连接质量」面板靠这行算「平均可达耗时」（老日志没有这个字段，
                # 解析器要能容忍 —— 见 metrics.py 的 _ATTEMPT_RE）
                _log("网络已可达（第 %s 次尝试，耗时 %.2fs）", attempt, time.time() - t0)
                return True
        except OSError as exc:
            if attempt <= 3 or attempt % 10 == 0:
                _log("等待中 (%s): %s", attempt, exc, level=logging.DEBUG)
            if _interruptible_wait(2):
                _log("等待网络被停止信号打断（已等 %s 次）", attempt)
                return False
    _log("等待 %ss 后 %s:%s 仍不可达", timeout, host, port, level=logging.ERROR)
    return False


def primary_local_ip(host, port=80):
    """本机在「通往 host」这条路上用的源 IP（**UDP connect，不发包、不起进程** ✓）。

    为什么要它（v2.1.1.0）：网络变化监视每 5 秒看一次「地址变没变」✓ ——
    这时候**绝不能**去跑 `ipconfig`（一次几十毫秒、还要起进程 ✗），
    UDP connect 只让内核选一下路由，微秒级 ✓。

    拿不到（没有默认路由等）→ 返回空串 "" ✓。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((host, int(port)))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return ""


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
        # 审计 C-21：v4ip / olmac 来自网关响应，不能直接拼进登录 URL ——
        # 先过形状白名单（IP 用 ipaddress 校验、MAC 必须 12 位十六进制），不合格当空串。
        m = re.search(r'"v4ip"\s*:\s*"([^"]*)"', txt)
        if m:
            candidate = m.group(1).strip()
            try:
                ipaddress.ip_address(candidate)
                ip = candidate
            except ValueError:
                ip = ""
        m2 = re.search(r'"olmac"\s*:\s*"([^"]*)"', txt, re.I)
        if m2:
            candidate_mac = m2.group(1).upper().replace(":", "").replace("-", "")
            if re.fullmatch(r"[0-9A-F]{12}", candidate_mac):
                mac = candidate_mac
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


# ============================================================
# 门户跳转参数发现（v2.1.3.11）
#
# 为什么要它：认证请求必须带上门户认识的 AC 参数（wlanacip / wlanacname）。
# 老部署（SR8806-X-S）留空也能过，但 2026-11-20 真机实测新 AC（Ruijie_Ac_cc73fa）
# 留空会直接被判「AC认证失败」—— 同一个账号密码，补上 AC 参数后门户立刻正常受理
# （返回「已经在线」）。这些参数只在**未认证**时由门户 302 给出，所以登录前先探一次。
# ============================================================
PORTAL_PROBE_URLS = (
    "http://www.msftconnecttest.com/redirect",
    "http://connectivitycheck.gstatic.com/generate_204",
    "http://captive.apple.com/hotspot-detect.html",
    "http://detectportal.firefox.com/success.txt",
    "http://9.9.9.9/",
)

#: 门户跳转参数里各字段的形状白名单（与审计 C-21 同口径：拿到的值先验形状再拼进请求）
_AC_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_MAC_RE = re.compile(r"[0-9A-F]{12}")


def _grab_query_value(text, name):
    """从文本里的 query / JS 赋值 / JSON 片段抓一个参数（值形状由调用方再校验）✓。

    v2.1.3.12：不同版本的 AC 门户吐参数的方式不一样 —— 有的放在 302 的 Location query 里，
    有的只给 200 页面、把参数写进 JS 变量（`var wlanacip='...'` / `"wlanacname":"..."`）。
    这里三种形态都认；抓到的值仍要过 _safe_ip / 白名单，误命中的脏串会被丢弃 ✓。
    """
    if not text:
        return ""
    patterns = (
        r"(?i)[?&]" + re.escape(name) + r"=([^&\s\"'<>]+)",
        r"(?i)\b" + re.escape(name) + r"\s*[=:]\s*[\"']([^\"'<>\s]{1,128})[\"']",
        r"(?i)\b" + re.escape(name) + r"\s*=\s*([^&\s\"'<>;]{1,128})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return urllib.parse.unquote(match.group(1))
    return ""


def _safe_ip(value):
    try:
        ipaddress.ip_address(value)
        return value
    except (ValueError, TypeError):
        return ""


def _safe_mac(value):
    mac = (value or "").upper().replace(":", "").replace("-", "")
    return mac if _MAC_RE.fullmatch(mac) else ""


def _portal_param_from_text(text):
    """**纯函数**：从 302 的 Location / 门户页面文本里抠出 AC 与网络参数 ✓。

    只收形状合法的值（IP 过 ipaddress、AC 名走白名单、MAC 12 位十六进制、SSID 截 64）；
    其余一律空串（拿不到就走老逻辑：AC 参数留空，至少不会把脏数据拼进 URL ✗）。
    """
    out = {"ac_ip": "", "ac_name": "", "user_ip": "", "user_mac": "", "ssid": ""}
    if not text:
        return out
    out["ac_ip"] = _safe_ip(_grab_query_value(text, "wlanacip"))
    ac_name = _grab_query_value(text, "wlanacname")
    out["ac_name"] = ac_name if _AC_NAME_RE.fullmatch(ac_name or "") else ""
    out["user_ip"] = _safe_ip(_grab_query_value(text, "wlanuserip"))
    out["user_mac"] = _safe_mac(_grab_query_value(text, "mac"))
    out["ssid"] = _grab_query_value(text, "ssid")[:64]
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不让 urllib 自动跟随 302 —— 我们要读 Location 里的门户参数本身 ✓。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ARG002
        return None


def discover_portal_params(host="", timeout=6):
    """未认证时探一次门户跳转，拿 AC / 本机 IP / MAC / SSID（拿不到就全空）✓。

    只在 is_online() **明确 False** 之后调用（在线时门户不跳转，探也白探 ✗）。
    任何异常都吞掉 —— 这是「尽量拿到更准参数」的增强路径；失败仍走老逻辑（AC 留空），
    绝不因为探不到门户而让本次登录失败 ✗。

    返回 dict 额外带 `source`（location / body / 空）—— 排障时能看出参数从哪来的 ✓。
    v2.1.3.12：① 探测地址扩到 5 个（不同 AC 的门户拦截策略不一样，有的只拦某个探针域名）；
    ② 找到 / 找不到都写一行日志 —— 学校里有多个版本的 AC 时，**下次再有人报「AC认证失败」，
    先看这行日志就知道是"没发现参数"还是"参数不对"** ✓。
    """
    empty = {"ac_ip": "", "ac_name": "", "user_ip": "", "user_mac": "", "ssid": "", "source": ""}
    opener = urllib.request.build_opener(_NoRedirect)
    for url in PORTAL_PROBE_URLS:
        location = ""
        body = ""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
            try:
                with opener.open(req, timeout=timeout) as resp:
                    location = resp.headers.get("Location") or ""
                    body = resp.read(4096).decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                location = exc.headers.get("Location") or ""
                try:
                    body = exc.read(4096).decode("utf-8", errors="replace")
                except (OSError, ValueError):
                    body = ""
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
            continue
        for source, text in (("location", location), ("body", body)):
            found = _portal_param_from_text(text)
            if not (found["ac_ip"] or found["ac_name"]):
                continue
            found["source"] = source
            _log("门户参数发现（来源=%s）：wlanacip=%s wlanacname=%s wlanuserip=%s mac=%s ssid=%s",
                 source,
                 found["ac_ip"] or "（空）", found["ac_name"] or "（空）",
                 found["user_ip"] or "（空）", found["user_mac"] or "（空）",
                 found["ssid"] or "（空）")
            return found
    _log("门户参数未发现（%d 个探测地址都没拿到 wlanacip/wlanacname）—— 本次按旧行为（AC 参数留空）登录；"
         "若该网络持续「AC认证失败」，可在 config.json 里手动补 wlan_ac_ip / wlan_ac_name（值抄门户跳转 URL）✓",
         len(PORTAL_PROBE_URLS), level=logging.WARNING)
    return empty


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
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
        _log("在线检查网络异常: %s", exc, level=logging.DEBUG)
        return None
    # 审计 C-20：锚定到末尾 + 贪婪匹配 —— 非贪婪的 \{.*?\} 遇到响应里带 } / ) 的字段会被截断，
    # 不锚定则可能把页面里任意括号串当 JSONP。lenient 只放宽尾部分号/空白。
    m = re.search(r"\((\{.*\})\s*\)\s*;?\s*$", txt, re.S)
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


def _scrub_url(text):
    """兜底擦除：把文本里可能出现的整条 URL / 密码参数打掉（v2.1.0.0 / P7-2）。

    登录是 **GET**，`user_password` 就在 query 里 —— 任何把 URL 带出来的异常
    （`HTTPError` / `URLError` 的包装、底层 socket 错误）都会顺手把密码写进日志 ✗。
    """
    if not text:
        return text
    text = re.sub(r"https?://\S+", "<url>", str(text))
    text = re.sub(r"(?i)(user_password|password)=[^&\s]*", r"\1=***", text)
    return text


def _safe_error_text(exc, limit=200):
    """把网络异常变成**可以进日志 / 可以返回给界面**的文本（v2.1.0.0 / P7-2）。

    规则（信息从多到少）：
      1. `HTTPError.code` → `HTTP 500`（最有用的那一位）✓；
      2. 否则 `type(exc).__name__` + `exc.reason`（若有）✓；
      3. 最后整体过一遍 `_scrub_url()`：URL → `<url>`、任何 `password=...` → `***` ✓。
    这样即使底层异常真把整条 URL 塞进消息里，密码也进不了日志 ✓。
    """
    code = getattr(exc, "code", None)
    reason = getattr(exc, "reason", None)
    head = "HTTP {}".format(code) if code else type(exc).__name__
    detail = ""
    if reason is not None and reason is not exc:
        detail = ": {}".format(reason)
    elif str(exc) and str(exc) != "None":
        # 没有 .reason（普通 OSError / TimeoutError）时带上原文 —— 下面的 _scrub_url
        # 会把可能藏在里面的 URL 与 password= 参数擦掉，所以「有用」与「安全」可以兼得 ✓
        detail = ": {}".format(exc)
    text = _scrub_url("{}{}".format(head, detail))
    return text if len(text) <= limit else text[:limit] + "…"


def login(host, account, suffix, password, wlan_user_ip, wlan_user_mac,
          wlan_ac_ip="", wlan_ac_name=""):
    """Dr.COM JSONP 登录（GET /eportal/portal/login，端口 801）。

    请求：GET http://host:801/eportal/portal/login?callback=dr1234&...
    响应：dr1234({"result":1,"msg":"...","ret_code":0})

    返回 (success, msg)：
        success  True=成功（result == 1；或门户说「已经在线」）
        msg      服务端 msg 字段或本地诊断信息（用于日志）

    v2.1.3.11：新增 wlan_ac_ip / wlan_ac_name。**新 AC（Ruijie_Ac_cc73fa）留空会被判
    「AC认证失败」**（真机 2026-11-20 实测：同一账号密码，补上 AC 参数后门户立刻受理）。
    参数由调用方用 discover_portal_params() 在未认证时从门户 302 里取；取不到才留空（老行为）。
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
        "wlan_ac_ip":     wlan_ac_ip or "",
        "wlan_ac_name":   wlan_ac_name or "",
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
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
        # v2.1.0.0（P7-2）：**绝不把异常原文写进日志 / 返回给界面** ——
        # 这条 GET 的 URL 里带着 `user_password`，而某些异常（HTTPError / URLError 的包装、
        # 底层 socket 错误）会把整条 URL 原样带出来 ✗ → 密码就进了 logs\campus_login.log，
        # 进而进诊断包。现在只记「异常类型 / HTTP 状态码 + 安全化后的原因」，
        # 返回给调用方的也是同一口径 ✓（见 _safe_error_text）。
        safe = _safe_error_text(exc)
        _log("登录请求异常: %s", safe, level=logging.ERROR)
        return False, "请求失败: {}".format(safe)
    # 解析 JSONP：dr1234({...});
    # 审计 C-20：贪婪匹配（msg 里含 } / ) 时不会被截断）；仍锚定末尾，避免把中间片段当 JSONP。
    m = re.search(r"\((\{.*\})\s*\)\s*;?\s*$", txt, re.S)
    if not m:
        # 审计 C-10：响应体也可能是门户回显的参数（含 user_password）—— 同样过擦除
        return False, "login 接口返回非 JSONP: {}".format(_scrub_url(txt[:80]))
    try:
        data = json.loads(m.group(1))
    except (ValueError, json.JSONDecodeError):
        return False, "login JSON 解析失败: {}".format(_scrub_url(txt[:80]))
    success = data.get("result") == 1
    # 审计 C-10：服务端 msg 会原样进日志 / 界面，同样先擦 URL 与 password=（I6）
    msg = _scrub_url(data.get("msg", "") or "")
    # v2.1.3.11：门户对「这台机器/这个账号已经在线」回的是 result=0 + 「IP: … 已经在线！」——
    # 这不是失败 ✗。不认出来会一直重试、越退越久，界面还一直显示「未登录」。
    if not success and ("已经在线" in msg or "已在线" in msg):
        success = True
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
    text = _netsh_text(["wlan", "show", "interfaces"])
    if not text:
        return None
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


# ============================================================
# Wi-Fi 名清单（v2.1.1.0：配置页「点一下就填」）
#
# 为什么要它：守卫白名单 / 方案匹配名都得**手打** SSID ✗ ——
#   差一个字母就永远不命中（而且用户完全看不出来为什么守卫不生效 ✗）。
# 这里把「本机保存过的」和「附近能看到的」名字列出来，界面直接点选 ✓。
# 解析只看 `标签 : 名字` 的右半边 → 中英文系统的 netsh 输出都能认 ✓。
# ============================================================
MAX_SSID_LIST = 40          # 列表上限：某些环境能刷出几百条，会把界面撑爆 ✗


def _netsh_text(args):
    """跑一条 netsh 并返回文本（**中英文 Windows 的编码都兼容** ✓）；失败 → None ✓。"""
    try:
        proc = subprocess.run(
            ["netsh"] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = proc.stdout or b""
    for enc in ("utf-8", "gbk"):          # 中文 Windows 的 netsh 输出是 GBK
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_label_values(text):
    """把 `标签 : 名字` 的**右半边**收集成列表 ✓（语言无关 ✓，去重、限量 ✓）。"""
    out = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        name = line.split(":", 1)[1].strip()
        if not name or name.startswith("-"):
            continue
        if name not in out:
            out.append(name)
    return out[:MAX_SSID_LIST]


def known_ssids():
    """本机**保存过**的 Wi-Fi 名（`netsh wlan show profiles`）→ 列表 ✓。"""
    text = _netsh_text(["wlan", "show", "profiles"])
    return _parse_label_values(text) if text else []


def visible_ssids():
    """附近**当前可见**的 Wi-Fi 名（`netsh wlan show networks`）→ 列表 ✓。

    只认 `SSID n : 名字` 开头的行 —— `BSSID n :` 那些（MAC 地址）会被排除 ✓。
    """
    text = _netsh_text(["wlan", "show", "networks"])
    if not text:
        return []
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("SSID ") or ":" not in stripped:
            continue
        name = stripped.split(":", 1)[1].strip()
        if name and name not in out:
            out.append(name)
    return out[:MAX_SSID_LIST]


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
            # v2.0.9.0（B5）：先按当前 Wi-Fi 名看看要不要自动切方案 ——
            # **必须在守卫判定之前**，否则这次检查还按旧方案的白名单走 ✗。
            # 切完要重载 cfg（方案改的就是 host/interval/守卫这几个键）。
            if _auto_profile is not None and _ssid:
                try:
                    _switched = _auto_profile(_ssid)
                except Exception as exc:      # noqa: BLE001 —— 自动切换失败不该影响这次检查
                    _log("自动切换配置方案失败：%r", exc, level=logging.WARNING)
                    _switched = ""
                if _switched:
                    _log("已按当前 Wi-Fi（%s）自动切换到配置方案「%s」", _ssid, _switched)
                    cfg = _load_config()
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
            # 审计 C-09：is_online 有三种语义（True / False / None=网络异常或解析失败）。
            # 旧实现 `if is_online(host):` 把 None 与 False 混同 → ① UI 显示「未登录」而实际可能在线；
            # ② 拿一次抖动结果多打一次登录请求（Dr.COM 对已在线会话再登录可能踢掉原会话）；
            # ③ 失败后进 5 分钟退避，把一次网络抖动放大成"停摆"。现在 None 单独处理：
            #    只记「未知」、不登录、也不计退避 ✓。
            _online = is_online(host)
            if _online is True:
                _set_state(online=True, last_error=None)
                _reset_backoff()
                _log("已在线，无需登录")
                return
            if _online is None:
                _log("在线状态未知（网关响应异常），本次不登录、不计退避",
                     level=logging.WARNING)
                # 不清 last_login_at / online=False：不要把"查不到"说成"掉线" ✗
                _set_state(network_reachable=True, online=None,
                           last_error="在线状态未知（网关响应异常）")
                return

            _set_state(online=False)

            # 3. 登录
            # v2.0.14.0（P1-7）：账号没填就**别拿空用户名去认证** —— 记一条清楚的中文提示、
            # 不计退避（属于「还没配置好」，不是网络问题），与下面的「密码未设置」同等对待 ✓。
            if not str(account or "").strip():
                _log("账号未设置，跳过登录。请在 Web UI 的「配置 → 账户」里填写学号 / 工号。",
                     level=logging.WARNING)
                _set_state(last_error="账号未设置")
                return

            pwd = _get_password()
            if pwd is None:
                _log("密码未设置，跳过登录。请通过 Web UI 设置密码。", level=logging.WARNING)
                _set_state(last_error="密码未设置")
                # 不计入退避（用户操作问题，不是网络问题）
                return

            wlan_ip, wlan_mac = discover_network(host)
            # v2.1.3.11：未认证时先探门户 302 —— 新 AC（Ruijie_Ac_cc73fa）要求登录请求
            # 带 wlanacip / wlanacname，留空会被判「AC认证失败」（真机 2026-11-20 实测）。
            # 探不到就退回老行为（AC 留空），不会因此让本次登录失败 ✗。
            portal = discover_portal_params(host)
            if portal.get("user_ip"):
                wlan_ip = portal["user_ip"]
            if portal.get("user_mac"):
                wlan_mac = portal["user_mac"]
            # v2.1.3.12：探测不到时的**手动兜底** —— config.json 可选键（值抄门户跳转 URL）。
            # 学校有多个版本的 AC / 偶尔探测被拦时，能让用户不升级也能自救 ✓。
            manual_ac_ip = _safe_ip(str(cfg.get("wlan_ac_ip") or "").strip())
            manual_ac_name_raw = str(cfg.get("wlan_ac_name") or "").strip()
            manual_ac_name = (manual_ac_name_raw
                              if _AC_NAME_RE.fullmatch(manual_ac_name_raw or "") else "")
            ac_ip = portal.get("ac_ip") or manual_ac_ip
            ac_name = portal.get("ac_name") or manual_ac_name
            if not portal.get("ac_ip") and not portal.get("ac_name") and (ac_ip or ac_name):
                _log("使用 config.json 手动指定的 AC 参数：wlanacip=%s wlanacname=%s",
                     ac_ip or "（空）", ac_name or "（空）")
            ok, msg = login(host, account, suffix, pwd, wlan_ip, wlan_mac,
                            wlan_ac_ip=ac_ip, wlan_ac_name=ac_name)
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
            # 审计 C-17：异常原文可能带登录 URL（含密码）—— 兜底路径也必须安全化（I6）
            safe = _safe_error_text(exc)
            _log("run_once 未捕获异常: %s", safe, level=logging.ERROR)
            _set_state(last_error="内部异常: {}".format(safe))
            _set_backoff()
        finally:
            with _STATE_LOCK:
                _STATE["login_in_progress"] = False
                _STATE["last_check_at"] = _now_iso()
            # 更新 next_check_at：**只在退避生效时写**（v2.1.0.0 / P7-4）。
            # 双写竞争：`run_periodic` 在睡前会按 interval 写一次，这里旧实现又无条件写一次
            # （按 interval 或退避）→ 界面上的倒计时会在两个值之间跳 ✗。
            # 现在分工明确：**平时由调度者（run_periodic）写**；这里只在本次检查触发了退避
            # 时改写（那种情况下调度者事先不可能知道，只有 run_once 知道）✓。
            bu = _backoff_until()
            if bu:
                with _STATE_LOCK:
                    _STATE["next_check_at"] = bu
    finally:
        # v2.0.4.5：RUN_LOCK 现在是手工非阻塞 acquire 的，必须自己释放
        # （原来靠 `with _RUN_LOCK:` 自动释放；用 try/finally 保证任何 return / 异常都释放）
        _RUN_LOCK.release()