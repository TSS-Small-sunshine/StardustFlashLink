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

# ---- v2.0.4.2：自动升级执行器（任务计划程序 + 看门狗 + 退出码）----
# 真机证据：installer 直启时 nssm 一停服务就把它连同 Job 一起杀掉 →
# installer-silent.log 都没生成、版本号不变、服务停在 StopPending。
check("v2.0.4.2 installer 由任务计划程序拉起", "schtasks" in src_upd and '"/create"' in src_upd)
check("v2.0.4.2 任务名单含 desktopicon",
      "/TASKS=desktopicon,startservice" in src_upd)   # 只传 startservice 会漏掉公共桌面图标
check("v2.0.4.2 包装脚本落盘 installer 退出码", "installer_rc=%ERRORLEVEL%" in src_upd)
check("v2.0.4.2 启动钩子读执行器结果", "_report_update_runner_result()" in src_upd)
check("v2.0.4.2 升级残留会被清理", "_cleanup_update_leftovers()" in src_upd)
check("v2.0.4.2 不再用备份 hash 误判升级成功",
      "升级完成（v{}）：当前脚本与备份不同" not in src_upd)
check("v2.0.4.2 AppExit 按 nssm 子键结构读取",
      "APPEXIT_SUBKEY" in src_upd and "QueryValueEx(k, APPEXIT_DEFAULT_VALUE)" in src_upd)
check("v2.0.4.2 nssm set AppExit 拆成两个 argv",
      '"AppExit", subparam.strip(), value.strip()' in src_upd)

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
check("版本 = 2.0.4.2", version.VERSION == "2.0.4.2", version.VERSION)
check("v2.0.4.0 版本代号在位", bool(getattr(version, "CODENAME", "")) and bool(getattr(version, "CODENAME_CN", "")),
      "%s / %s" % (getattr(version, "CODENAME", ""), getattr(version, "CODENAME_CN", "")))

print("\n结果：%d 项失败 / %d 项检查" % (len(FAILS), TOTAL[0]))
sys.exit(1 if FAILS else 0)
