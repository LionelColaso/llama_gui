"""Server options page — the global server configuration for every model.

The page is a searchable editor for every ``llama-server`` option (248 flags as
of llama-server b10488), generated from the :mod:`app.serverargs` catalogue,
with a live command-line preview of exactly what ``launch`` will run.

**One scope.** *Global defaults* is what every model starts from: the values a
model runs with unless it has its own configuration (the *Server options…*
popup on the Models tab is where that is decided). A model scope is still
selectable here for inspecting and editing one model's own configuration
directly, which is handy when a model's settings are already its own.

Four rows are *dedicated* (``--host``, ``--port``, ``--ctx-size``,
``--n-gpu-layers``): they bind to the long-standing ``AppConfig`` fields the
rest of the engine (port probe, stop, dashboard) already uses. ``--host`` and
``--port`` — plus the raw *extra args* row — stay global: the app probes exactly
one host:port for health, status and stop, so one model cannot move it out from
under the rest.
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...lifecycle import uses_global_server_config
from ...serverargs import SERVER_ARGS, validate_options
from ..payload import as_payload
from ..theme import COLORS
from ..widgets.server_options_editor import ServerOptionsEditor
from ..worker_pool import EngineWorker, WorkerPool

#: Label of the scope that edits the global defaults every model starts from.
GLOBAL_SCOPE_LABEL = "Global defaults"


def _model_names(data: Any) -> set[str]:
    """Model names out of a ``list_models`` payload, ignoring an odd shape.

    The scope selector is a convenience: a payload it cannot read costs the
    user the dropdown entries, never the page.
    """
    raw: Any = as_payload(data).get("models")
    rows: list[dict[str, Any]] = (
        cast("list[dict[str, Any]]", raw) if isinstance(raw, list) else []
    )
    return {row["name"] for row in rows if row.get("name")}


class ServerArgsPage(QWidget):
    """Sidebar page: the global server options, plus a per-model scope."""

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        #: Model names offered by the scope selector (library + own configs).
        self._scope_models: list[str] = []

        layout = QVBoxLayout(self)

        title = QLabel("Server options")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self._intro = QLabel()
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(self._intro)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Scope"))
        self._scope_combo = QComboBox()
        self._scope_combo.setToolTip(
            "Global defaults are what every model starts from; picking a model "
            "edits only that model's own configuration."
        )
        self._scope_combo.currentTextChanged.connect(self._on_scope_changed)
        toolbar.addWidget(self._scope_combo, stretch=1)
        reset_btn = QPushButton("Reset all")
        reset_btn.setToolTip(
            "Clear every value in this scope back to the option's default"
        )
        reset_btn.clicked.connect(self._reset_all)
        toolbar.addWidget(reset_btn)
        layout.addLayout(toolbar)

        self._editor = ServerOptionsEditor(orch)
        layout.addWidget(self._editor, stretch=1)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save server options")
        save_btn.setToolTip("Persist the values; the next launch uses them")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        btn_row.addWidget(self._status_label, stretch=1)
        layout.addLayout(btn_row)

        self.set_scope(None)
        self._load_models()

    # ─── Scope ──────────────────────────────────────────────────────────

    def set_scope(self, model: str | None) -> None:
        """Edit the global defaults (``None``) or one model's own configuration.

        Also used by the *Server options…* popup, which offers the model in the
        selector even when the library list has not arrived yet.
        """
        if model and model not in self._scope_models:
            self._scope_models.append(model)
        self._apply_scope(model)

    @property
    def _scope(self) -> str | None:
        return self._editor.scope()

    def _apply_scope(self, model: str | None) -> None:
        self._editor.set_scope(model)
        self._scope_combo.blockSignals(True)
        self._scope_combo.clear()
        self._scope_combo.addItems([GLOBAL_SCOPE_LABEL, *self._scope_models])
        self._scope_combo.setCurrentText(model or GLOBAL_SCOPE_LABEL)
        self._scope_combo.blockSignals(False)
        self._intro.setText(self._scope_intro())

    def _scope_intro(self) -> str:
        if self._scope is None:
            return (
                f"Every llama-server command-line option ({len(SERVER_ARGS)} flags, "
                "generated from the catalogue). These are the global defaults "
                "every model starts from; empty values are omitted so the "
                "binary's own default wins. The preview below is the exact "
                "command 'Launch' runs."
            )
        mode = (
            "it follows the global defaults"
            if uses_global_server_config(self._orch.cfg, self._scope)
            else "it runs on the values below"
        )
        return (
            f"Server options for {self._scope}: {mode}. Save writes this "
            "model's own configuration; the globals stay untouched. Empty "
            "values are omitted so the binary's own default wins."
        )

    def _on_scope_changed(self, label: str) -> None:
        self._apply_scope(None if label == GLOBAL_SCOPE_LABEL else label)

    def _load_models(self) -> None:
        """Fill the scope selector with the library plus any own configurations."""
        # Known before the worker returns, so a model is never missing from the
        # selector while the (async) list is still in flight.
        known = set(self._scope_models) | set(self._orch.cfg.model_server_options)
        known.discard("")
        self._scope_models = sorted(known)
        self._apply_scope(self._scope)
        worker = EngineWorker(self._orch, "list_models")
        worker.signals.finished.connect(self._on_models)
        worker.signals.error.connect(self._on_error)
        WorkerPool.instance().start(worker)

    def _on_models(self, data: Any) -> None:
        names = _model_names(data) | set(self._orch.cfg.model_server_options)
        self._scope_models = sorted(names)
        self._apply_scope(self._scope)

    def _on_error(self, msg: str) -> None:
        self._status_label.setText(f"Error: {msg}")

    # ─── Save / reset ───────────────────────────────────────────────────

    def _save(self) -> None:
        scope = self._scope
        try:
            if scope is None:
                data = self._editor.collect_global()
                options = {k: str(v) for k, v in data["server_options"].items()}
            else:
                data = self._editor.collect_model()
                options = dict(data)
        except ValueError as exc:
            self._status_label.setText(str(exc))
            return
        errors = validate_options(options)
        if errors:
            self._status_label.setText(next(iter(errors.values())))
            return
        if scope is None:
            self._orch.save_config(data)
            self._status_label.setText("Server options saved.")
        else:
            self._orch.save_model_server_config(scope, cast("dict[str, str]", data))
            count = len(options)
            self._status_label.setText(
                f"Saved for {scope}: {count} option"
                f"{'' if count == 1 else 's'} of its own."
            )
        self._apply_scope(scope)

    def _reset_all(self) -> None:
        if self._scope is not None:
            # The model's own configuration becomes empty: every option at the
            # binary's default. It does *not* start following the globals, which
            # is what the popup's checkbox is for.
            self._orch.reset_model_server_config(self._scope)
            self._status_label.setText(
                f"{self._scope} now uses its own default values."
            )
            self._apply_scope(self._scope)
            return
        self._editor.clear_to_defaults()
        self._status_label.setText("All values cleared — save to apply.")
