from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..token import delete_token, get_token, set_token


class SettingsPage(QWidget):
    def __init__(self, orch: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orch = orch

        layout = QVBoxLayout(self)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()

        self._root_edit = QLineEdit()
        form.addRow("Root path", self._root_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        form.addRow("Port", self._port_spin)

        self._listen_check = QCheckBox("--listen")
        form.addRow("Listen flag", self._listen_check)

        self._backend_combo = QComboBox()
        self._backend_combo.addItems(self._orch.backend_names())
        form.addRow("Default backend", self._backend_combo)

        self._auto_update_check = QCheckBox()
        form.addRow("Auto-update", self._auto_update_check)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 168)
        self._interval_spin.setSuffix(" hours")
        form.addRow("Update interval", self._interval_spin)

        self._launch_check = QCheckBox()
        form.addRow("Launch router on start", self._launch_check)

        self._minimized_check = QCheckBox()
        form.addRow("Start minimized", self._minimized_check)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["system", "light", "dark"])
        form.addRow("Theme", self._theme_combo)

        self._priority_edit = QLineEdit()
        form.addRow("Source priority (comma-separated)", self._priority_edit)

        self._pointed_folder_edit = QLineEdit()
        form.addRow("Pointed folder", self._pointed_folder_edit)

        self._pointed_server_edit = QLineEdit()
        form.addRow("Pointed llama-server path", self._pointed_server_edit)

        self._pointed_swap_edit = QLineEdit()
        form.addRow("Pointed llama-swap path", self._pointed_swap_edit)

        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        token = get_token()
        if token:
            self._token_edit.setText("*" * 8)
            self._token_edit.setPlaceholderText("token saved in keyring")
        form.addRow("GitHub token", self._token_edit)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        clear_token_btn = QPushButton("Clear token")
        clear_token_btn.clicked.connect(self._clear_token)
        btn_row.addWidget(clear_token_btn)

        layout.addLayout(btn_row)

        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        layout.addStretch()

        self._load()

    def _load(self) -> None:
        cfg = self._orch.cfg
        self._root_edit.setText(cfg.root)
        self._port_spin.setValue(cfg.port)
        self._listen_check.setChecked(cfg.listen_flag == "--listen")
        self._backend_combo.setCurrentText(cfg.default_backend)
        self._auto_update_check.setChecked(cfg.auto_update)
        self._interval_spin.setValue(cfg.auto_update_interval_hours)
        self._launch_check.setChecked(cfg.launch_on_start)
        self._minimized_check.setChecked(cfg.start_minimized)
        self._theme_combo.setCurrentText(cfg.theme)
        self._priority_edit.setText(", ".join(cfg.source_priority))
        self._pointed_folder_edit.setText(cfg.pointed.folder or "")
        self._pointed_server_edit.setText(cfg.pointed.llama_server or "")
        self._pointed_swap_edit.setText(cfg.pointed.llama_swap or "")

    def _save(self) -> None:
        cfg = self._orch.cfg
        data = {
            "root": self._root_edit.text().strip(),
            "port": self._port_spin.value(),
            "listen_flag": "--listen" if self._listen_check.isChecked() else "",
            "default_backend": self._backend_combo.currentText(),
            "auto_update": self._auto_update_check.isChecked(),
            "auto_update_interval_hours": self._interval_spin.value(),
            "launch_on_start": self._launch_check.isChecked(),
            "start_minimized": self._minimized_check.isChecked(),
            "theme": self._theme_combo.currentText(),
            "source_priority": cfg.source_priority,
            "pointed": {
                "folder": self._pointed_folder_edit.text().strip() or None,
                "llama_server": self._pointed_server_edit.text().strip() or None,
                "llama_swap": self._pointed_swap_edit.text().strip() or None,
            },
        }
        raw_priority = self._priority_edit.text()
        if raw_priority.strip():
            data["source_priority"] = [
                p.strip() for p in raw_priority.split(",") if p.strip()
            ]

        token_text = self._token_edit.text()
        if token_text and token_text != "*" * 8:
            set_token(token_text)

        # Route through orch.save_config() so the orchestrator's root path is
        # updated in sync with the config (fixes stale-root bugs after a change).
        self._orch.save_config(data)
        self._status_label.setText("Settings saved. Token stored in keyring.")

    def _clear_token(self) -> None:
        delete_token()
        self._token_edit.clear()
        self._token_edit.setPlaceholderText("token removed from keyring")
        self._status_label.setText("Token cleared from keyring.")
