from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "build"


def _build_nuitka_command(
    product_version: str = "",
    dev: bool = False,
) -> list[str]:
    args = [
        "uv",
        "run",
        "python",
        "-m",
        "nuitka",
        "--standalone",
        "--output-filename=llama-gui",
        "--python-flag=-m",
        "--enable-plugin=pyside6",
        "--include-qt-plugins=platforms,imageformats,iconengines,tls",
        "--assume-yes-for-downloads",
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
    else:
        # POSIX builds always standalone (mirrors nuitka-project-else in __main__.py)
        pass

    # Compile the package directory with --python-flag=-m so the built
    # executable behaves like `python -m llamagui` (correct relative imports).
    args.append(str(ROOT / "llamagui"))
    return args


def _post_build_verify() -> None:
    exe_name = "llama-gui.exe" if platform.system() == "Windows" else "llama-gui"
    candidates = list(OUT_DIR.rglob(exe_name))
    if not candidates:
        print(f"    WARNING: built executable not found at {OUT_DIR}", file=sys.stderr)
        return

    built = candidates[0]
    print(f"==> Post-build verify: {built}", file=sys.stderr)
    try:
        # The CLI has no --version flag; use the describe action (fast, no network).
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build llama-gui into a standalone executable using Nuitka (cross-platform)."
    )
    parser.add_argument(
        "--product-version",
        default="",
        help='Set product version (e.g. "0.1.0.0")',
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable dev mode (console window visible on Windows)",
    )
    args = parser.parse_args()

    print(
        f"==> Building llama-gui with Nuitka... (platform: {platform.system()})",
        file=sys.stderr,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = _build_nuitka_command(product_version=args.product_version, dev=args.dev)
    print(f"    Command: {' '.join(cmd)}", file=sys.stderr)

    try:
        result = subprocess.run(cmd, cwd=ROOT, check=False)
        if result.returncode != 0:
            print(
                f"    FAILED: Nuitka build exited {result.returncode}", file=sys.stderr
            )
            return 1
        print("    Build completed successfully!", file=sys.stderr)
    except FileNotFoundError as e:
        print(f"    FAILED: {e}", file=sys.stderr)
        return 1

    _post_build_verify()

    print(f"\nBuild output: {OUT_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
