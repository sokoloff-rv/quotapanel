from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    assert reset_countdown(now + timedelta(hours=2, minutes=14), now) == "2ч14м"
    assert reset_countdown(now + timedelta(days=3, hours=7), now) == "3д7ч"
    assert reset_countdown(now - timedelta(seconds=1), now) == "сейчас"
    assert reset_countdown(None, now) == "—"


def test_upgrade_shows_remaining_without_losing_placement(tmp_path) -> None:
    from quotabubble.app.taskbar_settings import PanelSettings

    path = tmp_path / "settings.json"
    path.write_text('{"remaining": false, "width": 320, "offset": 20}', encoding="utf-8")
    settings = PanelSettings.load(path)
    assert settings.remaining is True
    assert (settings.width, settings.offset) == (320, 20)


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
