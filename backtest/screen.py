"""Screen the coin universe and name the target coin (justified selection).

    source .venv/bin/activate
    python -m backtest.screen                # defaults from .env (1d, 365, strategy)
    python -m backtest.screen 1d 365 rsi     # interval limit strategy

Judges each coin on a LONG window: trend filter + system edge + consistency
across sub-periods. Only coins clearing all three qualify. If none do, the
answer is "stay in cash". Never trades.
"""

from __future__ import annotations

import sys

from src.config import config
from src.screener import screen


def main(argv: list[str]) -> None:
    interval = argv[0] if len(argv) > 0 else config.target_screen_interval
    limit = int(argv[1]) if len(argv) > 1 else config.target_screen_limit
    strategy_name = argv[2] if len(argv) > 2 else config.strategy

    print(f"Target-coin screen | strategy={strategy_name.upper()} | {interval} "
          f"| {limit} candles/coin | net of fees+tax\n")
    print(f"universe: {', '.join(config.target_coins)}\n")

    ranked = screen(interval=interval, limit=limit, strategy_name=strategy_name)

    print(f"{'coin':>10} {'net%':>8} {'mom%':>8} {'regime':>8} {'consist':>7} "
          f"{'ok':>3}  reason")
    print("-" * 74)
    for c in ranked:
        if c.error:
            print(f"{c.symbol:>10}   (skipped: {c.error})")
            continue
        mark = "✓" if c.qualified else "·"
        print(f"{c.symbol:>10} {c.net_return_pct:>+7.2f}% {c.momentum_pct:>+7.2f}% "
              f"{c.regime.value:>8} {c.consistency:>6.0%} {mark:>3}  {c.reason}")

    qualified = [c for c in ranked if c.qualified]
    print()
    if qualified:
        best = qualified[0]
        print(f"🎯 TARGET COIN: {best.symbol}  "
              f"(net {best.net_return_pct:+.2f}%, {best.regime.value}, "
              f"consistency {best.consistency:.0%})")
        print(f"   Set SYMBOL={best.symbol} in .env (or AUTO_TARGET=true).")
    else:
        print("No coin qualifies — trend/edge/consistency gates all failed. "
              "STAY IN CASH (correct call in a broad bear market).")


if __name__ == "__main__":
    main(sys.argv[1:])