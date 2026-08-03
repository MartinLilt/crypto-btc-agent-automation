"""Out-of-sample (walk-forward) validation of the autonomous selection.

The autopilot picks the coin+strategy it earned most on RECENTLY — which risks
overfitting. This tests whether that choice survives on data it never saw:

    For each fold:
      • SELECT  — on the training window, scan the universe and pick the best
                  coin+strategy (exactly what the autopilot does).
      • TEST    — run that same coin+strategy on the NEXT window (out-of-sample)
                  and measure the return earned purely in that window.

Anchored walk-forward: the training window grows, the test window is the next
unseen chunk. Warmup for the test carries over from prior candles, so the whole
test window is tradeable (not wasted on warmup).

    source .venv/bin/activate
    python -m backtest.walkforward            # defaults: 4h, regime 1d
    python -m backtest.walkforward 4h 1d 3    # interval regime folds
"""

from __future__ import annotations

import sys

from src.binance_api import get_candles
from src.config import config
from src.regime import tf_factor
from src.strategy import STRATEGIES
from backtest.engine import run_backtest

_MIN_TRADES = 3


def _select(coin_candles: dict, end: int, interval: str, reg_tf: str):
    """Autopilot selection restricted to candles[:end]: best coin+strategy.

    Uses the SAME robust (repeated-edge) scoring the live autopilot uses, so the
    OOS test judges the real selection method.
    """
    from src.autopilot import _robust_eval

    best = None
    for coin, candles in coin_candles.items():
        sub = candles[:end]
        for name in STRATEGIES:
            pick = _robust_eval(sub, name, interval, reg_tf)
            if pick is None:
                continue
            if best is None or pick.robust_score > best[2]:
                best = (coin, name, pick.robust_score, pick.net_return_pct)
    return best  # (coin, strategy, robust_score, in-sample net) or None


def _oos_return(candles, name, interval, reg_tf, train_end, test_end) -> float | None:
    """Return earned by (name) on candles between train_end and test_end."""
    r = run_backtest(candles[:test_end], name, STRATEGIES[name].default_params,
                     1000.0, 1000.0, use_regime=True,
                     trading_interval=interval, regime_interval=reg_tf)
    w = r.warmup
    if not r.equity_curve or train_end - 1 < w:
        return None

    def eq_at(j: int) -> float:
        k = min(max(j - w, 0), len(r.equity_curve) - 1)
        return r.equity_curve[k]

    start_eq = eq_at(train_end - 1)
    end_eq = eq_at(test_end - 1)
    if start_eq <= 0:
        return None
    return (end_eq / start_eq - 1) * 100


def main(argv: list[str]) -> None:
    interval = argv[0] if len(argv) > 0 else config.interval
    reg_tf = argv[1] if len(argv) > 1 else config.regime_interval
    folds = int(argv[2]) if len(argv) > 2 else 3
    per_seg = int(argv[3]) if len(argv) > 3 else 220  # candles per segment

    factor = tf_factor(interval, reg_tf)
    warmup = (config.regime_ma + config.regime_slope_lookback + 1) * factor
    # Fetch enough history: warmup + room for training + all test folds.
    total = warmup + per_seg * (folds + 1)

    print(f"Walk-forward OOS | trade={interval} regime={reg_tf} | {folds} folds "
          f"| net of fees+tax\n")
    print(f"Fetching {total} candles/coin for {len(config.target_coins)} coins...")

    from src.universe import get_universe
    coin_candles = {}
    for coin in get_universe():
        try:
            c = get_candles(symbol=coin, interval=interval, limit=total)
            if len(c) >= warmup + per_seg * 2:
                coin_candles[coin] = c
        except Exception:
            pass
    if not coin_candles:
        print("Not enough data.")
        return

    n = min(len(c) for c in coin_candles.values())
    usable = n - warmup
    seg = usable // (folds + 1)
    print(f"{n} candles, {seg} per segment\n")

    # Fold boundaries in candle-index space (first test starts after 1 train seg).
    print(f"{'fold':>4} {'period(train→test)':>20} {'pick (IS)':>18} "
          f"{'IS net%':>8} {'OOS net%':>9}")
    print("-" * 66)
    oos_results = []
    for f in range(folds):
        train_end = warmup + seg * (f + 1)
        test_end = min(warmup + seg * (f + 2), n)
        chosen = _select(coin_candles, train_end, interval, reg_tf)
        if chosen is None:
            print(f"{f+1:>4} {'—':>20} {'(none — cash)':>18} {'—':>8} {'0.00':>9}")
            oos_results.append(0.0)
            continue
        coin, name, _score, is_net = chosen
        oos = _oos_return(coin_candles[coin], name, interval, reg_tf,
                          train_end, test_end)
        oos = 0.0 if oos is None else oos
        oos_results.append(oos)
        label = f"{name.upper()} {coin.replace('USDT','')}"
        print(f"{f+1:>4} {f'seg{f+1}→seg{f+2}':>20} {label:>18} "
              f"{is_net:>+7.2f}% {oos:>+8.2f}%")

    print("-" * 66)
    pos = sum(1 for x in oos_results if x > 0)
    avg = sum(oos_results) / len(oos_results) if oos_results else 0.0
    compounded = 1.0
    for x in oos_results:
        compounded *= (1 + x / 100)
    print(f"OOS folds positive: {pos}/{len(oos_results)} | "
          f"avg OOS {avg:+.2f}% | compounded {((compounded-1)*100):+.2f}%")
    print()
    if pos > len(oos_results) / 2 and avg > 0:
        print("✅ Selection GENERALIZES — the autonomous pick holds out-of-sample.")
    elif pos == 0:
        print("❌ Selection is OVERFIT — in-sample winners lose out-of-sample.")
    else:
        print("⚠️ Mixed — edge is weak/inconsistent out-of-sample. Treat with caution.")


if __name__ == "__main__":
    main(sys.argv[1:])