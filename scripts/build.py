from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "build"


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
        description="Build llama-gui (standalone Nuitka executable)."
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
    return parser


def main() -> int:
    args = make_parser().parse_args()
    rc = _build_gui(args.product_version, args.dev)
    if rc == 0:
        _post_build_verify()

    print(f"\nBuild output: {OUT_DIR}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
