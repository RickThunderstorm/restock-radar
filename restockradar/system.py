"""Betriebssystem-Helfer: Autostart unter Windows und die eigene Adresse im WLAN."""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "RestockRadar"


def autostart_supported() -> bool:
    return sys.platform == "win32"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    project = Path(__file__).resolve().parent.parent
    return f'"{exe}" "{project / "run.py"}" --minimized'


def autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if not autostart_supported():
        raise RuntimeError("Autostart gibt es nur unter Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


_lan_cache: tuple[float, str | None] = (0.0, None)


def lan_ip() -> str | None:
    """Ermittelt die Adresse im Heimnetz (es wird dabei nichts gesendet)."""
    global _lan_cache
    if time.monotonic() - _lan_cache[0] < 60:
        return _lan_cache[1]
    ip = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.168.1.1", 9))
            ip = s.getsockname()[0]
        except OSError:
            pass
    ip = None if not ip or ip.startswith("127.") else ip
    _lan_cache = (time.monotonic(), ip)
    return ip
