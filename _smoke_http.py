# -*- coding: utf-8 -*-
"""P1-1 冒烟测试：Host 白名单 / 写接口自定义头 / Origin 同源。

只测「请求拦截层」，不碰业务 API（业务 API 需要 _attach 注入）。
跑法：python _smoke_http.py（在 DrcomAutoLogin-Windows 目录下）
"""
import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

import web_api

# 控制台编码兜底（CI 的 cp1252 会让中文断言名把脚本打印崩掉；只改错误处理，不改编码）
try:
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass

PORT = 18748
HOST = "127.0.0.1:%d" % PORT
results = []


def _req(method, path, headers):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    resp.read()
    ctype = resp.getheader("Content-Type") or ""
    conn.close()
    return resp.status, ctype


def _get_json(path):
    """GET 一个 JSON 接口并解析（v2.0.8.0 的 /api/metrics 要断言字段，不只断言状态码）。"""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    conn.request("GET", path, headers={"Host": HOST})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", errors="replace")
    conn.close()
    try:
        return json.loads(body)
    except ValueError:
        return {"_unparsable": body[:120]}


def _check(name, got, want, note=""):
    """want 为元组/列表时：got 也是元组则整体比较，否则按「属于其中之一」处理。"""
    if isinstance(want, (tuple, list)) and isinstance(got, (tuple, list)):
        ok = tuple(got) == tuple(want)
    elif isinstance(want, (tuple, list)):
        ok = got in want
    else:
        ok = got == want
    results.append((ok, name, got, want, note))


def _post_json(path, payload):
    """POST 一个 JSON 接口并解析（写接口要带 X-Requested-With，否则会被 403）。"""
    body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    conn.request("POST", path, body=body, headers={
        "Host": HOST, "Content-Type": "application/json",
        "X-Requested-With": "DrcomUI", "Content-Length": str(len(body))})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", errors="replace")
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except ValueError:
        return resp.status, {"_unparsable": raw[:120]}


server = ThreadingHTTPServer(("127.0.0.1", PORT), web_api._Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    # 1) 非法 Host（DNS rebinding 场景）→ 400
    _check("非法 Host 被拒", _req("GET", "/api/health", {"Host": "evil.com"})[0], 400)
    # 2) 非法 Host 上的写请求同样被挡
    _check("非法 Host + POST 被拒", _req("POST", "/api/login", {"Host": "evil.com"})[0], 400)
    # 3) 正常 Host + GET → 200
    _check("合法 Host GET 放行", _req("GET", "/api/health", {"Host": HOST})[0], 200)
    # 4) 写请求缺自定义头（跨站简单请求无法带该头）→ 403
    _check("缺 X-Requested-With 的 POST 被拒",
           _req("POST", "/api/login", {"Host": HOST,
                                       "Content-Type": "text/plain", "Content-Length": "0"})[0], 403)
    # 5) 跨源 Origin → 403
    _check("跨源 Origin 的 POST 被拒",
           _req("POST", "/api/login", {"Host": HOST,
                                       "X-Requested-With": "DrcomUI",
                                       "Origin": "http://evil.com",
                                       "Content-Type": "application/json", "Content-Length": "2"})[0], 403)
    # 6) v2.0.3.0：品牌图片静态路由（修 logo / favicon 404）
    st, ctype = _req("GET", "/branding/web-logo-32.png", {"Host": HOST})
    _check("GET /branding/web-logo-32.png", (st, ctype.startswith("image/png")), (200, True))
    # 7) 不存在的图片 → 404（而不是 500 / 目录列表）
    _check("不存在的品牌图片 404", _req("GET", "/branding/nope.png", {"Host": HOST})[0], 404)
    # 8) 路径穿越 / 非图片扩展名 → 404
    _check("路径穿越被拒", _req("GET", "/branding/..%2fweb_api.py", {"Host": HOST})[0], 404)
    _check("非图片扩展名被拒", _req("GET", "/branding/web_api.py", {"Host": HOST})[0], 404)

    # ============================================================
    # v2.0.8.0（B4）：/api/metrics —— 连接质量面板的端到端断言
    # 合成一份「7 天窗口内」的最小日志（三种走向各一次），断言**算出来的数字**，
    # 而不是「接口没报错」。全程离线：延迟量的是本机测试端口。
    # ============================================================
    import metrics as _metrics
    import tempfile
    import time as _time

    _mdir = tempfile.mkdtemp(prefix="drcom_metrics_")
    _now = _time.time()

    def _ts(offset_sec):
        return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(_now - offset_sec))

    _log_lines = [
        "[%s] [INFO] 开始检查 (reason=periodic)" % _ts(300),
        "[%s] [INFO] 网络已可达（第 1 次尝试，耗时 0.25s）" % _ts(300),
        "[%s] [INFO] 已在线，无需登录" % _ts(299),
        "[%s] [INFO] 开始检查 (reason=periodic)" % _ts(200),
        "[%s] [INFO] 网络已可达（第 1 次尝试，耗时 0.25s）" % _ts(200),
        "[%s] [INFO] 登录成功: Portal协议认证成功！" % _ts(198),
        "[%s] [INFO] 开始检查 (reason=periodic)" % _ts(100),
        "[%s] [ERROR] 登录失败: 账号不存在或账号未绑定宽带" % _ts(100),
        "[%s] [WARNING] 登录失败，进入退避：连续 1 次，下次重试 5 分钟后（…）" % _ts(100),
        "这一行没有时间戳前缀，必须被忽略",
    ]
    _log_path = os.path.join(_mdir, "campus_login.log")
    with open(_log_path, "w", encoding="utf-8") as _f:
        _f.write("\n".join(_log_lines) + "\n")
    _metrics.LOG_DIR = _mdir
    _metrics._load_config = lambda: {"host": "127.0.0.1", "port": PORT}   # 本机端口 → 延迟必成功
    web_api._metrics_mod = _metrics

    _m = _get_json("/api/metrics?days=7")
    _check("v2.0.8.0 /api/metrics 三种走向各计一次",
           (_m.get("checks"), _m.get("online"), _m.get("relogin"), _m.get("fail"),
            _m.get("unknown")), (3, 1, 1, 1, 0),
           "checks=%s online=%s relogin=%s fail=%s unknown=%s"
           % (_m.get("checks"), _m.get("online"), _m.get("relogin"), _m.get("fail"),
              _m.get("unknown")))
    _check("v2.0.8.0 /api/metrics 在线率 66.7%", _m.get("uptime_pct"), 66.7)
    _check("v2.0.8.0 /api/metrics 平均恢复耗时 2000ms", _m.get("avg_recover_ms"), 2000)
    _check("v2.0.8.0 /api/metrics 可达耗时 250ms（本版起记录的新字段）",
           _m.get("avg_reach_ms"), 250)
    _check("v2.0.8.0 /api/metrics 当前延迟可测（本机端口，应 >= 0）",
           isinstance(_m.get("latency_ms"), int) and _m.get("latency_ms") >= 0, True,
           str(_m.get("latency_ms")))
    _check("v2.0.8.0 /api/metrics 7 天序列 + 当天计数",
           len(_m.get("series") or []) == 7
           and (_m.get("series") or [{}])[-1].get("relogin") == 1, True,
           str((_m.get("series") or [{}])[-1]))
    _check("v2.0.8.0 /api/metrics 坏参数回退 7 天",
           (_get_json("/api/metrics?days=abc") or {}).get("days"), 7)
    _metrics.LOG_DIR = os.path.join(_mdir, "不存在的目录")
    _check("v2.0.8.0 /api/metrics 日志缺失 → 0 周期且不 500",
           (_get_json("/api/metrics?days=7") or {}).get("checks"), 0)
    web_api._metrics_mod = None
    _check("v2.0.8.0 /api/metrics 模块未注入 → 优雅降级（不 500）",
           (_get_json("/api/metrics?days=7") or {}).get("ok") is False, True)

    # ============================================================
    # v2.0.9.0（B5）：/api/profiles —— 配置方案端到端（临时 config，真跑增删改切）
    # ============================================================
    import profiles as _pf_mod

    _pstore = {}

    def _pload():
        return json.loads(json.dumps(_pstore))      # 深拷贝：被测代码改了也看得见

    def _psave(cfg):
        _pstore.clear()
        _pstore.update(json.loads(json.dumps(cfg)))

    _pstore.update({"host": "1.2.3.4", "port": 80, "auto_check_interval_min": 30,
                    "account": "2023999999", "network_guard_enabled": False,
                    "guard_allowed_ssids": "", "guard_allowed_subnets": "",
                    "profiles": {}, "active_profile": "", "profiles_auto_switch": False})
    web_api._load_config = _pload
    web_api._save_config = _psave
    web_api._validate_config = lambda cfg: []       # 形状校验在 profiles 里已单独测过
    web_api._profiles_mod = _pf_mod
    _pf_mod._attach(load_config=_pload, save_config=_psave, validate_config=lambda c: [])

    _st, _b = _post_json("/api/profiles/save", {"name": "家里", "match_ssids": "Home-WiFi"})
    _check("v2.0.9.0 POST /api/profiles/save（用当前配置存方案）",
           (_st, _b.get("ok"), _b.get("name")), (200, True, "家里"))
    _check("v2.0.9.0 坏方案名 → 400",
           _post_json("/api/profiles/save", {"name": "a/b"})[0], 400)
    _g = _get_json("/api/profiles")
    _check("v2.0.9.0 GET /api/profiles 列出方案",
           (_g.get("ok"), _g.get("count"), (_g.get("items") or [{}])[0].get("name")), (True, 1, "家里"))
    _st, _b = _post_json("/api/profiles/activate", {"name": "家里"})
    _check("v2.0.9.0 POST /api/profiles/activate（写盘且账号不动）",
           (_st, _b.get("ok"), _pstore.get("active_profile"), _pstore.get("account")),
           (200, True, "家里", "2023999999"))
    _check("v2.0.9.0 激活后会重新读回（GET 里 active 标记）",
           (_get_json("/api/profiles") or {}).get("active"), "家里")
    _check("v2.0.9.0 POST /api/profiles/auto 打开自动切换",
           (_post_json("/api/profiles/auto", {"enabled": True})[0], _pstore.get("profiles_auto_switch")),
           (200, True))
    check_auto = _pf_mod.auto_switch("home-wifi")     # 已激活「家里」→ 不该重复切
    _check("v2.0.9.0 已是当前方案 → 自动切换不动手", check_auto, "")
    _st, _b = _post_json("/api/profiles/delete", {"name": "家里"})
    _check("v2.0.9.0 POST /api/profiles/delete（清标记、配置值保留）",
           (_st, _b.get("ok"), _pstore.get("profiles"), _pstore.get("active_profile"),
            _pstore.get("account")), (200, True, {}, "", "2023999999"))
    _check("v2.0.9.0 删不存在的方案 → 400",
           _post_json("/api/profiles/delete", {"name": "没有这个"})[0], 400)
finally:
    server.shutdown()

fails = [r for r in results if not r[0]]
for ok, name, got, want, note in results:
    print("%s  %-34s got=%s want=%s %s" % ("PASS" if ok else "FAIL", name, got, want, note))
print("\n结果：%d/%d 通过" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
