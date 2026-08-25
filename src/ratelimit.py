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

from . import net

# Weight-1 endpoint. The cheapest possible way to ask "is this IP allowed to
# talk to Binance right now?" before spending real weight on the cycle.
_PING_URL = "https://api.binance.com/api/v3/ping"
_PING_TIMEOUT = 10

# "IP banned until 1787546698129" — epoch milliseconds.
_UNTIL_RE = re.compile(r"banned until (\d+)")

# Small cushion so we never come back a hair early and re-arm the ban.
_WAKE_MARGIN = 5.0

# The IP budget, straight from /api/v3/exchangeInfo -> rateLimits:
# REQUEST_WEIGHT, 6000 per 1 MINUTE. Binance: "Every request will contain
# X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter) in the response headers which
# has the current used weight for the IP for all request rate limiters defined."
# We never read it before the bans — so we had no way to see the pressure build.
WEIGHT_LIMIT_1M = 6000
# A whole cycle of ours costs ~30. Anything past this is somebody else on the
# same shared Railway egress IP, and it is the early warning for the next ban.
_FOREIGN_TRAFFIC_ALARM = 300

# Measured in production 2026-08-24 08:21 UTC: the shared egress IP was sitting
# at 5890/6000 while our whole cycle costs ~38. Firing into a bucket that full
# is what earns the 429 — and the 429 is what earns the ban. Above this mark we
# stand aside for a moment and let the rolling minute drain.
_WEIGHT_BACKOFF_AT = 0.85
_BACKOFF_WAIT = 20.0
_BACKOFF_TRIES = 3

_peak_weight = 0
_last_weight = 0
# Ban-sitting is budgeted for the WHOLE run, not per call. BAN_MAX_WAIT_MIN is
# sized against the 4h cron slot, and a per-call cap silently allowed several
# waits to stack: preflight sits out one ban, the cycle hits another, and the
# run is still going when Railway wants to start the next slot — which it then
# skips. One budget, spent once.
_ban_sleep_total = 0.0


def note(headers) -> None:
    """Record the IP's used weight from a response. Cheap, and the only way to
    see a neighbour filling the budget before Binance shuts the address down."""
    global _peak_weight, _last_weight
    if not headers:
        return
    raw = headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m")
    if raw is None:
        return
    try:
        _last_weight = int(raw)
    except (TypeError, ValueError):
        return
    _peak_weight = max(_peak_weight, _last_weight)


def peak_weight() -> int:
    """Highest used-weight seen on this IP during the run (0 = never reported)."""
    return _peak_weight


def last_weight() -> int:
    """Most recent used-weight reading — what matters for deciding to hold off."""
    return _last_weight


def weight_report() -> str | None:
    """One console line about IP pressure, or None when there is nothing to say.

    This reading is also how we PROVE the dedicated egress is doing its job: on
    an address that is only ours the peak can never be much above our own ~38,
    because nobody else is spending. A high one there does not mean "a ban may
    follow" — it means the traffic is not going where we think it is.
    """
    if not _peak_weight:
        return None
    line = f"IP weight (1m)    : {_peak_weight}/{WEIGHT_LIMIT_1M} peak"
    if _peak_weight < _FOREIGN_TRAFFIC_ALARM:
        return line
    if net.on_own_egress():
        return line + ("  ‼️ this is NOT our ~38 and the address is supposed to "
                       "be ours alone — the proxy is not carrying the traffic, "
                       "or its IP is not dedicated")
    return line + ("  ‼️ far above our ~38 — foreign traffic on this shared "
                   "egress IP, a ban may follow")


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


def _retry_after_ms(headers) -> int | None:
    """`Retry-After` as an absolute epoch-ms deadline. Binance sends it on BOTH
    418 and 429: "the number of seconds required to wait, in the case of a 429,
    to prevent a ban, or, in the case of a 418, until the ban is over"."""
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return int((time.time() + float(raw)) * 1000)
    except (TypeError, ValueError):
        return None


def check(path: str, status: int, text: str, headers=None) -> None:
    """Raise BinanceBanned for a 418/429 answer. Any other status is not ours.

    Runs on EVERY response so the used-weight header is recorded even when all
    is well — that reading is our only view of the shared IP's pressure.
    """
    note(headers)
    if status not in (418, 429):
        return
    # Trust whichever deadline is further out: the body carries the ban expiry,
    # Retry-After carries the wait, and coming back early re-arms the ban.
    candidates = [t for t in (parse_until(text), _retry_after_ms(headers))
                  if t is not None]
    until = max(candidates) if candidates else None
    raise BinanceBanned(f"{path} -> HTTP {status}: {text[:200]}",
                        until_ms=until, status=status)


def wait_out(exc: BinanceBanned, max_wait_s: float) -> bool:
    """Sleep until the ban lifts, if that fits in `max_wait_s`.

    Returns True when the caller may carry on with the cycle, False when the ban
    outlasts our patience (or has no known end) and the run should bail out
    without sending another request.
    """
    global _ban_sleep_total
    left = exc.seconds_left
    if left <= 0:
        # Expired between the answer and here, or a 429 with no deadline. Give
        # it a breath rather than firing straight back into the limiter.
        if exc.until_ms is None:
            time.sleep(_WAKE_MARGIN)
        return True
    budget = max_wait_s - _ban_sleep_total
    if left > budget:
        return False
    print(f"  ⏸ Binance ban: waiting {left / 60:.1f} min for it to lift "
          f"({budget / 60:.0f} min of patience left this run)")
    time.sleep(left + _WAKE_MARGIN)
    _ban_sleep_total += left + _WAKE_MARGIN
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
            r = net.get(_PING_URL, timeout=_PING_TIMEOUT)
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
        _yield_to_a_crowded_ip()
        return None
    return None


def _yield_to_a_crowded_ip() -> None:
    """Hold off while the shared IP's minute is nearly spent.

    Our ~38 weight is not what fills the bucket, but it can be the request that
    tips it over — and the 429 that follows is what Binance turns into a ban.
    Waiting lets the rolling minute drain. We NEVER skip the cycle over this:
    a missed take-profit costs real money, a wasted 20 seconds does not.
    """
    ceiling = WEIGHT_LIMIT_1M * _WEIGHT_BACKOFF_AT
    for attempt in range(1, _BACKOFF_TRIES + 1):
        if last_weight() < ceiling:
            return
        print(f"  ⏸ shared IP at {last_weight()}/{WEIGHT_LIMIT_1M} — holding "
              f"{_BACKOFF_WAIT:.0f}s for the minute to roll "
              f"({attempt}/{_BACKOFF_TRIES})")
        time.sleep(_BACKOFF_WAIT)
        try:
            r = net.get(_PING_URL, timeout=_PING_TIMEOUT)
        except requests.RequestException:
            return                      # network trouble — let the cycle decide
        note(r.headers)
    print(f"  ⚠ shared IP still at {last_weight()}/{WEIGHT_LIMIT_1M} — "
          f"running anyway rather than losing the cycle")
