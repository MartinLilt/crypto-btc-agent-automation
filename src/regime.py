"""Market regime (tendency) detector.

Computed BEFORE any strategy acts, so a strategy can adapt its behaviour to the
current market tendency. Three regimes:

  BULL    : market rising   — long-term MA sloping up   AND price above it
  BEAR    : market falling   — long-term MA sloping down AND price below it
  NEUTRAL : ambiguous        — flat / conflicting (sideways, chop)

Method: take a long SMA (REGIME_MA). Measure its slope over the last
REGIME_SLOPE_LOOKBACK candles as a percentage. A slope inside ±REGIME_FLAT_PCT
counts as flat (NEUTRAL); outside it, price position vs the MA confirms the
direction.

This is deliberately separate from the trading strategies — it is a filter/
context layer, not a signal generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .binance_api import Candle
from .config import config
from .strategy import sma


class Regime(str, Enum):
    BULL = "BULL"        # рынок растёт
    NEUTRAL = "NEUTRAL"  # рынок неоднозначный
    BEAR = "BEAR"        # рынок в упадке


_INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
}


def tf_factor(source: str, target: str) -> int:
    """How many `source` candles make up one `target` candle (>=1)."""
    if source not in _INTERVAL_SECONDS or target not in _INTERVAL_SECONDS:
        return 1
    return max(1, _INTERVAL_SECONDS[target] // _INTERVAL_SECONDS[source])


def resample(candles: list[Candle], factor: int) -> list[Candle]:
    """Aggregate candles into groups of `factor` bars (e.g. 24×1h -> 1d)."""
    if factor <= 1:
        return list(candles)
    out: list[Candle] = []
    for i in range(0, len(candles), factor):
        chunk = candles[i:i + factor]
        if not chunk:
            continue
        out.append(Candle(
            open_time=chunk[0].open_time,
            open=chunk[0].open,
            high=max(c.high for c in chunk),
            low=min(c.low for c in chunk),
            close=chunk[-1].close,
            volume=sum(c.volume for c in chunk),
            close_time=chunk[-1].close_time,
        ))
    return out


@dataclass(frozen=True)
class RegimeResult:
    regime: Regime
    ma: float | None          # long SMA now
    slope_pct: float | None   # % change of that SMA over the lookback
    price: float
    reason: str


def detect_regime(
    candles: list[Candle],
    ma_window: int | None = None,
    slope_lookback: int | None = None,
    flat_pct: float | None = None,
) -> RegimeResult:
    ma_window = ma_window or config.regime_ma
    slope_lookback = slope_lookback or config.regime_slope_lookback
    flat_pct = flat_pct if flat_pct is not None else config.regime_flat_pct

    closes = [c.close for c in candles]
    price = closes[-1]

    ma_now = sma(closes, ma_window)
    ma_past = sma(closes[: len(closes) - slope_lookback], ma_window)

    if ma_now is None or ma_past is None or ma_past == 0:
        return RegimeResult(
            Regime.NEUTRAL, ma_now, None, price,
            reason=f"not enough candles (need >{ma_window + slope_lookback})",
        )

    slope_pct = (ma_now / ma_past - 1) * 100
    above = price >= ma_now

    if slope_pct > flat_pct and above:
        regime = Regime.BULL
        reason = f"MA rising {slope_pct:+.1f}% & price above"
    elif slope_pct < -flat_pct and not above:
        regime = Regime.BEAR
        reason = f"MA falling {slope_pct:+.1f}% & price below"
    else:
        regime = Regime.NEUTRAL
        pos = "above" if above else "below"
        reason = f"flat/mixed (slope {slope_pct:+.1f}%, price {pos} MA)"

    return RegimeResult(regime, ma_now, slope_pct, price, reason)