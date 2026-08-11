"""Micro-scalp / grid simulator — honest mark-to-market test of two ideas.

A) SCALP+STOP : one unit at a time, micro take-profit, hard stop-loss.
                Controlled risk — losses are realised and capped.
B) GRID/FREEZE: buy a unit; if price drops grid_step, don't stop — "freeze"
                that bag and buy another lower. Every open bag has a standing
                take-profit; new bags added on the way down until cash runs out.
                No stop — bags are held ("frozen") indefinitely.

The whole honesty of the test is MARK-TO-MARKET equity: a frozen bag is valued
at the CURRENT price, so hidden (unrealised) losses show up. "Not selling"
never makes a loss disappear here.

    python -m backtest.scalp SYMBOL INTERVAL
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from src.binance_api import get_candles
from src.config import config
from src.strategy import sma

FEE = config.fee_rate


@dataclass
class GridResult:
    start: float
    end_equity: float          # mark-to-market: cash + frozen bags at last price
    cash: float
    open_bags: int
    realized: float
    closed_trades: int
    max_drawdown_pct: float
    peak_bags: int
    recovery_equity: float = 0.0   # if every frozen bag eventually hits its TP
    equity_curve: list = field(default_factory=list)
    # ── stagnation metrics (bars; 4h → 6 bars/day) ──
    avg_hold_bars: float = 0.0     # mean bars a SOLD bag was held (turnover speed)
    max_hold_bars: int = 0         # worst freeze among sold bags
    end_max_age_bars: int = 0      # oldest STILL-open bag at the end
    frozen_cap_pct: float = 0.0    # time-avg of underwater bag cost / start (%)

    @property
    def return_pct(self) -> float:
        return (self.end_equity / self.start - 1) * 100

    @property
    def recovery_pct(self) -> float:
        return (self.recovery_equity / self.start - 1) * 100


def run_grid(
    candles,
    unit_usdt: float,
    tp_pct: float,
    grid_step_pct: float,
    start_balance: float = 1000.0,
    stop_pct: float = 0.0,     # 0 = freeze (no stop); >0 = scalp+stop
    fee: float = FEE,
    block_entry: list | None = None,   # per-bar: True = don't open a new bag
    max_bags: int = 0,                 # cap concurrent bags (0 = unlimited)
    pool_tp: bool = False,             # True = sell the WHOLE stack at avg-cost+TP
                                       #        (idea #1) instead of per-bag TP
) -> GridResult:
    cash = start_balance
    bags: list[dict] = []      # each: {entry, qty, cost, i}  (i = open bar index)
    realized = 0.0
    closed = 0
    peak_eq = start_balance
    mdd = 0.0
    peak_bags = 0
    curve = []
    hold_sum = 0        # Σ bars held over all sold bags
    hold_max = 0        # worst single freeze among sold bags
    frozen_bar_sum = 0.0   # Σ over bars of (underwater bag cost)

    for i, c in enumerate(candles):
        price = c.close
        blocked = block_entry is not None and block_entry[i]

        # 1. exits
        if pool_tp and bags:
            # Idea #1: sell the ENTIRE stack once price reaches the cost-weighted
            # average entry + TP. A small bounce to the average releases every
            # frozen bag at once (winners carry laggards) → faster feast.
            tot_qty = sum(b["qty"] for b in bags)
            tot_cost = sum(b["cost"] for b in bags)
            avg = tot_cost / tot_qty
            if price >= avg * (1 + tp_pct / 100):
                proceeds = tot_qty * price * (1 - fee)
                cash += proceeds
                realized += proceeds - tot_cost
                for b in bags:
                    held = i - b["i"]; hold_sum += held; hold_max = max(hold_max, held)
                closed += len(bags)
                bags = []
        else:
            # take-profit on any bag, or stop-loss (scalp mode only)
            keep = []
            for b in bags:
                hit_tp = price >= b["entry"] * (1 + tp_pct / 100)
                hit_sl = stop_pct and price <= b["entry"] * (1 - stop_pct / 100)
                if hit_tp or hit_sl:
                    proceeds = b["qty"] * price * (1 - fee)
                    cash += proceeds
                    realized += proceeds - b["cost"]
                    held = i - b["i"]; hold_sum += held; hold_max = max(hold_max, held)
                    closed += 1
                else:
                    keep.append(b)
            bags = keep

        # 2. entry — first bag, or add one when price fell grid_step below the
        #    lowest open bag (buy the dip deeper). Capped by available cash.
        lowest = min((b["entry"] for b in bags), default=None)
        want = lowest is None or price <= lowest * (1 - grid_step_pct / 100)
        capped = max_bags and len(bags) >= max_bags
        if want and cash >= unit_usdt and not blocked and not capped:
            fee_paid = unit_usdt * fee
            qty = (unit_usdt - fee_paid) / price
            bags.append({"entry": price, "qty": qty, "cost": unit_usdt, "i": i})
            cash -= unit_usdt

        # 3. mark-to-market equity + stagnation accounting
        equity = cash + sum(b["qty"] * price for b in bags)
        curve.append(equity)
        peak_eq = max(peak_eq, equity)
        if peak_eq > 0:
            mdd = max(mdd, (peak_eq - equity) / peak_eq)
        peak_bags = max(peak_bags, len(bags))
        frozen_bar_sum += sum(b["cost"] for b in bags if price < b["entry"])

    last = candles[-1].close
    end_eq = cash + sum(b["qty"] * last for b in bags)
    # "Bull recovery" payoff (feast): every frozen bag eventually reaches its exit.
    if pool_tp and bags:
        # pool mode exits the whole stack at the average entry + TP
        tot_qty = sum(b["qty"] for b in bags)
        avg = sum(b["cost"] for b in bags) / tot_qty
        recovery = cash + tot_qty * avg * (1 + tp_pct / 100) * (1 - fee)
    else:
        recovery = cash + sum(
            b["qty"] * b["entry"] * (1 + tp_pct / 100) * (1 - fee) for b in bags
        )
    n = len(candles) or 1
    avg_hold = hold_sum / closed if closed else 0.0
    end_max_age = max((len(candles) - 1 - b["i"] for b in bags), default=0)
    frozen_cap = frozen_bar_sum / (n * start_balance) * 100
    return GridResult(start_balance, end_eq, cash, len(bags), realized, closed,
                      mdd * 100, peak_bags, recovery, curve,
                      avg_hold, hold_max, end_max_age, frozen_cap)


def _report(label, r: GridResult, bh: float) -> None:
    print(f"{label}")
    print(f"  equity (mark-to-market): {r.end_equity:7.2f}  ({r.return_pct:+.2f}%)")
    print(f"  cash / frozen bags     : {r.cash:7.2f} / {r.open_bags} open "
          f"(peak {r.peak_bags})")
    print(f"  realized profit        : {r.realized:+.2f} over {r.closed_trades} closed")
    print(f"  max drawdown           : {r.max_drawdown_pct:.1f}%")
    print(f"  if bags recover (feast): {r.recovery_equity:7.2f}  ({r.recovery_pct:+.2f}%)")
    print(f"  (buy & hold this coin  : {bh:+.2f}%)\n")


def main(argv):
    symbol = argv[0] if len(argv) > 0 else "AVAXUSDT"
    interval = argv[1] if len(argv) > 1 else "4h"
    limit = int(argv[2]) if len(argv) > 2 else 1000

    candles = get_candles(symbol=symbol, interval=interval, limit=limit)
    bh = (candles[-1].close / candles[0].close - 1) * 100
    span = f"{candles[0].open_time:%Y-%m-%d} → {candles[-1].open_time:%Y-%m-%d}"
    print(f"\nScalp/grid test | {symbol} {interval} | {span} | 1000 start | "
          f"unit 100 | TP +0.55% | grid step 1.5%\n")

    # Uptrend gate: only open bags when price is above its long SMA (rising tide).
    closes = [c.close for c in candles]
    win = 100
    block = [
        (sma(closes[: i + 1], win) is None) or (closes[i] <= sma(closes[: i + 1], win))
        for i in range(len(candles))
    ]

    stop = run_grid(candles, unit_usdt=100, tp_pct=0.55, grid_step_pct=1.5,
                    stop_pct=2.0)
    freeze = run_grid(candles, unit_usdt=100, tp_pct=0.55, grid_step_pct=1.5,
                      stop_pct=0.0)
    gated = run_grid(candles, unit_usdt=100, tp_pct=0.55, grid_step_pct=1.5,
                     stop_pct=0.0, block_entry=block)

    _report("A) SCALP + STOP (2%)", stop, bh)
    _report("B) GRID / FREEZE (no stop)", freeze, bh)
    _report(f"C) GRID / FREEZE + UPTREND (bags only when price > SMA{win})", gated, bh)


if __name__ == "__main__":
    main(sys.argv[1:])