from __future__ import annotations

from quotabubble.app.polling import PollingService
from quotabubble.app.taskbar_settings import PanelSettings
from quotabubble.ui.taskbar_panel import TaskbarPanel, edit_settings


class PanelSettingsController:
    """Apply accepted settings to storage, placement and the running poller."""

    def __init__(self, panel: TaskbarPanel, settings: PanelSettings) -> None:
        self.panel = panel
        self.settings = settings
        self.polling: PollingService | None = None

    def configure(self) -> None:
        self.panel.popup.hide()
        if not edit_settings(self.settings):
            return
        self.settings.save()
        self.panel.apply_settings()
        if self.polling is not None:
            self.polling.set_interval(self.settings.refresh_interval_ms)


class PanelShutdownController:
    """Release the foreign taskbar wrapper before Qt checks open windows."""

    def __init__(self, panel: TaskbarPanel, application) -> None:
        self.panel = panel
        self.application = application

    def quit(self) -> None:
        self.panel.shutdown()
        self.application.quit()
