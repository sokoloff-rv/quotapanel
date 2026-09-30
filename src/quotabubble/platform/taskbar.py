"""Read taskbar geometry; do not inject code into or reparent to Explorer."""

from __future__ import annotations

import ctypes
import winreg
from ctypes import wintypes
from dataclasses import dataclass

from quotabubble.app.taskbar_model import Rect
from quotabubble.platform.launch import launch_arguments

_user = ctypes.WinDLL("user32", use_last_error=True)
_shell = ctypes.WinDLL("shell32", use_last_error=True)
_user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user.FindWindowW.restype = wintypes.HWND
_user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user.GetWindowRect.restype = wintypes.BOOL
_user.GetForegroundWindow.restype = wintypes.HWND
_user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user.IsWindowVisible.argtypes = [wintypes.HWND]
_user.IsWindowVisible.restype = wintypes.BOOL
_user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user.MonitorFromWindow.restype = wintypes.HANDLE
_user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_user.GetMonitorInfoW.restype = wintypes.BOOL
_user.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
_user.SetWindowPos.restype = wintypes.BOOL


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("monitor", wintypes.RECT),
        ("work", wintypes.RECT),
        ("flags", wintypes.DWORD),
        ("device", wintypes.WCHAR * 32),
    ]


class _AppBarData(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("edge", wintypes.UINT),
        ("rect", wintypes.RECT),
        ("param", ctypes.c_ssize_t),
    ]


_shell.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(_AppBarData)]
_shell.SHAppBarMessage.restype = ctypes.c_size_t


@dataclass(frozen=True)
class TaskbarInfo:
    bar: Rect
    monitor: Rect
    device: str
    visible: bool
    fullscreen: bool
    autohide: bool
    left_aligned: bool


def _rect(hwnd: int) -> Rect | None:
    rect = wintypes.RECT()
    if not _user.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _fullscreen(monitor: Rect) -> bool:
    hwnd = _user.GetForegroundWindow()
    name = ctypes.create_unicode_buffer(256)
    _user.GetClassNameW(hwnd, name, len(name))
    if name.value in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
        return False
    rect = _rect(hwnd)
    return bool(
        rect
        and rect.x <= monitor.x
        and rect.y <= monitor.y
        and rect.x + rect.width >= monitor.x + monitor.width
        and rect.y + rect.height >= monitor.y + monitor.height
    )


def _left_aligned() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "TaskbarAl")
            return value == 0
    except OSError:
        return False


def taskbar_info() -> TaskbarInfo | None:
    hwnd = _user.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return None
    bar = _rect(hwnd)
    info = _MonitorInfo(size=ctypes.sizeof(_MonitorInfo))
    monitor = _user.MonitorFromWindow(hwnd, 2)
    if not bar or not _user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    r = info.monitor
    screen = Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    data = _AppBarData(size=ctypes.sizeof(_AppBarData), hwnd=hwnd)
    autohide = bool(_shell.SHAppBarMessage(4, ctypes.byref(data)) & 1)
    return TaskbarInfo(
        bar,
        screen,
        info.device,
        bool(_user.IsWindowVisible(hwnd)),
        _fullscreen(screen),
        autohide,
        _left_aligned(),
    )


def keep_above_taskbar(hwnd: int) -> None:
    _user.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)


def set_autostart(enabled: bool) -> None:
    import subprocess
    import sys

    arguments = (
        launch_arguments()
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "quotabubble.app.taskbar_main"]
    )
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, "QuotaPanel", 0, winreg.REG_SZ, subprocess.list2cmdline(arguments)
            )
        else:
            try:
                winreg.DeleteValue(key, "QuotaPanel")
            except FileNotFoundError:
                pass


def taskbar_is_light() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return bool(value)
    except OSError:
        return False
