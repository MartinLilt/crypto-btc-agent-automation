"""Grid-stream core logic — pure decisions, no I/O.

Per coin, each cycle:
  • sell any bag whose price reached its micro take-profit (the stream drips)
  • add a new bag only when the coin is in an uptrend (price > SMA) AND price has
    dropped grid_step below the lowest open bag (buy the dip deeper)

No stop-loss: bags that go underwater are "frozen" and held for the bull-cycle
feast. The uptrend filter is what stops us piling bags into a falling market.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import config
from .strategy import sma


@dataclass(frozen=True)
class GridParams:
    tp_pct: float
    step_pct: float
    unit_usdt: float
    max_bags: int
    sma_win: int


def params_from_config() -> GridParams:
    return GridParams(
        tp_pct=config.grid_tp_pct,
        step_pct=config.grid_step_pct,
        unit_usdt=config.grid_unit_usdt,
        max_bags=config.grid_max_bags,
        sma_win=config.grid_sma,
    )


def is_uptrend(closes: list[float], sma_win: int) -> bool | None:
    """True if price is above its long SMA (rising tide); None if too few bars."""
    m = sma(closes, sma_win)
    if m is None:
        return None
    return closes[-1] > m


def bags_hitting_tp(bags: list[dict], price: float, tp_pct: float) -> list[int]:
    """Indices of bags whose take-profit has been reached at `price`."""
    return [i for i, b in enumerate(bags)
            if price >= b["entry"] * (1 + tp_pct / 100)]


def should_add_bag(
    bags: list[dict],
    price: float,
    cash: float,
    uptrend: bool | None,
    params: GridParams,
) -> bool:
    """Whether to open a new bag this cycle."""
    if not uptrend:
        return False                       # never buy into a downtrend
    if cash < params.unit_usdt:
        return False                       # out of dry powder
    if len(bags) >= params.max_bags:
        return False                       # capital cap for this coin reached
    if not bags:
        return True                        # first bag while in uptrend
    lowest = min(b["entry"] for b in bags)
    return price <= lowest * (1 - params.step_pct / 100)  # dip deeper than last