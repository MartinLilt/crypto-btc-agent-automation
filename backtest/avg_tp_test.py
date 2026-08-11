"""Idea #1 test — pool take-profit (sell the whole stack at avg-cost + TP)
versus the current per-bag take-profit, on the live basket.

Goal: do frozen bags convert to realized "feast" faster when a small bounce to
the average releases the whole stack at once, instead of waiting for each bag to
individually clear its own +TP?

Same everything else (uptrend gate, unit, step, SMA, max bags, live config) —
only the exit rule differs. Reports per coin, per calendar year (balance reset =
OOS character check) and a continuous full-period run (bags carry over = the real
grid behaviour), for BASELINE vs POOL side by side.

    python -m backtest.avg_tp_test            # 4h, ~2.7y, live basket
    python -m backtest.avg_tp_test 4h 6000
"""

from __future__ import annotations

import sys
from collections import defaultdict

from src.binance_api import get_candles
from src.config import config
from src.grid import params_from_config
from src.strategy import sma
from backtest.scalp import run_grid

_P = params_from_config()
# Live basket, but on USDT pairs (deeper history than the USDC listings).
BASKET = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
START = config.paper_start_balance


def _block(candles, win):
    """Per-bar uptrend gate: True = price at/below its long SMA → don't open."""
    closes = [c.close for c in candles]
    return [
        (sma(closes[: i + 1], win) is None) or (closes[i] <= sma(closes[: i + 1], win))
        for i in range(len(candles))
    ]


def _run(candles, block, pool_tp):
    return run_grid(candles, unit_usdt=_P.unit_usdt, tp_pct=_P.tp_pct,
                    grid_step_pct=_P.step_pct, start_balance=START, stop_pct=0.0,
                    block_entry=block, max_bags=_P.max_bags, pool_tp=pool_tp)


def _fmt(r):
    return (f"stream {r.realized:>+7.1f} · equity {r.return_pct:>+6.2f}% · "
            f"feast {r.recovery_pct:>+6.1f}% · dd {r.max_drawdown_pct:>4.1f}% · "
            f"bags {r.open_bags:>2}/{r.peak_bags:<2} · trades {r.closed_trades}")


def main(argv):
    interval = argv[0] if len(argv) > 0 else "4h"
    limit = int(argv[1]) if len(argv) > 1 else 6000  # ~2.7y of 4h

    print(f"Idea #1: POOL take-profit (avg-cost+TP) vs BASELINE (per-bag TP)")
    print(f"live cfg: TP {_P.tp_pct}% · step {_P.step_pct}% · SMA{_P.sma_win} · "
          f"unit {_P.unit_usdt} · max {_P.max_bags} bags · {interval} · "
          f"{limit} candles · {START:.0f} start\n")

    agg = defaultdict(lambda: {"base": None, "pool": None})
    cont_base_tot = {"stream": 0.0, "eq": 0.0, "feast": 0.0}
    cont_pool_tot = {"stream": 0.0, "eq": 0.0, "feast": 0.0}

    for coin in BASKET:
        try:
            candles = get_candles(symbol=coin, interval=interval, limit=limit)
        except Exception as exc:
            print(f"  ({coin} skipped: {str(exc)[:50]})")
            continue
        if len(candles) < _P.sma_win + 50:
            print(f"  ({coin}: only {len(candles)} candles, skipped)")
            continue
        block = _block(candles, _P.sma_win)
        span = f"{candles[0].open_time:%Y-%m-%d} → {candles[-1].open_time:%Y-%m-%d}"

        # ── continuous (bags carry the whole period = real grid behaviour) ──
        cb = _run(candles, block, pool_tp=False)
        cp = _run(candles, block, pool_tp=True)
        print(f"■ {coin}   [{span}]  (continuous)")
        print(f"    BASELINE  {_fmt(cb)}")
        print(f"    POOL      {_fmt(cp)}")
        for tot, r in ((cont_base_tot, cb), (cont_pool_tot, cp)):
            tot["stream"] += r.realized
            tot["eq"] += r.end_equity - START
            tot["feast"] += r.recovery_equity - START

        # ── per calendar year (balance reset each year = OOS character) ──
        by_year = defaultdict(list)
        for i, c in enumerate(candles):
            by_year[c.open_time.year].append(i)
        for yr in sorted(by_year):
            idx = by_year[yr]
            if len(idx) < _P.sma_win:      # too short to warm the SMA gate
                continue
            yc = [candles[i] for i in idx]
            yb = [block[i] for i in idx]
            rb = _run(yc, yb, pool_tp=False)
            rp = _run(yc, yb, pool_tp=True)
            print(f"      {yr}  base: {_fmt(rb)}")
            print(f"      {yr}  pool: {_fmt(rp)}")
        print()

    def _line(tag, t):
        print(f"  {tag:8} stream {t['stream']:>+8.1f} · equity Δ {t['eq']:>+8.1f} · "
              f"feast Δ {t['feast']:>+8.1f}  (sum across {len(BASKET)} coins)")

    print("═" * 78)
    print("BASKET TOTAL (continuous, per-coin independent, summed):")
    _line("BASELINE", cont_base_tot)
    _line("POOL", cont_pool_tot)
    d_stream = cont_pool_tot["stream"] - cont_base_tot["stream"]
    d_feast = cont_pool_tot["feast"] - cont_base_tot["feast"]
    print(f"\n  Δ POOL−BASE:  stream {d_stream:+.1f} · feast {d_feast:+.1f}")
    print("  (idea #1 wins if it banks MORE stream and/or leaves LESS locked in "
          "frozen bags — i.e. higher realized, feast pulled forward into cash.)")


if __name__ == "__main__":
    main(sys.argv[1:])