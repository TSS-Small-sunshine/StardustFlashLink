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

PORT = 18748
results = []


def _req(method, path, headers):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


def _check(name, got, want, note=""):
    ok = (got in want) if isinstance(want, (tuple, list)) else (got == want)
    results.append((ok, name, got, want, note))


server = ThreadingHTTPServer(("127.0.0.1", PORT), web_api._Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    # 1) 非法 Host（DNS rebinding 场景）→ 400
    _check("非法 Host 被拒", _req("GET", "/api/health", {"Host": "evil.com"}), 400)
    # 2) 非法 Host 上的写请求同样被挡
    _check("非法 Host + POST 被拒", _req("POST", "/api/login", {"Host": "evil.com"}), 400)
    # 3) 正常 Host + GET → 200
    _check("合法 Host GET 放行", _req("GET", "/api/health", {"Host": "127.0.0.1:%d" % PORT}), 200)
    # 4) 写请求缺自定义头（跨站简单请求无法带该头）→ 403
    _check("缺 X-Requested-With 的 POST 被拒",
           _req("POST", "/api/login", {"Host": "127.0.0.1:%d" % PORT,
                                       "Content-Type": "text/plain", "Content-Length": "0"}), 403)
    # 5) 跨源 Origin → 403
    _check("跨源 Origin 的 POST 被拒",
           _req("POST", "/api/login", {"Host": "127.0.0.1:%d" % PORT,
                                       "X-Requested-With": "DrcomUI",
                                       "Origin": "http://evil.com",
                                       "Content-Type": "application/json", "Content-Length": "2"}), 403)
finally:
    server.shutdown()

fails = [r for r in results if not r[0]]
for ok, name, got, want, note in results:
    print("%s  %-34s got=%s want=%s %s" % ("PASS" if ok else "FAIL", name, got, want, note))
print("\n结果：%d/%d 通过" % (len(results) - len(fails), len(results)))
sys.exit(1 if fails else 0)
