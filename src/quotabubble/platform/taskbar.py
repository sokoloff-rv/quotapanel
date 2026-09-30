"""Read taskbar geometry; do not inject code into or reparent to Explorer."""

from __future__ import annotations

import ctypes
import winreg
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import PureWindowsPath

from quotabubble.app.taskbar_model import Rect
from quotabubble.platform.launch import launch_arguments

_user = ctypes.WinDLL("user32", use_last_error=True)
_shell = ctypes.WinDLL("shell32", use_last_error=True)
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user.FindWindowW.restype = wintypes.HWND
_user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user.GetWindowRect.restype = wintypes.BOOL
_user.GetForegroundWindow.restype = wintypes.HWND
_user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user.GetWindowThreadProcessId.restype = wintypes.DWORD
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
_kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel.CloseHandle.restype = wintypes.BOOL
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
_user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user.GetWindow.restype = wintypes.HWND
_get_window_long = (
    _user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else _user.GetWindowLongW
)
_get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_window_long.restype = ctypes.c_ssize_t
_WinEventProc = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
)
_user.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WinEventProc,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
]
_user.SetWinEventHook.restype = wintypes.HANDLE
_user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
_user.UnhookWinEvent.restype = wintypes.BOOL


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
    covers_monitor = bool(
        rect
        and rect.x <= monitor.x
        and rect.y <= monitor.y
        and rect.x + rect.width >= monitor.x + monitor.width
        and rect.y + rect.height >= monitor.y + monitor.height
    )
    return covers_monitor and not _shell_flyout(hwnd)


def _shell_flyout(hwnd: int) -> bool:
    # Shell flyouts can use a monitor-sized transparent host window. Its
    # bounds alone must not make Start/Search/notifications look fullscreen.
    pid = wintypes.DWORD()
    _user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    process = _kernel.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not process:
        return False
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if not _kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return False
        return PureWindowsPath(path.value).name.lower() in {
            "startmenuexperiencehost.exe", "shellexperiencehost.exe",
            "searchhost.exe", "searchapp.exe",
        }
    finally:
        _kernel.CloseHandle(process)


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
    if not _needs_raise(hwnd):
        return
    tray = _user.FindWindowW("Shell_TrayWnd", None)
    if not tray:
        return
    # Insert just above the taskbar, preserving flyouts/popups above it.
    predecessor = _user.GetWindow(tray, 3) or -1
    _user.SetWindowPos(
        hwnd, wintypes.HWND(predecessor), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200
    )


def _needs_raise(hwnd: int) -> bool:
    if not (_get_window_long(hwnd, -20) & 0x00000008):  # WS_EX_TOPMOST
        return True
    tray = _user.FindWindowW("Shell_TrayWnd", None)
    if not tray:
        return False
    previous = _user.GetWindow(hwnd, 3)  # GW_HWNDPREV
    seen: set[int] = set()
    # Window handles can disappear or reorder while being read. Bound the
    # traversal and detect cycles; never mutate a healthy window each second.
    for _ in range(128):
        if not previous or previous in seen:
            return False
        if previous == tray:
            return True
        seen.add(previous)
        previous = _user.GetWindow(previous, 3)
    return False


class TaskbarEventWatcher:
    """Observe window order without injecting into Explorer or inspecting content."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._closed = False
        self._callback = _WinEventProc(self._handle)
        self._hooks = []
        for first, last in ((3, 3), (0x8002, 0x8004)):
            # OUTOFCONTEXT | SKIPOWNPROCESS: callbacks run on the installing
            # GUI thread, and our own window repairs cannot trigger a loop.
            hook = _user.SetWinEventHook(first, last, None, self._callback, 0, 0, 2)
            if hook:
                self._hooks.append(hook)

    def _handle(self, hook, event, hwnd, object_id, child_id, thread, when) -> None:
        if not self._closed and (event in {3, 0x8004} or object_id == 0):
            self._on_change()

    def stop(self) -> None:
        self._closed = True
        for hook in self._hooks:
            _user.UnhookWinEvent(hook)
        self._hooks.clear()


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
