"""Placement logic runs on every CI OS using a stub for native Windows calls."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from quotabubble.app.state import AppState
from quotabubble.app.taskbar_model import Rect
from quotabubble.app.taskbar_settings import PanelSettings
from quotabubble.ui.taskbar_panel import TaskbarPanel


@pytest.mark.parametrize("missing", ["taskbar", "fullscreen"])
@pytest.mark.parametrize("embedded", [True, False])
def test_steady_docking_does_not_redraw_or_blink_on_a_single_bad_read(
    qapp, monkeypatch, missing, embedded
):
    screen = qapp.primaryScreen()
    g = screen.geometry()
    scale = screen.devicePixelRatio()
    monitor = Rect(0, 0, round(g.width() * scale), round(g.height() * scale))
    info = SimpleNamespace(
        bar=Rect(0, round((g.height() - 48) * scale), monitor.width, round(48 * scale)),
        monitor=monitor, device=screen.name(), visible=True,
        fullscreen=False, autohide=False, left_aligned=False,
    )
    current = [info]
    native = ModuleType("quotabubble.platform.taskbar")
    native.taskbar_info = lambda: current[0]
    native.taskbar_is_light = lambda: False
    repair = Mock()
    native.keep_above_taskbar = repair
    monkeypatch.setitem(sys.modules, native.__name__, native)
    panel = TaskbarPanel(AppState(), PanelSettings(), preview=True)
    panel._native_dock = Mock()
    panel._native_dock.prepare.return_value = embedded
    panel._preview = False
    try:
        panel.sync_placement()
        assert panel.isVisible()
        assert panel.y() == (2 if embedded else g.height() - 46)
        panel._native_dock.prepare.assert_called_with(embedded=True)
        geometry = Mock(wraps=panel.setGeometry)
        update = Mock(wraps=panel.update)
        monkeypatch.setattr(panel, "setGeometry", geometry)
        monkeypatch.setattr(panel, "update", update)
        for _ in range(3):
            panel.sync_placement()
        geometry.assert_not_called()
        update.assert_not_called()
        bad = (
            None if missing == "taskbar"
            else SimpleNamespace(**{**vars(info), "fullscreen": True})
        )
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
