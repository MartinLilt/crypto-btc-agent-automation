"""Signed Binance spot client — real orders + balances for LIVE mode.

Market orders only (no resting limits, no stops) so nothing of our intent sits
in the public book — see the strategy notes. Used exclusively by LiveGridBroker;
paper mode never touches this module.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

from .config import config

_TIMEOUT = 10
_lot_cache: dict[str, str] = {}
# Per-process free-balance cache. The cron restarts the process each cycle, so
# this only ever holds one cycle's reads. Any order mutates balances → we drop
# it, so the next read (e.g. the next bag's _sellable) refetches fresh.
_bal_cache: dict[str, float] | None = None


class TradeError(RuntimeError):
    pass


def _base_url() -> str:
    return "https://testnet.binance.vision" if config.testnet else "https://api.binance.com"


def _signed(method: str, path: str, params: dict) -> dict:
    if not (config.api_key and config.api_secret):
        raise TradeError("live trading needs BINANCE_API_KEY / BINANCE_API_SECRET")
    params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
    qs = urlencode(params)
    sig = hmac.new(config.api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{_base_url()}{path}?{qs}&signature={sig}"
    try:
        r = requests.request(method, url, headers={"X-MBX-APIKEY": config.api_key},
                             timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise TradeError(f"request {path} failed: {exc}") from exc
    if r.status_code != 200:
        raise TradeError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def get_free_balances(force: bool = False) -> dict[str, float]:
    """Free (available) balance per asset. Read-only.

    Cached within a cycle: `free_quote()` (init), every `_sellable()` and the
    end-of-cycle reconcile all call this, but the /account endpoint is weight-20.
    Each order invalidates the cache (`_invalidate_balances`), so a read after a
    trade still reflects the post-trade balance — only redundant reads are saved.
    Pass force=True to bypass the cache.
    """
    global _bal_cache
    if _bal_cache is None or force:
        data = _signed("GET", "/api/v3/account", {})
        _bal_cache = {b["asset"]: float(b["free"]) for b in data["balances"]}
    return _bal_cache


def _invalidate_balances() -> None:
    """Drop the cached balances — call after any fill, since it moved them."""
    global _bal_cache
    _bal_cache = None


def free_quote() -> float:
    """Free balance of the quote currency (USDC in the EU)."""
    return get_free_balances().get(config.quote_asset, 0.0)


def _lot_step(symbol: str) -> str:
    if symbol not in _lot_cache:
        info = requests.get(f"{_base_url()}/api/v3/exchangeInfo",
                            params={"symbol": symbol}, timeout=_TIMEOUT).json()
        step = "0.00000001"
        for f in info["symbols"][0]["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step = f["stepSize"]
        _lot_cache[symbol] = step
    return _lot_cache[symbol]


def _lot_places(symbol: str) -> int:
    """Decimal places implied by the symbol's LOT_SIZE step."""
    step = _lot_step(symbol).rstrip("0")
    return len(step.split(".")[1]) if "." in step else 0


def round_qty(symbol: str, qty: float) -> float:
    """Round DOWN to the symbol's LOT_SIZE step so the order is accepted."""
    factor = 10 ** _lot_places(symbol)
    return int(qty * factor) / factor


def qty_str(symbol: str, qty: float) -> str:
    """LOT_SIZE-rounded qty as a PLAIN decimal string (no scientific notation).

    A small float like 0.00009 stringifies to '9e-05', whose 'e' Binance rejects
    with -1100 'Illegal characters in parameter quantity'. Fixed-point format
    avoids that for tiny quantities (e.g. a few-dollar BTC bag)."""
    return f"{round_qty(symbol, qty):.{_lot_places(symbol)}f}"


def market_buy(symbol: str, quote_usdt: float) -> dict:
    """Spend `quote_usdt` USDT on a market buy (quoteOrderQty — no qty rounding)."""
    # Plain fixed-point string, same reasoning as qty_str: never risk a float
    # stringifying to scientific notation in an API parameter (Binance -1100).
    resp = _signed("POST", "/api/v3/order", {
        "symbol": symbol, "side": "BUY", "type": "MARKET",
        "quoteOrderQty": f"{quote_usdt:.2f}"})
    _invalidate_balances()          # balances moved — next read must be fresh
    return resp


def market_sell(symbol: str, qty: float) -> dict:
    if round_qty(symbol, qty) <= 0:
        raise TradeError(f"{symbol}: qty {qty} rounds below LOT_SIZE")
    resp = _signed("POST", "/api/v3/order", {
        "symbol": symbol, "side": "SELL", "type": "MARKET",
        "quantity": qty_str(symbol, qty)})
    _invalidate_balances()          # balances moved — next read must be fresh
    return resp


def fill_amounts(resp: dict, fallback_price: float) -> tuple[float, float, float]:
    """(executed_qty, quote_spent_or_received, avg_price) from an order response."""
    executed = float(resp.get("executedQty", 0) or 0)
    quote = float(resp.get("cummulativeQuoteQty", 0) or 0) or executed * fallback_price
    avg = quote / executed if executed else fallback_price
    return executed, quote, avg