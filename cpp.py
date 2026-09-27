#!/usr/bin/env python3
"""Entry point for the ``cpp`` command.

Kept to a thin shim so the real work lives in the ``lib`` package, which the
tests import directly.
"""

from __future__ import annotations

import os
import sys

# When run from a clone (rather than installed), make sure the sibling package
# is importable and that the version is not confused with a system package.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MIN_PYTHON = (3, 6)

if sys.version_info < MIN_PYTHON:
    sys.stderr.write(
        "cpp needs Python %d.%d or newer (this is %s).\n"
        % (MIN_PYTHON[0], MIN_PYTHON[1], sys.version.split()[0])
    )
    raise SystemExit(2)

try:
    from lib.cli import main
except ImportError as exc:  # a broken checkout should say so clearly
    sys.stderr.write("Could not load the cpp setup tool from %s:\n  %s\n" % (_ROOT, exc))
    raise SystemExit(2)

if __name__ == "__main__":
    raise SystemExit(main())
