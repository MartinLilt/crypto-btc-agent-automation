"""Funding-rate data + carry math (Binance USD-M perpetuals).

Perpetual futures pay a funding rate every 8h (3×/day) between longs and shorts:
  funding > 0 -> longs pay shorts
  funding < 0 -> shorts pay longs

A delta-neutral funding-carry = long spot + short perp of equal size. Price
moves cancel; you collect (or pay) funding each period. When funding is
persistently positive, the short-perp leg RECEIVES it — a structural yield that
does not depend on predicting price (unlike TA). This is why the prior research
flagged funding-carry as the real edge.

Data endpoints are PUBLIC (no keys). Execution of the carry needs a futures
short leg, which is beyond pure spot — this module supplies the data + the
yield math so we can measure whether the edge is actually there.

Docs: https://developers.binance.com/docs/derivatives/usds-margined-futures
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from . import net

FAPI_URL = "https://fapi.binance.com"
_TIMEOUT = 10
PERIODS_PER_YEAR = 3 * 365  # funding every 8h


class FundingError(RuntimeError):
    pass


def _get(path: str, params: dict | None = None):
    try:
        resp = net.get(f"{FAPI_URL}{path}", params=params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise FundingError(f"request to {path} failed: {exc}") from exc
    if resp.status_code != 200:
        raise FundingError(f"{path} -> HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


@dataclass(frozen=True)
class FundingPoint:
    time: datetime
    rate: float  # per-8h fraction (e.g. 0.0001 = 0.01%)


def get_funding_history(symbol: str, limit: int = 1000) -> list[FundingPoint]:
    """Historical 8h funding rates, oldest first (max 1000 per request)."""
    raw = _get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": min(limit, 1000)})
    return [
        FundingPoint(
            time=datetime.fromtimestamp(x["fundingTime"] / 1000, tz=timezone.utc),
            rate=float(x["fundingRate"]),
        )
        for x in raw
    ]


def get_perpetual_symbols() -> set[str]:
    """All actively-trading USD-M PERPETUAL symbols (e.g. {'BTCUSDT', ...}).

    Used to filter the universe down to real, liquid crypto that also has a
    futures market — excludes tokenized stocks, gold and stablecoin pairs, and
    guarantees funding-carry compatibility.
    """
    data = _get("/fapi/v1/exchangeInfo")
    return {
        s["symbol"]
        for s in data.get("symbols", [])
        if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
    }


def get_current_funding(symbol: str) -> float:
    """Latest funding rate for a symbol (per 8h fraction)."""
    data = _get("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(data["lastFundingRate"])


def annualized(rate_per_8h: float) -> float:
    """Annualise a single 8h funding rate, as a percentage."""
    return rate_per_8h * PERIODS_PER_YEAR * 100