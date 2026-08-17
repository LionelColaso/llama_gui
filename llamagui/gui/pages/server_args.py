"""Server options page — a typed editor for every ``llama-server`` option.

The page is data-driven from :mod:`llamagui.serverargs`: one row per catalogue
option (248 flags as of llama-server b10488), each with the right editor for
its kind — on/off combo for booleans, choice combo, text or path with browse.
A live command-line preview shows exactly what ``launch`` will run, and Save
persists the values through :meth:`Orchestrator.save_config` so the engine and
the CLI see the same settings.

Four rows are *dedicated* (``--host``, ``--port``, ``--ctx-size``,
``--n-gpu-layers``): they bind to the long-standing ``AppConfig`` fields the
rest of the engine (port probe, stop, dashboard) already uses.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...lifecycle import build_llama_server_args
from ...resolver import resolve_llama_server
from ...serverargs import (
    DEDICATED_FLAGS,
    SECTIONS,
    SERVER_ARGS,
    ArgKind,
    ServerArg,
    validate_options,
)
from ..theme import COLORS

_EMPTY = "(default)"

#: Pseudo-flag for the raw "extra args" row (not a real llama-server flag).
EXTRA_ARGS_FLAG = "__extra_args__"


class _PathEdit(QWidget):
    """A line edit + Browse button for PATH-kind options."""

    def __init__(
        self,
        is_dir: bool,
        caption: str,
        text_changed: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_dir = is_dir
        self._caption = caption
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText(_EMPTY)
        self._edit.textChanged.connect(text_changed)
        layout.addWidget(self._edit, stretch=1)
        browse = QPushButton("…")
        browse.setFixedWidth(28)
        browse.clicked.connect(self._browse)
        layout.addWidget(browse)

    def value(self) -> str:
        return self._edit.text().strip()

    def setValue(self, value: str) -> None:
        self._edit.setText(value or "")

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self._edit.setEnabled(enabled)

    def _browse(self) -> None:
        if self._is_dir:
            chosen = QFileDialog.getExistingDirectory(self, self._caption)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, self._caption)
        if chosen:
            self._edit.setText(chosen)


class ServerArgsPage(QWidget):
    """Sidebar page: searchable, sectioned editor for all server options."""

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._rows: list[tuple[ServerArg, QWidget]] = []

        layout = QVBoxLayout(self)

        title = QLabel("Server options")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        intro = QLabel(
            f"Every llama-server command-line option ({len(SERVER_ARGS)} flags, "
            "generated from the catalogue). Empty values are omitted so the "
            "binary's own default wins. The preview below is the exact command "
            "'Launch' runs."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(intro)

        toolbar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by flag, alias or help text…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search, stretch=1)

        self._section_combo = QComboBox()
        self._section_combo.addItems(["all", *SECTIONS])
        self._section_combo.currentTextChanged.connect(self._apply_filter)
        toolbar.addWidget(self._section_combo)

        self._count_label = QLabel()
        toolbar.addWidget(self._count_label)

        reset_btn = QPushButton("Reset all")
        reset_btn.setToolTip("Clear every value back to its default")
        reset_btn.clicked.connect(self._reset_all)
        toolbar.addWidget(reset_btn)
        layout.addLayout(toolbar)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Option", "Value", "Default", "Description"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, stretch=1)

        preview_group = QGroupBox("Command line preview")
        preview_layout = QVBoxLayout(preview_group)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(120)
        self._preview.setPlaceholderText("(nothing set yet — defaults apply)")
        preview_layout.addWidget(self._preview)
        layout.addWidget(preview_group)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save server options")
        save_btn.setToolTip("Persist the values; the next launch uses them")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        btn_row.addWidget(self._status_label, stretch=1)
        layout.addLayout(btn_row)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._refresh_preview)

        self._extra_row = self._extra_args_row()
        self._populate()
        self._refresh_preview()

    # ─── Table construction ─────────────────────────────────────────────

    def _populate(self) -> None:
        self._table.setRowCount(0)
        self._rows.clear()
        for arg in SERVER_ARGS:
            self._append_row(arg)
        self._append_row(self._extra_row)
        self._apply_filter()

    @staticmethod
    def _extra_args_row() -> ServerArg:
        return ServerArg(
            EXTRA_ARGS_FLAG,
            "server",
            ArgKind.STRING,
            "Raw extra arguments appended after every generated flag "
            "(split on whitespace).",
            default="",
        )

    def _append_row(self, arg: ServerArg) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        editor = self._make_editor(arg)
        self._set_editor_value(editor, self._current_value(arg))
        self._rows.append((arg, editor))
        self._table.setCellWidget(row, 1, editor)
        self._table.setItem(row, 0, QTableWidgetItem(arg.flag))
        self._table.setItem(row, 2, QTableWidgetItem(arg.default))
        desc = arg.help
        if arg.aliases:
            desc += f"\nAliases: {', '.join(arg.aliases)}"
        if arg.env:
            desc += f"\nEnv: {arg.env}"
        if arg.deprecated:
            desc += "\nDEPRECATED / REMOVED — may be rejected by newer builds."
        if arg.volatile:
            desc += "\nOne-shot flag: prints and exits — never passed at launch."
        if arg.app_managed:
            desc += "\nSupplied automatically by the app (the active model)."
        self._table.setItem(row, 3, QTableWidgetItem(desc))
        item = self._table.item(row, 0)
        if item:
            item.setToolTip(arg.help)

    def _make_editor(self, arg: ServerArg) -> QWidget:
        if arg.volatile:
            disabled = QLineEdit()
            disabled.setEnabled(False)
            disabled.setPlaceholderText("one-shot flag — cannot be set")
            return disabled
        if arg.kind is ArgKind.BOOL:
            combo = QComboBox()
            combo.addItems([_EMPTY, "on", "off"] if arg.negated else [_EMPTY, "on"])
            combo.setCurrentText(self._current_value(arg))
            combo.currentTextChanged.connect(self._mark_dirty)
            return combo
        if arg.kind is ArgKind.CHOICE:
            combo = QComboBox()
            combo.addItems([_EMPTY, *arg.choices])
            combo.setCurrentText(self._current_value(arg))
            combo.currentTextChanged.connect(self._mark_dirty)
            return combo
        if arg.kind is ArgKind.PATH:
            return _PathEdit(
                is_dir=arg.is_dir,
                caption=f"Select {'directory' if arg.is_dir else 'file'}",
                text_changed=self._mark_dirty,
            )
        line = QLineEdit()
        line.setPlaceholderText(_EMPTY)
        line.setText(self._current_value(arg))
        line.textChanged.connect(self._mark_dirty)
        return line

    def _current_value(self, arg: ServerArg) -> str:
        """Value shown for ``arg`` ('' = not set / binary default wins)."""
        if arg.flag in DEDICATED_FLAGS:
            if arg.flag == "--host":
                return str(self._orch.cfg.host)
            if arg.flag == "--port":
                return str(self._orch.cfg.port)
            if arg.flag == "--ctx-size":
                return (
                    str(self._orch.cfg.ctx_size) if self._orch.cfg.ctx_size > 0 else ""
                )
            if arg.flag == "--n-gpu-layers":
                return (
                    str(self._orch.cfg.n_gpu_layers)
                    if self._orch.cfg.n_gpu_layers >= 0
                    else ""
                )
        return str(self._orch.cfg.server_options.get(arg.flag, ""))

    def _set_editor_value(self, editor: QWidget, value: str) -> None:
        if isinstance(editor, QComboBox):
            index = editor.findText(value)
            if index >= 0:
                editor.setCurrentIndex(index)
            return
        if isinstance(editor, _PathEdit):
            editor.setValue(value)
            return
        if isinstance(editor, QLineEdit):
            editor.setText(value)

    # ─── Collect / save ─────────────────────────────────────────────────

    def collect(self) -> dict[str, Any]:
        """The settings dict that ``save`` persists ('' = binary default)."""
        options: dict[str, str] = {}
        dedicated: dict[str, str] = {}
        extra = ""
        for arg, editor in self._rows:
            if arg is self._extra_row:
                extra = self._read_editor(editor)
                continue
            value = self._read_editor(editor)
            if not value or value == _EMPTY:
                continue
            if arg.flag in DEDICATED_FLAGS:
                dedicated[arg.flag] = value
            else:
                options[arg.flag] = value
        data: dict[str, Any] = {"server_options": options}
        data.update(self._dedicated_updates(dedicated))
        if extra:
            data["extra_server_args"] = extra
        return data

    def _dedicated_updates(self, values: dict[str, str]) -> dict[str, Any]:
        """Map the four dedicated rows onto the long-standing AppConfig fields."""
        updates: dict[str, Any] = {}
        host = values.get("--host", "").strip()
        port = values.get("--port", "").strip()
        ctx = values.get("--ctx-size", "").strip()
        ngl = values.get("--n-gpu-layers", "").strip()

        if host:
            updates["host"] = host
        if port:
            if not port.isdigit():
                raise ValueError(f"--port expects an integer, got '{port}'")
            updates["port"] = int(port)
        if ctx:
            if ctx in ("auto", "default"):
                updates["ctx_size"] = -1
            elif ctx.isdigit():
                updates["ctx_size"] = int(ctx)
            else:
                raise ValueError(
                    f"--ctx-size expects an integer or 'auto', got '{ctx}'"
                )
        if ngl:
            if ngl in ("auto", "all", "default"):
                updates["n_gpu_layers"] = -1
            elif ngl.isdigit():
                updates["n_gpu_layers"] = int(ngl)
            else:
                raise ValueError(
                    f"--n-gpu-layers expects an integer, 'auto' or 'all', got '{ngl}'"
                )
        return updates

    def _save(self) -> None:
        try:
            data = self.collect()
        except ValueError as exc:
            self._status_label.setText(str(exc))
            return
        errors = validate_options(data.get("server_options", {}))
        if errors:
            first = next(iter(errors.values()))
            self._status_label.setText(first)
            return
        self._orch.save_config(data)
        self._status_label.setText("Server options saved.")
        self._refresh_preview()

    def _reset_all(self) -> None:
        for arg, editor in self._rows:
            self._set_editor_value(editor, self._current_value(arg))
        self._status_label.setText("All values reset to their defaults.")
        self._refresh_preview()

    # ─── Preview ────────────────────────────────────────────────────────

    def _mark_dirty(self, *_: Any) -> None:
        self._debounce.start()

    def _refresh_preview(self) -> None:
        try:
            data = self.collect()
        except ValueError as exc:
            self._preview.setPlainText(f"invalid: {exc}")
            return
        cfg = self._orch.cfg
        resolved = resolve_llama_server(cfg, validate=False)
        exe = resolved.path if resolved.path else "llama-server"
        try:
            model = str(self._orch._resolve_model_path())
        except Exception:  # noqa: BLE001 - preview degrades to a placeholder
            model = "<model>"
        cmd = build_llama_server_args(
            exe,
            model,
            host=data.get("host") or cfg.host,
            port=data.get("port") or cfg.port,
            ctx_size=data.get("ctx_size") or -1,
            n_gpu_layers=data.get("n_gpu_layers") or -1,
            extra_args=data.get("extra_server_args") or cfg.extra_server_args,
            server_options=data.get("server_options", {}),
        )
        quoted = [t if " " not in t else f'"{t}"' for t in cmd]
        self._preview.setPlainText(" ".join(quoted))

    # ─── Filtering ──────────────────────────────────────────────────────

    def _apply_filter(self) -> None:
        query = self._search.text().strip().lower()
        section = self._section_combo.currentText()
        visible = 0
        for row, (arg, _editor) in enumerate(self._rows):
            if section != "all" and arg.section != section:
                self._table.hideRow(row)
                continue
            if query and not self._row_matches(arg, query):
                self._table.hideRow(row)
                continue
            self._table.showRow(row)
            visible += 1
        self._count_label.setText(f"{visible} options")

    def _row_matches(self, arg: ServerArg, query: str) -> bool:
        haystack = " ".join(
            (arg.flag, *arg.aliases, arg.help, arg.env, arg.default)
        ).lower()
        return query in haystack

    @staticmethod
    def _read_editor(editor: QWidget) -> str:
        if isinstance(editor, QComboBox):
            return editor.currentText().strip()
        if isinstance(editor, _PathEdit):
            return editor.value()
        if isinstance(editor, QLineEdit):
            return editor.text().strip()
        return ""
