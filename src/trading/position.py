"""
Position tracker — SQLite-backed, one open position at a time.

Position lifecycle:
  open  →  watcher monitors price every 30 sec
         ├─ price >= tp_price          → TP_HIT, close
         ├─ price <= sl_price          → SL_HIT (trailing stop), close
         ├─ profit >= BREAKEVEN_TRIGGER → move SL to entry (stealth)
         └─ profit >= TRAILING_TRIGGER → trail SL = price - SL_PCT
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.data.db import close_pos, get_open_pos, open_pos, update_pos_sl
from src.trading.modes import (
    BREAKEVEN_TRIGGER_PCT,
    SL_PCT,
    TRAILING_SL_OFFSET_PCT,
    TRAILING_TRIGGER_PCT,
    TP_PCT,
)

logger = logging.getLogger(__name__)


def new_position(
    symbol: str,
    mode: str,
    entry_price: float,
    qty: float,
    budget: float,
    total_score: int,
) -> dict:
    """Open a new position in SQLite. Returns saved position dict."""
    sl_price = round(entry_price * (1 - SL_PCT / 100), 2)
    tp_price = round(entry_price * (1 + TP_PCT / 100), 2)
    entry_time = datetime.now(timezone.utc).isoformat()

    data = {
        "symbol":       symbol,
        "mode":         mode,
        "entry_time":   entry_time,
        "entry_price":  entry_price,
        "qty":          qty,
        "budget":       budget,
        "sl_price":     sl_price,
        "tp_price":     tp_price,
        "total_score":  total_score,
    }
    pos_id = open_pos(data)
    data["id"] = pos_id
    logger.info(
        "Position opened: %s @ %.2f  SL=%.2f  TP=%.2f  (score=%d)",
        symbol, entry_price, sl_price, tp_price, total_score,
    )
    return data


def check_and_update(position: dict, current_price: float) -> dict:
    """
    Evaluate price against open position.
    Returns action dict:
      {"action": "none" | "close" | "update_sl", ...}
    Caller is responsible for executing close/update in DB and notifying.
    """
    pos_id = position["id"]
    entry = position["entry_price"]
    sl = position["sl_price"]
    tp = position["tp_price"]
    breakeven = bool(position.get("breakeven_hit", 0))

    pnl_pct = (current_price - entry) / entry * 100

    # ── TP hit ────────────────────────────────────────────────────────────────
    if current_price >= tp:
        return {
            "action":     "close",
            "reason":     "TP_HIT",
            "exit_price": tp,
            "pnl_pct":    round((tp - entry) / entry * 100, 4),
        }

    # ── SL hit ────────────────────────────────────────────────────────────────
    if current_price <= sl:
        return {
            "action":     "close",
            "reason":     "SL_HIT",
            "exit_price": sl,
            "pnl_pct":    round((sl - entry) / entry * 100, 4),
        }

    # ── Trailing stop updates (stealth — no orders on exchange) ───────────────
    new_sl = sl
    new_breakeven = breakeven

    if pnl_pct >= TRAILING_TRIGGER_PCT:
        # Trail: keep SL = current_price - SL_PCT  (but never below entry)
        trailing_sl = round(current_price * (1 - TRAILING_SL_OFFSET_PCT / 100), 2)
        new_sl = max(trailing_sl, entry)
        new_breakeven = True
    elif pnl_pct >= BREAKEVEN_TRIGGER_PCT and not breakeven:
        new_sl = entry
        new_breakeven = True

    if new_sl != sl:
        update_pos_sl(pos_id, new_sl, new_breakeven)
        logger.info(
            "Trailing stop updated: pos_id=%d  SL %.2f → %.2f  pnl=%.2f%%",
            pos_id, sl, new_sl, pnl_pct,
        )
        return {
            "action":       "update_sl",
            "new_sl":       new_sl,
            "breakeven_hit": new_breakeven,
            "pnl_pct":      round(pnl_pct, 4),
        }

    return {"action": "none", "pnl_pct": round(pnl_pct, 4)}


def close_position(pos_id: int, exit_price: float, reason: str, pnl_pct: float):
    """Persist position close to SQLite."""
    exit_time = datetime.now(timezone.utc).isoformat()
    close_pos(pos_id, exit_price, exit_time, reason, round(pnl_pct, 4))
    logger.info(
        "Position closed: pos_id=%d  reason=%s  exit=%.2f  pnl=%.4f%%",
        pos_id, reason, exit_price, pnl_pct,
    )


def get_position() -> Optional[dict]:
    """Return currently open position or None."""
    return get_open_pos()