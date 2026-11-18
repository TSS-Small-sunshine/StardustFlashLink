# -*- coding: utf-8 -*-
"""静态/单元冒烟测试（覆盖 v2.0.2.4 安全项 + v2.0.3.0 前端/品牌路由 + v2.0.4.0 修复项）。

跑法：python _smoke_static.py（在 DrcomAutoLogin-Windows 目录下）
隐私守卫：想连真机安装目录一起查，先设 DRCOM_DATA_DIR（见文末「隐私守卫」）。
"""
import pathlib
import re
import sys
import tempfile
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 控制台编码兜底：CI（cp1252）或任何非 UTF-8 终端下，print 中文断言名会抛 UnicodeEncodeError
# —— 那会把「断言失败」变成「脚本崩溃」，CI 里只看得到编码错误、看不到真正的失败项。
# 这里只改错误处理方式（不改成 utf-8，免得 GBK 终端变乱码）。
try:
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.stderr.reconfigure(errors="backslashreplace")
except (AttributeError, ValueError):
    pass

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
check("P0-3 跳过模板提示行取到真密码", svc._load_password_from_disk() == "realpass")
# v2.0.4.0：语义收紧 —— 只有模板提示行算注释；其它 `#` 行一律按密码原文处理
pathlib.Path(p).write_text("# 只有一行井号文本\n\n", encoding="utf-8")
check("P0-3 非模板 # 行按密码原文处理", svc._load_password_from_disk() == "# 只有一行井号文本")
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

# ---- v2.0.3.0：Apple 风格前端 + 品牌图片静态路由 ----
check("v2.0.3.0 /branding 路由已挂载", 'if path.startswith("/branding/")' in src_web)
check("v2.0.3.0 _serve_branding 方法", "def _serve_branding(self, path)" in src_web)
check("v2.0.3.0 文件名白名单正则", "_BRANDING_NAME_RE = re.compile(" in src_web)
check("v2.0.3.0 favicon 用绝对路径", 'href="/branding/web-logo-32.png"' in src_web)
check("v2.0.3.0 顶栏 logo 用绝对路径", 'src="/branding/web-logo-64.png"' in src_web)
check("v2.0.3.0 logo 失败兜底 bindBrand", "function bindBrand()" in src_web and "bindBrand();" in src_web)
check("v2.0.3.0 pollUpdate banner 作用域已修", "var bnr = $('update-banner');" in src_web)
check("v2.0.3.0 toast 改内联 SVG 图标", "_ICO.check" in src_web and "_ICO.warn" in src_web)
check("v2.0.3.0 顶栏亚克力玻璃材质", "--blur: saturate(180%) blur(30px)" in src_web and "backdrop-filter: var(--blur)" in src_web)
check("v2.0.3.0 旧网格底纹已移除", "background-size: 40px 40px" not in src_web)
check("v2.0.3.0 品牌区两行锁排", 'class="brand-sub"' in src_web)
_page = src_web.split('_HTML_PAGE = r"""', 1)[1]
_left = sorted(set(__import__("re").findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]", _page)))
check("v2.0.3.0 页面内无 emoji 图标", not _left, "残留=%s" % _left)

# ---- v2.0.4.0：密码 / 启动钩子 / AppExit / 升级熔断 / 静默安装 ----
# (1) 密码：不得再用 `#` 判定注释（真机上「# 包裹的密码」曾被整行吃掉）
#     注意：这里用**合成夹具**，绝不把任何真机密码写进本文件（见文末「隐私守卫」）。
_HASH_WRAPPED = "#Demo-Pwd-0000#"
check("v2.0.4.0 密码不再按 # 判注释", 's.startswith("#")' not in src_svc)
check("v2.0.4.0 只跳过模板提示行", "_PASSWORD_HINT_MARKERS" in src_svc and "_is_password_hint" in src_svc)
_tmp_pwd = os.path.join(tmp, "password_regress.txt")
svc.PASSWORD_FILE = _tmp_pwd
pathlib.Path(_tmp_pwd).write_text(_HASH_WRAPPED + "\n", encoding="utf-8")
check("v2.0.4.0 井号包裹的密码能读回", svc._load_password_from_disk() == _HASH_WRAPPED,
      repr(svc._load_password_from_disk()))
pathlib.Path(_tmp_pwd).write_bytes(b"\xef\xbb\xbfpwd_with_bom\n")
check("v2.0.4.0 容忍 BOM 的密码文件", svc._load_password_from_disk() == "pwd_with_bom",
      repr(svc._load_password_from_disk()))
pathlib.Path(_tmp_pwd).write_text("# 在此行写入你的校园网账号密码\nrealpass2\n", encoding="utf-8")
check("v2.0.4.0 模板提示行仍被跳过", svc._load_password_from_disk() == "realpass2")
pathlib.Path(_tmp_pwd).write_text("# 在此行写入你的校园网账号密码\n", encoding="utf-8")
check("v2.0.4.0 全是提示行 -> None", svc._load_password_from_disk() is None)

# (2) 启动钩子必须在 auto_update _attach 之后调用（否则 LOG_DIR 未注入 → 钩子从未生效）
check("v2.0.4.0 启动钩子在 _attach 之后",
      src_svc.index("_auto_update_mod._attach(") < src_svc.index("_auto_update_mod._post_upgrade_startup()"))

# (3) AppExit：空值视为无效 + 自愈
check("v2.0.4.0 AppExit 空值视为无效", "if not isinstance(value, str) or not value.strip():" in src_upd)
check("v2.0.4.0 AppExit 自愈函数在位", "_ensure_nssm_appexit_sane" in src_upd and "AppExit 自愈" in src_upd)
check("v2.0.4.0 AppExit 优先走 nssm.exe",
      'NSSM_PATH, "set", SERVICE_NAME, "AppExit", subparam.strip()' in src_upd)

# (4) 升级熔断：同版本失败后冷却 / 上限
check("v2.0.4.0 升级尝试记录落盘", "update_attempt.json" in src_upd and "_write_update_attempt" in src_upd)
check("v2.0.4.0 自动重试熔断", "_auto_retry_blocked" in src_upd and "UPDATE_ATTEMPT_COOLDOWN_SEC" in src_upd)
check("v2.0.4.0 启动钩子确认升级结果", "升级成功确认" in src_upd and "上次自动升级未生效" in src_upd)

# (5) 静默安装参数 + 安装器不弹窗
check("v2.0.4.0 installer 用 /VERYSILENT", "/VERYSILENT" in src_upd and '"/SILENT",' not in src_upd)
check("v2.0.4.0 installer 抑制弹窗", "/SUPPRESSMSGBOXES" in src_upd and "/NORESTART" in src_upd)
check("v2.0.4.0 installer 落盘安装日志", "installer-silent.log" in src_upd)
_iss_src = pathlib.Path("packaging/setup.iss").read_text(encoding="utf-8", errors="replace")
check("v2.0.4.0 setup.iss 静默不弹 MsgBox", "if WizardSilent then" in _iss_src)

# (6) CHANGELOG 随包分发 + 多路径查找（真机弹窗报 "No such file or directory"）
_src_eula = pathlib.Path("eula.py").read_text(encoding="utf-8")
check("v2.0.4.0 CHANGELOG 已随包打包", 'Source: "..\\CHANGELOG.md"' in _iss_src)

# ---- v2.0.4.1：快捷方式 / 开始菜单 / 卸载项图标改用品牌 app.ico ----
check("v2.0.4.1 app.ico 随包分发", 'Source: "branding\\app.ico"' in _iss_src)
check("v2.0.4.1 快捷方式图标用品牌 ico",
      'IconFilename: "{app}\\branding\\app.ico"' in _iss_src)
check("v2.0.4.1 不再回退到 shell32 通用图标",
      'IconFilename: "{sys}\\shell32.dll"' not in _iss_src)   # 注释里提到历史做法不算
check("v2.0.4.1 卸载项图标用品牌 ico", "UninstallDisplayIcon={app}\\branding\\app.ico" in _iss_src)
check("v2.0.4.1 .url 带 IconFile", "'IconFile=' + ExpandConstant('{app}\\branding\\app.ico')" in _iss_src)

# ---- v2.0.4.4：安装向导改用多尺寸品牌图（不再单张 wizard.bmp）----
# 参数与 _ui_redesign/flashlink-mock.iss 上真机验证过的一致。
check("v2.0.4.4 向导大图多尺寸列表已接入",
      "WizardImageFile=branding\\wizard-left-202x386.png" in _iss_src
      and "branding\\wizard-left-430x824.png" in _iss_src)
check("v2.0.4.4 向导小图多尺寸列表已接入",
      "WizardSmallImageFile=branding\\wizard-small-58x58.png" in _iss_src
      and "branding\\wizard-small-124x124.png" in _iss_src)
check("v2.0.4.4 向导暗色风格 + 欢迎页",
      "WizardStyle=modern dark includetitlebar hidebevels" in _iss_src
      and "WizardSizePercent=110" in _iss_src and "DisableWelcomePage=no" in _iss_src)
check("v2.0.4.4 旧的单张 wizard.bmp 已不再被引用",
      "WizardImageFile=branding\\wizard.bmp" not in _iss_src
      and not pathlib.Path("packaging/branding/wizard.bmp").exists())

# ---- v2.0.4.5：并发登录死逻辑 / 写盘 tmp 名 / Inno 弃用 API ----
_src_proto = pathlib.Path("protocol.py").read_text(encoding="utf-8")
_proto_code = "\n".join(l for l in _src_proto.splitlines() if not l.strip().startswith("#"))
check("v2.0.4.5 run_once 改为非阻塞抢锁", "_RUN_LOCK.acquire(blocking=False)" in _src_proto)
check("v2.0.4.5 不再先持锁再判 login_in_progress（死逻辑已除）", "with _RUN_LOCK:" not in _proto_code)
check("v2.0.4.5 手工抢到的锁会被释放", "_RUN_LOCK.release()" in _src_proto)
# v2.x 维护（审计 C-01）：写盘从「pid 级固定 tmp 名」升级为 tempfile.mkstemp 唯一名 +
# CONFIG_LOCK —— 同进程多线程并发写不再互相踩（pid 名只挡得住跨进程）。
check("v2.x 维护 config 写盘走唯一 tmp 名（mkstemp，不再用 os.getpid）",
      "def _atomic_write_text(" in src_svc
      and "tempfile.mkstemp(" in src_svc
      and "_atomic_write_text(CONFIG_FILE, text)" in src_svc
      and '"{}.{}.tmp".format(CONFIG_FILE' not in src_svc)
check("v2.x 维护 password 写盘走同一套原子写",
      '_atomic_write_text(path, password.rstrip(' in src_svc
      and '"{}.{}.tmp".format(path' not in src_svc)
check("v2.x 维护 config.json 的读-改-写有 CONFIG_LOCK（RLock，可重入）",
      "CONFIG_LOCK = threading.RLock()" in src_svc
      and "with CONFIG_LOCK:" in src_svc)
check("v2.0.4.5 installer 不再用弃用的 IsTaskSelected",
      "WizardIsTaskSelected('desktopicon')" in _iss_src
      and "if IsTaskSelected('desktopicon')" not in _iss_src)

# 行为级：非阻塞抢锁的语义（连点登录不再排队，靠的就是这两条）
# 注意：protocol._RUN_LOCK 平时由 _attach() 注入（导入时是 None），这里自己放一把，
# 只影响本测试进程，不碰正在跑的服务。
import threading as _th2
import protocol as _pl
_pl._RUN_LOCK = _th2.Lock()
_got1 = _pl._RUN_LOCK.acquire(blocking=False)
check("v2.0.4.5 RUN_LOCK 可非阻塞抢到", _got1 is True)
_got2 = _pl._RUN_LOCK.acquire(blocking=False)
check("v2.0.4.5 已占用时非阻塞抢锁立即返回 False（不排队）", _got2 is False)
if _got1:
    _pl._RUN_LOCK.release()

# ---- v2.0.5.0：网络位置守卫（SSID / 网段白名单）----
_g = _pl.guard_allows
check("v2.0.5.0 守卫未启用 → 放行", _g({"network_guard_enabled": False}, "Home", ["192.168.1.5"])[0] is True)
check("v2.0.5.0 SSID 命中白名单 → 放行",
      _g({"network_guard_enabled": True, "guard_allowed_ssids": "Campus-WiFi,Dorm"}, "Dorm", ["192.168.1.5"])[0] is True)
check("v2.0.5.0 网段命中 → 放行（有线也适用）",
      _g({"network_guard_enabled": True, "guard_allowed_subnets": "172.16.0.0/12"}, "Home", ["172.20.3.9"])[0] is True)
check("v2.0.5.0 有线无 SSID 但网段命中 → 放行",
      _g({"network_guard_enabled": True, "guard_allowed_ssids": "Campus",
          "guard_allowed_subnets": "10.0.0.0/8"}, None, ["10.20.30.40"])[0] is True)
check("v2.0.5.0 两个白名单都不命中 → 拒绝",
      _g({"network_guard_enabled": True, "guard_allowed_ssids": "Campus",
          "guard_allowed_subnets": "172.16.0.0/12"}, "Home", ["192.168.1.5"])[0] is False)
check("v2.0.5.0 开了守卫但白名单为空 → 放行（等于没配）",
      _g({"network_guard_enabled": True, "guard_allowed_ssids": "", "guard_allowed_subnets": ""},
         "Home", ["192.168.1.5"])[0] is True)
check("v2.0.5.0 读不到 SSID/IP → fail-open 放行",
      _g({"network_guard_enabled": True, "guard_allowed_ssids": "Campus"}, None, [])[0] is True)
check("v2.0.5.0 中文逗号也能当分隔符",
      _g({"network_guard_enabled": True, "guard_allowed_ssids": "Campus，Dorm"}, "Dorm", ["1.2.3.4"])[0] is True)

# 配置层：默认配置能过校验、非法 CIDR 被拦
_good = dict(svc.DEFAULT_CONFIG)
_good["account"] = "20230001"
check("v2.0.5.0 默认配置（含守卫字段）校验通过", svc._validate_config(_good) == [], svc._validate_config(_good))
_bad = dict(_good)
_bad["guard_allowed_subnets"] = "172.16.0.0/12,not-a-cidr"
check("v2.0.5.0 非法网段被拦", any("非法网段" in e for e in svc._validate_config(_bad)))
_bad2 = dict(_good)
_bad2["network_guard_enabled"] = "yes"
check("v2.0.5.0 守卫开关必须是布尔值", any("network_guard_enabled" in e for e in svc._validate_config(_bad2)))
check("v2.0.5.0 配置页有守卫开关 + 两个白名单输入",
      all(x in src_web for x in ("cfg-guard-enabled", "cfg-guard-ssids", "cfg-guard-subnets")))
check("v2.0.5.0 前端提交时带上守卫字段",
      "network_guard_enabled: $('cfg-guard-enabled').checked" in src_web
      and "guard_allowed_ssids: $('cfg-guard-ssids').value.trim()" in src_web
      and "guard_allowed_subnets: $('cfg-guard-subnets').value.trim()" in src_web)
check("v2.0.5.0 run_once 真的接上了守卫", "guard_allows(cfg, _ssid, _ips)" in _src_proto)
_guard_slice = "\n".join(
    l for l in _src_proto.split("1.5 网络位置守卫")[1].split("host = cfg")[0].splitlines()
    if not l.strip().startswith("#")
)
check("v2.0.5.0 守卫不触发退避（跳过不等于失败）",
      "不调 _set_backoff" in _src_proto and "_set_backoff" not in _guard_slice)

# ---- v2.0.6.0：托盘小程序（断线通知 + 状态图标）----
_tray_src = pathlib.Path("tray.py").read_text(encoding="utf-8")
_au_src = pathlib.Path("auto_update.py").read_text(encoding="utf-8")
_tray = importlib.import_module("tray")
check("v2.0.6.0 tray.py 随包分发", 'Source: "..\\tray.py"' in _iss_src)
check("v2.0.6.0 托盘随登录启动（HKCU Run 项 + 卸载自动清）",
      "DrcomAutoLoginTray" in _iss_src and "uninsdeletevalue" in _iss_src)
check("v2.0.6.0 托盘任务在安装向导里可选", 'Name: "trayicon"' in _iss_src)
# v2.0.6.1：Run 项必须挂 HKLM —— 自动升级是 schtasks /ru SYSTEM 跑的，
# 那时 HKCU 写进的是 SYSTEM 的配置单元，真实用户登录不会自启托盘
_tray_reg = _iss_src.split("[Registry]")[1].split("[Run]")[0] if "[Registry]" in _iss_src else ""
check("v2.0.6.1 托盘自启项挂 HKLM（不能是 HKCU）",
      "Root: HKLM;" in _tray_reg and "Root: HKCU;" not in _tray_reg
      and 'Subkey: "Software\\Microsoft\\Windows\\CurrentVersion\\Run"' in _tray_reg)
check("v2.0.6.1 托盘会自己识别「已卸载」并退出（不留孤儿图标）",
      "_log(\"安装目录里已经没有 tray.py" in _tray_src and "missing_streak >= 2" in _tray_src)
check("v2.0.6.0 升级时保留托盘任务（否则升级会摘掉登录启动项）",
      _au_src.count("desktopicon,startservice,trayicon") == 2)
check("v2.0.6.0 托盘不引入第三方依赖（pystray/requests 之类都不许）",
      all(x not in _tray_src for x in ("pystray", "import requests", "win32gui", "PyQt")))
check("v2.0.6.0 托盘只连本机 127.0.0.1", "http://127.0.0.1:%d" in _tray_src)
check("v2.0.6.0 托盘写接口带自定义头（否则被 403 挡掉）",
      '_request("/api/login", method="POST")' in _tray_src
      and '"X-Requested-With", "DrcomUI"' in _tray_src)

# 行为级：状态迁移 → 通知（防刷屏规则）
_d = _tray.decide_events
check("v2.0.6.0 托盘首轮不弹（开机时服务可能还在起）", _d(None, "off", 0) == [] and _d(None, "down", 9) == [])
check("v2.0.6.0 掉线弹警告", _d("ok", "off", 0)[0][0] == "已掉线")
check("v2.0.6.0 恢复登录弹提示", _d("off", "ok", 0)[0][0] == "已恢复登录")
check("v2.0.6.0 偶发一次拿不到服务不弹", _d("ok", "down", 1) == [])
check("v2.0.6.0 连续三次拿不到服务才弹", _d("ok", "down", 3)[0][0] == "服务未响应")
check("v2.0.6.0 同一状态不重复弹", _d("off", "off", 0) == [] and _d("ok", "ok", 0) == [])
check("v2.0.6.0 classify 四态齐全",
      [_tray.classify({"_reachable": True, "online": True}),
       _tray.classify({"_reachable": True, "online": False}),
       _tray.classify({"_reachable": True, "online": None}),
       _tray.classify({"_reachable": False})]
      == ["ok", "off", "unknown", "down"])
check("v2.0.6.0 拿不到服务时的判定文案", _tray.status_text({"_reachable": False}) == "服务未响应")

# ---- v2.0.6.2：托盘自启项自愈（升级路径保证不了 Inno 任务被"选中"）----
_au = importlib.import_module("auto_update")
# BASE_DIR 平时由 _attach() 注入，导入时是 None —— 测试里指到仓库根（那儿有 tray.py）
_au.BASE_DIR = os.getcwd()
_act = _au.tray_autostart_action
_EXP = '"C:\\app\\python\\pythonw.exe" "C:\\app\\tray.py"'
check("v2.0.6.2 缺自启项 → 建", _act(True, True, None, _EXP) == "create")
check("v2.0.6.2 已经正确 → 不动", _act(True, True, _EXP, _EXP) == "keep")
check("v2.0.6.2 值不对（旧路径 / 被人改过）→ 修回来", _act(True, True, '"x" "y"', _EXP) == "create")
check("v2.0.6.2 用户关掉开关 → 删掉", _act(False, True, _EXP, _EXP) == "delete")
check("v2.0.6.2 关掉且本来就没有 → 不瞎写", _act(False, True, None, _EXP) == "keep")
check("v2.0.6.2 tray.py 不在（没装托盘）→ 清理掉", _act(True, False, _EXP, _EXP) == "delete")
check("v2.0.6.2 dry-run 不碰注册表且结论合法",
      _au._ensure_tray_autostart_sane(dry_run=True)["action"] in ("create", "keep", "delete"))
check("v2.0.6.2 安装器把开关记进 HKLM（供服务对齐）",
      "TrayAutostart" in _iss_src and "WizardIsTaskSelected('trayicon')" in _iss_src)
check("v2.0.6.2 启动钩子会调用自愈", "_ensure_tray_autostart_sane()" in _au_src)

# ---- v2.0.7.0（B3）：唤醒 / 换网 → 事件驱动立即重连 ----
# 真机证据（2026-09-28 本机实跑 --test-event，日志到秒）：
#   [08:58:44] 事件：网络地址变化 → 立即重连：已触发一次登录检查
#   [08:59:00] 事件：从睡眠唤醒 → 4 秒后触发重连
#   [08:59:04] 事件：从睡眠唤醒 → 立即重连：已触发一次登录检查
check("v2.0.7.0 三种「醒过来」都识别", _tray.power_event_kind(0x12) == "从睡眠唤醒"
      and _tray.power_event_kind(0x07) == "从睡眠唤醒"
      and _tray.power_event_kind(0x06) == "从睡眠唤醒")
check("v2.0.7.0 进入睡眠 / 无关电源事件不触发重连", _tray.power_event_kind(0x04) is None
      and _tray.power_event_kind(0x8013) is None and _tray.power_event_kind(0) is None)
check("v2.0.7.0 事件去抖：5 秒内连着来的只打一次",
      _tray.should_reconnect(None, 1000) is True
      and _tray.should_reconnect(999, 1000) is False
      and _tray.should_reconnect(995, 1000) is True
      and _tray.should_reconnect(990, 1000) is True)
check("v2.0.7.0 接电源广播 + 网络地址变化", "WM_POWERBROADCAST" in _tray_src
      and "NotifyAddrChange" in _tray_src and "WM_NETCHANGE" in _tray_src)
check("v2.0.7.0 网络变化用后台线程等（不占消息循环）",
      "def _addr_change_loop" in _tray_src
      and "threading.Thread(target=self._addr_change_loop, daemon=True)" in _tray_src)
check("v2.0.7.0 唤醒后延后触发（刚醒时网卡还没连上）",
      "TIMER_WAKE" in _tray_src and _tray.WAKE_SETTLE_SEC >= 3)
check("v2.0.7.0 监听线程异常只退化成轮询（不许拖垮托盘）", "退回纯轮询" in _tray_src)
# 真跑才抓到的坑：ctypes.wintypes **没有** OVERLAPPED —— 直接用会 AttributeError，
# 而它发生在 _declare_win32 里 → 托盘启动即崩。必须自带结构体定义。
check("v2.0.7.0 OVERLAPPED 自带定义（wintypes 里没有）",
      "class OVERLAPPED(ctypes.Structure)" in _tray_src
      and "wintypes.OVERLAPPED" not in _tray_src)
check("v2.0.7.0 事件模拟入口在位（本机实测用）",
      "--test-event" in _tray_src and "def test_event" in _tray_src)

# ---- v2.0.7.1：升级后托盘自己换上新代码 ----
# 真机证据（2.0.6.3 → 2.0.7.0 升级后）：tray 进程 PID 没变 —— 安装器确实又拉了一次，
# 但老实例握着单实例互斥体，新实例一起就秒退 → "文件已换、代码没换"，且完全静默。
_sig_tmp = os.path.join(tmp, "tray_sig_probe.py")
pathlib.Path(_sig_tmp).write_text("# probe\n", encoding="utf-8")
_sig1 = _tray.code_signature(_sig_tmp)
# v2.1.3.7（审计 C-30）起指纹是 (mtime, size, sha256[:16]) 三元组，不再是二元组
check("v2.0.7.1 脚本指纹读得到（mtime + size + 头部哈希）",
      isinstance(_sig1, tuple) and len(_sig1) == 3
      and _sig1[1] == os.path.getsize(_sig_tmp)
      and isinstance(_sig1[2], str) and len(_sig1[2]) == 16)
check("v2.0.7.1 文件不在时指纹为 None（不误判成「变了」）",
      _tray.code_signature(os.path.join(tmp, "no_such_file_xyz.py")) is None)
os.utime(_sig_tmp, (1000000000, 1000000000))
check("v2.0.7.1 mtime 一变 → 指纹就变", _tray.code_signature(_sig_tmp) != _sig1)
check("v2.0.7.1 轮询里会检查指纹并重启自己",
      "self._code_changed()" in _tray_src and "self._restart_self()" in _tray_src)
check("v2.0.7.1 重启前先放手单实例互斥体（否则新实例秒退）",
      "self.kernel32.CloseHandle(self.mutex_handle)" in _tray_src
      and "app.mutex_handle = handle" in _tray_src)
check("v2.0.7.1 用独立进程起新实例（DETACHED_PROCESS + 不继承控制台）",
      "DETACHED_PROCESS" in _tray_src and "subprocess.Popen(" in _tray_src
      and "subprocess.DEVNULL" in _tray_src)
check("v2.0.7.1 新实例起不来时把互斥体拿回来（不放弃当前进程）",
      "self.mutex_handle = _single_instance()[0]" in _tray_src)

# ---- v2.0.8.0（B4）：连接质量面板（数据来自 logs/campus_login.log，不落新状态文件）----
_mx = importlib.import_module("metrics")
check("v2.0.8.0 打包带上 metrics.py", 'Source: "..\\metrics.py"' in _iss_src)
check("v2.0.8.0 服务注入 metrics（_attach 在位）",
      "import metrics as _metrics_mod" in src_svc and "_metrics_mod._attach(" in src_svc)
check("v2.0.8.0 /api/metrics 路由 + 优雅降级",
      'path == "/api/metrics"' in src_web and "def api_get_metrics(" in src_web
      and "统计模块未就绪" in src_web)
check("v2.1.2.0 连接质量板块已删（用户：「没啥用」）—— 卡片 / 柱图 / 刷新按钮 / 相关 CSS 全清掉",
      'id="card-quality"' not in src_web and 'id="qbars"' not in src_web
      and 'id="btn-metrics-refresh"' not in src_web and 'id="quality-note"' not in src_web
      and ".qbars" not in src_web and "qbar-fill" not in src_web and "qbar-day" not in src_web)
check("v2.1.2.0 诊断指标不再折叠（用户：「直接显示出来」）—— 改 4 格统计条，且仍不引图表库",
      'class="card stat-strip stat-strip-4"' in src_web
      and 'id="kpi-uptime"' in src_web and 'id="kpi-relogin"' in src_web
      and 'id="kpi-recover"' in src_web and 'id="kpi-latency"' in src_web
      and '<details class="diag">' not in src_web and ".diag-body" not in src_web
      and "chart.js" not in src_web.lower() and "echarts" not in src_web.lower())
# 行为级：三种走向 + 窗口过滤 + 老格式（没有「耗时」字段）容忍
_ev = _mx.parse_log_lines([
    "[2026-09-28 07:28:05] [INFO] 开始检查 (reason=periodic)",
    "[2026-09-28 07:28:05] [INFO] 网络已可达（第 2 次尝试）",        # 老格式：无耗时
    "[2026-09-28 07:28:06] [INFO] 登录成功: Portal协议认证成功！",
    "[2026-09-28 07:00:00] [INFO] 开始检查 (reason=periodic)",
    "[2026-09-28 07:00:00] [INFO] 已在线，无需登录",
    "[2026-08-01 07:00:00] [INFO] 开始检查 (reason=periodic)",        # 窗口外 → 不计
    "[2026-08-01 07:00:01] [ERROR] 登录失败: 老数据",
    "没有时间戳的行也要被忽略",
])
_sum = _mx.summarize(_mx.group_cycles(_ev), _mx._parse_ts("2026-09-28 09:00:00"), days=7)
check("v2.0.8.0 三种走向解析正确（online/relogin/checks）",
      (_sum["checks"], _sum["online"], _sum["relogin"]) == (2, 1, 1),
      str((_sum["checks"], _sum["online"], _sum["relogin"])))
check("v2.0.8.0 窗口外的周期不计入", _sum["fail"] == 0, str(_sum["fail"]))
check("v2.0.8.0 平均恢复耗时 = 1 秒", _sum["avg_recover_ms"] == 1000,
      str(_sum["avg_recover_ms"]))
check("v2.0.8.0 老格式行不炸：可达耗时 None、重试次数照算",
      _sum["avg_reach_ms"] is None and _sum["avg_attempts"] == 2.0,
      str(_sum["avg_attempts"]))
check("v2.0.8.0 空输入 / 非法 days 不炸",
      _mx.summarize([], 1000, days=7)["checks"] == 0
      and _mx.summarize([], 1000, days="abc")["days"] == 7)

# ---- v2.0.8.1：安装器中止回滚 → 「半新半旧」→ 服务起不来（真机 2026-09-28 实测）----
# installer-silent.log：
#   09:25:48.922  DeleteFile: The existing file appears to be in use (5). Retrying.
#   09:25:52.969  ...python\libcrypto-3.dll 拒绝访问 → User canceled the installation process.
#   09:25:52.969  Rolling back changes.  /  09:25:52.971  Deleting file: ...\metrics.py
# service_stderr.log: ModuleNotFoundError: No module named 'metrics'
#   → 服务主进程秒退（nssm AppExit=Ignore 不重启）→ Web UI 端口整个消失 ✗
# 根因：托盘进程（{app}\python\pythonw.exe）import urllib→ssl 锁住了 libcrypto-3.dll，
#       Inno 替换 DLL 失败后静默 Abort → 回滚，却留下「一半新一半旧」。
_iss_py_sources = set(re.findall(r'Source:\s*"\.\.\\([A-Za-z_][A-Za-z0-9_]*\.py)"', _iss_src))
_local_imports = set(re.findall(r'^(?:import|from)\s+([a-z_][a-z0-9_]*)\b', src_svc, re.M))
_missing_pack = sorted(m for m in _local_imports
                       if os.path.isfile(m + ".py") and (m + ".py") not in _iss_py_sources)
check("v2.0.8.1 打包完整性：服务 import 的本地模块全部在 setup.iss 里",
      not _missing_pack, "漏: %s（已打包: %s）" % (_missing_pack, sorted(_iss_py_sources)))
check("v2.0.8.1 可选模块导入带降级（缺文件也不许服务崩）",
      "except ImportError as _exc:" in src_svc and "_METRICS_IMPORT_ERROR" in src_svc
      and "if _metrics_mod is not None:" in src_svc)
_ptray_src = pathlib.Path("packaging/stop-tray.ps1").read_text(encoding="utf-8")
check("v2.0.8.1 装前请走托盘：脚本在位 + dontcopy + ExtractTemporaryFile + PrepareToInstall",
      "Source: \"stop-tray.ps1\"; Flags: dontcopy" in _iss_src
      and "ExtractTemporaryFile('stop-tray.ps1')" in _iss_src
      and "function PrepareToInstall(" in _iss_src
      and "function KillTray(" in _iss_src)
check("v2.0.8.1 请托盘脚本只按「tray.py + 安装目录」匹配（不误伤别的 pythonw）",
      "$AppDir" in _ptray_src and "Name='pythonw.exe'" in _ptray_src
      and "-like '*tray.py*'" in _ptray_src and "exit 0" in _ptray_src)

# ---- v2.0.9.0（B5）：配置方案（教室 / 宿舍 / 家里）----
src_proto = pathlib.Path("protocol.py").read_text(encoding="utf-8")
_pf = importlib.import_module("profiles")
check("v2.0.9.0 打包带上 profiles.py", 'Source: "..\\profiles.py"' in _iss_src)
check("v2.0.9.0 服务按可选模块导入 + 注入",
      "import profiles as _profiles_mod" in src_svc and "_PROFILES_IMPORT_ERROR" in src_svc
      and "_profiles_mod._attach(" in src_svc)
check("v2.0.9.0 协议层只认回调（不 import profiles 模块）",
      "_set_auto_profile" in src_proto and "import profiles as" not in src_proto
      and "_auto_profile is not None" in src_proto)
check("v2.0.9.0 自动切换排在守卫判定之前（否则本次仍按旧白名单 ✗）",
      src_proto.index("_auto_profile is not None") < src_proto.index("_allowed, _why = guard_allows"))
check("v2.0.9.0 五个接口 + 配置页卡片在位",
      all(x in src_web for x in ('"/api/profiles"', '"/api/profiles/save"',
                                 '"/api/profiles/activate"', '"/api/profiles/delete"',
                                 '"/api/profiles/auto"', 'id="card-profiles"',
                                 'id="profile-select"', 'id="profile-auto-switch"')))
# 行为级：快照 / 新建 / 应用 / 校验 / 自动匹配
_cfg0 = {"host": "1.2.3.4", "port": 80, "auto_check_interval_min": 30, "account": "2023",
         "network_guard_enabled": False, "guard_allowed_ssids": "", "guard_allowed_subnets": ""}
_snap = _pf.snapshot(_cfg0)
check("v2.0.9.0 / v2.1.1.0 快照含位置相关字段（v2.1.1.0 起账号与后缀也在内）",
      "account" in _snap and set(_snap) <= set(_pf.PROFILE_KEYS),
      str(sorted(_snap)))
_profs, _errs = _pf.upsert({}, "家里",
                           {"network_guard_enabled": True, "guard_allowed_ssids": "Home-WiFi"},
                           "Home-WiFi")
check("v2.0.9.0 新建方案 + 记下自动匹配 Wi-Fi",
      not _errs and _profs["家里"]["values"]["network_guard_enabled"] is True
      and _profs["家里"]["match_ssids"] == ["Home-WiFi"], str(_errs))
check("v2.0.9.0 方案名归一化 / 非法名被拒",
      _pf.normalize_name("  教  室 ") == "教 室" and _pf.upsert({}, "a/b", {})[1] != [])
check("v2.0.9.0 未知字段被拒（**密码绝不许塞进方案**）",
      _pf.validate_values({"password": "x"}) != [] and _pf.validate_values({"账号": "x"}) != [])
_merged, _errs2 = _pf.apply_to_config(_cfg0, _profs, "家里")
check("v2.0.9.0 应用方案：覆盖位置字段 + 保留账号 + 不改原对象",
      not _errs2 and _merged["guard_allowed_ssids"] == "Home-WiFi"
      and _merged["account"] == "2023" and _merged["active_profile"] == "家里"
      and _cfg0["guard_allowed_ssids"] == "" and "active_profile" not in _cfg0, str(_errs2))
_merged2, _errs3 = _pf.apply_to_config(_cfg0, _profs, "家里", lambda cfg: ["故意报错"])
check("v2.0.9.0 全量校验不过 → 整体拒绝（原配置一动不动）",
      _errs3 != [] and _merged2 is _cfg0)
check("v2.0.9.0 按 Wi-Fi 名挑方案（大小写不敏感）",
      _pf.pick_by_ssid(_profs, "home-wifi") == "家里"
      and _pf.pick_by_ssid(_profs, "别的网") is None)
check("v2.0.9.0 匹配名去重 + 描述文案",
      _pf.normalize_match_ssids("a, b, a") == ["a", "b"]
      and "守卫开" in _pf.describe({"network_guard_enabled": True, "auto_check_interval_min": 30}))
check("v2.0.9.0 删方案：删当前方案只清标记",
      _pf.delete(_profs, "家里")[0] == {} and _pf.delete(_profs, "没有这个")[1] is False)

# ---- v2.0.9.1：不得在函数内 import 模块别名（会把模块级同名变量变成局部 → UnboundLocalError）----
# 真机事故（2026-09-28，v2.0.9.0）：main() 里多了一句 `import protocol as _protocol_mod`，
# 于是同一函数里更早的 `_protocol_mod._attach(...)` 直接崩：
#   UnboundLocalError: cannot access local variable '_protocol_mod'
#   → 服务启动即退（nssm AppExit=Ignore 不重启）→ Web UI 端口消失 ✗
_svc_lines = src_svc.splitlines()
_svc_func_imports = []
for _i, _ln in enumerate(_svc_lines):
    if not re.match(r'^\s+import\s+\w+\s+as\s+_\w+', _ln):
        continue
    # 允许的唯一形式：模块级 `try: import X as _X except ImportError:` 的「可选模块」导入
    _prev = ""
    for _j in range(_i - 1, -1, -1):
        if _svc_lines[_j].strip():
            _prev = _svc_lines[_j].strip()
            break
    if _prev != "try:":
        _svc_func_imports.append(_ln.strip())
check("v2.0.9.1 服务里没有「函数内 import 模块别名」（会遮蔽模块级名）",
      not _svc_func_imports, str(_svc_func_imports))
check("v2.0.9.1 自动切换回调挂在模块级 _protocol_mod 上（无重复 import）",
      "_protocol_mod._set_auto_profile(" in src_svc)

# ---- v2.0.4.1：修 PWD_LOCK 自锁死锁（api_get_config 套了两层不可重入锁）----
_apicfg = src_web.split("def api_get_config()")[1].split("def api_post_config")[0]
# 只看代码行：注释里出现 "with PWD_LOCK" 不算（注释正是用来解释这条约定的）
_apicfg_code = "\n".join(l for l in _apicfg.splitlines() if not l.strip().startswith("#"))
check("v2.0.4.1 api_get_config 不再自锁 PWD_LOCK", "with PWD_LOCK" not in _apicfg_code)
check("v2.0.4.1 api_get_config 仍读密码状态", "_get_password()" in _apicfg)
check("v2.0.4.1 加锁责任在 _get_password 内部",
      "def _get_password():" in src_svc and "with PWD_LOCK:" in
      src_svc.split("def _get_password():")[1].split("def ")[0])

# 行为级回归：真跑一次 api_get_config()，2 秒内必须返回（死锁时会永远卡住）
import threading as _threading
_wa = importlib.import_module("web_api")
_cfg_tmp = os.path.join(tmp, "config.json")
svc.CONFIG_FILE = _cfg_tmp
_wa._load_config = svc._load_config
_wa._get_password = svc._get_password
_wa.DEFAULT_CONFIG = svc.DEFAULT_CONFIG
_wa.PWD_LOCK = svc.PWD_LOCK
_box = {}


def _call_get_config():
    try:
        _box["r"] = _wa.api_get_config()
    except Exception as exc:  # noqa: BLE001 —— 测试里只看有没有结果
        _box["e"] = exc


_th = _threading.Thread(target=_call_get_config, daemon=True)
_th.start()
_th.join(3)
check("v2.0.4.1 api_get_config 3 秒内返回（不再死锁）", "r" in _box, repr(_box.get("e", "")))
check("v2.0.4.1 api_get_config 返回 password_status", _box.get("r", {}).get("password_status") in ("set", "missing"))

# ---- v2.0.6.3：升级接口不再「假成功」（真机验证 v2.0.6.2 时踩到）----
# 现象：先点「立即检查更新」、紧接着点「立即升级」，第二个请求被 _acquire_update_lock
# 挡掉，但 HTTP 早已回了 {"ok": true, "已提交升级任务"} —— 用户以为点了没反应。
# 断言：忙 → 409 + 真话（且不白启后台线程）；空闲 → 仍走原来的异步成功路径。
_au.UPDATE_LOCK = _threading.Lock()   # 平时由 _attach() 注入（同上面 _au.BASE_DIR 的处理）
_au.STATE = {}
_au.STATE_LOCK = _threading.Lock()
check("v2.0.6.3 空闲时不忙", _au.is_update_busy() is False)
check("v2.0.6.3 空闲时不报忙原因", _au.update_busy_message() == "")
_au._acquire_update_lock()
check("v2.0.6.3 任务在跑 → 忙", _au.is_update_busy() is True)
check("v2.0.6.3 忙原因取当前进度文案", "准备升级" in _au.update_busy_message())
_au._release_update_lock()
_au._set_update_state(update_state="error", update_progress_message="下载失败：xxx")
check("v2.0.6.3 锁一放就算空闲（状态残值不得把接口锁死）",
      _au.is_update_busy() is False and _au.update_busy_message() == "")


class _FakeUpdateMod(object):
    """替身：只验 Web 层「先探再回车」的顺序，不碰网络。"""

    def __init__(self, busy=""):
        self.busy = busy
        self.called = []
        self.evt = _threading.Event()

    def update_busy_message(self):
        return self.busy

    def _do_check_now(self):
        self.called.append("check")

    def _do_update_now(self):
        self.called.append("install")
        self.evt.set()

    def _log_upgrade(self, *a, **k):
        pass

    def _set_update_state(self, **k):
        pass


_saved_au_mod = getattr(_wa, "_auto_update_mod", None)
_wa._auto_update_mod = _FakeUpdateMod("检查 GitHub 最新版本...")
_st_c, _body_c = _wa.api_post_update_check({})
check("v2.0.6.3 忙时 check 回 409（不再假成功）",
      _st_c == 409 and _body_c.get("ok") is False and "任务进行中" in _body_c.get("error", ""),
      repr((_st_c, _body_c)))
_st_i, _body_i = _wa.api_post_update_install({})
check("v2.0.6.3 忙时 install 回 409 + 真话（带当前进度）",
      _st_i == 409 and _body_i.get("ok") is False
      and "检查 GitHub 最新版本" in _body_i.get("error", ""), repr((_st_i, _body_i)))
check("v2.0.6.3 忙时不启后台线程（没白跑一次检查/升级）",
      _wa._auto_update_mod.called == [])

_wa._auto_update_mod = _FakeUpdateMod("")
_st_i, _body_i = _wa.api_post_update_install({})
_ran = _wa._auto_update_mod.evt.wait(2)
check("v2.0.6.3 空闲时 install 仍回 200「已提交升级任务」并真的开跑",
      _st_i == 200 and _body_i.get("message") == "已提交升级任务"
      and _ran and _wa._auto_update_mod.called == ["install"], repr((_st_i, _body_i)))
if _saved_au_mod is None:
    del _wa._auto_update_mod
else:
    _wa._auto_update_mod = _saved_au_mod

# ---- v2.0.4.2：自动升级执行器（任务计划程序 + 看门狗 + 退出码）----
# 真机证据：installer 直启时 nssm 一停服务就把它连同 Job 一起杀掉 →
# installer-silent.log 都没生成、版本号不变、服务停在 StopPending。
check("v2.0.4.2 installer 由任务计划程序拉起", "schtasks" in src_upd and '"/create"' in src_upd)
check("v2.0.4.2 任务名单含 desktopicon",
      "/TASKS=desktopicon,startservice" in src_upd)   # 只传 startservice 会漏掉公共桌面图标
check("v2.0.4.2 包装脚本落盘 installer 退出码", "installer_rc=%ERRORLEVEL%" in src_upd)
check("v2.0.4.2 启动钩子读执行器结果", "_report_update_runner_result(" in src_upd)
check("v2.0.4.2 升级残留会被清理", "_cleanup_update_leftovers()" in src_upd)
check("v2.0.4.2 不再用备份 hash 误判升级成功",
      "升级完成（v{}）：当前脚本与备份不同" not in src_upd)
# 维护第二轮（审计 E2）：读取函数加了 subvalue 参数（默认仍是 (默认) 值），
# I7 要求 Default 与 "0" 两个子值都读 —— 断言跟着改到新签名，语义不变。
check("v2.0.4.2 AppExit 按 nssm 子键结构读取",
      "APPEXIT_SUBKEY" in src_upd
      and "def _read_nssm_appexit(subvalue=APPEXIT_DEFAULT_VALUE)" in src_upd
      and "QueryValueEx(k, subvalue)" in src_upd)
check("v2.0.4.2 nssm set AppExit 拆成两个 argv",
      '"AppExit", subparam.strip(), value.strip()' in src_upd)

# ---- v2.0.4.3：winreg 路径前缀 / 清理时序 / 日志归档 ----
# 真机证据：AppExit 子键明明是 (默认)=Ignore / 0=Ignore，日志却每次开机都报"自愈失败"
# → 根因是 winreg.OpenKey 收到了带 "HKLM\\" 前缀的路径，异常被 except 吞掉。
check("v2.0.4.3 winreg 路径去掉 HKLM\\ 前缀",
      "def _hklm_subpath" in src_upd and "_hklm_subpath(APPEXIT_SUBKEY)" in src_upd)
check("v2.0.4.3 清理只针对陈旧残留（不删正在跑的执行器）",
      "UPDATE_LEFTOVER_STALE_SEC" in src_upd and "stale_before" in src_upd)
check("v2.0.4.3 执行器结果读取带宽限",
      "_report_update_runner_result(wait_sec=8)" in src_upd)
check("v2.0.4.3 安装日志归档回安装目录",
      'os.path.join(LOG_DIR, "installer-silent.log")' in src_upd)

# 行为级：包装脚本内容必须同时具备「装 + 落退出码 + 看门狗 + 自删」
au = importlib.import_module("auto_update")
_wrap = au._build_update_wrapper(
    r"C:\T\DrcomAutoLogin-Setup-v9.9.9.exe", r"D:\App", r"C:\T\l.log", r"C:\T\r.txt", "MyTask")
check("v2.0.4.2 包装脚本四要素齐全",
      all(s in _wrap for s in ("/VERYSILENT", "TASKS=desktopicon,startservice", "installer_rc=",
                               "sc.exe\" start", "schtasks.exe\" /delete", "del /f /q \"%~f0\"")))
check("v2.0.4.2 包装脚本 CRLF 行尾", _wrap.endswith("\r\n") and "\r\n" in _wrap)
check("v2.0.4.2 包装脚本带看门狗（服务没起来就 sc start）",
      (" start %s" % au.SERVICE_NAME) in _wrap and "service=RUNNING" in _wrap)
check("v2.0.4.2 包装脚本不外泄密码/凭据", "password" not in _wrap.lower() and "PWD" not in _wrap)
check("v2.0.4.3 _hklm_subpath 去前缀且幂等",
      au._hklm_subpath(r"HKLM\SYSTEM\X") == r"SYSTEM\X"
      and au._hklm_subpath(r"SYSTEM\X") == r"SYSTEM\X"
      and au._hklm_subpath("hklm\\SYSTEM\\X") == "SYSTEM\\X")
check("v2.0.4.3 AppExit 注册表路径可用于 winreg",
      au._hklm_subpath(au.APPEXIT_SUBKEY).upper().startswith("SYSTEM\\")
      and "Parameters\\AppExit" in au._hklm_subpath(au.APPEXIT_SUBKEY))
# ---- v2.0.10.0：升级看门狗改探 HTTP /api/health（「wrapper 活着」≠「服务真的活了」）----
# 真机证据：v2.0.8.0（安装器回滚）与 v2.0.9.0（服务启动即崩）两次都是「升完才发现」，
# 因为收尾判定只看 `sc query` —— 那是 nssm 的 wrapper，而 AppExit=Ignore 下里面的
# Python 进程崩了 wrapper 照样 RUNNING（既不重启也不报错）。
import subprocess  # noqa: E402  （本文件后段才 import 它，这里必须先用上）
import threading  # noqa: E402
import time  # noqa: E402
from http.server import BaseHTTPRequestHandler as _BaseHTTPRequestHandler  # noqa: E402
from http.server import ThreadingHTTPServer as _ThreadingHTTPServer  # noqa: E402


class _HealthStub(_BaseHTTPRequestHandler):
    """只回 /api/health 的假服务（模拟真服务的健康端点，供行为级断言使用）。"""

    def do_GET(self):  # noqa: N802
        body = b'{"ok": true, "version": "0.0.0-stub"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音（否则每个请求都往 stderr 刷一行）
        pass


check("v2.0.10.0 健康探测端点就是 /api/health", au.HEALTH_PROBE_PATH == "/api/health")
check("v2.0.10.0 启动钩子读执行器的 health= 结论",
      'l.startswith("health=")' in src_upd and "HTTP 健康探测" in src_upd)
check("v2.0.10.0 探针用随包内嵌 python（不依赖 PATH / 用户装没装 Python）",
      au.EMBEDDED_PYTHON_REL == os.path.join("python", "python.exe"))
check("v2.0.10.0 启动钩子绝不等 health=（否则与探针互相等成死锁）",
      "绝不等" in src_upd or "deadlock" in src_upd)
check("v2.0.10.0 非法 ui_port 退回 8848",
      au._normalize_ui_port(None) == 8848 and au._normalize_ui_port("x") == 8848
      and au._normalize_ui_port(70000) == 8848 and au._normalize_ui_port(18899) == 18899
      and au._normalize_ui_port(True) == 8848,
      repr([au._normalize_ui_port(v) for v in (None, "x", 70000, 18899, True)]))

# 执行器（.cmd）：探针接线 + 失败重试 + 缺件退回旧判据 + 收尾删探针
_wrap_h = au._build_update_wrapper(
    r"C:\T\DrcomAutoLogin-Setup-v9.9.9.exe", r"D:\App", r"C:\T\l.log", r"C:\T\r.txt", "MyTask",
    python_path=r"C:\App\python\python.exe", probe_path=r"C:\T\drcom_health_probe.py", ui_port=18899)
check("v2.0.10.0 执行器调探针（内嵌 python + 端口 + rc）",
      r'"C:\App\python\python.exe" "C:\T\drcom_health_probe.py" --port 18899 --rc "%RC%" --tag first'
      in _wrap_h)
check("v2.0.10.0 探测失败先「停 + 起」重启，再给一次窗口",
      ("stop %s" % au.SERVICE_NAME) in _wrap_h and ("start %s" % au.SERVICE_NAME) in _wrap_h
      and "--tag retry" in _wrap_h and str(au.HEALTH_PROBE_RETRY_WAIT_SEC) in _wrap_h)
check("v2.0.10.0 探针 / 内嵌 python 缺失时退回旧判据（health=SKIP）",
      _wrap_h.count("health=SKIP") == 2)
check("v2.0.10.0 执行器收尾删探针", 'del /f /q "C:\\T\\drcom_health_probe.py"' in _wrap_h)
check("v2.0.10.0 rc 里仍写 service=（既有格式不变）", "service=RUNNING" in _wrap_h)
check("v2.0.10.0 执行器仍不外泄凭据", "password" not in _wrap_h.lower() and "PWD" not in _wrap_h)

# v2.0.10.0 修的老坑：`echo installer_rc=0>"%RC%"` 会被 cmd 当成「**句柄 0** 重定向」
# （数字紧贴 `>`）→ 落盘的是**空文件**、文本跑进 stdout，于是 installer_rc= 从来没写进去过
# （「非 0 退出码」告警因此一直是死代码）。现在把重定向写到命令**前面**。
check("v2.0.10.0 rc 写入用「重定向前置」写法",
      '>>"%RC%" echo installer_rc=%ERRORLEVEL%' in _wrap_h
      and 'echo installer_rc=%ERRORLEVEL%>"%RC%"' not in _wrap_h)
if os.name != "nt":
    print("SKIP  v2.0.10.0 rc 重定向前置行为级断言：非 Windows（没有 cmd）")
else:
    _rdir = tempfile.mkdtemp(prefix="drcom_redir_")
    _rfile = os.path.join(_rdir, "r.txt")
    _rcmd = os.path.join(_rdir, "redir.cmd")
    with open(_rcmd, "w", encoding="utf-8", newline="") as _f:
        # 注意 newline=""（审计 G5 更正措辞）：Windows 文本模式默认会把字符串里的 CRLF
        # 再转换一次、生成 CR CR LF。cmd 实测**能容忍**这种行尾（不是「整行失效」），
        # 但生成物应与预期一致，所以生成批处理/执行器时一律 newline=""。
        _f.write('@echo off\r\nset "RC=' + _rfile + '"\r\n>>"%RC%" echo installer_rc=0\r\n')
    subprocess.run(["cmd", "/c", _rcmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    _rtxt = pathlib.Path(_rfile).read_text(encoding="utf-8", errors="replace") if os.path.isfile(_rfile) else ""
    check("v2.0.10.0 rc 重定向前置真的落盘（installer_rc=0）", _rtxt.strip() == "installer_rc=0", repr(_rtxt))

# 行为级：把生成的探针**真跑起来** —— 对着在跑的服务 → OK；对着空端口 → FAIL
_pdir = tempfile.mkdtemp(prefix="drcom_probe_")
_pport = 18931
_pprobe = os.path.join(_pdir, "drcom_health_probe.py")
_prc_ok = os.path.join(_pdir, "rc_ok.txt")
_plog_ok = os.path.join(_pdir, "upgrade_ok.log")
_pprobe_txt = au._build_health_probe_script(_prc_ok, _plog_ok, _pport,
                                            wait_sec=6, interval_sec=0.5, timeout_sec=2)
pathlib.Path(_pprobe).write_text(_pprobe_txt, encoding="utf-8")
_psrv = _ThreadingHTTPServer(("127.0.0.1", _pport), _HealthStub)
threading.Thread(target=_psrv.serve_forever, daemon=True).start()
_po = subprocess.run([sys.executable, _pprobe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
_prc_txt = pathlib.Path(_prc_ok).read_text(encoding="utf-8") if os.path.isfile(_prc_ok) else ""
_plog_txt = pathlib.Path(_plog_ok).read_text(encoding="utf-8") if os.path.isfile(_plog_ok) else ""
check("v2.0.10.0 探针：服务在 → 退出码 0 / rc 落 health=OK",
      _po.returncode == 0 and _prc_txt.strip() == "health=OK",
      repr((_po.returncode, _prc_txt, _po.stdout.decode("utf-8", "replace").strip())))
check("v2.0.10.0 探针：把「通过」写进 upgrade.log（人读）",
      "[INFO]" in _plog_txt and "HTTP 健康检查通过" in _plog_txt, repr(_plog_txt[:120]))
check("v2.0.10.0 探针不 import 任何项目模块（要比被探的服务更耐活）",
      "import auto_update" not in _pprobe_txt and "联网_service" not in _pprobe_txt
      and "urllib.request.ProxyHandler({})" in _pprobe_txt)  # 顺带禁用系统代理

_prc_bad = os.path.join(_pdir, "rc_bad.txt")
_plog_bad = os.path.join(_pdir, "upgrade_bad.log")
_t0 = time.time()
_pb = subprocess.run([sys.executable, _pprobe, "--port", "18932", "--rc", _prc_bad, "--log", _plog_bad,
                      "--tag", "retry", "--wait", "2", "--interval", "0.3"],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
_prc_bad_txt = pathlib.Path(_prc_bad).read_text(encoding="utf-8") if os.path.isfile(_prc_bad) else ""
_plog_bad_txt = pathlib.Path(_plog_bad).read_text(encoding="utf-8") if os.path.isfile(_plog_bad) else ""
check("v2.0.10.0 探针：服务不在 → 退出码 1 / rc 落 health=FAIL",
      _pb.returncode == 1 and _prc_bad_txt.strip() == "health=FAIL",
      repr((_pb.returncode, _prc_bad_txt)))
check("v2.0.10.0 探针：失败写中文告警（服务起不来时唯一的告警面）",
      "[ERROR]" in _plog_bad_txt and "HTTP 健康检查未通过" in _plog_bad_txt
      and "service_stderr.log" in _plog_bad_txt, repr(_plog_bad_txt[:160]))
check("v2.0.10.0 探针遵守 --wait 窗口（不无限等）", (time.time() - _t0) < 30,
      "%.1fs" % (time.time() - _t0))
_psrv.shutdown()

# 行为级：启动钩子解析 rc 里的 health=（多行时以**最后一条**为准 —— 先 FAIL 后 OK = 重启后自己好了）
import logging  # noqa: E402

_rcbox = tempfile.mkdtemp(prefix="drcom_rc_")
_saved_g = {k: au.__dict__.get(k) for k in ("LOG_DIR", "UPGRADE_LOG_FILE", "logger")}
_saved_temp = os.environ.get("TEMP")
_au_logger = logging.getLogger("smoke_auto_update")
_au_logger.addHandler(logging.NullHandler())
au.LOG_DIR = _rcbox
au.UPGRADE_LOG_FILE = os.path.join(_rcbox, "upgrade.log")
au.logger = _au_logger
os.environ["TEMP"] = _rcbox  # _rc_path() / _wrapper_path() 都取 TEMP


def _runner_result(rc_lines):
    """把合成 rc 写进 %TEMP%，跑一次启动钩子的收尾读取，返回 upgrade.log 全文。"""
    with open(au._rc_path(), "w", encoding="utf-8") as f:
        f.write("\n".join(rc_lines) + "\n")
    if os.path.isfile(au.UPGRADE_LOG_FILE):
        os.remove(au.UPGRADE_LOG_FILE)
    au._report_update_runner_result(wait_sec=0)
    return pathlib.Path(au.UPGRADE_LOG_FILE).read_text(encoding="utf-8")


_rc_fail = _runner_result(["installer_rc=0", "service=RUNNING", "health=FAIL"])
check("v2.0.10.0 收尾记录：health=FAIL → WARN + 中文原因",
      "[WARN]" in _rc_fail and "HTTP 健康探测未通过" in _rc_fail, repr(_rc_fail[-140:]))
_rc_ok2 = _runner_result(["installer_rc=0", "service=RUNNING", "health=FAIL", "health=OK"])
check("v2.0.10.0 收尾记录：先 FAIL 后 OK → 按最后一条判（重启后自己好了）",
      "[INFO]" in _rc_ok2 and "HTTP 健康探测通过" in _rc_ok2 and "未通过" not in _rc_ok2,
      repr(_rc_ok2[-140:]))
_rc_skip = _runner_result(["installer_rc=0", "service=STOPPED", "health=SKIP"])
check("v2.0.10.0 收尾记录：health=SKIP → 说明没做探测（退回旧判据）",
      "没做 HTTP 健康探测" in _rc_skip, repr(_rc_skip[-140:]))
_rc_old = _runner_result(["installer_rc=0", "service=RUNNING"])
check("v2.0.10.0 收尾记录：旧执行器没有 health= 行也不炸",
      "升级执行器结果" in _rc_old and "HTTP 健康探测" not in _rc_old, repr(_rc_old[-140:]))

for _k, _v in _saved_g.items():
    if _v is None:
        au.__dict__.pop(_k, None)
    else:
        au.__dict__[_k] = _v
if _saved_temp is None:
    os.environ.pop("TEMP", None)
else:
    os.environ["TEMP"] = _saved_temp

# ---- v2.0.11.0：备份改整目录 + 自动/手动回滚（P6-6 / T4.4）----
# 背景：v2.0.10.0 的看门狗只解决「发现」；这一版给「回不去」补退路 ——
# 旧实现只备份 联网_service.py 一个文件（v2.0.2 拆 9 个模块后不够）+ 放 %TEMP%（会被清理）。
import json  # noqa: E402  （本文件后段才 import 它，这里先用上）
import shutil  # noqa: E402

check("v2.0.11.0 回滚白名单 = 随包分发的 .py 清单（双向一致）",
      _iss_py_sources | {"联网_service.py"} == set(au.ROLLBACK_MODULES)
      and 'Source: "..\\联网_service.py"' in _iss_src,
      "白名单:%s vs 打包:%s" % (sorted(au.ROLLBACK_MODULES), sorted(_iss_py_sources | {"联网_service.py"})))
check("v2.0.11.0 备份目录名走白名单（防 `..\\` 穿越）",
      au.ROLLBACK_VERSION_RE.match("2.0.10.0") is not None
      and au.ROLLBACK_VERSION_RE.match("..\\evil") is None
      and au.ROLLBACK_VERSION_RE.match("") is None
      and au._backup_dir_for("../evil") is None)

_appbox = tempfile.mkdtemp(prefix="drcom_app_")
_logbox2 = tempfile.mkdtemp(prefix="drcom_logs_")
_rb_state = {}
_saved_g2 = {k: au.__dict__.get(k) for k in ("LOG_DIR", "UPGRADE_LOG_FILE", "logger", "BASE_DIR")}


def _au_attach(base, logdir):
    """把 auto_update 的注入点指到沙箱（等价于服务 main() 里那次 _attach）。"""
    au._attach(
        logger=_au_logger, state=_rb_state, state_lock=threading.Lock(), update_lock=threading.Lock(),
        tools_dir=os.path.join(base, "tools"), nssm_path=os.path.join(base, "tools", "nssm.exe"),
        upgrade_log_file=os.path.join(logdir, "upgrade.log"), log_dir=logdir, base_dir=base,
        load_config=lambda: {}, save_config=lambda *a: None,
        now_iso=lambda: "2026-09-28T00:00:00", stop_event=threading.Event())


_py_names = [n for n in au.ROLLBACK_MODULES if n not in ("metrics.py", "profiles.py")]
for _n in _py_names:
    pathlib.Path(os.path.join(_appbox, _n)).write_text("# fake %s\n" % _n, encoding="utf-8")
pathlib.Path(os.path.join(_appbox, "config.json")).write_text('{"account": "fake-acct"}', encoding="utf-8")
_au_attach(_appbox, _logbox2)
# metrics.py / profiles.py 故意不建 = 模拟「可选模块没装上」
_bk = au._backup_modules(version="1.2.3", note="冒烟")
_man = {}
if _bk and os.path.isfile(os.path.join(_bk, au.BACKUP_MANIFEST_NAME)):
    with open(os.path.join(_bk, au.BACKUP_MANIFEST_NAME), encoding="utf-8") as _f:
        _man = json.load(_f)
check("v2.0.11.0 备份落到 {app}\\backup\\<版本>\\",
      bool(_bk) and os.path.normcase(_bk) == os.path.normcase(au._backup_dir_for("1.2.3")), repr(_bk))
check("v2.0.11.0 清单逐个记 sha256（能校验备份有没有被改）",
      all(_man.get("files", {}).get(n) == au._sha256_hex(os.path.join(_bk, n)) for n in _py_names),
      repr(sorted(_man.get("files", {}))))
check("v2.0.11.0 config.json 只作人工参考、不进回滚清单",
      "config.json" in _man.get("files", {}) and "config.json" not in _man.get("rollback_files", []))
check("v2.0.11.0 可选模块缺失不算失败（也不进清单）",
      "metrics.py" not in _man.get("files", {}) and "metrics.py" not in _man.get("rollback_files", []))
check("v2.0.11.0 备份失败就不升级（宁可这次不升，也不能在回不去的状态换代码）",
      '_backup_modules(version=VERSION, note="升级到 {}".format(remote_ver))' in src_upd
      and "已中止升级" in src_upd)

# 行为级 A：把生成的回滚脚本**真跑起来**（正常 / 备份被改坏 / 拿错版本 / 清单缺失 / 非法文件名）
_rbdir = tempfile.mkdtemp(prefix="drcom_rb_")
_saved_temp2 = os.environ.get("TEMP")
os.environ["TEMP"] = _rbdir                    # 让 _run_rollback 的临时文件也落沙箱
_scr = os.path.join(_rbdir, "drcom_rollback.py")
_rb_rc = os.path.join(_rbdir, "rollback.rc")
_rb_log = os.path.join(_logbox2, "upgrade.log")
_rb_src = au._build_rollback_script(_bk, _appbox, _rb_rc, _rb_log)
pathlib.Path(_scr).write_text(_rb_src, encoding="utf-8")
check("v2.0.11.0 回滚脚本：占位符全填好 / 不 import 项目模块 / 不含凭据",
      "__RB_" not in _rb_src and "import auto_update" not in _rb_src
      and "password" not in _rb_src.lower() and "联网" not in _rb_src)


def _break_app(tag):
    for _n in _py_names:
        pathlib.Path(os.path.join(_appbox, _n)).write_text("# %s\n" % tag, encoding="utf-8")


def _run_rb(*args):
    # PYTHONIOENCODING=utf-8：子进程 stdout 走管道时默认按控制台代码页（GBK）编码 ✗，
    # 断言里按 utf-8 解码就会全是乱码 —— 固定子进程编码，让断言可判定 ✓
    _env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, _scr, "--rc", _rb_rc, "--log", _rb_log] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, env=_env)


def _app_now():
    return {n: pathlib.Path(os.path.join(_appbox, n)).read_text(encoding="utf-8") for n in _py_names}


_break_app("BROKEN")
_r1 = _run_rb("--tag", "auto", "--expect", "1.2.3")
_r1_out = _r1.stdout.decode("utf-8", "replace")
_r1_rc = pathlib.Path(_rb_rc).read_text(encoding="utf-8") if os.path.isfile(_rb_rc) else ""
_r1_log = pathlib.Path(_rb_log).read_text(encoding="utf-8") if os.path.isfile(_rb_log) else ""
check("v2.0.11.0 回滚：文件真还原（内容逐一相等）+ 退出码 0",
      _r1.returncode == 0 and all(_app_now()[n] == "# fake %s\n" % n for n in _py_names), _r1_out[:140])
check("v2.0.11.0 回滚：rc 落 rollback=OK + rollback_version",
      "rollback=OK" in _r1_rc and "rollback_version=1.2.3" in _r1_rc, repr(_r1_rc))
check("v2.0.11.0 回滚：upgrade.log 留中文结论（并声明配置与密码未改动）",
      "版本回滚" in _r1_log and "配置与密码未改动" in _r1_log)
check("v2.0.11.0 回滚：绝不碰 config.json",
      pathlib.Path(os.path.join(_appbox, "config.json")).read_text(encoding="utf-8") == '{"account": "fake-acct"}')

# 备份被改坏（复制一份出来改）→ fail-closed：拒绝还原，app 里一个文件都不许动
_bk_bad = au._backup_dir_for("1.4.4")
shutil.copytree(_bk, _bk_bad)
pathlib.Path(os.path.join(_bk_bad, "version.py")).write_text("# TAMPERED\n", encoding="utf-8")
_break_app("KEEP")
_r2 = _run_rb("--backup", _bk_bad, "--tag", "auto", "--expect", "1.4.4")
_r2_rc = pathlib.Path(_rb_rc).read_text(encoding="utf-8") if os.path.isfile(_rb_rc) else ""
check("v2.0.11.0 回滚：备份被改坏就拒绝（退出码 1 + 末行 rollback=FAIL）",
      _r2.returncode == 1 and _r2_rc.strip().splitlines()[-1] == "rollback=FAIL", repr(_r2_rc))
check("v2.0.11.0 回滚：拒绝时 all-or-nothing（app 文件原样未动）",
      all(_app_now()[n] == "# KEEP\n" for n in _py_names))

_r3 = _run_rb("--backup", _bk, "--expect", "9.9.9")
check("v2.0.11.0 回滚：--expect 与备份版本不符 → 拒绝（防拿错备份）",
      _r3.returncode == 1 and "但期望" in _r3.stdout.decode("utf-8", "replace"),
      repr(_r3.stdout.decode("utf-8", "replace")[:160]))

_bk_no_man = au._backup_dir_for("2.2.2")
os.makedirs(_bk_no_man, exist_ok=True)
_r4 = _run_rb("--backup", _bk_no_man)
check("v2.0.11.0 回滚：清单缺失 → 拒绝",
      _r4.returncode == 1 and "读不了备份清单" in _r4.stdout.decode("utf-8", "replace"),
      repr(_r4.stdout.decode("utf-8", "replace")[:160]))

_bk_evil = au._backup_dir_for("3.3.3")
os.makedirs(_bk_evil, exist_ok=True)
with open(os.path.join(_bk_evil, au.BACKUP_MANIFEST_NAME), "w", encoding="utf-8") as _f:
    json.dump({"version": "3.3.3", "files": {"..\\evil.py": "x"}, "rollback_files": ["..\\evil.py"]}, _f)
_r5 = _run_rb("--backup", _bk_evil)
check("v2.0.11.0 回滚：清单里带路径分隔符的文件名 → 拒绝（不许写到 app 外）",
      _r5.returncode == 1 and "非法文件名" in _r5.stdout.decode("utf-8", "replace")
      and not os.path.isfile(os.path.join(os.path.dirname(_appbox), "evil.py")),
      repr(_r5.stdout.decode("utf-8", "replace")[:160]))

# 行为级 B：接口层（GET 列表 / 忙时 409 / 非法与不存在的版本 / 默认取最新并真还原）
_st_rb, _b_rb = au.api_get_rollback()
check("v2.0.11.0 GET /api/rollback：列表 + 保留策略 + 不把本机绝对路径抛给前端",
      _st_rb == 200 and _b_rb.get("ok") and _b_rb.get("current_version") == au.VERSION
      and any(x["version"] == "1.2.3" for x in _b_rb.get("backups", []))
      and _b_rb.get("retention_days") == au.BACKUP_RETENTION_DAYS
      and all("path" not in x for x in _b_rb.get("backups", [])), repr(_b_rb)[:170])
_rb_state["update_lock"] = True
_st_busy, _b_busy = au.api_post_rollback({"version": "1.2.3"})
_rb_state["update_lock"] = False
check("v2.0.11.0 POST /api/rollback：忙（正在升级）→ 409，不硬来",
      _st_busy == 409 and _b_busy.get("ok") is False and "任务进行中" in _b_busy.get("error", ""), repr(_b_busy))
_st_ev, _b_ev = au.api_post_rollback({"version": "../evil"})
check("v2.0.11.0 POST /api/rollback：版本串非法 → 400", _st_ev == 400 and "非法" in _b_ev.get("error", ""))
_st_nf, _b_nf = au.api_post_rollback({"version": "9.9.9"})
check("v2.0.11.0 POST /api/rollback：找不到该版本 → 404", _st_nf == 404 and "找不到" in _b_nf.get("error", ""))
_saved_ver2 = au.VERSION
au.VERSION = "1.2.3"
_st_cur, _b_cur = au.api_post_rollback({"version": "1.2.3"})
au.VERSION = _saved_ver2
check("v2.0.11.0 POST /api/rollback：目标就是当前版本 → 400（不白折腾）",
      _st_cur == 400 and "无需回滚" in _b_cur.get("error", ""))
_break_app("BROKEN2")
au._write_update_attempt("9.9.9", count=1, state="attempted")
_st_ok, _b_ok = au.api_post_rollback({})
check("v2.0.11.0 POST /api/rollback：默认取最新备份并真还原（与自动回滚同一份脚本）",
      _st_ok == 200 and _b_ok.get("ok") and _b_ok.get("version") == "1.2.3"
      and all(_app_now()[n] == "# fake %s\n" % n for n in _py_names), repr(_b_ok)[:170])
check("v2.0.11.0 回滚成功即清「升级尝试记录」（重启后旧代码不再误报「升级未生效」）",
      "_clear_update_attempt()" in src_upd
      and not os.path.isfile(os.path.join(_logbox2, "update_attempt.json")))

# 执行器（.cmd）：自动回滚接线 + 顺序 + 缺件降级
_wrap_rb = au._build_update_wrapper(
    r"C:\T\DrcomAutoLogin-Setup-v9.9.9.exe", r"D:\App", r"C:\T\l.log", r"C:\T\r.txt", "MyTask",
    python_path=r"C:\App\python\python.exe", probe_path=r"C:\T\drcom_health_probe.py", ui_port=8848,
    backup_dir=r"D:\App\backup\1.2.3", rollback_path=r"C:\T\drcom_rollback.py")
check("v2.0.11.0 执行器：两轮不健康 → 停服务 → 回滚 → 起服务 → 再探（顺序钉死）",
      "--tag auto" in _wrap_rb and "--expect 1.2.3" in _wrap_rb and "--tag after-rollback" in _wrap_rb
      and _wrap_rb.index("--tag retry") < _wrap_rb.index("--tag auto") < _wrap_rb.index("--tag after-rollback"))
# 维护第二轮（审计 A2）：执行器新增「端口被占 → 跳过回滚」分支，也落一条 rollback=SKIP，
# 所以这里从 3 处变 4 处（缺件三处 + 端口占用一处）。
check("v2.0.11.0 执行器：缺件（python / 回滚脚本 / 备份）落 rollback=SKIP",
      _wrap_rb.count("echo rollback=SKIP") == 4, str(_wrap_rb.count("echo rollback=SKIP")))
check("v2.0.11.0 执行器：收尾把回滚脚本也删掉", 'del /f /q "C:\\T\\drcom_rollback.py"' in _wrap_rb)
check("v2.0.11.0 执行器：回滚路径同样不外泄凭据",
      "password" not in _wrap_rb.lower() and "PWD" not in _wrap_rb)
check("v2.0.11.0 启动钩子回报回滚结论（OK / FAIL / SKIP 三种都有话说）",
      all(s in src_upd for s in ('rollback == "OK"', 'rollback == "FAIL"', 'rollback == "SKIP"')))

# 行为级 C：启动钩子不再被「%TEMP% 里没有旧式备份」卡住
# （这正是「备份从 %TEMP% 挪到整目录」会踩的坑：迁移后钩子会整段静默失效 ✗）
_au_restore = {k: au.__dict__.get(k) for k in
               ("_cleanup_update_leftovers", "_ensure_nssm_appexit_sane", "_ensure_tray_autostart_sane")}
au._cleanup_update_leftovers = lambda: None
au._ensure_nssm_appexit_sane = lambda: "Ignore"
au._ensure_tray_autostart_sane = lambda: {"action": "keep", "want": True, "current": "x"}
os.environ["TEMP"] = tempfile.mkdtemp(prefix="drcom_empty_")   # 空目录 = 没有旧式备份
if os.path.isfile(au.UPGRADE_LOG_FILE):
    os.remove(au.UPGRADE_LOG_FILE)
au._post_upgrade_startup()
_hook_log = pathlib.Path(au.UPGRADE_LOG_FILE).read_text(encoding="utf-8") if os.path.isfile(au.UPGRADE_LOG_FILE) else ""
check("v2.0.11.0 启动钩子：没有旧式备份也照常跑完（回滚清单 / AppExit / 托盘自启）",
      "可用回滚备份" in _hook_log and "AppExit 策略由安装器维持" in _hook_log
      and "托盘自启项" in _hook_log, repr(_hook_log[-160:]))
for _k, _v in _au_restore.items():
    au.__dict__[_k] = _v

# 沙箱收尾：还原注入点与 TEMP，别影响后面「版本一致性 / 隐私守卫」
for _k, _v in _saved_g2.items():
    if _v is None:
        au.__dict__.pop(_k, None)
    else:
        au.__dict__[_k] = _v
if _saved_temp2 is None:
    os.environ.pop("TEMP", None)
else:
    os.environ["TEMP"] = _saved_temp2

# ---- v2.0.12.0：日志轮转 + 一键诊断包 ----
# 背景：业务日志以前是 `logging.FileHandler` 无限追加（README 只能写「请手动清理」）；
#       出错时让用户自己翻 logs 目录、还容易把账号 / 密码截图发出去。
import io  # noqa: E402
import metrics as _metrics  # noqa: E402
import zipfile  # noqa: E402

_src_svc12 = pathlib.Path("联网_service.py").read_text(encoding="utf-8")
check("v2.0.12.0 业务日志改用 RotatingFileHandler（5 MB × 3）",
      "from logging.handlers import RotatingFileHandler" in _src_svc12
      and "maxBytes=LOG_ROTATE_MAX_BYTES" in _src_svc12
      and "LOG_ROTATE_MAX_BYTES = 5 * 1024 * 1024" in _src_svc12
      and "LOG_ROTATE_BACKUPS = 3" in _src_svc12)
check("v2.0.12.0 升级日志也轮转（2 MB × 2）",
      "UPGRADE_LOG_MAX_BYTES = 2 * 1024 * 1024" in src_upd and "UPGRADE_LOG_BACKUPS = 2" in src_upd
      and "def _rotate_upgrade_log" in src_upd and "_rotate_upgrade_log()" in src_upd)

# 行为级：统计必须把归档一起按「由老到新」读 —— 否则轮转一发生，近 7 天统计就断档
_mlog = os.path.join(tempfile.mkdtemp(prefix="drcom_mlog_"), "campus_login.log")
pathlib.Path(_mlog + ".3").write_text("L3\n", encoding="utf-8")
pathlib.Path(_mlog + ".1").write_text("L1\n", encoding="utf-8")
pathlib.Path(_mlog).write_text("L0\n", encoding="utf-8")
_mlines = _metrics.collect_lines(_mlog)
check("v2.0.12.0 面板统计按 .3→.1→当前 顺序读归档（轮转不断档）",
      _mlines == ["L3", "L1", "L0"], repr(_mlines))

# 行为级：upgrade.log 超限轮转（把上限临时调小，不然真得写 2 MB）
_ulog_dir = tempfile.mkdtemp(prefix="drcom_ulog_")
_au_saved12 = {k: au.__dict__.get(k) for k in
               ("LOG_DIR", "UPGRADE_LOG_FILE", "UPGRADE_LOG_MAX_BYTES", "logger")}
au.LOG_DIR = _ulog_dir
au.UPGRADE_LOG_FILE = os.path.join(_ulog_dir, "upgrade.log")
au.UPGRADE_LOG_MAX_BYTES = 300          # 临时调小
au.logger = _au_logger
for _i in range(30):
    au._log_upgrade("INFO", "第 {} 行 {}".format(_i, "x" * 40))
_ulog_files = sorted(n for n in os.listdir(_ulog_dir) if n.startswith("upgrade.log"))
check("v2.0.12.0 upgrade.log 超限后滚成 .1 / .2（当前文件重新变小）",
      "upgrade.log.1" in _ulog_files and "upgrade.log.2" in _ulog_files
      and os.path.getsize(au.UPGRADE_LOG_FILE) < 300, repr(_ulog_files))
check("v2.0.12.0 只保留 2 份旧档（不会无限堆积）",
      not os.path.isfile(au.UPGRADE_LOG_FILE + ".3"), repr(_ulog_files))
for _k, _v in _au_saved12.items():
    if _v is None:
        au.__dict__.pop(_k, None)
    else:
        au.__dict__[_k] = _v

# 行为级：诊断包（组成 + 脱敏 + 不含密码）
_dlog_dir = tempfile.mkdtemp(prefix="drcom_diag_")
_FAKE_ACCT = "2023fake0001"
_FAKE_PWD = "#Fake-Pwd-9999#"
pathlib.Path(os.path.join(_dlog_dir, "campus_login.log")).write_text(
    "[t] [INFO] 账号 {} 登录成功，MAC E25B367B8DAC，网关 172.16.80.3，密码 {} 不该出现\n".format(
        _FAKE_ACCT, _FAKE_PWD), encoding="utf-8")
pathlib.Path(os.path.join(_dlog_dir, "upgrade.log")).write_text("升级流水\n", encoding="utf-8")
_wa_saved12 = {k: _wa.__dict__.get(k) for k in
               ("LOG_DIR", "logger", "_load_config", "_get_password", "_snapshot_state")}
_wa.LOG_DIR = _dlog_dir
_wa.logger = _au_logger
_wa._load_config = lambda: {"account": _FAKE_ACCT, "host": "172.16.80.3", "ui_port": 8848}
_wa._get_password = lambda: _FAKE_PWD
_wa._snapshot_state = lambda: {"service_started_at": "2026-09-28T10:31:28", "update_state": None}
_diag = _wa._build_diagnostics_zip()
with zipfile.ZipFile(io.BytesIO(_diag)) as _zf:
    _dnames = set(_zf.namelist())
    _dsummary = _zf.read("summary.json").decode("utf-8")
    _dcfg = _zf.read("config.json").decode("utf-8")
    _dreadme = _zf.read("README.txt").decode("utf-8")
    _dlog = _zf.read("logs/campus_login.log").decode("utf-8")
check("v2.0.12.0 诊断包组成（README + 摘要 + 配置 + logs/）",
      {"README.txt", "summary.json", "config.json"} <= _dnames
      and "logs/campus_login.log" in _dnames, repr(sorted(_dnames)))
check("v2.0.12.0 诊断包不含 password.txt、不含密码明文（且日志里的密码被换成 ***）",
      not any("password" in n.lower() for n in _dnames)
      and _FAKE_PWD not in (_dsummary + _dcfg + _dreadme + _dlog) and "***" in _dlog)
check("v2.0.12.0 账号打码（配置与日志都打）",
      _FAKE_ACCT not in _dcfg and re.search(r"2023\*{4,}", _dcfg) is not None
      and _FAKE_ACCT not in _dlog and re.search(r"2023\*{4,}", _dlog) is not None)
check("v2.0.12.0 MAC 打码（前 4 位 + ********）",
      "E25B367B8DAC" not in _dlog and "E25B********" in _dlog)
check("v2.0.12.0 摘要带版本 / 环境 / 密码状态；内网 IP 保留（排障必需）",
      '"version"' in _dsummary and '"platform"' in _dsummary and '"has_password": true' in _dsummary
      and "172.16.80.3" in _dcfg)
check("v2.0.12.0 README 写清脱敏范围（账号 / MAC / 密码 + 内网 IP 保留）",
      "已脱敏" in _dreadme and "不包含" in _dreadme and "内网 IP" in _dreadme)
_st_logs12, _b_logs12 = _wa.api_get_logs()
check("v2.0.12.0 GET /api/logs 形状（逐个体积 + 合计 + 轮转提示）",
      _st_logs12 == 200 and _b_logs12.get("ok") and _b_logs12.get("total_size") > 0
      and {i["name"] for i in _b_logs12.get("items", [])} >= {"campus_login.log", "upgrade.log"}
      and all("size_h" in i and "rotate_hint" in i for i in _b_logs12.get("items", [])),
      repr(_b_logs12)[:150])
for _k, _v in _wa_saved12.items():
    if _v is None:
        _wa.__dict__.pop(_k, None)
    else:
        _wa.__dict__[_k] = _v
check("v2.0.12.0 前端接好（关于面板日志卡片 + 诊断包下载 + 两个路由）",
      'id="log-list"' in src_web and 'href="/api/diagnostics"' in src_web
      and 'path == "/api/diagnostics"' in src_web and 'path == "/api/logs"' in src_web
      and "api_get_diagnostics(self)" in src_web)

# ---- v2.0.13.0：Web 层加固（P3-6 / P3-7）----
# 三件事：① CSP 等安全响应头（页面全内联，所以能收得很紧）；② JSON 端点请求体上限；
# ③ 运行时单实例锁（AppMutex 只管安装器 GUI，服务自己一直没锁）。
import http.client  # noqa: E402

_p13 = 18967
_ph13 = "127.0.0.1:%d" % _p13
_srv13 = _ThreadingHTTPServer(("127.0.0.1", _p13), _wa._Handler)
threading.Thread(target=_srv13.serve_forever, daemon=True).start()


def _req13(method, path, headers=None, body=None):
    _c = http.client.HTTPConnection("127.0.0.1", _p13, timeout=15)
    _h = {"Host": _ph13}
    _h.update(headers or {})
    _c.request(method, path, body=body, headers=_h)
    _r = _c.getresponse()
    _hdrs = {k.lower(): v for k, v in _r.getheaders()}
    _data = _r.read()
    _c.close()
    return _r.status, _hdrs, _data


_st13, _h13, _b13 = _req13("GET", "/")
check("v2.0.13.0 页面带 CSP（default-src 'none' + frame-ancestors 'none'）",
      _st13 == 200 and "default-src 'none'" in _h13.get("content-security-policy", "")
      and "frame-ancestors 'none'" in _h13.get("content-security-policy", "")
      and "base-uri 'none'" in _h13.get("content-security-policy", ""),
      _h13.get("content-security-policy", "")[:120])
check("v2.0.13.0 页面带 nosniff / DENY / no-referrer / Permissions-Policy",
      _h13.get("x-content-type-options") == "nosniff" and _h13.get("x-frame-options") == "DENY"
      and _h13.get("referrer-policy") == "no-referrer"
      and "camera=()" in _h13.get("permissions-policy", ""))
_st13j, _h13j, _b13j = _req13("GET", "/api/health")
check("v2.0.13.0 JSON 响应同样带安全头（nosniff / DENY）",
      _st13j == 200 and _h13j.get("x-content-type-options") == "nosniff"
      and _h13j.get("x-frame-options") == "DENY")
check("v2.0.13.0 CSP 只放宽内联脚本 / 样式，其余全禁（无 CDN 依赖）",
      "script-src 'unsafe-inline'" in _h13.get("content-security-policy", "")
      and "connect-src 'self'" in _h13.get("content-security-policy", "")
      and "img-src 'self' data:" in _h13.get("content-security-policy", ""))
check("v2.0.13.0 JSON 端点请求体上限 = 1 MB",
      _wa.MAX_JSON_BODY_BYTES == 1024 * 1024 and "MAX_JSON_BODY_BYTES" in src_web)
_st413, _h413, _b413 = _req13("POST", "/api/login", headers={
    "Content-Type": "application/json", "X-Requested-With": "DrcomUI"},
    body=b"0" * (_wa.MAX_JSON_BODY_BYTES + 100))
_b413j = json.loads(_b413.decode("utf-8")) if _b413 else {}
check("v2.0.13.0 超大 JSON 请求体被拒 413（不再无条件读进来）",
      _st413 == 413 and _b413j.get("ok") is False and "过大" in _b413j.get("error", ""),
      repr((_st413, _b413j)))
check("v2.0.13.0 拒绝时显式关连接（未读数据不会把 413 冲成 RST）",
      _h413.get("connection") == "close" and "BODY_DRAIN_MAX_BYTES" in src_web)
_st13p, _h13p, _b13p = _req13("GET", "/api/health")
check("v2.0.13.0 拒收后服务照常可用（连接是干净关的）", _st13p == 200)
_srv13.shutdown()

check("v2.0.13.0 单实例锁用命名互斥体（Local\\ 名字，进程退出自动释放）",
      svc.SINGLETON_MUTEX_NAME.startswith("Local\\")
      and svc.ERROR_ALREADY_EXISTS == 183
      and "CreateMutexW" in src_svc)
check("v2.0.13.0 单实例锁对升级路径宽容（旧进程退出时最多等 20 秒）",
      svc.SINGLETON_WAIT_SEC == 20 and "等旧实例退出" in src_svc)
if os.name != "nt":
    print("SKIP  v2.0.13.0 单实例锁行为级断言：非 Windows")
else:
    _first13 = svc._acquire_singleton(wait_sec=0)
    _second13 = svc._acquire_singleton(wait_sec=0)   # 本进程已持有 → 必须被识破
    check("v2.0.13.0 单实例锁真的挡得住第二个实例（行为级）",
          _first13 is True and _second13 is False and svc._SINGLETON_HANDLE,
          repr((_first13, _second13)))
_main_src13 = src_svc[src_svc.index("def main():"):]
check("v2.0.13.0 单实例锁排在「真正干活」之前（早于载配置 / 起线程）",
      _main_src13.index("if not _acquire_singleton():") < _main_src13.index("cfg = _load_config()")
      and "本进程退出" in _main_src13 and "return 0" in _main_src13,
      "单实例检查必须紧跟在启动横幅之后")

# ---- v2.0.14.0：导入加固（P1-6）+ 空账号不再阻塞保存（P1-7）+ 周期线程兜底（P3-2）----
check("v2.0.14.0 导入上限覆盖「解压后」体积（zip 炸弹）",
      _wa.CONFIG_IMPORT_MAX_UNCOMPRESSED_BYTES == 8 * 1024 * 1024
      and _wa.CONFIG_IMPORT_MAX_MEMBER_BYTES == 4 * 1024 * 1024
      and "zf.infolist()" in src_web)
check("v2.0.14.0 导入异常类型补全（ValueError / TypeError / KeyError 也算参数不合法）",
      "ValueError, TypeError, KeyError) as exc" in src_web)
# v2.x 维护（审计 P1-2）：加密 zip / 未知压缩方法（RuntimeError / NotImplementedError）
# 与截断流（EOFError / zlib.error）也必须归到 400，且不再回显英文异常原文。
check("v2.x 维护 导入异常补收 RuntimeError / NotImplementedError / EOFError / zlib.error",
      "RuntimeError, NotImplementedError, EOFError, zlib.error" in src_web
      and "导入的 zip 无法解析" in src_web
      and 'import failed: {}".format(exc)' not in src_web)
check("v2.0.14.0 空账号合法（默认配置本身就能通过校验）",
      svc._validate_config(svc._default_config()) == []
      and svc._validate_config(dict(svc._default_config(), account="")) == []
      and any("account" in e for e in svc._validate_config(dict(svc._default_config(), account="12ab"))),
      repr((svc._validate_config(svc._default_config()),
            svc._validate_config(dict(svc._default_config(), account="12ab")))))

# 行为级：真发两个「坏 zip」给导入端点（都必须在读配置之前就被挡掉）
_p14 = 18968
_ph14 = "127.0.0.1:%d" % _p14
_srv14 = _ThreadingHTTPServer(("127.0.0.1", _p14), _wa._Handler)
threading.Thread(target=_srv14.serve_forever, daemon=True).start()


def _post_zip14(zip_bytes):
    _c = http.client.HTTPConnection("127.0.0.1", _p14, timeout=30)
    _c.request("POST", "/api/config/import", body=zip_bytes, headers={
        "Host": _ph14, "Content-Type": "application/zip", "X-Requested-With": "DrcomUI",
        "Content-Length": str(len(zip_bytes))})
    _r = _c.getresponse()
    _d = _r.read()
    _c.close()
    try:
        return _r.status, json.loads(_d.decode("utf-8"))
    except ValueError:
        return _r.status, {"_raw": _d[:100]}


def _mk_zip(members):
    _buf = io.BytesIO()
    with zipfile.ZipFile(_buf, "w", zipfile.ZIP_DEFLATED) as _z:
        for _n, _b in members.items():
            _z.writestr(_n, _b)
    return _buf.getvalue()


_good_manifest = json.dumps({"schema_version": 1, "tool": "smoke"})
_st_bomb, _b_bomb = _post_zip14(_mk_zip({
    "manifest.json": _good_manifest,
    "config.json": "{}",
    "b1.bin": b"\0" * (3 * 1024 * 1024),        # 三个各 3 MB（单成员都没超）
    "b2.bin": b"\0" * (3 * 1024 * 1024),
    "b3.bin": b"\0" * (3 * 1024 * 1024),        # 合计 9 MB > 8 MB = zip 炸弹
}))
check("v2.0.14.0 zip 炸弹被拦（413 + 中文原因，且在任何 read() 之前）",
      _st_bomb == 413 and "解压后总体积过大" in _b_bomb.get("error", ""), repr(_b_bomb))
_st_fat, _b_fat = _post_zip14(_mk_zip({
    "manifest.json": _good_manifest,
    "fat.bin": b"\0" * (5 * 1024 * 1024),       # 单成员超 4 MB
}))
check("v2.0.14.0 单成员过大也被拦（413）",
      _st_fat == 413 and "单个成员解压后过大" in _b_fat.get("error", ""), repr(_b_fat))
_st_sch, _b_sch = _post_zip14(_mk_zip({
    "manifest.json": json.dumps({"schema_version": "abc"}),   # 旧实现 int("abc") → 500 ✗
    "config.json": "{}",
}))
check("v2.0.14.0 schema_version 类型不对 → 400（不再 500）",
      _st_sch == 400 and "不是整数" in _b_sch.get("error", ""), repr(_b_sch))
_srv14.shutdown()

# 行为级：空账号时 run_once 必须**跳过登录**（不能拿空用户名去认证）
_proto = importlib.import_module("protocol")
_p_saved = {k: getattr(_proto, k, None) for k in
            ("_RUN_LOCK", "_STATE", "_STATE_LOCK", "_log", "_load_config", "get_current_ssid",
             "_auto_profile", "get_local_ips", "guard_allows", "_set_state", "wait_network",
             "is_online", "_get_password", "login", "_now_iso", "_backoff_until",
             "_set_backoff", "_reset_backoff")}
_p_seen = {}
_proto._RUN_LOCK = threading.Lock()
_proto._STATE = {}
_proto._STATE_LOCK = threading.Lock()
_proto._log = lambda *a, **k: None
_proto._load_config = lambda: {"host": "172.16.80.3", "port": 80, "account": "",
                               "suffix": "@yd", "auto_check_interval_min": 30,
                               "network_wait_timeout_sec": 10}
_proto.get_current_ssid = lambda: ""
_proto._auto_profile = None
_proto.get_local_ips = lambda: []
_proto.guard_allows = lambda cfg, ssid, ips: (True, "")
_proto._set_state = lambda **k: _p_seen.update(k)
_proto.wait_network = lambda *a, **k: True
_proto.is_online = lambda host: False
_proto._get_password = lambda: "pwd-whatever"
_proto.login = lambda *a, **k: _p_seen.setdefault("login_called", True) and (True, "x")
_proto._now_iso = lambda: "2026-09-28T11:30:00"
_proto._backoff_until = lambda: None
_proto._set_backoff = lambda *a, **k: None
_proto._reset_backoff = lambda *a, **k: None
_proto.run_once("manual")
for _k, _v in _p_saved.items():
    setattr(_proto, _k, _v)
check("v2.0.14.0 空账号：记「账号未设置」并跳过登录（不发认证请求）",
      _p_seen.get("last_error") == "账号未设置" and "login_called" not in _p_seen,
      repr({k: v for k, v in _p_seen.items() if k != "online"}))

# 行为级：周期自检线程遇到异常必须活下来（旧行为是线程直接死掉 ✗）
class _BoomLogger:
    def __init__(self):
        self.lines = []

    def exception(self, msg, *a):
        self.lines.append(msg % a if a else msg)

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


_boom_log = _BoomLogger()
_svc14_saved = {k: getattr(svc, k, None) for k in
                ("_load_config", "logger", "STOP_EVENT", "PERIODIC_ERROR_BACKOFF_SEC")}
_svc14_calls = []


def _boom_cfg():
    _svc14_calls.append(1)
    if len(_svc14_calls) == 1:
        raise RuntimeError("模拟配置读失败")       # 第一圈就炸
    svc.STOP_EVENT.set()                          # 第二圈让它正常退出
    return {"auto_check_enabled": False}


svc._load_config = _boom_cfg
svc.logger = _boom_log
svc.STOP_EVENT = threading.Event()
svc.PERIODIC_ERROR_BACKOFF_SEC = 0            # 测试里不等那 60 秒冷却
_th14 = threading.Thread(target=svc.run_periodic, daemon=True)
_th14.start()
_th14.join(5)
svc.STOP_EVENT.set()
for _k, _v in _svc14_saved.items():
    setattr(svc, _k, _v)
check("v2.0.14.0 周期自检异常后线程仍活着（不再静默死掉）",
      not _th14.is_alive() and len(_svc14_calls) >= 2 and any("周期自检循环异常" in l for l in _boom_log.lines),
      repr((_th14.is_alive(), len(_svc14_calls), _boom_log.lines[:1])))

# ---- v2.1.1.0：网络变化即触发（治「刚连上 Wi-Fi 要干等到下一个周期才登录」）----
# 老行为：run_periodic 一口气 wait(wait_sec)（最长 60 分钟），网络早就通了也不登 ✗
_w1 = svc.NetworkWatcher()
check("v2.1.1.0 网络监视：拿到地址才算「刚连上网」",
      _w1.observe_address("") == (False, "")
      and _w1.observe_address("172.16.59.11")[0] is True
      and _w1.observe_address("172.16.59.11") == (False, ""))
_w2 = svc.NetworkWatcher()
_w2.observe_address("10.0.0.5")
check("v2.1.1.0 网络监视：换网立刻检查，断网不折腾",
      _w2.observe_address("172.16.30.7")[0] is True
      and _w2.observe_address("") == (False, "")
      and _w2.observe_address("172.16.30.7")[0] is True)
_w3 = svc.NetworkWatcher()
check("v2.1.1.0 网络监视：Wi-Fi 名变了才算「换场景」",
      _w3.observe_ssid(None) == (False, "")
      and _w3.observe_ssid("Campus-WiFi") == (False, "")
      and _w3.observe_ssid("Dorm-WiFi")[0] is True
      and _w3.observe_ssid("Dorm-WiFi") == (False, "")
      and "Dorm-WiFi" in _w3.observe_ssid("Campus-WiFi")[1])
check("v2.1.1.0 主用地址探测：UDP connect 即刻返回（不发包 / 不起进程）",
      _proto.primary_local_ip("127.0.0.1", 80) == "127.0.0.1"
      and _proto.primary_local_ip("127.0.0.1") == "127.0.0.1")
check("v2.1.1.0 采样节拍：便宜通道秒级、贵通道明显更慢",
      1 <= svc.NETWATCH_TICK_SEC <= 5
      and svc.NETWATCH_SSID_EVERY_TICKS >= 2
      and svc.NETWATCH_TICK_SEC * svc.NETWATCH_SSID_EVERY_TICKS <= 120,
      repr((svc.NETWATCH_TICK_SEC, svc.NETWATCH_SSID_EVERY_TICKS)))
check("v2.1.1.0 状态里留了「为什么突然查了一次」",
      "last_net_change_at" in svc.STATE and "last_net_change_why" in svc.STATE)

# ---- v2.1.1.0：Wi-Fi 名清单（配置页「当前 SSID + 点一下就填」）----
check("v2.1.1.0 Wi-Fi 清单：`标签 : 名字` 取右半边（中英文 netsh 都能认）",
      _proto._parse_label_values(
          "配置文件信息\n-------------\n    所有用户配置文件 : Campus-WiFi\n"
          "    All User Profile     : Dorm-WiFi\n    所有用户配置文件 : Campus-WiFi\n"
      ) == ["Campus-WiFi", "Dorm-WiFi"])
check("v2.1.1.0 Wi-Fi 清单：空值 / 分隔线不混进来，且有上限",
      _proto._parse_label_values("   :   \n----\n: x\n a : b\n") == ["x", "b"]
      and _proto.MAX_SSID_LIST >= 10)
check("v2.1.1.0 /api/wifi 端点与前端挂钩都在位",
      "def api_get_wifi" in src_web
      and '"/api/wifi"' in src_web
      and "wifi: getJson" in src_web
      and "id=\"ssid-now\"" in src_web
      and "id=\"ssid-chips\"" in src_web
      and "function addSsidToGuard" in src_web)
check("v2.1.1.0 协议层提供 known_ssids / visible_ssids（读不到回空列表，不抛异常）",
      callable(getattr(_proto, "known_ssids", None))
      and callable(getattr(_proto, "visible_ssids", None))
      and isinstance(_proto.known_ssids(), list)
      and isinstance(_proto.visible_ssids(), list))

# ---- v2.1.1.0：多网络多账号（账号 + 后缀进方案；密码仍然不进配置）----
_mp_cfg = {"host": "172.16.80.3", "port": 80, "account": "2023000001", "suffix": "",
           "auto_check_interval_min": 30, "network_guard_enabled": False,
           "guard_allowed_ssids": "", "guard_allowed_subnets": "",
           "profiles": {}, "active_profile": "", "profiles_auto_switch": False}
_mp_profiles, _mp_err = _pf.upsert({}, "宿舍", _pf.snapshot(_mp_cfg))
check("v2.1.1.0 方案里现在**带**账号与后缀（多网络多账号的前提）",
      not _mp_err and {"account", "suffix"} <= set(_mp_profiles["宿舍"]["values"]),
      repr(_mp_err))
_mp_new, _mp_new_err = _pf.upsert({}, "教学楼", {"host": "172.16.80.3", "port": 80,
                                                 "account": "2023000002", "suffix": "@yd"})
check("v2.1.1.0 老方案（没有账号/后缀键）切换后**照旧不动**（向后兼容）",
      not _mp_new_err
      and _pf.apply_to_config(_mp_cfg, _pf.upsert({}, "老方案", {"port": 80})[0],
                              "老方案")[0].get("account") == "2023000001")
_mp_merged, _mp_merge_err = _pf.apply_to_config(
    _mp_cfg, _mp_new, "教学楼", validate_config=lambda c: [])
check("v2.1.1.0 切到「教学楼」→ 账号与后缀**一起换**（宿舍/校内切换的核心）",
      not _mp_merge_err
      and _mp_merged.get("account") == "2023000002"
      and _mp_merged.get("suffix") == "@yd"
      and _mp_merged.get("active_profile") == "教学楼",
      repr((_mp_merge_err, _mp_merged.get("account"), _mp_merged.get("suffix"))))
_mp_saved_pf = getattr(svc, "_profiles_mod", None)
_mp_saved_load = getattr(svc, "_load_config", None)
svc._profiles_mod = _pf
try:
    check("v2.1.1.0 密码文件可按方案区分（没方案名 / 名字不合法 → 默认文件）",
          svc.password_file_for("宿舍").endswith("password.宿舍.txt")
          and svc.password_file_for("") == svc.PASSWORD_FILE
          and svc.password_file_for("a/b") == svc.PASSWORD_FILE,
          repr((svc.password_file_for("宿舍"), svc.PASSWORD_FILE)))
    svc._load_config = lambda: {"active_profile": "宿舍"}
    check("v2.1.1.0 生效文件优先级：方案专属文件不存在时回退默认",
          svc.active_password_file() == svc.PASSWORD_FILE,
          repr(svc.active_password_file()))
    check("v2.1.1.0 结构上保证**密码永不进配置**（PROFILE_KEYS 里没有密码字段）",
          all("password" not in k for k in _pf.PROFILE_KEYS))
finally:
    svc._profiles_mod = _mp_saved_pf
    if _mp_saved_load is not None:
        svc._load_config = _mp_saved_load

# ---- v2.1.3.0：方案自己的账号 / 密码（多网络多账号的落地点）----
# (1) profiles.update_values：**合并**改，只动传进来的键（不是整个 values 换掉 ✗）
_pf_merge = {"宿舍": {"values": {"host": "1.1.1.1", "account": "2023000001",
                                 "auto_check_interval_min": 30},
                      "match_ssids": ["Dorm-WiFi"]}}
_merged, _merr = _pf.update_values(_pf_merge, "宿舍", {"account": "2023000002", "suffix": "@yd"})
check("v2.1.3.0 方案凭据是**合并**改：网关 / 间隔 / Wi-Fi 匹配一个都没被抹掉",
      not _merr
      and _merged["宿舍"]["values"]["host"] == "1.1.1.1"
      and _merged["宿舍"]["values"]["auto_check_interval_min"] == 30
      and _merged["宿舍"]["values"]["account"] == "2023000002"
      and _merged["宿舍"]["values"]["suffix"] == "@yd"
      and _merged["宿舍"]["match_ssids"] == ["Dorm-WiFi"],
      repr((_merr, _merged.get("宿舍"))))
check("v2.1.3.0 合并是深拷贝：没污染传进去的那份配置",
      _pf_merge["宿舍"]["values"]["account"] == "2023000001")
check("v2.1.3.0 方案不存在 → 报错（手滑少打一个字不该悄悄多出一个方案）",
      _pf.update_values(_pf_merge, "没有这个", {"account": "1"})[1] != [])
check("v2.1.3.0 不认识的键被过滤掉（密码永远进不了方案 values）",
      "password" not in _pf.update_values(_pf_merge, "宿舍", {"password": "x"})[0]["宿舍"]["values"])

# (2) 服务侧：方案专属密码文件（真文件，写到临时目录里）
import tempfile as _tf  # noqa: E402

_cred_dir = _tf.mkdtemp(prefix="drcom_cred_")
_cred_saved = {k: getattr(svc, k, None) for k in ("BASE_DIR", "PASSWORD_FILE", "_PWD_VALUE")}
_cred_saved_load = getattr(svc, "_load_config", None)
_cred_active = {"name": "宿舍"}
svc.BASE_DIR = _cred_dir
svc.PASSWORD_FILE = os.path.join(_cred_dir, "password.txt")
svc._profiles_mod = _pf
svc._load_config = lambda: {"active_profile": _cred_active["name"]}
try:
    with open(svc.PASSWORD_FILE, "w", encoding="utf-8") as _f:
        _f.write("public-pwd\n")
    svc._PWD_VALUE = "public-pwd"
    _cred_path = svc._save_password_to_disk("#Demo-0002#", profile="宿舍")
    check("v2.1.3.0 方案密码写进 password.<方案名>.txt，公共文件一个字节都不动",
          _cred_path.endswith("password.宿舍.txt") and os.path.isfile(_cred_path)
          and open(svc.PASSWORD_FILE, encoding="utf-8").read().strip() == "public-pwd",
          repr(_cred_path))
    check("v2.1.3.0 **第一次**给当前方案建专属密码：内存密码立刻跟着换",
          svc._get_password() == "#Demo-0002#", repr(svc._get_password()))
    check("v2.1.3.0 生效文件优先级：方案专属文件存在 → 就是它",
          svc.active_password_file() == _cred_path, repr(svc.active_password_file()))
    svc._PWD_VALUE = "public-pwd"
    svc._save_password_to_disk("#Demo-0003#", profile="教学楼")
    check("v2.1.3.0 给**别的**方案存密码 → 正在用的密码不受影响",
          svc._get_password() == "public-pwd", repr(svc._get_password()))
    _cred_active["name"] = "教学楼"
    svc._load_password_from_disk()
    check("v2.1.3.0 切到带专属密码的方案并重读 → 内存密码换成该方案的",
          svc._get_password() == "#Demo-0003#", repr(svc._get_password()))
    check("v2.1.3.0 profile_has_own_password：写过的为真、没写过的为假",
          svc.profile_has_own_password("教学楼") is True
          and svc.profile_has_own_password("没写过") is False)
    svc.remove_profile_password("教学楼")
    check("v2.1.3.0 删掉方案专属密码 → 回落公共文件（内存密码不擅自改）",
          svc.profile_has_own_password("教学楼") is False
          and svc.active_password_file() == svc.PASSWORD_FILE
          and svc._get_password() == "#Demo-0003#")
    _cred_before = open(svc.PASSWORD_FILE, encoding="utf-8").read()
    _cred_raised = False
    try:
        svc._save_password_to_disk("#Demo-9999#", profile="a/b")
    except ValueError:
        _cred_raised = True
    check("v2.1.3.0 方案名不合法 → 拒绝写（fail-closed：绝不悄悄落到公共密码上）",
          _cred_raised and open(svc.PASSWORD_FILE, encoding="utf-8").read() == _cred_before)
finally:
    for _k, _v in _cred_saved.items():
        setattr(svc, _k, _v)
    if _cred_saved_load is not None:
        svc._load_config = _cred_saved_load

# (3) 切换方案要重载密码（否则切完还在用上一个方案的密码，登录一路失败却「配置看着都对」✗）
_pf_reloads = []
_pf._attach(load_config=lambda: {"profiles": {"宿舍": {"values": {"account": "2023000001"}}},
                                 "active_profile": ""},
            save_config=lambda cfg: None, validate_config=lambda cfg: [],
            load_password_from_disk=lambda: _pf_reloads.append(1))
check("v2.1.3.0 切方案后自动重载密码文件",
      _pf.activate("宿舍").get("ok") is True and _pf_reloads == [1], repr(_pf_reloads))


def _pf_boom():
    raise RuntimeError("读取密码文件炸了")


_pf._attach(load_config=lambda: {"profiles": {"宿舍": {"values": {"account": "1"}}},
                                 "active_profile": ""},
            save_config=lambda cfg: None, validate_config=lambda cfg: [],
            load_password_from_disk=_pf_boom)
check("v2.1.3.0 重载密码失败**不算**切方案失败（配置已切好，只记一条日志）",
      _pf.activate("宿舍").get("ok") is True)

# (4) 界面：状态页那张卡现在能存账号了；配置页能编辑「方案自己的账号 / 密码」
check("v2.1.3.0 状态页凭据卡：一次存「账号 + 运营商 + 密码」（不再是只存密码 ✗）",
      'id="btn-save-pwd" type="button">保存账号与密码' in _page
      and "API.saveCredentials" in _page and "scope: 'current'" in _page)
check("v2.1.3.0 状态页明确写了保存范围（账号进配置、密码单独存文件）",
      'id="cred-scope"' in _page and "密码永不写进配置文件" in _page
      and _page.index('id="btn-save-pwd"') < _page.index('id="cred-scope"'))
check("v2.1.3.0 配置页有「保存到选中方案」的账号 / 密码编辑区",
      'id="btn-profile-cred-save"' in _page and 'id="profile-account"' in _page
      and 'id="profile-pwd"' in _page and 'id="profile-pwd-clear"' in _page
      and "scope: 'profile'" in _page)
check("v2.1.3.0 端点里没有任何「把密码写进配置」的写法，PROFILE_KEYS 里也没有密码字段",
      'cfg["password"]' not in src_web and "cfg['password']" not in src_web
      and all("password" not in k for k in _pf.PROFILE_KEYS))

# ---- v2.1.1.0：安全冗余（新功能不许把既有防护漏掉）----
import web_api as _web_api_probe  # noqa: E402

_sec_cfg = {"account": "2023000001", "host": "172.16.80.3", "port": 80,
            "profiles": {"宿舍": {"values": {"account": "2023999999", "suffix": "@yd"}},
                         "教学楼": {"values": {"account": "2023000001", "suffix": ""}}},
            "suffix": ""}
check("v2.1.1.0 脱敏收集：顶层 + 每个方案里的账号都会被列出来（去重）",
      _web_api_probe._profile_accounts(_sec_cfg) == ["2023000001", "2023999999"],
      repr(_web_api_probe._profile_accounts(_sec_cfg)))
_masked = _web_api_probe._mask_cfg_accounts(_sec_cfg, _web_api_probe._profile_accounts(_sec_cfg))
check("v2.1.1.0 诊断包配置：**方案里的账号也打码**（多账号一起脱敏）",
      "2023999999" not in str(_masked) and "2023000001" not in str(_masked)
      and "2023******" in str(_masked),
      str(_masked.get("profiles")))
check("v2.1.1.0 脱敏是深拷贝：**不污染内存里的实时配置**",
      _sec_cfg["profiles"]["宿舍"]["values"]["account"] == "2023999999"
      and _sec_cfg["account"] == "2023000001")
check("v2.1.1.0 长的账号先替换（短账号不会把长账号截一半）",
      _web_api_probe._mask_every_account("A=2023000001 B=202300000123",
                                         ["2023000001", "202300000123"]).count("*") > 0
      and "202300000123" not in _web_api_probe._mask_every_account(
          "x 202300000123", ["2023000001", "202300000123"]))
_sec_ignore = pathlib.Path(".gitignore").read_text(encoding="utf-8")
check("v2.1.1.0 每个方案的密码文件都被 .gitignore 覆盖（一个都不能入库）",
      "password.txt" in _sec_ignore and "password.*.txt" in _sec_ignore)
_sec_saved = {k: getattr(_web_api_probe, k, None)
              for k in ("_load_config", "_get_password", "_snapshot_state")}
_web_api_probe._load_config = lambda: dict(_sec_cfg)
_web_api_probe._get_password = lambda: "#Fake-Pwd-0000#"
_web_api_probe._snapshot_state = lambda: {}
try:
    _sec_zip = _web_api_probe._build_diagnostics_zip()
finally:
    for _k, _v in _sec_saved.items():
        if _v is not None:
            setattr(_web_api_probe, _k, _v)
with zipfile.ZipFile(io.BytesIO(_sec_zip)) as _zf:
    _sec_names = _zf.namelist()
    _sec_cfg_text = _zf.read("config.json").decode("utf-8")
check("v2.1.1.0 诊断包里**没有任何密码文件**，密码原文也不在包内",
      all("password" not in n for n in _sec_names)
      and "#Fake-Pwd-0000#" not in _sec_zip.decode("utf-8", "replace"),
      repr(_sec_names))
check("v2.1.1.0 诊断包 config.json：两个账号都已脱敏",
      "2023000001" not in _sec_cfg_text and "2023999999" not in _sec_cfg_text)
check("v2.1.1.0 配置页：方案卡片显示账号、匹配名可一键用当前 Wi-Fi",
      "profile-use-current-ssid" in src_web
      and "var lastWifi = null;" in src_web
      and "cur.values.account" in src_web
      and "账号与后缀会一起切过来" in src_web
      and "账号密码不受影响" not in src_web)

# ---- v2.1.0.0（2.1 线开线）：P7 技术债 ----
# P7-2（隐私，优先）：登录是 **GET**，密码就在 URL 的 query 里 —— 任何把 URL 带出来的
# 异常（HTTPError / URLError 包装 / socket 层错误）都会顺手把密码写进日志 ✗。
import urllib.request  # noqa: E402

_p72_pwd = "#Fake-Pwd-7777#"
_p72_urlish = "http://172.16.80.3:801/eportal/portal/login?user_password={}&callback=dr1".format(_p72_pwd)
check("v2.1.0.0 P7-2：_scrub_url 能把 URL 与 password= 参数擦掉",
      _p72_pwd not in _proto._scrub_url("boom " + _p72_urlish)
      and "http://" not in _proto._scrub_url("boom " + _p72_urlish)
      and "***" in _proto._scrub_url("user_password=" + _p72_pwd))
check("v2.1.0.0 P7-2：_safe_error_text 保留有用信息（类型 / HTTP 状态码）",
      _proto._safe_error_text(OSError("connection refused")) == "OSError: connection refused"
      and _proto._safe_error_text(urllib.error.HTTPError("http://x", 500, "boom", None, None))
      .startswith("HTTP 500"))

_p72_logs = []
_p72_saved = {k: getattr(_proto, k, None) for k in ("_log",)}
_p72_saved_urlopen = urllib.request.urlopen
_proto._log = lambda *a, **k: _p72_logs.append((a[0] % a[1:]) if len(a) > 1 else str(a[0]))


def _p72_leaky_urlopen(req, timeout=None):
    """模拟「最坏情况」：底层异常把整条 URL（含密码）原样带出来 ✗。"""
    raise OSError("connection failed for {}".format(getattr(req, "full_url", _p72_urlish)))


urllib.request.urlopen = _p72_leaky_urlopen
_p72_ok, _p72_msg = _proto.login("172.16.80.3", "2023fake0001", "@yd", _p72_pwd, "10.0.0.2", "E25B367B8DAC")
urllib.request.urlopen = _p72_saved_urlopen
_proto._log = _p72_saved["_log"]
_p72_text = " | ".join(_p72_logs)
check("v2.1.0.0 P7-2：最坏情况异常下，日志与返回文案里都没有密码 / 没有 URL",
      _p72_ok is False and _p72_pwd not in _p72_text and _p72_pwd not in _p72_msg
      and "http://" not in _p72_text and "http://" not in _p72_msg
      and "OSError" in _p72_text,
      repr((_p72_msg, _p72_text[:120])))

# P7-4：next_check_at 单写者 —— 无退避时 run_once **不许**改写调度者写的值
_p74 = importlib.import_module("protocol")
_p74_saved = {k: getattr(_p74, k, None) for k in
              ("_RUN_LOCK", "_STATE", "_STATE_LOCK", "_log", "_load_config", "get_current_ssid",
               "_auto_profile", "get_local_ips", "guard_allows", "_set_state", "wait_network",
               "is_online", "_get_password", "login", "_now_iso", "_backoff_until",
               "_set_backoff", "_reset_backoff")}
_p74._RUN_LOCK = threading.Lock()
_p74._STATE = {"next_check_at": "SENTINEL"}
_p74._STATE_LOCK = threading.Lock()
_p74._log = lambda *a, **k: None
_p74._load_config = lambda: {"host": "172.16.80.3", "port": 80, "account": "2023fake0001",
                             "suffix": "@yd", "auto_check_interval_min": 30,
                             "network_wait_timeout_sec": 10}
_p74.get_current_ssid = lambda: ""
_p74._auto_profile = None
_p74.get_local_ips = lambda: []
_p74.guard_allows = lambda cfg, ssid, ips: (True, "")
_p74._set_state = lambda **k: _p74._STATE.update(k)
_p74.wait_network = lambda *a, **k: True
_p74.is_online = lambda host: True          # 已在线 → 走最短路径，跳过登录
_p74._get_password = lambda: "x"
_p74.login = lambda *a, **k: (True, "x")
_p74._now_iso = lambda: "2026-09-28T12:00:00"
_p74._backoff_until = lambda: None
_p74._set_backoff = lambda *a, **k: None
_p74._reset_backoff = lambda *a, **k: None
_p74.run_once("manual")
_p74_no_backoff = _p74._STATE.get("next_check_at")
_p74._backoff_until = lambda: "2026-09-28T12:05:00"
_p74.run_once("manual")
_p74_with_backoff = _p74._STATE.get("next_check_at")
for _k, _v in _p74_saved.items():
    setattr(_p74, _k, _v)
check("v2.1.0.0 P7-4：无退避时 run_once 不改写 next_check_at（消除双写竞争）",
      _p74_no_backoff == "SENTINEL" and _p74_with_backoff == "2026-09-28T12:05:00",
      repr((_p74_no_backoff, _p74_with_backoff)))

# P7-1：os._exit(0) 之前必须先显式放锁（顺序断言：**真正的调用**前 400 字符里必须有放锁 ✓）
_p71_exit_idx = src_upd.rindex("os._exit(0)")      # rindex：注释里也出现过这个词 ✗
check("v2.1.0.0 P7-1：os._exit 之前显式放锁（顺序写明白）",
      "_release_update_lock()" in src_upd[_p71_exit_idx - 400:_p71_exit_idx],
      repr(src_upd[_p71_exit_idx - 160:_p71_exit_idx]))

# P7-3：批处理细节
_inst14 = pathlib.Path("install.bat").read_text(encoding="utf-8", errors="replace")
_uinst14 = pathlib.Path("uninstall.bat").read_text(encoding="utf-8", errors="replace")
check("v2.1.0.0 P7-3：install.bat 不再硬编码 C:\\Python314（改用 py 启动器 + 常见安装位置）",
      'set "PYTHON=C:\\Python314' not in _inst14 and "py -3" in _inst14
      and "Programs\\Python\\Python3*" in _inst14)
check("v2.1.0.0 P7-3：uninstall.bat 的 choice 补上 /D N /T 30（无人值守不挂死、默认保留数据）",
      "choice /C YN /N /D N /T 30" in _uinst14)
check("v2.1.0.0 P7-3：两个批处理都写明了「路径不能含 !」的限制",
      "不能含" in _inst14 and "EnableDelayedExpansion" in _inst14
      and "不能含" in _uinst14)

# v2.1.0.0 补丁：发布说明里的代号必须由 CI 从 version.py 填 —— 头部不许再硬编码星名 ✗
# （事故：换了版本线到 Vega，发布页首行还写着 "Sirius"，因为模板里是死的字面量。）
# 2026-11 维护：发布说明改为「模板 + 渲染器」—— 机器事实（版本/大小/SHA/对比链接/日期）
# 一律由 .github/scripts/render_release_notes.py 注入，两个 release 通道共用一套逻辑。
_rel_head = pathlib.Path("packaging/RELEASE-NOTES.md").read_text(
    encoding="utf-8", errors="replace").splitlines()[0]
_wf_src = pathlib.Path(".github/workflows/build-installer.yml").read_text(
    encoding="utf-8", errors="replace")
_render_rel = ".github/scripts/render_release_notes.py"
_ver_mod = importlib.import_module("version")   # 本文件后段才 import version，这里自带 ✓
check("v2.1.0.0 补丁：RELEASE-NOTES 头部用 {{CODENAME}} 占位（不再硬编码星名）",
      "{{CODENAME}}" in _rel_head and "{{CODENAME_CN}}" in _rel_head
      and "Sirius" not in _rel_head and _ver_mod.CODENAME not in _rel_head,
      _rel_head[:100])
check("发布说明：CI 从 version.py 读代号并交给渲染器（不再手写 Replace）",
      "APP_CODENAME=$cn" in _wf_src
      and '"--codename", "$env:APP_CODENAME"' in _wf_src
      and '"--codename-cn", "$env:APP_CODENAME_CN"' in _wf_src)
check("发布说明：两条 release 通道都走同一个渲染器",
      _wf_src.count("render_release_notes.py") >= 2
      and '"--channel", "installer"' in _wf_src
      and '"--channel", "versioned"' in _wf_src)
check("发布说明：旧的逐条 $body.Replace 已移除（单一事实来源）",
      "$body.Replace('{{" not in _wf_src)
check("发布说明：安装器通道有独立模板",
      pathlib.Path("packaging/RELEASE-NOTES-installer.md").is_file())
check("发布说明：渲染器文件存在",
      pathlib.Path(_render_rel).is_file())

# —— 行为断言（不是 grep 源码）：真跑一遍渲染器，覆盖残留占位符 / 隐私 / 标题 / 时区 ——
import subprocess as _subprocess   # noqa: E402
_rc = _subprocess.run([sys.executable, _render_rel, "--self-test"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace")
check("发布说明：渲染器自测全绿（含两套模板 + 标题占位符 + 上海时区）",
      _rc.returncode == 0,
      ((_rc.stdout or "").strip().splitlines() or ["<no stdout>"])[-1]
      + " | " + ((_rc.stderr or "").strip().splitlines() or [""])[-1])
_dry = _subprocess.run(
    [sys.executable, _render_rel,
     "--template", "packaging/RELEASE-NOTES.md", "--repo", "o/r",
     "--channel", "versioned", "--version", "9.9.9.9",
     "--codename", "Vega", "--codename-cn", "织女星",
     "--asset", "x.exe", "--size", "1024", "--sha256", "ab" * 32,
     "--commit", "c" * 40, "--run", "1", "--prev-tag", "v9.9.9.8", "--dry-run"],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("发布说明：真实模板可端到端渲染（无 {{...}} 残留）",
      _dry.returncode == 0 and "{{" not in _dry.stdout,
      (_dry.stdout or "")[-300:] + (_dry.stderr or "")[-200:])

# —— 发布 / 分支保护闸门（审计 P1-1 / P1-2 / D-17）——
check("发布流程：同名版本默认不覆盖正式版 release（版本号必须先递增）",
      "allow_republish" in _wf_src and "已跳过正式版发布" in _wf_src)
_bp_json = json.loads(pathlib.Path(".github/branch-protection.json").read_text(encoding="utf-8"))
check("分支保护：main 的配置要求走 PR（required_pull_request_reviews 非 null）",
      isinstance(_bp_json.get("required_pull_request_reviews"), dict)
      and _bp_json.get("required_status_checks", {}).get("strict") is True)
_pr_checks_wf = pathlib.Path(".github/workflows/pr-checks.yml").read_text(encoding="utf-8")
check("CI：PR Gate 会跑 PR 机器人 / 发布说明渲染器的自测",
      "pr_review.py --self-test" in _pr_checks_wf
      and "render_release_notes.py --self-test" in _pr_checks_wf)

check("v2.0.4.0 changelog 多路径候选", "_changelog_candidates" in _src_eula)
_eula = importlib.import_module("eula")
_eula._attach(base_dir=tempfile.mkdtemp())  # 空目录 = 模拟"安装包漏带 CHANGELOG.md"
_st_missing, _pl_missing = _eula.api_get_changelog()
_err = _pl_missing.get("error", "")
check("v2.0.4.0 changelog 缺失 → 友好中文提示（不再是裸 Errno）",
      _st_missing == 500 and "Errno" not in _err and "更新日志" in _err, repr(_err))
check("v2.0.4.0 changelog 缺失时给出仓库链接", "github.com" in _pl_missing.get("url", ""))
check("v2.0.4.0 changelog 弹窗渲染兜底链接",
      "changelog-link" in src_web and "在仓库查看完整更新日志" in src_web)
_eula._attach(base_dir=os.path.dirname(os.path.abspath(__file__)))
_st_ok, _pl_ok = _eula.api_get_changelog()
check("v2.0.4.0 changelog 正常读取", _st_ok == 200 and int(_pl_ok.get("size") or 0) > 100,
      "status=%s size=%s" % (_st_ok, _pl_ok.get("size")))

# ---- v2.1.2.0：BakaXL 风（星尘主题）—— 默认外观 + 三态循环 ----
# 为什么要盯这几条：这次动的是「**默认外观**」，坏了不会报错，只会让所有人第一眼看到错的
# 主题；而新加的整窗图层一旦漏了 pointer-events: none，整页会点不动（静默灾难）。
import re as _re  # noqa: E402


def _css_rule(selector):
    """从页面 CSS 抠出某条声明的块（只认本文件里「选择器 { … }」的扁平写法，不嵌大括号）。"""
    _m = _re.search(_re.escape(selector) + r"\s*\{([^}]*)\}", _page)
    return _m.group(1) if _m else ""


def _decl(block, name):
    _m = _re.search(_re.escape(name) + r"\s*:\s*([^;]+);", block)
    return _m.group(1).strip() if _m else ""


def _pos(needle):
    """页面里的出现位置（没有就 -1）—— 用来断言「谁在谁前面」这种使用逻辑顺序。"""
    return _page.index(needle) if needle in _page else -1


check("v2.1.2.0 三套令牌并存（:root 亮色 / dark / baka），baka 是实打实的一套",
      ":root {" in _page and '[data-theme="dark"] {' in _page
      and '[data-theme="baka"] {' in _page
      and _decl(_css_rule('[data-theme="baka"]'), "--material") != ""
      and _decl(_css_rule('[data-theme="baka"]'), "--accent") != "")
check("v2.1.2.0 整窗大图背景只挂在 baka 上（老主题的 body::before 仍是三处色晕）",
      "radial-gradient" in _css_rule('[data-theme="baka"] body::before')
      and "var(--glow-a)" in _css_rule("body::before"))
_baka_mask = _css_rule('[data-theme="baka"] body::after')
check("v2.1.2.0 整窗遮罩不吃点击（漏了 pointer-events: none 整页就点不动）",
      _decl(_baka_mask, "pointer-events") == "none", repr(_baka_mask[:60]))
check("v2.1.2.0 背景 / 遮罩都压在内容层之下（负 z-index < .wrap 的 1）",
      _decl(_css_rule('[data-theme="baka"] body::before'), "z-index") == "-3"
      and _decl(_baka_mask, "z-index") == "-2"
      and _decl(_css_rule(".wrap"), "z-index") == "1")
check("v2.1.2.0 首屏按使用逻辑排：「状态 + 立即登录」在同一张卡里，且都在诊断指标之前",
      _pos('class="card status-hero"') >= 0
      and _pos('id="card-online"') >= 0 and _pos('id="btn-login"') >= 0
      and _pos('id="card-online"') < _pos('id="btn-login"') < _pos('id="card-uptime"'))

# —— 第三轮（用户反馈：「状态 / 配置 / 日志 改了个寂寞」——只改表面不算，得动信息架构）——
check("v2.1.2.0 状态页：状态与动作合并成一张卡（不再两张卡各空一半），旧的 .lead 结构清掉",
      'class="card status-hero"' in _page and 'class="status-word" id="kpi-online"' in _page
      and 'class="status-hero-act"' in _page and 'class="lead"' not in _page
      and '.action-card {' not in _page)
check("v2.1.2.0 状态页：5 项日常从「卡墙」改成一条统计条（格间竖线，出错那格浅橙底）",
      'class="card stat-strip"' in _page and _page.count('class="stat-cell"') >= 5
      and 'lead-facts' not in _page and '.stat-cell.stat-alert' in _page
      and "--warn-fill" in _page)
check("v2.1.2.0 状态页：统计条换了类名，JS 也同步（不然轮询一跑样式就被覆盖）",
      "'stat-value tone-ok'" in _page and "'status-word tone-ok'" in _page
      and "'stat-value mono'" in _page and "cardErr.className = 'stat-cell'" in _page
      and "'stat-value mono tone-muted'" in _page)
check("v2.1.2.0 页面两栏布局：配置页与状态页共用同一套（左主右辅，窄屏塌成一栏）",
      _page.count('class="page-cols"') == 2 and _page.count('<div class="page-col">') == 4
      and ".page-cols { display: grid" in _page and ".page-col { display: grid" in _page
      and ".page-col > .status-hero, .page-col > .stat-strip { margin-top: 0; }" in _page
      and "@media (max-width: 980px) { .page-cols" in _page
      and "cfg-page" not in _page and "cfg-col" not in _page)
check("v2.1.2.0 状态页左右两栏：左「状态 + 日常统计条 + 诊断指标条」/ 右「账户与登录密码」",
      _pos('id="card-online"') < _pos('class="card stat-strip"')
      < _pos('class="card stat-strip stat-strip-4"') < _pos('id="card-password"')
      and _pos('id="card-password"') < _pos('id="panel-config"'),
      "online@%s strip@%s diag@%s pwd@%s" % (_pos('id="card-online"'),
          _pos('class="card stat-strip"'), _pos('class="card stat-strip stat-strip-4"'),
          _pos('id="card-password"')))
check("v2.1.2.0 状态页右栏的账号密码竖排（右栏约 430px，四列会变窄条）；端口卡与升级设置仍一行三列",
      'class="pw-row pw-row-4"' not in _page and ".pw-row-4" not in _page
      and _page.count('class="pw-row pw-row-3"') == 2
      and ".pw-row-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }" in _page
      and ".pw-row .field > input, .pw-row .field > select { max-width: none; }" in _page
      and "pw-grid" not in _page)
check("v2.1.2.0 字体统一到 MiSans（装了优先用，没装退回系统栈；仓库不加字体文件）",
      '"MiSans", "MiSans VF", "MiSans Regular", "MiSans Normal"' in _page
      and "--font-ui:" in _page and "--font-mono: var(--font-ui);" in _page
      and "font-family: var(--font-ui);" in _page and "font-family: var(--font-mono);" in _page
      and "font-variant-numeric: tabular-nums;" in _page
      and "@font-face {" not in _page and "ui-monospace" not in _page)
check("v2.1.2.0 日志页：改成终端窗口 —— 标题栏 + 实时状态在终端之上（原来压在下面，等于标题栏装脚上）",
      'class="card log-window"' in _page and 'class="log-window-bar"' in _page
      and 'class="log-dots"' in _page
      and _pos('class="log-window-bar"') < _pos('id="log-box"')
      and _pos('class="log-meta"') < _pos('id="log-box"')
      and _decl(_css_rule(".log-meta"), "margin-left") == "auto")
check("v2.1.2.0 日志页：占用与诊断包改两栏（原来一长段说明把整行撑满）",
      'class="log-diag-cols"' in _page and "日志占用与诊断包" in _page
      and _pos('id="log-list"') < _pos('id="btn-logs-refresh"')
      and _decl(_css_rule(".log-diag-cols"), "display") == "grid")
check("v2.1.2.0 品牌大标题已撤（不再与顶栏重复）+ 诊断指标不再折叠（三轮：直接显示）",
      'class="hero-title"' not in _page and '<details class="diag">' not in _page
      and _page.count('<details class="diag fold">') >= 3   # 折叠机制仍服务于配置 / 关于的进阶设置
      and 'class="diag-summary"' in _page)
check("v2.1.2.0 首屏默认主题 = baka，三种取值都认（存过的用户不被清掉）",
      "var theme = 'baka';" in _page
      and "saved === 'baka' || saved === 'dark' || saved === 'light'" in _page
      and "systemTheme" not in _page and "matchMedia" not in _page)
_theme_arr = _re.search(r"var THEMES = \[([^\]]+)\]", _page)
_palette = [t.strip().strip("'") for t in _theme_arr.group(1).split(",")] if _theme_arr else []
_seq = []
if _palette:
    _cur = _palette[0]
    for _ in range(len(_palette)):
        _cur = _palette[(_palette.index(_cur) + 1) % len(_palette)]
        _seq.append(_cur)
check("v2.1.2.0 点三下回到原点（三态循环闭合：baka → light → dark → baka）",
      _palette == ["baka", "light", "dark"] and _seq == ["light", "dark", "baka"],
      "%s / %s" % (_palette, _seq))
check("v2.1.2.0 按钮静态标签不再写死两态，且首屏同步一次（读屏别念错）",
      'aria-label="切换亮色 / 暗色主题"' not in _page
      and "applyTheme(currentTheme(), false);" in _page)


# ---- v2.1.2.0（第二轮，按用户反馈重排）：账号密码上主页 / 关于页重做 / 安装方式识别 ----
# 用户原话：「把账户与密码移动到主页」「配置每个版块占这么多真的好吗」「关于放的很臃肿」
#           「更新的边上应该放一个小的日志窗或者告诉检测到新版本的结果」
#           「关于的访问入口有存在的必要吗」「卸载服务的提示是写死的吗？有没有办法区分」
class _FakeProbe:
    """给 `_detect_install_mode` 用的假探测（判据命中顺序必须可断言，且不碰真机）。"""

    def __init__(self, uninstaller=None, reg=(), files=(), dirs=()):
        self._unins = uninstaller
        self._reg = set(reg)
        self._files = set(files)
        self._dirs = set(dirs)

    def isfile(self, path):
        return path in self._files

    def isdir(self, path):
        return path in self._dirs

    def find_uninstaller(self, app_dir):
        return self._unins

    def reg_exists(self, subkey):
        return subkey in self._reg


_det = _web_api_probe._detect_install_mode
_app = r"C:\Program Files\DrcomAutoLogin"
_ins = _det(_app, probe=_FakeProbe(uninstaller="unins000.exe", reg=[_web_api_probe._SERVICE_REG_KEY]))
check("v2.1.2.0 安装方式：目录里有 Inno 卸载器 → 安装包，且提示走「设置 → 应用」",
      _ins["mode"] == "installer" and "应用" in _ins["uninstall_hint"]
      and "unins000.exe" in _ins["uninstall_hint"] and _ins["service_installed"] is True,
      _ins["uninstall_hint"][:60])
_reg_only = _det(_app, probe=_FakeProbe(reg=[_web_api_probe._UNINSTALL_REG_KEYS[0]]))
check("v2.1.2.0 安装方式：只剩注册表卸载项也判安装包（卸载器被改名 / 挪走时），",
      _reg_only["mode"] == "installer" and _reg_only["uninstaller"] is None,
      _reg_only["mode"])
_src = _det(r"D:\code\StardustFlashLink", probe=_FakeProbe(
    files=[r"D:\code\StardustFlashLink\packaging\setup.iss"]))
check("v2.1.2.0 安装方式：源码树特征 → 源码部署，提示运行 uninstall.bat",
      _src["mode"] == "source" and "uninstall.bat" in _src["uninstall_hint"]
      and "安装包安装" not in _src["uninstall_hint"], _src["uninstall_hint"][:60])
check("v2.1.2.0 安装方式：卸载器优先于源码特征（装完的源码目录不该被判成源码）",
      _det(_app, probe=_FakeProbe(uninstaller="unins001.exe", dirs=[_app + r"\.git"]))["mode"]
      == "installer")
# 真机实测撞到的组合（本机装过安装包，但当前跑的是源码工作区）：必须判「当前这份代码」
_src_reg = _det(r"D:\code\wt", probe=_FakeProbe(
    files=[r"D:\code\wt\packaging\setup.iss"],
    reg=[_web_api_probe._UNINSTALL_REG_KEYS[0], _web_api_probe._SERVICE_REG_KEY]))
check("v2.1.2.0 安装方式：源码树 + 注册表卸载项 → 判源码（别引去卸载没在跑的那份），并附注另有安装副本",
      _src_reg["mode"] == "source" and "uninstall.bat" in _src_reg["uninstall_hint"]
      and "安装包安装的副本" in _src_reg["uninstall_hint"],
      _src_reg["uninstall_hint"][:70])
_unk = _det(r"C:\some\where", probe=_FakeProbe())
check("v2.1.2.0 安装方式：都没命中 → unknown，且两种卸载途径都提到",
      _unk["mode"] == "unknown" and "uninstall.bat" in _unk["uninstall_hint"]
      and "应用" in _unk["uninstall_hint"])
_about_saved = {k: getattr(_web_api_probe, k, None) for k in
                ("BASE_DIR", "LOG_FILE", "CONFIG_FILE", "PASSWORD_FILE", "LOG_DIR",
                 "VERSION", "VERSION_FULL", "CODENAME", "CODENAME_CN", "logger",
                 "_snapshot_state")}


class _FakeLogger:
    def warning(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


_web_api_probe.BASE_DIR = _app            # 假安装目录：不依赖本机真实布局
_web_api_probe.LOG_FILE = _app + r"\logs\campus_login.log"
_web_api_probe.CONFIG_FILE = _app + r"\config.json"
_web_api_probe.PASSWORD_FILE = _app + r"\password.txt"
_web_api_probe.LOG_DIR = _app + r"\logs"
_web_api_probe.VERSION = "1.2.3"
_web_api_probe.VERSION_FULL = "1.2.3 (test)"
_web_api_probe.CODENAME = "Test"
_web_api_probe.CODENAME_CN = "测试"
_web_api_probe.logger = _FakeLogger()
_web_api_probe._snapshot_state = lambda: {"service_started_at": "2026-09-29T10:00:00",
                                          "service_uptime_sec": 60}
try:
    _about_payload = _web_api_probe.api_get_about()
finally:
    for _k, _v in _about_saved.items():
        if _v is not None:
            setattr(_web_api_probe, _k, _v)
check("v2.1.2.0 /api/about 带上 install 判定 + 卸载提示（前端据此说对话）",
      isinstance(_about_payload.get("install"), dict)
      and _about_payload["install"].get("mode") in ("installer", "source", "unknown")
      and bool(_about_payload["install"].get("uninstall_hint")),
      str(_about_payload.get("install", {}).get("mode")))
check("v2.1.2.0 卸载提示不再写死一句话（按 installInfo 给，兜底才用旧文案）",
      "installInfo.uninstall_hint" in _page and "installInfo.evidence" in _page
      and 'id="about-mode-badge"' in _page)

# —— 搬运结果：谁该在哪个面板里（用位置断言，防止以后又被搬回去）——
_end_status = _pos('id="panel-config"')
_end_config = _pos('id="panel-log"')
_end_log = _pos('id="panel-about"')
check("v2.1.2.0 账户与登录密码在「状态」页（不是配置页）",
      _pos('id="card-password"') >= 0 and _pos('id="card-password"') < _end_status
      and _pos('id="cfg-account"') < _end_status,
      "card-password@%s panel-config@%s" % (_pos('id="card-password"'), _end_status))
check("v2.1.2.0 日志与诊断搬到了「日志」页（不再挤在关于页）",
      _pos('id="log-list"') > _end_config and _pos('id="log-list"') < _end_log
      and _pos('id="btn-logs-refresh"') < _end_log,
      "log-list@%s panel-log@%s panel-about@%s" % (_pos('id="log-list"'), _end_config, _end_log))
check("v2.1.2.0 关于页：删掉「访问入口」、加上「更新」块与升流小窗、管理操作折起来",
      "about-local" not in _page
      and 'id="about-upd-result"' in _page and 'id="about-update-log"' in _page
      and _pos('id="btn-update-check-now"') > _end_log   # 检查按钮也在关于页里（三轮搬来）
      and _pos('id="about-upd-result"') > _end_log)  # 确实落在关于面板里（不是别处）
check("v2.1.2.0 配置页瘦身：主流程之外的都折起来（新建方案 / 网络位置守卫）",
      _page.count('<details class="diag fold">') >= 2
      and 'id="guard-summary-hint"' in _page and 'class="fold-body field"' in _page)

# —— 第四轮（用户：删掉那几条说明性文案 + 顶部提示条；修长路径换行与全局间距）——
check("v2.1.2.0 顶部说明条整条已删（连同样式与那条空转的进场动画）",
      'class="hint-strip"' not in _page and ".hint-strip {" not in _page
      and "本服务仅监听" not in _page and "查看源码" not in _page
      and "@keyframes riseIn" not in _page and "animation: riseIn" not in _page)
check("v2.1.2.0 三条「说明性文案」不上屏（诊断脚注 / 凭据说明 / 安装判据）",
      "不另存状态文件" not in _page and "完整凭据" not in _page
      and "about-mode-evidence" not in _page
      and "badge.setAttribute('title', '判据：'" in _page)   # 判据改悬停提示，信息没丢
check("v2.1.2.0 统计条里的 ISO 日期不再折成三行（日期 / 时间两段 nowrap，最多两行）",
      ".nb { white-space: nowrap; }" in _page
      and "display: flex; align-items: center; flex-wrap: wrap;" in _page
      and "function isoValueHtml(iso) {" in _page
      and "$('kpi-lastlogin').innerHTML = s.last_login_at ? isoValueHtml(s.last_login_at)" in _page)
check("v2.1.2.0 次要操作不再用主按钮尺寸：「服务与维护」四键缩成 btn-sm（窄栏里少占一行）",
      ".btn-sm { padding: 7px 14px; font-size: 13px; gap: 5px; }" in _page
      and 'class="btn btn-sm btn-secondary" id="btn-restart"' in _page
      and 'class="btn btn-sm btn-danger" id="btn-uninstall"' in _page
      and 'class="btn btn-sm btn-secondary" id="btn-update-history"' in _page
      and 'class="btn btn-sm btn-secondary" id="btn-changelog"' in _page)
check("v2.1.2.0 间距收口成三个令牌（卡片内距 / 卡片间距 / 字段间距），不再各处随手写",
      "--pad-card: 20px;" in _page and "--gap-card: 14px;" in _page and "--gap-field: 16px;" in _page
      and "  padding: var(--pad-card);" in _page
      and ".page-col { display: grid; gap: var(--gap-card);" in _page
      and ".field { position: relative; margin-bottom: var(--gap-field); }" in _page
      and ".stat-cell { padding: 15px var(--pad-card);" in _page)
check("v2.1.2.0 长路径只在目录分隔符后换行（不再断成「半个圆角框」/ 半截文件名）",
      "code.path {\n  display: block;" in _page
      and "code.path .seg { display: inline-block; max-width: 100%; overflow-wrap: anywhere;" in _page
      and "function pathHtml(p) {" in _page and "split(/([\\\\/])/)" in _page
      and '<span class="seg">' in _page
      and "grid-template-columns: 84px minmax(0, 1fr);" in _page)
check("v2.1.2.0 升级相关整块搬进「关于 → 更新」（配置页不再出现任何升级字段 / 按钮）",
      _pos('id="cfg-auto-update-enabled"') > _end_log and _pos('id="cfg-update-interval"') > _end_log
      and _pos('id="cfg-update-disk"') > _end_log and _pos('id="rollback-version"') > _end_log
      and _pos('id="btn-update-install-now"') > _end_log
      and _pos('id="cfg-auto-interval"') < _end_config   # 周期自检仍留在配置页（对照）
      and _pos('id="cfg-auto-interval"') < _end_log,
      "cfg-update-interval@%s panel-log@%s panel-about@%s"
      % (_pos('id="cfg-update-interval"'), _end_config, _end_log))
check("v2.1.2.0 关于页的升级设置自带保存按钮，且跳过配置页校验（新装用户会先来这儿开自动升级）",
      'id="btn-save-update-settings"' in _page
      and "saveConfigFrom(saveUpd, true)" in _page and "saveConfigFrom(saveBtn, false)" in _page)
check("v2.1.2.0 横向表单行：端口卡与关于页升级设置各一行三列（不会再被 auto-fit 挤出空轨）",
      _page.count('class="pw-row pw-row-3"') == 2
      and ".pw-row-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }" in _page)
# —— 第二轮打磨（用户：「排版要重新设计」「动效要做」「关于里 Logo 也没打上去」）——
check("v2.1.2.0 关于页用的是真品牌 Logo（取不到才回退内联星芒）",
      'id="about-logo"' in _page and 'src="/branding/web-logo-64.png"' in _page
      and "bindLogoFallback($('about-logo'), 40)" in _page)
check("v2.1.2.0 关于页：结论条 + 三张数据牌 + 数据/维护两栏（不再是一列白卡堆到底）",
      'id="about-upd-callout"' in _page and 'class="about-callout' in _page
      and 'class="about-stats"' in _page and 'id="about-upd-latest"' in _page
      and 'class="about-cols"' in _page)
check("v2.1.2.0 动效层：统一缓动令牌 + 卡片进场 + 数值变化脉冲 + 折叠淡入",
      "--ease: cubic-bezier" in _page and "--dur-3:" in _page
      and "@keyframes cardIn" in _page and "@keyframes statePulse" in _page
      and "@keyframes valueFlash" in _page and "@keyframes foldIn" in _page
      and ".kpi-value.flash" in _page and "markValues();" in _page)
check("v2.1.2.0 配置页不再为单个字段占一整张卡（管理页面端口并进「网络与服务端口」）",
      'class="section-title">网络与服务端口' in _page
      and 'id="cfg-ui-port"' in _page and ">Web UI<" not in _page)
check("v2.1.2.0 密码徽标在主页也要能更新（不能只在 loadConfig 里刷）",
      "c.password_status" in _page and "setPwdBadge(c.password_status)" in _page)


# ============================================================
# v2.x 维护（2026-11 代码审计）—— 行为级回归断言
# 每条对应一个真实复现过的缺陷；断言测行为，不 grep 源码。
# ============================================================

# —— M-1：config.json 带 BOM 不再被重置；损坏时先备份再写默认值（审计 C-02/C-07）——
_m1_dir = tempfile.mkdtemp(prefix="drcom_m1_")
_m1_cfg = os.path.join(_m1_dir, "config.json")
_m1_saved_cfg_file = svc.CONFIG_FILE
svc.CONFIG_FILE = _m1_cfg
pathlib.Path(_m1_cfg).write_bytes(b"\xef\xbb\xbf" + json.dumps({
    "host": "10.9.9.9", "port": 80, "account": "2023000001", "suffix": "",
    "ui_port": 9999, "profiles": {"宿舍": {"values": {"host": "10.9.9.9"}}},
    "active_profile": "宿舍",
}, ensure_ascii=False).encode("utf-8"))
_m1_read = svc._load_config()
check("维护 M-1：config.json 带 BOM 仍能读出原值（不再回退默认）",
      _m1_read.get("account") == "2023000001" and _m1_read.get("ui_port") == 9999
      and "宿舍" in (_m1_read.get("profiles") or {}),
      repr({k: _m1_read.get(k) for k in ("account", "ui_port")}))
pathlib.Path(_m1_cfg).write_text("{ 这不是 JSON", encoding="utf-8")
svc._load_config()
_m1_bads = sorted(p.name for p in pathlib.Path(_m1_dir).glob("config.json.bad-*"))
check("维护 M-1：损坏的 config.json 先备份成 config.json.bad-*（不再直接覆盖）",
      len(_m1_bads) == 1, repr(_m1_bads))
svc.CONFIG_FILE = _m1_saved_cfg_file

# —— M-2：同进程多线程并发写 config.json 不再落盘坏 JSON（审计 C-01）——
_m2_dir = tempfile.mkdtemp(prefix="drcom_m2_")
_m2_cfg = os.path.join(_m2_dir, "config.json")
_m2_saved_cfg_file = svc.CONFIG_FILE
svc.CONFIG_FILE = _m2_cfg
_m2_errors = []


def _m2_writer():
    try:
        for i in range(40):
            c = svc._default_config()
            c["account"] = "202300%04d" % i
            svc._save_config(c)
    except Exception as exc:  # noqa: BLE001
        _m2_errors.append(repr(exc))


_m2_threads = [threading.Thread(target=_m2_writer) for _ in range(3)]
for _t in _m2_threads:
    _t.start()
for _t in _m2_threads:
    _t.join()
try:
    _m2_final = json.loads(pathlib.Path(_m2_cfg).read_text(encoding="utf-8-sig"))
    _m2_valid = str(_m2_final.get("account", "")).startswith("2023")
except (ValueError, OSError) as exc:
    _m2_final, _m2_valid = repr(exc), False
check("维护 M-2：3 线程 × 40 轮并发保存 → 最终文件仍是合法 JSON（审计 C-01）",
      not _m2_errors and _m2_valid, repr((_m2_errors[:2], _m2_final))[:300])
svc.CONFIG_FILE = _m2_saved_cfg_file

# —— M-3：profiles.py 仍是可选模块（审计 C-03：不能有第二段无条件 import）——
_m3_code = "\n".join(_l for _l in src_svc.splitlines() if not _l.strip().startswith("#"))
check("维护 M-3：profiles 只有一段受 ImportError 保护的 import（可选模块护栏）",
      _m3_code.count("import profiles as _profiles_mod") == 1,
      "代码里出现 %d 次" % _m3_code.count("import profiles as _profiles_mod"))
# 审计 C-06：方案保存 / 切换的「读-改-写」必须与服务层 api_post_config 共用同一把锁
_m3_prof = pathlib.Path("profiles.py").read_text(encoding="utf-8")
check("维护 M-3b：profiles 的四个读-改-写入口都持同一把 CONFIG_LOCK",
      "def _with_config_lock(fn)" in _m3_prof
      and _m3_prof.count("@_with_config_lock") == 4
      and "config_lock=None" in _m3_prof,
      "装饰器 %d 个" % _m3_prof.count("@_with_config_lock"))

# —— M-4：托盘「服务未响应」气泡可达（审计 C-04：旧判定是死逻辑）——
_tr = importlib.import_module("tray")
check("维护 M-4：托盘在刚跨过 down 阈值时弹一次、之后不重复、开机不吓人",
      _tr.decide_events("down", "down", _tr.DOWN_STREAK_TO_NOTIFY) != []
      and _tr.decide_events("down", "down", _tr.DOWN_STREAK_TO_NOTIFY + 1) == []
      and _tr.decide_events(None, "down", 1) == []
      and _tr.decide_events("ok", "ok", 0) == [],
      repr((_tr.decide_events("down", "down", _tr.DOWN_STREAK_TO_NOTIFY),
            _tr.decide_events("down", "down", _tr.DOWN_STREAK_TO_NOTIFY + 1))))

# —— M-5：「账号未设置」不算进连接质量分母（审计 C-12）——
_mt = importlib.import_module("metrics")
check("维护 M-5：「账号未设置」归入 skip，不再把在线率拉成 0%",
      any("账号未设置" in p for p in _mt.EV_SKIP), repr(_mt.EV_SKIP))

# —— M-6：配置页保存是「部分更新」，空 body 不再重置配置（审计 P1-1/P2-2）——
# web_api 的 _load_config / _save_config 正常由 _attach() 注入；本冒烟不 attach，
# 所以这里用「有没有旧值」分别记录，结束后按原样恢复（不留测试用的假属性）。
_m6_had_load = hasattr(_wa, "_load_config")
_m6_had_save = hasattr(_wa, "_save_config")
_m6_saved_load = getattr(_wa, "_load_config", None)
_m6_saved_save = getattr(_wa, "_save_config", None)
_m6_saved_lock = _wa.CONFIG_LOCK
# 前面几段测试给 web_api 注入过替身 logger（只实现部分方法）—— 这里临时摘掉，
# 让 _log 走「logger 未注入就静默」的分支，断言只关心配置合并行为。
_m6_had_logger = hasattr(_wa, "logger")
_m6_saved_logger = getattr(_wa, "logger", None)
_wa.logger = None
_m6_cur = dict(svc._default_config(), host="10.9.9.9", account="2023000001",
               profiles={"宿舍": {"values": {"host": "10.9.9.9"}}},
               active_profile="宿舍", profiles_auto_switch=True)


def _m6_load():
    return dict(_m6_cur)


def _m6_save(cfg):
    _m6_cur.clear()
    _m6_cur.update(cfg)


_wa._load_config = _m6_load
_wa._save_config = _m6_save
_wa.CONFIG_LOCK = threading.RLock()
_st6, _b6 = _wa.api_post_config({"auto_check_interval_min": 60})
check("维护 M-6：配置页保存只合并表单键（已建方案 / 自动切换不被清空）",
      _st6 == 200 and _m6_cur.get("auto_check_interval_min") == 60
      and _m6_cur.get("profiles") and _m6_cur.get("active_profile") == "宿舍"
      and _m6_cur.get("profiles_auto_switch") is True,
      repr({k: _m6_cur.get(k) for k in ("auto_check_interval_min", "profiles", "active_profile")})[:300])
_st6b, _b6b = _wa.api_post_config({})
check("维护 M-6：空 body 保存 → 400（不再把配置重置成默认值）",
      _st6b == 400 and _m6_cur.get("host") == "10.9.9.9", repr((_st6b, _b6b)))
if _m6_had_logger:
    _wa.logger = _m6_saved_logger
else:
    del _wa.logger
if _m6_had_load:
    _wa._load_config = _m6_saved_load
else:
    del _wa._load_config
if _m6_had_save:
    _wa._save_config = _m6_saved_save
else:
    del _wa._save_config
_wa.CONFIG_LOCK = _m6_saved_lock

# —— M-7/M-8/M-9：导入端点健壮性 + do_GET 异常兜底（审计 P1-2/P1-3/P2-1）——
# 同样先摘掉替身 logger：_log 在 logger 缺失时必须静默，不能反过来抛异常把响应吞掉。
_m7_had_logger = hasattr(_wa, "logger")
_m7_saved_logger = getattr(_wa, "logger", None)
_wa.logger = None
_p_m = 18969
_ph_m = "127.0.0.1:%d" % _p_m
_srv_m = _ThreadingHTTPServer(("127.0.0.1", _p_m), _wa._Handler)
threading.Thread(target=_srv_m.serve_forever, daemon=True).start()


def _post_import_m(body):
    _c = http.client.HTTPConnection("127.0.0.1", _p_m, timeout=30)
    _c.request("POST", "/api/config/import", body=body, headers={
        "Host": _ph_m, "Content-Type": "application/zip",
        "X-Requested-With": "DrcomUI", "Content-Length": str(len(body))})
    _r = _c.getresponse()
    _d = _r.read()
    _conn = _r.getheader("Connection")
    _c.close()
    try:
        return _r.status, json.loads(_d.decode("utf-8")), _conn
    except ValueError:
        return _r.status, {"_raw": _d[:120]}, _conn


# 加密 zip：把 local header 与 central directory 的 general-purpose bit0 置上
_m7 = bytearray(_mk_zip({"manifest.json": _good_manifest, "config.json": "{}"}))
_i = 0
while True:
    _i = _m7.find(b"PK\x03\x04", _i)
    if _i < 0:
        break
    _m7[_i + 6] |= 1
    _i += 4
_i = 0
while True:
    _i = _m7.find(b"PK\x01\x02", _i)
    if _i < 0:
        break
    _m7[_i + 8] |= 1
    _i += 4
_st7, _b7, _ = _post_import_m(bytes(_m7))
check("维护 M-7：加密 zip → 400 且给中文可读原因（不再 500 + 回显异常原文）",
      _st7 == 400 and "无法解析" in _b7.get("error", "") and "internal" not in _b7.get("error", ""),
      repr((_st7, _b7)))

# 超限 body（4MB 上限 + 1MB）：必须先排空再关连接，客户端才收得到 413
# 只超上限 1 字节：服务端把 body 全部排空后再关连接，Windows 不会因"未读数据"发 RST，
# 断言才稳定（曾经用 +1MB，排空上限 4MB 挡不住那 1MB，偶发 ConnectionAborted ✗）。
_st8, _b8, _conn8 = _post_import_m(b"\0" * (_wa.CONFIG_IMPORT_MAX_BYTES + 1))
check("维护 M-8：导入超限 → 客户端真的收到 413（不是连接被重置）",
      _st8 == 413 and (_conn8 or "").lower() == "close", repr((_st8, _b8, _conn8)))

# do_GET 异常兜底：未 _attach（logger 未注入）时也必须回 500 JSON，而不是零响应断连
_m9_saved = _wa.api_get_status
_wa.api_get_status = lambda: (_ for _ in ()).throw(RuntimeError("boom-M9"))
try:
    _c9 = http.client.HTTPConnection("127.0.0.1", _p_m, timeout=10)
    _c9.request("GET", "/api/status", headers={"Host": _ph_m})
    _r9 = _c9.getresponse()
    _d9 = _r9.read()
    _c9.close()
    _m9_ok = _r9.status == 500 and b"internal" in _d9
    _m9_detail = (_r9.status, _d9[:80])
except Exception as exc:  # noqa: BLE001
    _m9_ok = False
    _m9_detail = repr(exc)
finally:
    _wa.api_get_status = _m9_saved
check("维护 M-9：GET 异常分支不再因裸 logger NameError 断连（回 500 JSON）",
      _m9_ok, repr(_m9_detail))
_srv_m.shutdown()

# —— M-10：登录失败文案 / 兜底异常都必须擦掉 URL 与 password=（审计 C-10/C-17，I6）——
class _M10Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_m10_saved_open = _proto.urllib.request.urlopen


def _m10_bad_status(req, timeout=None):
    # BadStatusLine 的 str 会带上 req 的 URL —— 而登录 URL 里就有 user_password
    raise http.client.BadStatusLine("bad status: " + getattr(req, "full_url", ""))


_proto.urllib.request.urlopen = _m10_bad_status
try:
    _m10_ok, _m10_msg = _proto.login("10.0.0.1", "2023001234", "", "#Demo-Pwd-0000#", "10.0.0.2", "")
finally:
    _proto.urllib.request.urlopen = _m10_saved_open
check("维护 M-10：登录遇到 HTTPException（BadStatusLine）也被安全化（无密码 / 无 URL）",
      _m10_ok is False and "#Demo-Pwd-0000#" not in _m10_msg
      and "password=" not in _m10_msg and "http://" not in _m10_msg,
      repr(_m10_msg))


def _m10_echo(req, timeout=None):
    return _M10Resp(b'dr1({"result":0,"msg":"bad: user_password=#Demo-Pwd-0000# at http://10.0.0.1/x"})')


_proto.urllib.request.urlopen = _m10_echo
try:
    _m10_ok2, _m10_msg2 = _proto.login("10.0.0.1", "2023001234", "", "#Demo-Pwd-0000#", "10.0.0.2", "")
finally:
    _proto.urllib.request.urlopen = _m10_saved_open
check("维护 M-10：服务端 msg 回显密码参数时被擦除（I6：日志/界面零明文凭据）",
      _m10_ok2 is False and "#Demo-Pwd-0000#" not in _m10_msg2
      and "password=***" in _m10_msg2 and "http://" not in _m10_msg2,
      repr(_m10_msg2))


if _m7_had_logger:
    _wa.logger = _m7_saved_logger
else:
    del _wa.logger


# ============================================================
# 维护第二轮（2026-11 升级链审计：A2 / G1 / G2 / E1 / E2 / F3 / B1+B2 / C1 / G5 / E4）
# 对应 _维护报告/audit/upgrade.md；断言测行为（打桩 / 替身），**不联网、不真装**。
# 每条都先钉「旧行为会怎样错」，再钉「现在必须怎样」。
# ============================================================
import hashlib  # noqa: E402
import types  # noqa: E402

_R2_MISSING = object()
_R2_NAMES = (
    "LOG_DIR", "UPGRADE_LOG_FILE", "logger", "BASE_DIR", "NSSM_PATH",
    "STATE", "STATE_LOCK", "UPDATE_LOCK", "STOP_EVENT",
    "_load_config", "_check_github_latest", "_check_disk_free_mb", "_now_iso",
    "_probe_local_health", "_download_installer", "_do_download_installer",
    "_launch_installer", "_set_nssm_appexit", "_read_nssm_appexit",
    "_ensure_nssm_appexit_sane", "_ensure_tray_autostart_sane", "_cleanup_update_leftovers",
    "_start_deferred_runner_recheck", "_deferred_runner_recheck",
    "_do_check_now", "subprocess", "GITHUB_DOWNLOAD_MIRRORS",
    "_auto_update_disabled_logged",
)
_r2_saved = {k: au.__dict__.get(k, _R2_MISSING) for k in _R2_NAMES}
_r2_saved_temp = os.environ.get("TEMP")

_r2_root = tempfile.mkdtemp(prefix="drcom_r2_")
_r2_app = os.path.join(_r2_root, "app")
_r2_logs = os.path.join(_r2_root, "logs")
_r2_bin = os.path.join(_r2_root, "bin")
for _d in (_r2_app, _r2_logs, _r2_bin):
    os.makedirs(_d, exist_ok=True)
os.environ["TEMP"] = _r2_root
_r2_state = {}
_r2_attach_logger = logging.getLogger("smoke_auto_update_r2")
_r2_attach_logger.addHandler(logging.NullHandler())
au._attach(
    logger=_r2_attach_logger, state=_r2_state, state_lock=threading.Lock(), update_lock=threading.Lock(),
    tools_dir=os.path.join(_r2_root, "tools"), nssm_path=os.path.join(_r2_root, "tools", "nssm.exe"),
    upgrade_log_file=os.path.join(_r2_logs, "upgrade.log"), log_dir=_r2_logs, base_dir=_r2_app,
    load_config=lambda: {}, save_config=lambda *a: None,
    now_iso=lambda: "2026-11-16T00:00:00", stop_event=threading.Event())
_r2_nssm_stub = os.path.join(_r2_root, "nssm_stub.exe")
pathlib.Path(_r2_nssm_stub).write_bytes(b"stub")   # _ensure_nssm_appexit_sane 会先判 isfile


def _r2_write_rc(lines):
    with open(au._rc_path(), "w", encoding="utf-8") as _f:
        _f.write("\n".join(lines) + "\n")


def _r2_read_log():
    _p = au.UPGRADE_LOG_FILE
    return pathlib.Path(_p).read_text(encoding="utf-8") if os.path.isfile(_p) else ""


def _r2_clear_files():
    for _p in (au._rc_path(), au._wrapper_path(), au._health_probe_path(), au._rollback_script_path()):
        try:
            if os.path.isfile(_p):
                os.remove(_p)
        except OSError:
            pass


def _r2_run_report(lines):
    """清掉旧日志 / rc → 写入合成 rc → 跑一次收尾读取，返回 (reported, upgrade.log 全文)。"""
    for _p in (au.UPGRADE_LOG_FILE, au._rc_path()):
        try:
            if os.path.isfile(_p):
                os.remove(_p)
        except OSError:
            pass
    _r2_write_rc(lines)
    _reported = au._report_update_runner_result(wait_sec=0)
    return _reported, _r2_read_log()


# —— B1：预发布后缀必须「低于」同版本正式版；fix/hotfix/patch 维持设计意图 999 ——
_r2_cv = au._compare_versions
check("维护 R2-B1：rc / alpha / beta / pre / dev 低于同版本正式版",
      _r2_cv("2.1.3.0", "2.1.3.0-rc1") == 1
      and _r2_cv("2.1.3.1", "2.1.3.1-beta") == 1
      and _r2_cv("2.1.3.0", "2.1.3.0-alpha2") == 1
      and _r2_cv("2.1.3.0", "2.1.3.0-pre") == 1
      and _r2_cv("2.1.3.0", "2.1.3.0-dev") == 1,
      repr([_r2_cv("2.1.3.0", v) for v in ("2.1.3.0-rc1", "2.1.3.0-alpha2", "2.1.3.0-pre", "2.1.3.0-dev")]))
check("维护 R2-B1：fix / hotfix / patch 仍高于同主版本（设计意图 999 不变）",
      _r2_cv("2.1.3.0", "2.1.3-fix") == -1
      and _r2_cv("2.1.3.0", "2.1.3-hotfix") == -1
      and _r2_cv("2.1.3.0", "2.1.3-patch") == -1)
check("维护 R2-B1：普通版本比较没被改坏（三段==四段 / 递增 / v 前缀）",
      _r2_cv("2.1.3", "2.1.3.0") == 0 and _r2_cv("2.1.3.0", "2.1.3.1") == -1
      and _r2_cv("2.1.3.0", "v2.1.4") == -1 and au._parse_version("abc") == ())

# —— B2：远端版本无法解析 → error，不再静默「已是最新」；白名单两处共用 ——
_r2_saved_check = au._check_github_latest
try:
    au._check_github_latest = lambda: ("abc", "https://example.invalid/x.exe", None, None, None)
    au._set_update_state(update_state=None, update_last_error=None, update_available=True)
    _r2_chk = au._do_check_now()
    _r2_chk_ok = (isinstance(_r2_chk, dict) and _r2_chk.get("ok") is False
                  and au._get_update_field("update_state") == "error"
                  and "无法解析" in (au._get_update_field("update_last_error") or "")
                  and au._get_update_field("update_available") is False)
    _r2_chk_snapshot = (au._get_update_field("update_state"), au._get_update_field("update_last_error"))
    # ② 白名单分支：带路径穿越的 tag
    au._check_github_latest = lambda: ("../evil", "https://example.invalid/x.exe", None, None, None)
    au._set_update_state(update_state=None, update_last_error=None)
    _r2_chk2 = au._do_check_now()
    _r2_chk2_ok = (au._get_update_field("update_state") == "error"
                   and "无法解析" in (au._get_update_field("update_last_error") or ""))
finally:
    au._check_github_latest = _r2_saved_check
check("维护 R2-B2：远端版本无法解析 → update_state=error + 中文 last_error（不再当「已是最新」）",
      _r2_chk_ok and _r2_chk2_ok, repr(_r2_chk_snapshot))
check("维护 R2-B2：版本白名单抽成 _valid_remote_version()，_do_update_now / _do_check_now 共用",
      callable(getattr(au, "_valid_remote_version", None))
      and au._valid_remote_version("2.1.4.0") is True
      and au._valid_remote_version("../evil") is False
      and src_upd.count("_valid_remote_version(") >= 3,
      str(src_upd.count("_valid_remote_version(")))

# —— E4 / I9：安装包暂存名与 setup.iss 的产物名一致，清理前缀同步 ——
check("维护 R2-E4：暂存名改成 StardustFlashLink-Setup-v{ver}.exe（I9 实际产物名）",
      au.INSTALLER_FILENAME_PATTERN.format(ver="2.1.4.0") == "StardustFlashLink-Setup-v2.1.4.0.exe"
      and au.INSTALLER_FILENAME_PREFIX == "StardustFlashLink-Setup-v"
      and au.LEGACY_INSTALLER_FILENAME_PREFIX == "DrcomAutoLogin-Setup-v"
      and '"StardustFlashLink-Setup-v{ver}.exe"' in src_upd)
if os.name != "nt":
    print("SKIP  维护 R2-E4：清理残留行为级断言（非 Windows）")
else:
    _r2_pkg_names = (
        "StardustFlashLink-Setup-v%s.exe" % au.VERSION,
        "DrcomAutoLogin-Setup-v%s.exe" % au.VERSION,      # 历史产物名
        "StardustFlashLink-Setup-v9.9.8.exe",             # 别的版本 → 必须留着
    )
    for _n in _r2_pkg_names:
        pathlib.Path(os.path.join(_r2_root, _n)).write_bytes(b"x")
    au._cleanup_update_leftovers()
    check("维护 R2-E4：清理升级残留同时认新老前缀（只删本版本，别版本的包不碰）",
          not os.path.isfile(os.path.join(_r2_root, _r2_pkg_names[0]))
          and not os.path.isfile(os.path.join(_r2_root, _r2_pkg_names[1]))
          and os.path.isfile(os.path.join(_r2_root, _r2_pkg_names[2])),
          repr(sorted(os.listdir(_r2_root))))
    try:
        os.remove(os.path.join(_r2_root, _r2_pkg_names[2]))
    except OSError:
        pass

# —— A2a：升级前健康基线（探不通就中止升级，绝不继续装）——
_r2_saved_disk = au._check_disk_free_mb
_r2_saved_cfg = au._load_config
_r2_saved_probe = au._probe_local_health
_r2_saved_launch = au._launch_installer
_r2_launch_hits = []
au.NSSM_PATH = _r2_nssm_stub
au._check_disk_free_mb = lambda: 99999
au._load_config = lambda: {"update_min_free_disk_mb": 0, "ui_port": 8848}
au._probe_local_health = lambda ui_port=None, timeout_sec=None: (
    False, "ConnectionRefusedError: [WinError 10061] 由于目标计算机积极拒绝，无法连接")
au._check_github_latest = lambda: (_ for _ in ()).throw(AssertionError("不健康时不该去 check GitHub"))
au._launch_installer = lambda *a, **k: _r2_launch_hits.append(("launch", a)) or {"mode": "stub"}
try:
    _r2_res_bad = au._do_update_now()
finally:
    au._check_disk_free_mb = _r2_saved_disk
    au._load_config = _r2_saved_cfg
    au._probe_local_health = _r2_saved_probe
    au._launch_installer = _r2_saved_launch
check("维护 R2-A2a：升级前健康基线不通 → 中止升级（不下载、不装）+ 中文提示先修服务",
      isinstance(_r2_res_bad, dict) and _r2_res_bad.get("ok") is False
      and "不健康" in (_r2_res_bad.get("error") or "")
      and "先修复服务" in (_r2_res_bad.get("error") or "")
      and not _r2_launch_hits
      and au._get_update_field("update_state") == "error"
      and "不健康" in (au._get_update_field("update_last_error") or ""),
      repr((_r2_res_bad, _r2_launch_hits)))
# 基线通过的路径要能继续（本用例远端 == 本机 → 走到「已是最新」，证明没被基线卡死）
au._check_disk_free_mb = lambda: 99999
au._load_config = lambda: {"update_min_free_disk_mb": 0, "ui_port": 8848}
au._probe_local_health = lambda ui_port=None, timeout_sec=None: (True, "http=200 ok=true")
au._check_github_latest = lambda: (au.VERSION, "https://example.invalid/x.exe", "0" * 64, 1, None)
au._launch_installer = lambda *a, **k: _r2_launch_hits.append(("launch", a)) or {"mode": "stub"}
try:
    _r2_res_ok = au._do_update_now()
finally:
    au._check_disk_free_mb = _r2_saved_disk
    au._load_config = _r2_saved_cfg
    au._probe_local_health = _r2_saved_probe
    au._check_github_latest = _r2_saved_check
    au._launch_installer = _r2_saved_launch
check("维护 R2-A2a：健康基线通过才继续（本例走到「已是最新」，不再卡住升级流程）",
      "已是最新" in (_r2_res_ok.get("error") or "") and not _r2_launch_hits,
      repr(_r2_res_ok))
check("维护 R2-A2a：健康基线复用端口规范化与探针超时口径",
      "def _probe_local_health(ui_port=None, timeout_sec=None)" in src_upd
      and "_normalize_ui_port(ui_port)" in src_upd
      and "HEALTH_PROBE_TIMEOUT_SEC" in src_upd)
# 行为级：真跑一次 _probe_local_health（对着假 /api/health 服务 → 健康；空端口 → 不健康）
_r2_srv_ok = _ThreadingHTTPServer(("127.0.0.1", 0), _HealthStub)
_r2_port_ok = _r2_srv_ok.server_address[1]
threading.Thread(target=_r2_srv_ok.serve_forever, daemon=True).start()
_r2_srv_free = _ThreadingHTTPServer(("127.0.0.1", 0), _HealthStub)
_r2_port_free = _r2_srv_free.server_address[1]
_r2_srv_free.server_close()          # 关掉 → 端口空出来（探测必须判「不健康」）
try:
    _r2_h_ok, _r2_h_detail = au._probe_local_health(_r2_port_ok, timeout_sec=2)
    _r2_h_bad, _r2_h_bad_detail = au._probe_local_health(_r2_port_free, timeout_sec=1)
finally:
    _r2_srv_ok.shutdown()
check("维护 R2-A2a：真实探测行为 —— 假 /api/health 在 → 健康；空端口 → 不健康",
      _r2_h_ok is True and "http=200" in _r2_h_detail and _r2_h_bad is False,
      repr((_r2_h_ok, _r2_h_detail, _r2_h_bad, _r2_h_bad_detail)))


# —— A2b：执行器归因（端口被占 → PORT_BUSY + 跳过回滚；回滚=OK 但仍不健康 → USELESS）——
_r2_wrap_txt = au._build_update_wrapper(
    r"C:\T\StardustFlashLink-Setup-v9.9.9.exe", r"D:\App", r"C:\T\l.log", r"C:\T\r.txt", "MyTask",
    python_path=r"C:\App\python\python.exe", probe_path=r"C:\T\drcom_health_probe.py", ui_port=8848,
    backup_dir=r"D:\App\backup\1.2.3", rollback_path=r"C:\T\drcom_rollback.py")
check("维护 R2-A2b：执行器先归因端口占用（find FATAL/bind），再决定回滚；USELESS 判定在 after-rollback 探针之后",
      _r2_wrap_txt.index('find /i "FATAL"') < _r2_wrap_txt.index("--tag auto")
      and _r2_wrap_txt.index('find /i "bind"') < _r2_wrap_txt.index("--tag auto")
      and _r2_wrap_txt.index("health=PORT_BUSY") < _r2_wrap_txt.index("--tag auto")
      and _r2_wrap_txt.index("rollback=USELESS") > _r2_wrap_txt.index("--tag after-rollback")
      and _r2_wrap_txt.index('find /i "rollback=OK"') > _r2_wrap_txt.index("--tag after-rollback")
      and '"%APP%' in _r2_wrap_txt,
      "PORT_BUSY@%d auto@%d after@%d" % (_r2_wrap_txt.index("health=PORT_BUSY"),
                                         _r2_wrap_txt.index("--tag auto"),
                                         _r2_wrap_txt.index("--tag after-rollback")))

if os.name != "nt":
    print("SKIP  维护 R2-A2b：执行器行为级断言（非 Windows，没有 cmd）")
    print("SKIP  维护 R2-E1：schtasks 行为级断言（非 Windows）")
else:
    # 替身：安装器 / sc / schtasks / ping 全部换成「秒退」的存量 exe（复制 hostname.exe），
    # 只验证执行器的**判定与落盘**，绝不真装、绝不碰真服务。
    _r2_host_stub = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "hostname.exe")
    for _n in ("ping.exe", "sc.exe", "schtasks.exe", "installer.exe"):
        shutil.copy2(_r2_host_stub, os.path.join(_r2_bin, _n))
    _r2_probe_py = os.path.join(_r2_root, "probe_fail.py")
    _r2_probe_src = """import sys
a = sys.argv[1:]
rc = a[a.index('--rc') + 1] if '--rc' in a else ''
if rc:
    open(rc, 'a', encoding='utf-8').write('health=FAIL' + chr(10))
sys.exit(1)
"""
    pathlib.Path(_r2_probe_py).write_text(_r2_probe_src, encoding="utf-8")
    _r2_rollback_src = """import os
import sys
a = sys.argv[1:]
rc = a[a.index('--rc') + 1] if '--rc' in a else ''
open(rc, 'a', encoding='utf-8').write('rollback=OK' + chr(10) + 'rollback_version=1.2.3' + chr(10))
open(os.path.join(os.path.dirname(rc), 'rollback_called.marker'), 'w').write('1')
sys.exit(0)
"""

    def _r2_run_wrapper(tag, app_dir, busy_log=None):
        """把生成的执行器真跑一遍（全部替身）；返回 rc 里的非空行。"""
        _rollback = os.path.join(_r2_root, "rollback_ok_%s.py" % tag)
        pathlib.Path(_rollback).write_text(_r2_rollback_src, encoding="utf-8")
        # 执行器收尾会 del 掉探针与回滚脚本 —— 每次重跑都要重新落盘（同一个替身复用）
        pathlib.Path(_r2_probe_py).write_text(_r2_probe_src, encoding="utf-8")
        _bk = os.path.join(app_dir, "backup", "1.2.3")
        os.makedirs(os.path.join(app_dir, "logs"), exist_ok=True)
        os.makedirs(_bk, exist_ok=True)
        _stderr_log = os.path.join(app_dir, "logs", "service_stderr.log")
        if busy_log is None:
            if os.path.isfile(_stderr_log):
                os.remove(_stderr_log)
        else:
            pathlib.Path(_stderr_log).write_text(busy_log, encoding="utf-8")
        _marker = os.path.join(_r2_root, "rollback_called.marker")
        if os.path.isfile(_marker):
            os.remove(_marker)
        _rc = os.path.join(_r2_root, "rc_%s.txt" % tag)
        _log = os.path.join(_r2_root, "installer_%s.log" % tag)
        if os.path.isfile(_rc):
            os.remove(_rc)
        _script = au._build_update_wrapper(
            os.path.join(_r2_bin, "installer.exe"), app_dir, _log, _rc, "DrcomAutoLogin-AutoUpdate",
            sc_path=os.path.join(_r2_bin, "sc.exe"),
            schtasks_path=os.path.join(_r2_bin, "schtasks.exe"),
            python_path=sys.executable, probe_path=_r2_probe_py, ui_port=18899,
            backup_dir=_bk, rollback_path=_rollback)
        _wrapper = os.path.join(_r2_root, "wrap_%s.cmd" % tag)
        au._write_update_wrapper(_wrapper, _script)
        _env = dict(os.environ)
        _env["PATH"] = _r2_bin + os.pathsep + _env.get("PATH", "")
        _env["PYTHONIOENCODING"] = "utf-8"
        subprocess.run(["cmd", "/c", _wrapper], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=120, env=_env)
        if not os.path.isfile(_rc):
            return []
        return [l.strip() for l in pathlib.Path(_rc).read_text(encoding="utf-8", errors="replace").splitlines()
                if l.strip()]

    _r2_lines_busy = _r2_run_wrapper(
        "busy", _r2_app, busy_log="[x] FATAL: bind 127.0.0.1:18899 failed\n")
    check("维护 R2-A2b：端口被占 → 落 health=PORT_BUSY + rollback=SKIP（不回滚，旧代码同样绑不上）",
          "health=PORT_BUSY" in _r2_lines_busy and "rollback=SKIP" in _r2_lines_busy
          and not any(l.startswith("rollback=OK") for l in _r2_lines_busy)
          and not os.path.isfile(os.path.join(_r2_root, "rollback_called.marker"))
          and any(l.startswith("service=") for l in _r2_lines_busy),
          repr(_r2_lines_busy))
    _r2_lines_useless = _r2_run_wrapper("useless", _r2_app)
    _r2_rb_lines = [l for l in _r2_lines_useless if l.startswith("rollback=")]
    check("维护 R2-A2b：回滚=OK 但回滚后仍不健康 → 追加 rollback=USELESS（最后一条为准）",
          _r2_rb_lines == ["rollback=OK", "rollback=USELESS"]
          and os.path.isfile(os.path.join(_r2_root, "rollback_called.marker")),
          repr(_r2_lines_useless))

# —— A2c：收尾上报必须区分 OK / FAIL / SKIP / USELESS ——
_r2_reported, _r2_report_log = _r2_run_report(
    ["installer_rc=0", "health=FAIL", "health=FAIL", "rollback=OK",
     "rollback_version=1.2.3", "rollback=USELESS", "service=STOPPED"])
check("维护 R2-A2c：rollback=USELESS → update_state=error + 明说「回滚后仍不健康，可能需要人工处理」",
      _r2_reported is True and au._get_update_field("update_state") == "error"
      and "回滚后仍不健康" in (au._get_update_field("update_last_error") or "")
      and "人工处理" in (au._get_update_field("update_last_error") or ""),
      repr((au._get_update_field("update_state"), au._get_update_field("update_last_error"))))
check("维护 R2-A2c：USELESS 绝不宣称回滚成功（日志是 ERROR +「没有奏效」）",
      "没有奏效" in _r2_report_log and "已自动回滚到备份版本" not in _r2_report_log,
      repr(_r2_report_log[-160:]))
_r2_reported_ok, _r2_report_ok = _r2_run_report(
    ["installer_rc=0", "health=FAIL", "health=OK", "rollback=OK", "rollback_version=1.2.3"])
check("维护 R2-A2c：rollback=OK 仍按成功口径上报（不误伤既有分支）",
      _r2_reported_ok is True and "已自动回滚" in _r2_report_ok
      and "已自动回滚" in (au._get_update_field("update_last_error") or ""),
      repr((_r2_report_ok[-140:], au._get_update_field("update_last_error"))))
_r2_reported_skip, _r2_report_skip = _r2_run_report(
    ["installer_rc=0", "health=PORT_BUSY", "rollback=SKIP"])
check("维护 R2-A2c：PORT_BUSY 单独给中文结论（先解决端口冲突/改 ui_port）",
      "端口被占用" in _r2_report_skip and "没做自动回滚" in _r2_report_skip,
      repr(_r2_report_skip[-160:]))

# —— G1：启动钩子延迟复核（只读一次 rc → 75 秒后再读一次并删 rc 防重复上报）——
check("维护 R2-G1：延迟复核函数存在、延迟约 75 秒、daemon 线程可单独调用",
      callable(getattr(au, "_deferred_runner_recheck", None))
      and callable(getattr(au, "_start_deferred_runner_recheck", None))
      and au.DEFERRED_RECHECK_DELAY_SEC >= 60
      and "_start_deferred_runner_recheck()" in src_upd
      and "_report_update_runner_result(wait_sec=8)" in src_upd)   # 同步等待保留（防与探针互相等）
if os.path.isfile(au._rc_path()):
    os.remove(au._rc_path())
check("维护 R2-G1：没有 rc 时延迟复核什么都不做（不误报、不抛异常）",
      au._deferred_runner_recheck(0) is False)
for _p in (au.UPGRADE_LOG_FILE, au._rc_path()):
    try:
        if os.path.isfile(_p):
            os.remove(_p)
    except OSError:
        pass
_r2_write_rc(["installer_rc=0", "health=FAIL", "rollback=FAIL"])
au._set_update_state(update_state=None, update_last_error=None, update_progress_message="")
_r2_defer_thread = au._start_deferred_runner_recheck(0)
_r2_defer_thread.join(10)
check("维护 R2-G1：延迟复核线程真跑了 → update_state=error + last_error 含「回滚」",
      _r2_defer_thread.daemon and not _r2_defer_thread.is_alive()
      and au._get_update_field("update_state") == "error"
      and "回滚" in (au._get_update_field("update_last_error") or ""),
      repr((au._get_update_field("update_state"), au._get_update_field("update_last_error"))))
check("维护 R2-G1：复核读完就删 rc（同一份结果不会被下一次启动重复上报）",
      not os.path.isfile(au._rc_path()))

# —— G2：TEMP 不可用只跳过「旧式备份扫描」，后段自愈必须照常 ——
_r2_no_temp = os.path.join(_r2_root, "no_such_temp_dir")
os.environ["TEMP"] = _r2_no_temp
_r2_calls = []
_r2_saved_cleanup = au._cleanup_update_leftovers
_r2_saved_appexit = au._ensure_nssm_appexit_sane
_r2_saved_tray = au._ensure_tray_autostart_sane
_r2_saved_defer_start = au._start_deferred_runner_recheck
au._cleanup_update_leftovers = lambda: _r2_calls.append("cleanup")
au._ensure_nssm_appexit_sane = lambda: (_r2_calls.append("appexit"), "Ignore")[1]
au._ensure_tray_autostart_sane = lambda: (_r2_calls.append("tray"), {"action": "keep", "want": True, "current": "x"})[1]
au._start_deferred_runner_recheck = lambda *a, **k: _r2_calls.append("defer")
try:
    au._post_upgrade_startup()
finally:
    au._cleanup_update_leftovers = _r2_saved_cleanup
    au._ensure_nssm_appexit_sane = _r2_saved_appexit
    au._ensure_tray_autostart_sane = _r2_saved_tray
    au._start_deferred_runner_recheck = _r2_saved_defer_start
    os.environ["TEMP"] = _r2_root
check("维护 R2-G2：TEMP 不可用 → 只跳过旧式备份；备份清理 / 升级结论 / AppExit / 托盘 / 延迟复核照常执行",
      {"cleanup", "appexit", "tray", "defer"} <= set(_r2_calls), repr(_r2_calls))
check("维护 R2-G2：TEMP 不可用会明确记 WARN（不是静默吞掉整段自愈）",
      "%TEMP% 不可用" in _r2_read_log(), repr(_r2_read_log()[-140:]))

# —— E1：schtasks /run 失败 → 退回 DETACHED 直启前先删一次性任务 ——
if os.name == "nt":
    class _R2Proc(object):
        def __init__(self, rc):
            self.returncode = rc
            self.pid = 4321

    _r2_saved_subproc = au.subprocess
    _r2_e1_calls = []
    _r2_e1_delete_rc = [0]

    def _r2_fake_subproc_run(cmd, **kwargs):
        _r2_e1_calls.append(list(cmd))
        if "/delete" in cmd:
            return _R2Proc(_r2_e1_delete_rc[0])
        if "/create" in cmd:
            return _R2Proc(0)
        if "/run" in cmd:
            return _R2Proc(1)          # /run 失败 → 必须走「删任务 + 退回直启」
        return _R2Proc(0)

    def _r2_with_subproc_shim(fn):
        au.subprocess = types.SimpleNamespace(
            run=_r2_fake_subproc_run,
            Popen=lambda *a, **k: _R2Proc(4321),
            TimeoutExpired=_r2_saved_subproc.TimeoutExpired,
            DEVNULL=_r2_saved_subproc.DEVNULL,
        )
        try:
            return fn()
        finally:
            au.subprocess = _r2_saved_subproc

    _r2_launch = _r2_with_subproc_shim(
        lambda: au._launch_installer(os.path.join(_r2_root, "Fake-Setup.exe")))
    _r2_e1_cmds = [" ".join(c) for c in _r2_e1_calls]
    check("维护 R2-E1：schtasks /run 失败 → 先 /delete 一次性任务，再退回 DETACHED 直启",
          _r2_launch.get("mode") == "detached"
          and any("/create" in c for c in _r2_e1_cmds)
          and any("/run" in c for c in _r2_e1_cmds)
          and any("/delete" in c and au.UPDATE_TASK_NAME in c for c in _r2_e1_cmds)
          and [i for i, c in enumerate(_r2_e1_cmds) if "/create" in c][0]
          < [i for i, c in enumerate(_r2_e1_cmds) if "/delete" in c][0],
          repr(_r2_e1_cmds))
    _r2_e1_delete_rc[0] = 1             # /delete 自己也失败 → 不致命，仍退回直启
    _r2_launch2 = _r2_with_subproc_shim(
        lambda: au._launch_installer(os.path.join(_r2_root, "Fake-Setup.exe")))
    check("维护 R2-E1：/delete 失败不致命（记 WARN，仍退回 DETACHED 直启）",
          _r2_launch2.get("mode") == "detached", repr(_r2_launch2))
    _r2_clear_files()

# —— E2：AppExit 自愈判据 =「Default 与 0 两个都等于 Ignore」——
def _r2_appexit_case(values):
    """用替身跑一次 _ensure_nssm_appexit_sane，返回 (返回值, nssm set 调用, 终值)。"""
    _state = dict(values)
    _calls = []

    def _fake_read(subvalue=au.APPEXIT_DEFAULT_VALUE):
        return _state.get(subvalue)

    def _fake_set(subparam, value):
        _calls.append((subparam, value))
        _state["" if subparam == "Default" else subparam] = value
        return True

    _saved_read, _saved_set, _saved_nssm = au._read_nssm_appexit, au._set_nssm_appexit, au.NSSM_PATH
    au._read_nssm_appexit = _fake_read
    au._set_nssm_appexit = _fake_set
    au.NSSM_PATH = _r2_nssm_stub
    try:
        _fixed = au._ensure_nssm_appexit_sane()
    finally:
        au._read_nssm_appexit = _saved_read
        au._set_nssm_appexit = _saved_set
        au.NSSM_PATH = _saved_nssm
    return _fixed, _calls, _state


_r2_ax_fixed1, _r2_ax_calls1, _r2_ax_state1 = _r2_appexit_case({"": "Restart", "0": "Ignore"})
check("维护 R2-E2：Default=Restart → Default 与 0 两个子值都纠偏成 Ignore（绝不写 Restart）",
      _r2_ax_fixed1 == "Ignore"
      and ("Default", "Ignore") in _r2_ax_calls1 and ("0", "Ignore") in _r2_ax_calls1
      and all(v != "Restart" for _, v in _r2_ax_calls1)
      and _r2_ax_state1 == {"": "Ignore", "0": "Ignore"},
      repr((_r2_ax_calls1, _r2_ax_state1)))
_r2_ax_fixed2, _r2_ax_calls2, _r2_ax_state2 = _r2_appexit_case({"": "Ignore", "0": None})
check("维护 R2-E2：缺 0 子值（或 0 不是 Ignore）也要纠偏 —— 判据是「两个都等于 Ignore」",
      ("0", "Ignore") in _r2_ax_calls2 and _r2_ax_state2["0"] == "Ignore",
      repr(_r2_ax_calls2))
_r2_ax_fixed3, _r2_ax_calls3, _r2_ax_state3 = _r2_appexit_case({"": "Ignore", "0": "Ignore"})
check("维护 R2-E2：已合规（Default Ignore + 0 Ignore）→ 一次 nssm set 都不发",
      _r2_ax_calls3 == [] and _r2_ax_fixed3 == "Ignore", repr(_r2_ax_calls3))
check("维护 R2-E2：读取函数支持读子值（签名钉住，默认仍读 (默认) 值）",
      "def _read_nssm_appexit(subvalue=APPEXIT_DEFAULT_VALUE)" in src_upd
      and '_read_nssm_appexit("0")' in src_upd)

# —— F3：默认关闭时后台线程只记一次 INFO；等待 30s → 300s；启用行为不变 ——
class _R2Logger(object):
    def __init__(self):
        self.lines = []

    def info(self, msg, *a):
        self.lines.append(msg % a if a else msg)

    def warning(self, msg, *a):
        self.lines.append("WARN " + (msg % a if a else msg))

    def error(self, msg, *a):
        self.lines.append("ERROR " + (msg % a if a else msg))

    def exception(self, msg, *a):
        self.lines.append("EXC " + (msg % a if a else msg))


class _R2Stop(object):
    def __init__(self, plan):
        self.plan = list(plan)
        self.waits = []

    def is_set(self):
        return False

    def wait(self, sec):
        self.waits.append(sec)
        return self.plan.pop(0) if self.plan else True


_r2_loop_logger = _R2Logger()
_r2_saved_loop = {k: au.__dict__.get(k, _R2_MISSING) for k in
                  ("logger", "_load_config", "_do_check_now", "STOP_EVENT", "_auto_update_disabled_logged")}
au.logger = _r2_loop_logger
au._load_config = lambda: {"auto_update_enabled": False}
_r2_stop_disabled = _R2Stop([False, False, True])
au.STOP_EVENT = _r2_stop_disabled
au._auto_update_disabled_logged = False
try:
    au._auto_update_loop()
    _r2_disabled_logs = [l for l in _r2_loop_logger.lines if "auto_update_enabled" in l]
    au._load_config = lambda: {"auto_update_enabled": True, "update_check_interval_hours": 6}
    au._do_check_now = lambda: {"ok": True, "update_available": False}
    _r2_stop_enabled = _R2Stop([False, True])
    au.STOP_EVENT = _r2_stop_enabled
    au._auto_update_loop()
finally:
    for _k, _v in _r2_saved_loop.items():
        if _v is _R2_MISSING:
            au.__dict__.pop(_k, None)
        else:
            au.__dict__[_k] = _v
check("维护 R2-F3：默认关闭时后台线程只记一条 INFO（不再每 30 秒刷一条）",
      len(_r2_disabled_logs) == 1, repr(_r2_disabled_logs))
check("维护 R2-F3：关闭时等待改成 300 秒；启用时仍按 update_check_interval_hours 周期（行为不变）",
      au.AUTO_UPDATE_DISABLED_WAIT_SEC == 300
      and _r2_stop_disabled.waits[:3] == [30, 300, 300]
      and _r2_stop_enabled.waits == [30, 6 * 3600],
      repr((_r2_stop_disabled.waits, _r2_stop_enabled.waits)))

# —— C1：SHA256 校验挪进镜像循环（每镜像下完即校验，不符换下一个）——
_r2_saved_download = au._download_installer
_r2_saved_do_download = au._do_download_installer
_r2_saved_mirrors = au.GITHUB_DOWNLOAD_MIRRORS
_r2_good = b"GOOD-INSTALLER-BYTES" * 64
_r2_good_digest = hashlib.sha256(_r2_good).hexdigest()
_r2_dest = os.path.join(_r2_root, "dl_test.exe")
_r2_tried = []


def _r2_fake_mixed_download(url, dest_path, expected_size, cb, timeout):
    _r2_tried.append(url)
    if "gh-proxy.com" not in url:  # 第一个镜像（主源）返回 200 + HTML 错误页（校园网/限流常见）
        pathlib.Path(dest_path).write_bytes(b"<html>429 Too Many Requests</html>")
        return 31
    pathlib.Path(dest_path).write_bytes(_r2_good)   # 下一个镜像给真包 → 必须换成它
    return len(_r2_good)


au.GITHUB_DOWNLOAD_MIRRORS = (None, "https://gh-proxy.com")
au._do_download_installer = _r2_fake_mixed_download
try:
    _r2_written = au._download_installer("https://example.invalid/pkg.exe", _r2_dest,
                                         len(_r2_good), None, expected_sha256=_r2_good_digest)
    _r2_content_ok = pathlib.Path(_r2_dest).read_bytes() == _r2_good
finally:
    au._download_installer = _r2_saved_download
    au._do_download_installer = _r2_saved_do_download
    au.GITHUB_DOWNLOAD_MIRRORS = _r2_saved_mirrors
check("维护 R2-C1：镜像逐个校验 —— 坏镜像（HTML 错误页）删掉换下一个，最终文件好且校验通过",
      _r2_written == len(_r2_good) and _r2_content_ok
      and any("gh-proxy.com" in u for u in _r2_tried),
      repr((_r2_tried, _r2_written)))


def _r2_fake_bad_download(url, dest_path, expected_size, cb, timeout):
    _r2_tried.append(url)
    pathlib.Path(dest_path).write_bytes(b"junk-not-an-installer")
    return 22


au.GITHUB_DOWNLOAD_MIRRORS = (None, "https://gh-proxy.com")
au._do_download_installer = _r2_fake_bad_download
if os.path.isfile(_r2_dest):
    os.remove(_r2_dest)
_r2_sha_error = ""
try:
    au._download_installer("https://example.invalid/pkg.exe", _r2_dest, 22, None,
                           expected_sha256=_r2_good_digest)
except OSError as _exc:
    _r2_sha_error = str(_exc)
finally:
    au._download_installer = _r2_saved_download
    au._do_download_installer = _r2_saved_do_download
    au.GITHUB_DOWNLOAD_MIRRORS = _r2_saved_mirrors
check("维护 R2-C1：所有镜像校验都不符 → 报错且不留下被篡改的安装器（逐镜像清理）",
      "SHA256" in _r2_sha_error and not os.path.isfile(_r2_dest), repr(_r2_sha_error))
check("维护 R2-C1：digest 只信主源（下载镜像仍保留，API 镜像禁用）",
      "gh-proxy.com" in src_upd.split("GITHUB_DOWNLOAD_MIRRORS")[1]
      and "gh-proxy" not in src_upd.split("GITHUB_API_MIRRORS = (")[1].split(")")[0]
      and "expected_sha256" in src_upd)

# —— G5：执行器落盘不再出现 CR CR LF（newline="" + 已拼好的 CRLF）——
_r2_wrap_path = os.path.join(_r2_root, "wrap_g5.cmd")
_r2_wrap_enc = au._write_update_wrapper(_r2_wrap_path, _r2_wrap_txt)
_r2_wrap_raw = pathlib.Path(_r2_wrap_path).read_bytes()
check("维护 R2-G5：执行器落盘 CR CR LF 计数为 0（此前应为 53 处）",
      _r2_wrap_raw.count(b"\r\r\n") == 0 and _r2_wrap_raw.count(b"\r\n") > 10,
      "CRLF=%d CRCRLF=%d enc=%s" % (_r2_wrap_raw.count(b"\r\n"),
                                    _r2_wrap_raw.count(b"\r\r\n"), _r2_wrap_enc))
check("维护 R2-G5：落盘统一走 _write_update_wrapper（newline 传空串，不重复转换）",
      "def _write_update_wrapper(path, script)" in src_upd
      and 'newline=""' in src_upd
      and "newline=\"\\r\\n\"" not in src_upd)

# —— 沙箱收尾：还原注入点 / TEMP，别影响后面的隐私守卫与版本一致性 ——
_r2_clear_files()
for _k, _v in _r2_saved.items():
    if _v is _R2_MISSING:
        au.__dict__.pop(_k, None)
    else:
        au.__dict__[_k] = _v
if _r2_saved_temp is None:
    os.environ.pop("TEMP", None)
else:
    os.environ["TEMP"] = _r2_saved_temp



# ============================================================
# 维护第二轮（2026-11：核心 / 托盘 / 协议 / CI 审计）
# C-08 / C-09 / C-14 / C-15 / C-20 / G3 / P2-5 / P2-6 / P2-8 / J1
# 行为级；不联网、不真装、不起真实服务进程。
# ============================================================

# —— R2-C08：周期线程退避到期不再 0 秒空转 ——
import datetime as _dtm2   # noqa: E402
_future2 = (_dtm2.datetime.now() + _dtm2.timedelta(seconds=60)).isoformat(timespec="seconds")
check("维护 R2-C08：退避到期至少等一个 tick（不再 0 秒空转刷日志）",
      svc.compute_periodic_wait_sec(1800, "2000-01-01T00:00:00") == svc.NETWATCH_TICK_SEC
      and svc.compute_periodic_wait_sec(1800, None) == 1800,
      repr((svc.compute_periodic_wait_sec(1800, "2000-01-01T00:00:00"),
            svc.compute_periodic_wait_sec(1800, None))))
check("维护 R2-C08：退避未到期时按剩余时间等（不会被 interval 吃掉）",
      abs(svc.compute_periodic_wait_sec(1800, _future2) - 60) < 5,
      repr(svc.compute_periodic_wait_sec(1800, _future2)))

# —— R2-G3：升级启动钩子挪到 HTTP 绑定之后（不再拖慢 /api/health 首轮探测）——
_main_src2 = src_svc[src_svc.index("def main():"):]
check("维护 R2-G3：升级启动钩子排在端口绑定之后、丢进后台线程",
      _main_src2.index("_post_upgrade_startup") > _main_src2.index("ThreadingHTTPServer(")
      and "post-upgrade" in _main_src2,
      "hook 必须在 bind 之后")

# —— R2-C09：is_online 的 None（未知）不再被当成「掉线 + 照发登录」——
_proto2 = importlib.import_module("protocol")
_p2_keys = ("_RUN_LOCK", "_STATE", "_STATE_LOCK", "_backoff_until", "_now_iso", "_get_password",
            "_load_config", "get_current_ssid", "get_local_ips", "guard_allows", "wait_network",
            "is_online", "login", "discover_network", "_set_state", "_set_backoff",
            "_reset_backoff", "_auto_profile")
_p2_orig = {_k: getattr(_proto2, _k, None) for _k in _p2_keys}
_p2_seen = {}
_proto2._RUN_LOCK = threading.Lock()
_proto2._STATE = {}
_proto2._STATE_LOCK = threading.Lock()
_proto2._load_config = lambda: {"host": "10.0.0.1", "port": 80, "account": "2023001234",
                                "suffix": "", "auto_check_interval_min": 30,
                                "network_wait_timeout_sec": 1, "network_guard_enabled": False,
                                "guard_allowed_ssids": "", "guard_allowed_subnets": ""}
_proto2._auto_profile = None
_proto2.get_current_ssid = lambda: "Campus-WiFi"
_proto2.get_local_ips = lambda: ["10.0.0.2"]
_proto2.guard_allows = lambda cfg, ssid, ips: (True, "")
_proto2.wait_network = lambda h, p, t: True
_proto2.is_online = lambda h: None
_proto2.login = lambda *a, **k: _p2_seen.setdefault("login", True) or (True, "x")
_proto2.discover_network = lambda h: ("10.0.0.2", "aabbccddeeff")
_proto2._get_password = lambda: "pwd"
_proto2._now_iso = lambda: "2026-11-16T00:00:00"
_proto2._backoff_until = lambda: None
_proto2._set_state = lambda **kw: _p2_seen.update(kw)
_proto2._set_backoff = lambda: _p2_seen.__setitem__("backoff", True)
_proto2._reset_backoff = lambda: None
try:
    _proto2.run_once("manual")
    _p2_result = dict(_p2_seen)
finally:
    for _k, _v in _p2_orig.items():
        setattr(_proto2, _k, _v)
check("维护 R2-C09：在线状态「未知」→ 不登录、不进退避、state.online 保持 None",
      "login" not in _p2_result and "backoff" not in _p2_result
      and _p2_result.get("online", "MISSING") is None,
      repr({k: _p2_result.get(k) for k in ("online", "login", "backoff", "last_error")}))

# —— R2-C20：JSONP 解析贪婪 + 锚定（msg 里带 } / ) 不再截断）——
class _R2Resp(object):
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_orig_open2 = _proto2.urllib.request.urlopen
_proto2.urllib.request.urlopen = lambda req, timeout=None: _R2Resp(
    b'dr1({"result":0,"msg":"bad } ) still text"})')
try:
    _ok20, _msg20 = _proto2.login("10.0.0.1", "2023001234", "", "pwd", "10.0.0.2", "")
finally:
    _proto2.urllib.request.urlopen = _orig_open2
check("维护 R2-C20：msg 里含 } / ) 也不会被 JSONP 正则截断",
      _ok20 is False and "still text" in (_msg20 or ""), repr(_msg20))
_proto2.urllib.request.urlopen = lambda req, timeout=None: _R2Resp(
    b'cb({"result":1,"v46ip":"10.0.0.2"});\r\n')
try:
    _on20 = _proto2.is_online("10.0.0.1")
finally:
    _proto2.urllib.request.urlopen = _orig_open2
check("维护 R2-C20：is_online 容忍尾部分号 / 空白", _on20 is True, repr(_on20))

# —— R2-C14 / C15：托盘事件与 explorer 重启自愈 ——
_src_tray2 = pathlib.Path("tray.py").read_text(encoding="utf-8")
check("维护 R2-C14：网络变化不再在 WndProc 里同步发 HTTP（丢后台线程）",
      'self._reconnect_async("网络地址变化")' in _src_tray2
      and 'self._reconnect("网络地址变化")' not in _src_tray2,
      "WndProc 必须只起线程")
check("维护 R2-C15：注册 TaskbarCreated，explorer 重启后重新加图标",
      'RegisterWindowMessageW("TaskbarCreated")' in _src_tray2
      and "_on_taskbar_created" in _src_tray2)
if os.name == "nt":
    _tray_app2 = _tr.TrayApp()
    check("维护 R2-C15（行为级）：TaskbarCreated 消息 id 已注册（>= 0xC000）",
          isinstance(_tray_app2.wm_taskbar_created, int)
          and _tray_app2.wm_taskbar_created >= 0xC000,
          repr(_tray_app2.wm_taskbar_created))

# —— R2-CI：发布只允许 main + 串行化；静默安装无 MsgBox；卸载清托盘注册表 ——
_wf2 = pathlib.Path(".github/workflows/build-installer.yml").read_text(encoding="utf-8")
check("维护 R2-CI：非 main 的构建不再发布 release（dispatch 不能从任意分支发正式版）",
      _wf2.count("refs/heads/main") >= 2 and "非 main 的构建只产出安装包 artifact" in _wf2)
check("维护 R2-CI：发布串行化（同 ref 并发构建不会互相踩 tag）",
      "concurrency:" in _wf2 and "build-installer-" in _wf2
      and "cancel-in-progress: false" in _wf2)
_iss2 = pathlib.Path("packaging/setup.iss").read_text(encoding="utf-8", errors="replace")
check("维护 R2-P2-8：静默安装路径的 MsgBox 都有 WizardSilent 守卫",
      _iss2.count("if not WizardSilent then") >= 2,
      "守卫数=%d" % _iss2.count("if not WizardSilent then"))
_src_uninst2 = pathlib.Path("uninstall.bat").read_text(encoding="utf-8", errors="replace")
check("维护 R2-J1：卸载显式清理托盘 Run 值与偏好键（不只靠 Inno 卸载记录）",
      "DelTrayRunValue" in _iss2 and "DelTrayPref" in _iss2
      and "DrcomAutoLoginTray" in _src_uninst2)


# ============================================================
# 维护第三轮（2026-11 升级链审计：D1 / D2 / D3 / D5 / F1 / F2）
# 对应 _维护报告/audit/upgrade.md「自动升级链剩余 P2」；
# 断言测行为（TEMP 沙箱 / 替身 / 假脚本内容），**不联网、不真装、不启动安装器**。
# ============================================================
_R3_MISSING = object()
_R3_NAMES = (
    "LOG_DIR", "UPGRADE_LOG_FILE", "logger", "BASE_DIR", "NSSM_PATH", "TOOLS_DIR",
    "STATE", "STATE_LOCK", "UPDATE_LOCK", "STOP_EVENT",
    "_load_config", "_save_config", "_now_iso",
    "_check_disk_free_mb", "_probe_local_health", "_check_github_latest",
    "_download_installer", "_launch_installer", "_read_nssm_appexit",
)
_r3_saved = {_k: au.__dict__.get(_k, _R3_MISSING) for _k in _R3_NAMES}
_r3_saved_temp = os.environ.get("TEMP")
_r3_root = tempfile.mkdtemp(prefix="drcom_r3_")
_r3_app = os.path.join(_r3_root, "app")
_r3_logs = os.path.join(_r3_root, "logs")
for _d in (_r3_app, _r3_logs):
    os.makedirs(_d, exist_ok=True)
os.environ["TEMP"] = _r3_root
_r3_state = {}
_r3_logger = logging.getLogger("smoke_auto_update_r3")
_r3_logger.addHandler(logging.NullHandler())
au._attach(
    logger=_r3_logger, state=_r3_state, state_lock=threading.Lock(), update_lock=threading.Lock(),
    tools_dir=os.path.join(_r3_root, "tools"), nssm_path=os.path.join(_r3_root, "tools", "nssm.exe"),
    upgrade_log_file=os.path.join(_r3_logs, "upgrade.log"), log_dir=_r3_logs, base_dir=_r3_app,
    load_config=lambda: {}, save_config=lambda *a: None,
    now_iso=lambda: "2026-11-16T00:00:00", stop_event=threading.Event())
_r3_nssm_stub = os.path.join(_r3_root, "nssm_stub.exe")
pathlib.Path(_r3_nssm_stub).write_bytes(b"stub")   # _do_update_now 会先判 isfile


def _r3_log():
    _lp = au.UPGRADE_LOG_FILE
    return pathlib.Path(_lp).read_text(encoding="utf-8") if os.path.isfile(_lp) else ""


def _r3_reset_log():
    try:
        if os.path.isfile(au.UPGRADE_LOG_FILE):
            os.remove(au.UPGRADE_LOG_FILE)
    except OSError:
        pass


def _r3_clear_files():
    for _fp in (au._rc_path(), au._wrapper_path(), au._health_probe_path(),
                au._rollback_script_path(), os.path.join(_r3_root, "drcom_rollback.rc")):
        try:
            if os.path.isfile(_fp):
                os.remove(_fp)
        except OSError:
            pass


# —— D5：生成脚本先 py_compile 校验（校验对象是脚本文件，不是项目模块）——
_r3_good = os.path.join(_r3_root, "ok_script.py")
_r3_bad = os.path.join(_r3_root, "bad_script.py")
pathlib.Path(_r3_good).write_text("VALUE = 1\n", encoding="utf-8")
pathlib.Path(_r3_bad).write_text("def broken(:\n    pass\n", encoding="utf-8")
_r3_good_ok, _r3_good_detail = au._py_compile_script(_r3_good, "测试脚本")
_r3_bad_ok, _r3_bad_detail = au._py_compile_script(_r3_bad, "测试脚本")
check("维护 R3-D5：_py_compile_script 语法正确放行、语法错误拦截（并清理 .pyc 残留）",
      _r3_good_ok is True and _r3_bad_ok is False
      and "语法校验失败" in _r3_bad_detail
      and not os.path.isfile(_r3_bad + ".pyc"),
      repr((_r3_good_detail, _r3_bad_detail)))
check("维护 R3-D5：校验器优先用随包内嵌 python，缺内嵌时退回当前解释器",
      callable(getattr(au, "_upgrade_python_executable", None))
      and isinstance(au._upgrade_python_executable(), str)
      and os.path.isfile(au._upgrade_python_executable()),
      repr(au._upgrade_python_executable()))

# 正常模板生成的回滚脚本必须能过校验（默认升级路径不被误伤）
for _n in ("联网_service.py", "version.py", "protocol.py", "web_api.py", "eula.py", "auto_update.py"):
    pathlib.Path(os.path.join(_r3_app, _n)).write_text("# fake %s\n" % _n, encoding="utf-8")
_r3_bk = au._backup_modules(version="1.2.3", note="维护第三轮")
_r3_scr = au._write_rollback_script(backup_dir=_r3_bk, rc_path=os.path.join(_r3_root, "r3.rc"))
_r3_scr_ok, _r3_scr_detail = au._py_compile_script(_r3_scr, "回滚脚本") if _r3_scr else (False, "未生成")
check("维护 R3-D5：正常生成的回滚脚本/健康探针都能过 py_compile（升级路径不被误伤）",
      bool(_r3_bk) and bool(_r3_scr) and _r3_scr_ok
      and os.path.isfile(_r3_scr), repr((_r3_scr, _r3_scr_detail)))

# 探针脚本语法坏掉（假内容）→ 同样必须在启动安装器之前中止
_r3_saved_build_probe = au._build_health_probe_script
_r3_probe_raised = None
_r3_reset_log()
try:
    au._build_health_probe_script = lambda *a, **k: "def broken(:\n    pass\n"
    au._launch_installer(os.path.join(_r3_root, "Fake-Setup.exe"), 8848, None)
except au._GeneratedScriptError as _r3_probe_exc:
    _r3_probe_raised = _r3_probe_exc
finally:
    au._build_health_probe_script = _r3_saved_build_probe
check("维护 R3-D5：健康探测脚本语法校验失败也中止升级（不写执行器、不启动安装器）",
      isinstance(_r3_probe_raised, au._GeneratedScriptError)
      and not os.path.isfile(au._wrapper_path())
      and "健康探测脚本生成后语法校验失败" in _r3_log(),
      repr((_r3_probe_raised, _r3_log()[-140:])))
_r3_clear_files()

# 用假（语法坏掉的）模板内容触发：回滚脚本生成后校验失败 → 必须中止升级、不启动安装器
_r3_saved_tpl = au._ROLLBACK_TEMPLATE
_r3_saved_disk3 = au._check_disk_free_mb
_r3_saved_cfg3 = au._load_config
_r3_saved_probe3 = au._probe_local_health
_r3_saved_latest3 = au._check_github_latest
_r3_saved_dl3 = au._download_installer
_r3_saved_appexit3 = au._read_nssm_appexit
_r3_saved_nssm3 = au.NSSM_PATH
_r3_saved_popen3 = au.subprocess.Popen
_r3_popen_calls = []
_r3_write_bad = None
_r3_abort = None
_r3_raised = None
_r3_proc_stub = types.SimpleNamespace(pid=4321)


def _r3_popen_guard(*a, **k):
    """拦住「启动安装器」的 Popen：只记录 + 返回假进程，绝不真跑（py_compile 的子进程照常放行）。"""
    _r3_name = str(a[0]) if a else ""
    _r3_popen_calls.append(_r3_name)
    if "Setup" in _r3_name:
        return _r3_proc_stub
    return _r3_saved_popen3(*a, **k)


def _r3_fake_download(url, dest_path, expected_size, progress_callback=None, expected_sha256=None):
    pathlib.Path(dest_path).write_bytes(b"fake-installer")
    return 15


try:
    _r3_reset_log()
    # ① 生成器本身：语法坏模板 → 返回 None 并删掉坏脚本（手动回滚路径的降级行为）
    au._ROLLBACK_TEMPLATE = "def broken(:\n    pass\n"
    _r3_write_bad = au._write_rollback_script(backup_dir=_r3_bk)
    _r3_bad_deleted = not os.path.isfile(au._rollback_script_path())
    # ② 升级路径：_do_update_now → _launch_installer 真跑校验 → 抛 _GeneratedScriptError
    au.NSSM_PATH = _r3_nssm_stub
    au._check_disk_free_mb = lambda: 99999
    au._load_config = lambda: {"update_min_free_disk_mb": 0, "ui_port": 8848}
    au._probe_local_health = lambda *a, **k: (True, "http=200 ok=true")
    au._check_github_latest = lambda: ("9.9.9", "https://example.invalid/x.exe", "0" * 64, 1, None)
    au._download_installer = _r3_fake_download
    au._read_nssm_appexit = lambda *a, **k: None
    au.subprocess.Popen = _r3_popen_guard
    _r3_abort = au._do_update_now()
finally:
    au._ROLLBACK_TEMPLATE = _r3_saved_tpl
    au.NSSM_PATH = _r3_saved_nssm3
    au._check_disk_free_mb = _r3_saved_disk3
    au._load_config = _r3_saved_cfg3
    au._probe_local_health = _r3_saved_probe3
    au._check_github_latest = _r3_saved_latest3
    au._download_installer = _r3_saved_dl3
    au._read_nssm_appexit = _r3_saved_appexit3
    au.subprocess.Popen = _r3_saved_popen3
_r3_abort_log = _r3_log()
check("维护 R3-D5：回滚脚本生成后语法校验失败 → 升级中止（中文 error），不写执行器、不启动安装器",
      isinstance(_r3_abort, dict) and _r3_abort.get("ok") is False
      and "已中止升级" in (_r3_abort.get("error") or "")
      and "语法校验失败" in (_r3_abort.get("error") or "")
      and not [c for c in _r3_popen_calls if "Setup" in c]
      and not os.path.isfile(au._wrapper_path())
      and _r3_state.get("update_state") == "error"
      and "语法校验失败" in (_r3_state.get("update_last_error") or "")
      and "本次升级中止" in _r3_abort_log,
      repr((_r3_abort, _r3_popen_calls, _r3_state.get("update_state"))))
check("维护 R3-D5：手动回滚路径拿不到坏脚本（返回 None 且坏脚本已删除）",
      _r3_write_bad is None and _r3_bad_deleted,
      repr((_r3_write_bad, _r3_bad_deleted)))
_r3_attempt_after_abort = au._read_update_attempt()
check("维护 R3-D5：校验失败算一次失败尝试（熔断计数照常，不会无限重试）",
      _r3_attempt_after_abort.get("state") == "failed"
      and int(_r3_attempt_after_abort.get("count") or 0) >= 1,
      repr(_r3_attempt_after_abort))
check("维护 R3-D5：_GeneratedScriptError 是 OSError 子类（旧兜底逻辑不失效）",
      issubclass(au._GeneratedScriptError, OSError)
      and "except _GeneratedScriptError" in src_upd
      and "raise_on_invalid=True" in src_upd)
_r3_clear_files()

# —— D3：回滚脚本优先用内嵌 python（缺内嵌退回 sys.executable），注释与代码一致 ——
_r3_py_used = au._upgrade_python_executable()
check("维护 R3-D3：回滚/校验统一走 _upgrade_python_executable（内嵌优先，退回 sys.executable）",
      isinstance(_r3_py_used, str) and os.path.isfile(_r3_py_used)
      and "args = [_upgrade_python_executable(), path" in src_upd
      and 'sys.executable, path, "--backup"' not in src_upd,
      repr(_r3_py_used))

# —— D1：manifest 原子写（.tmp + os.replace）——
_r3_real_replace = os.replace
_r3_replace_calls = []


def _r3_rec_replace(src, dst):
    _r3_replace_calls.append((str(src), str(dst)))
    return _r3_real_replace(src, dst)


os.replace = _r3_rec_replace
try:
    _r3_bk_atomic = au._backup_modules(version="1.2.4", note="原子写")
finally:
    os.replace = _r3_real_replace
_r3_man_path = os.path.join(_r3_bk_atomic or "", au.BACKUP_MANIFEST_NAME)
check("维护 R3-D1：manifest 原子写（先写 .tmp 再 os.replace，不留半截清单）",
      bool(_r3_bk_atomic) and os.path.isfile(_r3_man_path)
      and any(s.endswith("manifest.json.tmp") and d == _r3_man_path for s, d in _r3_replace_calls)
      and not os.path.isfile(_r3_man_path + ".tmp"),
      repr(_r3_replace_calls))
check("维护 R3-D1：清单读回可用（原子写之后 _list_backups 仍认这份备份）",
      any(b["version"] == "1.2.4" for b in au._list_backups()))

# —— D1 / D2：prune 清理半成品（无 manifest 且超过 1 天）但绝不误删新目录；keep 语义真实生效 ——
_r3_app_keep = os.path.join(_r3_root, "app_keep")
os.makedirs(_r3_app_keep, exist_ok=True)
_r3_saved_base3 = au.BASE_DIR
_r3_old_mtime = time.time() - 8 * 86400
try:
    au.BASE_DIR = _r3_app_keep
    # 4 份「合法但已过期」的备份：最新 2 份必须被 keep 住，另外 2 份按 7 天删
    _r3_keep_plan = (("7.0.1", "2027-01-01T00:00:00"), ("7.0.2", "2027-01-02T00:00:00"),
                     ("7.0.3", "2027-01-03T00:00:00"), ("7.0.4", "2027-01-04T00:00:00"))
    for _ver, _created in _r3_keep_plan:
        _bd = au._backup_dir_for(_ver)
        os.makedirs(_bd)
        pathlib.Path(os.path.join(_bd, "version.py")).write_text("# %s\n" % _ver, encoding="utf-8")
        au._write_backup_manifest(_bd, {"version": _ver, "created_at": _created,
                                        "files": {"version.py": "x"},
                                        "rollback_files": ["version.py"]})
        os.utime(_bd, (_r3_old_mtime, _r3_old_mtime))
    # 半成品：旧（8 天前，必须清掉） / 新（mtime 现在，可能正在备份，绝不能动）
    _r3_orphan_old = au._backup_dir_for("8.8.8")
    os.makedirs(_r3_orphan_old)
    pathlib.Path(os.path.join(_r3_orphan_old, "version.py")).write_text("# half\n", encoding="utf-8")
    os.utime(_r3_orphan_old, (_r3_old_mtime, _r3_old_mtime))
    _r3_orphan_new = au._backup_dir_for("8.8.7")
    os.makedirs(_r3_orphan_new)
    pathlib.Path(os.path.join(_r3_orphan_new, "version.py")).write_text("# half\n", encoding="utf-8")
    _r3_reset_log()
    au._prune_backups()
    _r3_left = sorted(os.listdir(au._backup_root()))
    _r3_prune_log = _r3_log()
finally:
    au.BASE_DIR = _r3_saved_base3
check("维护 R3-D1：半成品备份目录（有目录、无合法 manifest 且 mtime 超过 1 天）被清理",
      "8.8.8" not in _r3_left and "清理半成品备份目录" in _r3_prune_log
      and "8.8.8" in _r3_prune_log,
      repr(_r3_left))
check("维护 R3-D1：mtime 新的半成品目录（可能正在备份）绝不误删",
      "8.8.7" in _r3_left, repr(_r3_left))
check("维护 R3-D2：prune keep 不再死代码 —— 全部过期也至少保留最新 2 份，其余按 7 天删",
      "7.0.4" in _r3_left and "7.0.3" in _r3_left
      and "7.0.2" not in _r3_left and "7.0.1" not in _r3_left,
      repr(_r3_left))

# —— F1：抢锁失败不再静默丢弃（写 last_error / state=error / upgrade.log）——
_r3_reset_log()
au._set_update_state(update_state=None, update_last_error=None, update_progress_message="")
_r3_state["update_lock"] = True
try:
    _r3_f1_update = au._do_update_now()
    _r3_f1_check = au._do_check_now()
finally:
    _r3_state["update_lock"] = False
_r3_f1_log = _r3_log()
check("维护 R3-F1：_do_update_now / _do_check_now 抢锁失败 → 中文 last_error + state=error + 升级日志",
      isinstance(_r3_f1_update, dict) and _r3_f1_update.get("ok") is False
      and isinstance(_r3_f1_check, dict) and _r3_f1_check.get("ok") is False
      and "另一项升级任务正在进行，本次请求未执行" in (_r3_f1_update.get("error") or "")
      and _r3_state.get("update_state") == "error"
      and "本次请求未执行" in (_r3_state.get("update_last_error") or "")
      and "另一项升级任务正在进行" in _r3_f1_log,
      repr((_r3_f1_update, _r3_f1_check, _r3_state.get("update_last_error"))))

# —— F2：reset_update_attempt 清空熔断计时（3 次失败后自动重试恢复），且未 attach 时不炸 ——
au._write_update_attempt("9.9.9", count=3, state="failed")
_r3_blocked_before, _r3_blocked_reason = au._auto_retry_blocked("9.9.9")
_r3_reset_log()
_r3_reset_ok = au.reset_update_attempt("冒烟重置")
_r3_blocked_after, _r3_blocked_after_reason = au._auto_retry_blocked("9.9.9")
_r3_reset_log_txt = _r3_log()
check("维护 R3-F2：reset_update_attempt 清空失败计时（3 次熔断 → 自动重试恢复）",
      _r3_blocked_before is True and "已停止自动重试" in _r3_blocked_reason
      and _r3_reset_ok is True
      and not os.path.isfile(au._attempt_file())
      and _r3_blocked_after is False,
      repr((_r3_blocked_before, _r3_blocked_after, _r3_reset_ok)))
check("维护 R3-F2：重置落一条中文 upgrade.log（UI 后续接线的入口有据可查）",
      "已重置自动升级熔断" in _r3_reset_log_txt, repr(_r3_reset_log_txt[-160:]))
check("维护 R3-F2：死分支 state==\"success\" 已删除（成功只走 _clear_update_attempt）",
      '    if rec.get("state") == "success":' not in src_upd
      and "def reset_update_attempt(" in src_upd)
_r3_na_saved = {_k: au.__dict__.get(_k, _R3_MISSING) for _k in
                ("LOG_DIR", "UPGRADE_LOG_FILE", "logger")}
for _k in _r3_na_saved:
    au.__dict__.pop(_k, None)
try:
    _r3_na_result = au.reset_update_attempt("未 attach")
finally:
    for _k, _v in _r3_na_saved.items():
        if _v is _R3_MISSING:
            au.__dict__.pop(_k, None)
        else:
            au.__dict__[_k] = _v
check("维护 R3-F2：reset_update_attempt 未 attach（无 LOG_DIR）时不炸（返回 False）",
      _r3_na_result is False, repr(_r3_na_result))

# —— 沙箱收尾：还原注入点 / TEMP，别影响隐私守卫与版本一致性 ——
_r3_clear_files()
for _k, _v in _r3_saved.items():
    if _v is _R3_MISSING:
        au.__dict__.pop(_k, None)
    else:
        au.__dict__[_k] = _v
if _r3_saved_temp is None:
    os.environ.pop("TEMP", None)
else:
    os.environ["TEMP"] = _r3_saved_temp
shutil.rmtree(_r3_root, ignore_errors=True)


# ============================================================
# 维护第三轮（2026-11：metrics / profiles / protocol / CI）
# C-11 / C-18 / C-21 / C-24 / C-25 / P2-7 / P2-11 / P2-12 / P2-16
# ============================================================

# —— R3-C24：面板延迟测量加 TTL 缓存（请求线程不再每次最多卡 3 秒）——
_mt3 = importlib.import_module("metrics")
_mt3_calls = []
_mt3_orig_measure = _mt3.measure_latency
_mt3.measure_latency = lambda h, p: (_mt3_calls.append((h, p)), 17)[1]
try:
    _mt3._LATENCY_CACHE = {"key": None, "at": 0.0, "value": None}
    _l1 = _mt3.cached_latency("10.0.0.1", 80, now_epoch=1000)
    _l2 = _mt3.cached_latency("10.0.0.1", 80, now_epoch=1010)
    _l3 = _mt3.cached_latency("10.0.0.1", 80, now_epoch=1040)
    _l4 = _mt3.cached_latency("10.0.0.2", 80, now_epoch=1041)
finally:
    _mt3.measure_latency = _mt3_orig_measure
check("维护 R3-C24：延迟测量 30 秒 TTL 缓存（同目标复用，超时/换目标才重测）",
      (_l1, _l2, _l3, _l4) == (17, 17, 17, 17) and len(_mt3_calls) == 3,
      "calls=%d" % len(_mt3_calls))

# —— R3-C25：轮转瞬间丢文件不再丢整段归档（重试一次）——
_mt3_seq = {"n": 0}
_mt3_orig_once = _mt3._read_log_once


def _mt3_flaky(path, max_bytes):
    _mt3_seq["n"] += 1
    if _mt3_seq["n"] == 1:
        raise FileNotFoundError(path)
    return ["轮转后读到的行"]


_mt3._read_log_once = _mt3_flaky
try:
    _rot_lines = _mt3.read_log_tail("whatever.log")
finally:
    _mt3._read_log_once = _mt3_orig_once
check("维护 R3-C25：日志轮转瞬间读不到文件会重试一次（不丢整段归档）",
      _rot_lines == ["轮转后读到的行"] and _mt3_seq["n"] >= 2,
      repr((_rot_lines, _mt3_seq["n"])))

# —— R3-C11：方案保存与切换用同一套合法取值（不再「能存不能切」）——
_pf3 = importlib.import_module("profiles")
check("维护 R3-C11：方案保存就按切换口径校验（间隔 / 账号 / 后缀 / host）",
      _pf3.validate_values({"auto_check_interval_min": 10}) != []
      and _pf3.validate_values({"account": "abc123"}) != []
      and _pf3.validate_values({"suffix": "@xx"}) != []
      and _pf3.validate_values({"host": "a b"}) != []
      and _pf3.validate_values({"auto_check_interval_min": 30, "account": "2023001",
                                "suffix": "@yd", "host": "172.16.80.3"}) == [],
      repr(_pf3.validate_values({"auto_check_interval_min": 10})))
_pf3._attach(allowed_intervals=(15, 30), allowed_suffixes=("", "@yd"))
try:
    _pf3_ok15 = _pf3.validate_values({"auto_check_interval_min": 15}) == []
    _pf3_bad5 = _pf3.validate_values({"auto_check_interval_min": 5}) != []
finally:
    _pf3._attach(allowed_intervals=(5, 15, 30, 60, 120),
                 allowed_suffixes=("", "@yd", "@dx", "@lt"))
check("维护 R3-C11：服务层注入的合法取值会被方案校验使用（单一来源）",
      _pf3_ok15 and _pf3_bad5, repr((_pf3_ok15, _pf3_bad5)))

# —— R3-C21：host 白名单 + 网关给的 v4ip / olmac 先校验形状 ——
check("维护 R3-C21：配置校验拒绝带空格 / 斜杠的 host（结构性防御）",
      svc._validate_config(dict(svc._default_config(), host="a b")) != []
      and svc._validate_config(dict(svc._default_config(), host="a/b")) != []
      and svc._validate_config(dict(svc._default_config(), host="172.16.80.3")) == [])


class _R3Resp(object):
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_proto3 = importlib.import_module("protocol")
_r3_open = _proto3.urllib.request.urlopen
try:
    _proto3.urllib.request.urlopen = lambda req, timeout=None: _R3Resp(
        b'cb({"result":1,"v4ip":"10.9.9.9/evil","olmac":"ZZ:QQ"})')
    _r3_ip_bad, _r3_mac_bad = _proto3.discover_network("10.0.0.1")
    _proto3.urllib.request.urlopen = lambda req, timeout=None: _R3Resp(
        b'cb({"result":1,"v4ip":"10.1.2.3","olmac":"aa:bb:cc:dd:ee:ff"})')
    _r3_ip_ok, _r3_mac_ok = _proto3.discover_network("10.0.0.1")
finally:
    _proto3.urllib.request.urlopen = _r3_open
check("维护 R3-C21：网关给的 v4ip / olmac 过形状白名单（坏值不拼进登录 URL）",
      _r3_ip_bad != "10.9.9.9/evil" and _r3_mac_bad == ""
      and _r3_ip_ok == "10.1.2.3" and _r3_mac_ok == "AABBCCDDEEFF",
      repr((_r3_ip_bad, _r3_mac_bad, _r3_ip_ok, _r3_mac_ok)))

# —— R3-C18：wait_network 可被停止信号打断（不再等满 timeout）——
_r3_event = threading.Event()
_r3_orig_stop = _proto3._STOP_EVENT
_r3_orig_conn = _proto3.socket.create_connection
_proto3._STOP_EVENT = _r3_event
_proto3.socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(OSError("不可达"))
threading.Timer(1.0, _r3_event.set).start()
_r3_t0 = time.time()
try:
    _r3_wait = _proto3.wait_network("10.255.255.1", 9, 30)
finally:
    _proto3._STOP_EVENT = _r3_orig_stop
    _proto3.socket.create_connection = _r3_orig_conn
_r3_cost = time.time() - _r3_t0
check("维护 R3-C18：wait_network 收到停止信号立即返回（不再等完 timeout）",
      _r3_wait is False and _r3_cost < 5, "cost=%.2fs" % _r3_cost)

# —— R3-CI：注入面 / quotePath / 双线白名单 / 批处理延迟展开 ——
_wf3 = pathlib.Path(".github/workflows/build-installer.yml").read_text(encoding="utf-8")
_pr3 = pathlib.Path(".github/scripts/pr_review.py").read_text(encoding="utf-8")
check("维护 R3-CI：PR 机器人 git 调用关闭 core.quotePath（中文文件名不再被转义）",
      "core.quotePath=false" in _pr3)
check("维护 R3-CI：双线隔离白名单包含主服务脚本 联网_service.py",
      "联网_service.py" in _pr3)
_inst3 = pathlib.Path("install.bat").read_text(encoding="utf-8", errors="replace")
_uninst3 = pathlib.Path("uninstall.bat").read_text(encoding="utf-8", errors="replace")
check("维护 R3-CI：批处理里不再用会被提前展开的 %ERRORLEVEL%",
      "%ERRORLEVEL%" not in _inst3 and "%ERRORLEVEL%" not in _uninst3,
      "install=%d uninstall=%d" % (_inst3.count("%ERRORLEVEL%"), _uninst3.count("%ERRORLEVEL%")))


# —— R3-F2：熔断重置已接线（业务函数 + API 字段只增）——
_src_upd3 = pathlib.Path("auto_update.py").read_text(encoding="utf-8")
_src_web3 = pathlib.Path("web_api.py").read_text(encoding="utf-8")
check("维护 R3-F2：reset_update_attempt 可用，且 /api/update/install 支持 reset_attempt 字段",
      "def reset_update_attempt(" in _src_upd3
      and "reset_attempt" in _src_web3
      and "reset_update_attempt(" in _src_web3)

# —— R3-CI：输入注入面 / quotePath / 双线白名单 / 批处理延迟展开 ——
_wf3 = pathlib.Path(".github/workflows/build-installer.yml").read_text(encoding="utf-8")
_pb3 = pathlib.Path(".github/workflows/pr-bot.yml").read_text(encoding="utf-8")
_pr3b = pathlib.Path(".github/scripts/pr_review.py").read_text(encoding="utf-8")
check("维护 R3-CI：workflow 输入通过 env 注入（run 块里不再直接拼表达式）",
      "INPUT_TITLE" in _wf3 and "$env:INPUT_TITLE" in _wf3
      and "GIT_REF" in _wf3 and "$env:GIT_REF" in _wf3
      and _wf3.count("github.event.inputs.title") == 1,
      "count=%d" % _wf3.count("github.event.inputs.title"))
check("维护 R3-CI：PR Bot 的 base_ref 也走 env（不再拼进 shell）",
      "BASE_REF" in _pb3 and '--base-ref "$BASE_REF"' in _pb3
      and _pb3.count("github.base_ref") == 2,
      "count=%d" % _pb3.count("github.base_ref"))
check("维护 R3-CI：PR 机器人 git 调用关闭 core.quotePath（中文文件名不再被转义）",
      _pr3b.count("core.quotePath=false") >= 2,
      "count=%d" % _pr3b.count("core.quotePath=false"))
check("维护 R3-CI：双线隔离白名单包含主服务脚本 联网_service.py",
      "联网_service.py" in _pr3b)
_inst3 = pathlib.Path("install.bat").read_text(encoding="utf-8", errors="replace")
_uninst3 = pathlib.Path("uninstall.bat").read_text(encoding="utf-8", errors="replace")
check("维护 R3-CI：批处理不再用会被括号块提前展开的 %ERRORLEVEL%",
      "=%ERRORLEVEL%" not in _inst3 and "=%ERRORLEVEL%" not in _uninst3,
      "install=%d uninstall=%d" % (_inst3.count("=%ERRORLEVEL%"), _uninst3.count("=%ERRORLEVEL%")))
check("维护 R3-CI：卸载脚本不再硬编码桌面 / 开始菜单路径（OneDrive 重定向也能删）",
      "%USERPROFILE%\\Desktop" not in _uninst3
      and "%APPDATA%\\Microsoft" not in _uninst3)



# ============================================================
# 维护第四轮（2026-11：供应链锁 / 删方案清密码）
# P2-4 / J7 / C-26
# ============================================================

# —— R4-C26：删除方案会一并删掉专属密码文件（避免同名重建继承旧密码）——
_src_web4 = pathlib.Path("web_api.py").read_text(encoding="utf-8")
check("维护 R4-C26：删方案接口清理专属密码，并只增返回字段 password_removed",
      "password_removed" in _src_web4
      and "_remove_profile_password" in _src_web4
      and "def api_post_profiles_delete" in _src_web4)

# —— R4-供应链：上游二进制固定 SHA256（tools.lock.json 为唯一事实来源）——
_lock_doc = json.loads(pathlib.Path("tools.lock.json").read_text(encoding="utf-8"))
_lock_tools = _lock_doc.get("tools") or []
_lock_urls = {str(t.get("url", "")).strip(): t for t in _lock_tools}
_nssm_url = "https://nssm.cc/release/nssm-2.24.zip"
check("维护 R4-供应链：tools.lock.json 锁定 NSSM 与内嵌 Python，哈希格式合法",
      _nssm_url in _lock_urls
      and _lock_urls[_nssm_url].get("required") is True
      and all(isinstance(t.get("sha256"), str) and len(t["sha256"]) == 64
              for t in _lock_tools)
      and any("python-embed" in str(t.get("name", "")) for t in _lock_tools),
      "entries=%d" % len(_lock_tools))
_wf4 = pathlib.Path(".github/workflows/build-installer.yml").read_text(encoding="utf-8")
_bp4 = pathlib.Path("packaging/build.ps1").read_text(encoding="utf-8")
_ib4 = pathlib.Path("install.bat").read_text(encoding="utf-8", errors="replace")
_nssm_sha = _lock_urls[_nssm_url]["sha256"]
check("维护 R4-供应链：CI / 本地构建 / 源码安装都从 lock 读哈希校验（不硬编码哈希）",
      "tools.lock.json" in _wf4 and "Get-FileHash" in _wf4
      and "tools.lock.json" in _bp4 and "Get-FileHash" in _bp4
      and "tools.lock.json" in _ib4,
      "wf=%s build=%s inst=%s" % ("tools.lock.json" in _wf4,
                                  "tools.lock.json" in _bp4,
                                  "tools.lock.json" in _ib4))
check("维护 R4-供应链：哈希值只出现在 tools.lock.json（代码 / 脚本里不重复写死）",
      _nssm_sha not in _wf4 and _nssm_sha not in _bp4 and _nssm_sha not in _ib4)
check("维护 R4-供应链：浮动下载物（Inno 官方安装器 / choco 包）在 not_locked 里写明未锁定原因",
      isinstance(_lock_doc.get("not_locked"), list)
      and any("innosetup" in str(n.get("name", "")).lower()
              for n in _lock_doc["not_locked"]),
      repr([n.get("name") for n in (_lock_doc.get("not_locked") or [])]))


# ============================================================
# 维护第五轮（2026-11 / 2.x 维护：C2 / J8 / E5 / J6）
# 对应 _维护报告/audit/upgrade.md 的 C2 / J8 / E5 / J6。
# 行为级：C2 用替身模拟镜像下载；J8 在 TEMP 沙箱造安装包 + 控制 mtime；
# E5 测纯函数 + 写脚本函数拒绝生成；J6 读 build.ps1 / VERSIONING.md 源码。
# 不联网、不真装、不起真实服务进程。
# ============================================================

_R5_MISSING = object()
_R5_NAMES = (
    "LOG_DIR", "UPGRADE_LOG_FILE", "logger", "BASE_DIR", "NSSM_PATH",
    "STATE", "STATE_LOCK", "UPDATE_LOCK", "STOP_EVENT",
    "_load_config", "_check_github_latest", "_check_disk_free_mb", "_now_iso",
    "_probe_local_health", "_do_download_installer", "GITHUB_DOWNLOAD_MIRRORS",
)
_r5_saved = {k: au.__dict__.get(k, _R5_MISSING) for k in _R5_NAMES}
_r5_saved_temp = os.environ.get("TEMP")
_r5_root = tempfile.mkdtemp(prefix="drcom_r5_")
_r5_app = os.path.join(_r5_root, "app")
_r5_logs = os.path.join(_r5_root, "logs")
for _d in (_r5_app, _r5_logs):
    os.makedirs(_d, exist_ok=True)
os.environ["TEMP"] = _r5_root
_r5_state = {}
_r5_logger = logging.getLogger("smoke_auto_update_r5")
_r5_logger.addHandler(logging.NullHandler())
au._attach(
    logger=_r5_logger, state=_r5_state, state_lock=threading.Lock(), update_lock=threading.Lock(),
    tools_dir=os.path.join(_r5_root, "tools"), nssm_path=os.path.join(_r5_root, "tools", "nssm.exe"),
    upgrade_log_file=os.path.join(_r5_logs, "upgrade.log"), log_dir=_r5_logs, base_dir=_r5_app,
    load_config=lambda: {}, save_config=lambda *a: None,
    now_iso=lambda: "2026-11-16T00:00:00", stop_event=threading.Event())


class _R5Logger(object):
    """记录 info / warning / debug 文案（C2 / E5 的行为断言用）。"""

    def __init__(self):
        self.lines = []

    def _add(self, level, msg, args):
        self.lines.append(level + " " + (msg % args if args else str(msg)))

    def info(self, msg, *a):
        self._add("INFO", msg, a)

    def warning(self, msg, *a):
        self._add("WARN", msg, a)

    def error(self, msg, *a):
        self._add("ERROR", msg, a)

    def exception(self, msg, *a):
        self._add("EXC", msg, a)

    def debug(self, msg, *a):
        self._add("DEBUG", msg, a)


def _r5_upgrade_log():
    _lp = au.UPGRADE_LOG_FILE
    return pathlib.Path(_lp).read_text(encoding="utf-8") if os.path.isfile(_lp) else ""


_r5_cap = _R5Logger()
au.logger = _r5_cap
_r5_saved_mirrors = au.GITHUB_DOWNLOAD_MIRRORS
_r5_saved_do_download = au._do_download_installer

# —— C2：下载完成后按 expected_size 核对实际大小（不符 → 删文件、换下一个镜像）——
_r5_dl_calls = []
_r5_good = b"GOOD-R5-INSTALLER" * 8          # 136 字节
_r5_dest = os.path.join(_r5_root, "dl_r5.exe")


def _r5_fake_size_mismatch_then_ok(url, dest_path, expected_size, cb, timeout):
    _r5_dl_calls.append(url)
    if "gh-proxy.com" not in url:             # 主源返回被截断的包（9 字节 < 期望 144）
        pathlib.Path(dest_path).write_bytes(b"TRUNCATED")
        return 9
    pathlib.Path(dest_path).write_bytes(_r5_good)
    return len(_r5_good)


au.GITHUB_DOWNLOAD_MIRRORS = (None, "https://gh-proxy.com")
au._do_download_installer = _r5_fake_size_mismatch_then_ok
_r5_written = None
try:
    _r5_written = au._download_installer("https://example.invalid/pkg.exe", _r5_dest,
                                         len(_r5_good), None)
    _r5_size_ok = (_r5_written == len(_r5_good)
                   and pathlib.Path(_r5_dest).read_bytes() == _r5_good)
finally:
    au._do_download_installer = _r5_saved_do_download
    au.GITHUB_DOWNLOAD_MIRRORS = _r5_saved_mirrors
_r5_size_warns = [l for l in _r5_cap.lines if "大小不符" in l]
check("维护 R5-C2：expected_size 不符 → 删掉该镜像的包并换下一个镜像（不判成功）",
      _r5_size_ok and len(_r5_size_warns) == 1
      and ("期望 %d 字节" % len(_r5_good)) in _r5_size_warns[0]
      and "实际 9 字节" in _r5_size_warns[0]
      and any("gh-proxy.com" in u for u in _r5_dl_calls),
      repr((_r5_written, _r5_size_warns, _r5_dl_calls)))

# 所有镜像都不符 → 抛错、文件删干净，绝不把半截包判成成功
_r5_dest2 = os.path.join(_r5_root, "dl_r5_bad.exe")


def _r5_fake_always_short(url, dest_path, expected_size, cb, timeout):
    pathlib.Path(dest_path).write_bytes(b"SHORT")
    return 5


au.GITHUB_DOWNLOAD_MIRRORS = (None, "https://gh-proxy.com")
au._do_download_installer = _r5_fake_always_short
_r5_size_error = ""
try:
    au._download_installer("https://example.invalid/pkg.exe", _r5_dest2, 144, None)
except OSError as _exc:
    _r5_size_error = str(_exc)
finally:
    au._do_download_installer = _r5_saved_do_download
    au.GITHUB_DOWNLOAD_MIRRORS = _r5_saved_mirrors
check("维护 R5-C2：所有镜像大小都不符 → 抛中文错误且不留半截包（绝不判成功）",
      "大小不符" in _r5_size_error and "期望 144 字节" in _r5_size_error
      and "实际 5 字节" in _r5_size_error and not os.path.isfile(_r5_dest2),
      repr((_r5_size_error, os.path.isfile(_r5_dest2))))

# expected_size 缺失 / 相符 → 行为与以前一致（不误删、不误判失败）
def _r5_fake_writes(url, dest_path, expected_size, cb, timeout):
    pathlib.Path(dest_path).write_bytes(b"ANY-BYTES")
    return 9


au._do_download_installer = _r5_fake_writes
_r5_dest3 = os.path.join(_r5_root, "dl_r5_none.exe")
_r5_dest4 = os.path.join(_r5_root, "dl_r5_match.exe")
try:
    _r5_none_written = au._download_installer("https://example.invalid/pkg.exe", _r5_dest3, None, None)
    _r5_none_ok = _r5_none_written == 9 and os.path.isfile(_r5_dest3)
    _r5_match_written = au._download_installer("https://example.invalid/pkg.exe", _r5_dest4, 9, None)
    _r5_match_ok = _r5_match_written == 9 and os.path.isfile(_r5_dest4)
finally:
    au._do_download_installer = _r5_saved_do_download
check("维护 R5-C2：expected_size 缺失 / 相符时不改行为（不校验、不误删）",
      _r5_none_ok and _r5_match_ok, repr((_r5_none_ok, _r5_match_ok)))

# —— J8：TEMP 里「旧版本 + 超过 24 小时」的安装包要清；新的 / 坏名字 / 子目录不动 ——
_r5_old_mtime = time.time() - (au.OLD_INSTALLER_MAX_AGE_SEC + 3600)   # 25 小时前
_r5_fresh_mtime = time.time() - 60                                    # 1 分钟前
_r5_pkg = {
    "old_expired": "StardustFlashLink-Setup-v2.1.0.0.exe",   # 旧版本 + 过期 → 删
    "old_fresh": "StardustFlashLink-Setup-v2.1.0.1.exe",     # 旧版本但刚下好 → 留
    "higher": "StardustFlashLink-Setup-v9.9.9.9.exe",        # 版本更高 → 留
    "badname": "StardustFlashLink-Setup-vabc.exe",           # 解析不出 → 留
    "three_seg": "StardustFlashLink-Setup-v2.1.0.exe",       # 只有三段 → 留
}
for _n in _r5_pkg.values():
    _p = os.path.join(_r5_root, _n)
    pathlib.Path(_p).write_bytes(b"x")
    os.utime(_p, (_r5_old_mtime, _r5_old_mtime))
os.utime(os.path.join(_r5_root, _r5_pkg["old_fresh"]), (_r5_fresh_mtime, _r5_fresh_mtime))
# 子目录里的同名旧包：清理范围限定 TEMP 根目录（不递归）→ 必须保留
_r5_sub = os.path.join(_r5_root, "sub")
os.makedirs(_r5_sub, exist_ok=True)
_r5_sub_pkg = os.path.join(_r5_sub, "StardustFlashLink-Setup-v2.1.0.0.exe")
pathlib.Path(_r5_sub_pkg).write_bytes(b"x")
os.utime(_r5_sub_pkg, (_r5_old_mtime, _r5_old_mtime))
au._cleanup_update_leftovers()
check("维护 R5-J8：TEMP 里旧版本 + 超过 24h 的安装包被清理，新版本 / 坏名字 / 三段版本保留",
      not os.path.isfile(os.path.join(_r5_root, _r5_pkg["old_expired"]))
      and os.path.isfile(os.path.join(_r5_root, _r5_pkg["old_fresh"]))
      and os.path.isfile(os.path.join(_r5_root, _r5_pkg["higher"]))
      and os.path.isfile(os.path.join(_r5_root, _r5_pkg["badname"]))
      and os.path.isfile(os.path.join(_r5_root, _r5_pkg["three_seg"])),
      repr(sorted(os.listdir(_r5_root))))
check("维护 R5-J8：清理范围限定 TEMP 根目录（子目录里的同名旧包不递归误删）",
      os.path.isfile(_r5_sub_pkg), repr(sorted(os.listdir(_r5_sub))))
for _n in _r5_pkg.values():          # 清掉夹具，免得干扰后面断言
    try:
        os.remove(os.path.join(_r5_root, _n))
    except OSError:
        pass
shutil.rmtree(_r5_sub, ignore_errors=True)

# —— E5：cmd 插值危险字符的纯函数 + 生成 / 启动脚本前拒绝 ——
_r5_safe_path = r"D:\Program Files\DrcomAutoLogin"
check("维护 R5-E5：正常安装路径（盘符 + 空格）不判成不安全",
      au._cmd_path_unsafe_reason(_r5_safe_path) == "",
      repr(au._cmd_path_unsafe_reason(_r5_safe_path)))
_r5_danger = {
    "%": r"D:\100%\DrcomAutoLogin",
    "!": r"D:\a!b\DrcomAutoLogin",
    "^": r"D:\a^b\DrcomAutoLogin",
    "&": r"D:\a&b\DrcomAutoLogin",
    "|": r"D:\a|b\DrcomAutoLogin",
    "<": r"D:\a<b\DrcomAutoLogin",
    ">": r"D:\a>b\DrcomAutoLogin",
    "换行": "D:\\a\nb\\DrcomAutoLogin",
}
_r5_danger_missed = {
    _k: au._cmd_path_unsafe_reason(_v) for _k, _v in _r5_danger.items()
    if not au._cmd_path_unsafe_reason(_v)
}
check("维护 R5-E5：含 % / ! / ^ / & / | / < / > / 换行的路径都返回非空中文原因",
      not _r5_danger_missed, repr(_r5_danger_missed))

# 写执行器函数：路径危险 → 拒绝生成（不落盘 + 中文 WARN）
_r5_evil_wrapper = os.path.join(_r5_root, "100%", "wrap.cmd")
_r5_cap.lines = []
_r5_wrap_raised = None
try:
    au._write_update_wrapper(_r5_evil_wrapper, "@echo off\r\n")
except au._CmdPathUnsafeError as _exc:
    _r5_wrap_raised = _exc
check("维护 R5-E5：_write_update_wrapper 在危险路径下拒绝生成（不写盘 + 中文 WARN）",
      isinstance(_r5_wrap_raised, au._CmdPathUnsafeError)
      and not os.path.isfile(_r5_evil_wrapper)
      and any("cmd 特殊字符" in l for l in _r5_cap.lines),
      repr((_r5_wrap_raised, _r5_cap.lines[-3:])))

# 启动升级：安装目录含 % → 一开始就拒绝，不生成任何升级脚本
_r5_base_before = au.BASE_DIR
_r5_unsafe_app = os.path.join(_r5_root, "100%", "app")
os.makedirs(_r5_unsafe_app, exist_ok=True)
_r5_cap.lines = []
_r5_launch_raised = None
try:
    au.BASE_DIR = _r5_unsafe_app
    au._launch_installer(os.path.join(_r5_root, "Fake-Setup.exe"), 8848, None)
except au._CmdPathUnsafeError as _exc:
    _r5_launch_raised = _exc
finally:
    au.BASE_DIR = _r5_base_before
_r5_launch_msg = str(_r5_launch_raised or "")
check("维护 R5-E5：安装目录含 cmd 特殊字符 → 不生成执行器 / 回滚脚本，中文 error 指向手动安装",
      isinstance(_r5_launch_raised, au._CmdPathUnsafeError)
      and "安装路径包含 cmd 特殊字符" in _r5_launch_msg
      and "请手动运行安装包" in _r5_launch_msg
      and not os.path.isfile(au._wrapper_path())
      and not os.path.isfile(au._health_probe_path())
      and not os.path.isfile(au._rollback_script_path()),
      repr((_r5_launch_msg, os.path.isfile(au._wrapper_path()))))

# 端到端：_do_update_now 在危险路径下按失败处理（不启动安装器、state=error）
_r5_nssm_stub = os.path.join(_r5_root, "tools", "nssm.exe")
os.makedirs(os.path.dirname(_r5_nssm_stub), exist_ok=True)
pathlib.Path(_r5_nssm_stub).write_bytes(b"stub")
_r5_saved_e2e = {k: au.__dict__.get(k, _R5_MISSING) for k in (
    "NSSM_PATH", "SCHTASKS_PATH", "BASE_DIR", "_check_disk_free_mb", "_load_config",
    "_probe_local_health", "_check_github_latest", "_download_installer", "_read_nssm_appexit")}
_r5_saved_popen = au.subprocess.Popen
_r5_popen_calls = []


def _r5_popen_guard(*a, **k):
    _r5_popen_calls.append(str(a[0]) if a else "")
    raise OSError("危险路径下不得启动安装器")


def _r5_e2e_download(url, dest_path, expected_size, progress_callback=None, expected_sha256=None):
    pathlib.Path(dest_path).write_bytes(b"fake-installer-bytes")
    return 21


_r5_e2e_result = None
_r5_cap.lines = []
try:
    au.NSSM_PATH = _r5_nssm_stub
    au.SCHTASKS_PATH = os.path.join(_r5_root, "no_schtasks_for_r5.exe")   # 回归时也别碰真计划任务
    au.BASE_DIR = _r5_unsafe_app
    au._check_disk_free_mb = lambda: 99999
    au._load_config = lambda: {"update_min_free_disk_mb": 0, "ui_port": 8848}
    au._probe_local_health = lambda *a, **k: (True, "http=200 ok=true")
    au._check_github_latest = lambda: ("9.9.9", "https://example.invalid/x.exe", "0" * 64, 21, None)
    au._download_installer = _r5_e2e_download
    au._read_nssm_appexit = lambda *a, **k: None
    au.subprocess.Popen = _r5_popen_guard
    _r5_e2e_result = au._do_update_now()
finally:
    for _k, _v in _r5_saved_e2e.items():
        if _v is _R5_MISSING:
            au.__dict__.pop(_k, None)
        else:
            au.__dict__[_k] = _v
    au.subprocess.Popen = _r5_saved_popen
_r5_e2e_log = _r5_upgrade_log()
check("维护 R5-E5：_do_update_now 在危险路径下按失败处理（state=error + 中文 error + WARN，不启动安装器）",
      isinstance(_r5_e2e_result, dict) and _r5_e2e_result.get("ok") is False
      and "安装路径包含 cmd 特殊字符" in (_r5_e2e_result.get("error") or "")
      and "请手动运行安装包" in (_r5_e2e_result.get("error") or "")
      and _r5_state.get("update_state") == "error"
      and "安装路径包含 cmd 特殊字符" in (_r5_state.get("update_last_error") or "")
      and not _r5_popen_calls
      and "安装路径包含 cmd 特殊字符" in _r5_e2e_log
      and "[WARN]" in _r5_e2e_log,
      repr((_r5_e2e_result, _r5_state.get("update_state"), _r5_popen_calls, _r5_e2e_log[-200:])))

# —— J6：build.ps1 的版本号从 version.py 读取（不再维护第 6 份字面量副本）——
_r5_build_ps1 = pathlib.Path("packaging/build.ps1").read_text(encoding="utf-8")
check("维护 R5-J6：build.ps1 不再硬编码 AppVersionText 版本字面量，且从 version.py 读取",
      "$AppVersionText  = 'v2." not in _r5_build_ps1
      and "version.py" in _r5_build_ps1
      and "^VERSION" in _r5_build_ps1
      and "Stop-Build" in _r5_build_ps1
      and "$AppVersionText = 'v' +" in _r5_build_ps1,
      "read_logic=%s" % ("version.py" in _r5_build_ps1))
_r5_versioning = pathlib.Path("docs/VERSIONING.md").read_text(encoding="utf-8")
check("维护 R5-J6：docs/VERSIONING.md 把 build.ps1 列为「自动读取，无需手改」",
      "自动读取" in _r5_versioning
      and "`packaging/build.ps1`" in _r5_versioning
      and "| `packaging/build.ps1` | banner |" not in _r5_versioning,
      "listed=%s" % ("`packaging/build.ps1`" in _r5_versioning))

# —— 沙箱收尾：还原注入点 / TEMP，别影响隐私守卫与版本一致性 ——
for _k, _v in _r5_saved.items():
    if _v is _R5_MISSING:
        au.__dict__.pop(_k, None)
    else:
        au.__dict__[_k] = _v
if _r5_saved_temp is None:
    os.environ.pop("TEMP", None)
else:
    os.environ["TEMP"] = _r5_saved_temp
shutil.rmtree(_r5_root, ignore_errors=True)


# —— R5-RETRY：上游下载有界重试（nssm.cc 偶发 503 实测）——
_wf5r = pathlib.Path(".github/workflows/build-installer.yml").read_text(encoding="utf-8")
_bp5r = pathlib.Path("packaging/build.ps1").read_text(encoding="utf-8")
check("维护 R5-RETRY：发版 CI 与本地构建的上游下载都有重试（一次 503 不再打红构建）",
      "Invoke-DownloadWithRetry" in _wf5r and "Invoke-DownloadWithRetry" in _bp5r
      and "MaxAttempts" in _wf5r and "MaxAttempts" in _bp5r)

# —— R6-供应链收口：issrc 中文语言文件固定到 commit（P2-4 / P3-15 收尾）——
# 上一轮 isl 还记在 not_locked（issrc main 分支浮动引用）。本轮固定到 issrc 某个 commit
# 并锁 SHA256；CI / 本地构建都从 lock 读 URL 与哈希，workflow 里不再出现 main 分支硬编码。
_wf6 = pathlib.Path(".github/workflows/build-installer.yml").read_text(encoding="utf-8")
_bp6 = pathlib.Path("packaging/build.ps1").read_text(encoding="utf-8")
_doc6 = pathlib.Path("docs/RELEASING.md").read_text(encoding="utf-8")
_isl6 = next((t for t in _lock_tools
              if str(t.get("name", "")).strip() == "chinese-simplified-isl"), None)
_isl6_version = str((_isl6 or {}).get("version", "")).strip()
_isl6_url = str((_isl6 or {}).get("url", "")).strip()
_isl6_sha = str((_isl6 or {}).get("sha256", "")).strip().lower()
_isl6_commit_ok = (len(_isl6_version) == 12
                   and all(c in "0123456789abcdef" for c in _isl6_version)
                   and _isl6_version in _isl6_url)
check("维护 R6-ISL：tools.lock.json 固定 chinese-simplified-isl（commit 前缀 + 64 位 SHA256 + required=false）",
      _isl6 is not None
      and _isl6_commit_ok
      and len(_isl6_sha) == 64 and all(c in "0123456789abcdef" for c in _isl6_sha)
      and isinstance(_isl6.get("size_bytes"), int) and _isl6["size_bytes"] > 1000
      and _isl6.get("required") is False
      and "/jrsoftware/issrc/" in _isl6_url and "/main/" not in _isl6_url
      and _isl6_sha not in _wf6 and _isl6_sha not in _bp6,
      repr(_isl6))

# 从 workflow 里抽 isl 步骤正文（避免把 Load 步骤里生成的 helper 文本一起算进来）
_wf6_isl_step = ""
if "- name: Locate ISCC and ensure Chinese language file" in _wf6:
    _wf6_isl_step = _wf6.split("- name: Locate ISCC and ensure Chinese language file", 1)[1] \
                       .split("- name:", 1)[0]
# 只看真正发起校验的调用行：注释里可以提「不传 -Required」，调用处必须真的没传
_isl6_assert_no_required = bool(_wf6_isl_step) and all(
    "-Required" not in _line
    for _line in _wf6_isl_step.splitlines() if "Assert-LockedDownload" in _line)
check("维护 R6-ISL：CI 的 isl 步骤从 lock 取 URL/哈希（dot-source + 重试 + Assert），不再硬编码 main 分支",
      "TOOL_LOCK_HELPER" in _wf6_isl_step
      and "TOOL_LOCK_PATH" in _wf6_isl_step
      and "Invoke-DownloadWithRetry" in _wf6_isl_step
      and "Assert-LockedDownload" in _wf6_isl_step
      and _isl6_assert_no_required
      and "Default.isl" in _wf6_isl_step
      and "raw.githubusercontent.com/jrsoftware/issrc/main" not in _wf6
      and "issrc/main" not in _wf6,
      "step_len=%d hardcoded=%s no_required=%s" % (len(_wf6_isl_step), "issrc/main" in _wf6,
                                                    _isl6_assert_no_required))

check("维护 R6-ISL：本地 build.ps1 也按 lock 取 isl 条目（缺条目才回退硬编码镜像）",
      "chinese-simplified-isl" in _bp6
      and "Get-ToolsLockEntryByName" in _bp6
      and "Test-ToolsLockedFile" in _bp6
      and "Get-ToolsLockEntryByName -Lock $script:ToolsLockDoc -Name $IslLockName" in _bp6,
      "name=%s lookup=%s" % ("chinese-simplified-isl" in _bp6,
                             "Get-ToolsLockEntryByName" in _bp6))

# build.ps1 编码要求：UTF-8 BOM + CRLF（PS 5.1 读中文需要 BOM；丢了会乱码 —— 上一轮踩过）
_bp6_raw = pathlib.Path("packaging/build.ps1").read_bytes()
check("维护 R6-ISL：packaging/build.ps1 保持 UTF-8 BOM + CRLF（PS 5.1 中文不乱码）",
      _bp6_raw.startswith(b"\xef\xbb\xbf")
      and b"\r\n" in _bp6_raw
      and b"\n" not in _bp6_raw.replace(b"\r\n", b""),
      "bom=%s" % _bp6_raw.startswith(b"\xef\xbb\xbf"))

check("维护 R6-ISL：not_locked 不再包含 isl（只保留 Inno 官方安装器 / choco 两条浮动项）",
      isinstance(_lock_doc.get("not_locked"), list)
      and not any("isl" in str(n.get("name", "")).lower()
                  or "issrc" in str(n.get("url", "")).lower()
                  for n in _lock_doc["not_locked"])
      and any("innosetup" in str(n.get("name", "")).lower()
              for n in _lock_doc["not_locked"])
      and any("choco" in str(n.get("name", "")).lower()
              for n in _lock_doc["not_locked"]),
      repr([n.get("name") for n in (_lock_doc.get("not_locked") or [])]))

check("维护 R6-ISL：docs/RELEASING.md 写明 workflow_dispatch 手动发版 / 上游有界重试 / 工具锁维护",
      "workflow_dispatch" in _doc6
      and "手动触发" in _doc6
      and "有界重试" in _doc6
      and "nssm.cc" in _doc6
      and "工具锁维护" in _doc6
      and "chinese-simplified-isl" in _doc6,
      "doc_len=%d" % len(_doc6))


# ============================================================
# 维护第七轮（2026-11 / v2.1.3.7）
# 对应 _维护报告/audit/core.md 的 C-16 / C-28 / C-29 / C-30、§4 I-8，
# 以及 build-P2-4 剩余项（ISCC 版本钉死）。
# ============================================================
import ctypes as _ctypes7  # noqa: E402

# —— C-16：静默升级后跨会话拉起托盘（纯函数 + 行为级 + 触发点结构）——
check("维护 R7-C16：非 Windows 不拉（原因可读）",
      _au.tray_relaunch_decision("posix", True, True, True)[0] is False
      and "Windows" in _au.tray_relaunch_decision("posix", True, True, True)[1])
check("维护 R7-C16：用户关了托盘自启 → 不拉",
      _au.tray_relaunch_decision("nt", False, True, True)[0] is False
      and "关闭" in _au.tray_relaunch_decision("nt", False, True, True)[1])
check("维护 R7-C16：pythonw.exe 缺失 → 不拉",
      _au.tray_relaunch_decision("nt", True, False, True)[0] is False
      and "pythonw" in _au.tray_relaunch_decision("nt", True, False, True)[1])
check("维护 R7-C16：tray.py 缺失 → 不拉",
      _au.tray_relaunch_decision("nt", True, True, False)[0] is False
      and "tray.py" in _au.tray_relaunch_decision("nt", True, True, False)[1])
check("维护 R7-C16：全都满足 → 拉（无原因串）",
      _au.tray_relaunch_decision("nt", True, True, True) == (True, ""))
check("维护 R7-C16：命令行逐字 = 双引号包裹的 pythonw + tray.py",
      _au.tray_relaunch_command("C:\\app\\python\\pythonw.exe", "C:\\app\\tray.py")
      == '"C:\\app\\python\\pythonw.exe" "C:\\app\\tray.py"')

# 行为级：dry_run 只算不拉；本机不是 LocalSystem，真调用必须回 ok=False + 可读原因（不抛）
_saved_base7 = _au.__dict__.get("BASE_DIR")
_had_base7 = "BASE_DIR" in _au.__dict__
_au.BASE_DIR = os.getcwd()          # 仓库根没有 python/pythonw.exe，决策必然停在「缺文件」
try:
    try:
        _rl7_dry = _au._relaunch_tray_in_user_session(dry_run=True)
        _rl7_dry_err = ""
    except Exception as _exc7:      # noqa: BLE001
        _rl7_dry, _rl7_dry_err = None, repr(_exc7)
    check("维护 R7-C16（行为级）：dry_run 返回结构化 dict、ok 为 bool、不抛异常",
          isinstance(_rl7_dry, dict) and isinstance(_rl7_dry.get("ok"), bool)
          and _rl7_dry_err == "",
          _rl7_dry_err or repr(_rl7_dry))
    check("维护 R7-C16（行为级）：dry_run 不真正拉起（ok=False + 中文原因）",
          isinstance(_rl7_dry, dict) and _rl7_dry.get("ok") is False
          and bool(_rl7_dry.get("reason")), repr(_rl7_dry))
    try:
        _rl7_real = _au._relaunch_tray_in_user_session()
        _rl7_real_err = ""
    except Exception as _exc7b:     # noqa: BLE001
        _rl7_real, _rl7_real_err = None, repr(_exc7b)
    check("维护 R7-C16（行为级）：真实调用在非 SYSTEM 环境只回失败原因（绝不抛 / 绝不拉进程）",
          _rl7_real_err == "" and isinstance(_rl7_real, dict)
          and _rl7_real.get("ok") is False and bool(_rl7_real.get("reason")),
          _rl7_real_err or repr(_rl7_real))
    _wts7 = _au._wts_query_user_token()
    check("维护 R7-C16：辅助拆分可单独导入调用（_wts_query_user_token 返回失败 dict 不抛）",
          isinstance(_wts7, dict) and isinstance(_wts7.get("ok"), bool)
          and _wts7.get("ok") is False and bool(_wts7.get("reason")), repr(_wts7))
    check("维护 R7-C16：_enable_privilege 拿不到 SeTcbPrivilege 时返回 False（不抛）",
          _au._enable_privilege("SeTcbPrivilege") is False)
finally:
    if _had_base7:
        _au.BASE_DIR = _saved_base7
    else:
        _au.__dict__.pop("BASE_DIR", None)

# 行为级：启动钩子的「要不要拉托盘」门 —— 没升级过绝不拉；刚升级成功才拉一次
_saved_f2 = {}
for _k in ("_cleanup_update_leftovers", "_ensure_nssm_appexit_sane",
           "_ensure_tray_autostart_sane", "_start_deferred_runner_recheck",
           "_report_update_runner_result", "_relaunch_tray_in_user_session",
           "LOG_DIR", "UPGRADE_LOG_FILE", "BASE_DIR", "logger", "STATE", "STATE_LOCK"):
    _saved_f2[_k] = (_k in _au.__dict__, _au.__dict__.get(_k))
_f2_dir = tempfile.mkdtemp(prefix="au_f2_")
_f2_calls = []


def _f2_stub_ok(**_kw):
    _f2_calls.append(1)
    return {"ok": True, "pid": 4321, "reason": "", "command": "x"}


def _f2_stub_fail(**_kw):
    return {"ok": False, "pid": None, "reason": "测试原因：拿不到 SeTcbPrivilege", "command": "x"}


try:
    _au.LOG_DIR = _f2_dir
    _au.UPGRADE_LOG_FILE = os.path.join(_f2_dir, "upgrade.log")
    _au.BASE_DIR = _f2_dir
    _au.logger = logging.getLogger("smoke_r7_au")   # 启动钩子里 _log_upgrade / WARN 分支要用
    _au.STATE = {}
    _au.STATE_LOCK = _th2.RLock()
    _au._cleanup_update_leftovers = lambda: None
    _au._ensure_nssm_appexit_sane = lambda: "Ignore"
    _au._ensure_tray_autostart_sane = lambda: {"action": "keep", "want": True, "current": "x"}
    _au._start_deferred_runner_recheck = lambda *_a, **_k: None
    _au._report_update_runner_result = lambda *_a, **_k: False
    _au._relaunch_tray_in_user_session = _f2_stub_ok
    _au._post_upgrade_startup()
    _f2_none = len(_f2_calls)
    _au._write_update_attempt(_au.VERSION, count=1, state="attempted")
    _au._post_upgrade_startup()
    _f2_one = len(_f2_calls)
    _f2_log_ok = pathlib.Path(_au.UPGRADE_LOG_FILE).read_text(encoding="utf-8")
    _au._relaunch_tray_in_user_session = _f2_stub_fail
    _au._write_update_attempt(_au.VERSION, count=1, state="attempted")
    _au._post_upgrade_startup()
    _f2_log_fail = pathlib.Path(_au.UPGRADE_LOG_FILE).read_text(encoding="utf-8")
finally:
    for _k, (_had, _v) in _saved_f2.items():
        if _had:
            _au.__dict__[_k] = _v
        else:
            _au.__dict__.pop(_k, None)

check("维护 R7-C16（行为级）：没升级过 → 绝不拉托盘；刚升级成功 → 拉一次并记 pid",
      _f2_none == 0 and _f2_one == 1
      and "托盘已在当前登录会话重新拉起（pid=4321）" in _f2_log_ok,
      "none=%d one=%d" % (_f2_none, _f2_one))
check("维护 R7-C16（行为级）：拉起失败 → 日志带中文原因 + 「下次登录会由自启项启动」",
      "托盘未能在当前会话自动拉起（测试原因：拿不到 SeTcbPrivilege）" in _f2_log_fail
      and "下次登录会由自启项启动" in _f2_log_fail, repr(_f2_log_fail[-160:]))

_src_upd7 = pathlib.Path("auto_update.py").read_text(encoding="utf-8")
_f7_post = _src_upd7.find("def _post_upgrade_startup")
_f7_loop = _src_upd7.find("def _auto_update_loop")
_f7_tray = _src_upd7.find("_ensure_tray_autostart_sane()")
_f7_call = _src_upd7.find("_relaunch_tray_in_user_session()")
_f7_guard = _src_upd7.rfind("if just_upgraded:", 0, _f7_call) if _f7_call >= 0 else -1
check("维护 R7-C16：触发点在启动钩子里「托盘自启」之后（不是另起一条路径）",
      -1 < _f7_loop < _f7_post < _f7_tray < _f7_call,
      "loop=%d post=%d tray=%d call=%d" % (_f7_loop, _f7_post, _f7_tray, _f7_call))
check("维护 R7-C16：调用受 just_upgraded 守卫（不是每次服务启动都拉）",
      _f7_guard > _f7_post and 0 < _f7_call - _f7_guard < 400
      and _src_upd7.count("if just_upgraded:") == 1,
      "guard=%d call=%d" % (_f7_guard, _f7_call))
check("维护 R7-C16：just_upgraded 只由「目标版本 == 当前版本」的成功分支置真",
      _src_upd7.find("just_upgraded = False") > 0
      and _src_upd7.find("_compare_versions(VERSION, target) == 0")
      < _src_upd7.find("just_upgraded = True")
      and _src_upd7.find("just_upgraded = True") < _f7_call)
check("维护 R7-C16：服务每次启动路径（_auto_update_loop）里没有拉托盘调用",
      _f7_loop >= 0 and "_relaunch_tray_in_user_session" not in _src_upd7[_f7_loop:_f7_post]
      and "just_upgraded" not in _src_upd7[_f7_loop:_f7_post])
_BS7 = chr(92)
check("维护 R7-C16：ctypes 关键调用齐全（会话令牌 / 复制令牌 / 用户进程 / 桌面名 / 提权）",
      all(_s in _src_upd7 for _s in (
          "WTSQueryUserToken", "DuplicateTokenEx", "CreateProcessAsUserW",
          "AdjustTokenPrivileges", "SeTcbPrivilege",
          'si.lpDesktop = "winsta0' + _BS7 + _BS7 + 'default"',
          "ctypes.create_unicode_buffer(command)")))
check("维护 R7-C16：成功 / 失败都写一行中文 upgrade.log（含 C-16 标注）",
      "托盘已在当前登录会话重新拉起" in _src_upd7
      and "C-16" in _src_upd7.split("托盘已在当前登录会话重新拉起", 1)[1][:120]
      and "下次登录会由自启项启动" in _src_upd7)

# —— C-30：指纹补头部哈希（同长度、同秒内改写必须能被发现）+ 日志轮转失败兜底 ——
_sig7 = os.path.join(tmp, "tray_sig_same_len.py")
pathlib.Path(_sig7).write_text("AAAA", encoding="utf-8")
_sig7_a = _tray.code_signature(_sig7)
_sig7_st = os.stat(_sig7)
pathlib.Path(_sig7).write_text("BBBB", encoding="utf-8")
os.utime(_sig7, (_sig7_st.st_atime, _sig7_st.st_mtime))   # 强行保持 mtime / size 不变
_sig7_b = _tray.code_signature(_sig7)
check("维护 R7-C30（行为级）：mtime+size 完全相同、只改内容 → 指纹仍变（关键回归）",
      isinstance(_sig7_a, tuple) and len(_sig7_a) == 3
      and _sig7_a[:2] == _sig7_b[:2] and _sig7_a != _sig7_b,
      repr((_sig7_a, _sig7_b)))
check("维护 R7-C30：指纹第三段 = 16 位小写十六进制（sha256 前 16 位）",
      isinstance(_sig7_a[2], str) and len(_sig7_a[2]) == 16
      and all(c in "0123456789abcdef" for c in _sig7_a[2]), repr(_sig7_a))

_saved7 = (_tray.LOG_DIR, _tray.LOG_FILE, _tray.LOG_MAX_BYTES,
           _tray._LOG_ROTATE_FAIL_STREAK)
_real_replace7 = os.replace
_log7_dir = tempfile.mkdtemp(prefix="tray_log7_")
try:
    _tray.LOG_DIR = _log7_dir
    _tray.LOG_FILE = os.path.join(_log7_dir, "tray.log")
    _tray.LOG_MAX_BYTES = 200
    _tray._LOG_ROTATE_FAIL_STREAK = 0
    pathlib.Path(_tray.LOG_FILE).write_text("x" * 600, encoding="utf-8")

    def _bad_replace7(_a, _b):
        raise OSError("模拟日志文件被占用")

    os.replace = _bad_replace7
    _log7_err = ""
    try:
        _tray._log("轮转失败兜底冒烟")
    except Exception as _exc7c:     # noqa: BLE001
        _log7_err = repr(_exc7c)
    finally:
        os.replace = _real_replace7
    _log7_txt = pathlib.Path(_tray.LOG_FILE).read_text(encoding="utf-8")
    _log7_size = os.path.getsize(_tray.LOG_FILE)
    check("维护 R7-C30（行为级）：轮转失败 → 截断重写（文件有界）+ 中文说明，且不抛异常",
          _log7_err == "" and _log7_size < 600
          and "日志轮转失败，已截断重写" in _log7_txt,
          "err=%s size=%d" % (_log7_err, _log7_size))

    # 连续失败 >= 3 次 → 本次进程放弃 rename 尝试，但仍截断（不无界增长）
    _tray._LOG_ROTATE_FAIL_STREAK = 3
    pathlib.Path(_tray.LOG_FILE).write_text("y" * 600, encoding="utf-8")
    _replace7_calls = []
    os.replace = lambda _a, _b: _replace7_calls.append((_a, _b))
    try:
        _tray._log("放弃轮转尝试")
    finally:
        os.replace = _real_replace7
    check("维护 R7-C30（行为级）：连续失败≥3 次后放弃 rename（仍截断，文件有界）",
          not _replace7_calls and os.path.getsize(_tray.LOG_FILE) < 600,
          "calls=%d" % len(_replace7_calls))
finally:
    os.replace = _real_replace7
    (_tray.LOG_DIR, _tray.LOG_FILE, _tray.LOG_MAX_BYTES,
     _tray._LOG_ROTATE_FAIL_STREAK) = _saved7

# —— C-28 / C-29：--self-test 不再假通过；互斥体细节 ——
_src_tray7 = pathlib.Path("tray.py").read_text(encoding="utf-8")
check("维护 R7-C28：--self-test 互斥体分支返回独立退出码 7（结构级）",
      "EXIT_SELF_TEST_SKIP = 7" in _src_tray7
      and "return EXIT_SELF_TEST_SKIP" in _src_tray7
      and "托盘已在运行，自检跳过" in _src_tray7)
check("维护 R7-C28：--help 与 --check / --test-event 分支不变（开工前后语义一致）",
      '"--help" in argv or "-h" in argv' in _src_tray7
      and "--check" in _src_tray7 and "--test-event" in _src_tray7)
check("维护 R7-C29：CreateMutexW 失败（句柄为 0）→ 中文警告 + 返回 (None, True)",
      "if not handle:" in _src_tray7 and "return None, True" in _src_tray7
      and "避免双实例" in _src_tray7)
check("维护 R7-C29：already 分支与正常退出都关闭句柄（单实例语义不变）",
      "def _close_handle(handle)" in _src_tray7
      and _src_tray7.count("_close_handle(") >= 5
      and "app.mutex_handle = None" in _src_tray7)

if os.name == "nt":
    # 行为级：自己先持有同名互斥体，再真跑一次 tray.py --self-test 读退出码
    _k327 = _ctypes7.windll.kernel32
    _k327.CreateMutexW.restype = _ctypes7.c_void_p
    _mut7 = _k327.CreateMutexW(None, False, _tray.MUTEX_NAME)
    try:
        _proc7 = subprocess.run(
            [sys.executable, os.path.join(os.getcwd(), "tray.py"), "--self-test"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        _rc7 = _proc7.returncode
    finally:
        if _mut7:
            _k327.CloseHandle(_mut7)
    _out7 = _proc7.stdout.decode("utf-8", errors="replace")
    check("维护 R7-C28（行为级）：托盘在运行时 --self-test 退出码 = 7（SKIP，不再假通过）",
          _rc7 == 7, "rc=%s stdout=%r" % (_rc7, _out7[-200:]))
    check("维护 R7-C28（行为级）：明确输出「自检跳过」原因（不是静默返回）",
          "自检跳过" in _out7, repr(_out7[-200:]))
else:
    print("SKIP  维护 R7-C28（行为级）：非 Windows，跳过「真子进程 + 互斥体」退出码断言，"
          "上面已有结构级断言兜底")

# —— build-P2-4 剩余项：ISCC 版本钉死 6.7.1 ——
_wf7 = pathlib.Path(".github/workflows/build-installer.yml").read_text(encoding="utf-8")
_lock7 = pathlib.Path("tools.lock.json").read_text(encoding="utf-8")
_doc7 = pathlib.Path("docs/RELEASING.md").read_text(encoding="utf-8")
check("维护 R7-ISCC：CI 主路径按 6.7.1 钉版安装（choco --version=6.7.1）",
      "choco install innosetup --version=6.7.1 --no-progress -y" in _wf7)
check("维护 R7-ISCC：装完读注册表 DisplayVersion 并与 6.7.1 比对，不一致 throw",
      "DisplayVersion" in _wf7 and "6.7.1" in _wf7
      and "throw" in _wf7.split("DisplayVersion", 1)[1][:2500])
check("维护 R7-ISCC：官方静默安装器兜底路径明确警告「版本未钉死」",
      "兜底" in _wf7 and "未钉死" in _wf7)
check("维护 R7-ISCC：tools.lock 的 not_locked 两条说明已更新（版本钉死 / 内容哈希仍不校验）",
      "6.7.1" in _lock7 and "内容哈希" in _lock7 and "钉死" in _lock7)
check("维护 R7-ISCC：发版手册写明 ISCC 升级步骤（改 version + 核对注册表 + 本地实测）",
      "6.7.1" in _doc7 and "DisplayVersion" in _doc7 and "本地实测" in _doc7)


# ---- 隐私守卫（v2.0.4.0）：本机真机凭据不得进入任何被 git 跟踪的文件 ----
# 这是「推上去之前」的最后一道闸：CI 上没有 password.txt / config.json，
# 整段会自动 SKIP，不会误报；本地开发跑冒烟时才会真正扫描。
import json  # noqa: E402
import subprocess  # noqa: E402


def _git_rc(git_args):
    """跑 git 并返回退出码；git 不可用 / 非仓库 → None。"""
    try:
        proc = subprocess.run(["git"] + git_args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=os.getcwd())
    except OSError:
        return None
    return proc.returncode


def _data_dirs():
    """需要扫描的「数据目录」：当前目录 + 可选的 DRCOM_DATA_DIR。

    真机安装目录里才有 password.txt / config.json（仓库根目录里没有），
    所以想在本地把「安装目录的凭据」拿去反查仓库时，先设置：
        $env:DRCOM_DATA_DIR = 'D:\\Program Files\\DrcomAutoLogin'; python _smoke_static.py
    """
    dirs = [pathlib.Path(".")]
    extra = os.environ.get("DRCOM_DATA_DIR", "").strip()
    if extra:
        dirs.append(pathlib.Path(extra))
    return dirs


def _local_secrets():
    """从本机 password.txt / config.json 收集「绝不该入库」的字符串。"""
    found = []
    seen = set()
    for data_dir in _data_dirs():
        pwd_file = data_dir / "password.txt"
        if pwd_file.is_file():
            try:
                raw = pwd_file.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                raw = ""
            for line in raw.splitlines():
                s = line.strip()
                # 安装包自带的模板提示行本身就在源码里，扫它只会误报
                if len(s) >= 6 and not svc._is_password_hint(s) and s not in seen:
                    seen.add(s)
                    found.append(("%s\\password.txt" % data_dir, s))
        cfg_file = data_dir / "config.json"
        if cfg_file.is_file():
            try:
                cfg = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                cfg = {}
            account = str(cfg.get("account") or "").strip()
            if len(account) >= 6 and account not in seen:
                seen.add(account)
                found.append(("%s\\config.json#account" % data_dir, account))
    return found


_local = _local_secrets()
if not _local:
    print("SKIP  隐私守卫：本机无 password.txt / config.json.account（CI 环境正常）")
else:
    for _label, _secret in _local:
        _rc = _git_rc(["grep", "-n", "--text", "-F", "-e", _secret, "--", "."])
        if _rc is None:
            print("SKIP  隐私守卫：git 不可用，无法扫描")
            break
        check("隐私守卫：%s 未出现在入库文件中" % _label, _rc == 1,
              "命中！立刻把该字符串从源码 / 文档 / 截图里删掉" if _rc == 0 else "")

# 敏感运行期文件必须始终被 .gitignore 覆盖（本机有没有这些文件都要成立）
for _rel in ("password.txt", "config.json", "logs/campus_login.log",
             "logs/upgrade.log", "logs/update_attempt.json",
             "logs/installer-silent.log", "packaging/output/x.exe"):
    _rc = _git_rc(["check-ignore", "-q", _rel])
    if _rc is None:
        print("SKIP  隐私守卫：git 不可用，跳过 .gitignore 检查")
        break
    check("隐私守卫：%s 已被 .gitignore 覆盖" % _rel, _rc == 0)

# ---- 版本一致性 ----

import version
iss = pathlib.Path("packaging/setup.iss").read_text(encoding="utf-8", errors="replace")
check("版本一致 version.py vs setup.iss", ('#define MyAppVersion "%s"' % version.VERSION) in iss)
check("版本 = 2.1.3.7", version.VERSION == "2.1.3.7", version.VERSION)
check("v2.1.0.0 代号跟着版本线走（2.1 = Vega 织女星，且 setup.iss 同步）",
      version.VERSION.startswith("2.1.") and version.CODENAME == "Vega"
      and version.CODENAME_CN == "织女星" and '#define MyAppCodename "Vega"' in iss,
      "%s / %s" % (version.CODENAME, version.CODENAME_CN))
check("v2.0.4.0 版本代号在位", bool(getattr(version, "CODENAME", "")) and bool(getattr(version, "CODENAME_CN", "")),
      "%s / %s" % (getattr(version, "CODENAME", ""), getattr(version, "CODENAME_CN", "")))

# v2.1.2.0：版本字面量同步补齐 —— 这几个位置以前只改了文件头，banner / NSSM 服务描述
# 一路漂到了 v2.0.14.0 / v2.1.0.0（用户 `sc qc DrcomAutoLogin` 看到的描述是过期的 ✗）。
check("v2.1.2.0 install.bat 的 banner 与 NSSM 服务描述也同步版本号",
      ("Windows 服务安装 (v%s)" % version.VERSION) in _inst14
      and ('DrcomAutoLogin Description "星尘闪连 (Stardust Flash Link) - Dr.COM 校园网自动登录（v%s）"'
           % version.VERSION) in _inst14)
check("v2.1.2.0 uninstall.bat 的 banner 也同步版本号",
      ("服务 - 卸载 (v%s)" % version.VERSION) in _uinst14)
check("v2.1.2.0 setup.iss 的 NSSM 服务描述改用 {#MyAppVersion}（不再写死版本号）",
      "DrcomAutoLogin Description \"星尘闪连 (Stardust Flash Link) - Dr.COM 校园网自动登录（v{#MyAppVersion}）\""
      in _iss_src)

print("\n结果：%d 项失败 / %d 项检查" % (len(FAILS), TOTAL[0]))
sys.exit(1 if FAILS else 0)
