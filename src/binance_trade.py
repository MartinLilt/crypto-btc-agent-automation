"""Signed Binance spot client — real orders + balances for LIVE mode.

Market orders only (no resting limits, no stops) so nothing of our intent sits
in the public book — see the strategy notes. Used exclusively by LiveGridBroker;
paper mode never touches this module.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from urllib.parse import urlencode

import requests

from . import ratelimit
from .config import config

_TIMEOUT = 10
_lot_cache: dict[str, str] = {}
# NOTIONAL filter (minimum order value in quote currency) per symbol. Binance
# rejects anything below it with -1013 "Filter failure: NOTIONAL".
_notional_cache: dict[str, float] = {}
_DEFAULT_MIN_NOTIONAL = 5.0     # every USDC/USDT spot pair we trade uses $5
# Per-process free-balance cache. The cron restarts the process each cycle, so
# this only ever holds one cycle's reads. Any order mutates balances → we drop
# it, so the next read (e.g. the next bag's _sellable) refetches fresh.
_bal_cache: dict[str, float] | None = None
# Signing keys for the account currently being processed. None → fall back to the
# single-account config keys (backward compatible). set_credentials() switches
# accounts and drops the balance cache so no cross-account balance leaks.
_creds: tuple[str, str] | None = None


class TradeError(RuntimeError):
    pass


def set_credentials(api_key: str, api_secret: str) -> None:
    """Point signed calls at THIS account's keys; clears the balance cache so a
    read never returns the previous account's balances."""
    global _creds
    _creds = (api_key, api_secret)
    _invalidate_balances()


def _key_secret() -> tuple[str, str]:
    return _creds if _creds is not None else (config.api_key, config.api_secret)


def _base_url() -> str:
    return "https://testnet.binance.vision" if config.testnet else "https://api.binance.com"


def _signed(method: str, path: str, params: dict) -> dict:
    api_key, api_secret = _key_secret()
    if not (api_key and api_secret):
        raise TradeError("live trading needs a Binance API key/secret for this account")
    params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
    qs = urlencode(params)
    sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{_base_url()}{path}?{qs}&signature={sig}"
    try:
        r = requests.request(method, url, headers={"X-MBX-APIKEY": api_key},
                             timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise TradeError(f"request {path} failed: {exc}") from exc
    # A ban must surface as BinanceBanned, never as a generic TradeError the
    # read-retry would happily hammer three more times (see src/ratelimit.py).
    ratelimit.check(path, r.status_code, r.text, r.headers)
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


def _fetch_exchange_info(params: dict) -> dict:
    """GET /api/v3/exchangeInfo, surfacing a ban instead of blowing up on JSON."""
    r = requests.get(f"{_base_url()}/api/v3/exchangeInfo",
                     params=params, timeout=_TIMEOUT)
    ratelimit.check("/api/v3/exchangeInfo", r.status_code, r.text, r.headers)
    if r.status_code != 200:
        raise TradeError(f"/api/v3/exchangeInfo -> HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _store_filters(entry: dict) -> None:
    step, floor = "0.00000001", _DEFAULT_MIN_NOTIONAL
    for f in entry["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step = f["stepSize"]
        elif f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL"):
            floor = float(f.get("minNotional", floor))
    _lot_cache[entry["symbol"]] = step
    _notional_cache[entry["symbol"]] = floor


def preload_filters(symbols) -> None:
    """Cache LOT_SIZE/NOTIONAL for the whole basket in ONE request.

    exchangeInfo costs weight 20 whether it is asked about one symbol or ten, so
    four per-symbol calls burned 80 of the cycle's ~110 weight for nothing. The
    process restarts every cron run, so this cache is per-cycle either way.
    Best-effort: on failure the lazy per-symbol path below still covers us.
    """
    wanted = [s for s in dict.fromkeys(symbols) if s not in _lot_cache]
    if not wanted:
        return
    # Binance wants a JSON array with no spaces: symbols=["BTCUSDC","ETHUSDC"]
    payload = "[" + ",".join(f'"{s}"' for s in wanted) + "]"
    for entry in _fetch_exchange_info({"symbols": payload})["symbols"]:
        _store_filters(entry)


def _load_filters(symbol: str) -> None:
    """Fetch and cache this symbol's LOT_SIZE step and NOTIONAL minimum."""
    _store_filters(_fetch_exchange_info({"symbol": symbol})["symbols"][0])


def _lot_step(symbol: str) -> str:
    if symbol not in _lot_cache:
        _load_filters(symbol)
    return _lot_cache[symbol]


def min_notional(symbol: str) -> float:
    """Smallest order value the exchange accepts for this symbol (quote ccy)."""
    if symbol not in _notional_cache:
        try:
            _load_filters(symbol)
        except Exception:                      # network/parse — assume the norm
            return _DEFAULT_MIN_NOTIONAL
    return _notional_cache.get(symbol, _DEFAULT_MIN_NOTIONAL)


def min_round_trip_unit(symbol: str, price: float) -> float:
    """Smallest bag that can still be SOLD after the exchange's rounding.

    A quoteOrderQty buy truncates the base qty DOWN to LOT_SIZE, and the sell
    truncates again — so up to two steps of value evaporate on a round-trip.
    What is left must still clear NOTIONAL, plus a little slack for the fee.
    On BTC the step is ~$0.78 at 78k, which is why a $6 bag bought $5.40 of BTC
    and then could not be sold ($4.71 < $5, -1013)."""
    step = float(_lot_step(symbol))
    return (min_notional(symbol) + 2 * step * price) * (1 + config.fee_rate) + 0.01


def _lot_places(symbol: str) -> int:
    """Decimal places implied by the symbol's LOT_SIZE step."""
    step = _lot_step(symbol).rstrip("0")
    return len(step.split(".")[1]) if "." in step else 0


def round_qty(symbol: str, qty: float) -> float:
    """Round DOWN to the symbol's LOT_SIZE step so the order is accepted.

    Decimal, not float: `int(0.00007 * 100000)` is 6, because 0.00007 has no
    exact binary form — a qty sitting exactly ON the step lost a WHOLE step
    (on BTC that is ~$0.78, enough to drop a $5.48 TP-sell under the $5
    NOTIONAL floor and get it rejected)."""
    step = Decimal(_lot_step(symbol))
    return float((Decimal(str(qty)) // step) * step)


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


def market_sell(symbol: str, qty: float, price: float | None = None) -> dict:
    """Market-sell `qty` base units. `price` (if known) enables a NOTIONAL
    pre-check, so a too-small bag fails with a clear message here instead of
    costing an API round-trip and a -1013 rejection every cycle."""
    lot = round_qty(symbol, qty)
    if lot <= 0:
        raise TradeError(f"{symbol}: qty {qty} rounds below LOT_SIZE")
    if price:
        floor = min_notional(symbol)
        if lot * price < floor:
            raise TradeError(
                f"{symbol}: sellable {lot:g} @ {price:g} = "
                f"{lot * price:.2f} {config.quote_asset} < minNotional "
                f"{floor:.2f} — bag too small to sell (needs price "
                f">= {floor / lot:,.0f} or a top-up)")
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