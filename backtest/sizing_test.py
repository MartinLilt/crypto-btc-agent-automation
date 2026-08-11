"""Dynamic bag-sizing test — how the bot behaves when the balance is topped up.

Bag size options:
  FIXED $U      — current live behaviour (bag = a fixed dollar amount)
  PCT p%        — bag = p% of live equity, floored at the Binance min-notional
                  (auto-scales when you deposit)

Runs the live basket (continuous ~2.7y, 4h) at several STARTING balances so you
can see whether a deposit actually changes deployment — and where the min-notional
floor pins small accounts regardless of the percentage.

    python -m backtest.sizing_test            # 4h, ~2.7y
    python -m backtest.sizing_test 4h 6000
"""

from __future__ import annotations

import sys

from src.binance_api import get_candles
from src.config import config
from src.grid import params_from_config
from src.strategy import sma
from backtest.scalp import run_grid

_P = params_from_config()
BASKET = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
MIN_UNIT = config.grid_min_unit          # Binance min-notional floor (~$6)
STARTS = [22.0, 500.0, 5000.0]
# (label, unit_pct)  — pct 0 = fixed $MIN_UNIT bag (current live style)
MODES = [("fixed $6", 0.0), ("pct 0.4%", 0.004),
         ("pct 1.0%", 0.010), ("pct 2.0%", 0.020)]


def _block(closes, win):
    return [(sma(closes[: i + 1], win) is None) or (closes[i] <= sma(closes[: i + 1], win))
            for i in range(len(closes))]


def main(argv):
    interval = argv[0] if len(argv) > 0 else "4h"
    limit = int(argv[1]) if len(argv) > 1 else 6000

    data = {}
    for coin in BASKET:
        try:
            candles = get_candles(symbol=coin, interval=interval, limit=limit)
        except Exception as exc:
            print(f"  ({coin} skipped: {str(exc)[:50]})"); continue
        if len(candles) < _P.sma_win + 50:
            continue
        data[coin] = (candles, _block([c.close for c in candles], _P.sma_win))
    if not data:
        print("no data"); return

    span = next(iter(data.values()))[0]
    span = f"{span[0].open_time:%Y-%m-%d} → {span[-1].open_time:%Y-%m-%d}"
    print(f"Dynamic sizing | live basket {BASKET} | {interval} | {span}")
    print(f"step {_P.step_pct}% · TP {_P.tp_pct}% · max {_P.max_bags} bags · "
          f"SMA{_P.sma_win} · min-notional floor ${MIN_UNIT:.0f}\n")
    print(f"  {'start':>7} {'mode':>9} {'unit@start':>10} | {'stream%':>8} "
          f"{'equity%':>8} {'dd':>5} {'peakbags':>8} {'frozen%':>7}")
    print("  " + "-" * 78)

    for start in STARTS:
        for label, pct in MODES:
            unit_at_start = MIN_UNIT if pct == 0 else max(MIN_UNIT, pct * start)
            stream = eq = dd = frozen = 0.0
            peak = 0
            for candles, block in data.values():
                r = run_grid(candles, unit_usdt=MIN_UNIT, tp_pct=_P.tp_pct,
                             grid_step_pct=_P.step_pct, start_balance=start,
                             stop_pct=0.0, block_entry=block, max_bags=_P.max_bags,
                             unit_pct=pct, min_unit=MIN_UNIT)
                stream += r.realized
                eq += r.end_equity - start
                dd += r.max_drawdown_pct
                frozen += r.frozen_cap_pct
                peak = max(peak, r.peak_bags)
            n = len(data)
            # normalise stream / equity to % of the per-coin start (basket = n coins)
            base = start * n
            print(f"  {start:>7.0f} {label:>9} {unit_at_start:>9.2f}$ | "
                  f"{stream / base * 100:>+7.2f}% {eq / base * 100:>+7.2f}% "
                  f"{dd / n:>4.1f}% {peak:>8} {frozen / n:>6.2f}%")
        print()

    print("  unit@start = bag size at the starting balance. Where it equals the "
          f"${MIN_UNIT:.0f} floor,\n  the percentage is too small to lift the bag "
          "above min-notional — small accounts\n  run $6 bags no matter the %. "
          "Dynamic sizing only enlarges bags once p%×balance > floor.")


if __name__ == "__main__":
    main(sys.argv[1:])