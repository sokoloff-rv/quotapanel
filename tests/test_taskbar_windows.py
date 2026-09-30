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
        GetWindow=Mock(side_effect=lambda hwnd, _: {10: 20, 20: 10}[hwnd]),
        SetWindowPos=Mock(),
    )
    monkeypatch.setattr(taskbar, "_user", user)
    monkeypatch.setattr(taskbar, "_get_window_long", lambda *_: 8)
    taskbar.keep_above_taskbar(10)
    assert user.GetWindow.call_count == 3
    user.SetWindowPos.assert_not_called()


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


@pytest.mark.parametrize("missing", ["taskbar", "fullscreen"])
def test_steady_docking_does_not_redraw_or_blink_on_a_single_bad_read(
    qapp, monkeypatch, missing
):
    from dataclasses import replace

    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_model import Rect
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.platform import taskbar
    from quotabubble.ui.taskbar_panel import TaskbarPanel

    screen = qapp.primaryScreen()
    g = screen.geometry()
    scale = screen.devicePixelRatio()
    monitor = Rect(0, 0, round(g.width() * scale), round(g.height() * scale))
    info = taskbar.TaskbarInfo(
        Rect(0, round((g.height() - 48) * scale), monitor.width, round(48 * scale)),
        monitor, screen.name(), True, False, False, False,
    )
    current = [info]
    monkeypatch.setattr(taskbar, "taskbar_info", lambda: current[0])
    monkeypatch.setattr(taskbar, "taskbar_is_light", lambda: False)
    repair = Mock()
    monkeypatch.setattr(taskbar, "keep_above_taskbar", repair)
    panel = TaskbarPanel(AppState(), PanelSettings(), preview=True)
    panel._preview = False
    try:
        panel.sync_placement()
        assert panel.isVisible()
        geometry = Mock(wraps=panel.setGeometry)
        update = Mock(wraps=panel.update)
        monkeypatch.setattr(panel, "setGeometry", geometry)
        monkeypatch.setattr(panel, "update", update)
        for _ in range(3):
            panel.sync_placement()
        geometry.assert_not_called()
        update.assert_not_called()
        bad = None if missing == "taskbar" else replace(info, fullscreen=True)
        current[0] = bad
        panel.sync_placement()
        assert panel.isVisible()
        current[0] = info
        panel.sync_placement()
        assert panel.isVisible()
        current[0] = bad
        panel.sync_placement()
        panel.sync_placement()
        assert not panel.isVisible()
        current[0] = info
        panel.sync_placement()
        assert panel.isVisible()
        # Bursts of shell events coalesce into one immediate order check.
        repair.reset_mock()
        for _ in range(5):
            panel._schedule_window_order()
        qapp.processEvents()
        repair.assert_called_once()
        panel._show_requested = False
        panel.sync_placement()
        assert not panel.isVisible()
    finally:
        panel.close()
        panel.popup.close()
