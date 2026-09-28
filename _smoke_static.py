# -*- coding: utf-8 -*-
"""静态/单元冒烟测试（覆盖 v2.0.2.4 安全项 + v2.0.3.0 前端/品牌路由 + v2.0.4.0 修复项）。

跑法：python _smoke_static.py（在 DrcomAutoLogin-Windows 目录下）
隐私守卫：想连真机安装目录一起查，先设 DRCOM_DATA_DIR（见文末「隐私守卫」）。
"""
import pathlib
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
check("v2.0.4.5 password 写盘用唯一 tmp 名", "PASSWORD_FILE, os.getpid()" in src_svc)
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
check("v2.0.8.0 状态页有连接质量卡片",
      'id="card-quality"' in src_web and 'id="kpi-uptime"' in src_web
      and 'id="qbars"' in src_web)
check("v2.0.8.0 柱图是纯 CSS（不引入图表库）",
      ".qbars" in src_web and "chart.js" not in src_web.lower()
      and "echarts" not in src_web.lower())
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
check("版本 = 2.0.8.0", version.VERSION == "2.0.8.0", version.VERSION)
check("v2.0.4.0 版本代号在位", bool(getattr(version, "CODENAME", "")) and bool(getattr(version, "CODENAME_CN", "")),
      "%s / %s" % (getattr(version, "CODENAME", ""), getattr(version, "CODENAME_CN", "")))

print("\n结果：%d 项失败 / %d 项检查" % (len(FAILS), TOTAL[0]))
sys.exit(1 if FAILS else 0)
