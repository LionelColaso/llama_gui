from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "build"

#: Submodule paths declared in .gitmodules. Initializing these is what makes
#: the from-source build possible; without it vendor/llama.cpp does not exist.
_SUBMODULES = ["vendor/llama.cpp", "vendor/llama-swap"]


# ─── Git submodules ─────────────────────────────────────────────────────────


def _init_submodules() -> None:
    """Run ``git submodule update --init --recursive`` so vendored sources exist.

    A build from source is impossible until the C++/Go sources are checked out;
    this is the wiring that ``.gitmodules`` alone does not provide.
    """
    print("==> Ensuring git submodules are initialized...", file=sys.stderr)
    try:
        subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=ROOT,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"    WARNING: submodule init failed: {e}", file=sys.stderr)
        print(
            "    Build from source will fail unless the submodules already exist.",
            file=sys.stderr,
        )


def _vendor_root() -> Path:
    """Locate the ``vendor/`` submodules (matches orchestrator._vendor_root)."""
    candidate = ROOT / "vendor"
    return candidate if candidate.exists() else ROOT / "vendor"


# ─── From-source builds ─────────────────────────────────────────────────────


def _managed_locations(root: str | None) -> tuple[Path, Path]:
    """Resolve (managed_dir, build_dir) for compiled binaries."""
    from llamagui.config import AppConfig

    cfg = AppConfig(root=root) if root else AppConfig()
    return cfg.managed_dir, cfg.root_path / "build"


def _default_buildable_backends() -> list[str]:
    from llamagui.backends import build as build_mod

    return sorted(b for b in build_mod.BUILDABLE_BACKENDS)


def _build_llama_cpp(
    backends: list[str], root: str | None, config: str, cuda_arch: str
) -> None:
    from llamagui.backends import build as build_mod
    from llamagui.backends.build import ToolchainMissing

    src = _vendor_root() / "llama.cpp"
    if not src.exists():
        print(
            "    ERROR: vendor/llama.cpp not found. Re-run without "
            "--skip-submodules so it can be checked out.",
            file=sys.stderr,
        )
        sys.exit(1)

    managed_dir, build_dir = _managed_locations(root)
    for backend in backends:
        print(f"==> Building llama.cpp backend '{backend}'...", file=sys.stderr)
        try:
            data = build_mod.build_backend(
                backend, managed_dir, src, build_dir, config=config, cuda_arch=cuda_arch
            )
        except ToolchainMissing as e:
            print(f"    SKIP '{backend}': {e}", file=sys.stderr)
            continue
        except RuntimeError as e:
            print(f"    FAILED '{backend}': {e}", file=sys.stderr)
            continue
        print(
            f"    OK: {data['name']} ({data['version']}) -> {managed_dir / backend}",
            file=sys.stderr,
        )


def _build_llama_swap(root: str | None) -> None:
    from llamagui.backends import build as build_mod
    from llamagui.backends.build import ToolchainMissing

    src = _vendor_root() / "llama-swap"
    if not src.exists():
        print(
            "    ERROR: vendor/llama-swap not found. Re-run without "
            "--skip-submodules so it can be checked out.",
            file=sys.stderr,
        )
        sys.exit(1)

    managed_dir, build_dir = _managed_locations(root)
    print("==> Building llama-swap...", file=sys.stderr)
    try:
        data = build_mod.build_llama_swap(managed_dir, src, build_dir)
    except ToolchainMissing as e:
        print(f"    SKIP llama-swap: {e}", file=sys.stderr)
        return
    except RuntimeError as e:
        print(f"    FAILED llama-swap: {e}", file=sys.stderr)
        return
    print(
        f"    OK: {data['name']} ({data['version']}) -> {managed_dir / 'llama-swap'}",
        file=sys.stderr,
    )


# ─── Nuitka GUI build ───────────────────────────────────────────────────────


def _build_nuitka_command(product_version: str = "", dev: bool = False) -> list[str]:
    args = [
        "uv",
        "run",
        # Build against the lean build group (nuitka only) without dev/test
        # deps so mypy/pyright/pytest are NOT frozen into the standalone binary.
        "--no-dev",
        "--group",
        "build",
        "python",
        "-m",
        "nuitka",
        "--standalone",
        "--output-filename=llama-gui",
        "--python-flag=-m",
        "--enable-plugin=pyside6",
        "--include-qt-plugins=platforms,imageformats,iconengines,tls",
        "--assume-yes-for-downloads",
        # Link libpython statically so the standalone folder has no external
        # Python DLL dependency (more portable to clean machines).
        "--static-libpython=auto",
        # Keep the frozen binary lean: never bundle the type-checker / test /
        # linter tooling even if it happens to be present in the venv.
        # NOTE: Nuitka 4.x uses --nofollow-import-to (not --exclude-module).
        "--nofollow-import-to=mypy",
        "--nofollow-import-to=pyright",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=pytest_qt",
        "--nofollow-import-to=pytest_mock",
        "--nofollow-import-to=ruff",
        "--nofollow-import-to=nodeenv",
        f"--output-dir={OUT_DIR}",
        "--remove-output",
    ]

    system = platform.system()
    if system == "Windows":
        if dev:
            args.append("--windows-console-mode=force")
        else:
            args.append("--windows-console-mode=disable")
        if product_version:
            args.append("--file-description=llama-gui")
            args.append(f"--product-version={product_version}")
        icon = ROOT / "resources" / "icon.ico"
        if icon.exists():
            args.append(f"--windows-icon-from-ico={icon}")
    elif system == "Darwin":
        # On macOS produce an .app bundle
        # so the bundle gets a proper Info.plist and macOS can launch it.
        args.append("--macos-create-app-bundle")
    else:
        # POSIX builds always standalone (mirrors nuitka-project-else in __main__.py)
        pass

    # Compile the package directory with --python-flag=-m so the built
    # executable behaves like `python -m llamagui` (correct relative imports).
    args.append(str(ROOT / "llamagui"))
    return args


def _build_gui(product_version: str, dev: bool) -> int:
    print(
        f"==> Building llama-gui with Nuitka... (platform: {platform.system()})",
        file=sys.stderr,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = _build_nuitka_command(product_version=product_version, dev=dev)
    print(f"    Command: {' '.join(cmd)}", file=sys.stderr)
    try:
        result = subprocess.run(cmd, cwd=ROOT, check=False)
    except FileNotFoundError as e:
        print(f"    FAILED: {e}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"    FAILED: Nuitka build exited {result.returncode}", file=sys.stderr)
        return 1
    print("    Build completed successfully!", file=sys.stderr)
    return 0


def _find_built_exe() -> Path | None:
    """Locate the built executable across standalone folder and macOS .app bundle.

    Nuitka names the standalone output directory after the compiled top-level
    package (``llamagui`` -> ``llamagui.dist``), not after --output-filename.
    """
    if platform.system() == "Windows":
        exe = OUT_DIR / "llamagui.dist" / "llama-gui.exe"
        return exe if exe.is_file() else None
    if platform.system() == "Darwin":
        bundle = OUT_DIR / "llama-gui.app"
        if bundle.is_dir():
            macos_exe = bundle / "Contents" / "MacOS" / "llama-gui"
            return macos_exe if macos_exe.is_file() else None
        standalone = OUT_DIR / "llamagui.dist" / "llama-gui"
        return standalone if standalone.is_file() else None
    standalone = OUT_DIR / "llamagui.dist" / "llama-gui"
    return standalone if standalone.is_file() else None


def _post_build_verify() -> None:
    built = _find_built_exe()
    if built is None:
        print(f"    WARNING: built executable not found at {OUT_DIR}", file=sys.stderr)
        return

    print(f"==> Post-build verify: {built}", file=sys.stderr)
    try:
        result = subprocess.run(
            [str(built), "describe", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            print("    OK: describe --json exited 0", file=sys.stderr)
        else:
            print(
                f"    WARNING: describe --json exited {result.returncode}\n"
                f"    stderr: {result.stderr.strip()[:500]}",
                file=sys.stderr,
            )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"    WARNING: post-build verify failed: {e}", file=sys.stderr)


# ─── CLI ────────────────────────────────────────────────────────────────────


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build llama-gui (standalone Nuitka executable) and, optionally, the "
            "vendored llama.cpp / llama-swap sources from the git submodules."
        )
    )
    parser.add_argument(
        "--product-version",
        default="",
        help='Set product version for the GUI build (e.g. "0.1.0.0")',
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable dev mode (console window visible on Windows for the GUI build)",
    )
    parser.add_argument(
        "--build-llama-cpp",
        nargs="*",
        metavar="BACKEND",
        help=(
            "Build llama.cpp backends (vulkan/cuda13/cuda12/cpu/metal) from "
            "vendor/llama.cpp. With no backend names, build all that are buildable "
            "on this platform."
        ),
    )
    parser.add_argument(
        "--build-llama-swap",
        action="store_true",
        help="Build llama-swap from vendor/llama-swap.",
    )
    parser.add_argument(
        "--skip-submodules",
        action="store_true",
        help="Do not run 'git submodule update --init --recursive' before a source build.",
    )
    parser.add_argument(
        "--skip-gui-build",
        action="store_true",
        help="Skip the Nuitka standalone GUI build (only build the submodules).",
    )
    parser.add_argument(
        "--config",
        default="Release",
        help="CMake build type for llama.cpp (default: Release).",
    )
    parser.add_argument(
        "--cuda-arch",
        default=None,
        help="CUDA architecture for CUDA builds (default: 75). Never use 61 (Pascal).",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Managed root to install compiled binaries into (default: app default).",
    )
    return parser


def main() -> int:
    args = make_parser().parse_args()

    building_source = args.build_llama_cpp is not None or args.build_llama_swap
    if building_source and not args.skip_submodules:
        _init_submodules()

    rc = 0
    if args.build_llama_cpp is not None:
        from llamagui.backends.build import DEFAULT_CUDA_ARCH

        backends = args.build_llama_cpp or _default_buildable_backends()
        cuda_arch = args.cuda_arch or DEFAULT_CUDA_ARCH
        _build_llama_cpp(backends, args.root, args.config, cuda_arch)
    if args.build_llama_swap:
        _build_llama_swap(args.root)

    if not args.skip_gui_build:
        rc = _build_gui(args.product_version, args.dev)
        if rc == 0:
            _post_build_verify()

    print(f"\nBuild output: {OUT_DIR}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
