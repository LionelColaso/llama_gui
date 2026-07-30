from __future__ import annotations

from contextlib import suppress

import keyring

SERVICE = "llama-gui"
KEY = "github-token"


def get_token() -> str | None:
    with suppress(Exception):
        val = keyring.get_password(SERVICE, KEY)
        return val if val else None
    return None


def set_token(token: str) -> None:
    # Keyring may not have a usable backend on all platforms
    # (e.g. Linux without a desktop keyring, headless macOS).
    # Fail softly so the GUI can still function without token storage.
    with suppress(Exception):
        keyring.set_password(SERVICE, KEY, token)


def delete_token() -> None:
    with suppress(Exception):
        keyring.delete_password(SERVICE, KEY)
