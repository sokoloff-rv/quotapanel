"""Pure presentation and geometry for the Windows taskbar panel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

from quotabubble.providers.base import ProviderStatus, UsageSnapshot, UsageWindow

PROVIDER_NAMES = {"codex": "Codex", "claude": "Claude"}
STATUS_TEXT = {
    ProviderStatus.LOADING: "Обновление…",
    ProviderStatus.NO_CREDENTIALS: "Войдите в аккаунт",
    ProviderStatus.EXPIRED: "Войдите заново",
    ProviderStatus.ERROR: "Ошибка сети",
}


def reset_countdown(resets_at: datetime | None, now: datetime | None = None) -> str:
    """Minute-precision countdown; never claim a quota has reset locally."""
    if resets_at is None:
        return "—"
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    seconds = int((resets_at - now).total_seconds())
    if seconds <= 0:
        return "сейчас"
    minutes = (seconds + 59) // 60
    days, remainder = divmod(minutes, 1440)
    hours, mins = divmod(remainder, 60)
    if days:
        return f"{days}д{hours}ч" if hours else f"{days}д"
    if hours:
        return f"{hours}ч{mins:02d}м"
    return f"{mins}м"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def dock_rect(bar: Rect, screen: Rect, width: int, offset: int) -> Rect | None:
    """Only dock to a visible horizontal taskbar at the bottom of its screen."""
    if bar.height < 24 or bar.width <= bar.height:
        return None
    if bar.y < screen.y + screen.height - bar.height - 8:
        return None
    visible_height = min(bar.y + bar.height, screen.y + screen.height) - max(bar.y, screen.y)
    if visible_height < bar.height / 2:
        return None
    width = min(max(220, width), bar.width - 24)
    if width < 220:
        return None
    offset = min(max(8, offset), bar.width - width - 8)
    height = min(44, bar.height - 4)
    return Rect(bar.x + offset, bar.y + (bar.height - height) // 2, width, height)


def primary_windows(snapshot: UsageSnapshot) -> list[UsageWindow | None]:
    """Choose overall limits, never a model-specific weekly bucket by accident."""
    result = []
    for key in ("session", "weekly"):
        candidates = [w for w in snapshot.windows if w.key == key]
        result.append(
            next((w for w in candidates if w.scope is None), None) or next(iter(candidates), None)
        )
    return result


def metric(window: UsageWindow | None, remaining: bool) -> tuple[str, str]:
    if window is None or not isfinite(window.used_pct):
        return "—", "muted"
    used = max(0.0, min(100.0, window.used_pct))
    value = 100 - used if remaining else used
    tone = "critical" if used >= 90 else "warning" if used >= 75 else "ok"
    return f"{round(value)}%", tone


def row_values(snapshot: UsageSnapshot, remaining: bool) -> tuple[str, list[tuple[str, str]]]:
    if snapshot.status is not ProviderStatus.OK:
        return STATUS_TEXT.get(snapshot.status, "Нет данных"), []
    return "", [metric(window, remaining) for window in primary_windows(snapshot)]
