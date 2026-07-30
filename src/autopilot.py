"""Autopilot — the autonomous decision chain.

Everything except the trading MODE is chosen by the system:

    1. target coin  — the coin our system is most PROFITABLE on right now
                      (net of fees+tax), scanning the universe. A promising coin
                      is one we can earn on — short-term reversion earns in flat/
                      down markets too, so this is not a plain uptrend filter.
    2. strategy     — picked jointly with the coin: the best-performing strategy
                      FOR that coin (backtested, gated, multi-TF, net).
    3. tendency     — regime read on the higher timeframe for the chosen coin.

If no coin yields a profitable, non-fluke setup -> stay in cash. The user only
flips TRADING_MODE (paper/live). `plan()` returns the full autonomous decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import config
from .regime import Regime, detect_regime, tf_factor
from .strategy import STRATEGIES


_MIN_TRADES = 4    # avoid crowning a coin on one lucky trade
_N_SEG = 3         # sub-periods used to check the edge is repeated, not a fluke


@dataclass
class StrategyPick:
    name: str
    net_return_pct: float
    trades: int
    win_rate: float
    robust_score: float = 0.0   # avg sub-period return; the ranking metric
    consistency: float = 0.0    # fraction of sub-periods that were positive


@dataclass
class Plan:
    symbol: str | None          # None -> nothing worth trading, stay in cash
    strategy: str | None
    regime: Regime | None
    strategy_pick: StrategyPick | None
    reason: str


def _candles_for(symbol: str, interval: str, regime_interval: str):
    """Fetch enough candles to warm up both the strategy and the multi-TF regime."""
    from src.binance_api import get_candles

    factor = tf_factor(interval, regime_interval)
    warmup = (config.regime_ma + config.regime_slope_lookback + 1) * factor
    limit = warmup + 600  # leave a healthy tradeable window after warmup
    return get_candles(symbol=symbol, interval=interval, limit=limit)


def _robust_eval(candles, name, interval, regime_interval) -> StrategyPick | None:
    """Score a strategy by REPEATED edge, not one recent window (anti-overfit).

    Runs the full gated/exit-managed backtest, then slices the equity curve into
    sub-periods. A pick must be net-positive overall, positive in a MAJORITY of
    sub-periods AND in the most recent one, with enough trades. Ranked by the
    average sub-period return (rewards a steady edge, not a single lucky spike).
    """
    from backtest.engine import run_backtest

    r = run_backtest(candles, name, STRATEGIES[name].default_params, 1000.0, 1000.0,
                     use_regime=True, trading_interval=interval,
                     regime_interval=regime_interval)
    ec = r.equity_curve
    if r.n_trades < _MIN_TRADES or r.net_return_pct() <= 0 or len(ec) < _N_SEG * 4:
        return None

    seg = len(ec) // _N_SEG
    seg_rets = []
    for k in range(_N_SEG):
        a, b = ec[k * seg], ec[(k + 1) * seg - 1]
        seg_rets.append((b / a - 1) * 100 if a > 0 else 0.0)

    positive = sum(1 for x in seg_rets if x > 0)
    if positive < _N_SEG - 1 or seg_rets[-1] <= 0:  # majority + recent must hold
        return None

    return StrategyPick(name, r.net_return_pct(), r.n_trades, r.win_rate,
                        robust_score=sum(seg_rets) / len(seg_rets),
                        consistency=positive / _N_SEG)


def select_strategy(
    symbol: str,
    interval: str | None = None,
    regime_interval: str | None = None,
) -> StrategyPick | None:
    """Best strategy for the coin by robust (repeated-edge) score, else None."""
    interval = interval or config.interval
    regime_interval = regime_interval or config.regime_interval
    candles = _candles_for(symbol, interval, regime_interval)

    best: StrategyPick | None = None
    for name in STRATEGIES:
        pick = _robust_eval(candles, name, interval, regime_interval)
        if pick is None:
            continue
        if best is None or pick.robust_score > best.robust_score:
            best = pick
    return best  # None if no strategy shows a repeated, non-fluke edge here


def _tendency(symbol: str) -> Regime | None:
    from src.binance_api import get_candles
    if not config.regime_gate:
        return None
    rc = get_candles(symbol=symbol, interval=config.regime_interval,
                     limit=config.regime_ma + config.regime_slope_lookback + 5)
    return detect_regime(rc).regime


def plan(symbol: str | None = None) -> Plan:
    """Run the full autonomous chain: coin (+strategy) -> tendency."""
    # Fixed coin path: only the strategy is auto-picked.
    if not config.auto_target:
        symbol = symbol or config.symbol
        if config.auto_strategy:
            pick = select_strategy(symbol)
            if pick is None:
                return Plan(None, None, None, None,
                            f"{symbol}: no profitable strategy — stay in cash")
            strategy = pick.name
        else:
            strategy, pick = config.strategy, None
        return Plan(symbol, strategy, _tendency(symbol), pick,
                    f"trade {strategy.upper()} on {symbol}")

    # Autonomous path: scan the universe, pick the coin+strategy we earn most on.
    best_symbol: str | None = None
    best_pick: StrategyPick | None = None
    for coin in config.target_coins:
        try:
            pick = select_strategy(coin)
        except Exception:
            continue
        if pick is None:
            continue
        if best_pick is None or pick.robust_score > best_pick.robust_score:
            best_symbol, best_pick = coin, pick

    if best_symbol is None:
        return Plan(None, None, None, None,
                    "no coin yields a profitable setup — stay in cash")

    # If AUTO_STRATEGY is off, honour the fixed strategy on the chosen coin.
    strategy = best_pick.name if config.auto_strategy else config.strategy
    return Plan(best_symbol, strategy, _tendency(best_symbol), best_pick,
                f"trade {strategy.upper()} on {best_symbol}")