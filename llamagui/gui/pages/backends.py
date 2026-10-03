"""Backends: installed backends, the active one, and which llama-server runs —
with their actions (install / update / use / download / launch / stop) inline.

This fuses the former Dashboard "overview", the Resolver, and the backend
Actions into one section: each backend card shows install state plus its own
Install / Update / Use buttons, a single global row drives server launch/stop
and "download all missing", and the resolved binary is shown once. Refresh is
cheap — ``status`` reads files + one socket probe and ``resolve`` is a quick
``--version`` — so it can poll.
"""

from __future__ import annotations

import functools
from contextlib import suppress
from typing import Any, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...download import DownloadControl
from ..payload import as_payload
from ..theme import COLORS
from ..widgets.backend_card import BackendCard
from ..widgets.progress_bar import ProgressWidget
from ..widgets.source_badge import SourceBadge
from ..worker_pool import EngineWorker, WorkerPool


class BinaryRow(QGroupBox):
    """One binary: resolved state and where it came from."""

    def __init__(self, title: str) -> None:
        super().__init__(title)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.badge = SourceBadge(None)
        header.addWidget(self.badge)
        self.state_label = QLabel("resolving…")
        self.state_label.setWordWrap(True)
        header.addWidget(self.state_label, stretch=1)
        layout.addLayout(header)

        self.path_label = QLabel("")
        self.path_label.setStyleSheet(f"color: {COLORS['muted']};")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

    def update_state(self, info: dict[str, Any]) -> None:
        source = info.get("source")
        self.badge.update_source(source or "unknown")
        path = info.get("path")
        if not path:
            self.state_label.setText(
                "not found — download one or enable the OS install"
            )
            self.state_label.setStyleSheet(f"color: {COLORS['danger']};")
            self.path_label.setText("")
            return
        if info.get("valid"):
            self.state_label.setText(f"OK — {info.get('version') or 'version unknown'}")
            self.state_label.setStyleSheet(f"color: {COLORS['success']};")
        else:
            self.state_label.setText(
                f"cannot run: {info.get('error') or 'unknown error'}"
            )
            self.state_label.setStyleSheet(f"color: {COLORS['danger']};")
        self.path_label.setText(str(path))


class BackendsPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._cards: dict[str, BackendCard] = {}
        self._busy = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)

        layout = QVBoxLayout(self)

        title = QLabel("Backends")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self._platform_label = QLabel()
        self._platform_label.setStyleSheet(f"color: {COLORS['muted']};")
        self._platform_label.setWordWrap(True)
        layout.addWidget(self._platform_label)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        # ── Installed backends (state + per-card actions) ───────────────────
        self._action_buttons: list[QPushButton] = []
        card_area = QHBoxLayout()
        for name in self._orch.backend_names():
            card = BackendCard(
                name,
                on_install=functools.partial(self._do_install, name),
                on_update=functools.partial(self._do_update, name),
                on_use=functools.partial(self._do_use, name),
            )
            self._cards[name] = card
            self._action_buttons.extend(
                (card._install_btn, card._update_btn, card._use_btn)
            )
            card_area.addWidget(card)
        card_area.addStretch()
        layout.addLayout(card_area)

        # ── Global actions ───────────────────────────────────────────────────
        global_row = QHBoxLayout()
        download_all_btn = QPushButton("Download all missing")
        download_all_btn.clicked.connect(self._do_download_all)
        global_row.addWidget(download_all_btn)
        self._action_buttons.append(download_all_btn)
        for label, handler, ghost in (
            ("Launch", self._do_launch, False),
            ("Restart", self._do_restart, True),
            ("Stop", self._do_stop, True),
        ):
            btn = QPushButton(label)
            if ghost:
                btn.setObjectName("GhostButton")
            btn.clicked.connect(handler)
            global_row.addWidget(btn)
            self._action_buttons.append(btn)
        global_row.addStretch()
        layout.addLayout(global_row)

        # ── Status summary ───────────────────────────────────────────────────
        self._active_label = QLabel()
        layout.addWidget(self._active_label)

        self._listen_label = QLabel()
        self._listen_label.setWordWrap(True)
        layout.addWidget(self._listen_label)

        self._model_label = QLabel()
        layout.addWidget(self._model_label)

        self._config_label = QLabel()
        layout.addWidget(self._config_label)

        # ── Resolved binary (which llama-server would actually run) ─────────
        if getattr(self._orch.cfg, "use_os_llama_server", False):
            hint = QLabel(
                "Source: 'Use OS installed llama.cpp' is on — a llama-server "
                "found on PATH is preferred, with the downloaded backend "
                f"({self._orch.cfg.managed_dir}) as fallback."
            )
        else:
            hint = QLabel(
                f"Source: the backend location ({self._orch.cfg.managed_dir}). "
                "Enable 'Use OS installed llama.cpp' in Settings to prefer a "
                "PATH install."
            )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(hint)

        self._server_row = BinaryRow("llama-server (llama.cpp)")
        layout.addWidget(self._server_row)

        resolve_row = QHBoxLayout()
        recheck_btn = QPushButton("Re-check")
        recheck_btn.clicked.connect(self._resolve)
        resolve_row.addWidget(recheck_btn)
        resolve_row.addStretch()
        layout.addLayout(resolve_row)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addStretch()

    # ─── Refresh ───────────────────────────────────────────────────────────

    def start_refresh(self, interval_ms: int = 3000) -> None:
        self._refresh()
        self._resolve()
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
        worker.signals.error.connect(self._on_status_error)
        WorkerPool.instance().start(worker)

    def _resolve(self) -> None:
        worker = EngineWorker(self._orch, "resolve")
        worker.signals.finished.connect(self._on_resolved)
        worker.signals.error.connect(self._on_status_error)
        WorkerPool.instance().start(worker)

    def _set_actions_enabled(self, enabled: bool) -> None:
        for btn in self._action_buttons:
            btn.setEnabled(enabled)

    def _start(
        self,
        action: str,
        label: str,
        *,
        on_done: Any = None,
        progress_callback: Any = None,
        control: Any = None,
        **kwargs: Any,
    ) -> None:
        self._set_actions_enabled(False)
        self._progress.start_operation(label, control=control)
        worker = EngineWorker(
            self._orch,
            action,
            progress_callback=progress_callback,
            control=control,
            **kwargs,
        )
        worker.signals.finished.connect(
            functools.partial(self._on_action_done, on_done=on_done)
        )
        worker.signals.error.connect(self._on_action_error)
        WorkerPool.instance().start(worker)

    def _on_action_done(self, data: Any, on_done: Any = None) -> None:
        self._set_actions_enabled(True)
        self._progress.finish_operation()
        if on_done is not None:
            on_done()

    def _on_action_error(self, msg: str) -> None:
        self._set_actions_enabled(True)
        with suppress(RuntimeError):
            self._progress.fail_operation(msg)
            self._status_label.setText(f"Error: {msg}")

    def _after_action(self) -> None:
        # Re-read state so cards, the active backend and the resolved binary
        # all reflect what the action just changed.
        self._refresh()
        self._resolve()

    # ─── Actions ────────────────────────────────────────────────────────────

    def _do_install(self, name: str) -> None:
        control = DownloadControl()
        self._start(
            "install",
            f"Installing {name}…",
            on_done=self._after_action,
            progress_callback=self._on_progress,
            control=control,
            backends=[name],
        )

    def _do_update(self, name: str) -> None:
        control = DownloadControl()
        self._start(
            "update",
            f"Updating {name}…",
            on_done=self._after_action,
            progress_callback=self._on_progress,
            control=control,
            backends=[name],
        )

    def _do_use(self, name: str) -> None:
        control = DownloadControl()
        self._start(
            "use",
            f"Switching to {name}…",
            on_done=self._after_action,
            progress_callback=self._on_progress,
            control=control,
            backend=name,
            auto_install=True,
        )

    def _do_download_all(self) -> None:
        control = DownloadControl()
        self._start(
            "bootstrap",
            "Downloading what is missing…",
            on_done=self._after_action,
            progress_callback=self._on_progress,
            control=control,
        )

    def _do_launch(self) -> None:
        self._start("launch", "Launching…", on_done=self._after_action)

    def _do_restart(self) -> None:
        self._start("restart", "Restarting…", on_done=self._after_action)

    def _do_stop(self) -> None:
        self._start("stop", "Stopping…", on_done=self._after_action)

    # ─── Render ────────────────────────────────────────────────────────────

    def _on_status(self, data: Any) -> None:
        self._busy = False
        payload = as_payload(data)
        with suppress(RuntimeError):
            self._render(payload)

    def _render(self, payload: dict[str, Any]) -> None:
        platform: dict[str, Any] = payload.get("platform", {}) or {}
        self._platform_label.setText(
            f"{platform.get('system', '?')}/{platform.get('arch', '?')} — "
            f"root: {payload.get('root', '?')}"
        )

        active = payload.get("active")
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
                    if info.get("prebuilt_available")
                    else str(info.get("unavailable_reason", ""))
                ),
            )
            card.set_active(name == active)

        self._active_label.setText(f"Active backend: {active or 'none selected'}")

        server: dict[str, Any] = payload.get("server", {}) or {}
        listening = bool(server.get("listening"))
        pids: list[int] = list(server.get("pids") or [])
        self._listen_label.setText(
            f"Server: {server.get('host', '127.0.0.1')}:{server.get('port', 8080)} — "
            + (f"LISTENING (pid {pids[0]})" if listening and pids else "not listening")
        )
        self._listen_label.setStyleSheet(
            f"color: {COLORS['success']};"
            if listening
            else f"color: {COLORS['muted']};"
        )

        models_data: dict[str, Any] = payload.get("models", {}) or {}
        models = list(models_data.get("models") or [])
        active_model = models_data.get("active")
        self._config_label.setText(
            f"Models: {len(models)} in {models_data.get('dir', '?')}"
            + (f" (active: {active_model})" if active_model else "")
        )
        if server.get("model"):
            self._model_label.setText(f"Loaded model: {server['model']}")

    def _on_resolved(self, data: Any) -> None:
        payload = as_payload(data)
        server: dict[str, Any] = cast(
            "dict[str, Any]", payload.get("llama_server", {}) or {}
        )
        self._server_row.update_state(server)

    # ─── Signals ───────────────────────────────────────────────────────────

    def _on_progress(
        self, done: int, total: int, phase: str, overall: float | None = None
    ) -> None:
        self._progress.update_progress(done, total, phase, overall)

    def _on_status_error(self, msg: str) -> None:
        self._busy = False
        with suppress(RuntimeError):
            self._status_label.setText(f"Error: {msg}")
