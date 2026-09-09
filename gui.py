"""Phoenix Desktop GUI - double-click launcher.

Usage:
    python gui.py          open the futuristic desktop GUI
    python gui.py --selftest   offline self-check

The main CLI stays at main.py; this is the GUI entrypoint.
"""

import sys

if __name__ == "__main__":
    sys.path.insert(0, ".")
    from futuristic_gui import main, selftest

    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
