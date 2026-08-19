from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel

contract_version = "4"


class ExitCode(enum.IntEnum):
    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    NOT_AVAILABLE = 2
    NETWORK_ERROR = 3
    LOCK_CONFLICT = 4
    BAD_ARGUMENT = 5
    CONTRACT_MISMATCH = 6


class EngineError(Exception):
    """An engine failure that already knows its contract exit code.

    Every layer (lifecycle, locking, orchestrator) raises this so the CLI and
    the GUI can map a failure to the documented exit code (§9.2) without
    guessing from the exception type.
    """

    def __init__(
        self,
        exit_code: ExitCode,
        message: str,
        log_tail: list[str] | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.log_tail = log_tail
        super().__init__(message)


class ResolvedBinaryData(BaseModel):
    path: str | None = None
    source: str | None = None
    version: str | None = None
    valid: bool = False
    error: str | None = None


class BackendStatusData(BaseModel):
    installed: bool = False
    version: str | None = None
    source: str | None = None
    prebuilt_available: bool = False
    unavailable_reason: str = ""


class ServerStatusData(BaseModel):
    """State of the llama-server process this app manages."""

    host: str = "127.0.0.1"
    port: int = 8080
    listening: bool = False
    pids: list[int] = []
    model: str | None = None


class ModelInfo(BaseModel):
    """One .gguf file in the models directory."""

    name: str
    size_bytes: int = 0
    modified: str = ""


class ModelsData(BaseModel):
    """Contents of the configured models directory."""

    dir: str = ""
    models: list[ModelInfo] = []
    active: str | None = None


class DownloadData(BaseModel):
    """Outcome of a model download."""

    name: str
    path: str = ""
    size_bytes: int = 0


class PendingDownloadInfo(BaseModel):
    """One interrupted download (``.part`` + meta) offered for resume."""

    id: str = ""
    kind: str = "model"  # "model" (.gguf) or "backend" (release archive)
    name: str = ""
    url: str = ""
    dest: str = ""
    part: str = ""
    total: int = 0
    done: int = 0
    percent: float = 0.0


class PendingDownloadsData(BaseModel):
    """All interrupted downloads the app knows about, grouped by source dir."""

    models_dir: str = ""
    downloads_dir: str = ""
    tasks: list[PendingDownloadInfo] = []


class PlatformData(BaseModel):
    system: str = ""
    arch: str = ""
    exe_suffix: str = ""


class StatusData(BaseModel):
    backends: dict[str, BackendStatusData] = {}
    active: str | None = None
    junction_target: str | None = None
    server: ServerStatusData = ServerStatusData()
    models: ModelsData = ModelsData()
    resolved: dict[str, ResolvedBinaryData] = {}
    platform: PlatformData = PlatformData()
    root: str = ""
    config_file: str = ""
    ready: bool = False
    first_run_complete: bool = False


class InstallResultItem(BaseModel):
    name: str
    status: str
    version: str | None = None
    bytes: int | None = None


class InstallData(BaseModel):
    release: str | None = None
    results: list[InstallResultItem] = []
    summary: dict[str, int] = {}


class ResolveData(BaseModel):
    llama_server: ResolvedBinaryData = ResolvedBinaryData()


class SwitchData(BaseModel):
    backend: str | None = None
    active_before: str | None = None
    active_after: str | None = None
    auto_installed: bool = False


class StopData(BaseModel):
    stopped_pids: list[int] = []
    port_free: bool = False
    still_listening: bool = False
    unknown_holder: bool = False


class AssetInfo(BaseModel):
    name: str
    size: int
    flag: str


class ListAssetsData(BaseModel):
    release: str | None = None
    assets: list[AssetInfo] = []


class BackendInfo(BaseModel):
    name: str
    notes: str = ""
    needs_cudart: bool = False
    prebuilt_available: bool = False
    unavailable_reason: str = ""


class BootstrapData(BaseModel):
    """Outcome of the "make it work out of the box" first-run action."""

    performed: list[str] = []
    skipped: list[str] = []
    backend: str | None = None
    llama_cpp_version: str | None = None
    ready: bool = False
    message: str = ""


class ConfigData(BaseModel):
    """The saved settings, plus where they are stored."""

    config_file: str = ""
    values: dict[str, Any] = {}
    warnings: list[str] = []


class DescribeData(BaseModel):
    backends: list[BackendInfo] = []
    supported_sources: list[str] = []
    available_actions: list[str] = []
    defaults: dict[str, Any] = {}
    valid_exit_codes: list[int] = []
    platform: PlatformData = PlatformData()


class Envelope(BaseModel):
    contract_version: str
    ok: bool
    exit_code: int
    action: str
    root: str
    timestamp: str
    duration_ms: int
    data: Any = None
    error: str | None = None
    log_tail: list[str] | None = None
    warnings: list[str] = []
