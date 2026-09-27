#!/usr/bin/env python3
"""Backwards-compatible entry point: ``python3 video_archiver.py <command> ...``.

The implementation lives in the ``marion_stokes`` package. See AGENTS.md.
"""

import sys

from marion_stokes.cli import main

if __name__ == "__main__":
    sys.exit(main())
