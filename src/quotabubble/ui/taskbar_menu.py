"""Compact menus shared by the taskbar panel and its tray icon."""

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QMenu, QStyleFactory, QWidget


class TaskbarMenu(QMenu):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Windows 11's native menu style reserves a large icon/check column.
        # Use Fusion for these small menus without changing the application style.
        self._compact_style = QStyleFactory.create("Fusion")
        self.setStyle(self._compact_style)
        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        background, foreground, border, hover = (
            ("#20242d", "#e8edf5", "#424b5c", "#3b4555") if dark
            else ("#f4f5f8", "#252b35", "#c5cbd4", "#dce5f1")
        )
        self.setStyleSheet(f"""
            QMenu {{ background: {background}; color: {foreground};
                     border: 1px solid {border}; padding: 3px; }}
            QMenu::item {{ padding: 4px 8px; }}
            QMenu::item:selected {{ background: {hover}; }}
            QMenu::item:disabled {{ color: #88919e; }}
            QMenu::indicator {{ width: 12px; height: 12px; }}
            QMenu::separator {{ height: 1px; background: {border}; margin: 3px 4px; }}
        """)
