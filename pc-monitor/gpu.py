# -*- coding: utf-8 -*-
"""
多厂商 GPU 采集（NVIDIA / AMD / Intel）

三个数据源，按精度从高到低自动选择：
  1. nvidia-smi            —— NVIDIA 独显，占用/显存/温度都精确（已安装驱动即自带）
  2. LibreHardwareMonitor  —— 支持 NVIDIA / AMD / Intel 全厂商，含 GPU 温度、风扇
  3. Windows PDH 性能计数器 —— 兜底，任意厂商都能读到 3D 引擎利用率和专用显存，
                              不需要任何额外程序、不需要管理员权限

三者都不需要改动系统，读不到就返回 None，前端显示「—」。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

from ctypes import wintypes

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
MB = 1024 * 1024


# ------------------------------------------------------------------ 显卡名称（注册表）
_ADAPTER_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
_SKIP_NAMES = ("microsoft basic display", "microsoft remote display", "virtual display",
               "standard vga", "displaylink", "parsec", "spacedesk", "todesk")


def adapters():
    """从注册表枚举所有显示适配器，返回 [(名称, 显存字节数)]（快，不需要权限）"""
    out = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _ADAPTER_KEY) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        desc, _ = winreg.QueryValueEx(k, "DriverDesc")
                        desc = " ".join(str(desc).split())
                        if not desc or any(s in desc.lower() for s in _SKIP_NAMES):
                            continue
                        vram = 0
                        try:
                            hi = winreg.OpenKey(k, "HardwareInformation")
                            with hi:
                                v, _t = winreg.QueryValueEx(hi, "qwMemorySize")
                                vram = int(v or 0)
                        except OSError:
                            pass
                        out.append((desc, vram))
                except OSError:
                    continue
    except Exception:
        pass
    return out


def adapter_names():
    return [a[0] for a in adapters()]


def _vendor_of(name):
    low = (name or "").lower()
    if "nvidia" in low or "geforce" in low or "quadro" in low or "rtx" in low:
        return "nvidia"
    if "amd" in low or "radeon" in low or "ati " in low or "firepro" in low:
        return "amd"
    if "intel" in low or "arc" in low or "iris" in low or "uhd" in low or "hd graphics" in low:
        return "intel"
    return "unknown"


def _pick_name(vendor, names, used):
    """在注册表名称里挑一个同厂商、还没被占用的"""
    for n in names:
        if n in used:
            continue
        if _vendor_of(n) == vendor:
            return n
    return None


# ------------------------------------------------------------------ nvidia-smi
def read_nvidia():
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not os.path.exists(exe):
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=NO_WINDOW)
    except Exception:
        return []
    gpus = []
    for line in (out.stdout or "").strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            used = float(parts[2]) * MB
            total = float(parts[3]) * MB
            temp = float(parts[4])
        except ValueError:
            continue
        gpus.append({
            "name": parts[0], "vendor": "nvidia",
            "percent": round(float(parts[1]), 1) if parts[1] not in ("", "[N/A]") else None,
            "mem_used": int(used), "mem_total": int(total),
            "mem_percent": round(used / total * 100, 1) if total else 0.0,
            "temp": round(temp, 1) if temp > 0 else None,
            "shared": False, "src": "nvidia-smi",
        })
    return gpus


# ------------------------------------------------------------------ LibreHardwareMonitor
_GPU_IMG = {"nvidia.png": "nvidia", "amd.png": "amd", "ati.png": "amd",
            "intel.png": "intel", "gpu.png": "unknown"}


def _num(text):
    """'61.0 °C' / '25.0 %' / '6509.0 MB' -> float"""
    try:
        return float(str(text).split()[0])
    except (ValueError, IndexError):
        return None


def read_lhm(url):
    """从 LibreHardwareMonitor 的 Web 服务读所有 GPU。读不到返回 []"""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=2) as r:
            raw = r.read()
        root = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return []

    gpus = []

    def walk(node):
        for ch in node.get("Children") or []:
            img = (ch.get("ImageURL") or "").rsplit("/", 1)[-1]
            if img in _GPU_IMG:
                g = _parse_gpu_node(ch, _GPU_IMG[img])
                if g:
                    gpus.append(g)
            else:
                walk(ch)

    try:
        walk(root)
    except Exception:
        return []
    return gpus


def _parse_gpu_node(node, vendor):
    name = (node.get("Text") or "").strip()
    if not name:
        return None
    util = mem_pct = temp = None
    mem_used = mem_total = None
    used_shared = False
    d3d_3d = []          # "D3D 3D" 引擎（最接近任务管理器的 GPU 利用率）
    d3d_any = []         # 其它 D3D 引擎（Intel 核显只有这一类）

    for grp in node.get("Children") or []:
        gname = (grp.get("Text") or "").strip()
        for s in grp.get("Children") or []:
            stext = (s.get("Text") or "").strip()
            sval = (s.get("Value") or "").strip()
            low = stext.lower()
            if gname == "Load":
                v = _num(sval)
                if v is None:
                    continue
                if low == "gpu core":
                    util = v
                elif low == "gpu memory":
                    mem_pct = v
                elif low.startswith("d3d"):
                    (d3d_3d if low == "d3d 3d" else d3d_any).append(v)
            elif gname == "Temperatures":
                if low in ("gpu core", "gpu", "core") and temp is None:
                    temp = _num(sval)
            elif gname == "Data":
                if low == "gpu memory used":
                    mem_used = _num(sval)
                elif low == "gpu memory total":
                    mem_total = _num(sval)
                elif low == "d3d shared memory used" and mem_used is None:
                    mem_used = _num(sval)          # 核显没有独立显存，用共享内存
                    used_shared = True
                elif low == "d3d shared memory total" and mem_total is None:
                    mem_total = _num(sval)
                    used_shared = True
                elif low == "d3d dedicated memory used" and mem_used is None:
                    mem_used = _num(sval)

    if util is None:
        pool = d3d_3d or d3d_any
        if pool:
            util = max(pool)
    if util is not None:
        util = round(min(100.0, util), 1)
    if temp is not None and not (0 < temp < 130):
        temp = None

    used = int(mem_used * MB) if mem_used else None
    total = int(mem_total * MB) if mem_total else None
    if mem_pct is None and used and total:
        mem_pct = round(used / total * 100, 1)
    shared = bool(used_shared)

    return {
        "name": name, "vendor": vendor, "percent": util,
        "mem_used": used, "mem_total": total,
        "mem_percent": round(mem_pct, 1) if mem_pct is not None else None,
        "temp": round(temp, 1) if temp is not None else None,
        "shared": shared, "src": "lhm",
    }


# ------------------------------------------------------------------ Windows PDH
_PDH_MORE_DATA = -2147481646          # 0x800007D2 as signed LONG
_PDH_FMT_DOUBLE = 0x00000200
_ENGINE_RE = re.compile(
    r"pid_(\d+)_luid_(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+)_phys_(\d+)_eng_(\d+)_engtype_(\w+)",
    re.I)
_LUID_RE = re.compile(r"luid_(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+)_phys_(\d+)", re.I)


class _Pdh:
    """PDH GPU 采集器。长期持有一个 query，按需 Collect（开销约 20~40ms）"""

    def __init__(self):
        self.ready = None            # None=未尝试 True=可用 False=不可用
        self.lock = threading.Lock()
        self.q = None
        self.h_engine = None
        self.h_mem = None
        self.cache = {"gpus": [], "ts": 0.0}

    # -- 内部 ------------------------------------------------------
    def _open(self):
        import ctypes
        try:
            pdh = ctypes.WinDLL("pdh")
        except Exception:
            return False

        PTR = ctypes.c_size_t

        class VAL(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("longValue", wintypes.LONG), ("doubleValue", ctypes.c_double),
                            ("largeValue", ctypes.c_longlong),
                            ("AnsiStringValue", ctypes.c_char_p),
                            ("WideStringValue", ctypes.c_wchar_p)]
            _anonymous_ = ("u",)
            _fields_ = [("CStatus", wintypes.DWORD), ("u", _U)]

        class ITEM(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", VAL)]

        pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, PTR, ctypes.POINTER(wintypes.HANDLE)]
        pdh.PdhOpenQueryW.restype = wintypes.LONG
        for fn in ("PdhAddEnglishCounterW", "PdhAddCounterW"):
            f = getattr(pdh, fn)
            f.argtypes = [wintypes.HANDLE, ctypes.c_wchar_p, PTR, ctypes.POINTER(wintypes.HANDLE)]
            f.restype = wintypes.LONG
        pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        pdh.PdhCollectQueryData.restype = wintypes.LONG
        pdh.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
        pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        pdh.PdhCloseQuery.restype = wintypes.LONG

        q = wintypes.HANDLE()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
            return False

        def add(path):
            h = wintypes.HANDLE()
            if pdh.PdhAddEnglishCounterW(q, path, 0, ctypes.byref(h)) == 0:
                return h
            h2 = wintypes.HANDLE()
            if pdh.PdhAddCounterW(q, path, 0, ctypes.byref(h2)) == 0:
                return h2
            return None

        he = add(r"\GPU Engine(*)\Utilization Percentage")
        hm = add(r"\GPU Adapter Memory(*)\Dedicated Usage")
        if he is None and hm is None:
            pdh.PdhCloseQuery(q)
            return False

        self._pdh, self._ITEM = pdh, ITEM
        self.q, self.h_engine, self.h_mem = q, he, hm
        # 先采一次建立基准，否则首个读数恒为 0
        pdh.PdhCollectQueryData(q)
        time.sleep(0.12)
        pdh.PdhCollectQueryData(q)
        return True

    def _read_array(self, h):
        import ctypes
        if not h:
            return []
        size = wintypes.DWORD(0)
        cnt = wintypes.DWORD(0)
        rc = self._pdh.PdhGetFormattedCounterArrayW(
            h, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(cnt), None)
        if rc != _PDH_MORE_DATA or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        rc = self._pdh.PdhGetFormattedCounterArrayW(
            h, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(cnt), buf)
        if rc != 0:
            return []
        out = []
        items = ctypes.cast(buf, ctypes.POINTER(self._ITEM))
        for i in range(cnt.value):
            it = items[i]
            if it.FmtValue.CStatus == 0 and it.szName:
                out.append((it.szName, it.FmtValue.doubleValue))
        return out

    # -- 对外 ------------------------------------------------------
    def refresh(self):
        if self.ready is None:
            with self.lock:
                if self.ready is None:
                    try:
                        self.ready = self._open()
                    except Exception:
                        self.ready = False
        if not self.ready:
            return []

        with self.lock:
            try:
                self._pdh.PdhCollectQueryData(self.q)
                engines = self._read_array(self.h_engine)
                mems = self._read_array(self.h_mem)
            except Exception:
                return []

        # 每个 (gpu, 引擎) 累加所有进程 → 取最大的引擎 → 该 GPU 的利用率
        per_engine = {}
        for name, val in engines:
            m = _ENGINE_RE.search(name)
            if not m:
                continue
            key = (m.group(2).lower(), m.group(3).lower(), m.group(4))
            per_engine.setdefault(key, {})
            e = m.group(5)
            per_engine[key][e] = per_engine[key].get(e, 0.0) + val

        def by_luid(rows):
            out = {}
            for name, val in rows:
                m = _LUID_RE.search(name)
                if not m:
                    continue
                key = (m.group(1).lower(), m.group(2).lower(), m.group(3))
                out[key] = max(out.get(key, 0), val)
            return out

        vram = by_luid(mems)

        gpus = []
        for key, engines_val in per_engine.items():
            util = max(engines_val.values()) if engines_val else None
            if util is not None:
                util = round(min(100.0, util), 1)
            used = int(vram.get(key) or 0)
            gpus.append({
                "name": "", "vendor": "unknown", "percent": util,
                "mem_used": used or None, "mem_total": None,
                "mem_percent": None,
                "temp": None, "shared": False, "src": "pdh",
                "luid": "%s:%s:%s" % key,
            })
        self.cache = {"gpus": gpus, "ts": time.time()}
        return gpus

    def peek(self):
        return self.cache["gpus"]


# ------------------------------------------------------------------ 汇总
class GpuSampler(threading.Thread):
    """后台线程：每 2 秒刷新一次，合并三个来源给出统一结果"""

    def __init__(self, lhm_url, interval=2.0):
        super().__init__(daemon=True)
        self.lhm_url = lhm_url
        self.interval = interval
        self.lock = threading.Lock()
        self.result = {"gpus": [], "ts": 0.0}
        self._pdh = _Pdh()
        self._names = []
        self._names_at = 0.0
        self._nv_at = 0.0
        self._nv = []

    # 显卡名注册表枚举缓 30s（换显卡驱动时会变，但无所谓）
    def _adapter_names(self):
        if not self._names or time.time() - self._names_at > 30:
            self._names = adapter_names()
            self._names_at = time.time()
        return self._names

    def _sample(self):
        now = time.time()
        names = self._adapter_names()
        used_names = set()
        gpus = []

        # 1) nvidia-smi（缓存 2s）
        if not self._nv or now - self._nv_at > 2:
            self._nv = read_nvidia()
            self._nv_at = now
        for g in self._nv:
            used_names.add(g["name"])
            gpus.append(g)

        # 2) LHM：补充 Intel / AMD，以及 NVIDIA 的温度
        try:
            lhm = read_lhm(self.lhm_url)
        except Exception:
            lhm = []
        lhm_vendors = set()
        for g in lhm:
            lhm_vendors.add(g["vendor"])
            idx = next((i for i, x in enumerate(gpus)
                        if x["vendor"] == g["vendor"] and
                        (not x["name"] or not g["name"] or x["name"] == g["name"])), None)
            if idx is not None:
                base = gpus[idx]
                for k in ("percent", "temp", "mem_percent"):
                    if base.get(k) is None and g.get(k) is not None:
                        base[k] = g[k]
                for k in ("mem_used", "mem_total"):
                    if not base.get(k) and g.get(k):
                        base[k] = g[k]
            else:
                used_names.add(g["name"])
                gpus.append(dict(g))

        # 3) PDH 兜底：LHM 没覆盖到的厂商就补上
        pdh = self._pdh.peek()
        need_pdh = not gpus or (not lhm and not self._nv)
        if need_pdh or not lhm:
            pdh = self._pdh.refresh() or pdh
            if not lhm:
                # LHM 没开：用 PDH 补齐所有 GPU（含 Intel/AMD 核显）
                have = {g["vendor"] for g in gpus}
                extras = [g for g in pdh if g["vendor"] == "unknown"]
                # 丢掉既没显存又完全空闲的适配器（微软基本渲染驱动之类）
                useful = [g for g in extras if g.get("mem_used") or g.get("percent")]
                if not useful and extras:
                    useful = [max(extras, key=lambda x: (x.get("mem_used") or 0,
                                                         x.get("percent") or 0))]
                useful.sort(key=lambda x: (x.get("mem_used") or 0,
                                           x.get("percent") or 0), reverse=True)
                for g in useful:
                    # 名称/厂商靠注册表猜
                    cand = None
                    for n in names:
                        if n not in used_names and _vendor_of(n) not in have:
                            cand = n
                            break
                    if cand:
                        g["name"] = cand
                        g["vendor"] = _vendor_of(cand)
                        have.add(g["vendor"])
                        used_names.add(cand)
                    else:
                        g["name"] = g.get("name") or ("GPU %d" % (len(gpus) + 1))
                    gpus.append(g)

        # 主 GPU：优先独显（非共享显存），其次显存大的，最后看利用率
        def rank(g):
            return (0 if g.get("shared") else 1,
                    1 if (g.get("mem_total") or 0) > 0 else 0,
                    g.get("mem_total") or 0,
                    g.get("percent") or 0)
        gpus.sort(key=rank, reverse=True)
        for g in gpus:
            g.pop("luid", None)

        with self.lock:
            self.result = {"gpus": gpus, "ts": now}

    def run(self):
        while True:
            t0 = time.time()
            try:
                self._sample()
            except Exception:
                pass
            time.sleep(max(0.3, self.interval - (time.time() - t0)))

    def snapshot(self):
        with self.lock:
            return list(self.result["gpus"])
