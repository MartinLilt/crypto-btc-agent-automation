"""Opportunity finder — where does our system make money even in a down market?

The market has been broadly declining since early 2025, yet short-term
mean-reversion can still extract profit from volatility (buy dips, sell
bounces) without needing an uptrend. This sweeps the whole grid:

    coins × timeframes × strategies   (gated, multi-TF, net of fees + tax)

and reports every profitable combination, best first — the concrete
"earning potential" pockets in the current market.

    source .venv/bin/activate
    python -m backtest.opportunities
"""

from __future__ import annotations

from src.binance_api import get_candles
from src.config import config
from src.strategy import get_strategy
from src.universe import get_universe

# (trading interval, candles, regime timeframe) — multi-horizon view.
# 1h needs 61*24≈1464 bars just to warm up a daily regime, so fetch ~150 days.
HORIZONS = [
    ("1h", 3600, "1d"),   # ~150 days, short-term
    ("4h", 1000, "1d"),   # ~166 days, swing
    ("1d", 365, "1d"),    # 1 year, positional
]
STRATEGIES = ["sma", "ema", "rsi"]


def main() -> None:
    from backtest.engine import run_backtest

    universe = get_universe()
    print("Opportunity scan | gated, multi-TF | net of Binance fees + LT tax "
          "| 1000 all-in\n")
    print(f"universe ({len(universe)}): {', '.join(universe)}\n")

    rows = []
    for interval, limit, reg_tf in HORIZONS:
        for coin in universe:
            try:
                candles = get_candles(symbol=coin, interval=interval, limit=limit)
                if len(candles) < 120:
                    continue
                bh = (candles[-1].close / candles[0].close - 1) * 100
                for name in STRATEGIES:
                    s = get_strategy(name)
                    r = run_backtest(candles, name, s.default_params,
                                     1000.0, 1000.0, use_regime=True,
                                     trading_interval=interval, regime_interval=reg_tf)
                    rows.append({
                        "coin": coin, "tf": interval, "strat": name.upper(),
                        "net": r.net_return_pct(), "bh": bh,
                        "trades": r.n_trades, "win": r.win_rate,
                    })
            except Exception as exc:
                print(f"  ({coin} {interval} skipped: {str(exc)[:50]})")

    winners = sorted([r for r in rows if r["net"] > 0],
                     key=lambda x: x["net"], reverse=True)

    print(f"Scanned {len(rows)} combinations — {len(winners)} were profitable.\n")
    print(f"{'coin':>9} {'tf':>4} {'strat':>5} {'net%':>8} {'buy&hold%':>10} "
          f"{'trades':>7} {'win%':>5}")
    print("-" * 56)
    for r in winners[:20]:
        print(f"{r['coin']:>9} {r['tf']:>4} {r['strat']:>5} {r['net']:>+7.2f}% "
              f"{r['bh']:>+9.2f}% {r['trades']:>7} {r['win']:>4.0f}%")

    if winners:
        best = winners[0]
        print(f"\n🏆 Best pocket: {best['strat']} on {best['coin']} {best['tf']} "
              f"→ {best['net']:+.2f}% net (buy&hold {best['bh']:+.2f}%)")
        # How many coins have at least one profitable setup right now?
        good_coins = {r["coin"] for r in winners}
        print(f"   {len(good_coins)}/{len(config.target_coins)} coins have a "
              f"profitable setup even in this market.")
    else:
        print("\nNo profitable setup anywhere — market too weak, stay in cash.")


if __name__ == "__main__":
    main()