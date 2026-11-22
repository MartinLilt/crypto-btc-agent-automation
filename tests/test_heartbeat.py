"""Heartbeat file the box watchdog checks (deploy/server/watchdog.sh, check 5).

    python tests/test_heartbeat.py      (standalone)
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config as config_mod  # noqa: E402
import main as runner  # noqa: E402


def _in_tmp(fn):
    old = config_mod.STATE_DIR
    with tempfile.TemporaryDirectory() as d:
        config_mod.STATE_DIR = Path(d) / "state"
        try:
            return fn(config_mod.STATE_DIR)
        finally:
            config_mod.STATE_DIR = old


def test_mark_ok_writes_utc_timestamp() -> None:
    def run(state: Path):
        runner._mark_ok()
        ts = datetime.fromisoformat((state / "last_ok").read_text().strip())
        assert ts.tzinfo is not None
    _in_tmp(run)


def test_mark_ok_never_raises() -> None:
    def run(state: Path):
        state.parent.mkdir(exist_ok=True)
        state.write_text("a file where the dir should be")
        runner._mark_ok()                 # must print, not raise
    _in_tmp(run)


def test_failed_cycle_does_not_beat() -> None:
    """main() only beats when no account failed: check the source keeps it so."""
    src = Path(runner.__file__).read_text()
    tail = src[src.index("    if failed:"):]
    assert tail.index("else:") < tail.index("_mark_ok()")


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ok — {len(tests)} tests")
