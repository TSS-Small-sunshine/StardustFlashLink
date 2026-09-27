# -*- coding: utf-8 -*-
"""tray.py — 登录会话里的托盘小程序（B2：断线通知 + 托盘图标）。

为什么需要它：服务跑在 LocalSystem / **session 0**，跟用户桌面会话隔离，弹不出任何通知
（`Shell_NotifyIcon` / 气泡在服务里调等于扔进黑洞）。所以「断线通知 + 托盘图标」必须由
一个**随登录启动的用户进程**来做：它每 10 秒轮询服务在本机的 `/api/status`，状态迁移
时弹气泡，右键菜单提供「打开配置页 / 立即登录 / 打开日志目录 / 退出」。

只依赖标准库（ctypes 直调 Win32），跟主程序一样不引入第三方依赖。

跑法（安装器写进 HKCU 的 Run 键）：
    "{app}\\python\\pythonw.exe" "{app}\\tray.py"

自检（真加一次图标 + 弹一次气泡，用来确认环境 OK；会看到气泡一闪）：
    "{app}\\python\\python.exe" "{app}\\tray.py" --self-test

单次检查（不建图标，只打印一次判定结果；CI / 排障用）：
    "{app}\\python\\python.exe" "{app}\\tray.py" --check
"""
import ctypes
import json
import os
import sys
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
    return (ctypes.windll.kernel32, ctypes.windll.user32, ctypes.windll.shell32)


class TrayApp(object):
    """一个隐藏窗口 + 一个通知区图标 + 一个轮询定时器。"""

    def __init__(self):
        self.hwnd = None
        self.hicon = None
        self.nid = None
        self.kind = None            # 上一次的 kind（None = 还没轮询过）
        self.down_streak = 0
        self.last_status = {}
        self._proc = None           # WNDPROC 必须留引用，否则被 GC 掉会崩
        self.kernel32, self.user32, self.shell32 = _libs()
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

    # —— 轮询 ——
    def _poll(self):
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
            if msg == WM_TIMER:
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
        self.user32.SetTimer(self.hwnd, 1, POLL_SEC * 1000, None)
        msg = wintypes.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))
        self.user32.KillTimer(self.hwnd, 1)
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
        _out("用法：tray.py [--check | --self-test]")
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
    handle, already = _single_instance()
    if already:
        _out("托盘已经在运行了，本次退出。")
        return 0
    app = TrayApp()
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
