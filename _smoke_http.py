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


def _get_json2(path):
    """GET 一个 JSON 接口，同时返回 HTTP 状态码（断言「真收到 200」而不只是 body 长得对）。"""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    conn.request("GET", path, headers={"Host": HOST})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", errors="replace")
    conn.close()
    try:
        return resp.status, json.loads(body)
    except ValueError:
        return resp.status, {"_unparsable": body[:120]}


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


def _post_raw(path, body, ctype="application/zip"):
    """POST 原始字节（/api/config/import 是二进制上传，不走 JSON 解析）。"""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    conn.request("POST", path, body=body, headers={
        "Host": HOST, "Content-Type": ctype,
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

    # ============================================================
    # v2.1.3.0：/api/credentials —— 账号 + 运营商 + 密码一次存（current / profile 两种范围）
    # 这一节的钉子（每一条不这么写都会变成真 bug）：
    #   · current 必须是**部分更新**：只动 account / suffix，别的键一个都不许被重置 ✗
    #   · profile 必须是**合并**：只动这两个键，方案的网关 / 间隔 / 守卫不许被抹 ✗
    #   · 密码只进密码文件（这里打桩记录），方案 values 里永远不出现密码字段 ✗
    #   · 给「别的方案」存密码不许碰当前生效的密码 ✗
    #   · 方案不存在 → 400（否则会写出一堆 password.<乱名字>.txt 野文件 ✗）
    # ============================================================
    _pwd_calls = []
    _reloads = []
    _own = {"宿舍": True}
    web_api._save_password_to_disk = lambda pwd, profile=None: _pwd_calls.append((pwd, profile))
    web_api._profile_has_own_password = lambda name: bool(_own.get(name))
    web_api._remove_profile_password = lambda name: bool(_own.pop(name, False))
    web_api._load_password_from_disk = lambda: _reloads.append(1)
    # 这几个常量平时由 联网_service.py 的 _attach() 注入；冒烟里只挂了 web_api → 手动补上 ✓
    web_api.ALLOWED_SUFFIXES = ("", "@yd", "@dx", "@lt")
    _pf_mod._attach(load_config=_pload, save_config=_psave, validate_config=lambda c: [],
                    load_password_from_disk=lambda: _reloads.append(1))
    _pstore.update({"host": "172.16.80.3", "port": 80, "auto_check_interval_min": 30,
                    "account": "2023000001", "suffix": "@yd", "ui_port": 8848,
                    "network_guard_enabled": True, "guard_allowed_ssids": "Dorm-WiFi",
                    "guard_allowed_subnets": "",
                    "profiles": {"宿舍": {"values": {"account": "2023000001", "suffix": "@yd",
                                                     "host": "172.16.80.3",
                                                     "auto_check_interval_min": 30}}},
                    "active_profile": "宿舍", "profiles_auto_switch": False})

    _st, _b = _post_json("/api/credentials", {"scope": "current", "account": "2023000002",
                                              "suffix": ""})
    _check("v2.1.3.0 current：账号 / 后缀存下了、密码没动",
           (_st, _b.get("ok"), _pstore.get("account"), _pstore.get("suffix"), _b.get("password")),
           (200, True, "2023000002", "", "unchanged"))
    _check("v2.1.3.0 current 是部分更新：网关 / 端口 / 间隔 / 守卫 / UI 端口都没被重置",
           (_pstore.get("host"), _pstore.get("port"), _pstore.get("auto_check_interval_min"),
            _pstore.get("network_guard_enabled"), _pstore.get("guard_allowed_ssids"),
            _pstore.get("ui_port")),
           ("172.16.80.3", 80, 30, True, "Dorm-WiFi", 8848))
    _check("v2.1.3.0 current 不带密码 → 一次密码文件都没写", len(_pwd_calls), 0)
    _st, _b = _post_json("/api/credentials", {"scope": "current", "account": "2023000003",
                                              "suffix": "@dx", "password": "#Demo-0001#"})
    _check("v2.1.3.0 current + 密码 → 写「当前生效文件」（profile=None）",
           (_st, _b.get("password"), _pwd_calls[-1]), (200, "set", ("#Demo-0001#", None)))
    _check("v2.1.3.0 账号非数字 → 400",
           _post_json("/api/credentials", {"account": "abc"})[0], 400)
    _check("v2.1.3.0 后缀不在白名单 → 400",
           _post_json("/api/credentials", {"suffix": "@xx"})[0], 400)
    _check("v2.1.3.0 scope 不合法 → 400",
           _post_json("/api/credentials", {"scope": "profiles"})[0], 400)
    _check("v2.1.3.0 scope=profile 缺方案名 → 400",
           _post_json("/api/credentials", {"scope": "profile"})[0], 400)
    _check("v2.1.3.0 方案不存在 → 400（不写野密码文件）",
           (_post_json("/api/credentials", {"scope": "profile", "name": "没有这个",
                                            "password": "#Demo-0009#"})[0], len(_pwd_calls)),
           (400, 1))
    _st, _b = _post_json("/api/credentials", {"scope": "profile", "name": "宿舍",
                                              "account": "2023999999", "suffix": "@lt",
                                              "password": "#Demo-0002#"})
    _vals = ((_pstore.get("profiles") or {}).get("宿舍") or {}).get("values") or {}
    _check("v2.1.3.0 profile：账号 / 后缀进方案 + 密码写方案专属文件",
           (_st, _b.get("ok"), _b.get("password"), _pwd_calls[-1]),
           (200, True, "set", ("#Demo-0002#", "宿舍")))
    _check("v2.1.3.0 profile 是**合并**：方案的网关 / 间隔还在（没被账号编辑抹掉）",
           (_vals.get("host"), _vals.get("auto_check_interval_min")), ("172.16.80.3", 30))
    _check("v2.1.3.0 方案 values 里没有任何密码字段（密码永不进配置）",
           not any(("pass" in k.lower() or "pwd" in k.lower()) for k in _vals), True)
    _check("v2.1.3.0 改的是当前方案 → 顺手重新应用（顶层账号跟着走）",
           _pstore.get("account"), "2023999999")
    _check("v2.1.3.0 重新应用顺带重载了密码文件", bool(_reloads), True)

    _post_json("/api/profiles/save", {"name": "教学楼", "match_ssids": "Teach-WiFi"})
    _reloads.clear()
    _st, _b = _post_json("/api/credentials", {"scope": "profile", "name": "教学楼",
                                              "password": "#Demo-0003#"})
    _check("v2.1.3.0 给**别的**方案存密码：写它的专属文件、也不重载当前密码",
           (_st, _b.get("password"), _pwd_calls[-1], len(_reloads)),
           (200, "set", ("#Demo-0003#", "教学楼"), 0))
    _g2 = _get_json("/api/profiles")
    _item = {i["name"]: i for i in (_g2.get("items") or [])}
    _check("v2.1.3.0 GET /api/profiles 带上 has_own_password（界面要显示「专属密码」）",
           (_item.get("宿舍", {}).get("has_own_password"),
            _item.get("教学楼", {}).get("has_own_password")), (True, False))
    _own["教学楼"] = True
    _st, _b = _post_json("/api/credentials", {"scope": "profile", "name": "教学楼",
                                              "clear_password": True})
    _check("v2.1.3.0 删除方案的专属密码 → removed + 重载（回落公共密码）",
           (_st, _b.get("password"), len(_reloads)), (200, "removed", 1))
    _check("v2.1.3.0 本来就没有专属密码 → 删除请求 400",
           _post_json("/api/credentials", {"scope": "profile", "name": "教学楼",
                                           "clear_password": True})[0], 400)

    # ============================================================
    # 维护第四轮（审计 C-26）：删方案要一并删掉它的专属密码文件 ——
    # 否则「删掉 → 再建同名方案」会静默继承旧密码（用户看不到这条凭据还留着）。
    # ============================================================
    _pf_mod.save_profile("临时", {"host": "10.1.2.3", "auto_check_interval_min": 30})
    _own["临时"] = True
    _reloads.clear()
    _st, _b = _post_json("/api/profiles/delete", {"name": "临时"})
    _check("维护 R4-C26：删除方案会一并删专属密码文件（同名重建不继承旧密码）",
           (_st, (_b or {}).get("ok"), (_b or {}).get("password_removed"),
            "临时" in _own, len(_reloads)),
           (200, True, True, False, 1))
    _own["无密"] = False
    _pf_mod.save_profile("无密", {"host": "10.1.2.3"})
    _st, _b = _post_json("/api/profiles/delete", {"name": "无密"})
    _check("维护 R4-C26：没有专属密码的方案 → 删除成功且 password_removed=False",
           (_st, (_b or {}).get("ok"), (_b or {}).get("password_removed")), (200, True, False))

    # ============================================================
    # 维护第二轮（审计 H1）：/api/update/* 的契约以前零覆盖 —— 状态码 / 字段漂移
    # 只有用户在界面上手点才发现。这里用替身把 HTTP 层的契约钉住。
    # ============================================================
    class _FakeUpdater(object):
        busy = "下载中 42%"
        resets = []

        def _schedule_success_clear(self):
            pass

        def reset_update_attempt(self, reason=""):
            # 维护第三轮（审计 F2）：/api/update/install 的 reset_attempt 字段接线
            self.resets.append(reason)
            return True

        def update_busy_message(self):
            return self.busy

        def _log_upgrade(self, level, msg):
            pass

        def api_get_rollback(self):
            return 200, {"ok": True, "backups": []}

        def api_post_rollback(self, payload):
            return 400, {"ok": False, "error": "版本号不合法"}

    web_api._auto_update_mod = _FakeUpdater()
    web_api._snapshot_state = lambda: {
        "update_state": "idle", "update_progress": 0, "update_progress_message": "",
        "update_available": False, "update_latest_version": None, "update_last_error": None,
    }
    _u = _get_json("/api/update/status")
    _check("维护 R2-H1：GET /api/update/status 契约字段齐全（local_version / state / progress_pct）",
           isinstance(_u, dict) and "local_version" in _u and "state" in _u
           and "progress_pct" in _u and _u.get("local_version") == web_api.VERSION,
           True, str(sorted((_u or {}).keys()))[:120])
    _st_busy, _b_busy = _post_json("/api/update/install", {})
    _check("维护 R2-H1：忙时 POST /api/update/install → 409 + 可读原因（不再假成功）",
           (_st_busy, "任务进行中" in (_b_busy or {}).get("error", "")), (409, True))
    _st_rb, _b_rb = _post_json("/api/rollback", {"version": "../evil"})
    _check("维护 R2-H1：POST /api/rollback 坏版本号 → 400（不把路径拼进备份目录）",
           (_st_rb, (_b_rb or {}).get("ok")), (400, False))
    # 维护第三轮（审计 F2）：reset_attempt 字段只增不改既有契约，且不影响忙时 409
    _resets_before = len(_FakeUpdater.resets)
    _st_reset, _b_reset = _post_json("/api/update/install", {"reset_attempt": True})
    _check("维护 R3-F2：POST /api/update/install 带 reset_attempt → 先重置熔断（忙时仍 409）",
           (_st_reset, len(_FakeUpdater.resets) - _resets_before,
            "任务进行中" in (_b_reset or {}).get("error", "")),
           (409, 1, True))
    # ============================================================
    # v2.1.3.8（审计 P3-2）：日志读取上限 + 轮转后 offset 复位。
    # 旧实现两个坑：① 从 offset 一次 read() 到 EOF（20MB 日志整份进内存）；
    # ② offset 超过文件大小（轮转）时 next_offset 仍指向旧文件末尾 → 新文件开头被跳过。
    # 这里真起服务、真造 20MB 日志，断言响应有界且轮转后下一轮能补出新内容。
    # ============================================================
    _p32_dir = tempfile.mkdtemp(prefix="drcom_p32_")
    _p32_log = os.path.join(_p32_dir, "campus_login.log")
    _p32_line = "[2026-11-19 18:00:00] [INFO] 用于验证读取上限的合成日志行 0123456789abcdef\n"
    _p32_chunk = _p32_line * 500
    with open(_p32_log, "w", encoding="utf-8") as _p32_f:
        _p32_size = 0
        while _p32_size < 20 * 1024 * 1024:
            _p32_f.write(_p32_chunk)
            _p32_size += len(_p32_chunk.encode("utf-8"))
    _p32_had = hasattr(web_api, "LOG_FILE")
    _p32_old = getattr(web_api, "LOG_FILE", None)
    web_api.LOG_FILE = _p32_log
    try:
        _p32_t0 = _time.time()
        _st2a, _b2a = _get_json2("/api/log_tail?offset=0&max=500")
        _p32_elapsed = _time.time() - _p32_t0
        _b2a = _b2a or {}
        _p32_next = _b2a.get("next_offset")
        _check("维护 P3-2：20MB 日志单次响应有界（读到 4MB 上限即停，不整份读进内存）",
               (_st2a, isinstance(_p32_next, int),
                0 < (_p32_next or 0) <= web_api.LOG_TAIL_MAX_BYTES,
                _p32_next < _b2a.get("total_size", 0),
                len(_b2a.get("lines") or []) <= 500, bool(_b2a.get("lines")),
                _p32_elapsed < 10.0),
               (200, True, True, True, True, True, True),
               repr((_st2a, _p32_next, _b2a.get("total_size"),
                     len(_b2a.get("lines") or []), round(_p32_elapsed, 3))))
        with open(_p32_log, "w", encoding="utf-8") as _p32_f:
            _p32_f.write("轮转后的新文件第一行\n" * 10)
        _st2b, _b2b = _get_json2("/api/log_tail?offset=%d&max=500" % (_p32_next or 0))
        _b2b = _b2b or {}
        _check("维护 P3-2：日志轮转后旧 offset 失效 → next_offset 复位为 0（不跳过新文件开头）",
               (_st2b, _b2b.get("next_offset"), _b2b.get("lines")), (200, 0, []),
               repr((_st2b, _b2b.get("next_offset"), _b2b.get("lines"))))
        _st2c, _b2c = _get_json2("/api/log_tail?offset=0&max=500")
        _b2c = _b2c or {}
        _p32_new_size = os.path.getsize(_p32_log)
        _check("维护 P3-2：复位后下一轮轮询补出新文件开头内容（不用手动刷新）",
               (_st2c, "轮转后的新文件第一行" in "\n".join(_b2c.get("lines") or []),
                _b2c.get("next_offset") == _p32_new_size),
               (200, True, True),
               repr((_st2c, _b2c.get("next_offset"), _p32_new_size)))
    finally:
        if _p32_had:
            web_api.LOG_FILE = _p32_old
        else:
            del web_api.LOG_FILE
        try:
            os.remove(_p32_log)
            os.rmdir(_p32_dir)
        except OSError:
            pass

    # ============================================================
    # v2.1.3.8（审计 P3-8）：状态接口不再直接 int() 信配置 / 状态里的值。
    # 手改过或从旧版沿用的 config.json 里数值字段可能是字符串，旧实现一个 "abc"
    # 就让 GET /api/update/status 抛 ValueError → 500（界面升级块整块空白）✗。
    # ============================================================
    _p38_saved_snap = web_api._snapshot_state
    _p38_saved_cfg = dict(_pstore)
    web_api._snapshot_state = lambda: {
        "update_state": "downloading", "update_progress": "abc",
        "update_progress_message": "下载中", "update_available": False,
        "update_latest_version": None, "update_last_error": None,
    }
    _pstore.update({"update_check_interval_hours": "abc",
                    "update_min_free_disk_mb": "not-a-number"})
    _st38, _b38 = _get_json2("/api/update/status")
    _b38 = _b38 or {}
    _check("维护 P3-8：字符串型脏配置 / 脏状态不再 500（进度 / 间隔 / 磁盘余量回落默认）",
           (_st38, _b38.get("progress_pct"), _b38.get("check_interval_hours"),
            _b38.get("min_free_disk_mb")), (200, 0, 6, 200), repr((_st38, _b38)))
    web_api._snapshot_state = lambda: {
        "update_state": "downloading", "update_progress": "42",
        "update_progress_message": "下载中", "update_available": False,
        "update_latest_version": None, "update_last_error": None,
    }
    _pstore.update({"update_check_interval_hours": "12", "update_min_free_disk_mb": "512"})
    _st38b, _b38b = _get_json2("/api/update/status")
    _b38b = _b38b or {}
    _check("维护 P3-8：数字字符串照常转换（容错只兜脏值，不把合法值当成脏值丢掉）",
           (_st38b, _b38b.get("progress_pct"), _b38b.get("check_interval_hours"),
            _b38b.get("min_free_disk_mb")), (200, 42, 12, 512), repr((_st38b, _b38b)))
    web_api._snapshot_state = _p38_saved_snap
    _pstore.clear()
    _pstore.update(_p38_saved_cfg)

    # ============================================================
    # v2.1.3.8（审计 P3-15）：配置导入的坏请求文案必须中文、可读、不回声内部异常。
    # 第一轮（P1-2 / P1-3）已把「加密 / 损坏 zip」与「请求体过大」改成中文；
    # 这里钉住此前漏掉的空 body 分支，并给「不是 zip」补一条行为级回归。
    # ============================================================
    def _has_cjk(s):
        return any(ord(ch) > 0x2E80 for ch in (s or ""))

    _st_empty, _b_empty = _post_raw("/api/config/import", b"")
    _empty_err = (_b_empty or {}).get("error", "")
    _check("维护 P3-15：空 body 导入 → 400 + 中文「请求体为空」（不再回英文 empty body）",
           (_st_empty, "请求体为空" in _empty_err, "empty body" not in _empty_err,
            "internal" not in _empty_err),
           (400, True, True, True), repr((_st_empty, _empty_err)))
    _st_bad, _b_bad = _post_raw("/api/config/import", b"this is not a zip")
    _bad_err = (_b_bad or {}).get("error", "")
    _check("维护 P3-15：不是 zip → 400 + 中文可读原因（不回显 Python 异常原文）",
           (_st_bad, _has_cjk(_bad_err), "internal" not in _bad_err, "zip" in _bad_err),
           (400, True, True, True), repr((_st_bad, _bad_err)))
    _st_big, _b_big = _post_raw("/api/config/import",
                                b"\0" * (web_api.CONFIG_IMPORT_MAX_BYTES + 1))
    _big_err = (_b_big or {}).get("error", "")
    _check("维护 P3-15：导入请求体过大 → 413 + 中文「请求体过大」（可读，不含内部异常）",
           (_st_big, "请求体过大" in _big_err, "internal" not in _big_err),
           (413, True, True), repr((_st_big, _big_err)))
finally:
    server.shutdown()

fails = [r for r in results if not r[0]]
for ok, name, got, want, note in results:
    print("%s  %-34s got=%s want=%s %s" % ("PASS" if ok else "FAIL", name, got, want, note))
print("\n结果：%d/%d 通过" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
