"""Regression tests for the Binance IP-ban handling (HTTP 418 / -1003).

Twice — 2026-08-23 08:01 and 2026-08-24 04:03 UTC — the live cycle died on

    /api/v3/account -> HTTP 418: {"code":-1003,"msg":"Way too much request
    weight used; IP banned until 1787546698129. ..."}

and both times the generic read-retry fired two MORE requests into the live ban,
which is precisely what Binance escalates on. These tests pin the two things
that must never regress:

  * a 418/429 surfaces as BinanceBanned (not as a generic error the retry loop
    treats like a network blip), with the expiry parsed out of the body;
  * a ban longer than the cap is refused rather than slept through, so the run
    can never overrun the next cron slot.

No network, no sleeping: `wait_out` is exercised with expiries that are already
in the past or far beyond the cap.

Run standalone (no pytest needed):
    python tests/test_ratelimit.py
or, if pytest is installed:
    pytest tests/test_ratelimit.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ratelimit import BinanceBanned, check, parse_until, wait_out  # noqa: E402

# The exact body Binance sent on 2026-08-24.
BAN_BODY = ('{"code":-1003,"msg":"Way too much request weight used; IP banned '
            'until 1787546698129. Please use WebSocket Streams for live updates '
            'to avoid bans."}')


def test_parses_the_expiry_out_of_a_real_418_body():
    assert parse_until(BAN_BODY) == 1787546698129


def test_a_body_without_a_deadline_has_no_expiry():
    assert parse_until('{"code":-1003,"msg":"Too many requests."}') is None
    assert parse_until("") is None


def test_418_raises_banned_with_the_expiry():
    try:
        check("/api/v3/account", 418, BAN_BODY, {})
    except BinanceBanned as exc:
        assert exc.until_ms == 1787546698129
        assert exc.status == 418
    else:
        raise AssertionError("a 418 must raise BinanceBanned")


def test_429_uses_retry_after_when_the_body_carries_no_deadline():
    try:
        check("/api/v3/klines", 429, '{"code":-1003}', {"Retry-After": "30"})
    except BinanceBanned as exc:
        left = exc.seconds_left
        assert 25 <= left <= 31, f"expected ~30s of ban left, got {left}"
    else:
        raise AssertionError("a 429 must raise BinanceBanned")


def test_a_healthy_answer_is_not_a_ban():
    check("/api/v3/ping", 200, "{}", {})          # must not raise
    check("/api/v3/order", 400, '{"code":-1013}', {})   # a filter error is not ours


def test_a_ban_longer_than_the_cap_is_refused_not_slept_through():
    """The cron slot is 4h; a 3-day ban must never be waited out."""
    three_days = BinanceBanned("banned", until_ms=int((time.time() + 3 * 86400) * 1000),
                               status=418)
    started = time.time()
    assert wait_out(three_days, max_wait_s=50 * 60) is False
    assert time.time() - started < 1, "refusing a long ban must be instant"


def test_an_expired_ban_lets_the_cycle_carry_on_immediately():
    stale = BinanceBanned("banned", until_ms=int((time.time() - 60) * 1000), status=418)
    started = time.time()
    assert wait_out(stale, max_wait_s=50 * 60) is True
    assert time.time() - started < 1, "an expired ban must not cost a sleep"


def test_seconds_left_never_goes_negative():
    stale = BinanceBanned("banned", until_ms=int((time.time() - 3600) * 1000), status=418)
    assert stale.seconds_left == 0.0
    assert BinanceBanned("rate-limited").seconds_left == 0.0


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  ✗ {name}: {exc}")
    print("\nall good" if not failures else f"\n{failures} test(s) failed")
    sys.exit(1 if failures else 0)
