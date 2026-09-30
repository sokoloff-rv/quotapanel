from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows taskbar only")


def test_order_repair_only_when_covered_and_stays_below_flyouts(monkeypatch):
    from quotabubble.platform import taskbar

    previous = {10: 20, 20: 99, 99: 77, 77: None}
    user = SimpleNamespace(
        FindWindowW=lambda *_: 99,
        GetParent=lambda _: None,
        GetWindow=lambda hwnd, _: previous.get(hwnd),
        SetWindowPos=Mock(),
    )
    monkeypatch.setattr(taskbar, "_user", user)
    monkeypatch.setattr(taskbar, "_get_window_long", lambda *_: 8)
    taskbar.keep_above_taskbar(10)
    assert user.SetWindowPos.call_count == 1
    assert user.SetWindowPos.call_args.args[1].value == 77
    # Explorer's flyout remains above both our repaired panel and the tray.
    previous[10] = 77
    previous[99] = 10
    taskbar.keep_above_taskbar(10)
    taskbar.keep_above_taskbar(10)
    assert user.SetWindowPos.call_count == 1


def test_order_traversal_tolerates_cyclic_handles(monkeypatch):
    from quotabubble.platform import taskbar

    user = SimpleNamespace(
        FindWindowW=lambda *_: 99,
        GetParent=lambda _: None,
        GetWindow=Mock(side_effect=lambda hwnd, _: {10: 20, 20: 10}[hwnd]),
        SetWindowPos=Mock(),
    )
    monkeypatch.setattr(taskbar, "_user", user)
    monkeypatch.setattr(taskbar, "_get_window_long", lambda *_: 8)
    taskbar.keep_above_taskbar(10)
    assert user.GetWindow.call_count == 3
    user.SetWindowPos.assert_not_called()


def test_embedded_panel_repairs_only_sibling_order_without_activation(monkeypatch):
    from quotabubble.platform import taskbar

    user = SimpleNamespace(
        FindWindowW=lambda *_: 99, GetParent=lambda _: 99,
        GetWindow=Mock(return_value=None), SetWindowPos=Mock(),
    )
    monkeypatch.setattr(taskbar, "_user", user)
    taskbar.keep_above_taskbar(10)
    user.SetWindowPos.assert_not_called()
    user.GetWindow.return_value = 20
    taskbar.keep_above_taskbar(10)
    assert user.SetWindowPos.call_args.args[1] is None  # HWND_TOP in child order
    assert user.SetWindowPos.call_args.args[-1] & 0x0010  # SWP_NOACTIVATE


@pytest.mark.parametrize("restart", [False, True])
def test_dock_reuses_parent_and_rebinds_after_taskbar_recreation(monkeypatch, restart):
    from PySide6.QtGui import QWindow

    from quotabubble.platform import taskbar, windows

    current_tray, parent = [99], [None]
    native = SimpleNamespace(
        FindWindowW=lambda *_: current_tray[0], GetParent=lambda _: parent[0],
        IsWindow=lambda _: not restart,
    )
    monkeypatch.setattr(taskbar, "_user", native)
    monkeypatch.setattr(taskbar, "_window_band", lambda _: None)
    foreign = Mock()
    disposed = Mock()
    monkeypatch.setattr(taskbar, "_dispose_foreign", disposed)
    wrapper = Mock(return_value=foreign)
    monkeypatch.setattr(QWindow, "fromWinId", wrapper)
    window = Mock()
    window.setParent.side_effect = lambda value: parent.__setitem__(
        0, current_tray[0] if value else None
    )
    widget = Mock()
    widget.winId.return_value = 10
    widget.windowHandle.return_value = window
    configure = Mock()
    monkeypatch.setattr(windows, "configure_window", configure)
    dock = taskbar.TaskbarDock(widget)
    assert dock.prepare(embedded=True)
    assert dock.prepare(embedded=True)
    wrapper.assert_called_once_with(99)
    assert widget.destroy.call_count == int(restart)
    assert configure.call_count == int(restart)
    current_tray[0] = 100
    assert dock.prepare(embedded=True)
    assert wrapper.call_count == 2
    assert parent[0] == 100
    assert dock.prepare(embedded=False) is False
    assert parent[0] is None
    assert disposed.call_count == 2
    dock.detach()
    assert disposed.call_count == 2


@pytest.mark.parametrize("failure", ["absent", "unsupported", "parent_rejected"])
def test_dock_falls_back_to_overlay_if_embedding_unavailable(monkeypatch, failure):
    from PySide6.QtGui import QWindow

    from quotabubble.platform import taskbar

    monkeypatch.setattr(taskbar, "_user", SimpleNamespace(
        FindWindowW=lambda *_: None if failure == "absent" else 99,
        GetParent=lambda _: None, IsWindow=lambda _: True,
    ))
    monkeypatch.setattr(taskbar, "_window_band", lambda _: None)
    foreign = Mock()
    disposed = Mock()
    monkeypatch.setattr(taskbar, "_dispose_foreign", disposed)
    monkeypatch.setattr(QWindow, "fromWinId", lambda _: (
        None if failure == "unsupported" else foreign
    ))
    widget = Mock()
    widget.winId.return_value = 10
    dock = taskbar.TaskbarDock(widget)
    assert dock.prepare(embedded=True) is False
    assert dock._foreign is None
    if failure == "parent_rejected":
        disposed.assert_called_once_with(foreign)
        assert widget.windowHandle().setParent.call_args.args == (None,)


def test_start_open_at_launch_defers_attachment_until_bands_match(monkeypatch):
    from PySide6.QtGui import QWindow

    from quotabubble.platform import taskbar

    parent_band, parent = [6], [None]
    monkeypatch.setattr(taskbar, "_user", SimpleNamespace(
        FindWindowW=lambda *_: 99, GetParent=lambda _: parent[0], IsWindow=lambda _: True,
    ))
    monkeypatch.setattr(taskbar, "_window_band", lambda hwnd: parent_band[0] if hwnd == 99 else 1)
    wrapper = Mock(return_value=Mock())
    monkeypatch.setattr(QWindow, "fromWinId", wrapper)
    widget = Mock()
    widget.winId.return_value = 10
    widget.windowHandle().setParent.side_effect = lambda _: parent.__setitem__(0, 99)
    dock = taskbar.TaskbarDock(widget)
    for _ in range(3):
        assert dock.prepare(embedded=True) is False
    widget.windowHandle().setParent.assert_not_called()
    wrapper.assert_not_called()
    parent_band[0] = 1
    assert dock.prepare(embedded=True)
    wrapper.assert_called_once_with(99)


@pytest.mark.parametrize("result", ["absent", "error", "success"])
def test_optional_band_query_failure_is_not_a_startup_error(monkeypatch, result):
    from quotabubble.platform import taskbar

    def query(hwnd, pointer):
        pointer._obj.value = 6
        return result == "success"

    monkeypatch.setattr(taskbar, "_band_query", None if result == "absent" else query)
    assert taskbar._window_band(99) == (6 if result == "success" else None)


def test_watcher_filters_content_events_and_unhooks_once(monkeypatch):
    from quotabubble.platform import taskbar

    user = SimpleNamespace(
        SetWinEventHook=Mock(side_effect=[11, 12]), UnhookWinEvent=Mock()
    )
    monkeypatch.setattr(taskbar, "_user", user)
    changed = Mock()
    watcher = taskbar.TaskbarEventWatcher(changed)
    watcher._handle(11, 0x8002, 50, -4, 1, 0, 0)
    changed.assert_not_called()
    watcher._handle(11, 3, 50, 0, 0, 0, 0)
    watcher._handle(12, 0x8004, 99, -4, 0, 0, 0)
    assert changed.call_count == 2
    watcher.stop()
    watcher.stop()
    watcher._handle(11, 3, 50, 0, 0, 0, 0)
    assert changed.call_count == 2
    assert user.UnhookWinEvent.call_count == 2


@pytest.mark.parametrize("shell", [True, False])
def test_monitor_sized_shell_flyouts_are_not_fullscreen_apps(monkeypatch, shell):
    from quotabubble.app.taskbar_model import Rect
    from quotabubble.platform import taskbar

    def window_class(hwnd, name, size):
        name.value = "Windows.UI.Core.CoreWindow"

    user = SimpleNamespace(GetForegroundWindow=lambda: 10, GetClassNameW=window_class)
    monkeypatch.setattr(taskbar, "_user", user)
    monkeypatch.setattr(taskbar, "_rect", lambda _: Rect(0, 0, 1920, 1080))
    monkeypatch.setattr(taskbar, "_shell_flyout", lambda _: shell)
    assert taskbar._fullscreen(Rect(0, 0, 1920, 1080)) is not shell


def test_shell_identity_uses_executable_basename_and_closes_handle(monkeypatch):
    from quotabubble.platform import taskbar

    def pid(hwnd, pointer):
        pointer._obj.value = 123

    def path(handle, flags, buffer, size):
        buffer.value = r"C:\Windows\SystemApps\Shell\StartMenuExperienceHost.exe"
        return True

    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=42), QueryFullProcessImageNameW=path, CloseHandle=Mock()
    )
    monkeypatch.setattr(taskbar, "_user", SimpleNamespace(GetWindowThreadProcessId=pid))
    monkeypatch.setattr(taskbar, "_kernel", kernel)
    assert taskbar._shell_flyout(10) is True
    kernel.OpenProcess.assert_called_once_with(0x1000, False, 123)
    kernel.CloseHandle.assert_called_once_with(42)
