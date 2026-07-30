"""Decision policy — combine market regime with the strategy signal.

This is the gating layer the user asked for: the tendency (regime) is computed
first, then it decides whether the strategy's raw signal is allowed, blocked, or
overridden with an exit. Runner and backtest both go through `decide()` so live
and simulated behaviour are identical.

Behaviour matrix (3 strategies × 3 regimes), long-only spot:

              BULL          NEUTRAL        BEAR
    sma       active        active         cash (exit)
    ema       active        aside          cash (exit)
    rsi       aside         active         aside

RSI is mean-reversion: it is meant to hold a dip and wait for the bounce, so in
BEAR it stands aside (no new buys, but no forced cash-exit) rather than selling
at the bottom the way the trend strategies correctly do.

Zones:
    active : use the strategy signal as-is.
    aside  : block new BUYs (wrong regime for this strategy); still allow the
             strategy's own SELL to close an existing position.
    cash   : no new BUYs and force-exit to cash (SELL if in position). This is
             the BEAR capital-protection rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from .binance_api import Candle
from .config import config
from .regime import Regime, detect_regime
from .strategy import Signal, get_strategy

# strategy -> regime -> zone
ZONES: dict[str, dict[Regime, str]] = {
    "sma": {Regime.BULL: "active", Regime.NEUTRAL: "active", Regime.BEAR: "cash"},
    "ema": {Regime.BULL: "active", Regime.NEUTRAL: "aside", Regime.BEAR: "cash"},
    "rsi": {Regime.BULL: "aside", Regime.NEUTRAL: "active", Regime.BEAR: "aside"},
    # breakout is momentum: ride trends, sit out bear (like the MA strategies).
    "breakout": {Regime.BULL: "active", Regime.NEUTRAL: "active", Regime.BEAR: "cash"},
}


@dataclass(frozen=True)
class Decision:
    signal: Signal        # final, gated signal to act on
    raw_signal: Signal    # what the strategy alone wanted
    regime: Regime | None
    zone: str             # active | aside | cash | off
    reason: str


def _gate(zone: str, raw: Signal) -> Signal:
    if zone == "cash":
        # Force exit: SELL is a no-op when already flat (broker checks position).
        return Signal.SELL
    if zone == "aside":
        # Block new entries; let an existing position exit on its own signal.
        return Signal.HOLD if raw is Signal.BUY else raw
    return raw  # active


def decide(
    candles: list[Candle],
    strategy_name: str | None = None,
    params: dict | None = None,
    use_regime: bool | None = None,
    regime_override: Regime | None = None,
) -> Decision:
    strat = get_strategy(strategy_name)
    params = params or strat.default_params
    raw = strat.evaluate(candles, params).signal

    use_regime = config.regime_gate if use_regime is None else use_regime
    if not use_regime:
        return Decision(raw, raw, None, "off", "regime gate disabled")

    # regime_override lets the caller supply a higher-timeframe regime
    # (multi-timeframe gating); otherwise read it from the trading candles.
    regime = regime_override if regime_override is not None else detect_regime(candles).regime
    zone = ZONES[strat.name][regime]
    final = _gate(zone, raw)

    if zone == "cash":
        reason = f"{regime.value}: exit to cash"
    elif zone == "aside":
        reason = f"{regime.value}: {strat.name.upper()} stands aside (no new buys)"
    else:
        reason = f"{regime.value}: {strat.name.upper()} active"

    return Decision(final, raw, regime, zone, reason)