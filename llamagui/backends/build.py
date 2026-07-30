from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

BUILDABLE_BACKENDS = {"vulkan", "cuda12", "cuda13"}


class ToolchainMissing(Exception):
    def __init__(self, tool: str, hint: str = "") -> None:
        self.tool = tool
        self.hint = hint
        super().__init__(f"Toolchain not found: {tool}. {hint}".strip())


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=120, check=False
        )
    except FileNotFoundError:
        raise ToolchainMissing(cmd[0]) from None
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def detect_msvc() -> str | None:
    if platform.system() != "Windows":
        return None
    prog = os.environ.get("CC") or "cl"
    try:
        out = subprocess.run(
            [prog, "/?"], capture_output=True, text=True, timeout=10, check=False
        )
        if out.returncode == 0:
            return prog
    except FileNotFoundError:
        pass
    pf = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
    vs_where = Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vs_where.exists():
        try:
            find_arg = "VC\\Tools\\MSVC\\*\\bin\\Hostx64\\x64\\cl.exe"
            out = subprocess.run(
                [str(vs_where), "-latest", "-find", find_arg],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            path = out.stdout.strip()
            if path:
                return path
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return None


def detect_c_compiler() -> str | None:
    if platform.system() == "Windows":
        return detect_msvc()
    for prog in ("cc", "gcc", "clang"):
        found = shutil.which(prog)
        if found:
            return found
    return None


def detect_cmake() -> str | None:
    try:
        out = _run(["cmake", "--version"])
        match = re.search(r"cmake\s+version\s+(\S+)", out)
        if match:
            return match.group(1)
    except (ToolchainMissing, RuntimeError):
        pass
    return None


def detect_go() -> str | None:
    try:
        out = _run(["go", "version"])
        match = re.search(r"go(\d+\.\d+\.\d+)", out)
        if match:
            return match.group(1)
    except (ToolchainMissing, RuntimeError):
        pass
    return None


def detect_ninja_or_make() -> str | None:
    for prog in ("ninja", "make", "gmake"):
        found = shutil.which(prog)
        if found:
            return found
    return None


def _exe_suffix() -> str:
    return ".exe" if platform.system() == "Windows" else ""


def get_toolchain_info() -> dict[str, Any]:
    return {
        "c_compiler": detect_c_compiler(),
        "cmake": detect_cmake(),
        "go": detect_go(),
        "ninja_or_make": detect_ninja_or_make(),
    }


def has_toolchain(backend: str) -> bool:
    if not detect_c_compiler():
        return False
    return detect_cmake() is not None


def build_backend(
    backend: str,
    managed_root: Path,
    src_dir: Path,
    build_root: Path,
    config: str = "Release",
) -> dict[str, Any]:
    if not has_toolchain(backend):
        raise ToolchainMissing("C compiler", "Install a C/C++ compiler")

    compiler = detect_c_compiler()
    if not compiler:
        hint = (
            "Install Visual Studio Build Tools with C++ workload"
            if platform.system() == "Windows"
            else "Install gcc or clang from your package manager"
        )
        raise ToolchainMissing("C compiler", hint)

    build_dir = build_root / backend
    build_dir.mkdir(parents=True, exist_ok=True)

    ggml_backend = "vulkan" if backend == "vulkan" else "cuda"
    cmake_args = [
        "cmake",
        "-S",
        str(src_dir),
        "-B",
        str(build_dir),
        f"-DGGML_{ggml_backend.upper()}=ON",
        f"-DCMAKE_BUILD_TYPE={config}",
        "-DLLAMA_CURL=OFF",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DLLAMA_STATIC=ON",
    ]

    ninja_or_make = detect_ninja_or_make()
    if ninja_or_make and "ninja" in Path(ninja_or_make).name:
        cmake_args.append("-G Ninja")

    if not compiler:
        raise ToolchainMissing(
            "MSVC", "Install Visual Studio Build Tools with C++ workload"
        )

    _run(cmake_args)

    build_cmd = ["cmake", "--build", str(build_dir), "--config", config]
    if ninja_or_make and "ninja" in Path(ninja_or_make).name:
        build_cmd.append("--parallel")
    if platform.system() == "Windows":
        cl_path = compiler or ""
        build_cmd.extend(["--", f"/p:CL={cl_path}"])
    _run(build_cmd)

    dest = managed_root / backend
    dest.mkdir(parents=True, exist_ok=True)

    suffix = _exe_suffix()
    binaries = [
        f"llama-server{suffix}",
        f"llama-cli{suffix}",
        f"llama-perplexity{suffix}",
        f"llama-bench{suffix}",
    ]
    copied = 0
    for binary in binaries:
        src_bin = build_dir / f"bin/{config}" / binary
        if not src_bin.exists():
            src_bin = build_dir / "bin" / binary
        if src_bin.exists():
            shutil.copy2(str(src_bin), str(dest / binary))
            copied += 1

    if not copied:
        src_bin = build_dir / f"bin/{config}/llama-server{suffix}"
        if not src_bin.exists():
            src_bin = build_dir / f"bin/llama-server{suffix}"
        if src_bin.exists():
            shutil.copy2(str(src_bin), str(dest / f"llama-server{suffix}"))
            copied = 1

    tag = _get_git_tag(src_dir)
    marker = dest / ".version"
    marker.write_text(f"{tag or 'local'}\nmanaged-build\n", encoding="utf-8")

    return {
        "name": backend,
        "status": "ok",
        "version": tag or "local",
        "bytes": 0,
        "source": "managed-build",
    }


def build_llama_swap(
    managed_root: Path,
    src_dir: Path,
    build_root: Path,
) -> dict[str, Any]:
    go_ver = detect_go()
    if not go_ver:
        raise ToolchainMissing("go", "Install Go from https://go.dev")

    suffix = _exe_suffix()
    out_path = build_root / f"llama-swap{suffix}"
    _run(["go", "build", "-o", str(out_path), "."], cwd=src_dir)

    dest = managed_root / "llama-swap"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(out_path), str(dest / f"llama-swap{suffix}"))

    tag = _get_git_tag(src_dir)
    marker = dest / ".version"
    marker.write_text(f"{tag or 'local'}\nmanaged-build\n", encoding="utf-8")

    return {
        "name": "llama-swap",
        "status": "ok",
        "version": tag or "local",
        "bytes": 0,
        "source": "managed-build",
    }


def _get_git_tag(src_dir: Path) -> str | None:
    try:
        out = _run(["git", "describe", "--tags", "--always", "--dirty"], cwd=src_dir)
        return out
    except (ToolchainMissing, RuntimeError):
        return None
