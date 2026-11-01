# -*- coding: utf-8 -*-
"""
开机自动启动（写 HKCU 的 Run 项，不需要管理员权限）
"""
import os
import sys

IS_WIN = os.name == "nt"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "PCMonitor"


def _target():
    """返回 (可执行文件, 附加参数)"""
    if getattr(sys, "frozen", False):
        return sys.executable, "--tray"
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "server.py"))
    return sys.executable, '"%s" --tray' % script


def command():
    exe, args = _target()
    return '"%s" %s' % (exe, args)


def current():
    """读注册表里现有的自启命令，没有则 None"""
    if not IS_WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, APP_NAME)
            return v
    except Exception:
        return None


def is_enabled():
    return bool(current())


def _matches_saved(cmd):
    """判断已注册的路径是否就是当前这份程序（避免换了目录后判断错）"""
    if not cmd:
        return False
    exe, _ = _target()
    return exe.lower() in cmd.lower()


def enabled_for_this_copy():
    cmd = current()
    return bool(cmd) and _matches_saved(cmd)


def enable():
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, command())
        return True
    except Exception:
        return False


def disable():
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def toggle():
    """切换，返回切换后的状态"""
    if enabled_for_this_copy():
        disable()
        return False
    enable()
    return True
