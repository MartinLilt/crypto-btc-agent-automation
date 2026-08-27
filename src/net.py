"""The address Binance sees us as.

Binance counts request weight and hands out bans PER IP, never per key: "all
connections from a banned IP address are blocked, affecting all accounts using
that IP". Railway's static outbound IPs are not ours alone — from their own
docs: "There is no guarantee that the IPv4 addresses assigned to your service
are dedicated. They may be shared with other customers."

That is the whole story behind the bans of 2026-08-23, -24 and -25. Every one
of them was ALREADY in force when the cycle's very first request went out, and
one cycle spends ~38 weight per four hours against a budget of 6000 per minute
— 0.6% of a single minute. A neighbour on the shared address burns the budget,
Binance bans the address, we are collateral.

Nothing on Railway fixes this: there is no dedicated-IP setting, and disabling
static IPs only trades a known shared address for an unknown one. `data-api
.binance.vision` is not an escape either — measured 2026-08-24, it shares the
same weight counter as api.binance.com.

So Binance traffic leaves through an egress WE own (BINANCE_PROXY_URL), where
the only weight on the counter is our own. Empty → direct, exactly as before.

Telegram deliberately does NOT go through here. When the proxy is the thing
that broke, the alert saying so still has to reach the owner.
"""

from __future__ import annotations

import requests

from .config import config

_TIMEOUT = 10

# One process = one cron cycle, so this only ever remembers within a single run:
# once the proxy has proved unreachable, stop paying its connect timeout on
# every remaining call of the cycle.
_proxy_down = False
_proxy_error: str | None = None

_session = requests.Session()


def on_own_egress() -> bool:
    """True when this cycle's Binance traffic is leaving through our own IP.

    Two ways to be there: the bot runs ON the box we own (EGRESS_DEDICATED, no
    hop at all), or it runs elsewhere and tunnels through that box's proxy.
    """
    if config.egress_dedicated and not config.binance_proxy_url:
        return True
    return bool(config.binance_proxy_url) and not _proxy_down


def _proxies() -> dict | None:
    # on_own_egress() is also true when the bot runs ON the dedicated box, where
    # there is no proxy URL — handing requests an empty one would break every
    # call, so the URL itself decides here.
    url = config.binance_proxy_url
    if not url or _proxy_down:
        return None
    return {"http": url, "https": url}


def _note_failure(exc: Exception) -> None:
    global _proxy_down, _proxy_error
    _proxy_down = True
    _proxy_error = f"{type(exc).__name__}: {str(exc)[:120]}"
    where = "falling back to the shared Railway IP" if config.binance_proxy_fallback \
        else "and fallback is off"
    print(f"  ‼️ egress proxy unreachable — {where}\n     {_proxy_error}")


def request(method: str, url: str, **kw) -> requests.Response:
    """A Binance request, sent from our own egress when one is configured.

    Falls back to a direct connection ONLY on the two errors that prove the
    tunnel never came up — a refused/unauthorised proxy and a connect timeout.
    Anything else (a read timeout above all) may mean the request DID reach
    Binance, and replaying a POST /api/v3/order there would place the order
    twice. Those propagate untouched.
    """
    kw.setdefault("timeout", _TIMEOUT)
    proxies = _proxies()
    if proxies is None:
        return _session.request(method, url, **kw)
    try:
        return _session.request(method, url, proxies=proxies, **kw)
    except (requests.exceptions.ProxyError,
            requests.exceptions.ConnectTimeout) as exc:
        _note_failure(exc)
        if not config.binance_proxy_fallback:
            raise
        return _session.request(method, url, **kw)


def get(url: str, **kw) -> requests.Response:
    return request("GET", url, **kw)


def report() -> str | None:
    """One console/report line about which address the cycle went out on."""
    if not config.binance_proxy_url:
        if config.egress_dedicated:
            return "Egress            : own dedicated IP ✅ (this host)"
        return ("Egress            : shared IP — a neighbour's traffic "
                "can still get us banned")
    if _proxy_down:
        tail = ("fell back to the shared Railway IP"
                if config.binance_proxy_fallback else "cycle ran without it")
        return f"Egress            : ⚠️ own IP unreachable, {tail} ({_proxy_error})"
    return "Egress            : own dedicated IP ✅"
