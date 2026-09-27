import argparse
import logging

import pytest

from marion_stokes.cli import build_parser


@pytest.fixture
def logger():
    log = logging.getLogger("marion_stokes.test")
    log.setLevel(logging.DEBUG)
    return log


@pytest.fixture
def make_args(tmp_path):
    """Build a CLI namespace for a command, pointed at temp paths."""

    def _make(command: str, *extra: str) -> argparse.Namespace:
        return build_parser().parse_args([
            command,
            "--db", str(tmp_path / "videos.db"),
            "--log", "",
            "--feeds", str(tmp_path / "feeds.txt"),
            "--download-dir", str(tmp_path / "downloads"),
            *extra,
        ])

    return _make
