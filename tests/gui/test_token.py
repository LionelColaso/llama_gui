"""Keyring token storage and the Settings page's token controls.

The GitHub token is the one secret the app handles, and the invariant is
absolute: it lives in the OS keyring and is never written to the config
file. These tests use a fake keyring so nothing touches the developer's real
credential store, and they exercise the "keyring is unavailable" path that
makes these functions fail softly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pytestqt.qtbot import QtBot

from app.gui import token as token_mod
from app.gui.pages.settings import SettingsPage


class _FakeKeyring:
    """An in-memory stand-in for the keyring module."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("no usable keyring backend")

    def get_password(self, service: str, key: str) -> str | None:
        self._check()
        return self.store.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self._check()
        self.store[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        self._check()
        if (service, key) not in self.store:
            raise KeyError("no such entry")
        del self.store[(service, key)]


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(token_mod, "keyring", fake)
    return fake


def test_set_then_get_round_trips(fake_keyring: _FakeKeyring) -> None:
    token_mod.set_token("ghp_secret")
    assert token_mod.get_token() == "ghp_secret"
    assert fake_keyring.store[(token_mod.SERVICE, token_mod.KEY)] == "ghp_secret"


def test_get_returns_none_when_unset(fake_keyring: _FakeKeyring) -> None:
    assert token_mod.get_token() is None


def test_get_returns_none_for_an_empty_value(fake_keyring: _FakeKeyring) -> None:
    """An empty stored token is no token."""
    fake_keyring.store[(token_mod.SERVICE, token_mod.KEY)] = ""
    assert token_mod.get_token() is None


def test_delete_removes_the_token(fake_keyring: _FakeKeyring) -> None:
    token_mod.set_token("ghp_secret")
    token_mod.delete_token()
    assert token_mod.get_token() is None


def test_delete_of_a_missing_token_is_harmless(fake_keyring: _FakeKeyring) -> None:
    token_mod.delete_token()  # must not raise
    assert token_mod.get_token() is None


@pytest.mark.parametrize("op", ["get_token", "set_token", "delete_token"])
def test_operations_survive_a_broken_keyring(
    fake_keyring: _FakeKeyring, op: str
) -> None:
    """A headless Linux box has no usable backend; that must not crash the app."""
    fake_keyring.fail = True
    if op == "get_token":
        assert token_mod.get_token() is None
    elif op == "set_token":
        token_mod.set_token("ghp_secret")  # must not raise
    else:
        token_mod.delete_token()  # must not raise


# ─── Settings page token controls ─────────────────────────────────────────


def test_settings_masks_a_stored_token(
    qtbot: QtBot, fake_orch: MagicMock, fake_keyring: _FakeKeyring
) -> None:
    token_mod.set_token("ghp_secret")
    page = SettingsPage(fake_orch)
    qtbot.addWidget(page)
    page._load()
    assert page._token_edit.text() != "ghp_secret", "the token must not be shown"
    assert page._token_edit.text(), "its presence should still be indicated"


def test_settings_save_stores_the_token_in_the_keyring(
    qtbot: QtBot, fake_orch: MagicMock, fake_keyring: _FakeKeyring
) -> None:
    page = SettingsPage(fake_orch)
    qtbot.addWidget(page)
    page._token_edit.setText("ghp_typed")
    page._save()
    assert token_mod.get_token() == "ghp_typed"


def test_settings_save_never_sends_the_token_to_the_config(
    qtbot: QtBot, fake_orch: MagicMock, fake_keyring: _FakeKeyring
) -> None:
    """The token must reach the keyring only, never save_config()."""
    page = SettingsPage(fake_orch)
    qtbot.addWidget(page)
    page._token_edit.setText("ghp_typed")
    page._save()

    saved: dict[str, Any] = fake_orch.save_config.call_args.args[0]
    assert "ghp_typed" not in str(saved), "the token leaked into the config payload"


def test_settings_save_does_not_overwrite_with_the_mask(
    qtbot: QtBot, fake_orch: MagicMock, fake_keyring: _FakeKeyring
) -> None:
    """Re-saving while the mask is displayed must not clobber the real token."""
    token_mod.set_token("ghp_original")
    page = SettingsPage(fake_orch)
    qtbot.addWidget(page)
    page._load()
    page._save()
    assert token_mod.get_token() == "ghp_original"


def test_settings_clear_token_removes_it(
    qtbot: QtBot, fake_orch: MagicMock, fake_keyring: _FakeKeyring
) -> None:
    token_mod.set_token("ghp_secret")
    page = SettingsPage(fake_orch)
    qtbot.addWidget(page)
    page._load()
    page._clear_token()
    assert token_mod.get_token() is None
    assert page._token_edit.text() == ""


def test_settings_validate_reports_a_resolution(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    from app.schemas import ResolveData, ResolvedBinaryData

    fake_orch.resolve.return_value = ResolveData(
        llama_server=ResolvedBinaryData(
            path="/x/llama-server", source="system", version="b1", valid=True
        )
    )
    page = SettingsPage(fake_orch)
    qtbot.addWidget(page)
    page._validate()
    assert "/x/llama-server" in page._status_label.text()


def test_settings_validate_reports_a_failure(
    qtbot: QtBot, fake_orch: MagicMock
) -> None:
    from app.schemas import ResolveData, ResolvedBinaryData

    fake_orch.resolve.return_value = ResolveData(
        llama_server=ResolvedBinaryData(valid=False, error="wrong architecture")
    )
    page = SettingsPage(fake_orch)
    qtbot.addWidget(page)
    page._validate()
    assert "wrong architecture" in page._status_label.text()
