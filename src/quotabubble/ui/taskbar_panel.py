from __future__ import annotations

import sys
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from quotabubble.app.state import AppState
from quotabubble.app.taskbar_model import (
    PROVIDER_NAMES,
    STATUS_TEXT,
    Rect,
    dock_rect,
    primary_windows,
    reset_countdown,
    row_values,
)
from quotabubble.app.taskbar_settings import PanelSettings
from quotabubble.presentation.formatting import format_age, format_reset
from quotabubble.providers.base import ProviderStatus, UsageSnapshot

COLORS = {"ok": "#64d5ba", "warning": "#efc164", "critical": "#ff7d88", "muted": "#a4adba"}


def _reset(value: datetime | None) -> str:
    text = format_reset(value)
    if text is None:
        return "—"
    return text.replace("now", "сейчас").replace("d", "д").replace("h", "ч").replace("m", "м")


class DetailsPopup(QWidget):
    refresh_requested = Signal()
    settings_requested = Signal()

    def __init__(self, state: AppState, settings: PanelSettings) -> None:
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._state = state
        self._settings = settings
        self.setWindowTitle("Квоты Codex и Claude")
        self.setFixedWidth(390)
        self.setStyleSheet("""
            QWidget { background: #20242d; color: #e8edf5; font: 10pt 'Segoe UI'; }
            QLabel { background: transparent; }
            QPushButton { background: #303745; border: 1px solid #424b5c;
                          border-radius: 6px; padding: 7px 12px; }
            QPushButton:hover { background: #3b4555; }
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 14, 18, 14)
        self._layout.setSpacing(9)
        self._labels: list[QLabel] = []
        refresh = QPushButton("Обновить квоты")
        refresh.clicked.connect(self.refresh_requested.emit)
        self._layout.addWidget(refresh)
        setup = QPushButton("Настройки")
        setup.clicked.connect(self.settings_requested.emit)
        self._layout.addWidget(setup)

    def rebuild(self, note: str = "") -> None:
        rows = [
            (
                "Квоты · осталось" if self._settings.remaining else "Квоты · использовано",
                "font-size: 13pt; font-weight: 600;",
                False,
            )
        ]
        for snapshot in self._state.ordered():
            name = PROVIDER_NAMES[snapshot.provider]
            if snapshot.plan:
                name += f" · {snapshot.plan}"
            rows.append((name, "font-weight: 600; margin-top: 6px;", False))
            if snapshot.status is not ProviderStatus.OK:
                message = STATUS_TEXT.get(snapshot.status, "Нет данных")
                if snapshot.status in {ProviderStatus.NO_CREDENTIALS, ProviderStatus.EXPIRED}:
                    message += " в Codex" if snapshot.provider == "codex" else " в Claude Code"
                rows.append((message, "", True))
            for window in snapshot.windows:
                label = {"session": "5 часов", "weekly": "Неделя"}.get(window.key, window.label)
                if window.scope:
                    label += f" · {window.scope}"
                value = 100 - window.used_pct if self._settings.remaining else window.used_pct
                text = (
                    f"{label}: {max(0, min(100, round(value)))}%"
                    f" · сброс через {_reset(window.resets_at)}"
                )
                rows.append((text, "", True))
            if snapshot.stale:
                rows.append(("Сохранённые данные · обновить сейчас", "", False))
            elif snapshot.fetched_at:
                age = format_age(snapshot.fetched_at).replace("just now", "только что")
                age = (
                    age.replace("d ago", "д назад")
                    .replace("h ago", "ч назад")
                    .replace("m ago", "м назад")
                )
                rows.append((f"Обновлено: {age}", "", False))
        if note:
            rows.append((note, "color: #efc164;", True))
        for index, (text, style, wrap) in enumerate(rows):
            if index == len(self._labels):
                label = QLabel(self)
                self._labels.append(label)
                self._layout.insertWidget(index, label)
            label = self._labels[index]
            label.setText(text)
            if label.styleSheet() != style:
                label.setStyleSheet(style)
            label.setWordWrap(wrap)
            label.show()
            label.ensurePolished()
        for label in self._labels[len(rows):]:
            label.hide()
        # Resolve fonts and wrapping before the first show. Reuse widgets on
        # countdown ticks so deferred deletes cannot disturb the layout.
        self.ensurePolished()
        self._layout.invalidate()
        height = (
            self._layout.totalHeightForWidth(self.width())
            if self._layout.hasHeightForWidth()
            else self._layout.sizeHint().height()
        )
        self.setFixedHeight(height)
        self._layout.activate()


class TaskbarPanel(QWidget):
    refresh_requested = Signal()
    settings_requested = Signal()
    placement_changed = Signal(str)

    def __init__(self, state: AppState, settings: PanelSettings, *, preview: bool = False) -> None:
        super().__init__()
        self._state = state
        self._settings = settings
        self._preview = preview
        self._light = False
        self._note = ""
        self._show_requested = not settings.hidden
        self.setWindowTitle("QuotaPanel")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.resize(settings.width, 44)
        self.popup = DetailsPopup(state, settings)
        self.popup.refresh_requested.connect(self.refresh_requested.emit)
        self.popup.settings_requested.connect(self.settings_requested.emit)
        if sys.platform == "win32" and not preview:
            from quotabubble.platform.windows import configure_window

            configure_window(self)
        self._dock_timer = QTimer(self)
        self._dock_timer.setInterval(1000)
        self._dock_timer.timeout.connect(self.sync_placement)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(15_000)
        self._countdown_timer.timeout.connect(self._tick_countdown)
        if not preview:
            self._dock_timer.start()
            self._countdown_timer.start()

    def _tick_countdown(self) -> None:
        self.update()
        if self.popup.isVisible():
            self.popup.rebuild(self._note)

    @property
    def note(self) -> str:
        return self._note

    def sync_placement(self) -> None:
        if self._preview:
            return
        from quotabubble.platform.taskbar import (
            keep_above_taskbar,
            taskbar_info,
            taskbar_is_light,
        )

        info = taskbar_info()
        target = None
        note = ""
        if info:
            self._light = taskbar_is_light()
            screen = next(
                (s for s in QGuiApplication.screens() if s.name() == info.device),
                QGuiApplication.primaryScreen(),
            )
            g = screen.geometry()
            scale = screen.devicePixelRatio()
            bar = Rect(
                g.x() + round((info.bar.x - info.monitor.x) / scale),
                g.y() + round((info.bar.y - info.monitor.y) / scale),
                round(info.bar.width / scale),
                round(info.bar.height / scale),
            )
            target = dock_rect(
                bar,
                Rect(g.x(), g.y(), g.width(), g.height()),
                self._settings.width,
                self._settings.offset,
            )
            if info.left_aligned:
                target = None
                note = (
                    "Панель скрыта: кнопки Windows выровнены слева. "
                    "Выберите выравнивание по центру в настройках панели задач."
                )
            elif info.autohide:
                # Overlaying an auto-hide taskbar can keep it open forever. Place
                # the panel above its reserved edge instead, only while it is visible.
                if target:
                    target = Rect(target.x, target.y - bar.height, target.width, target.height)
                note = "При автоскрытии квоты появляются над открытой панелью задач."
            if not info.visible or info.fullscreen:
                target = None
        else:
            note = "Ожидаю панель задач Windows."
        if not target and not note:
            note = (
                "Панель временно скрыта: автоскрытие, полный экран "
                "или неподдерживаемое положение панели задач."
            )
        if note != self._note:
            self._note = note
            self.placement_changed.emit(note)
        if target and self._show_requested:
            self.setGeometry(target.x, target.y, target.width, target.height)
            if not self.isVisible():
                self.show()
            keep_above_taskbar(int(self.winId()))
            self.update()
        else:
            self.hide()

    def toggle_visible(self) -> None:
        self._show_requested = not self._show_requested
        self._settings.hidden = not self._show_requested
        self._settings.save()
        self.sync_placement()

    def apply_snapshot(self, snapshot: UsageSnapshot) -> None:
        self._state.update(snapshot)
        self.update()
        if self.popup.isVisible():
            self.popup.rebuild(self._note)

    def show_details(self) -> None:
        self.popup.rebuild(self._note)
        screen = self.screen()
        available = screen.availableGeometry()
        x = max(available.left(), min(self.x(), available.right() - self.popup.width() + 1))
        y = max(
            available.top(),
            min(self.y() - self.popup.height() - 8, available.bottom() - self.popup.height() + 1),
        )
        self.popup.move(x, y)
        self.popup.show()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor("#f4f5f8" if self._light else "#20232b")
        background.setAlpha(242)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()), 7, 7)
        font = QFont("Segoe UI")
        font.setPixelSize(12)
        painter.setFont(font)
        name_width = 49
        column_gap = 8
        column_width = (self.width() - name_width - 22 - column_gap) / 2
        padding = 4
        height = (self.height() - padding * 2) / 2
        foreground = QColor("#252b35" if self._light else "#edf1f7")
        muted = QColor("#667181" if self._light else "#a4adba")
        for index, snapshot in enumerate(self._state.ordered()[:2]):
            top = padding + index * height
            painter.setPen(foreground)
            painter.drawText(
                QRectF(10, top, name_width, height),
                Qt.AlignmentFlag.AlignVCenter,
                PROVIDER_NAMES[snapshot.provider],
            )
            status, values = row_values(snapshot, self._settings.remaining)
            if status:
                painter.setPen(muted)
                painter.drawText(
                    QRectF(10 + name_width, top, self.width() - name_width - 20, height),
                    Qt.AlignmentFlag.AlignVCenter,
                    status,
                )
                continue
            for column, (value, tone) in enumerate(values):
                left = 10 + name_width + column * (column_width + column_gap)
                metrics = QFontMetrics(font)
                label = "5ч:" if column == 0 else "7д:"
                label_width = metrics.horizontalAdvance(label)
                painter.setPen(muted)
                painter.drawText(
                    QRectF(left, top, label_width, height),
                    Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
                color = COLORS[tone]
                if self._light:
                    color = {
                        "ok": "#087d68",
                        "warning": "#926000",
                        "critical": "#ba3047",
                        "muted": "#667181",
                    }[tone]
                painter.setPen(muted if snapshot.stale else QColor(color))
                suffix = "·" if snapshot.stale else ""
                value_left = left + label_width + 4
                value_width = metrics.horizontalAdvance(value + suffix)
                painter.drawText(
                    QRectF(value_left, top, value_width, height),
                    Qt.AlignmentFlag.AlignVCenter,
                    value + suffix,
                )
                window = primary_windows(snapshot)[column]
                countdown = reset_countdown(window.resets_at if window else None)
                reset_left = value_left + value_width + 4
                reset_width = max(0, left + column_width - reset_left)
                painter.setPen(muted)
                reset_text = metrics.elidedText(
                    f"({countdown})", Qt.TextElideMode.ElideRight, round(reset_width)
                )
                painter.drawText(
                    QRectF(reset_left, top, reset_width, height),
                    Qt.AlignmentFlag.AlignVCenter,
                    reset_text,
                )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.show_details()
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            menu.addAction("Обновить", self.refresh_requested.emit)
            menu.addAction("Настройки", self.settings_requested.emit)
            menu.exec(event.globalPosition().toPoint())

    def enterEvent(self, event: object) -> None:
        lines = ["Осталось" if self._settings.remaining else "Использовано"]
        for snapshot in self._state.ordered():
            status, values = row_values(snapshot, self._settings.remaining)
            summary = status or " · ".join(
                f"{label}: {value}, сброс через "
                f"{reset_countdown(window.resets_at if window else None)}"
                for label, (value, _), window in zip(
                    ("5 часов", "неделя"), values, primary_windows(snapshot), strict=True
                )
            )
            lines.append(
                f"{PROVIDER_NAMES[snapshot.provider]}: {summary}"
                + (" (сохранённые данные)" if snapshot.stale else "")
            )
        self.setToolTip("\n".join(lines))


class PanelSettingsDialog(QDialog):
    def __init__(self, settings: PanelSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки QuotaPanel")
        layout = QVBoxLayout(self)
        note = QLabel(
            "Панель не резервирует место для кнопок Windows. "
            "Если они приближаются, уменьшите ширину."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.width_input = QSpinBox()
        self.width_input.setRange(220, 420)
        self.width_input.setValue(settings.width)
        self.offset_input = QSpinBox()
        self.offset_input.setRange(0, 4000)
        self.offset_input.setValue(settings.offset)
        self.interval_input = QSpinBox()
        self.interval_input.setRange(1, 60)
        self.interval_input.setValue(settings.refresh_interval_minutes)
        self.interval_input.setSuffix(" мин")
        form.addRow("Ширина", self.width_input)
        form.addRow("Отступ слева", self.offset_input)
        form.addRow("Обновлять квоты каждые", self.interval_input)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(390, 230)

    def apply(self, settings: PanelSettings) -> None:
        settings.width = self.width_input.value()
        settings.offset = self.offset_input.value()
        settings.refresh_interval_minutes = self.interval_input.value()


def edit_settings(settings: PanelSettings, parent: QWidget | None = None) -> bool:
    dialog = PanelSettingsDialog(settings, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    dialog.apply(settings)
    return True
