# -*- coding: utf-8 -*-
"""
PC 资源监控服务
- 后台线程固定 1s 采样，HTTP 请求直接返回快照（毫秒级响应，避免手机端超时断连）
- 采集 CPU / GPU / 内存 / 显存 / 磁盘 / 网络 / 进程 / 温度
- 仅依赖 psutil
"""

import atexit
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

FROZEN = bool(getattr(sys, "frozen", False))          # 是否由 PyInstaller 打包运行
if FROZEN:
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))   # exe 所在目录（可写）
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# onefile 打包时内置资源解压到这里；未打包时就是源码目录
_BUNDLE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
# 网页资源优先用 exe 同级的（方便直接改页面），没有则用打包内置的那份
WEB_DIR = BASE_DIR if os.path.exists(os.path.join(BASE_DIR, "index.html")) else _BUNDLE_DIR

PORT = int(os.environ.get("PCM_PORT", "8000"))
INDEX_FILE = os.path.join(WEB_DIR, "index.html")
STATIC_DIR = os.path.join(WEB_DIR, "static")
PID_FILE = os.path.join(BASE_DIR, "server.pid")

# PWA 静态资源白名单: URL 路径 -> (文件名, Content-Type)
STATIC_FILES = {
    "/manifest.json": ("manifest.json", "application/manifest+json; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/favicon.ico": ("icon-192.png", "image/png"),
    "/pc-monitor.apk": ("pc-monitor.apk", "application/vnd.android.package-archive"),
}

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW

# ---------------------------------------------------------------- 日志
# exe 以「无控制台」方式打包（双击不弹黑框），此时 sys.stdout 为 None，
# print 会静默丢弃。所以统一走 _log()：能打印就打印，同时落盘一份便于排查。
LOG_FILE = os.path.join(BASE_DIR, "pcmonitor.log")
_LOG_LOCK = threading.Lock()


def _log(msg=""):
    line = str(msg)
    try:
        if sys.stdout is not None:
            print(line)
            sys.stdout.flush()
    except Exception:
        pass
    try:
        with _LOG_LOCK:
            try:
                if os.path.getsize(LOG_FILE) > 262144:      # 超过 256KB 清空重来
                    open(LOG_FILE, "w").close()
            except OSError:
                pass
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass

import gpu as gpumod          # 多厂商 GPU 采集（NVIDIA / AMD / Intel）

CPU_COUNT = psutil.cpu_count() or 1
SNAP = {}
SNAP_LOCK = threading.Lock()
# CPU 温度：只有真正读得到才给值，读不到就返回 None（不再用 ACPI 温区凑数）
TEMP = {"cpu": None, "src": None, "ts": 0.0}
TEMP_LOCK = threading.Lock()
LHM_PORT = int(os.environ.get("PCM_LHM_PORT", "8099"))
LHM_URL = "http://127.0.0.1:%d/data.json" % LHM_PORT

_SKIP_NIC = ("vethernet", "loopback", "isatap", "teredo", "bluetooth",
             "vmware", "virtualbox", "tap-", "wintun", "docker")

GPU_SAMPLER = gpumod.GpuSampler(LHM_URL, interval=2.0)


# ---------------------------------------------------------------- 基础信息
def _cpu_name():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)
        return " ".join(str(val).split())
    except Exception:
        import platform
        return platform.processor() or "CPU"


CPU_NAME = _cpu_name()


def _calc_cpu(prev_total, prev_per):
    """基于 cpu_times 差值自算（psutil.cpu_percent 在多线程下会失效）"""
    t = psutil.cpu_times()
    p = psutil.cpu_times(percpu=True)

    def ratio(cur, old):
        if old is None:
            return 0.0
        dt = sum(cur) - sum(old)
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, (1.0 - (cur.idle - old.idle) / dt) * 100.0))

    total = ratio(t, prev_total)
    cores = ([ratio(c, o) for c, o in zip(p, prev_per)]
             if prev_per and len(prev_per) == len(p) else [0.0] * len(p))
    return round(total, 1), [round(x, 1) for x in cores], t, p


def _net_totals():
    up = down = 0
    try:
        per = psutil.net_io_counters(pernic=True) or {}
    except Exception:
        per = {}
    for name, c in per.items():
        low = name.lower()
        if any(low.startswith(p) for p in _SKIP_NIC):
            continue
        up += c.bytes_sent
        down += c.bytes_recv
    return up, down


# ---------------------------------------------------------------- 温度
# 说明：Windows 没有用户态可读的 CPU 核心温度接口。
# 网上常见的 MSAcpi_ThermalZoneTemperature / ThermalZoneInformation 在本机
# 实测为“假数据”（12 线程满载 16 秒，数值纹丝不动），因此不再使用。
# 唯一可靠来源是内核级驱动（LibreHardwareMonitor），它的 Web 服务会给出
# CPU Package / Core Max 真实温度。未启用时干脆返回 None，页面显示“—”。

_CPU_TEMP_KEYS = ("cpu package", "core max", "cpu total", "core average", "cpu (tctl")

LHM_DIR = os.path.join(BASE_DIR, "sensors", "lhm")
LHM_EXE = os.path.join(LHM_DIR, "LibreHardwareMonitor.exe")
LHM_TASK = "PCMonitorTempSensor"     # 启用CPU温度.bat 注册的登录自启任务
_LHM_SPAWN = {"at": 0.0, "tries": 0}


def _ensure_lhm():
    """读不到温度时，尝试用计划任务把传感器静默拉起来。

    LibreHardwareMonitor.exe 的清单声明了 requireAdministrator，直接启动必然弹 UAC。
    所以这里只走「启用CPU温度.bat」注册的计划任务：任务存在就能无提示恢复，
    任务不存在则什么也不做（页面显示「—」并提示用户去双击那个 bat）。
    这样新电脑首次使用时不会被莫名的 UAC 弹窗打扰。
    """
    now = time.time()
    if _LHM_SPAWN["tries"] >= 4 or now - _LHM_SPAWN["at"] < 45:
        return
    if not os.path.exists(LHM_EXE):
        return
    _LHM_SPAWN["at"] = now
    _LHM_SPAWN["tries"] += 1
    try:
        subprocess.run(
            ["schtasks", "/Run", "/TN", LHM_TASK],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW, timeout=8,
        )
    except Exception:
        pass


def _walk_sensors(node, found):
    for ch in node.get("Children") or []:
        text = (ch.get("Text") or "").strip()
        value = (ch.get("Value") or "").strip()
        low = text.lower()
        if "°c" in value.lower() and any(k in low for k in _CPU_TEMP_KEYS):
            try:
                found.setdefault(low, float(value.split()[0]))
            except (ValueError, IndexError):
                pass
        if ch.get("Children"):
            _walk_sensors(ch, found)


def _lhm_cpu_temp():
    """从 LibreHardwareMonitor 的本地 Web 服务读 CPU 温度，失败返回 None"""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理
        with opener.open(LHM_URL, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    found = {}
    try:
        _walk_sensors(data, found)
    except Exception:
        return None
    if not found:
        return None
    for key in _CPU_TEMP_KEYS:          # 优先 CPU Package，其次 Core Max
        if key in found:
            v = found[key]
            return v if 0 < v < 120 else None
    v = max(found.values())
    return v if 0 < v < 120 else None


def _temp_worker():
    idle = 0
    while True:
        t = _lhm_cpu_temp()
        with TEMP_LOCK:
            if t is not None:
                TEMP["cpu"] = round(t, 1)
                TEMP["src"] = "lhm"
                TEMP["ts"] = time.time()
                idle = 0
            else:
                if time.time() - TEMP["ts"] > 15:
                    TEMP["cpu"] = None      # 读数过期（LHM 被关掉）就清空，不留旧值
                    TEMP["src"] = None
                idle += 1
                if idle in (3, 6, 10):      # 约 9s / 18s / 30s 后各尝试拉起一次传感器
                    _ensure_lhm()
        time.sleep(3)


# ---------------------------------------------------------------- 采样器
class Sampler(threading.Thread):
    def __init__(self, interval=1.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.prev = {}
        self.procs = {}
        self.tick = 0
        self.last_procs = []

    def run(self):
        # 预热差值基准
        self.prev["ct"], self.prev["pcpt"] = psutil.cpu_times(), psutil.cpu_times(percpu=True)
        self.prev["net_up"], self.prev["net_down"] = _net_totals()
        dio = psutil.disk_io_counters()
        self.prev["dread"] = dio.read_bytes if dio else 0
        self.prev["dwrite"] = dio.write_bytes if dio else 0
        self.prev["t"] = time.time()
        while True:
            t0 = time.time()
            try:
                snap = self.sample()
                with SNAP_LOCK:
                    SNAP.clear()
                    SNAP.update(snap)
            except Exception as e:
                sys_err = "%s: %s" % (type(e).__name__, e)
                with SNAP_LOCK:
                    if "error" not in SNAP:
                        SNAP["error"] = sys_err
            dt = time.time() - t0
            time.sleep(max(0.05, self.interval - dt))

    def sample(self):
        now = time.time()
        cpu_pct, cores, ct, cpt = _calc_cpu(self.prev.get("ct"), self.prev.get("pcpt"))
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        du = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")

        net_up, net_down = _net_totals()
        try:
            dio = psutil.disk_io_counters()
        except Exception:
            dio = None
        dread = dio.read_bytes if dio else 0
        dwrite = dio.write_bytes if dio else 0

        dt = max(0.0, now - self.prev.get("t", now))
        if dt <= 0 or dt > 15:
            dt = 0

        def rate(cur, prev):
            if dt <= 0 or prev is None or cur < prev:
                return 0.0
            return (cur - prev) / dt

        r_up = rate(net_up, self.prev.get("net_up"))
        r_down = rate(net_down, self.prev.get("net_down"))
        r_read = rate(dread, self.prev.get("dread"))
        r_write = rate(dwrite, self.prev.get("dwrite"))

        self.prev.update({
            "ct": ct, "pcpt": cpt, "t": now,
            "net_up": net_up, "net_down": net_down,
            "dread": dread, "dwrite": dwrite,
        })

        # 进程每 2 秒采样一次，降低开销
        self.tick += 1
        if self.tick % 2 == 1 or not self.last_procs:
            self.last_procs = self._top_procs()

        freq = None
        try:
            f = psutil.cpu_freq()
            freq = int(f.current) if f and f.current else None
        except Exception:
            pass

        battery = None
        try:
            b = psutil.sensors_battery()
            if b:
                battery = {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
        except Exception:
            pass

        gpus = GPU_SAMPLER.snapshot()
        gpu = gpus[0] if gpus else None
        with TEMP_LOCK:
            cpu_temp, temp_src = TEMP["cpu"], TEMP["src"]
        return {
            "ts": now,
            "host": socket.gethostname(),
            "uptime": int(now - psutil.boot_time()),
            "cpu": {
                "percent": cpu_pct, "cores": cores, "count": CPU_COUNT,
                "freq": freq, "name": CPU_NAME,
                "temp": cpu_temp, "temp_src": temp_src, "temp_approx": False,
            },
            "gpu": gpu,
            "gpus": gpus,
            "mem": {
                "total": mem.total, "used": mem.total - mem.available,
                "percent": round(mem.percent, 1),
                "swap_total": swap.total, "swap_used": swap.used,
            },
            "disk": {
                "total": du.total, "used": du.used, "percent": round(du.percent, 1),
                "read_bps": r_read, "write_bps": r_write,
            },
            "net": {"up_bps": r_up, "down_bps": r_down, "recv": net_down},
            "battery": battery,
            "procs": self.last_procs,
        }

    def _top_procs(self, limit=5):
        rows = []
        alive = set()
        for p in psutil.process_iter(["pid", "name"]):
            pid = p.info.get("pid")
            nm = p.info.get("name") or "?"
            if pid in (None, 0) or nm in ("System Idle Process", "Idle"):
                continue
            alive.add(pid)
            proc = self.procs.get(pid)
            if proc is None:
                try:
                    proc = psutil.Process(pid)
                    proc.cpu_percent(None)
                    self.procs[pid] = proc
                except Exception:
                    continue
            try:
                cpu = proc.cpu_percent(None) / CPU_COUNT
                mem = proc.memory_info().rss
            except Exception:
                continue
            rows.append({"pid": pid, "name": nm, "cpu": round(cpu, 1), "mem": mem})
        for pid in list(self.procs):
            if pid not in alive:
                self.procs.pop(pid, None)
        rows.sort(key=lambda r: (r["cpu"], r["mem"]), reverse=True)
        return rows[:limit]


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "PCMonitor/2.0"
    protocol_version = "HTTP/1.1"   # 长连接，避免手机每秒新建 TCP 连接
    timeout = 30

    def log_message(self, fmt, *args):
        pass

    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_FILE, "rb") as f:
                    self._send(f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(b"index.html missing", "text/plain; charset=utf-8", 404)
        elif path in STATIC_FILES:
            fname, ctype = STATIC_FILES[path]
            try:
                with open(os.path.join(STATIC_DIR, fname), "rb") as f:
                    self._send(f.read(), ctype)
            except FileNotFoundError:
                self._send(b"not found", "text/plain; charset=utf-8", 404)
        elif path == "/api/stats":
            with SNAP_LOCK:
                data = dict(SNAP)
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
        else:
            self._send(b"not found", "text/plain; charset=utf-8", 404)


def port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def write_pid():
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def remove_pid():
    """只删除属于本进程的 pid 文件"""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r", encoding="utf-8") as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(PID_FILE)
    except Exception:
        pass


def lan_ips():
    ips = set()
    try:
        for name, addrs in psutil.net_if_addrs().items():
            low = name.lower()
            if any(low.startswith(p) for p in _SKIP_NIC):
                continue
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    ips.add(a.address)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def _pause_before_exit():
    """命令行里出错时停住，避免一闪而过看不到原因。

    无控制台的 exe（双击运行）没有窗口可停，sys.stdout 为 None，直接跳过。
    """
    if sys.stdout is None:
        return
    try:
        input("\n按回车键关闭窗口 ...")
    except Exception:
        pass


# ---------------------------------------------------------------- 托盘 / 小工具
_SRV = None
_TRAY = None
_QUITTING = {"v": False}


def _fmt_cap(n):
    """容量紧凑格式：16.4G / 593M"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "0"
    for u in ("B", "K", "M", "G", "T"):
        if n < 1024 or u == "T":
            v = ("%.1f" % n).rstrip("0").rstrip(".")
            return v + u
        n /= 1024.0


def _local_urls():
    ips = lan_ips()
    if not ips:
        return ["http://127.0.0.1:%d" % PORT]
    return ["http://%s:%d" % (ip, PORT) for ip in ips]


def _open_url(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        try:
            os.startfile(url)
        except Exception:
            pass


def _copy_to_clipboard(text):
    if not IS_WIN:
        return False
    import ctypes
    from ctypes import wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.OpenClipboard.argtypes = [wt.HWND]
    u.OpenClipboard.restype = wt.BOOL
    u.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    u.SetClipboardData.restype = wt.HANDLE
    k.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    k.GlobalAlloc.restype = wt.HGLOBAL
    k.GlobalLock.argtypes = [wt.HGLOBAL]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [wt.HGLOBAL]
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u.OpenClipboard(None):
        return False
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(0x0002, len(data))      # GMEM_MOVEABLE
        if not h:
            return False
        p = k.GlobalLock(h)
        if not p:
            return False
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(13, h)                 # CF_UNICODETEXT
        return True
    except Exception:
        return False
    finally:
        try:
            u.CloseClipboard()
        except Exception:
            pass


def _run_elevated(extra):
    """以管理员身份重新运行本程序（会弹 UAC）"""
    if not IS_WIN:
        return False
    import ctypes
    from ctypes import wintypes as wt
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
                                      wt.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    if FROZEN:
        exe, params = sys.executable, extra
    else:
        exe = sys.executable
        params = '"%s" %s' % (os.path.abspath(__file__), extra)
    r = shell32.ShellExecuteW(None, "runas", exe, params, BASE_DIR, 1)
    return int(r or 0) > 32


def _tray_tip():
    """托盘悬浮提示：CPU / GPU / 内存"""
    with SNAP_LOCK:
        d = dict(SNAP)
    if not d:
        return "电脑资源监控 · 启动中 ..."
    cpu = d.get("cpu") or {}
    gpu = d.get("gpu") or {}
    mem = d.get("mem") or {}
    l1 = "CPU %d%%" % round(cpu.get("percent") or 0)
    if cpu.get("temp") is not None:
        l1 += "   %d°C" % round(cpu["temp"])
    if gpu:
        l2 = "GPU %d%%" % round(gpu.get("percent") or 0)
        if gpu.get("temp") is not None:
            l2 += "   %d°C" % round(gpu["temp"])
        if gpu.get("mem_percent") is not None:
            l2 += "   显存 %d%%" % round(gpu["mem_percent"])
    else:
        l2 = "GPU  —"
    l3 = "内存 %d%%   %s / %s" % (round(mem.get("percent") or 0),
                                 _fmt_cap(mem.get("used")), _fmt_cap(mem.get("total")))
    return "\n".join((l1, l2, l3))


def _notify(title, text):
    """托盘气泡通知（没有托盘时静默忽略）"""
    try:
        if _TRAY:
            _TRAY.notify(title, text)
    except Exception:
        pass


def _temp_task_exists():
    """温度传感器的开机自启计划任务是否已注册"""
    if not IS_WIN:
        return False
    import temp_enable
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", temp_enable.TASK_NAME],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=NO_WINDOW, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


UAC_MARK = os.path.join(BASE_DIR, ".temp_uac_tried")
UAC_COOLDOWN = 12 * 3600          # 12 小时内不重复弹 UAC（避免每次开机骚扰）


def _uac_recently_tried():
    try:
        return time.time() - os.path.getmtime(UAC_MARK) < UAC_COOLDOWN
    except OSError:
        return False


def _mark_uac_tried():
    try:
        with open(UAC_MARK, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def _clear_uac_mark():
    try:
        if os.path.exists(UAC_MARK):
            os.remove(UAC_MARK)
    except Exception:
        pass


def _auto_temp_setup():
    """启动后自动把 CPU 温度传感器带起来（用户不用管）。

    顺序：
      1. 已经能读到温度 → 什么都不做
      2. 计划任务已注册 → schtasks /Run 静默拉起（不弹 UAC）
      3. 已是管理员 → 直接执行启用流程
      4. 都不是 → 提权重启自身（弹一次 UAC，用户点【是】即可）
         用户点【否】会留一个标记，之后不再自动弹，避免每次开机骚扰。
    """
    time.sleep(4)                       # 让服务与托盘先起来
    try:
        import temp_enable
    except Exception:
        return

    if temp_enable.sensor_web_alive():
        _log("[温度] 传感器已在运行")
        return

    # 1) 计划任务已在：静默运行，不弹 UAC
    if _temp_task_exists():
        _log("[温度] 用计划任务拉起传感器")
        temp_enable.run_task()
        for _ in range(20):
            time.sleep(1)
            if temp_enable.sensor_web_alive(timeout=1):
                _log("[温度] 传感器已就绪")
                return
        _log("[温度] 计划任务已运行但 Web 服务未响应")

    # 2) 管理员身份：直接启用
    if temp_enable.is_admin():
        _log("[温度] 以管理员身份启用传感器")
        ok, why = temp_enable.enable(BASE_DIR, _BUNDLE_DIR, say=_log)
        if ok:
            _clear_uac_mark()
            _notify("CPU 温度", "已启用，页面马上显示真实温度。")
        else:
            _log("[温度] 启用失败: %s" % why)
        return

    # 3) 普通权限：自动提权（12 小时内只问一次）
    if _uac_recently_tried():
        _log("[温度] 最近已请求过提权，本次不再自动询问（托盘菜单可手动启用）")
        return
    _mark_uac_tried()
    _log("[温度] 需要管理员权限，正在请求提权（会弹 UAC）")
    _notify("CPU 温度", "正在启用温度传感器，请在系统弹窗中点【是】。")
    if _run_elevated("--elevate-temp"):
        for _ in range(45):              # 等提权进程装完驱动并起服务
            time.sleep(1)
            if temp_enable.sensor_web_alive(timeout=1):
                _log("[温度] 提权进程已完成，温度可用")
                _clear_uac_mark()
                _notify("CPU 温度", "已启用，页面马上显示真实温度。")
                return
        _log("[温度] 提权进程已启动，但温度仍未就绪")
    else:
        _log("[温度] 未获得管理员授权，本次跳过（12 小时后会再自动尝试一次）")
        _notify("CPU 温度", "已取消授权。需要时可在托盘右键 →「启用 CPU 温度传感器」。")


def _enable_temp_from_tray():
    import temp_enable
    if temp_enable.sensor_web_alive():
        _notify("CPU 温度", "传感器已在运行，无需重复启用。")
        return
    _clear_uac_mark()
    if temp_enable.is_admin():
        ok, _why = temp_enable.enable(BASE_DIR, _BUNDLE_DIR, say=_log)
        _notify("CPU 温度", "已启用" if ok else "启用失败")
        return
    _notify("CPU 温度", "正在启用，请在系统弹窗中点【是】。")
    if not _run_elevated("--elevate-temp"):
        _notify("CPU 温度", "已取消管理员授权，未启用。")


def _quit_app():
    """托盘右键「退出」：撤掉图标、停服务、结束进程"""
    if _QUITTING["v"]:
        return
    _QUITTING["v"] = True
    _log("[退出] 用户从托盘菜单退出")

    def _die():
        try:
            if _TRAY:
                _TRAY.stop()
        except Exception:
            pass
        # 让托盘线程有机会把图标删掉，再优雅停 HTTP 服务
        time.sleep(0.35)
        try:
            if _SRV:
                _SRV.shutdown()
        except Exception:
            pass
        remove_pid()
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()


def _start_tray():
    """启动托盘，成功返回 Tray 对象"""
    global _TRAY
    if not IS_WIN:
        return None
    try:
        import startup
        import tray as traymod
    except Exception:
        return None

    icon = os.path.join(WEB_DIR, "static", "icon.ico")
    if not os.path.exists(icon):
        icon = None

    def on_toggle_startup():
        now = startup.toggle()
        if _TRAY:
            _TRAY.notify("开机自动启动", "已开启（下次登录自动运行）" if now else "已关闭")

    cb = {
        "on_open": lambda: _open_url("http://127.0.0.1:%d/" % PORT),
        "on_copy": _copy_to_clipboard,
        "on_toggle_startup": on_toggle_startup,
        "on_enable_temp": _enable_temp_from_tray,
        "on_open_dir": lambda: os.startfile(BASE_DIR),
        "on_quit": _quit_app,
        "get_tip": _tray_tip,
        "get_ips": lambda: [(u, u) for u in _local_urls()],
        "startup_checked": startup.enabled_for_this_copy,
        "log": _log,
    }

    t = traymod.Tray(cb, icon_path=icon)
    if not t.start():
        return None
    _TRAY = t

    def _ticker():
        while True:
            time.sleep(3)
            try:
                t.update_tip()
            except Exception:
                pass

    threading.Thread(target=_ticker, daemon=True).start()
    return t


def main():
    global PORT
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a in ("-p", "--port") and i + 1 < len(argv):
            try:
                PORT = int(argv[i + 1])
            except ValueError:
                pass
        elif a.startswith("--port="):
            try:
                PORT = int(a.split("=", 1)[1])
            except ValueError:
                pass

    # 打包后的 exe 天生没有控制台窗口，也不需要开浏览器：直接静默运行。
    # 源码方式调试时若想保留黑窗口，加 --console。
    quiet = FROZEN or "--tray" in argv or "--quiet" in argv
    if "--console" in argv:
        quiet = False
    want_tray = IS_WIN and "--no-tray" not in argv
    # 只有显式要求时才打开浏览器（默认不弹）
    open_browser = "--open" in argv or "--browser" in argv

    # ---------------- 单一任务模式（不启服务） ----------------
    if "--elevate-temp" in argv:
        import temp_enable
        ok, _why = temp_enable.enable(BASE_DIR, _BUNDLE_DIR, say=_log)
        _pause_before_exit()
        return 0 if ok else 1

    if "--enable-temp" in argv:
        import temp_enable
        if temp_enable.sensor_web_alive():
            _log("CPU 温度传感器已在运行，无需重复启用。")
            _pause_before_exit()
            return 0
        if temp_enable.is_admin():
            ok, _why = temp_enable.enable(BASE_DIR, _BUNDLE_DIR, say=_log)
            _pause_before_exit()
            return 0 if ok else 1
        if _run_elevated("--elevate-temp"):
            _log("已请求管理员权限，请在系统弹窗里点【是】，约 10 秒后生效。")
            time.sleep(1.5)
            return 0
        _log("未能获取管理员权限，已取消。")
        _pause_before_exit()
        return 1

    if "--install-startup" in argv or "--remove-startup" in argv:
        import startup
        ok = startup.enable() if "--install-startup" in argv else startup.disable()
        if ok:
            _log("已设置开机自动启动（登录后自动在右下角托盘运行）。"
                 if "--install-startup" in argv else "已取消开机自动启动。")
        else:
            _log("操作失败。")
        _pause_before_exit()
        return 0 if ok else 1

    # ---------------- 正常启动 ----------------
    # 单实例保护：端口已被占用直接退出（避免双击两次起两个进程）
    if port_in_use(PORT):
        _log("服务已经在运行（端口 %d 被占用），本次启动跳过。" % PORT)
        if open_browser:
            _open_url("http://127.0.0.1:%d/" % PORT)
        return 0

    write_pid()
    atexit.register(remove_pid)

    GPU_SAMPLER.start()
    threading.Thread(target=_temp_worker, daemon=True).start()
    sampler = Sampler()
    sampler.start()

    deadline = time.time() + 3
    while time.time() < deadline:
        with SNAP_LOCK:
            if SNAP:
                break
        time.sleep(0.05)

    global _SRV
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    _SRV = srv

    line = "=" * 58
    _log(line)
    _log("  电脑资源监控服务已启动  ·  端口 %d" % PORT)
    _log(line)
    if FROZEN:
        _log("  程序目录: %s" % BASE_DIR)
    _log("  本机名称: %s" % socket.gethostname())
    for ip in lan_ips():
        _log("  手机访问: http://%s:%d" % (ip, PORT))
    _log(line)
    _log("  手机 / 平板与电脑连同一个 WiFi 即可访问上面的地址。")
    _log("  右下角托盘图标：悬停看占用，右键可打开页面 / 启用温度 / 退出。")
    if not quiet:
        _log("  关闭本窗口即停止服务。")
    _log(line)

    tray = _start_tray() if want_tray else None
    if tray and quiet:
        try:
            import tray as traymod
            traymod.hide_console()
        except Exception:
            pass

    # 自动把 CPU 温度传感器带起来（无需用户操作）
    if "--no-auto-temp" not in argv:
        threading.Thread(target=_auto_temp_setup, daemon=True,
                         name="pcm-auto-temp").start()

    if open_browser:
        try:
            threading.Timer(1.0, lambda: _open_url("http://127.0.0.1:%d/" % PORT)).start()
        except Exception:
            pass

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _log("已停止")
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
        if _TRAY:
            try:
                _TRAY.stop()
            except Exception:
                pass
        remove_pid()
    return 0


if __name__ == "__main__":
    sys.exit(main())
