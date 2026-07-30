"""Exit management for short-term spot trades.

Independent of the entry signal: once in a position, these rules decide when to
GET OUT — the other half of "enter and exit short spots". Checked every bar
before the strategy is consulted, so a trade is closed the moment a rule fires.

  take-profit  : lock in a gain at +X%
  stop-loss    : cap a loss at -X%
  trailing-stop: exit X% below the highest price seen since entry (rides winners)
  timeout      : force-exit after N bars (don't sit in a stale trade)

All thresholds are PERCENTAGES (3 = 3%); 0 disables that rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import config


@dataclass(frozen=True)
class ExitRules:
    take_profit_pct: float = 0.0
    stop_loss_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    max_hold_bars: int = 0

    @property
    def any_active(self) -> bool:
        return bool(self.take_profit_pct or self.stop_loss_pct
                    or self.trailing_stop_pct or self.max_hold_bars)


def rules_from_config() -> ExitRules:
    return ExitRules(
        take_profit_pct=config.take_profit_pct,
        stop_loss_pct=config.stop_loss_pct,
        trailing_stop_pct=config.trailing_stop_pct,
        max_hold_bars=config.max_hold_bars,
    )


def check_exit(
    rules: ExitRules,
    entry_price: float,
    peak_price: float,
    bars_held: int,
    price: float,
) -> str | None:
    """Return an exit reason if a rule fires, else None."""
    if rules.take_profit_pct and price >= entry_price * (1 + rules.take_profit_pct / 100):
        return "take-profit"
    if rules.stop_loss_pct and price <= entry_price * (1 - rules.stop_loss_pct / 100):
        return "stop-loss"
    if rules.trailing_stop_pct and price <= peak_price * (1 - rules.trailing_stop_pct / 100):
        return "trailing-stop"
    if rules.max_hold_bars and bars_held >= rules.max_hold_bars:
        return "timeout"
    return None