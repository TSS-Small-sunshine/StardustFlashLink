# -*- coding: utf-8 -*-
"""tray.py — 登录会话里的托盘小程序（B2：断线通知 + 托盘图标；B3：唤醒 / 换网立刻重连）。

为什么需要它：服务跑在 LocalSystem / **session 0**，跟用户桌面会话隔离，弹不出任何通知
（`Shell_NotifyIcon` / 气泡在服务里调等于扔进黑洞）。所以「断线通知 + 托盘图标」必须由
一个**随登录启动的用户进程**来做：它每 10 秒轮询服务在本机的 `/api/status`，状态迁移
时弹气泡，右键菜单提供「打开配置页 / 立即登录 / 打开日志目录 / 退出」。

v2.0.7.0（B3）新增**事件驱动重连**：托盘本来就是用户会话里的 GUI 程序（有消息循环），
于是顺手当事件源 —— 收 ``WM_POWERBROADCAST``（睡眠唤醒；等几秒让网卡连上再触发）
与 ``NotifyAddrChange``（插网线 / 换 Wi-Fi / DHCP 换地址），事件一到就 POST 一次
``/api/login``。不用再等服务那边「最长等一个检查周期」的节奏。

只依赖标准库（ctypes 直调 Win32），跟主程序一样不引入第三方依赖。

跑法（安装器写进 HKCU 的 Run 键）：
    "{app}\\python\\pythonw.exe" "{app}\\tray.py"

自检（真加一次图标 + 弹一次气泡，用来确认环境 OK；会看到气泡一闪）：
    "{app}\\python\\python.exe" "{app}\\tray.py" --self-test

单次检查（不建图标，只打印一次判定结果；CI / 排障用）：
    "{app}\\python\\python.exe" "{app}\\tray.py" --check

模拟一次事件（本机实测用：走的就是真实处理路径，事件与结论都写进日志）：
    "{app}\\python\\python.exe" "{app}\\tray.py" --test-event wake     # 当作刚睡醒
    "{app}\\python\\python.exe" "{app}\\tray.py" --test-event net      # 当作网络变了
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes

IS_WIN = os.name == "nt"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or APP_DIR,
                       "DrcomAutoLogin")
LOG_FILE = os.path.join(LOG_DIR, "tray.log")
LOG_MAX_BYTES = 1 * 1024 * 1024

POLL_SEC = 10                  # 轮询间隔
DOWN_STREAK_TO_NOTIFY = 3      # 连续几次拿不到服务才报「服务未响应」（避免偶发抖动刷屏）
DEFAULT_UI_PORT = 8848
HTTP_TIMEOUT = 5

# —— v2.0.7.0（B3）：事件驱动重连 ——
RECONNECT_MIN_GAP_SEC = 5      # 事件去抖：唤醒与地址变化常常连着来，别打成一串
WAKE_SETTLE_SEC = 4            # 唤醒后等几秒再触发：网卡 / 无线这时才刚连上


# ============================================================
# 日志 / 配置读取
# ============================================================
def _log(msg):
    """写一行日志（托盘没有控制台，只能落盘；失败也绝不抛）。"""
    line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if not os.path.isdir(LOG_DIR):
            os.makedirs(LOG_DIR)
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            try:
                os.replace(LOG_FILE, LOG_FILE + ".1")
            except OSError:
                pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def ui_port():
    """从安装目录的 config.json 读 ui_port（用户改过端口也要能连上），失败回默认值。"""
    try:
        with open(os.path.join(APP_DIR, "config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        port = cfg.get("ui_port")
        if isinstance(port, int) and 1024 <= port <= 65535:
            return port
    except (OSError, ValueError):
        pass
    return DEFAULT_UI_PORT


def api_base():
    return "http://127.0.0.1:%d" % ui_port()


# ============================================================
# 与服务的通讯（只用本机 HTTP 接口，不碰服务内部状态）
# ============================================================
def _request(path, method="GET", timeout=HTTP_TIMEOUT):
    """发一个请求；返回 (status_code, body_text)。异常 → (None, 错误文案)。"""
    req = urllib.request.Request(api_base() + path, method=method)
    # 写接口要求这个自定义头（跨站简单请求带不了）；读接口带着也无害
    req.add_header("X-Requested-With", "DrcomUI")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, str(exc)


def poll_status():
    """取一次 /api/status。返回 dict；拿不到时返回 {"_reachable": False, "_error": ...}。"""
    code, body = _request("/api/status")
    if code != 200:
        return {"_reachable": False, "_error": body or ("HTTP %s" % code)}
    try:
        data = json.loads(body)
    except ValueError as exc:
        return {"_reachable": False, "_error": "响应不是 JSON: %s" % exc}
    if not isinstance(data, dict):
        return {"_reachable": False, "_error": "响应不是对象"}
    data["_reachable"] = True
    return data


def trigger_login():
    """POST /api/login（异步触发一次检查）。返回 (ok, 文案)。"""
    code, body = _request("/api/login", method="POST")
    if code == 200:
        return True, "已触发一次登录检查"
    if code is None:
        return False, "服务未响应：%s" % body
    return False, "触发失败（HTTP %s）" % code


# ============================================================
# 状态归类 + 通知判定（纯函数，单测在这两块上做）
# ============================================================
def classify(status):
    """把 /api/status 归一成四种 kind 之一：

    - ``"down"``    服务没响应（服务没装 / 没启动 / 端口被占）
    - ``"ok"``      服务在，且当前在线
    - ``"off"``     服务在，但当前不在线（掉线 / 尚未登录）
    - ``"unknown"`` 服务在，但状态未知（online 为 null，通常是刚启动还没查过）
    """
    if not status or not status.get("_reachable"):
        return "down"
    online = status.get("online")
    if online is True:
        return "ok"
    if online is False:
        return "off"
    return "unknown"


def status_text(status):
    """给 tooltip / 菜单用的一行人话。"""
    kind = classify(status)
    if kind == "down":
        return "服务未响应"
    ssid = status.get("current_ssid")
    extra = "（Wi-Fi %s）" % ssid if ssid else ""
    if kind == "ok":
        return "已登录 ✓" + extra
    if kind == "off":
        err = status.get("last_error") or "未登录"
        return "未登录 · %s" % err
    return "状态未知" + extra


# ============================================================
# 事件驱动重连（B3）：电源事件 → 事件名；事件去抖（纯函数，单测在这两块上做）
# ============================================================
PBT_APMRESUMECRITICAL = 0x0006     # 从关键挂起恢复
PBT_APMRESUMESUSPEND = 0x0007      # 从睡眠恢复（用户按键 / 开盖）
PBT_APMRESUMEAUTOMATIC = 0x0012    # 从睡眠自动恢复（定时器 / 网络唤醒）


def power_event_kind(wparam):
    """``WM_POWERBROADCAST`` 的 wparam → 事件名；不是「醒过来」就返回 None。

    只认三种恢复：手动恢复 / 自动恢复 / 关键恢复。
    进入睡眠（``PBT_APMSUSPEND``）与电源状态变化（``PBT_POWERSETTINGCHANGE``）一律忽略 ——
    睡着的时候不需要重连，醒着时那些事件也跟「该不该重新登录」无关。
    """
    if wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMECRITICAL):
        return "从睡眠唤醒"
    return None


def should_reconnect(last_at, now, gap=RECONNECT_MIN_GAP_SEC):
    """事件去抖：距上次触发不足 ``gap`` 秒就跳过。纯函数。

    ``last_at is None`` = 托盘起来后还没因事件触发过 → 允许。
    没有这道闸，唤醒时「电源广播 + 无线重连 + DHCP 续租」会连打三四个登录请求。
    """
    return last_at is None or (now - last_at) >= gap


def code_signature(path):
    """脚本的「版本指纹」= ``(mtime, size)``；读不到就返回 None。

    v2.0.7.1：升级会替换安装目录里的 `tray.py`，但**正在跑的那个进程还是旧代码** ——
    安装器再拉一个实例也会被单实例互斥体挡掉（v2.0.7.0 真机验证时发现：升级后 PID 没变，
    新装的唤醒/换网重连等于白装 ✗）。所以托盘自己盯着这个指纹，一变就换个新进程接着跑。
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (int(st.st_mtime), int(st.st_size))


def decide_events(prev_kind, cur_kind, down_streak):
    """状态迁移 → 要弹的通知。纯函数，返回 list[(标题, 正文, 级别)]，级别 ∈ {info, warn}。

    规则（防刷屏）：
    - ``prev_kind is None``（托盘刚起来）→ **不弹**（开机时服务可能还在启动，别吓人）；
    - 只有**连续** ``DOWN_STREAK_TO_NOTIFY`` 次拿不到服务，才报「服务未响应」；
    - 掉线 → 警告；恢复在线 → 提示；这两类只弹一次（同一状态不重复弹）。
    """
    events = []
    if prev_kind is None or prev_kind == cur_kind:
        return events
    if cur_kind == "down":
        if down_streak >= DOWN_STREAK_TO_NOTIFY:
            events.append(("服务未响应",
                           "DrcomAutoLogin 服务没有响应。可以试试：打开配置页看看，或重启服务。",
                           "warn"))
    elif cur_kind == "off":
        if prev_kind in ("ok", "unknown"):
            events.append(("已掉线",
                           "校园网连接已断开，服务正在尝试重新登录…",
                           "warn"))
    elif cur_kind == "ok":
        if prev_kind in ("off", "down", "unknown"):
            events.append(("已恢复登录", "校园网已重新连上 ✓", "info"))
    return events


# ============================================================
# Win32 托盘（ctypes 直调 Shell_NotifyIcon；只用标准库）
# ============================================================
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING = 0x1, 0x2
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
IDI_APPLICATION = 32512
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_NONOTIFY = 0x100, 0x2, 0x80
MF_STRING, MF_SEPARATOR = 0x0, 0x800
ID_OPEN, ID_LOGIN, ID_LOGS, ID_QUIT = 1001, 1002, 1003, 1099
MUTEX_NAME = "Local\\DrcomAutoLoginTray"      # Local\ = 每个登录会话一个托盘
# —— v2.0.7.0（B3）：事件驱动重连用到的消息 / 定时器 id ——
WM_POWERBROADCAST = 0x0218
WM_NETCHANGE = WM_APP + 2                     # 自己 PostMessage 的：网络地址变了
TIMER_POLL, TIMER_WAKE = 1, 2                 # 周期性轮询 / 唤醒后延后触发的一次性重连
INFINITE = 0xFFFFFFFF
DETACHED_PROCESS = 0x00000008                 # v2.0.7.1：重启自己时不要继承控制台


class OVERLAPPED(ctypes.Structure):
    """``OVERLAPPED`` —— ctypes.wintypes 里**没有**这个结构（v2.0.7.0 实跑才发现的坑）。

    只用 ``hEvent``：``NotifyAddrChange`` 是异步 API，靠这个事件告诉我们"地址变了"。
    Internal / InternalHigh 按指针宽度放（64 位下 int 会被截断）。
    """
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    """NOTIFYICONDATAW（V3 布局；uTimeout/uVersion 是同一个 union，这里都按 4 字节放）。"""
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


_WNDPROC_T = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM)


def _libs():
    return (ctypes.windll.kernel32, ctypes.windll.user32, ctypes.windll.shell32,
            ctypes.windll.iphlpapi)          # v2.0.7.0：网络地址变化通知（NotifyAddrChange）


class TrayApp(object):
    """一个隐藏窗口 + 一个通知区图标 + 一个轮询定时器。"""

    def __init__(self):
        self.hwnd = None
        self.hicon = None
        self.nid = None
        self.kind = None            # 上一次的 kind（None = 还没轮询过）
        self.down_streak = 0
        self.missing_streak = 0     # 连续几次找不到自己的脚本（用来识别"已卸载"）
        self.last_status = {}
        self._proc = None           # WNDPROC 必须留引用，否则被 GC 掉会崩
        self.last_reconnect_at = None    # v2.0.7.0：上次因事件触发重连的时刻（去抖用）
        self.code_sig = None             # v2.0.7.1：自己脚本的指纹，变了就换新代码重启
        self.mutex_handle = None         # v2.0.7.1：单实例互斥体句柄（重启自己前必须放手）
        self.kernel32, self.user32, self.shell32, self.iphlpapi = _libs()
        self._declare_win32()

    def _declare_win32(self):
        """把用到的 Win32 调用签名声明清楚。

        64 位下**必须**这么做：句柄（HWND/HICON/HMENU/HMODULE）都是 64 位指针，
        没声明 argtypes/restype 时 ctypes 会按 C int（32 位）来回传 → 要么 OverflowError、
        要么句柄被截断后失效（托盘"静默消失"的经典原因）。`--self-test` 第一次跑就撞上了。
        """
        k32, u32 = self.kernel32, self.user32
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        u32.CreateWindowExW.restype = wintypes.HWND
        u32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        u32.DefWindowProcW.restype = ctypes.c_ssize_t
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.LoadImageW.restype = wintypes.HICON
        u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u32.LoadIconW.restype = wintypes.HICON
        u32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        u32.CreatePopupMenu.restype = wintypes.HMENU
        u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u32.TrackPopupMenu.restype = ctypes.c_int           # TPM_RETURNCMD 返回菜单 id
        u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        u32.DestroyMenu.argtypes = [wintypes.HMENU]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        u32.DestroyWindow.argtypes = [wintypes.HWND]
        u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.PostQuitMessage.argtypes = [ctypes.c_int]
        u32.SetTimer.restype = ctypes.c_size_t
        u32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        u32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        u32.GetCursorPos.argtypes = [ctypes.c_void_p]
        u32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                     wintypes.UINT]
        u32.TranslateMessage.argtypes = [ctypes.c_void_p]
        u32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        # v2.0.7.0：NotifyAddrChange 是**异步** API —— 拿到事件句柄后 WaitForSingleObject
        # 等它。句柄（HANDLE）不声明 argtypes/restype，64 位下会被 ctypes 按 int 截断。
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                     wintypes.LPCWSTR]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.iphlpapi.NotifyAddrChange.restype = wintypes.DWORD
        self.iphlpapi.NotifyAddrChange.argtypes = [ctypes.POINTER(wintypes.HANDLE),
                                                   ctypes.POINTER(OVERLAPPED)]
        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    # —— 图标 ——
    def _load_icon(self):
        ico = os.path.join(APP_DIR, "branding", "app.ico")
        if os.path.isfile(ico):
            h = self.user32.LoadImageW(None, ico, IMAGE_ICON, 0, 0,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                return h
        # IDI_APPLICATION 是资源 id（整数），必须按指针传：直接传 int 会被当成字符串地址
        return self.user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _add_icon(self):
        """把图标加进通知区。返回 True/False（False 时调用方应记日志退出）。"""
        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAY
        self.nid.hIcon = self._load_icon()
        self.nid.szTip = "DrcomAutoLogin · 启动中…"
        return bool(self.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid)))

    def _set_tip(self, text):
        if not self.nid:
            return
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.szTip = text[:127]
        self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def balloon(self, title, text, level="info"):
        """弹气泡。失败只记日志（气泡不是关键路径）。"""
        if not self.nid:
            return
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
        self.nid.szInfoTitle = title[:63]
        self.nid.szInfo = text[:255]
        self.nid.dwInfoFlags = NIIF_WARNING if level == "warn" else NIIF_INFO
        ok = self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))
        if not ok:
            _log("气泡发送失败：%s / %s" % (title, text))
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    # —— 窗口 ——
    def _create_window(self):
        hmod = self.kernel32.GetModuleHandleW(None)
        cls = "DrcomAutoLoginTrayWnd"
        self._proc = _WNDPROC_T(self._wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = hmod
        wc.lpszClassName = cls
        if not self.user32.RegisterClassExW(ctypes.byref(wc)):
            err = self.kernel32.GetLastError()
            if err != 1410:            # 1410 = 类已注册（同一会话里重启会撞上，不算错）
                _log("RegisterClassExW 失败：err=%s" % err)
                return False
        # 隐藏窗口（不 WS_VISIBLE）：只用来收通知区消息与定时器，不占屏幕
        self.hwnd = self.user32.CreateWindowExW(0, cls, cls, 0, 0, 0, 0, 0,
                                                None, None, hmod, None)
        if not self.hwnd:
            _log("CreateWindowExW 失败：err=%s" % self.kernel32.GetLastError())
            return False
        return True

    def _open(self, path):
        try:
            os.startfile(path)
        except OSError as exc:
            _log("打开 %s 失败：%r" % (path, exc))

    # —— 菜单 ——
    def _show_menu(self):
        menu = self.user32.CreatePopupMenu()
        self.user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "打开配置页")
        self.user32.AppendMenuW(menu, MF_STRING, ID_LOGIN, "立即登录")
        self.user32.AppendMenuW(menu, MF_STRING, ID_LOGS, "打开日志目录")
        self.user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        self.user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出托盘")
        pos = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(pos))
        # 先 SetForegroundWindow：否则菜单点空白处不消失（Win32 老毛病）
        self.user32.SetForegroundWindow(self.hwnd)
        cmd = self.user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                                         pos.x, pos.y, 0, self.hwnd, None)
        self.user32.PostMessageW(self.hwnd, 0x0000, 0, 0)
        self.user32.DestroyMenu(menu)
        self._on_command(cmd)

    def _on_command(self, cmd):
        if cmd == ID_OPEN:
            self._open(api_base())
        elif cmd == ID_LOGIN:
            ok, msg = trigger_login()
            _log("菜单：立即登录 → %s（%s）" % (msg, ok))
            self.balloon("立即登录", msg, "info" if ok else "warn")
        elif cmd == ID_LOGS:
            logs = os.path.join(APP_DIR, "logs")
            self._open(logs if os.path.isdir(logs) else LOG_DIR)
        elif cmd == ID_QUIT:
            _log("用户从菜单退出托盘")
            self.user32.DestroyWindow(self.hwnd)

    # —— v2.0.7.1：升级后换上新代码（不然托盘一直跑着内存里的旧版本）——
    def _code_changed(self):
        """安装目录里的 tray.py 被换过了吗？第一次调用只记指纹。"""
        sig = code_signature(os.path.join(APP_DIR, "tray.py"))
        if sig is None:
            return False                    # 读不到 → 交给上面「已卸载」那套逻辑
        if self.code_sig is None:
            self.code_sig = sig
            return False
        return sig != self.code_sig

    def _restart_self(self):
        """换新代码：先放手互斥体 → 起一个新实例 → 自己退出。

        为什么必须**先放手**：新实例起来会抢 `Local\\DrcomAutoLoginTray`，
        我们不放手它就立刻自己退出（这正是升级后托盘一直是旧代码的原因 ✗）。
        先记下新指纹再动手：万一这次没起来，也只重试一次，不会每 10 秒刷一遍日志。
        """
        self.code_sig = code_signature(os.path.join(APP_DIR, "tray.py"))
        _log("检测到 tray.py 已被升级 → 换上新代码重启托盘")
        if self.mutex_handle:
            self.kernel32.CloseHandle(self.mutex_handle)
            self.mutex_handle = None
        try:
            subprocess.Popen([sys.executable, os.path.join(APP_DIR, "tray.py")],
                             cwd=APP_DIR, creationflags=DETACHED_PROCESS,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as exc:
            _log("重启失败：%r（本次继续用旧代码跑，下次升级再试）" % (exc,))
            self.mutex_handle = _single_instance()[0]        # 把互斥体拿回来
            return False
        self.user32.DestroyWindow(self.hwnd)                 # 撤图标 + PostQuitMessage
        return True

    # —— v2.0.7.0（B3）：事件驱动的立即重连 ——
    def _reconnect(self, reason):
        """打一下服务端的 ``/api/login``（服务侧自己会判断「该不该登、能不能登」）。

        只在**事件**到了才走这里，而且带 5 秒去抖：唤醒时「电源广播 + 无线重连 +
        DHCP 续租」常常连着来，不去抖会连打三四个登录请求。
        """
        now = time.time()
        if not should_reconnect(self.last_reconnect_at, now):
            _log("事件：%s → 距上次触发不足 %ss，跳过（去抖）" % (reason, RECONNECT_MIN_GAP_SEC))
            return
        self.last_reconnect_at = now
        ok, msg = trigger_login()
        _log("事件：%s → 立即重连：%s" % (reason, msg))
        if ok:
            self._poll()        # 顺手立刻刷新一次状态，不等下一个 10 秒

    def _addr_change_loop(self):
        """后台线程：等一次网络地址变化 → PostMessage 叫醒主循环 → 再等下一次。

        用 ``NotifyAddrChange``（iphlpapi，XP 起就有）：插网线 / 换 Wi-Fi / DHCP 换地址 /
        唤醒后网卡重新拿到地址都会触发。**不轮询** —— 事件到了才醒，平时零开销。

        任何异常都只记一行日志并结束线程：托盘主体（10 秒轮询 + 通知）不受影响，
        最坏情况就是退回 v2.0.6.x 的行为。
        """
        ip, k32, u32 = self.iphlpapi, self.kernel32, self.user32
        while True:
            try:
                h_event = k32.CreateEventW(None, False, False, None)
                if not h_event:
                    _log("网络变化监听：CreateEventW 失败，退回纯轮询（%ss）" % POLL_SEC)
                    return
                h_async = wintypes.HANDLE()
                ov = OVERLAPPED()               # 必须一直活到 WaitForSingleObject 返回
                ov.hEvent = h_event
                rc = ip.NotifyAddrChange(ctypes.byref(h_async), ctypes.byref(ov))
                try:
                    if rc not in (0, 997):        # 0=ERROR_SUCCESS / 997=ERROR_IO_PENDING
                        _log("网络变化监听：NotifyAddrChange 返回 %s，退回纯轮询" % rc)
                        return
                    k32.WaitForSingleObject(h_event, INFINITE)   # 等到真的变了才继续
                finally:
                    k32.CloseHandle(h_event)
                    if h_async:
                        k32.CloseHandle(h_async)
                if self.hwnd:
                    u32.PostMessageW(self.hwnd, WM_NETCHANGE, 0, 0)
            except Exception as exc:            # noqa: BLE001 —— 监听线程绝不拖垮托盘
                _log("网络变化监听异常：%r（退回纯轮询）" % (exc,))
                return

    # —— 轮询 ——
    def _poll(self):
        # 卸载后自己退出：安装目录里的 tray.py 没了（连续两次轮询都找不到）→ 撤图标退出，
        # 免得卸载后留下一个永远"服务未响应"的孤儿图标。**用两次而不是一次**是有意的：
        # 升级时安装器会替换这个文件，撞上被删的那一瞬间就退出反而糟糕。
        if not os.path.isfile(os.path.join(APP_DIR, "tray.py")):
            self.missing_streak += 1
            if self.missing_streak >= 2:
                _log("安装目录里已经没有 tray.py（多半是已卸载），托盘退出")
                self.user32.DestroyWindow(self.hwnd)
                return
        else:
            self.missing_streak = 0
        if self._code_changed():
            self._restart_self()
            return
        status = poll_status()
        kind = classify(status)
        self.down_streak = self.down_streak + 1 if kind == "down" else 0
        for title, textv, level in decide_events(self.kind, kind, self.down_streak):
            _log("通知：%s —— %s" % (title, textv))
            self.balloon(title, textv, level)
        if kind != self.kind:
            _log("状态：%s → %s（%s）" % (self.kind, kind, status_text(status)))
        self.kind = kind
        self.last_status = status
        self._set_tip("DrcomAutoLogin · " + status_text(status))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam == WM_LBUTTONDBLCLK:
                    self._open(api_base())
                elif lparam == WM_RBUTTONUP:
                    self._show_menu()
                return 0
            if msg == WM_POWERBROADCAST:
                kind = power_event_kind(wparam)
                if kind:
                    _log("事件：%s → %s 秒后触发重连" % (kind, WAKE_SETTLE_SEC))
                    # 不能在这里直接发请求：WndProc 卡住会把整个托盘冻住。
                    # 改用一次性定时器延后 —— 刚醒时网卡 / 无线还没连上。
                    self.user32.SetTimer(self.hwnd, TIMER_WAKE, WAKE_SETTLE_SEC * 1000, None)
                return 1        # TRUE：电源广播要求「已处理」才回 1
            if msg == WM_NETCHANGE:
                self._reconnect("网络地址变化")
                return 0
            if msg == WM_TIMER:
                if wparam == TIMER_WAKE:
                    self.user32.KillTimer(self.hwnd, TIMER_WAKE)
                    self._reconnect("从睡眠唤醒")
                else:
                    self._poll()
                return 0
            if msg in (WM_CLOSE, WM_DESTROY):
                if self.nid:
                    self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
                    self.nid = None
                self.user32.PostQuitMessage(0)
                return 0
        except Exception as exc:       # 托盘绝不能因为一次异常整体挂掉
            _log("WndProc 异常：%r" % (exc,))
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # —— 主循环 ——
    def run(self):
        if not self._create_window():
            return 1
        if not self._add_icon():
            _log("Shell_NotifyIcon(NIM_ADD) 失败：托盘无法显示（没有桌面会话？）")
            return 2
        _log("托盘已启动（每 %ss 轮询 %s）" % (POLL_SEC, api_base()))
        self._poll()                       # 起来就先来一次，别等一个轮询周期
        self.user32.SetTimer(self.hwnd, TIMER_POLL, POLL_SEC * 1000, None)
        # v2.0.7.0（B3）：另起一个线程等「网络地址变化」（插网线 / 换 Wi-Fi / DHCP 换地址），
        # 到了就 PostMessage 叫醒上面的消息循环 → 立刻重连，不用等下一个轮询周期。
        threading.Thread(target=self._addr_change_loop, daemon=True).start()
        _log("事件监听已就绪：网络地址变化 + 系统唤醒（唤醒后 %s 秒触发重连）" % WAKE_SETTLE_SEC)
        msg = wintypes.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))
        self.user32.KillTimer(self.hwnd, TIMER_POLL)
        if self.nid:
            self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            self.nid = None
        _log("托盘已退出")
        return 0

    def self_test(self, wait_sec=4):
        """真加一次图标 + 弹一次气泡，等几秒再撤掉。返回 0 = 通过。

        这是唯一能自动验证「Win32 那半边」的办法：NIM_ADD 返回 TRUE 就说明通知区
        真的接受了这个图标（不只是 ctypes 没报错）。
        """
        if not self._create_window():
            return 1
        if not self._add_icon():
            _log("自检失败：NIM_ADD 返回 FALSE")
            return 2
        self._set_tip("DrcomAutoLogin · 托盘自检")
        self.balloon("托盘自检", "看到这个气泡就说明托盘环境正常（写入 %s）。" % LOG_FILE)
        deadline = time.time() + wait_sec
        msg = wintypes.MSG()
        while time.time() < deadline:
            while self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.05)
        ok = bool(self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid)))
        self.nid = None
        self.user32.DestroyWindow(self.hwnd)
        if ok:
            _log("自检通过：图标已加入通知区并已撤回，气泡已发送")
            return 0
        _log("自检失败：NIM_DELETE 返回 FALSE")
        return 3

    def test_event(self, kind, wait_sec=15):
        """模拟一次事件（``--test-event wake|net``）：**真投递那条消息**，走真实处理路径。

        故意**不建图标**：消息循环不需要它，免得测试时通知区闪一下
        （``_set_tip`` / ``balloon`` 在没有图标时本来就会安静跳过）。

        返回 0 = 消息已投递并跑完消息循环（日志里能看到「事件：… → 立即重连：…」）。
        """
        if kind not in ("wake", "net"):
            _out("--test-event 只认 wake / net（收到 %r）" % kind)
            return 2
        if not self._create_window():
            return 1
        if kind == "wake":
            self.user32.PostMessageW(self.hwnd, WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC, 0)
            note = "已投递 WM_POWERBROADCAST(PBT_APMRESUMEAUTOMATIC)：%s 秒后应触发重连" % \
                   WAKE_SETTLE_SEC
        else:
            self.user32.PostMessageW(self.hwnd, WM_NETCHANGE, 0, 0)
            note = "已投递 WM_NETCHANGE：应当立刻触发重连"
        _log("事件模拟（%s）：%s" % (kind, note))
        _out(note)
        deadline = time.time() + wait_sec
        msg = wintypes.MSG()
        while time.time() < deadline:          # 真消息循环：事件与定时器都走真代码
            while self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.05)
        self.user32.DestroyWindow(self.hwnd)
        _out("上面出现「事件：… → 立即重连：…」就说明链路通了；完整日志：%s" % LOG_FILE)
        return 0


# ============================================================
# 入口
# ============================================================
def _out(text):
    """有控制台才打印（pythonw.exe 下 sys.stdout 是 None，print 会抛）。"""
    try:
        if sys.stdout is not None:
            sys.stdout.write(text.rstrip() + "\n")
            sys.stdout.flush()
    except (OSError, ValueError):
        pass


def _single_instance():
    """抢单实例互斥体。返回 (handle, already_running)。

    用 CreateMutexW 而不是锁文件：句柄随进程退出由内核释放，进程被强杀也不会留残留锁
    （这正是托盘这种"随登录起、可能被任务管理器杀掉"的进程需要的）。
    """
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.SetLastError(0)
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    return handle, k32.GetLastError() == 183      # 183 = ERROR_ALREADY_EXISTS


def main(argv):
    if "--help" in argv or "-h" in argv:
        _out(__doc__ or "")
        _out("用法：tray.py [--check | --self-test | --test-event wake|net]")
        return 0
    if not IS_WIN:
        _out("tray.py 只在 Windows 上有意义（当前平台：%s）" % os.name)
        return 0
    if "--check" in argv:
        st = poll_status()
        kind = classify(st)
        _out("服务地址：%s" % api_base())
        _out("本次判定：%s（%s）" % (kind, status_text(st)))
        for title, textv, level in decide_events("ok", kind, 1):
            _out("照这个走向会弹：%s —— %s（%s）" % (title, textv, level))
        return 0
    if "--test-event" in argv:
        # 本机实测用：**故意**绕开单实例互斥体 —— 真实托盘通常正在跑，
        # 而模拟事件不需要抢它的窗口（也不建图标，免得通知区闪一下）。
        _idx = argv.index("--test-event")
        _kind = argv[_idx + 1] if len(argv) > _idx + 1 else "wake"
        return TrayApp().test_event(_kind)
    handle, already = _single_instance()
    if already:
        _out("托盘已经在运行了，本次退出。")
        return 0
    app = TrayApp()
    app.mutex_handle = handle        # v2.0.7.1：换新代码重启自己前要先放手这个互斥体
    if "--self-test" in argv:
        code = app.self_test()
        _out("托盘自检%s" % ("通过 ✓" if code == 0 else "失败 ✗（code=%d）" % code))
        _out("日志：%s" % LOG_FILE)
        return code
    return app.run()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:                              # 崩了也要在日志里留下traceback，别静默消失
        import traceback
        _log("托盘异常退出：\n%s" % traceback.format_exc())
        sys.exit(1)
