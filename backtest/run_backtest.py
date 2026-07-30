"""Run a backtest of a strategy over historical LTC candles.

    source .venv/bin/activate
    python -m backtest.run_backtest                 # defaults from .env
    python -m backtest.run_backtest 1d 500          # interval + how many candles
    python -m backtest.run_backtest 1h 1000 ema     # + strategy: sma | ema | rsi

Uses REAL historical data from Binance; simulates locally. Never places orders.
"""

from __future__ import annotations

import sys

from src.binance_api import get_candles
from src.config import config
from src.strategy import get_strategy
from backtest.engine import run_backtest


def main(argv: list[str]) -> None:
    interval = argv[0] if len(argv) > 0 else config.interval
    limit = int(argv[1]) if len(argv) > 1 else 1000
    strategy_name = argv[2] if len(argv) > 2 else config.strategy

    strat = get_strategy(strategy_name)
    params = strat.default_params

    print(f"Backtest {config.symbol} | {interval} | {limit} candles "
          f"| strategy={strat.name.upper()} {params}\n")

    candles = get_candles(interval=interval, limit=limit)
    if len(candles) <= strat.warmup(params) + 1:
        print(f"Not enough candles ({len(candles)}) for this strategy.")
        return

    period = f"{candles[0].open_time:%Y-%m-%d} → {candles[-1].open_time:%Y-%m-%d}"
    r = run_backtest(candles, strategy_name=strat.name, params=params)

    print(f"period          : {period}")
    print(f"start balance   : {r.start_balance:.2f} USDT")
    print(f"end equity      : {r.end_equity:.2f} USDT")
    print(f"strategy return : {r.return_pct:+.2f}%")
    print(f"buy & hold      : {r.buy_hold_pct:+.2f}%")
    print(f"trades (closed) : {r.n_trades}")
    print(f"win rate        : {r.win_rate:.0f}%  ({r.wins}/{r.n_trades})")
    print(f"max drawdown    : {r.max_drawdown_pct:.2f}%")

    if r.trades:
        print("\nlast trades:")
        for t in r.trades[-6:]:
            pnl = f"  pnl {t.pnl:+.2f}" if t.side == "SELL" else ""
            print(f"  {t.time}  {t.side:4} {t.base_qty:.4f} LTC @ {t.price:.2f}{pnl}")


if __name__ == "__main__":
    main(sys.argv[1:])