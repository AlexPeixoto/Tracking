"""Entry point for the frozen build (PyInstaller needs a script, not `-m tracking`)."""

import sys

from tracking.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
