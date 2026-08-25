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

from src import ratelimit  # noqa: E402
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


def test_used_weight_header_is_recorded_on_every_answer():
    """Binance tells us to watch X-MBX-USED-WEIGHT-1M; before the bans we never
    read it, so a neighbour filling the shared IP's budget was invisible."""
    ratelimit._peak_weight = 0
    check("/api/v3/klines", 200, "[]", {"X-MBX-USED-WEIGHT-1M": "38"})
    assert ratelimit.peak_weight() == 38
    check("/api/v3/klines", 200, "[]", {"x-mbx-used-weight-1m": "1200"})
    assert ratelimit.peak_weight() == 1200, "must keep the PEAK, not the last"
    check("/api/v3/klines", 200, "[]", {"X-MBX-USED-WEIGHT-1M": "40"})
    assert ratelimit.peak_weight() == 1200


def test_weight_report_flags_traffic_that_cannot_be_ours():
    ratelimit._peak_weight = 0
    assert ratelimit.weight_report() is None, "silent when Binance told us nothing"
    ratelimit._peak_weight = 45                     # our own cycle, nothing to see
    assert "‼️" not in ratelimit.weight_report()
    ratelimit._peak_weight = 2500                   # 80x our whole cycle
    assert "‼️" in ratelimit.weight_report()
    ratelimit._peak_weight = 0


def test_a_junk_weight_header_is_ignored_not_fatal():
    ratelimit._peak_weight = 0
    check("/api/v3/klines", 200, "[]", {"X-MBX-USED-WEIGHT-1M": "n/a"})
    check("/api/v3/klines", 200, "[]", {})
    assert ratelimit.peak_weight() == 0


def test_the_furthest_deadline_wins():
    """Binance sends Retry-After on a 418 too. Coming back on the shorter of the
    two would re-arm the ban, so the later deadline must win."""
    body_until = int((time.time() + 60) * 1000)
    body = f'{{"code":-1003,"msg":"IP banned until {body_until}."}}'
    try:
        check("/api/v3/account", 418, body, {"Retry-After": "600"})
    except BinanceBanned as exc:
        assert exc.seconds_left > 500, f"took the shorter deadline: {exc.seconds_left}"
    else:
        raise AssertionError("a 418 must raise BinanceBanned")


def test_a_crowded_ip_is_yielded_to_but_never_costs_the_cycle():
    """Measured in prod: the shared IP sat at 5890/6000 while our cycle costs
    ~38. We wait for the minute to roll, but a missed take-profit costs real
    money and a neighbour's noise must never skip the run."""
    ratelimit._last_weight = 5890
    slept, pinged = [], []
    orig_sleep, orig_get = ratelimit.time.sleep, ratelimit.net.get

    class _Resp:
        status_code, text = 200, "{}"
        headers = {"X-MBX-USED-WEIGHT-1M": "5900"}   # never drains

    ratelimit.time.sleep = lambda s: slept.append(s)
    ratelimit.net.get = lambda *a, **k: pinged.append(1) or _Resp()
    try:
        ratelimit._yield_to_a_crowded_ip()           # must RETURN, not raise
        assert len(slept) == ratelimit._BACKOFF_TRIES, f"waited {len(slept)}x"
        assert len(pinged) == ratelimit._BACKOFF_TRIES, "each wait re-checks"
    finally:
        ratelimit.time.sleep, ratelimit.net.get = orig_sleep, orig_get
        ratelimit._last_weight = 0


def test_a_quiet_ip_is_not_waited_on():
    ratelimit._last_weight = 40                      # just our own cycle
    slept = []
    orig, ratelimit.time.sleep = ratelimit.time.sleep, lambda s: slept.append(s)
    try:
        ratelimit._yield_to_a_crowded_ip()
        assert slept == [], "a quiet IP must cost no delay at all"
    finally:
        ratelimit.time.sleep = orig
        ratelimit._last_weight = 0


def test_last_weight_is_the_latest_reading_not_the_peak():
    ratelimit._peak_weight = ratelimit._last_weight = 0
    check("/api/v3/klines", 200, "[]", {"X-MBX-USED-WEIGHT-1M": "5900"})
    check("/api/v3/klines", 200, "[]", {"X-MBX-USED-WEIGHT-1M": "60"})
    assert ratelimit.last_weight() == 60, "the back-off decision needs the LATEST"
    assert ratelimit.peak_weight() == 5900, "the report still shows the worst"
    ratelimit._peak_weight = ratelimit._last_weight = 0


def test_ban_patience_is_a_budget_for_the_whole_run_not_per_call():
    """BAN_MAX_WAIT_MIN is sized against the 4h cron slot. Raised to 180 it only
    stays safe if the allowance is spent ONCE: preflight sitting out one ban and
    the cycle sitting out another must not add up to more than the cap."""
    ratelimit._ban_sleep_total = 0.0
    slept = []
    orig, ratelimit.time.sleep = ratelimit.time.sleep, lambda s: slept.append(s)
    try:
        cap = 100 * 60
        first = ratelimit.BinanceBanned(
            "x", until_ms=int((time.time() + 80 * 60) * 1000), status=418)
        assert ratelimit.wait_out(first, cap) is True, "80 min fits in 100"
        second = ratelimit.BinanceBanned(
            "x", until_ms=int((time.time() + 30 * 60) * 1000), status=418)
        assert ratelimit.wait_out(second, cap) is False, \
            "only ~20 min of the run's patience was left — must refuse, not sleep"
        assert len(slept) == 1, "the refused ban must not have been slept through"
    finally:
        ratelimit.time.sleep = orig
        ratelimit._ban_sleep_total = 0.0


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
