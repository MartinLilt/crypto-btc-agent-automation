"""Regression tests for the egress path (src/net.py).

Three IP bans in three days — 2026-08-23, -24 and -25 — none of them earned by
our traffic. Binance bans by IP, and Railway's static outbound IPs are shared
with other customers by their own admission, so a neighbour's spending gets the
address banned and every account behind it locked out. The fix is an egress we
own; these tests pin the two properties that make routing through it safe.

The dangerous one is the fallback. When the proxy is down we would rather go
out of the shared Railway IP than lose the cycle — but ONLY when we can prove
the request was never delivered. A read timeout does not prove that: the order
may well have reached Binance, and replaying a POST /api/v3/order would buy the
bag twice with real money. So exactly two errors are replayable, and the test
below is what keeps somebody from widening that later.

No network: the session is stubbed.

Run standalone (no pytest needed):
    python tests/test_net.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import net  # noqa: E402
from src.config import config as _real_config  # noqa: E402

PROXY = "http://bot:secret@203.0.113.9:8443"


class _Recorder:
    """Stands in for the requests.Session, remembering how each call went out."""

    def __init__(self, raises=None, raise_times=1):
        self.calls, self._raises, self._left = [], raises, raise_times

    def request(self, method, url, **kw):
        self.calls.append(kw.get("proxies"))
        if self._raises is not None and self._left > 0:
            self._left -= 1
            raise self._raises
        return f"response for {method} {url}"


def _arrange(recorder, *, proxy=PROXY, fallback=True, dedicated=False):
    """Point net at a stub session and a chosen proxy config; returns a undo."""
    old_session, old_cfg = net._session, net.config
    old_down, old_err = net._proxy_down, net._proxy_error
    net._session = recorder
    net.config = replace(_real_config, binance_proxy_url=proxy,
                         binance_proxy_fallback=fallback,
                         egress_dedicated=dedicated)
    net._proxy_down, net._proxy_error = False, None

    def undo():
        net._session, net.config = old_session, old_cfg
        net._proxy_down, net._proxy_error = old_down, old_err
    return undo


def test_binance_traffic_leaves_through_our_own_egress():
    rec = _Recorder()
    undo = _arrange(rec)
    try:
        net.get("https://api.binance.com/api/v3/ping")
        assert rec.calls == [{"http": PROXY, "https": PROXY}], \
            f"the request did not go through the proxy: {rec.calls}"
        assert net.on_own_egress() is True
    finally:
        undo()


def test_no_proxy_configured_is_exactly_the_old_direct_behaviour():
    rec = _Recorder()
    undo = _arrange(rec, proxy="")
    try:
        net.get("https://api.binance.com/api/v3/ping")
        assert rec.calls == [None], "an empty proxy URL must not alter the call"
        assert net.on_own_egress() is False
    finally:
        undo()


def test_running_on_our_own_box_needs_no_proxy_and_says_so():
    """After the move off Railway the bot sits ON the dedicated address.

    There is no hop to make and nothing to fall back to, so the call must go out
    plain — but the report must NOT keep calling the address shared, because
    that line is the only proof we read after every cycle.
    """
    rec = _Recorder()
    undo = _arrange(rec, proxy="", dedicated=True)
    try:
        net.get("https://api.binance.com/api/v3/ping")
        assert rec.calls == [None], f"no proxy should be used: {rec.calls}"
        assert net.on_own_egress() is True
        assert "own dedicated IP" in net.report(), net.report()
    finally:
        undo()


def test_an_unreachable_proxy_falls_back_rather_than_losing_the_cycle():
    rec = _Recorder(raises=requests.exceptions.ProxyError("refused"))
    undo = _arrange(rec)
    try:
        net.get("https://api.binance.com/api/v3/ping")
        assert rec.calls == [{"http": PROXY, "https": PROXY}, None], \
            f"expected a proxied try then a direct one, got {rec.calls}"
    finally:
        undo()


def test_a_dead_proxy_is_not_retried_for_the_rest_of_the_cycle():
    """One process = one cron run. Paying a 10s connect timeout on every call of
    a cycle that makes a dozen is how a dead proxy turns into a missed slot."""
    rec = _Recorder(raises=requests.exceptions.ConnectTimeout("no route"),
                    raise_times=1)
    undo = _arrange(rec)
    try:
        net.get("https://api.binance.com/api/v3/ping")
        net.get("https://api.binance.com/api/v3/klines")
        assert rec.calls == [{"http": PROXY, "https": PROXY}, None, None], \
            f"the dead proxy was tried again: {rec.calls}"
        assert net.on_own_egress() is False
    finally:
        undo()


def test_a_read_timeout_is_never_replayed_because_the_order_may_have_landed():
    """THE one that protects real money. ProxyError and ConnectTimeout prove the
    tunnel never came up, so nothing was delivered. A ReadTimeout proves nothing
    of the sort — the POST /api/v3/order may already be filled, and sending it
    again buys the bag twice. It must propagate, not fall back."""
    rec = _Recorder(raises=requests.exceptions.ReadTimeout("no answer"))
    undo = _arrange(rec)
    try:
        try:
            net.request("POST", "https://api.binance.com/api/v3/order")
        except requests.exceptions.ReadTimeout:
            assert len(rec.calls) == 1, \
                f"a read timeout was replayed — double order risk: {rec.calls}"
            return
        raise AssertionError("a read timeout must propagate, not be swallowed")
    finally:
        undo()


def test_fallback_can_be_switched_off_for_a_whitelisted_key():
    """With the Binance key whitelisted to the proxy IP alone, going out of the
    Railway address earns -2015 anyway. Then failing loudly beats pretending."""
    rec = _Recorder(raises=requests.exceptions.ProxyError("refused"))
    undo = _arrange(rec, fallback=False)
    try:
        try:
            net.get("https://api.binance.com/api/v3/ping")
        except requests.exceptions.ProxyError:
            assert len(rec.calls) == 1, "must not have tried the direct route"
            return
        raise AssertionError("fallback is off — the error must propagate")
    finally:
        undo()


def test_the_report_names_the_address_the_cycle_actually_used():
    rec = _Recorder()
    undo = _arrange(rec)
    try:
        assert "own dedicated IP" in net.report()
    finally:
        undo()

    rec = _Recorder(raises=requests.exceptions.ProxyError("refused"))
    undo = _arrange(rec)
    try:
        net.get("https://api.binance.com/api/v3/ping")
        line = net.report()
        assert "unreachable" in line and "shared Railway IP" in line, line
    finally:
        undo()


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
