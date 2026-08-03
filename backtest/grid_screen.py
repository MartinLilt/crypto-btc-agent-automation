"""Grid-suitability analyzer — which coins fit the stream + bag + feast plan.

Runs mode C (grid + uptrend filter) on each coin over the bear market since the
Trump inauguration (~Jan 2025) and scores it for the strategy we committed to:

  stream%  : realized cash the grid banked over the period (the "ручеёк")
  equity%  : mark-to-market now (cash + frozen bags) — capital preservation
  feast%   : payoff if every frozen bag recovers to its TP (bull-reversal upside)
  maxdd%   : worst drawdown endured (the pain of holding bags)
  bags     : frozen bags at the end / peak (capital lock)

A good coin: steady positive stream, capital preserved, real feast upside, AND
— critically — a coin that will actually RECOVER. Blue-chips (BTC/ETH/major L1s)
reliably do; many small-caps never return, so they're flagged ⚠ risky-to-bag.

    python -m backtest.grid_screen            # dynamic universe, 4h
    python -m backtest.grid_screen 4h 3400    # interval, candles (~since Jan'25)
"""

from __future__ import annotations

import sys

from src.binance_api import get_candles
from src.config import config
from src.grid import params_from_config
from src.strategy import sma
from src.universe import get_universe
from backtest.scalp import run_grid

# Coins whose "bags will eventually recover" thesis is historically reliable.
_BLUE_CHIP = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "TRXUSDT", "LINKUSDT", "AVAXUSDT", "DOGEUSDT", "LTCUSDT", "DOTUSDT",
    "MATICUSDT", "UNIUSDT", "AAVEUSDT", "NEARUSDT", "ATOMUSDT",
}

# Grid params — pulled from the live config so the screen mirrors the real bot.
_P = params_from_config()
TP_PCT, GRID_STEP, SMA_WIN = _P.tp_pct, _P.step_pct, _P.sma_win
UNIT, MAX_BAGS, START = _P.unit_usdt, _P.max_bags, config.paper_start_balance


def _uptrend_block(candles, win):
    closes = [c.close for c in candles]
    return [
        (sma(closes[: i + 1], win) is None) or (closes[i] <= sma(closes[: i + 1], win))
        for i in range(len(candles))
    ]


def main(argv):
    interval = argv[0] if len(argv) > 0 else "4h"
    limit = int(argv[1]) if len(argv) > 1 else 3400  # ~18 months of 4h

    universe = get_universe()
    print(f"Grid-suitability | live cfg: TP {TP_PCT}% / step {GRID_STEP}% / SMA{SMA_WIN} "
          f"/ unit {UNIT} / max {MAX_BAGS} bags | {interval} | {limit} candles | {START:.0f} start\n")

    rows = []
    for coin in universe:
        try:
            candles = get_candles(symbol=coin, interval=interval, limit=limit)
            if len(candles) < SMA_WIN + 50:
                continue
            block = _uptrend_block(candles, SMA_WIN)
            r = run_grid(candles, UNIT, TP_PCT, GRID_STEP, START,
                         stop_pct=0.0, block_entry=block, max_bags=MAX_BAGS)
            bh = (candles[-1].close / candles[0].close - 1) * 100
            rows.append({
                "coin": coin, "stream": r.realized, "equity": r.return_pct,
                "feast": r.recovery_pct, "dd": r.max_drawdown_pct,
                "bags": r.open_bags, "peak": r.peak_bags, "bh": bh,
                "start": candles[0].open_time,
                # score: reward stream + capital preservation + feast, penalise dd
                "score": r.realized + r.return_pct * 10 + r.recovery_pct * 5 - r.max_drawdown_pct * 3,
                "blue": coin in _BLUE_CHIP,
            })
        except Exception as exc:
            print(f"  ({coin} skipped: {str(exc)[:45]})")

    rows.sort(key=lambda x: x["score"], reverse=True)
    if rows:
        print(f"period from ~{rows[0]['start']:%Y-%m-%d} (Trump-era bear)\n")

    print(f"{'coin':>10} {'stream':>8} {'equity%':>8} {'feast%':>7} {'dd%':>6} "
          f"{'bags':>5} {'coin b&h%':>9} {'recover?':>9}")
    print("-" * 74)
    for x in rows:
        tag = "blue-chip" if x["blue"] else "⚠ risky"
        print(f"{x['coin']:>10} {x['stream']:>+8.1f} {x['equity']:>+7.2f}% "
              f"{x['feast']:>+6.1f}% {x['dd']:>5.1f}% {x['bags']:>4}/{x['peak']:<2} "
              f"{x['bh']:>+8.1f}% {tag:>9}")

    # Strategy fit = reliable-recovery coin (blue-chip) with a real stream that
    # BEAT buy&hold (capital better preserved than just holding the coin).
    picks = sorted(
        [x for x in rows if x["blue"] and x["stream"] > 0 and x["equity"] > x["bh"]],
        key=lambda x: x["stream"] + x["feast"] * 5 - x["dd"], reverse=True,
    )
    print()
    if picks:
        print("🌊 Grid-stream picks (blue-chip · +stream · beat buy&hold · survivable dd):")
        for x in picks[:5]:
            print(f"   {x['coin']:<9} stream +{x['stream']:.0f} · equity {x['equity']:+.1f}% "
                  f"(coin {x['bh']:+.0f}%) · feast {x['feast']:+.0f}% · dd {x['dd']:.0f}%")
        print("   These preserved capital far better than holding, drip a stream, "
              "and carry feast upside when the cycle turns.")
    else:
        print("No blue-chip fits right now.")


if __name__ == "__main__":
    main(sys.argv[1:])