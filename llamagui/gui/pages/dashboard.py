from __future__ import annotations

from contextlib import suppress
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..widgets.backend_card import BackendCard
from ..widgets.progress_bar import ProgressWidget
from ..worker_pool import EngineWorker, WorkerPool


class DashboardPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._cards: dict[str, BackendCard] = {}
        self._busy = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)

        layout = QVBoxLayout(self)

        title = QLabel("Dashboard")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._status_line = QLabel()
        layout.addWidget(self._status_line)

        card_area = QHBoxLayout()
        for name in self._orch.backend_names():
            card = BackendCard(name)
            self._cards[name] = card
            card_area.addWidget(card)
        layout.addLayout(card_area)

        self._router_label = QLabel()
        layout.addWidget(self._router_label)

        self._llama_swap_label = QLabel()
        layout.addWidget(self._llama_swap_label)

        self._config_label = QLabel()
        layout.addWidget(self._config_label)

        layout.addStretch()

    def start_refresh(self, interval_ms: int = 3000) -> None:
        self._refresh()
        self._refresh_timer.start(interval_ms)

    def stop_refresh(self) -> None:
        self._refresh_timer.stop()

    def _refresh(self) -> None:
        # Guard against worker pile-up: if a previous status() is still running
        # (e.g. slow resolver validation), skip this tick rather than stacking
        # another worker on the thread pool.
        if self._busy:
            return
        self._busy = True
        worker = EngineWorker(self._orch, "status")
        worker.signals.finished.connect(self._on_status)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _on_status(self, data: Any) -> None:
        self._busy = False
        try:
            d = data.model_dump() if hasattr(data, "model_dump") else data
            backends = d.get("backends", {})
            for name, info in backends.items():
                if name in self._cards:
                    self._cards[name].refresh(
                        info.get("installed", False),
                        info.get("version"),
                        info.get("source"),
                    )

            router = d.get("router", {})
            listening = router.get("listening", False)
            self._router_label.setText(
                f"Router: port {router.get('port', 8080)} — "
                f"{'LISTENING' if listening else 'not listening'}"
            )
            self._router_label.setStyleSheet(
                "color: #4CAF50;" if listening else "color: #757575;"
            )

            ls = d.get("llama_swap", {})
            self._llama_swap_label.setText(
                f"llama-swap: {'installed' if ls.get('installed') else 'not installed'}"
            )
            self._config_label.setText(
                f"Config: {'present' if d.get('config_present') else 'missing'}"
            )
        except RuntimeError:
            pass

    def _on_error(self, msg: str) -> None:
        self._busy = False
        with suppress(RuntimeError):
            self._status_line.setText(f"Error: {msg}")
