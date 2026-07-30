from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel

contract_version = "1"


class ExitCode(enum.IntEnum):
    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    NOT_AVAILABLE = 2
    NETWORK_ERROR = 3
    LOCK_CONFLICT = 4
    BAD_ARGUMENT = 5
    CONTRACT_MISMATCH = 6
    TOOLCHAIN_MISSING = 7


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


class RouterStatusData(BaseModel):
    port: int = 8080
    listening: bool = False


class LlamaSwapStatusData(BaseModel):
    installed: bool = False
    version: str | None = None
    source: str | None = None


class StatusData(BaseModel):
    backends: dict[str, BackendStatusData] = {}
    active: str | None = None
    junction_target: str | None = None
    router: RouterStatusData = RouterStatusData()
    llama_swap: LlamaSwapStatusData = LlamaSwapStatusData()
    config_present: bool = False
    resolved: dict[str, ResolvedBinaryData] = {}


class InstallResultItem(BaseModel):
    name: str
    status: str
    version: str | None = None
    bytes: int | None = None


class InstallData(BaseModel):
    release: str | None = None
    results: list[InstallResultItem] = []
    llama_swap: dict[str, Any] = {}
    summary: dict[str, int] = {}


class ResolveData(BaseModel):
    llama_server: ResolvedBinaryData = ResolvedBinaryData()
    llama_swap: ResolvedBinaryData = ResolvedBinaryData()


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


class BuildData(BaseModel):
    name: str
    status: str
    version: str | None = None
    source: str | None = None


class DescribeData(BaseModel):
    backends: list[BackendInfo] = []
    supported_sources: list[str] = []
    available_actions: list[str] = []
    defaults: dict[str, Any] = {}
    valid_exit_codes: list[int] = []


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
