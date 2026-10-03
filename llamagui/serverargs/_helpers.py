"""Lookup indexes and serialisation helpers for the server-args catalogue."""

from collections.abc import Mapping

from ._catalogue import (
    _FALSE,
    _TRUE,
    DEDICATED_FLAGS,
    SERVER_ARGS,
    ArgKind,
    ServerArg,
)

# ─── Lookup index ──────────────────────────────────────────────────────────

_SERVER_ARGS_BY_FLAG: dict[str, ServerArg] = {arg.flag: arg for arg in SERVER_ARGS}
_SERVER_ARGS_BY_ALIAS: dict[str, ServerArg] = {}
for _arg in SERVER_ARGS:
    for _alias in _arg.aliases:
        _SERVER_ARGS_BY_ALIAS.setdefault(_alias, _arg)


def options_to_cli(options: Mapping[str, str]) -> list[str]:
    """Serialize a ``{flag: value}`` map to CLI tokens (catalogue order).

    Dedicated flags (``--host``/``--port``/``--ctx-size``/``--n-gpu-layers``)
    are skipped — :func:`llamagui.lifecycle.build_llama_server_args` emits them
    from the dedicated config fields so there is a single source of truth.
    Blank values are ignored (the flag is omitted so the binary default wins).
    """
    tokens: list[str] = []
    for arg in SERVER_ARGS:
        if arg.flag in DEDICATED_FLAGS:
            continue
        value = options.get(arg.flag)
        if value is None or str(value).strip() == "":
            continue
        tokens.extend(_value_to_cli(arg, str(value)))
    return tokens


def _value_to_cli(arg: ServerArg, value: str) -> list[str]:
    value = value.strip()
    if arg.kind is ArgKind.BOOL:
        lowered = value.lower()
        if lowered in _TRUE:
            return [arg.flag]
        if lowered in _FALSE:
            return [arg.negated] if arg.negated else []
        raise ValueError(f"{arg.flag}: invalid boolean value '{value}' (use on/off)")
    return [arg.flag, value]


def find_arg(name: str) -> ServerArg | None:
    """Resolve a flag or alias to its canonical :class:`ServerArg`."""
    return _SERVER_ARGS_BY_FLAG.get(name) or _SERVER_ARGS_BY_ALIAS.get(name)


def validate_value(arg: ServerArg, value: str) -> str:
    """Normalize a user-supplied value for ``arg``; raises ``ValueError``.

    A blank value stays blank (the flag is omitted). BOOL accepts
    on/true/1/yes and off/false/0/no. INT/FLOAT are parsed numerically.
    CHOICE must be one of the allowed values.
    """
    v = value.strip()
    if not v:
        return ""
    if arg.kind is ArgKind.BOOL:
        if v.lower() in _TRUE:
            return "on"
        if v.lower() in _FALSE:
            return "off"
        raise ValueError(f"{arg.flag}: expected on/off/true/false/1/0, got '{value}'")
    if arg.kind is ArgKind.INT:
        int(v)  # raises ValueError for non-integers
        return v
    if arg.kind is ArgKind.FLOAT:
        float(v)  # raises ValueError for non-numbers
        return v
    if arg.kind is ArgKind.CHOICE:
        if v not in arg.choices:
            raise ValueError(
                f"{arg.flag}: expected one of {', '.join(arg.choices)}, got '{value}'"
            )
        return v
    return v


def validate_options(options: Mapping[str, str]) -> dict[str, str]:
    """Validate every entry; returns ``{flag: error_message}`` for bad ones."""
    errors: dict[str, str] = {}
    for flag, value in options.items():
        arg = find_arg(flag)
        if arg is None:
            errors[flag] = f"unknown option '{flag}'"
            continue
        try:
            validate_value(arg, str(value))
        except ValueError as exc:
            errors[flag] = str(exc)
    return errors


def count() -> dict[str, int]:
    """Per-section counts for the docs / coverage report."""
    totals: dict[str, int] = {}
    for arg in SERVER_ARGS:
        totals[arg.section] = totals.get(arg.section, 0) + 1
    return totals
