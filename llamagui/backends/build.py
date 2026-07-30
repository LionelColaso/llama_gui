"""Managed-build source: compile llama.cpp / llama-swap from the submodules.

This path is entirely optional. Every entry point is gated on a toolchain
probe so a machine without a compiler falls back to the prebuilt download
instead of failing. Nothing here is required at runtime: a clone without
submodules still runs from prebuilt, pointed or system binaries.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import BACKENDS, get_backend
from ..paths import exe_name, is_macos, is_windows, platform_key

#: Backends that can be compiled on the running platform.
BUILDABLE_BACKENDS = frozenset(b.name for b in BACKENDS if b.is_buildable())

#: Default CUDA architecture. Never emit ``compute_61``: CUDA >= 13 dropped
#: Pascal, so a 61 target fails to compile with a modern toolkit.
DEFAULT_CUDA_ARCH = "75"

#: Programs produced by llama.cpp that are worth keeping.
_WANTED_BINARIES = ("llama-server", "llama-cli", "llama-bench", "llama-perplexity")

_PROBE_TIMEOUT = 30
#: Compiling llama.cpp takes many minutes; never impose a short timeout.
_BUILD_TIMEOUT = 4 * 60 * 60


class ToolchainMissing(Exception):
    def __init__(self, tool: str, hint: str = "") -> None:
        self.tool = tool
        self.hint = hint
        super().__init__(f"Toolchain not found: {tool}. {hint}".strip())


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = _PROBE_TIMEOUT) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout, check=False
        )
    except FileNotFoundError:
        raise ToolchainMissing(cmd[0]) from None
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Command timed out: {' '.join(cmd)}") from e
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


# ─── Toolchain detection ──────────────────────────────────────────────────


def detect_msvc() -> str | None:
    """Locate ``cl.exe`` through PATH or vswhere (Windows only)."""
    if not is_windows():
        return None
    prog = os.environ.get("CC") or "cl"
    try:
        out = subprocess.run(
            [prog, "/?"], capture_output=True, text=True, timeout=10, check=False
        )
        if out.returncode == 0:
            return prog
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    pf = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
    vs_where = Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vs_where.exists():
        return None
    try:
        out = subprocess.run(
            [
                str(vs_where),
                "-latest",
                "-find",
                "VC\\Tools\\MSVC\\*\\bin\\Hostx64\\x64\\cl.exe",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip().splitlines()[0] if out.stdout.strip() else None


def detect_c_compiler() -> str | None:
    if is_windows():
        return detect_msvc()
    for prog in ("cc", "clang", "gcc"):
        found = shutil.which(prog)
        if found:
            return found
    return None


def _detect_version(cmd: list[str], pattern: str) -> str | None:
    try:
        out = _run(cmd)
    except (ToolchainMissing, RuntimeError):
        return None
    match = re.search(pattern, out)
    return match.group(1) if match else None


def detect_cmake() -> str | None:
    return _detect_version(["cmake", "--version"], r"cmake\s+version\s+(\S+)")


def detect_go() -> str | None:
    return _detect_version(["go", "version"], r"go(\d+\.\d+(?:\.\d+)?)")


def detect_nvcc() -> str | None:
    return _detect_version(["nvcc", "--version"], r"release\s+(\d+\.\d+)")


def detect_vulkan_sdk() -> str | None:
    """Vulkan needs the shader compiler; the SDK env var is the usual hint."""
    sdk = os.environ.get("VULKAN_SDK")
    if sdk and Path(sdk).exists():
        return sdk
    for prog in ("glslc", "glslangValidator"):
        found = shutil.which(prog)
        if found:
            return found
    return None


def detect_xcode() -> str | None:
    if not is_macos():
        return None
    return shutil.which("xcrun")


def detect_ninja_or_make() -> str | None:
    for prog in ("ninja", "make", "gmake"):
        found = shutil.which(prog)
        if found:
            return found
    return None


def get_toolchain_info() -> dict[str, Any]:
    """Everything the Settings page needs to explain what can be built here."""
    return {
        "platform": platform_key(),
        "c_compiler": detect_c_compiler(),
        "cmake": detect_cmake(),
        "go": detect_go(),
        "ninja_or_make": detect_ninja_or_make(),
        "nvcc": detect_nvcc(),
        "vulkan_sdk": detect_vulkan_sdk(),
        "xcode": detect_xcode(),
        "buildable": sorted(name for name in BUILDABLE_BACKENDS if has_toolchain(name)),
    }


def has_toolchain(backend: str) -> bool:
    """True when this machine can compile ``backend`` from source."""
    entry = get_backend(backend)
    if entry is None or not entry.is_buildable():
        return False
    if not detect_c_compiler() or not detect_cmake():
        return False
    if entry.ggml_flag == "CUDA":
        return detect_nvcc() is not None
    if entry.ggml_flag == "VULKAN":
        return detect_vulkan_sdk() is not None
    if entry.ggml_flag == "METAL":
        return detect_xcode() is not None
    return True


# ─── Build ────────────────────────────────────────────────────────────────


def _configure_args(
    backend: str, src_dir: Path, build_dir: Path, config: str, cuda_arch: str
) -> list[str]:
    entry = get_backend(backend)
    args = [
        "cmake",
        "-S",
        str(src_dir),
        "-B",
        str(build_dir),
        f"-DCMAKE_BUILD_TYPE={config}",
        "-DLLAMA_CURL=OFF",
        "-DBUILD_SHARED_LIBS=OFF",
    ]
    if entry is not None and entry.ggml_flag:
        args.append(f"-DGGML_{entry.ggml_flag}=ON")
    if entry is not None and entry.ggml_flag == "CUDA":
        args.append(f"-DCMAKE_CUDA_ARCHITECTURES={cuda_arch}")
    generator = detect_ninja_or_make()
    if generator and "ninja" in Path(generator).name:
        args += ["-G", "Ninja"]
    return args


def _collect_binaries(build_dir: Path, dest: Path, config: str) -> int:
    """Copy the built programs (and any shared libs they need) into ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    search_dirs = [build_dir / "bin" / config, build_dir / "bin", build_dir]
    copied = 0
    for stem in _WANTED_BINARIES:
        for directory in search_dirs:
            candidate = directory / exe_name(stem)
            if candidate.is_file():
                shutil.copy2(candidate, dest / candidate.name)
                copied += 1
                break
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for lib in directory.iterdir():
            if lib.is_file() and lib.suffix in (".dll", ".so", ".dylib"):
                shutil.copy2(lib, dest / lib.name)
    return copied


def build_backend(
    backend: str,
    managed_root: Path,
    src_dir: Path,
    build_root: Path,
    config: str = "Release",
    cuda_arch: str = DEFAULT_CUDA_ARCH,
) -> dict[str, Any]:
    """Compile one backend from ``src_dir`` into ``managed_root/<backend>``."""
    if not has_toolchain(backend):
        raise ToolchainMissing(
            "cmake/compiler",
            f"Cannot build '{backend}' on this machine; download the prebuilt "
            "release instead.",
        )

    build_dir = build_root / backend
    build_dir.mkdir(parents=True, exist_ok=True)

    _run(_configure_args(backend, src_dir, build_dir, config, cuda_arch), timeout=600)
    _run(
        [
            "cmake",
            "--build",
            str(build_dir),
            "--config",
            config,
            "--parallel",
        ],
        timeout=_BUILD_TIMEOUT,
    )

    dest = managed_root / backend
    copied = _collect_binaries(build_dir, dest, config)
    if not copied:
        raise RuntimeError(
            f"Build produced no binaries for '{backend}' (looked in {build_dir})"
        )

    tag = _get_git_tag(src_dir) or "local"
    (dest / ".version").write_text(f"{tag}\nmanaged-build\n", encoding="utf-8")
    return {
        "name": backend,
        "status": "ok",
        "version": tag,
        "bytes": 0,
        "source": "managed-build",
    }


def build_llama_swap(
    managed_root: Path,
    src_dir: Path,
    build_root: Path,
) -> dict[str, Any]:
    """Compile llama-swap with the Go toolchain."""
    if not detect_go():
        raise ToolchainMissing("go", "Install Go from https://go.dev")

    build_root.mkdir(parents=True, exist_ok=True)
    out_path = build_root / exe_name("llama-swap")
    _run(["go", "build", "-o", str(out_path), "."], cwd=src_dir, timeout=_BUILD_TIMEOUT)

    dest = managed_root / "llama-swap"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_path, dest / out_path.name)

    tag = _get_git_tag(src_dir) or "local"
    (dest / ".version").write_text(f"{tag}\nmanaged-build\n", encoding="utf-8")
    return {
        "name": "llama-swap",
        "status": "ok",
        "version": tag,
        "bytes": 0,
        "source": "managed-build",
    }


def _get_git_tag(src_dir: Path) -> str | None:
    try:
        return _run(["git", "describe", "--tags", "--always", "--dirty"], cwd=src_dir)
    except (ToolchainMissing, RuntimeError):
        return None


__all__ = [
    "BUILDABLE_BACKENDS",
    "DEFAULT_CUDA_ARCH",
    "ToolchainMissing",
    "build_backend",
    "build_llama_swap",
    "detect_c_compiler",
    "detect_cmake",
    "detect_go",
    "detect_msvc",
    "detect_ninja_or_make",
    "detect_nvcc",
    "detect_vulkan_sdk",
    "get_toolchain_info",
    "has_toolchain",
]
