import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        from llamagui.gui.app import run as run_gui

        run_gui()
    else:
        from llamagui.cli import main as cli_main

        sys.exit(cli_main(list(argv)))


if __name__ == "__main__":
    main()
