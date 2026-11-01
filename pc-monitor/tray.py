# -*- coding: utf-8 -*-
"""
Windows 系统托盘（纯 ctypes 调 Win32，不依赖第三方库）

启动后常驻右下角通知区域，鼠标悬停显示 CPU / GPU / 内存，
右键菜单可以打开页面、复制地址、设开机自启、启用温度传感器、退出。
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes as w

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000

WM_TRAY = 0x0400 + 1              # WM_APP + 1
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_CONTEXTMENU = 0x007B           # 部分系统版本右键走这个

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
MF_GRAYED = 0x0001
MF_DEFAULT = 0x1000

TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
SM_CXSMICON = 49

IDI_APPLICATION = 32512


class GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD),
                ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("guidItem", GUID)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", w.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.UINT),
        ("style", w.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
        ("hIconSm", w.HICON),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hWnd", w.HWND), ("message", w.UINT), ("wParam", w.WPARAM),
                ("lParam", w.LPARAM), ("time", w.DWORD), ("pt", w.POINT)]


WNDPROC = ctypes.CFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = w.WORD
user32.CreateWindowExW.argtypes = [
    w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = w.HWND
user32.DestroyWindow.argtypes = [w.HWND]
user32.DestroyWindow.restype = w.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), w.HWND, w.UINT, w.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
user32.RegisterWindowMessageW.restype = w.UINT
user32.CreatePopupMenu.restype = w.HMENU
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.AppendMenuW.restype = w.BOOL
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, w.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [w.HMENU]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int,
                              ctypes.c_int, w.UINT]
user32.LoadImageW.restype = w.HANDLE
user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPCWSTR]
user32.LoadIconW.restype = w.HICON
user32.DestroyIcon.argtypes = [w.HICON]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [w.HWND]
user32.BringWindowToTop.restype = w.BOOL
user32.GetForegroundWindow.restype = w.HWND
user32.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, w.BOOL]
user32.AttachThreadInput.restype = w.BOOL
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.c_void_p]
user32.GetWindowThreadProcessId.restype = w.DWORD
user32.SetMenuDefaultItem.argtypes = [w.HMENU, w.UINT, w.UINT]
user32.EndMenu.argtypes = [w.HMENU]
kernel32.GetCurrentThreadId.restype = w.DWORD
kernel32.GetConsoleWindow.restype = w.HWND

kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
kernel32.GetModuleHandleW.restype = w.HINSTANCE

shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = w.BOOL
shell32.ExtractIconExW.argtypes = [w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.HICON),
                                   ctypes.POINTER(w.HICON), w.UINT]
shell32.ExtractIconExW.restype = w.UINT
try:
    shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                                ctypes.POINTER(w.RECT)]
    shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
except AttributeError:      # Win7 没有这个接口
    pass


class Tray(object):
    """托盘图标。

    回调 dict：
        on_open()            打开监控页面
        on_quit()            退出程序
        on_toggle_startup()  切换开机自启，返回 bool（当前是否已开启）
        on_enable_temp()     启用 CPU 温度传感器
        on_open_dir()        打开程序目录
        get_tip()            返回悬浮提示文字
        get_ips()            返回 [(显示文字, 地址)] 用于复制
        startup_checked()    开机自启当前状态
    """

    CLASS_NAME = "PCMonitorTrayWnd"

    def __init__(self, callbacks, icon_path=None, title="电脑资源监控"):
        self.cb = callbacks
        self.icon_path = icon_path
        self.title = title
        self.hwnd = None
        self.hicon = None
        self._thread = None
        self._ready = threading.Event()
        self._last_tip = None
        self._last_tip_at = 0.0
        self._wndproc_ref = None
        self._exit = False
        self._wm_taskbar = 0
        self.icon_added = False
        self._menu_open = False
        self._menu_at = 0.0

    # ------------------------------------------------------------ 工具
    def _log(self, text):
        """把托盘内部异常写进主日志，避免出错后完全没痕迹"""
        fn = self.cb.get("log")
        if fn:
            try:
                fn(text)
                return
            except Exception:
                pass
        try:
            sys.stderr.write(text + "\n")
        except Exception:
            pass

    def _cb(self, name, *args):
        """统一从回调字典里取函数（以前混用 object.get_ips 会直接抛异常）"""
        fn = self.cb.get(name)
        if not fn:
            return None
        try:
            if args:
                return fn(*args)
            return fn()
        except Exception as e:
            self._log("[托盘] 回调 %s 出错: %r" % (name, e))
            return None

    # ------------------------------------------------------------ 图标
    def _load_icon(self):
        size = user32.GetSystemMetrics(SM_CXSMICON) or 16
        # 1) 同目录 static/icon.ico
        for p in [self.icon_path] if self.icon_path else []:
            if p and os.path.exists(p):
                h = user32.LoadImageW(None, p, IMAGE_ICON, size, size, LR_LOADFROMFILE)
                if h:
                    return h
        # 2) 从 exe 自身资源里取（打包后 --icon 写入的那个）
        if getattr(sys, "frozen", False):
            lg, sm = w.HICON(), w.HICON()
            n = shell32.ExtractIconExW(sys.executable, 0, ctypes.byref(lg),
                                       ctypes.byref(sm), 1)
            if n >= 1:
                return sm if sm else lg
        # 3) 系统默认图标
        return user32.LoadIconW(None, w.LPCWSTR(IDI_APPLICATION))

    # ------------------------------------------------------------ 菜单
    def _menu(self):
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return None
        add = lambda fid, text, flag=MF_STRING: user32.AppendMenuW(
            hmenu, flag, fid, text)
        add(1, "打开监控页面", MF_STRING | MF_DEFAULT)
        ips = self._cb("get_ips") or []
        for i, (text, url) in enumerate(ips[:3]):
            add(20 + i, "复制地址  " + str(url))
        add(0, None, MF_SEPARATOR)
        checked = MF_CHECKED if self._cb("startup_checked") else MF_STRING
        add(10, "开机自动启动", checked)
        add(11, "启用 CPU 温度传感器")
        add(0, None, MF_SEPARATOR)
        add(12, "打开程序目录")
        add(13, "退出（停止监控）")
        return hmenu

    def _foreground(self):
        """把隐藏的托盘窗口顶到前台。

        SetForegroundWindow 对后台进程常常被系统拒绝，隐藏窗口更容易失败；
        直接失败会让弹出菜单立刻因失焦而消失，看起来就是「右键没反应」。
        这里再挂到当前前台线程上重试一次（经典做法）。
        """
        try:
            user32.SetForegroundWindow(self.hwnd)
            if user32.GetForegroundWindow() == self.hwnd:
                return True
            fg = user32.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            tid_me = kernel32.GetCurrentThreadId()
            if tid_fg and tid_fg != tid_me:
                user32.AttachThreadInput(tid_fg, tid_me, True)
                user32.SetForegroundWindow(self.hwnd)
                user32.BringWindowToTop(self.hwnd)
                user32.AttachThreadInput(tid_fg, tid_me, False)
            return user32.GetForegroundWindow() == self.hwnd
        except Exception as e:
            self._log("[托盘] 置前台失败: %r" % e)
            return False

    def _popup(self):
        if self._menu_open:                 # 防止 RBUTTONUP + CONTEXTMENU 弹两次
            return
        if time.time() - self._menu_at < 0.25:
            return
        self._menu_at = time.time()
        self._menu_open = True
        hmenu = None
        try:
            self._foreground()
            pt = w.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            hmenu = self._menu()
            if not hmenu:
                self._log("[托盘] 创建菜单失败")
                return
            self._log("[托盘] 右键菜单已弹出 @ %d,%d" % (pt.x, pt.y))
            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x, pt.y, 0, self.hwnd, None)
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if cmd:
                self._log("[托盘] 菜单选择: %d" % cmd)
            if cmd == 1:
                self._cb("on_open")
            elif 20 <= cmd < 23:
                ips = self._cb("get_ips") or []
                idx = cmd - 20
                if idx < len(ips):
                    self._cb("on_copy", ips[idx][1])
            elif cmd == 10:
                self._cb("on_toggle_startup")
            elif cmd == 11:
                self._cb("on_enable_temp")
            elif cmd == 12:
                self._cb("on_open_dir")
            elif cmd == 13:
                self._cb("on_quit")
        except Exception as e:
            self._log("[托盘] 弹出菜单出错: %r" % e)
        finally:
            if hmenu:
                try:
                    user32.EndMenu(hmenu)
                    user32.DestroyMenu(hmenu)
                except Exception:
                    pass
            self._menu_open = False
            self._menu_at = time.time()      # 关掉菜单后短暂冷却，挡住后到的 CONTEXTMENU

    # ------------------------------------------------------------ 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                low = lparam & 0xFFFF
                if low == WM_LBUTTONUP or low == WM_LBUTTONDBLCLK:
                    self._cb("on_open")
                    return 0
                if low == WM_RBUTTONUP:
                    self._popup()
                    return 0
            elif msg == WM_CONTEXTMENU:
                # 有些系统版本右键只发这个
                self._popup()
                return 0
            elif msg == WM_COMMAND:
                return 0
            elif msg == WM_CLOSE:
                return 0
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            elif self._wm_taskbar and msg == self._wm_taskbar:
                # 资源管理器重启了，重新添加图标
                self._add_icon()
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------ 图标操作
    def _nid(self, flags):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        tip = (self.cb.get("get_tip") or (lambda: self.title))()
        nid.szTip = (tip or self.title)[:127]
        return nid

    def _add_icon(self):
        nid = self._nid(NIF_ICON | NIF_MESSAGE | NIF_TIP)
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._last_tip = nid.szTip
        self.icon_added = bool(ok)
        return self.icon_added

    def icon_rect(self):
        """查询图标在屏幕上的位置，能查到说明确实进了通知区域"""
        if not self.hwnd:
            return None
        try:
            nid = NOTIFYICONIDENTIFIER()
            nid.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
            nid.hWnd = self.hwnd
            nid.uID = 1
            rc = w.RECT()
            if shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(rc)) == 0:
                return (rc.left, rc.top, rc.right, rc.bottom)
        except Exception:
            pass
        return None

    def _del_icon(self):
        if self.hwnd:
            nid = self._nid(0)     # 删除时不需要 flag
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    # ------------------------------------------------------------ 线程
    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._wm_taskbar = user32.RegisterWindowMessageW("TaskbarCreated")

            self._wndproc_ref = WNDPROC(self._wndproc)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            wc.hInstance = hinst
            wc.lpszClassName = self.CLASS_NAME
            user32.RegisterClassExW(ctypes.byref(wc))

            self.hicon = self._load_icon()
            self.hwnd = user32.CreateWindowExW(
                0, self.CLASS_NAME, self.title, 0, 0, 0, 0, 0,
                None, None, hinst, None)
            if not self.hwnd:
                self._ready.set()
                return

            self._add_icon()
            self._ready.set()

            msg = MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self._ready.set()
        finally:
            try:
                self._del_icon()
            except Exception:
                pass

    def start(self, wait=2.0):
        if os.name != "nt":
            return False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="pcm-tray")
        self._thread.start()
        self._ready.wait(wait)
        return bool(self.hwnd)

    def update_tip(self, text=None):
        """刷新悬浮提示（内容没变就不发，避免 tooltip 反复闪）"""
        if not self.hwnd or self._menu_open:
            return
        tip = text if text is not None else (self.cb.get("get_tip") or (lambda: self.title))()
        tip = (tip or self.title)[:127]
        if tip == self._last_tip:
            return
        self._last_tip = tip
        try:
            nid = self._nid(NIF_TIP | NIF_ICON)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    def notify(self, title, text):
        """弹一个气泡通知"""
        if not self.hwnd:
            return
        try:
            nid = self._nid(NIF_INFO)
            nid.szInfoTitle = (title or "")[:63]
            nid.szInfo = (text or "")[:255]
            nid.dwInfoFlags = 1          # NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    def stop(self):
        if self.hwnd:
            try:
                self._del_icon()
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
                user32.PostMessageW(self.hwnd, 0x0012, 0, 0)   # WM_QUIT
            except Exception:
                pass


def hide_console():
    """隐藏控制台窗口（开机自启时用，避免弹黑框）"""
    if os.name != "nt":
        return
    try:
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)      # SW_HIDE
    except Exception:
        pass
