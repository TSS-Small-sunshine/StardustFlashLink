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
check("v2.0.4.5 config 写盘用唯一 tmp 名", '"{}.{}.tmp"' in src_svc and "CONFIG_FILE, os.getpid()" in src_svc)
check("v2.0.4.5 password 写盘用唯一 tmp 名",
      "os.getpid()" in src_svc and "os.replace(tmp, path)" in src_svc)
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
check("v2.0.7.1 脚本指纹读得到（mtime + size）",
      isinstance(_sig1, tuple) and len(_sig1) == 2
      and _sig1[1] == os.path.getsize(_sig_tmp))
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
check("v2.0.4.2 AppExit 按 nssm 子键结构读取",
      "APPEXIT_SUBKEY" in src_upd and "QueryValueEx(k, APPEXIT_DEFAULT_VALUE)" in src_upd)
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
        # 注意 newline=""：文本模式的通用换行转换会把 \r\n 变成 \r\r\n，cmd 会整行失效。
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
check("v2.0.11.0 执行器：缺件（python / 回滚脚本 / 备份）落 rollback=SKIP",
      _wrap_rb.count("echo rollback=SKIP") == 3, str(_wrap_rb.count("echo rollback=SKIP")))
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
_rel_head = pathlib.Path("packaging/RELEASE-NOTES.md").read_text(
    encoding="utf-8", errors="replace").splitlines()[0]
_wf_src = pathlib.Path(".github/workflows/build-installer.yml").read_text(
    encoding="utf-8", errors="replace")
_ver_mod = importlib.import_module("version")   # 本文件后段才 import version，这里自带 ✓
check("v2.1.0.0 补丁：RELEASE-NOTES 头部用 {{CODENAME}} 占位（不再硬编码星名）",
      "{{CODENAME}}" in _rel_head and "{{CODENAME_CN}}" in _rel_head
      and "Sirius" not in _rel_head and _ver_mod.CODENAME not in _rel_head,
      _rel_head[:100])
check("v2.1.0.0 补丁：CI 从 version.py 读代号并替换这两个占位符",
      "APP_CODENAME=$cn" in _wf_src
      and "$body.Replace('{{CODENAME}}', $env:APP_CODENAME)" in _wf_src
      and _wf_src.count("{{CODENAME}}") >= 2 and _wf_src.count("{{CODENAME_CN}}") >= 2)

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
check("版本 = 2.1.2.0", version.VERSION == "2.1.2.0", version.VERSION)
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
