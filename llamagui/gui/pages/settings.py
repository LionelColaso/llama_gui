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
from ..payload import as_payload
from ..theme import COLORS
from ..token import delete_token, get_token, set_token
from ..widgets.path_picker import PathPicker

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

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch()

        self._load()

    # ─── Form construction ───────────────────────────────────────────────

    def _build_general_group(self) -> QGroupBox:
        group = QGroupBox("General")
        form = QFormLayout(group)

        self._root_picker = PathPicker(
            mode="directory", caption="Managed root directory"
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
        self._root_picker.setText(cfg.root)
        self._host_edit.setText(cfg.host)
        self._port_spin.setValue(cfg.port)
        self._backend_combo.setCurrentText(cfg.default_backend)
        self._theme_combo.setCurrentText(cfg.theme)
        self._launch_check.setChecked(cfg.launch_on_start)
        self._minimized_check.setChecked(cfg.start_minimized)
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
        """
        return {
            "root": self._root_picker.text() or self._orch.cfg.root,
            "host": self._host_edit.text().strip() or "127.0.0.1",
            "port": self._port_spin.value(),
            "default_backend": self._backend_combo.currentText(),
            "theme": self._theme_combo.currentText(),
            "launch_on_start": self._launch_check.isChecked(),
            "start_minimized": self._minimized_check.isChecked(),
            "models_dir": self._models_dir_picker.text() or self._orch.cfg.models_dir,
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

        self._orch.save_config(self.collect())
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

    def _clear_token(self) -> None:
        delete_token()
        self._token_edit.clear()
        self._token_edit.setPlaceholderText("token removed from keyring")
        self._status_label.setText("Token cleared from the OS keyring.")


def _format_resolution(label: str, info: dict[str, Any]) -> str:
    if not info.get("path"):
        return f"{label}: not found"
    state = "OK" if info.get("valid") else f"INVALID ({info.get('error') or '?'})"
    version = info.get("version") or "unknown version"
    return f"{label}: {info['path']} [{info.get('source')}] {version} — {state}"
