"""Allow 'python3 -m lib' as an alternative to the ./cpp launcher."""

import sys

from .cli import main

sys.exit(main())
