"""Broker — turns a Signal into an executed trade.

Two backends behind one interface:

  PaperBroker : simulates orders on a virtual balance using REAL market prices.
                State persists to state/paper_state.json. No keys, no risk.
  LiveBroker  : places REAL signed spot market orders on Binance.

The runner calls `execute(signal, price)` and never cares which backend it got.
Spot, single position: we are either FLAT (holding USDT) or LONG (holding LTC).
We never BUY twice in a row or SELL when flat.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode

import requests

from .config import PROJECT_ROOT, config
from .strategy import Signal

_STATE_DIR = PROJECT_ROOT / "state"
_PAPER_STATE_FILE = _STATE_DIR / "paper_state.json"
_TIMEOUT = 10


@dataclass
class Position:
    """Current holdings. FLAT when base==0, LONG when base>0."""

    quote: float  # USDT
    base: float   # base asset
    entry_price: float = 0.0   # price we bought at (for TP/SL)
    peak_price: float = 0.0    # highest price seen since entry (trailing stop)
    entry_epoch: float = 0.0   # unix time of entry (for timeout)

    @property
    def is_long(self) -> bool:
        return self.base > 1e-8

    def equity(self, price: float) -> float:
        """Total value in USDT at the given price."""
        return self.quote + self.base * price


@dataclass
class Trade:
    side: str        # BUY | SELL
    price: float
    base_qty: float
    quote_qty: float
    fee: float
    mode: str        # paper | live


class BrokerError(RuntimeError):
    pass


# ─────────────────────────── Paper (simulation) ───────────────────────────
class PaperBroker:
    mode = "paper"

    def __init__(self) -> None:
        self.position = self._load()

    def _load(self) -> Position:
        if _PAPER_STATE_FILE.exists():
            d = json.loads(_PAPER_STATE_FILE.read_text())
            return Position(quote=d["quote"], base=d["base"],
                            entry_price=d.get("entry_price", 0.0),
                            peak_price=d.get("peak_price", 0.0),
                            entry_epoch=d.get("entry_epoch", 0.0))
        return Position(quote=config.paper_start_balance, base=0.0)

    def _save(self, last_trade: Trade | None = None) -> None:
        _STATE_DIR.mkdir(exist_ok=True)
        payload = {
            "quote": self.position.quote,
            "base": self.position.base,
            "entry_price": self.position.entry_price,
            "peak_price": self.position.peak_price,
            "entry_epoch": self.position.entry_epoch,
            "last_trade": asdict(last_trade) if last_trade else None,
        }
        _PAPER_STATE_FILE.write_text(json.dumps(payload, indent=2))

    def buy(self, price: float) -> Trade:
        spend = min(config.quote_order_qty, self.position.quote)
        if spend <= 0:
            raise BrokerError("no quote balance to buy with")
        fee = spend * config.fee_rate
        base_qty = (spend - fee) / price
        self.position.quote -= spend
        self.position.base += base_qty
        self.position.entry_price = price
        self.position.peak_price = price
        self.position.entry_epoch = time.time()
        trade = Trade("BUY", price, base_qty, spend, fee, self.mode)
        self._save(trade)
        return trade

    def sell(self, price: float) -> Trade:
        base_qty = self.position.base
        if base_qty <= 0:
            raise BrokerError("no base balance to sell")
        gross = base_qty * price
        fee = gross * config.fee_rate
        self.position.quote += gross - fee
        self.position.base = 0.0
        self.position.entry_price = 0.0
        self.position.peak_price = 0.0
        self.position.entry_epoch = 0.0
        trade = Trade("SELL", price, base_qty, gross, fee, self.mode)
        self._save(trade)
        return trade

    def update_peak(self, price: float) -> None:
        """Track the running high while in a position (for the trailing stop)."""
        if self.position.is_long and price > self.position.peak_price:
            self.position.peak_price = price
            self._save()


# ─────────────────────────────── Live (real) ──────────────────────────────
class LiveBroker:
    mode = "live"

    def __init__(self) -> None:
        if not (config.api_key and config.api_secret):
            raise BrokerError("live mode needs BINANCE_API_KEY / BINANCE_API_SECRET")
        self.base_url = (
            "https://testnet.binance.vision"
            if config.testnet
            else "https://api.binance.com"
        )

    def _signed(self, method: str, path: str, params: dict) -> dict:
        params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
        qs = urlencode(params)
        sig = hmac.new(config.api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}?{qs}&signature={sig}"
        headers = {"X-MBX-APIKEY": config.api_key}
        resp = requests.request(method, url, headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            raise BrokerError(f"{path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    @property
    def position(self) -> Position:
        data = self._signed("GET", "/api/v3/account", {})
        base_asset = config.symbol.replace("USDT", "")
        quote = base = 0.0
        for b in data["balances"]:
            if b["asset"] == "USDT":
                quote = float(b["free"])
            elif b["asset"] == base_asset:
                base = float(b["free"])
        return Position(quote=quote, base=base)

    def buy(self, price: float) -> Trade:
        r = self._signed(
            "POST", "/api/v3/order",
            {"symbol": config.symbol, "side": "BUY", "type": "MARKET",
             "quoteOrderQty": config.quote_order_qty},
        )
        return self._trade_from_fill("BUY", r, price)

    def sell(self, price: float) -> Trade:
        base_qty = self.position.base
        if base_qty <= 0:
            raise BrokerError("no base balance to sell")
        qty = self._round_lot(base_qty)
        r = self._signed(
            "POST", "/api/v3/order",
            {"symbol": config.symbol, "side": "SELL", "type": "MARKET",
             "quantity": qty},
        )
        return self._trade_from_fill("SELL", r, price)

    def _round_lot(self, qty: float) -> float:
        """Round down to the symbol's LOT_SIZE step so the order is accepted."""
        info = requests.get(
            f"{self.base_url}/api/v3/exchangeInfo",
            params={"symbol": config.symbol}, timeout=_TIMEOUT,
        ).json()
        step = "0.00001"
        for f in info["symbols"][0]["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step = f["stepSize"]
        decimals = step.rstrip("0")
        places = len(decimals.split(".")[1]) if "." in decimals else 0
        factor = 10 ** places
        return int(qty * factor) / factor

    @staticmethod
    def _trade_from_fill(side: str, resp: dict, price: float) -> Trade:
        base_qty = float(resp.get("executedQty", 0))
        quote_qty = float(resp.get("cummulativeQuoteQty", base_qty * price))
        avg = quote_qty / base_qty if base_qty else price
        return Trade(side, avg, base_qty, quote_qty, 0.0, "live")


def get_broker() -> PaperBroker | LiveBroker:
    if config.trading_mode == "live":
        return LiveBroker()
    return PaperBroker()


def execute(broker, signal: Signal, price: float) -> Trade | None:
    """Apply a signal, respecting current position. Returns the Trade or None."""
    pos = broker.position
    if signal is Signal.BUY and not pos.is_long:
        return broker.buy(price)
    if signal is Signal.SELL and pos.is_long:
        return broker.sell(price)
    return None  # HOLD, or signal that doesn't change our position