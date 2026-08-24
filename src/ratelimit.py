"""Binance rate-limit / IP-ban handling.

Binance answers HTTP 429 when a request would exceed the per-IP weight budget
and HTTP 418 once it has stopped asking nicely and banned the IP outright. The
418 body carries the expiry:

    {"code":-1003,"msg":"Way too much request weight used; IP banned until
     1787546698129. Please use WebSocket Streams for live updates to avoid bans."}

Two rules follow, and the bot broke both on 2026-08-23 and 2026-08-24:

1. NEVER retry into an active ban. Binance escalates repeat offenders from two
   minutes to three days, and requests sent while banned are exactly what it
   counts. The generic read-retry in main.py fired three /api/v3/account calls
   into a live ban each time.
2. A ban is not a reason to throw the cycle away. It is measured in minutes and
   the cron only comes back in four hours, so waiting it out inside the run
   rescues that cycle's take-profits.

Why we get banned at all is NOT our traffic: one cycle spent ~110 request
weight per FOUR HOURS (20 account + 4x20 exchangeInfo + klines) against a budget
of 6000 per MINUTE — under 2% of a single minute's allowance — and both bans were
already in force when our very first request went out. Railway's static outbound
IPs are shared, in their own words: "There is no guarantee that the IPv4
addresses assigned to your service are dedicated. They may be shared with other
customers." A neighbour hammering Binance from that egress IP is what bans us.
"""

from __future__ import annotations

import re
import time

import requests

# Weight-1 endpoint. The cheapest possible way to ask "is this IP allowed to
# talk to Binance right now?" before spending real weight on the cycle.
_PING_URL = "https://api.binance.com/api/v3/ping"
_PING_TIMEOUT = 10

# "IP banned until 1787546698129" — epoch milliseconds.
_UNTIL_RE = re.compile(r"banned until (\d+)")

# Small cushion so we never come back a hair early and re-arm the ban.
_WAKE_MARGIN = 5.0


class BinanceBanned(RuntimeError):
    """The IP is rate-limited (429) or banned (418). Carries the expiry when
    Binance told us one — `until_ms` is None for a bare 429."""

    def __init__(self, message: str, until_ms: int | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.until_ms = until_ms
        self.status = status

    @property
    def seconds_left(self) -> float:
        """Seconds until the ban lifts. 0 when unknown or already expired."""
        if self.until_ms is None:
            return 0.0
        return max(0.0, self.until_ms / 1000 - time.time())

    def describe(self) -> str:
        if self.until_ms is None:
            return f"HTTP {self.status}: rate-limited, no expiry given"
        mins = self.seconds_left / 60
        return (f"HTTP {self.status}: IP banned for another {mins:.0f} min "
                f"(until {time.strftime('%H:%M:%S UTC', time.gmtime(self.until_ms / 1000))})")


def parse_until(text: str) -> int | None:
    """Epoch-ms expiry out of a 418 body, or None when it carries no deadline."""
    m = _UNTIL_RE.search(text or "")
    return int(m.group(1)) if m else None


def check(path: str, status: int, text: str, headers=None) -> None:
    """Raise BinanceBanned for a 418/429 answer. Any other status is not ours."""
    if status not in (418, 429):
        return
    until = parse_until(text)
    if until is None and headers:
        # A 429 has no body deadline but does carry Retry-After (seconds).
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                until = int((time.time() + float(retry_after)) * 1000)
            except ValueError:
                pass
    raise BinanceBanned(f"{path} -> HTTP {status}: {text[:200]}",
                        until_ms=until, status=status)


def wait_out(exc: BinanceBanned, max_wait_s: float) -> bool:
    """Sleep until the ban lifts, if that fits in `max_wait_s`.

    Returns True when the caller may carry on with the cycle, False when the ban
    outlasts our patience (or has no known end) and the run should bail out
    without sending another request.
    """
    left = exc.seconds_left
    if left <= 0:
        # Expired between the answer and here, or a 429 with no deadline. Give
        # it a breath rather than firing straight back into the limiter.
        if exc.until_ms is None:
            time.sleep(_WAKE_MARGIN)
        return True
    if left > max_wait_s:
        return False
    print(f"  ⏸ Binance ban: waiting {left / 60:.1f} min for it to lift "
          f"(cap {max_wait_s / 60:.0f} min)")
    time.sleep(left + _WAKE_MARGIN)
    return True


def preflight(max_wait_s: float) -> BinanceBanned | None:
    """Weight-1 gate in front of the whole cycle.

    Pings Binance; if the IP is banned, waits the ban out (when it fits in
    `max_wait_s`) and pings once more. Returns None when the coast is clear, or
    the BinanceBanned that made us give up — the caller then reports and exits
    WITHOUT spending another request.
    """
    for attempt in (1, 2):
        try:
            r = requests.get(_PING_URL, timeout=_PING_TIMEOUT)
        except requests.RequestException as exc:
            # Network trouble is not a ban — let the cycle run and hit its own
            # retries, which know how to handle a blip.
            print(f"  ⚠ ban preflight: ping failed ({type(exc).__name__}) — continuing")
            return None
        try:
            check("/api/v3/ping", r.status_code, r.text, r.headers)
        except BinanceBanned as banned:
            print(f"  ⛔ {banned.describe()}")
            if attempt == 2 or not wait_out(banned, max_wait_s):
                return banned
            continue
        return None
    return None
