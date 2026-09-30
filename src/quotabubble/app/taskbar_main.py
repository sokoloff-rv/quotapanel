"""QuotaPanel: local Windows taskbar overlay for Codex and Claude only."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from quotabubble.app.cache import load_snapshots, save_snapshots
from quotabubble.app.instance import SingleInstance
from quotabubble.app.logging_setup import setup_logging
from quotabubble.app.polling import PollingService
from quotabubble.app.runtime import PollingRuntime
from quotabubble.app.state import AppState
from quotabubble.app.taskbar_controller import PanelSettingsController
from quotabubble.app.taskbar_model import PROVIDER_NAMES
from quotabubble.app.taskbar_settings import CACHE_PATH, CONFIG_DIR, PanelSettings
from quotabubble.providers.base import ProviderStatus, UsageSnapshot, UsageWindow
from quotabubble.providers.claude import ClaudeProvider
from quotabubble.providers.codex import CodexProvider
from quotabubble.ui.icon import app_icon
from quotabubble.ui.taskbar_menu import TaskbarMenu
from quotabubble.ui.taskbar_panel import PanelSettingsDialog, TaskbarPanel


def demo_snapshots() -> list[UsageSnapshot]:
    now = datetime.now(UTC)
    return [
        UsageSnapshot(
            provider=provider,
            display_name=name,
            fetched_at=now,
            windows=[
                UsageWindow(
                    key="session",
                    label="5 часов",
                    used_pct=used,
                    resets_at=now + timedelta(hours=2, minutes=14),
                ),
                UsageWindow(
                    key="weekly",
                    label="Неделя",
                    used_pct=weekly,
                    resets_at=now + timedelta(days=3, hours=7),
                ),
            ],
        )
        for provider, name, used, weekly in [
            ("codex", "Codex", 42, 68),
            ("claude", "Claude", 23, 81),
        ]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Квоты Codex и Claude слева на панели задач")
    parser.add_argument("--demo", action="store_true", help="Пример без доступа к аккаунтам")
    parser.add_argument("--render-preview", type=Path, help="Сохранить изображение примера и выйти")
    parser.add_argument(
        "--probe-taskbar", action="store_true", help="Проверить расположение панели"
    )
    parser.add_argument("--quit-after", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("QuotaPanel")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    if args.probe_taskbar:
        from dataclasses import asdict

        from quotabubble.platform.taskbar import taskbar_info

        info = taskbar_info()
        print(
            json.dumps(
                {
                    "taskbar": asdict(info) if info else None,
                    "screens": [
                        {
                            "name": s.name(),
                            "scale": s.devicePixelRatio(),
                            "rect": [
                                s.geometry().x(),
                                s.geometry().y(),
                                s.geometry().width(),
                                s.geometry().height(),
                            ],
                        }
                        for s in QGuiApplication.screens()
                    ],
                },
                ensure_ascii=False,
            )
        )
        return
    preview = args.render_preview is not None
    demo = args.demo or preview
    settings = PanelSettings() if demo else PanelSettings.load()
    state = AppState()
    snapshots = demo_snapshots() if demo else []
    if not demo:
        setup_logging(path=CONFIG_DIR / "Logs" / "quotapanel.log")
        cached = load_snapshots(CACHE_PATH)
        for provider, name in PROVIDER_NAMES.items():
            snapshot = cached.get(provider)
            snapshots.append(
                snapshot.model_copy(update={"stale": True})
                if snapshot
                else UsageSnapshot(
                    provider=provider, display_name=name, status=ProviderStatus.LOADING
                )
            )
    state.replace(snapshots)
    panel = TaskbarPanel(state, settings, preview=preview)
    if preview:
        args.render_preview.mkdir(parents=True, exist_ok=True)
        panel.show()
        app.processEvents()
        panel.grab().save(str(args.render_preview / "panel-dark.png"))
        panel._light = True
        panel.update()
        app.processEvents()
        panel.grab().save(str(args.render_preview / "panel-light.png"))
        panel._transparent = True
        panel.update()
        app.processEvents()
        panel.grab().save(str(args.render_preview / "panel-transparent.png"))
        panel.popup.rebuild()
        panel.popup.show()
        app.processEvents()
        panel.popup.grab().save(str(args.render_preview / "details.png"))
        dialog = PanelSettingsDialog(settings)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(args.render_preview / "settings.png"))
        return
    instance = SingleInstance(panel.show_details, name="quotapanel-demo" if demo else "quotapanel")
    if not instance.acquire():
        return
    app.aboutToQuit.connect(instance.close)
    tray = QSystemTrayIcon(app_icon())
    tray.setToolTip("QuotaPanel · Codex и Claude" + (" · пример" if demo else ""))
    menu = TaskbarMenu()
    menu.addAction("Показать квоты", panel.show_details)
    menu.addAction("Показать / скрыть панель", panel.toggle_visible)
    controller = PanelSettingsController(panel, settings)
    panel.settings_requested.connect(controller.configure)
    menu.addAction("Настройки…", controller.configure)
    autostart = QAction("Запускать вместе с Windows", menu)
    autostart.setCheckable(True)
    autostart.setChecked(settings.autostart)
    autostart.setEnabled(not demo)

    def toggle_autostart(checked: bool) -> None:
        from quotabubble.platform.taskbar import set_autostart

        try:
            set_autostart(checked)
        except OSError:
            autostart.blockSignals(True)
            autostart.setChecked(not checked)
            autostart.blockSignals(False)
            QMessageBox.warning(None, "QuotaPanel", "Не удалось изменить автозапуск Windows.")
            return
        settings.autostart = checked
        settings.save()

    autostart.toggled.connect(toggle_autostart)
    menu.addAction(autostart)
    service = None
    if not demo:
        providers = [CodexProvider(), ClaudeProvider()]
        runtime = PollingRuntime(
            providers,
            last_good=load_snapshots(CACHE_PATH),
            save_last_good=lambda data: save_snapshots(data, CACHE_PATH),
        )
        service = PollingService(providers, settings.refresh_interval_ms, runtime=runtime)
        controller.polling = service
        service.snapshot_ready.connect(panel.apply_snapshot)
        panel.refresh_requested.connect(lambda: service.poll(force=True))
        menu.addAction("Обновить сейчас", lambda: service.poll(force=True))
        app.aboutToQuit.connect(service.stop)
        service.start()
    else:
        menu.addAction("Режим примера · данные вымышлены").setEnabled(False)
    menu.addSeparator()
    menu.addAction("Выход", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            panel.show_details() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
    )
    tray.show()
    panel.sync_placement()
    if panel.note and not panel.isVisible():
        tray.showMessage("QuotaPanel", panel.note, QSystemTrayIcon.MessageIcon.Information)
    if args.quit_after:
        QTimer.singleShot(args.quit_after * 1000, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
