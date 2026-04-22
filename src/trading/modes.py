"""Trading configuration and constants."""

import os
from enum import Enum


class TradingMode(Enum):
    SIMULATION = "simulation"
    LIVE = "live"


# ── Trade parameters ──────────────────────────────────────────────────────────

SYMBOL = "BTCUSDT"
BUDGET = float(os.getenv("TRADE_BUDGET", "100"))
TP_PCT = 2.0        # take profit %
SL_PCT = 1.0        # initial stop loss %

ENTRY_SCORE_MIN = 70   # minimum total score to open a position

# ── Loop intervals ────────────────────────────────────────────────────────────

SCAN_INTERVAL_SEC = 15 * 60   # scanner runs every 15 min
WATCH_INTERVAL_SEC = 30       # watcher checks price every 30 sec

# ── Trailing stop logic ───────────────────────────────────────────────────────

BREAKEVEN_TRIGGER_PCT = 1.0   # move SL to entry when profit >= 1%
TRAILING_TRIGGER_PCT = 1.5    # start trailing after +1.5%
TRAILING_SL_OFFSET_PCT = 1.0  # trailing SL = current_price - 1%

# ── Notification target ───────────────────────────────────────────────────────

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))