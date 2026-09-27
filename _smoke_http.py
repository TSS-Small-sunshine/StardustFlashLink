# -*- coding: utf-8 -*-
"""P1-1 冒烟测试：Host 白名单 / 写接口自定义头 / Origin 同源。

只测「请求拦截层」，不碰业务 API（业务 API 需要 _attach 注入）。
跑法：python _smoke_http.py（在 DrcomAutoLogin-Windows 目录下）
"""
import http.client
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


def _check(name, got, want, note=""):
    """want 为元组/列表时：got 也是元组则整体比较，否则按「属于其中之一」处理。"""
    if isinstance(want, (tuple, list)) and isinstance(got, (tuple, list)):
        ok = tuple(got) == tuple(want)
    elif isinstance(want, (tuple, list)):
        ok = got in want
    else:
        ok = got == want
    results.append((ok, name, got, want, note))


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
finally:
    server.shutdown()

fails = [r for r in results if not r[0]]
for ok, name, got, want, note in results:
    print("%s  %-34s got=%s want=%s %s" % ("PASS" if ok else "FAIL", name, got, want, note))
print("\n结果：%d/%d 通过" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
