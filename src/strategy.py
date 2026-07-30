"""Trading strategies + a switcher.

Three pluggable strategies, all producing the same BUY / SELL / HOLD Signal:

  sma : SMA crossover  — fast simple MA crosses the slow one.
  ema : EMA crossover  — same idea, exponential MA (reacts faster).
  rsi : RSI reversion  — buy leaving oversold, sell leaving overbought.

The active one is chosen by STRATEGY in .env (config.strategy). Everything
downstream (runner, broker, backtest, optimizer) only touches this module's
`evaluate()` / `get_strategy()` — swap strategies without touching them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .binance_api import Candle
from .config import config


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class StrategyResult:
    signal: Signal
    reason: str
    indicators: dict = field(default_factory=dict)  # name -> value, for display


# ─────────────────────────── indicator helpers ────────────────────────────
def sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    k = 2 / (window + 1)
    e = sum(values[:window]) / window  # seed with SMA of the first window
    for v in values[window:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int) -> float | None:
    if len(values) < period + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = deltas[-period:]
    gain = sum(d for d in recent if d > 0) / period
    loss = sum(-d for d in recent if d < 0) / period
    if loss == 0:
        return 100.0
    rs = gain / loss
    return 100 - 100 / (1 + rs)


# ─────────────────────────── strategy functions ───────────────────────────
def _cross(prev_fast, prev_slow, now_fast, now_slow) -> str | None:
    """'up', 'down', or None for a fast/slow crossover."""
    if None in (prev_fast, prev_slow, now_fast, now_slow):
        return None
    if prev_fast <= prev_slow and now_fast > now_slow:
        return "up"
    if prev_fast >= prev_slow and now_fast < now_slow:
        return "down"
    return None


def _ma_cross(candles, params, ma, label):
    fast, slow = params["fast"], params["slow"]
    closes = [c.close for c in candles]
    nf, ns = ma(closes, fast), ma(closes, slow)
    pf, ps = ma(closes[:-1], fast), ma(closes[:-1], slow)
    ind = {f"{label}{fast}": nf, f"{label}{slow}": ns}

    if None in (nf, ns, pf, ps):
        return StrategyResult(Signal.HOLD, f"not enough candles (need >{slow})", ind)

    cross = _cross(pf, ps, nf, ns)
    if cross == "up":
        return StrategyResult(Signal.BUY, f"{label}{fast} crossed above {label}{slow}", ind)
    if cross == "down":
        return StrategyResult(Signal.SELL, f"{label}{fast} crossed below {label}{slow}", ind)
    trend = "above" if nf > ns else "below"
    return StrategyResult(Signal.HOLD, f"no cross (fast {trend} slow)", ind)


def _sma_cross(candles, params):
    return _ma_cross(candles, params, sma, "SMA")


def _ema_cross(candles, params):
    return _ma_cross(candles, params, ema, "EMA")


def _breakout(candles, params):
    """Donchian volatility breakout: buy new highs, sell new lows."""
    period = params["period"]
    if len(candles) < period + 1:
        return StrategyResult(Signal.HOLD, f"not enough candles (need >{period})", {})
    prior = candles[:-1][-period:]           # the N bars before the current one
    upper = max(c.high for c in prior)
    lower = min(c.low for c in prior)
    price = candles[-1].close
    ind = {"upper": upper, "lower": lower}
    if price > upper:
        return StrategyResult(Signal.BUY, f"broke above {period}-bar high", ind)
    if price < lower:
        return StrategyResult(Signal.SELL, f"broke below {period}-bar low", ind)
    return StrategyResult(Signal.HOLD, "inside channel", ind)


def _rsi_reversion(candles, params):
    period = params["period"]
    lo, hi = params["oversold"], params["overbought"]
    closes = [c.close for c in candles]
    now = rsi(closes, period)
    prev = rsi(closes[:-1], period)
    ind = {"RSI": now}

    if now is None or prev is None:
        return StrategyResult(Signal.HOLD, f"not enough candles (need >{period})", ind)

    if prev <= lo and now > lo:
        return StrategyResult(Signal.BUY, f"RSI left oversold (<{lo})", ind)
    if prev >= hi and now < hi:
        return StrategyResult(Signal.SELL, f"RSI left overbought (>{hi})", ind)
    zone = "oversold" if now < lo else "overbought" if now > hi else "neutral"
    return StrategyResult(Signal.HOLD, f"RSI {now:.0f} ({zone})", ind)


# ─────────────────────────────── registry ─────────────────────────────────
@dataclass(frozen=True)
class Strategy:
    name: str
    evaluate: Callable[[list[Candle], dict], StrategyResult]
    default_params: dict
    param_grid: list[dict]
    warmup: Callable[[dict], int]


def _grid_ma() -> list[dict]:
    grid = []
    for fast in (5, 8, 10, 12, 15, 20, 25):
        for slow in (20, 30, 40, 50, 60, 80, 100, 120):
            if fast < slow:
                grid.append({"fast": fast, "slow": slow})
    return grid


def _grid_rsi() -> list[dict]:
    grid = []
    for period in (7, 10, 14, 21):
        for lo in (25, 30, 35):
            for hi in (65, 70, 75):
                grid.append({"period": period, "oversold": lo, "overbought": hi})
    return grid


def _grid_breakout() -> list[dict]:
    return [{"period": p} for p in (10, 15, 20, 30, 40, 55)]


STRATEGIES: dict[str, Strategy] = {
    "sma": Strategy(
        "sma", _sma_cross,
        {"fast": config.sma_fast, "slow": config.sma_slow},
        _grid_ma(), lambda p: p["slow"] + 1,
    ),
    "ema": Strategy(
        "ema", _ema_cross,
        {"fast": config.ema_fast, "slow": config.ema_slow},
        _grid_ma(), lambda p: p["slow"] + 1,
    ),
    "rsi": Strategy(
        "rsi", _rsi_reversion,
        {"period": config.rsi_period,
         "oversold": config.rsi_oversold,
         "overbought": config.rsi_overbought},
        _grid_rsi(), lambda p: p["period"] + 1,
    ),
    "breakout": Strategy(
        "breakout", _breakout,
        {"period": config.breakout_period},
        _grid_breakout(), lambda p: p["period"] + 1,
    ),
}


def get_strategy(name: str | None = None) -> Strategy:
    name = (name or config.strategy).lower()
    if name not in STRATEGIES:
        raise ValueError(
            f"unknown strategy '{name}'. Choose one of: {', '.join(STRATEGIES)}"
        )
    return STRATEGIES[name]


def evaluate(
    candles: list[Candle],
    name: str | None = None,
    params: dict | None = None,
) -> StrategyResult:
    """Dispatch to the active (or named) strategy."""
    strat = get_strategy(name)
    return strat.evaluate(candles, params or strat.default_params)