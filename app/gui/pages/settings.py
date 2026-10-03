"""Settings page.

Every value here is persisted through :meth:`Orchestrator.save_config`, which
writes the platform config file atomically and preserves keys it does not
know about — changing one setting can never lose the others.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...config import CUDA_RUNTIME_MODES
from ...schemas import RelocationData
from ..dialogs.relocate import RelocateDialog
from ..payload import as_payload
from ..theme import COLORS
from ..token import delete_token, get_token, set_token
from ..widgets.path_picker import PathPicker
from ..widgets.progress_bar import ProgressWidget
from ..worker_pool import EngineWorker, WorkerPool

_TOKEN_MASK = "*" * 8


class SettingsPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch

        layout = QVBoxLayout(self)

        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self._config_path_label = QLabel()
        self._config_path_label.setStyleSheet(f"color: {COLORS['muted']};")
        self._config_path_label.setWordWrap(True)
        layout.addWidget(self._config_path_label)

        layout.addWidget(self._build_general_group())
        layout.addWidget(self._build_paths_group())
        layout.addWidget(self._build_updates_group())

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save settings")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        validate_btn = QPushButton("Validate binaries")
        validate_btn.clicked.connect(self._validate)
        btn_row.addWidget(validate_btn)

        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._load)
        btn_row.addWidget(reload_btn)

        clear_token_btn = QPushButton("Clear token")
        clear_token_btn.clicked.connect(self._clear_token)
        btn_row.addWidget(clear_token_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch()

        # Set while a relocation is in flight: the pending settings dict to save
        # once the move finished, kept here because the worker reports back
        # through a signal that carries only the engine result.
        self._pending_save: dict[str, Any] | None = None

        self._load()

    # ─── Form construction ───────────────────────────────────────────────

    def _build_general_group(self) -> QGroupBox:
        group = QGroupBox("General")
        form = QFormLayout(group)

        self._root_picker = PathPicker(
            mode="directory",
            caption="Managed root directory",
            placeholder="Where backends are downloaded",
            action_label="Use Default",
            on_action=self._use_default_root,
        )
        self._root_picker.setToolTip(
            "Where llama-server builds are downloaded and installed.\n\n"
            "Use Default removes the override and goes back to the platform "
            "location."
        )
        form.addRow("Managed root", self._root_picker)

        self._host_edit = QLineEdit()
        form.addRow("Host", self._host_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        form.addRow("Port", self._port_spin)

        self._backend_combo = QComboBox()
        self._backend_combo.addItems(self._orch.backend_names())
        form.addRow("Default backend", self._backend_combo)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["system", "light", "dark"])
        form.addRow("Theme", self._theme_combo)

        self._launch_check = QCheckBox()
        form.addRow("Launch server on start", self._launch_check)

        self._minimized_check = QCheckBox()
        form.addRow("Start minimized to tray", self._minimized_check)
        return group

    def _build_paths_group(self) -> QGroupBox:
        group = QGroupBox("Paths")
        form = QFormLayout(group)

        self._models_dir_picker = PathPicker(
            mode="directory",
            caption="Choose models directory",
            placeholder="Where .gguf files live",
            action_label="Use Default",
            on_action=self._use_default_models_dir,
        )
        self._models_dir_picker.setToolTip(
            "Where downloaded .gguf models are stored.\n\n"
            "Use Default removes the override and stores models under "
            "<managed root>/models."
        )
        form.addRow("Models directory", self._models_dir_picker)

        self._backend_location_label = QLabel()
        self._backend_location_label.setStyleSheet(f"color: {COLORS['muted']};")
        form.addRow("Backend location", self._backend_location_label)

        self._os_llama_check = QCheckBox("Use OS installed llama.cpp")
        self._os_llama_check.setToolTip(
            "Prefer the llama-server found on PATH (OS install / package "
            "manager) over the backend downloaded into the backend location."
        )
        form.addRow("", self._os_llama_check)

        self._cudart_combo = QComboBox()
        self._cudart_combo.addItems(list(CUDA_RUNTIME_MODES))
        form.addRow("Bundle CUDA runtime", self._cudart_combo)
        return group

    def _build_updates_group(self) -> QGroupBox:
        group = QGroupBox("Updates")
        form = QFormLayout(group)

        self._auto_update_check = QCheckBox()
        form.addRow("Check for updates automatically", self._auto_update_check)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 168)
        self._interval_spin.setSuffix(" hours")
        form.addRow("Update interval", self._interval_spin)

        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("GitHub token", self._token_edit)
        return group

    # ─── Load / save ─────────────────────────────────────────────────────

    def _load(self) -> None:
        cfg = self._orch.cfg
        # A defaulted path is shown as a hint, not as text: a filled field would
        # be read back as an explicit override by ``collect`` and re-pinned to
        # the file on the next unrelated save.
        if cfg.root_is_default:
            self._root_picker.setText("")
            self._root_picker.setPlaceholderText(f"Default: {cfg.root}")
        else:
            self._root_picker.setText(cfg.root)
        self._host_edit.setText(cfg.host)
        self._port_spin.setValue(cfg.port)
        self._backend_combo.setCurrentText(cfg.default_backend)
        self._theme_combo.setCurrentText(cfg.theme)
        self._launch_check.setChecked(cfg.launch_on_start)
        self._minimized_check.setChecked(cfg.start_minimized)
        if cfg.models_dir_is_default:
            self._models_dir_picker.setText("")
            self._models_dir_picker.setPlaceholderText(
                f"Default: {cfg.models_dir_path}"
            )
        else:
            self._models_dir_picker.setText(cfg.models_dir)
        self._backend_location_label.setText(f"{cfg.managed_dir} (downloads)")
        self._os_llama_check.setChecked(cfg.use_os_llama_server)
        self._cudart_combo.setCurrentText(cfg.bundle_cuda_runtime)
        self._auto_update_check.setChecked(cfg.auto_update)
        self._interval_spin.setValue(cfg.auto_update_interval_hours)

        if get_token():
            self._token_edit.setText(_TOKEN_MASK)
            self._token_edit.setPlaceholderText("stored in the OS keyring")

        self._config_path_label.setText(f"Settings file: {self._config_file()}")
        for warning in getattr(cfg, "load_warnings", []):
            self._status_label.setText(str(warning))

    def _config_file(self) -> str:
        from ...paths import config_file

        return str(config_file())

    def collect(self) -> dict[str, Any]:
        """Return the form contents as a settings dict.

        Launch flags (``-c`` / ``-ngl`` / extra args and the whole llama-server
        option set) are edited on the **Server options** page; this page only
        touches app-level settings plus ``host``/``port``.

        An empty ``root``/``models_dir`` field means "follow the default" (the
        key is then left out of the settings file), so it must not be filled in
        with the resolved value — that would turn the default into an override.
        """
        return {
            "root": self._root_picker.text(),
            "host": self._host_edit.text().strip() or "127.0.0.1",
            "port": self._port_spin.value(),
            "default_backend": self._backend_combo.currentText(),
            "theme": self._theme_combo.currentText(),
            "launch_on_start": self._launch_check.isChecked(),
            "start_minimized": self._minimized_check.isChecked(),
            "models_dir": self._models_dir_picker.text(),
            "use_os_llama_server": self._os_llama_check.isChecked(),
            "bundle_cuda_runtime": self._cudart_combo.currentText(),
            "auto_update": self._auto_update_check.isChecked(),
            "auto_update_interval_hours": self._interval_spin.value(),
        }

    def _save(self) -> None:
        token_text = self._token_edit.text()
        if token_text and token_text != _TOKEN_MASK:
            set_token(token_text)
            self._token_edit.setText(_TOKEN_MASK)

        settings = self.collect()
        plan = self._orch.plan_relocation(
            root=settings["root"], models_dir=settings["models_dir"]
        )
        if plan.requires_choice:
            self._save_with_relocation(plan, settings)
            return
        self._persist(settings)

    def _save_with_relocation(
        self, plan: RelocationData, settings: dict[str, Any]
    ) -> None:
        """Ask about the data the new paths would leave behind.

        Cancelling changes nothing at all — neither the transfer nor the settings
        — so a mis-click cannot strand the files or move them unnoticed.
        """
        dialog = RelocateDialog(plan, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status_label.setText("Save cancelled; nothing was changed.")
            return
        transfer = dialog.choice
        if not dialog.moves_anything:
            # Move & save with nothing ticked is just a save.
            transfer = RelocateDialog.SAVE_PATHS_ONLY
        if transfer == RelocateDialog.SAVE_PATHS_ONLY:
            self._persist(settings)
            return

        self._pending_save = settings
        verb = "Copying" if transfer == RelocateDialog.COPY else "Moving"
        self._progress.start_operation(f"{verb}…")
        worker = EngineWorker(
            self._orch,
            "relocate_data",
            progress_callback=self._on_relocate_progress,
            root=settings["root"],
            models_dir=settings["models_dir"],
            transfer=transfer,
            move_backends=dialog.move_backends(),
            move_models=dialog.move_models(),
        )
        worker.signals.finished.connect(self._on_relocated)
        worker.signals.error.connect(self._on_relocate_failed)
        WorkerPool.instance().start(worker)

    def _on_relocate_progress(
        self, done: int, total: int, phase: str, overall: float | None = None
    ) -> None:
        self._progress.update_progress(done, total, phase, overall)

    def _on_relocated(self, data: Any) -> None:
        """Persist the new paths after the data arrived at them."""
        self._progress.finish_operation()
        settings = self._pending_save or {}
        self._pending_save = None
        verb = "Copied" if data.copied else "Moved"
        moved = ", ".join(item.label for item in data.transferred)
        self._persist(settings)
        detail = f"{verb} {moved}. " if moved else ""
        self._status_label.setText(
            f"{detail}Saved to {self._config_file()}. "
            "Restart the app for every view to pick it up."
        )

    def _on_relocate_failed(self, msg: str) -> None:
        """A failed transfer leaves the settings untouched, so the paths still work."""
        self._progress.fail_operation("Failed")
        self._pending_save = None
        self._status_label.setText(
            f"Transfer failed: {msg} — the settings were not changed."
        )

    def _persist(self, settings: dict[str, Any]) -> None:
        self._orch.save_config(settings)
        self._load()
        self._status_label.setText(f"Saved to {self._config_file()}")

    def _validate(self) -> None:
        """Run the resolver so the user sees exactly which binaries would run."""
        try:
            resolved = self._orch.resolve()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self._status_label.setText(f"Validation failed: {exc}")
            return
        data = as_payload(resolved)
        self._status_label.setText(
            _format_resolution("llama-server", data.get("llama_server", {}))
        )

    def _use_default_root(self, _current: str) -> None:
        """Drop the root override so the app follows the platform default."""
        self._use_default("root", self._orch.reset_root)

    def _use_default_models_dir(self, _current: str) -> None:
        """Drop the models directory override (back to ``<root>/models``)."""
        self._use_default("models directory", self._orch.reset_models_dir)

    def _use_default(self, label: str, reset: Any) -> None:
        """Run a "Use Default" reset and report the resulting location.

        The override is removed from the config file rather than written as the
        resolved path, so the setting keeps tracking the default instead of
        being pinned to today's value.
        """
        try:
            data = reset()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self._status_label.setText(f"Could not reset the {label}: {exc}")
            return
        self._load()
        self._status_label.setText(
            data.warnings[-1] if data.warnings else f"{label} set to its default."
        )

    def _clear_token(self) -> None:
        delete_token()
        self._token_edit.clear()
        self._token_edit.setPlaceholderText("token removed from keyring")
        self._status_label.setText("Token cleared from the OS keyring.")


def _format_resolution(label: str, info: dict[str, Any]) -> str:
    if not info.get("path"):
        # A resolver can fail with an error and no path (e.g. a binary that was
        # found but could not be validated). Reporting only "not found" would
        # hide the actual reason, so show it when there is one.
        detail = info.get("error")
        return f"{label}: {detail}" if detail else f"{label}: not found"
    state = "OK" if info.get("valid") else f"INVALID ({info.get('error') or '?'})"
    version = info.get("version") or "unknown version"
    return f"{label}: {info['path']} [{info.get('source')}] {version} — {state}"
