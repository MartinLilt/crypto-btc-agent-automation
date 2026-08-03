"""Target-coin selector — the first layer of the pipeline.

Before mode / regime / strategy, screen a universe of coins and pick the most
PROMISING one to trade — or decide nothing is worth trading and stay in cash.

A pick must be JUSTIFIED, not a recent bounce. The earlier version ranked coins
on ~30 days and picked AVAX right before it continued a −70% yearly collapse.
Selection is now about the coin being in a REAL, SUSTAINED uptrend, judged on a
LONG window of daily candles (where trend/regime read correctly). Three gates:

  1. Regime      : daily regime must NOT be BEAR (don't trade a falling market).
  2. Momentum    : price rose over the full window (momentum > 0).
  3. Consistency : the coin rose in a MAJORITY of equal sub-periods, not just
                   the latest one (kills recency bias / dead-cat bounces).

Qualified coins are ranked by momentum (strongest, steadiest uptrend first).
Our engine's net return over the window is shown as context (it depends on the
daily timeframe and is confirmed properly once we backtest the pick on 1h).
If none qualify, the target is None → stay in cash. In a broad bear market that
is the correct call.

    pick_target()  -> best qualified coin, or None
    screen()       -> full ranked table with the reasoning
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import config
from .regime import Regime, detect_regime

_MIN_SEGMENTS_POSITIVE = 0.5  # majority of sub-periods must trend up
_N_SEGMENTS = 3


def _pct_change(candles: list) -> float:
    """Price change % across a candle slice."""
    if len(candles) < 2 or candles[0].close == 0:
        return 0.0
    return (candles[-1].close / candles[0].close - 1) * 100


@dataclass
class CoinScore:
    symbol: str
    net_return_pct: float    # our system over the full window, after fees + tax
    momentum_pct: float      # buy&hold over the window (raw coin strength)
    regime: Regime           # regime on the screening timeframe (daily)
    consistency: float       # fraction of sub-periods that were net-positive
    trades: int
    win_rate: float
    qualified: bool = False
    reason: str = ""
    error: str | None = None

    @property
    def score(self) -> float:
        # Rank qualified coins by momentum; disqualified sink below all of them.
        return self.momentum_pct if self.qualified else self.momentum_pct - 1e6


def _segments(candles: list, n: int, min_len: int) -> list[list]:
    """Split candles into up to n contiguous chunks long enough to backtest."""
    size = len(candles) // n
    if size < min_len:
        return [candles]
    return [candles[i * size:(i + 1) * size] for i in range(n)]


def screen(
    coins: tuple[str, ...] | None = None,
    interval: str | None = None,
    limit: int | None = None,
    strategy_name: str | None = None,
) -> list[CoinScore]:
    """Score every candidate coin, best first."""
    # Imported here to avoid a circular import (engine -> policy -> strategy).
    from src.binance_api import get_candles
    from src.universe import get_universe
    from backtest.engine import run_backtest

    coins = coins or get_universe()
    interval = interval or config.target_screen_interval
    limit = limit or config.target_screen_limit
    strategy_name = strategy_name or config.strategy

    min_len = config.regime_ma + config.regime_slope_lookback + 20

    results: list[CoinScore] = []
    for symbol in coins:
        try:
            candles = get_candles(symbol=symbol, interval=interval, limit=limit)
            if len(candles) < min_len:
                raise ValueError(f"only {len(candles)} candles")

            full = run_backtest(candles, strategy_name=strategy_name,
                                start_balance=1000.0, spend=1000.0)
            net = full.net_return_pct()
            momentum = _pct_change(candles)
            regime = detect_regime(candles).regime

            # Trend consistency across sub-periods (recency-bias guard):
            # did the coin actually rise in a majority of them?
            segs = _segments(candles, _N_SEGMENTS, min_len)
            consistency = sum(1 for s in segs if _pct_change(s) > 0) / len(segs)

            # Three gates — all about a real, sustained uptrend.
            reasons = []
            if regime is Regime.BEAR:
                reasons.append("regime BEAR")
            if momentum <= 0:
                reasons.append("momentum ≤ 0")
            if consistency < _MIN_SEGMENTS_POSITIVE:
                reasons.append(f"uptrend only {consistency:.0%} of periods")
            qualified = not reasons

            results.append(CoinScore(
                symbol=symbol, net_return_pct=net, momentum_pct=momentum,
                regime=regime, consistency=consistency,
                trades=full.n_trades, win_rate=full.win_rate,
                qualified=qualified,
                reason="qualified" if qualified else ", ".join(reasons),
            ))
        except Exception as exc:  # keep screening the rest of the universe
            results.append(CoinScore(symbol, 0.0, 0.0, Regime.NEUTRAL, 0.0, 0, 0.0,
                                     qualified=False, reason="error",
                                     error=str(exc)[:60]))

    results.sort(key=lambda c: c.score, reverse=True)
    return results


def pick_target(
    coins: tuple[str, ...] | None = None,
    interval: str | None = None,
    limit: int | None = None,
    strategy_name: str | None = None,
) -> CoinScore | None:
    """Best qualified coin, or None if none are worth trading (stay in cash)."""
    for c in screen(coins, interval, limit, strategy_name):
        if c.qualified:
            return c
    return None