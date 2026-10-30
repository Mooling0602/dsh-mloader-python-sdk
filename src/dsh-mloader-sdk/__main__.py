# SPDX-License-Identifier: MIT
"""Entry point for the python -m dsh_mloader_sdk form of the command line."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
