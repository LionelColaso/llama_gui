"""Data-driven catalogue of every ``llama-server`` command-line option.

Public API re-exported from the split catalogue and helpers modules so
``from llamagui.serverargs import ...`` continues to work unchanged.
"""

from ._catalogue import (
    DEDICATED_FLAGS,
    SECTIONS,
    SERVER_ARGS,
    ArgKind,
    ServerArg,
)
from ._helpers import (
    count,
    find_arg,
    options_to_cli,
    validate_options,
    validate_value,
)

__all__ = [
    "DEDICATED_FLAGS",
    "SECTIONS",
    "SERVER_ARGS",
    "ArgKind",
    "ServerArg",
    "count",
    "find_arg",
    "options_to_cli",
    "validate_options",
    "validate_value",
]
