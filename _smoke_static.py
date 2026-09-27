# -*- coding: utf-8 -*-
"""静态/单元冒烟测试（v2.0.2.4 修复项）。

跑法：python _smoke_static.py（在 DrcomAutoLogin-Windows 目录下）
"""
import pathlib
import sys
import tempfile
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
FAILS = []
TOTAL = [0]


def check(name, cond, extra=""):
    TOTAL[0] += 1
    print("%s  %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        FAILS.append(name)


src_web = pathlib.Path("web_api.py").read_text(encoding="utf-8")
src_upd = pathlib.Path("auto_update.py").read_text(encoding="utf-8")

# ---- P0-1：6 个裸名不再出现 ----
for bare in ("time.sleep(0.3)",):
    check("P0-1 import time 可用", bare in src_web)
for name in ("_do_check_now", "_do_update_now", "_set_update_state", "_log_upgrade", "UPGRADE_HISTORY_MAX_LINES"):
    check("P0-1 %s 已加 _auto_update_mod. 前缀" % name,
          ("_auto_update_mod.%s" % name) in src_web)

# ---- P0-2：fail-closed ----
check("P0-2 digest 缺失即拒绝安装", "if not digest:" in src_upd and "拒绝安装" in src_upd)
check("P0-2 旧的 fail-open 分支已消失", "elif digest:" not in src_upd)

# ---- P1-2：API 元数据只信主源 ----
api_block = src_upd.split("GITHUB_API_MIRRORS = (")[1].split(")")[0]
check("P1-2 API 镜像已禁用（只剩主源 None）", api_block.strip() == "None,                    # 主源 api.github.com（唯一可信的元数据源）".strip() or "gh-proxy" not in api_block)
check("P1-2 下载镜像仍保留", "gh-proxy.com" in src_upd.split("GITHUB_DOWNLOAD_MIRRORS")[1])

# ---- P1-3：默认关闭 + 版本白名单 ----
src_svc = pathlib.Path("联网_service.py").read_text(encoding="utf-8")
check("P1-3 静默升级默认关闭", '"auto_update_enabled": False' in src_svc)
check("P1-3 远端版本串白名单", "[0-9A-Za-z._-]{1,32}" in src_upd)

# ---- P0-3：password.txt 注释行 ----
import importlib
svc = importlib.import_module("联网_service")
tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "password.txt")
svc.PASSWORD_FILE = p
pathlib.Path(p).write_text("# 在此行写入你的校园网账号密码\nrealpass\n", encoding="utf-8")
check("P0-3 跳过注释行取到真密码", svc._load_password_from_disk() == "realpass")
pathlib.Path(p).write_text("# 只有注释\n\n", encoding="utf-8")
check("P0-3 纯注释文件 -> None", svc._load_password_from_disk() is None)
tmpl = pathlib.Path("packaging/password.txt.template")
check("P0-3 password.txt.template 已清空", tmpl.stat().st_size == 0, "size=%d" % tmpl.stat().st_size)

# ---- P0-7：AppExit 单一口径 ----
check("P0-7 install.bat AppExit=Ignore", "AppExit 0 Ignore" in pathlib.Path("install.bat").read_text(encoding="utf-8", errors="replace"))
check("P0-7 auto_update 不再写回 Restart", "AppExit 已恢复为 Default" not in src_upd)

# ---- P1-4：导出不含密码 ----
check("P1-4 导出不再写 password.txt", 'zf.writestr("password.txt"' not in src_web)
check("P1-4 manifest 记 password_status", '"password_status"' in src_web)

# ---- P1-1：请求校验层在位 ----
check("P1-1 Host 白名单", "allowed_hosts" in src_web)
check("P1-1 写请求要求自定义头", '"X-Requested-With") != "DrcomUI"' in src_web)
check("P1-1 do_GET/do_POST 都调用 _check_request", src_web.count("if not self._check_request():") == 2)

# ---- 版本一致性 ----
import version
iss = pathlib.Path("packaging/setup.iss").read_text(encoding="utf-8", errors="replace")
check("版本一致 version.py vs setup.iss", ('#define MyAppVersion "%s"' % version.VERSION) in iss)
check("版本 = 2.0.2.4", version.VERSION == "2.0.2.4", version.VERSION)

print("\n结果：%d 项失败 / %d 项检查" % (len(FAILS), TOTAL[0]))
sys.exit(1 if FAILS else 0)
