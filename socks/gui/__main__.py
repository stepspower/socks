# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""``python -m socks.gui`` — same as ``python -m socks`` without the CLI dispatch."""

import sys

from socks.gui import main

sys.exit(main())
