# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Entry point for ``python -m socks``.

``python -m socks [--fake] ...`` starts the GUI; ``python -m socks cli ...`` runs the
command-line tool (same as ``socks-cli ...``).
"""

import sys


def main() -> int:
    """Dispatch to the CLI or the GUI; heavy modules are imported lazily."""
    argv = sys.argv[1:]
    if argv[:1] == ["cli"]:
        from socks.core.cli import main as cli_main

        return cli_main(argv[1:])
    from socks.gui.app import main as gui_main

    result = gui_main()
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
