from __future__ import annotations

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, Signal, Slot

from quotabubble.app.cache import load_snapshots, save_snapshots
from quotabubble.app.runtime import (  # noqa: F401
    ERROR_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    PollingRuntime,
)
from quotabubble.providers.base import Provider, UsageSnapshot

DEFAULT_REFRESH_INTERVAL_MS = 300_000


class PollingWorker(QObject):
    snapshot_ready = Signal(object)

    def __init__(
        self,
        providers: list[Provider],
        interval_ms: int,
        parent: QObject | None = None,
        *,
        runtime: PollingRuntime | None = None,
    ) -> None:
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._timer: QTimer | None = None
        self._runtime = runtime or PollingRuntime(
            providers,
            last_good=load_snapshots(),
            save_last_good=save_snapshots,
        )
        # Preserve these attributes for callers that inspect the worker's
        # retry state while the implementation lives in PollingRuntime.
        self._failures = self._runtime._failures
        self._retry_at = self._runtime._retry_at

    @Slot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        self.poll()

    @Slot()
    @Slot(bool)
    def poll(self, force: bool = False) -> None:
        for snapshot in self._runtime.poll(force=force):
            self.snapshot_ready.emit(snapshot)

    @Slot(object)
    def set_providers(self, providers: list[Provider]) -> None:
        if self._runtime.set_providers(providers):
            self.poll()

    @Slot(int)
    def set_interval(self, interval_ms: int) -> None:
        self._interval_ms = interval_ms
        if self._timer is not None:
            self._timer.setInterval(interval_ms)

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.setParent(None)
            self._timer = None


class PollingService(QObject):
    snapshot_ready = Signal(object)
    interval_changed = Signal(int)
    providers_changed = Signal(object)
    poll_requested = Signal(bool)

    def __init__(
        self,
        providers: list[Provider],
        interval_ms: int = DEFAULT_REFRESH_INTERVAL_MS,
        parent: QObject | None = None,
        *,
        runtime: PollingRuntime | None = None,
    ) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._worker = PollingWorker(providers, interval_ms, runtime=runtime)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.snapshot_ready.connect(self._relay)
        self.interval_changed.connect(self._worker.set_interval)
        self.providers_changed.connect(self._worker.set_providers)
        self.poll_requested.connect(self._worker.poll)

    def start(self) -> None:
        self._thread.start()

    def set_interval(self, interval_ms: int) -> None:
        self.interval_changed.emit(interval_ms)

    def set_providers(self, providers: list[Provider]) -> None:
        self.providers_changed.emit(list(providers))

    def poll(self, force: bool = True) -> None:
        self.poll_requested.emit(force)

    @Slot(object)
    def _relay(self, snapshot: UsageSnapshot) -> None:
        self.snapshot_ready.emit(snapshot)

    def stop(self) -> None:
        if not self._thread.isRunning():
            return
        QMetaObject.invokeMethod(
            self._worker, "stop", Qt.ConnectionType.BlockingQueuedConnection
        )
        self._thread.quit()
        self._thread.wait()
