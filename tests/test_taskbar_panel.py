from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quotabubble.app.taskbar_model import (
    Rect,
    dock_rect,
    primary_windows,
    reset_countdown,
    row_values,
)
from quotabubble.providers.base import ProviderStatus, UsageSnapshot, UsageWindow


def test_hidden_autohide_taskbar_does_not_leave_panel_on_desktop() -> None:
    screen = Rect(0, 0, 1920, 1080)
    assert dock_rect(Rect(0, 1078, 1920, 48), screen, 280, 12) is None
    assert dock_rect(Rect(0, 1032, 1920, 48), screen, 280, 12) == Rect(12, 1034, 280, 44)


def test_secondary_monitor_with_negative_coordinates() -> None:
    screen = Rect(-1920, -100, 1920, 1080)
    assert dock_rect(Rect(-1920, 932, 1920, 48), screen, 280, 12) == Rect(-1908, 934, 280, 44)


def test_vertical_taskbar_is_not_covered() -> None:
    assert dock_rect(Rect(0, 0, 48, 1080), Rect(0, 0, 1920, 1080), 280, 12) is None


def test_zero_offset_reaches_left_edge_on_negative_coordinate_monitor() -> None:
    screen = Rect(-1920, -100, 1920, 1080)
    assert dock_rect(Rect(-1920, 932, 1920, 48), screen, 280, 0) == Rect(-1920, 934, 280, 44)


def test_missing_session_never_relabels_weekly_as_five_hours() -> None:
    snapshot = UsageSnapshot(
        provider="codex",
        display_name="Codex",
        windows=[UsageWindow(key="weekly", label="Weekly", used_pct=80)],
    )
    assert row_values(snapshot, False)[1] == [("—", "muted"), ("80%", "warning")]


def test_overall_weekly_limit_is_preferred_to_model_specific() -> None:
    scoped = UsageWindow(key="weekly", label="Sonnet", scope="sonnet", used_pct=90)
    overall = UsageWindow(key="weekly", label="Weekly", used_pct=20)
    snapshot = UsageSnapshot(provider="claude", display_name="Claude", windows=[scoped, overall])
    assert primary_windows(snapshot)[1] is overall


def test_remaining_mode_preserves_warning_based_on_usage() -> None:
    snapshot = UsageSnapshot(
        provider="codex",
        display_name="Codex",
        windows=[UsageWindow(key="session", label="5h", used_pct=95)],
    )
    assert row_values(snapshot, True)[1][0] == ("5%", "critical")


def test_expired_credentials_cannot_look_like_zero_usage() -> None:
    snapshot = UsageSnapshot(
        provider="claude", display_name="Claude", status=ProviderStatus.EXPIRED
    )
    assert row_values(snapshot, False) == ("Войдите заново", [])


def test_countdown_does_not_show_zero_before_reset() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert reset_countdown(now + timedelta(seconds=30), now) == "1м"
    assert reset_countdown(now + timedelta(hours=2, minutes=14), now) == "2ч 14м"
    assert reset_countdown(now + timedelta(days=3, hours=7), now) == "3д 7ч"
    assert reset_countdown(now - timedelta(seconds=1), now) == "сейчас"
    assert reset_countdown(None, now) == "—"


def test_upgrade_shows_remaining_without_losing_placement(tmp_path) -> None:
    from quotabubble.app.taskbar_settings import PanelSettings

    path = tmp_path / "settings.json"
    path.write_text('{"remaining": false, "width": 320, "offset": 20}', encoding="utf-8")
    settings = PanelSettings.load(path)
    assert settings.remaining is True
    assert (settings.width, settings.offset) == (320, 20)
    assert settings.refresh_interval_minutes == 5
    assert settings.theme == "system"
    assert settings.transparent_background is False


def test_panel_defaults_to_remaining() -> None:
    from quotabubble.app.taskbar_main import demo_snapshots
    from quotabubble.app.taskbar_settings import PanelSettings

    assert row_values(demo_snapshots()[0], PanelSettings().remaining)[1][0][0] == "58%"


def test_preview_renders_both_rows_without_reading_credentials(qapp: object) -> None:
    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_main import demo_snapshots
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.ui.taskbar_panel import TaskbarPanel

    state = AppState()
    state.replace(demo_snapshots())
    panel = TaskbarPanel(state, PanelSettings(), preview=True)
    panel.show()
    assert not panel.grab().isNull()
    panel.popup.rebuild()
    assert panel.popup.layout().count() >= 9
    panel.close()
    panel.popup.close()


def test_popup_geometry_is_stable_on_first_show_and_countdown_ticks(qapp) -> None:
    from PySide6.QtWidgets import QLabel, QPushButton

    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_main import demo_snapshots
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.ui.taskbar_panel import TaskbarPanel

    state = AppState()
    state.replace(demo_snapshots())
    panel = TaskbarPanel(state, PanelSettings(), preview=True)
    popup = panel.popup
    try:
        popup.rebuild()
        first_size = popup.size()
        popup.show()
        qapp.processEvents()
        assert popup.size() == first_size
        geometry = [label.geometry() for label in popup.findChildren(QLabel)]
        buttons = popup.findChildren(QPushButton)
        refreshed = []
        popup.refresh_requested.connect(lambda: refreshed.append(True))
        for _ in range(3):
            panel._tick_countdown()
            qapp.processEvents()
            assert popup.size() == first_size
            assert [label.geometry() for label in popup.findChildren(QLabel)] == geometry
            assert popup.findChildren(QPushButton) == buttons
        buttons[0].click()
        assert refreshed == [True]
    finally:
        popup.close()
        panel.close()


def test_details_use_desktop_coordinates_when_panel_geometry_is_parent_relative(qapp, monkeypatch):
    from PySide6.QtCore import QPoint

    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_main import demo_snapshots
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.ui.taskbar_panel import TaskbarPanel

    state = AppState()
    state.replace(demo_snapshots())
    panel = TaskbarPanel(state, PanelSettings(), preview=True)
    area = panel.screen().availableGeometry()
    desktop_origin = QPoint(area.left() + 10, area.bottom() - 44)
    panel.setGeometry(10, 2, 280, 44)
    monkeypatch.setattr(panel, "mapToGlobal", lambda _: desktop_origin)
    try:
        panel.show_details()
        assert panel.popup.x() == desktop_origin.x()
        assert panel.popup.y() == max(
            area.top(), desktop_origin.y() - panel.popup.height() - 8
        )
    finally:
        panel.popup.close()
        panel.close()


def test_quit_stops_docking_and_releases_foreign_wrapper_before_application_exit(qapp):
    from unittest.mock import Mock

    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_controller import PanelShutdownController
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.ui.taskbar_panel import TaskbarPanel

    panel = TaskbarPanel(AppState(), PanelSettings(), preview=True)
    order = []
    panel._native_dock = Mock()
    panel._native_dock.detach.side_effect = lambda: order.append("detach")
    panel._order_watcher = Mock()
    application = Mock()
    application.quit.side_effect = lambda: order.append("quit")
    panel._dock_timer.start()
    try:
        PanelShutdownController(panel, application).quit()
        assert order == ["detach", "quit"]
        assert not panel._dock_timer.isActive()
        panel._order_watcher.stop.assert_called_once()
    finally:
        panel.close()


def test_settings_apply_zero_offset_and_interval_to_running_poller(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtWidgets import QDialog

    from quotabubble.app.polling import PollingService
    from quotabubble.app.runtime import PollingRuntime
    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_controller import PanelSettingsController
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.ui.taskbar_panel import PanelSettingsDialog, TaskbarPanel

    settings = PanelSettings()
    path = tmp_path / "settings.json"
    save = PanelSettings.save
    monkeypatch.setattr(PanelSettings, "save", lambda self: save(self, path))

    def accept(dialog):
        dialog.offset_input.setValue(0)
        dialog.interval_input.setValue(2)
        dialog.theme_input.setCurrentIndex(dialog.theme_input.findData("light"))
        dialog.transparent_input.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PanelSettingsDialog, "exec", accept)
    panel = TaskbarPanel(AppState(), settings, preview=True)
    controller = PanelSettingsController(panel, settings)
    service = PollingService(
        [], settings.refresh_interval_ms,
        runtime=PollingRuntime([], last_good={}, save_last_good=lambda _: None),
    )
    controller.polling = service
    service.start()
    try:
        controller.configure()
        # Drain the queued interval update in the polling thread without
        # waiting for a real refresh or touching account credentials.
        QMetaObject.invokeMethod(
            service._worker, "poll", Qt.ConnectionType.BlockingQueuedConnection
        )
        assert service._worker._timer.interval() == 120_000
        restored = PanelSettings.load(path)
        assert restored.offset == 0
        assert restored.refresh_interval_minutes == 2
        assert restored.refresh_interval_ms == 120_000
        assert restored.theme == "light"
        assert restored.transparent_background is True
        assert panel._light is True
        assert panel._transparent is True
    finally:
        service.stop()
        panel.close()
        panel.popup.close()


def test_cancel_settings_does_not_change_storage_or_interval(qapp, monkeypatch):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QDialog

    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_controller import PanelSettingsController
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.ui.taskbar_panel import PanelSettingsDialog, TaskbarPanel

    settings = PanelSettings(offset=0, refresh_interval_minutes=7)
    before = settings.model_dump()
    save = Mock()
    monkeypatch.setattr(PanelSettings, "save", save)

    def cancel(dialog):
        dialog.offset_input.setValue(20)
        dialog.interval_input.setValue(1)
        dialog.theme_input.setCurrentIndex(dialog.theme_input.findData("dark"))
        dialog.transparent_input.setChecked(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(PanelSettingsDialog, "exec", cancel)
    panel = TaskbarPanel(AppState(), settings, preview=True)
    controller = PanelSettingsController(panel, settings)
    controller.polling = Mock()
    controller.configure()
    assert settings.model_dump() == before
    save.assert_not_called()
    controller.polling.set_interval.assert_not_called()
    panel.close()
    panel.popup.close()


@pytest.mark.parametrize(
    ("theme", "system_light", "expected_light"),
    [("system", False, False), ("system", True, True),
     ("light", False, True), ("dark", True, False)],
)
@pytest.mark.parametrize("transparent", [False, True])
def test_theme_and_transparency_render_independently(
    qapp, theme, system_light, expected_light, transparent
):
    from quotabubble.app.state import AppState
    from quotabubble.app.taskbar_main import demo_snapshots
    from quotabubble.app.taskbar_settings import PanelSettings
    from quotabubble.ui.taskbar_panel import TaskbarPanel

    state = AppState()
    state.replace(demo_snapshots())
    settings = PanelSettings(theme=theme, transparent_background=transparent)
    panel = TaskbarPanel(state, settings, preview=True)
    try:
        panel._update_appearance(system_light)
        panel.show()
        image = panel.grab().toImage()
        scale = image.devicePixelRatio()
        background = image.pixelColor(round(4 * scale), round(20 * scale))
        assert panel._light is expected_light
        if transparent:
            assert background.alpha() == 1
            # Windows uses alpha for native hit testing. All blank pixels,
            # including the corners, must retain a nonzero alpha value.
            assert all(
                image.pixelColor(x, y).alpha() >= 1
                for x in range(image.width()) for y in range(image.height())
            )
        else:
            assert background.alpha() > 200
            assert (background.red() > 200) is expected_light
        # Font rasterizers need not produce any alpha-255 glyph pixels.
        # Look for sufficiently visible text in the expected foreground color,
        # excluding the background rather than requiring full opacity.
        foreground = (37, 43, 53) if expected_light else (237, 241, 247)
        ink = [
            image.pixelColor(x, y)
            for x in range(round(10 * scale), round(50 * scale))
            for y in range(round(4 * scale), round(22 * scale))
            if image.pixelColor(x, y).alpha() > 100
            and max(abs(component - expected) for component, expected in zip(
                image.pixelColor(x, y).getRgb()[:3], foreground, strict=True
            )) < 40
        ]
        assert ink
    finally:
        panel.close()
        panel.popup.close()
