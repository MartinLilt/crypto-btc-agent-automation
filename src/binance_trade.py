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


def get_free_balances() -> dict[str, float]:
    """Free (available) balance per asset. Read-only."""
    data = _signed("GET", "/api/v3/account", {})
    return {b["asset"]: float(b["free"]) for b in data["balances"]}


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
    return _signed("POST", "/api/v3/order", {
        "symbol": symbol, "side": "BUY", "type": "MARKET",
        "quoteOrderQty": round(quote_usdt, 2)})


def market_sell(symbol: str, qty: float) -> dict:
    if round_qty(symbol, qty) <= 0:
        raise TradeError(f"{symbol}: qty {qty} rounds below LOT_SIZE")
    return _signed("POST", "/api/v3/order", {
        "symbol": symbol, "side": "SELL", "type": "MARKET",
        "quantity": qty_str(symbol, qty)})


def fill_amounts(resp: dict, fallback_price: float) -> tuple[float, float, float]:
    """(executed_qty, quote_spent_or_received, avg_price) from an order response."""
    executed = float(resp.get("executedQty", 0) or 0)
    quote = float(resp.get("cummulativeQuoteQty", 0) or 0) or executed * fallback_price
    avg = quote / executed if executed else fallback_price
    return executed, quote, avg