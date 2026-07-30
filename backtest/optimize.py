"""Grid search — find the best params for a strategy over historical candles.

Loads candles once, then replays the backtest for every parameter combination
in the chosen strategy's grid and ranks them.

    source .venv/bin/activate
    python -m backtest.optimize                     # sma, 1h, 1000 candles
    python -m backtest.optimize ema 1d 1000         # strategy interval limit
    python -m backtest.optimize rsi 1h 1000 sharpe  # + rank metric

Rank metrics:  return (default) | sharpe (return/mdd) | winrate

Uses REAL history, simulates locally, never places orders.
"""

from __future__ import annotations

import sys

from src.binance_api import get_candles
from src.config import config
from src.strategy import get_strategy
from backtest.engine import run_backtest


def _score(r, metric: str) -> float:
    if metric == "sharpe":
        return r.return_pct / r.max_drawdown_pct if r.max_drawdown_pct > 0 else r.return_pct
    if metric == "winrate":
        return r.win_rate
    return r.return_pct


def _params_str(p: dict) -> str:
    return "/".join(str(v) for v in p.values())


def main(argv: list[str]) -> None:
    strategy_name = argv[0] if len(argv) > 0 else config.strategy
    interval = argv[1] if len(argv) > 1 else config.interval
    limit = int(argv[2]) if len(argv) > 2 else 1000
    metric = argv[3] if len(argv) > 3 else "return"

    strat = get_strategy(strategy_name)
    print(f"Grid search {config.symbol} | strategy={strat.name.upper()} "
          f"| {interval} | {limit} candles | rank by {metric}\n")

    candles = get_candles(interval=interval, limit=limit)
    period = f"{candles[0].open_time:%Y-%m-%d} → {candles[-1].open_time:%Y-%m-%d}"
    print(f"period: {period}  ({len(candles)} candles)\n")

    rows = []
    for params in strat.param_grid:
        if len(candles) <= strat.warmup(params) + 1:
            continue
        r = run_backtest(candles, strategy_name=strat.name, params=params)
        rows.append({
            "params": _params_str(params),
            "return": r.return_pct, "buyhold": r.buy_hold_pct,
            "trades": r.n_trades, "winrate": r.win_rate,
            "mdd": r.max_drawdown_pct, "score": _score(r, metric),
        })

    rows.sort(key=lambda x: x["score"], reverse=True)

    print(f"{'params':>12} {'return%':>8} {'b&h%':>7} "
          f"{'trades':>6} {'win%':>5} {'mdd%':>6}")
    print("-" * 50)
    for x in rows[:15]:
        print(f"{x['params']:>12} {x['return']:>+8.2f} "
              f"{x['buyhold']:>+7.2f} {x['trades']:>6} "
              f"{x['winrate']:>4.0f}% {x['mdd']:>6.2f}")

    if rows:
        best = rows[0]
        print(f"\nBest ({metric}) for {strat.name.upper()}: {best['params']}  "
              f"-> {best['return']:+.2f}%  (buy&hold {best['buyhold']:+.2f}%)")
        print("Set the matching params in .env, then re-check on a DIFFERENT "
              "period before trusting it.")


if __name__ == "__main__":
    main(sys.argv[1:])