import sys
from collections.abc import Sequence

from loguru import logger

from app.applog import configure_logging, install_excepthook
from app.paths import default_root


def main(argv: Sequence[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    # Every error (including uncaught crashes) is written to
    # <data-root>/logs/llamagui.log — the GUI has no console to inspect.
    configure_logging(default_root() / "logs")
    install_excepthook()
    logger.info("llamagui starting (argv={})", list(argv))

    if len(argv) == 0:
        from app.gui.bootstrap import run as run_gui

        run_gui()
    else:
        from app.cli import main as cli_main

        try:
            code = cli_main(list(argv))
        except SystemExit:
            # argparse's error() exits directly; drain before re-raising so
            # the argument-error record is not lost with the process.
            logger.complete()
            raise
        # Drain the enqueued log queue so a final failure line is not lost
        # when the interpreter exits.
        logger.complete()
        sys.exit(code)


if __name__ == "__main__":
    main()  # pragma: no cover
