"""Binance market-data API module.

Thin, dependency-light wrapper over Binance's public REST endpoints. Pulls the
data our strategy will need for LTC (candles, latest price, 24h stats). No API
keys required — these endpoints are public. Trading (signed) calls live in a
separate module later.

Docs: https://developers.binance.com/docs/binance-spot-api-docs
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .config import config

# Public production API — market data is the same for everyone and needs no key.
# (Testnet is only relevant for placing orders, handled by the trading module.)
BASE_URL = "https://api.binance.com"

_TIMEOUT = 10  # seconds


class BinanceAPIError(RuntimeError):
    """Raised when Binance returns a non-2xx response."""


def _get(path: str, params: dict | None = None) -> object:
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise BinanceAPIError(f"request to {url} failed: {exc}") from exc

    if resp.status_code != 200:
        raise BinanceAPIError(
            f"{path} -> HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


@dataclass(frozen=True)
class Candle:
    """One OHLCV candle (a Binance kline), typed and parsed."""

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: datetime

    @classmethod
    def from_kline(cls, k: list) -> "Candle":
        return cls(
            open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
            close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
        )


def get_price(symbol: str | None = None) -> float:
    """Latest trade price for a symbol (default: configured SYMBOL)."""
    symbol = symbol or config.symbol
    data = _get("/api/v3/ticker/price", {"symbol": symbol})
    return float(data["price"])


def get_candles(
    symbol: str | None = None,
    interval: str | None = None,
    limit: int | None = None,
) -> list[Candle]:
    """OHLCV candles for a symbol, oldest first.

    Defaults come from .env (SYMBOL / INTERVAL / CANDLE_LIMIT). When `limit`
    exceeds Binance's 1000-per-request cap, the history is fetched in pages
    (walking backwards via endTime) so long-horizon backtests are possible —
    e.g. a full year of 1h candles (~8760).
    """
    symbol = symbol or config.symbol
    interval = interval or config.interval
    limit = limit or config.candle_limit

    if limit <= 1000:
        raw = _get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        return [Candle.from_kline(k) for k in raw]

    # Page backwards: each request ends just before the earliest bar we have.
    collected: list[list] = []
    end_time: int | None = None
    while len(collected) < limit:
        params = {"symbol": symbol, "interval": interval, "limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time
        batch = _get("/api/v3/klines", params)
        if not batch:
            break
        collected = batch + collected
        end_time = batch[0][0] - 1  # ms before the earliest open_time
        if len(batch) < 1000:
            break  # reached the start of available history

    collected = collected[-limit:]
    return [Candle.from_kline(k) for k in collected]


def get_24h_stats(symbol: str | None = None) -> dict:
    """Rolling 24h stats (price change, high/low, volume) for a symbol."""
    symbol = symbol or config.symbol
    data = _get("/api/v3/ticker/24hr", {"symbol": symbol})
    return {
        "symbol": data["symbol"],
        "last_price": float(data["lastPrice"]),
        "price_change_pct": float(data["priceChangePercent"]),
        "high_24h": float(data["highPrice"]),
        "low_24h": float(data["lowPrice"]),
        "volume_24h": float(data["volume"]),
    }


# Quote/base assets that aren't tradeable trend instruments — skip them.
_SKIP_BASES = {
    "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "PYUSD", "AEUR",
    "USD1", "RLUSD", "EUR", "GBP", "AUD", "TRY", "BRL", "ARS", "JPY", "RUB",
    "XAUT", "PAXG",  # tokenized gold — not crypto
}
_LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")


def get_top_symbols(
    n: int = 30,
    min_quote_volume: float = 0.0,
    quote: str = "USDT",
) -> list[str]:
    """Top `n` spot symbols by 24h quote volume (liquidity-ranked, most first).

    Skips leveraged tokens (…UP/DOWN/BULL/BEAR) and stable/fiat pairs so the
    universe is real, liquid trend instruments. One request returns every
    ticker; no keys needed.
    """
    data = _get("/api/v3/ticker/24hr")
    rows: list[tuple[str, float]] = []
    for d in data:
        sym = d["symbol"]
        if not sym.endswith(quote):
            continue
        base = sym[: -len(quote)]
        if base in _SKIP_BASES or any(tok in base for tok in _LEVERAGED):
            continue
        qv = float(d.get("quoteVolume", 0.0))
        if qv < min_quote_volume:
            continue
        rows.append((sym, qv))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:n]]