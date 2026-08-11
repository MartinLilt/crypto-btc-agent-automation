"""Idea #2 test — smarter ENTRY gate: add a bag only on genuine oversold /
mean-reversion, not on every -0.5% dip.

The grid's edge is per-bag churn (idea #1 confirmed the exit is fine). So to make
bought bags more likely to bounce → become feast, we open FEWER but BETTER bags:
keep the uptrend gate, but additionally require the coin to be locally oversold
before adding. Fewer bags freeze; the ones we hold sit at better prices.

Each variant is the SAME grid, only the entry gate differs (ANDed with the
existing price>SMA100 uptrend gate). Watch: does stream stay high while peak
bags / drawdown drop and equity improves?

    python -m backtest.entry_gate_test           # 4h, ~2.7y, live basket
    python -m backtest.entry_gate_test 4h 6000
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
BASKET = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
START = config.paper_start_balance


def _sma_arr(closes, win):
    """Rolling SMA per bar (None until warm)."""
    out, s = [], 0.0
    for i, v in enumerate(closes):
        s += v
        if i >= win:
            s -= closes[i - win]
        out.append(s / win if i >= win - 1 else None)
    return out


def _ema_arr(closes, win):
    out = [None] * len(closes)
    if len(closes) < win:
        return out
    k = 2 / (win + 1)
    e = sum(closes[:win]) / win
    out[win - 1] = e
    for i in range(win, len(closes)):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out


def _rsi_arr(closes, period):
    """Per-bar RSI matching src.strategy.rsi (simple average of last `period`
    deltas), computed in O(n) with rolling gain/loss sums."""
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    g = sum(gains[:period]); l = sum(losses[:period])
    for j in range(period - 1, len(deltas)):
        if j >= period:
            g += gains[j] - gains[j - period]
            l += losses[j] - losses[j - period]
        rs = 100.0 if l == 0 else 100 - 100 / (1 + (g / period) / (l / period))
        out[j + 1] = rs      # deltas[j] ends at close index j+1
    return out


def _base_block(closes, win):
    m = _sma_arr(closes, win)
    return [m[i] is None or closes[i] <= m[i] for i in range(len(closes))]


def _variants(candles):
    closes = [c.close for c in candles]
    base = _base_block(closes, _P.sma_win)
    rsi14 = _rsi_arr(closes, 14)
    ema20 = _ema_arr(closes, 20)
    n = len(closes)

    def combine(cond):
        # block if base blocks OR the extra oversold condition is not met
        return [base[i] or not cond(i) for i in range(n)]

    return {
        "BASE (uptrend only)": base,
        "RSI14<40": combine(lambda i: rsi14[i] is not None and rsi14[i] < 40),
        "RSI14<35": combine(lambda i: rsi14[i] is not None and rsi14[i] < 35),
        "RSI14<30": combine(lambda i: rsi14[i] is not None and rsi14[i] < 30),
        "EMA20 -1%": combine(lambda i: ema20[i] is not None and closes[i] <= ema20[i] * 0.99),
        "EMA20 -1.5%": combine(lambda i: ema20[i] is not None and closes[i] <= ema20[i] * 0.985),
        "up-tick bar": combine(lambda i: i > 0 and closes[i] > closes[i - 1]),
        "RSI<40 & up-tick": combine(
            lambda i: rsi14[i] is not None and rsi14[i] < 40 and i > 0 and closes[i] > closes[i - 1]),
    }


def _run(candles, block):
    return run_grid(candles, unit_usdt=_P.unit_usdt, tp_pct=_P.tp_pct,
                    grid_step_pct=_P.step_pct, start_balance=START, stop_pct=0.0,
                    block_entry=block, max_bags=_P.max_bags, pool_tp=False)


def main(argv):
    interval = argv[0] if len(argv) > 0 else "4h"
    limit = int(argv[1]) if len(argv) > 1 else 6000

    print("Idea #2: smarter ENTRY gate (oversold) vs BASE (uptrend only)")
    print(f"live cfg: TP {_P.tp_pct}% · step {_P.step_pct}% · SMA{_P.sma_win} · "
          f"unit {_P.unit_usdt} · max {_P.max_bags} bags · {interval} · "
          f"{limit} candles · {START:.0f} start\n")

    totals = defaultdict(lambda: {"stream": 0.0, "eq": 0.0, "feast": 0.0,
                                   "dd": 0.0, "peak": 0})
    order = None
    for coin in BASKET:
        try:
            candles = get_candles(symbol=coin, interval=interval, limit=limit)
        except Exception as exc:
            print(f"  ({coin} skipped: {str(exc)[:50]})")
            continue
        if len(candles) < _P.sma_win + 50:
            continue
        variants = _variants(candles)
        order = list(variants)
        span = f"{candles[0].open_time:%Y-%m-%d} → {candles[-1].open_time:%Y-%m-%d}"
        print(f"■ {coin}   [{span}]")
        for name, block in variants.items():
            r = _run(candles, block)
            print(f"    {name:20} stream {r.realized:>+7.1f} · equity {r.return_pct:>+6.2f}% "
                  f"· feast {r.recovery_pct:>+6.1f}% · dd {r.max_drawdown_pct:>4.1f}% "
                  f"· peakbags {r.peak_bags:>2} · trades {r.closed_trades}")
            t = totals[name]
            t["stream"] += r.realized
            t["eq"] += r.end_equity - START
            t["feast"] += r.recovery_equity - START
            t["dd"] += r.max_drawdown_pct
            t["peak"] += r.peak_bags
        print()

    if not order:
        print("no data"); return
    print("═" * 82)
    print(f"BASKET TOTAL ({len(BASKET)} coins, continuous):")
    print(f"  {'variant':20} {'stream':>8} {'equityΔ':>9} {'feastΔ':>9} "
          f"{'avg dd':>7} {'Σpeak':>6}")
    for name in order:
        t = totals[name]
        print(f"  {name:20} {t['stream']:>+8.1f} {t['eq']:>+9.1f} {t['feast']:>+9.1f} "
              f"{t['dd'] / len(BASKET):>6.1f}% {t['peak']:>6}")
    print("\n  Goal: keep stream high while cutting Σpeak bags (fewer freeze) and dd,"
          "\n  and lifting equityΔ (less unrealized loss). Best = strong stream + fewer bags.")


if __name__ == "__main__":
    main(sys.argv[1:])