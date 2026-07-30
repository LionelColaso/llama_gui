from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


class ConfigYaml:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        self._data: dict[str, Any] = {}

    def load(self) -> None:
        if self._path.exists():
            with self._path.open(encoding="utf-8") as f:
                self._data = self._yaml.load(f) or {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            self._yaml.dump(self._data, f)

    def get_models(self) -> list[dict[str, Any]]:
        raw = self._data.get("models", [])
        return [dict(m) for m in raw]

    def add_model(self, item: dict[str, Any]) -> None:
        models = self._data.setdefault("models", [])
        raw = CommentedMap(item)
        models.append(raw)

    def update_model(self, index: int, item: dict[str, Any]) -> None:
        models = self._data.setdefault("models", [])
        if 0 <= index < len(models):
            models[index] = CommentedMap(item)

    def remove_model(self, index: int) -> None:
        models = self._data.setdefault("models", [])
        if 0 <= index < len(models):
            del models[index]

    def get_active(self) -> str | None:
        return self._data.get("active")

    def set_active(self, backend: str) -> None:
        self._data["active"] = backend

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self._data = value
