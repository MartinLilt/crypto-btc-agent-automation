"""Grid-stream broker — many concurrent bags across a basket of coins.

Paper broker: one shared USDT cash pool, a list of bags per coin, persisted to
state/grid_state.json between runs. Equity is always MARK-TO-MARKET (cash +
every bag valued at the current price) so frozen bags show their real value.

  bag = {entry, qty, cost, time}
  buy(symbol, price)      -> open a bag (unit_usdt from cash)
  sell_bag(symbol, i, p)  -> close a bag at its take-profit (adds to the stream)

Live grid execution (many real orders per coin) is not wired yet — paper only;
live stays user-gated as everywhere else.
"""

from __future__ import annotations

import json
import time

from .config import STATE_DIR, config

_STATE_DIR = STATE_DIR
_STATE_FILE = _STATE_DIR / "grid_state.json"


class GridBroker:
    mode = "paper"

    def __init__(self) -> None:
        self.cash: float = config.paper_start_balance
        self.realized: float = 0.0
        self.positions: dict[str, list[dict]] = {}
        self.holds: dict[str, dict] = {}   # per-coin buy-&-hold ride during BULL
        self._load()

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self) -> None:
        if _STATE_FILE.exists():
            d = json.loads(_STATE_FILE.read_text())
            self.cash = d.get("cash", config.paper_start_balance)
            self.realized = d.get("realized", 0.0)
            self.positions = d.get("positions", {})
            self.holds = d.get("holds", {})

    def save(self) -> None:
        _STATE_DIR.mkdir(exist_ok=True)
        _STATE_FILE.write_text(json.dumps({
            "cash": self.cash,
            "realized": self.realized,
            "positions": self.positions,
            "holds": self.holds,
        }, indent=2))

    # ── orders ───────────────────────────────────────────────────────────
    def bags(self, symbol: str) -> list[dict]:
        return self.positions.setdefault(symbol, [])

    def buy(self, symbol: str, price: float, unit_usdt: float) -> dict:
        fee = unit_usdt * config.fee_rate
        qty = (unit_usdt - fee) / price
        bag = {"entry": price, "qty": qty, "cost": unit_usdt, "time": time.time()}
        self.bags(symbol).append(bag)
        self.cash -= unit_usdt
        return bag

    def sell_bag(self, symbol: str, index: int, price: float) -> float:
        bag = self.bags(symbol).pop(index)
        gross = bag["qty"] * price
        fee = gross * config.fee_rate
        proceeds = gross - fee
        self.cash += proceeds
        pnl = proceeds - bag["cost"]
        self.realized += pnl
        return pnl

    # ── BULL hold-allocation (ride the trend) ────────────────────────────
    def has_hold(self, symbol: str) -> bool:
        return symbol in self.holds

    def buy_hold(self, symbol: str, price: float, amount_usdt: float) -> None:
        fee = amount_usdt * config.fee_rate
        self.holds[symbol] = {"entry": price, "qty": (amount_usdt - fee) / price,
                              "cost": amount_usdt, "time": time.time()}
        self.cash -= amount_usdt

    def sell_hold(self, symbol: str, price: float) -> float:
        h = self.holds.pop(symbol)
        gross = h["qty"] * price
        proceeds = gross - gross * config.fee_rate
        self.cash += proceeds
        pnl = proceeds - h["cost"]
        self.realized += pnl
        return pnl

    def hold_value(self, symbol: str, price: float) -> float:
        h = self.holds.get(symbol)
        return h["qty"] * price if h else 0.0

    # ── valuation ────────────────────────────────────────────────────────
    def coin_value(self, symbol: str, price: float) -> float:
        return sum(b["qty"] * price for b in self.bags(symbol)) + self.hold_value(symbol, price)

    def equity(self, prices: dict[str, float]) -> float:
        """Mark-to-market: cash + every bag AND hold at the current price."""
        symbols = set(self.positions) | set(self.holds)
        held = sum(self.coin_value(s, prices.get(s, 0.0)) for s in symbols)
        return self.cash + held

    def feast_value(self, tp_pct: float, prices: dict[str, float] | None = None) -> float:
        """Equity if every frozen bag sells at its TP; holds valued at market."""
        prices = prices or {}
        pending = 0.0
        for bags in self.positions.values():
            for b in bags:
                pending += b["qty"] * b["entry"] * (1 + tp_pct / 100) * (1 - config.fee_rate)
        holds = sum(self.hold_value(s, prices.get(s, 0.0)) for s in self.holds)
        return self.cash + pending + holds

    @property
    def total_bags(self) -> int:
        return sum(len(b) for b in self.positions.values())


def get_grid_broker() -> GridBroker:
    return GridBroker()