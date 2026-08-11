"""Geometry sweep — find the efficiency frontier of the grid: which (step, TP,
max_bags) banks a healthy stream while keeping bags from sitting frozen for long.

For each config, on the live basket (continuous ~2.7y, 4h), reports:
  stream    realized profit (the "ручеёк")
  equityΔ   mark-to-market money made
  dd        max drawdown
  avgHold   mean DAYS a sold bag was held  (turnover speed — low = bags cycle fast)
  maxHold   worst freeze among sold bags, DAYS
  endAge    oldest still-open bag at the end, DAYS (current stagnation)
  frozen%   time-avg share of capital sitting underwater (stagnation load)

The current live geometry (step 0.5 / TP 0.6 / max 40) is marked ◀ so you can see
where it sits and pick a point with similar stream but faster bag clearance.

    python -m backtest.geometry_sweep            # 4h, ~2.7y
    python -m backtest.geometry_sweep 4h 6000
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
START = config.paper_start_balance
UNIT = _P.unit_usdt
BARS_PER_DAY = 6   # 4h candles

# sweep grid — current live values included so it's the reference point
STEPS = [0.5, 0.8, 1.2]
TPS = [0.6, 1.0]
MAXBAGS = [40, 20]
CUR = (_P.step_pct, _P.tp_pct, _P.max_bags)   # (0.5, 0.6, 40)


def _block(closes, win):
    return [(sma(closes[: i + 1], win) is None) or (closes[i] <= sma(closes[: i + 1], win))
            for i in range(len(closes))]


def main(argv):
    interval = argv[0] if len(argv) > 0 else "4h"
    limit = int(argv[1]) if len(argv) > 1 else 6000

    # fetch once per coin, reuse across all configs
    data = {}
    for coin in BASKET:
        try:
            candles = get_candles(symbol=coin, interval=interval, limit=limit)
        except Exception as exc:
            print(f"  ({coin} skipped: {str(exc)[:50]})"); continue
        if len(candles) < _P.sma_win + 50:
            continue
        closes = [c.close for c in candles]
        data[coin] = (candles, _block(closes, _P.sma_win))
    if not data:
        print("no data"); return

    span = next(iter(data.values()))[0]
    span = f"{span[0].open_time:%Y-%m-%d} → {span[-1].open_time:%Y-%m-%d}"
    print(f"Geometry sweep | live basket {BASKET} | {interval} | {span}")
    print(f"unit {UNIT} · SMA{_P.sma_win} · {START:.0f} start | ◀ = current live geometry\n")
    print(f"  {'step':>4} {'TP':>4} {'maxB':>4} | {'stream':>7} {'equityΔ':>8} {'dd':>5} | "
          f"{'avgHold':>7} {'maxHold':>7} {'endAge':>6} {'frozen%':>7}")
    print("  " + "-" * 82)

    rows = []
    for mb in MAXBAGS:
        for tp in TPS:
            for step in STEPS:
                agg = {"stream": 0.0, "eq": 0.0, "dd": 0.0, "hold_sum": 0.0,
                       "closed": 0, "maxhold": 0, "endage": 0, "frozen": 0.0}
                for candles, block in data.values():
                    r = run_grid(candles, unit_usdt=UNIT, tp_pct=tp, grid_step_pct=step,
                                 start_balance=START, stop_pct=0.0, block_entry=block,
                                 max_bags=mb, pool_tp=False)
                    agg["stream"] += r.realized
                    agg["eq"] += r.end_equity - START
                    agg["dd"] += r.max_drawdown_pct
                    agg["hold_sum"] += r.avg_hold_bars * r.closed_trades
                    agg["closed"] += r.closed_trades
                    agg["maxhold"] = max(agg["maxhold"], r.max_hold_bars)
                    agg["endage"] = max(agg["endage"], r.end_max_age_bars)
                    agg["frozen"] += r.frozen_cap_pct
                n = len(data)
                avg_hold = (agg["hold_sum"] / agg["closed"]) if agg["closed"] else 0.0
                mark = " ◀" if (step, tp, mb) == CUR else ""
                rows.append(((step, tp, mb), agg, avg_hold, mark))
                print(f"  {step:>4} {tp:>4} {mb:>4} | {agg['stream']:>+7.0f} "
                      f"{agg['eq']:>+8.0f} {agg['dd']/n:>4.1f}% | "
                      f"{avg_hold/BARS_PER_DAY:>6.1f}d {agg['maxhold']/BARS_PER_DAY:>6.0f}d "
                      f"{agg['endage']/BARS_PER_DAY:>5.0f}d {agg['frozen']/n:>6.2f}%{mark}")

    print("\n  Read: lower avgHold/maxHold/endAge/frozen% = bags cycle faster (less "
          "stagnation).\n  Look for a row with stream close to the ◀ current one but "
          "clearly lower hold/frozen.")


if __name__ == "__main__":
    main(sys.argv[1:])