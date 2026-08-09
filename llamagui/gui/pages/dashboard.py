"""Dashboard: what is installed, what is active, and is the router up.

The refresh is deliberately cheap — the engine's ``status`` reads files and
does one non-blocking socket probe, never a subprocess — so it can poll.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, cast

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

        self._platform_label = QLabel()
        self._platform_label.setStyleSheet("color: #757575;")
        self._platform_label.setWordWrap(True)
        layout.addWidget(self._platform_label)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._status_line = QLabel()
        self._status_line.setWordWrap(True)
        layout.addWidget(self._status_line)

        card_area = QHBoxLayout()
        for name in self._orch.backend_names():
            card = BackendCard(name)
            self._cards[name] = card
            card_area.addWidget(card)
        card_area.addStretch()
        layout.addLayout(card_area)

        self._active_label = QLabel()
        layout.addWidget(self._active_label)

        self._router_label = QLabel()
        layout.addWidget(self._router_label)

        self._llama_swap_label = QLabel()
        layout.addWidget(self._llama_swap_label)

        self._server_label = QLabel()
        self._server_label.setWordWrap(True)
        layout.addWidget(self._server_label)

        self._config_label = QLabel()
        layout.addWidget(self._config_label)

        layout.addStretch()

    def start_refresh(self, interval_ms: int = 3000) -> None:
        self._refresh()
        self._refresh_timer.start(interval_ms)

    def stop_refresh(self) -> None:
        self._refresh_timer.stop()

    def _refresh(self) -> None:
        # Guard against worker pile-up: skip a tick while one is in flight.
        if self._busy:
            return
        self._busy = True
        worker = EngineWorker(self._orch, "status")
        worker.signals.finished.connect(self._on_status)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _on_status(self, data: Any) -> None:
        self._busy = False
        raw = data.model_dump() if hasattr(data, "model_dump") else data
        payload: dict[str, Any] = cast(
            "dict[str, Any]", raw if isinstance(raw, dict) else {}
        )
        with suppress(RuntimeError):
            self._render(payload)

    def _render(self, payload: dict[str, Any]) -> None:
        platform: dict[str, Any] = payload.get("platform", {}) or {}
        self._platform_label.setText(
            f"{platform.get('system', '?')}/{platform.get('arch', '?')} — "
            f"root: {payload.get('root', '?')}"
        )

        backends: dict[str, Any] = payload.get("backends", {}) or {}
        for name, info in backends.items():
            card = self._cards.get(name)
            if card is None:
                continue
            version = info.get("version")
            source = info.get("source")
            card.refresh(
                bool(info.get("installed")),
                str(version) if version is not None else None,
                str(source) if source is not None else None,
                unavailable_reason=(
                    ""
                    if info.get("prebuilt_available") or info.get("buildable")
                    else str(info.get("unavailable_reason", ""))
                ),
            )

        active = payload.get("active")
        self._active_label.setText(f"Active backend: {active or 'none selected'}")

        router: dict[str, Any] = payload.get("router", {}) or {}
        listening = bool(router.get("listening"))
        self._router_label.setText(
            f"Router: {router.get('host', '127.0.0.1')}:{router.get('port', 8080)} — "
            f"{'LISTENING' if listening else 'not listening'}"
        )
        self._router_label.setStyleSheet(
            "color: #4CAF50;" if listening else "color: #757575;"
        )

        swap: dict[str, Any] = payload.get("llama_swap", {}) or {}
        swap_version = swap.get("version")
        swap_source = swap.get("source")
        self._llama_swap_label.setText(
            "llama-swap: "
            + (
                f"{swap_version} ({swap_source})"
                if swap.get("installed")
                else "not installed"
            )
        )

        resolved: dict[str, Any] = cast(
            "dict[str, Any]",
            ((payload.get("resolved") or {}) or {}).get("llama_server") or {},
        )
        self._server_label.setText(
            f"llama-server: {resolved.get('path') or 'not resolved'}"
            + (f" [{resolved['source']}]" if resolved.get("source") else "")
        )

        self._config_label.setText(
            f"Model config: {'present' if payload.get('config_present') else 'missing'}"
        )

    def _on_error(self, msg: str) -> None:
        self._busy = False
        with suppress(RuntimeError):
            self._status_line.setText(f"Error: {msg}")
