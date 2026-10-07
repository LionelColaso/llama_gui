"""The server-options editor widget: every ``llama-server`` flag, one row each.

Data-driven from :mod:`app.serverargs`, with the right editor per kind — combo
for booleans and choices, text or path+browse for paths — plus a live
command-line preview of exactly what ``launch`` will run. The widget is pure
presentation plus collection: it never writes the config itself, so the same
editor backs the *Server options* page (global defaults) and the per-model
*Server options…* popup, which differ only in what they do with
:func:`collect_global` / :func:`collect_model`.

**Two scopes, one map.** :meth:`set_scope(None)` edits the global configuration;
:meth:`set_scope(model)` edits one model's *own* configuration — its values alone
decide, with a blank row meaning "let llama.cpp decide". A model that follows the
globals simply has no own configuration, so its popup shows the global rows
read-only rather than a second copy of them.
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

from ...lifecycle import (
    build_llama_server_args,
    dedicated_value_int,
    dedicated_value_text,
    launch_settings,
    model_server_options,
)
from ...resolver import resolve_llama_server
from ...serverargs import (
    DEDICATED_FLAGS,
    SECTIONS,
    SERVER_ARGS,
    ArgKind,
    ServerArg,
)

#: What an unset row shows: the value is omitted, so the binary's own default wins.
EMPTY_VALUE = "(default)"

#: Pseudo-flag for the raw "extra args" row (not a real llama-server flag).
EXTRA_ARGS_FLAG = "__extra_args__"

#: Flags that stay global because the app itself depends on them.
GLOBAL_ONLY_FLAGS = ("--host", "--port")


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
        self._edit.setPlaceholderText(EMPTY_VALUE)
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


class ServerOptionsEditor(QWidget):
    """The searchable, sectioned table of server options plus its preview."""

    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch
        self._rows: list[tuple[ServerArg, QWidget]] = []
        #: The model being edited; ``None`` = the global configuration.
        self._scope: str | None = None
        self._read_only = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._preview_pending)

        self._extra_row = _extra_args_row()
        self._populate()
        self.set_scope(None)

    # ─── Scope ──────────────────────────────────────────────────────────

    def scope(self) -> str | None:
        """The model being edited, or ``None`` for the global configuration."""
        return self._scope

    def set_scope(self, model: str | None) -> None:
        """Show the global configuration, or one model's own configuration."""
        self._scope = model
        self.reload()

    def set_read_only(self, read_only: bool) -> None:
        """Lock the rows (``read_only``) or unlock them.

        Locking is how the popup shows the configuration a model *follows*: the
        real values, visible but not editable, so it is obvious what the model
        would run with.
        """
        self._read_only = read_only
        self._update_row_states()

    def is_read_only(self) -> bool:
        return self._read_only

    def reload(self) -> None:
        """Re-read every row from the config for the current scope."""
        for arg, editor in self._rows:
            self._set_editor_value(editor, self._current_value(arg))
        self._update_row_states()
        self._refresh_preview()

    # ─── Table construction ─────────────────────────────────────────────

    def _populate(self) -> None:
        self._table.setRowCount(0)
        self._rows.clear()
        for arg in SERVER_ARGS:
            self._append_row(arg)
        self._append_row(self._extra_row)
        self._apply_filter()

    def _append_row(self, arg: ServerArg) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        editor = self._make_editor(arg)
        self._set_editor_value(editor, self._current_value(arg))
        self._rows.append((arg, editor))
        self._table.setCellWidget(row, 1, editor)
        self._table.setItem(row, 0, QTableWidgetItem(arg.flag))
        self._table.setItem(row, 2, QTableWidgetItem(arg.default))
        self._table.setItem(row, 3, QTableWidgetItem(_describe(arg)))
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
            combo.addItems(
                [EMPTY_VALUE, "on", "off"] if arg.negated else [EMPTY_VALUE, "on"]
            )
            combo.setCurrentText(self._current_value(arg))
            combo.currentTextChanged.connect(self._mark_dirty)
            return combo
        if arg.kind is ArgKind.CHOICE:
            combo = QComboBox()
            combo.addItems([EMPTY_VALUE, *arg.choices])
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
        line.setPlaceholderText(EMPTY_VALUE)
        line.setText(self._current_value(arg))
        line.textChanged.connect(self._mark_dirty)
        return line

    # ─── Values ─────────────────────────────────────────────────────────

    def _global_value(self, arg: ServerArg) -> str:
        """The global text for ``arg``, whatever the current scope is."""
        if arg.flag == EXTRA_ARGS_FLAG:
            return str(self._orch.cfg.extra_server_args)
        if arg.flag == "--host":
            return str(self._orch.cfg.host)
        if arg.flag == "--port":
            return str(self._orch.cfg.port)
        if arg.flag == "--ctx-size":
            return dedicated_value_text(arg.flag, self._orch.cfg.ctx_size)
        if arg.flag == "--n-gpu-layers":
            return dedicated_value_text(arg.flag, self._orch.cfg.n_gpu_layers)
        return str(self._orch.cfg.server_options.get(arg.flag, ""))

    def _current_value(self, arg: ServerArg) -> str:
        """Value shown for ``arg`` ('' = not set / the binary default wins).

        In a model scope this is what a launch of that model would use: the
        model's own values when it has them, the global ones when it follows
        them, and the global value for the rows that are global anyway.
        """
        if self._scope is None:
            return self._global_value(arg)
        if arg.flag in GLOBAL_ONLY_FLAGS or arg.flag == EXTRA_ARGS_FLAG:
            return self._global_value(arg)
        settings = launch_settings(self._orch.cfg, self._scope)
        if arg.flag == "--ctx-size":
            return dedicated_value_text(arg.flag, settings.ctx_size)
        if arg.flag == "--n-gpu-layers":
            return dedicated_value_text(arg.flag, settings.n_gpu_layers)
        return str(model_server_options(self._orch.cfg, self._scope).get(arg.flag, ""))

    def _set_editor_value(self, editor: QWidget, value: str) -> None:
        if isinstance(editor, QComboBox):
            # Combos list "(default)" where an unset value belongs, so an empty
            # value selects item 0 rather than leaving the previous one behind.
            index = editor.findText(value or EMPTY_VALUE)
            editor.setCurrentIndex(max(index, 0))
            return
        if isinstance(editor, _PathEdit):
            editor.setValue(value)
            return
        if isinstance(editor, QLineEdit):
            editor.setText(value)

    def _update_row_states(self) -> None:
        """Grey out what cannot be edited in the current scope/mode."""
        in_model = self._scope is not None
        for arg, editor in self._rows:
            if arg.volatile:
                continue
            locked = self._read_only or (
                in_model
                and (arg.flag in GLOBAL_ONLY_FLAGS or arg.flag == EXTRA_ARGS_FLAG)
            )
            editor.setEnabled(not locked)
            if in_model and arg.flag in GLOBAL_ONLY_FLAGS:
                editor.setToolTip(
                    "The app probes one host/port for health, status and stop, "
                    "so this stays global. Edit it in the global server options."
                )

    def clear_to_defaults(self) -> None:
        """Blank every row: the *Reset* form, before anything is written."""
        for _arg, editor in self._rows:
            self._set_editor_value(editor, "")
        self._refresh_preview()

    def row(self, flag: str) -> QWidget:
        """The editor widget of ``flag``'s row, whatever kind of editor it is."""
        for arg, editor in self._rows:
            if arg.flag == flag:
                return editor
        raise KeyError(flag)

    def value_of(self, flag: str) -> str:
        """``flag``'s text, with the unset sentinel read back as ``''``."""
        editor = self.row(flag)
        text = _read_editor(editor)
        return "" if text == EMPTY_VALUE else text

    def set_value_of(self, flag: str, value: str) -> None:
        """Set ``flag``'s row to ``value`` (``''`` clears it)."""
        editor = self.row(flag)
        if isinstance(editor, QComboBox):
            editor.setCurrentText(value)
            return
        self._set_editor_value(editor, value)

    # ─── Collect ────────────────────────────────────────────────────────

    def collect_global(self) -> dict[str, Any]:
        """The ``save_config`` payload for the global configuration."""
        options: dict[str, str] = {}
        dedicated: dict[str, str] = {}
        extra = ""
        for arg, editor in self._rows:
            if arg.flag == EXTRA_ARGS_FLAG:
                extra = _read_editor(editor)
                continue
            value = _read_editor(editor)
            if not value or value == EMPTY_VALUE:
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

    def collect_model(self) -> dict[str, str]:
        """One model's own options, read off the form.

        Every row the model may own is included; an empty row is simply absent,
        which is what "the binary's default wins" means. ``--host``/``--port``
        and the raw extra args are never collected — they are global.
        """
        options: dict[str, str] = {}
        for arg, editor in self._rows:
            if arg.flag in GLOBAL_ONLY_FLAGS or arg.flag == EXTRA_ARGS_FLAG:
                continue
            value = _read_editor(editor)
            if not value or value == EMPTY_VALUE:
                continue
            if arg.flag in DEDICATED_FLAGS:
                # Parsed through the same helper the launch path uses, so
                # "auto"/"all" and "999" compare equal and never get stored as
                # different values for the same thing.
                parsed = dedicated_value_int(arg.flag, value, -1)
                if parsed >= 0:
                    options[arg.flag] = str(parsed)
                continue
            options[arg.flag] = value
        return options

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
            updates["ctx_size"] = _dedicated_int(ctx, "--ctx-size")
        if ngl:
            updates["n_gpu_layers"] = _dedicated_int(ngl, "--n-gpu-layers")
        return updates

    # ─── Preview ────────────────────────────────────────────────────────

    def _mark_dirty(self, *_: Any) -> None:
        self._debounce.start()

    def _preview_pending(self) -> None:
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        try:
            self._preview.setPlainText(self._preview_text())
        except ValueError as exc:
            self._preview.setPlainText(f"invalid: {exc}")

    def _preview_text(self) -> str:
        cfg = self._orch.cfg
        resolved = resolve_llama_server(cfg, validate=False)
        exe = resolved.path if resolved.path else "llama-server"
        try:
            model = str(self._orch._resolve_model_path())
        except Exception:  # noqa: BLE001 - preview degrades to a placeholder
            model = "<model>"

        if self._scope is None:
            data = self.collect_global()
            options = dict(data.get("server_options", {}))
            host = data.get("host") or cfg.host
            port = data.get("port") or cfg.port
            # A blank dedicated row means "auto" in the form, so it must read as
            # -1 here too rather than falling back to the saved value.
            ctx_size = data.get("ctx_size", -1)
            n_gpu_layers = data.get("n_gpu_layers", -1)
            extra = data.get("extra_server_args") or cfg.extra_server_args
        else:
            settings = launch_settings(cfg, self._scope)
            options = self.collect_model()
            host, port = settings.host, settings.port
            ctx_size = dedicated_value_int(
                "--ctx-size", options.get("--ctx-size", ""), settings.ctx_size
            )
            n_gpu_layers = dedicated_value_int(
                "--n-gpu-layers",
                options.get("--n-gpu-layers", ""),
                settings.n_gpu_layers,
            )
            extra = settings.extra_args

        cmd = build_llama_server_args(
            exe,
            model,
            host=host,
            port=port,
            ctx_size=ctx_size,
            n_gpu_layers=n_gpu_layers,
            extra_args=extra,
            server_options=options,
        )
        quoted = [t if " " not in t else f'"{t}"' for t in cmd]
        preview = " ".join(quoted)
        if self._scope is not None:
            preview = f"# {self._scope}\n{preview}"
        return preview

    # ─── Filtering ──────────────────────────────────────────────────────

    def _apply_filter(self) -> None:
        query = self._search.text().strip().lower()
        section = self._section_combo.currentText()
        visible = 0
        for row, (arg, _editor) in enumerate(self._rows):
            if section != "all" and arg.section != section:
                self._table.hideRow(row)
                continue
            if query and not _row_matches(arg, query):
                self._table.hideRow(row)
                continue
            self._table.showRow(row)
            visible += 1
        self._count_label.setText(f"{visible} options")


def _read_editor(editor: QWidget) -> str:
    """The text of one editor widget, whatever kind it is."""
    if isinstance(editor, QComboBox):
        return editor.currentText().strip()
    if isinstance(editor, _PathEdit):
        return editor.value()
    if isinstance(editor, QLineEdit):
        return editor.text().strip()
    return ""


def _extra_args_row() -> ServerArg:
    """The pseudo-row for the raw "extra args" string."""
    return ServerArg(
        EXTRA_ARGS_FLAG,
        "server",
        ArgKind.STRING,
        "Raw extra arguments appended after every generated flag "
        "(split on whitespace).",
        default="",
    )


def _dedicated_int(text: str, flag: str) -> int:
    """Parse a dedicated cell, mapping the "no value" words onto -1."""
    if text in ("auto", "all", "default"):
        return -1
    if not text.isdigit():
        raise ValueError(f"{flag} expects an integer, got '{text}'")
    return int(text)


def _describe(arg: ServerArg) -> str:
    """The help cell: the flag's own help plus every note that matters here."""
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
    return desc


def _row_matches(arg: ServerArg, query: str) -> bool:
    haystack = " ".join((arg.flag, *arg.aliases, arg.help, arg.env, arg.default))
    return query in haystack.lower()
