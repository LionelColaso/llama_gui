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

from ...config import CUDA_RUNTIME_MODES, INSTALL_SOURCES
from ..token import delete_token, get_token, set_token
from ..widgets.path_picker import PathPicker

_TOKEN_MASK = "*" * 8


class SettingsPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch

        layout = QVBoxLayout(self)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        self._config_path_label = QLabel()
        self._config_path_label.setStyleSheet("color: #757575;")
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

        self._listen_check = QCheckBox("Pass --listen host:port to llama-swap")
        form.addRow("Listen flag", self._listen_check)

        self._backend_combo = QComboBox()
        self._backend_combo.addItems(self._orch.backend_names())
        form.addRow("Default backend", self._backend_combo)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["system", "light", "dark"])
        form.addRow("Theme", self._theme_combo)

        self._launch_check = QCheckBox()
        form.addRow("Launch router on start", self._launch_check)

        self._minimized_check = QCheckBox()
        form.addRow("Start minimized to tray", self._minimized_check)
        return group

    def _build_paths_group(self) -> QGroupBox:
        group = QGroupBox("Binary locations")
        form = QFormLayout(group)

        self._priority_edit = QLineEdit()
        self._priority_edit.setPlaceholderText("pointed, managed, system")
        form.addRow("Source priority", self._priority_edit)

        self._pointed_folder = PathPicker(
            mode="directory",
            caption="Folder containing llama-server / llama-swap",
            placeholder="Optional: a folder holding both binaries",
        )
        form.addRow("Pointed folder", self._pointed_folder)

        self._pointed_server = PathPicker(
            caption="Select llama-server",
            placeholder="Optional: full path to llama-server",
        )
        form.addRow("llama-server path", self._pointed_server)

        self._pointed_swap = PathPicker(
            caption="Select llama-swap",
            placeholder="Optional: full path to llama-swap",
        )
        form.addRow("llama-swap path", self._pointed_swap)

        self._install_source_combo = QComboBox()
        self._install_source_combo.addItems(list(INSTALL_SOURCES))
        form.addRow("Install source", self._install_source_combo)

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
        self._listen_check.setChecked(bool(cfg.listen_flag))
        self._backend_combo.setCurrentText(cfg.default_backend)
        self._theme_combo.setCurrentText(cfg.theme)
        self._launch_check.setChecked(cfg.launch_on_start)
        self._minimized_check.setChecked(cfg.start_minimized)
        self._priority_edit.setText(", ".join(cfg.source_priority))
        self._pointed_folder.setText(cfg.pointed.folder)
        self._pointed_server.setText(cfg.pointed.llama_server)
        self._pointed_swap.setText(cfg.pointed.llama_swap)
        self._install_source_combo.setCurrentText(cfg.install_source)
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
        """Return the form contents as a settings dict."""
        priority = [
            p.strip() for p in self._priority_edit.text().split(",") if p.strip()
        ]
        return {
            "root": self._root_picker.text() or self._orch.cfg.root,
            "host": self._host_edit.text().strip() or "127.0.0.1",
            "port": self._port_spin.value(),
            "listen_flag": "--listen" if self._listen_check.isChecked() else "",
            "default_backend": self._backend_combo.currentText(),
            "theme": self._theme_combo.currentText(),
            "launch_on_start": self._launch_check.isChecked(),
            "start_minimized": self._minimized_check.isChecked(),
            "source_priority": priority or list(self._orch.cfg.source_priority),
            "pointed": {
                "folder": self._pointed_folder.value(),
                "llama_server": self._pointed_server.value(),
                "llama_swap": self._pointed_swap.value(),
            },
            "install_source": self._install_source_combo.currentText(),
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
        data = resolved.model_dump() if hasattr(resolved, "model_dump") else resolved
        self._status_label.setText(
            "\n".join(
                _format_resolution(label, data.get(key, {}))
                for label, key in (
                    ("llama-server", "llama_server"),
                    ("llama-swap", "llama_swap"),
                )
            )
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
