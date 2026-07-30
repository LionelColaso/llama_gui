from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path.home() / ".llamagui"
DEFAULT_PORT = 8080
DEFAULT_LISTEN_FLAG = "--listen"
DEFAULT_BACKEND = "vulkan"
DEFAULT_SOURCE_PRIORITY = ["pointed", "managed", "system"]


@dataclass
class PointedPaths:
    folder: str | None = None
    llama_server: str | None = None
    llama_swap: str | None = None


@dataclass
class AppConfig:
    root: str = str(DEFAULT_ROOT)
    port: int = DEFAULT_PORT
    listen_flag: str = DEFAULT_LISTEN_FLAG
    default_backend: str = DEFAULT_BACKEND
    source_priority: list[str] = field(
        default_factory=lambda: list(DEFAULT_SOURCE_PRIORITY)
    )
    pointed: PointedPaths = field(default_factory=PointedPaths)
    auto_update: bool = False
    auto_update_interval_hours: int = 24
    launch_on_start: bool = False
    start_minimized: bool = False
    theme: str = "system"
    token: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        if path is None:
            path = DEFAULT_ROOT / "config.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pointed_data = data.get("pointed", {})
            pointed = PointedPaths(
                folder=pointed_data.get("folder"),
                llama_server=pointed_data.get("llama_server"),
                llama_swap=pointed_data.get("llama_swap"),
            )
            # The GitHub token is never persisted to disk in plaintext; it lives
            # in the OS keyring (gui/token.py). A legacy plaintext token written
            # by an older build is deliberately not loaded (security fix).
            return cls(
                root=data.get("root", str(DEFAULT_ROOT)),
                port=data.get("port", DEFAULT_PORT),
                listen_flag=data.get("listen_flag", DEFAULT_LISTEN_FLAG),
                default_backend=data.get("default_backend", DEFAULT_BACKEND),
                source_priority=data.get(
                    "source_priority", list(DEFAULT_SOURCE_PRIORITY)
                ),
                pointed=pointed,
                auto_update=data.get("auto_update", False),
                auto_update_interval_hours=data.get("auto_update_interval_hours", 24),
                launch_on_start=data.get("launch_on_start", False),
                start_minimized=data.get("start_minimized", False),
                theme=data.get("theme", "system"),
                token="",
            )
        except (json.JSONDecodeError, KeyError):
            return cls()

    def save(self, path: Path | None = None) -> None:
        if path is None:
            path = DEFAULT_ROOT / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._to_dict()
        dirpath = str(path.parent) or "."
        fd, tmp = tempfile.mkstemp(suffix=".json", dir=dirpath)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                if hasattr(os, "fsync"):
                    with suppress(OSError):
                        # fsync the file object; the underlying fd is still open
                        # inside ``f`` until the with-block exits.
                        os.fsync(f.fileno())
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "port": self.port,
            "listen_flag": self.listen_flag,
            "default_backend": self.default_backend,
            "source_priority": self.source_priority,
            "pointed": {
                "folder": self.pointed.folder,
                "llama_server": self.pointed.llama_server,
                "llama_swap": self.pointed.llama_swap,
            },
            "auto_update": self.auto_update,
            "auto_update_interval_hours": self.auto_update_interval_hours,
            "launch_on_start": self.launch_on_start,
            "start_minimized": self.start_minimized,
            "theme": self.theme,
            # Never persist the token to disk (keyring only).
            "token": "",
        }
