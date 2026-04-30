
#!/usr/bin/env python3
"""
Paper-trading logger — autonomous signal evaluation + position tracking.

Runs once per invocation (designed for hourly cron). Each run:
  1. Fetches latest 220 candles for each supported asset
  2. Updates OPEN paper trades: scans new candles for TP/SL/timeout
  3. Evaluates new signals on the latest closed bar
  4. Opens new paper trade if signal fires AND no open trade exists for that asset
  5. Sends Telegram notification on open/close (optional)

Usage:
    crontab:    0 * * * * cd /path/to/repo && .venv/bin/python -m scripts.paper_log
    manual:     python -m scripts.paper_log

Env vars:
    TELEGRAM_BOT_TOKEN   — required for notifications
    PAPER_NOTIFY_CHAT_ID — Telegram chat id to send to (optional; no notifications if unset)
    PAPER_TP_PCT         — default 3.0
    PAPER_SL_PCT         — default 1.5
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from src.backtest.engine import (
    _eval_bar,
    _fetch_candles_full,
    BINANCE_FEE_PCT,
    MAX_HOLD_HOURS,
    WARMUP_CANDLES,
)
from src.data.db import (
    init_db,
    open_paper_trade,
    get_open_paper_trades,
    has_open_paper_trade,
    close_paper_trade,
    mark_paper_notified,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

ASSETS = ["BTCUSDT", "SOLUSDT", "ETHUSDT"]
TP_PCT = float(os.getenv("PAPER_TP_PCT", "3.0"))
SL_PCT = float(os.getenv("PAPER_SL_PCT", "1.5"))

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("PAPER_NOTIFY_CHAT_ID", "").strip()


# ── Telegram (optional) ───────────────────────────────────────────────────────

def notify(text: str) -> bool:
    """Send Telegram message. Returns True if sent."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning("Telegram notify failed: %s", e)
        return False


# ── Position tracking ─────────────────────────────────────────────────────────

def _check_open_trade(trade: dict, candles: list) -> dict | None:
    """
    Scan candles for TP/SL/timeout against an open trade.
    Returns close payload (status, exit_price, exit_time, pnl_pct, ...) or None if still open.
    """
    entry_dt = datetime.fromisoformat(trade["entry_time"].replace("Z", "+00:00"))
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)

    tp_price = trade["tp_price"]
    sl_price = trade["sl_price"]
    entry_price = trade["entry_price"]

    relevant = [
        c for c in candles
        if datetime.fromtimestamp(c["open_time_ms"] / 1000, tz=timezone.utc) >= entry_dt
    ]

    for c in relevant:
        c_time = datetime.fromtimestamp(c["open_time_ms"] / 1000, tz=timezone.utc)
        hold_hours = int((c_time - entry_dt).total_seconds() / 3600)

        # Conservative: check SL before TP (worst-fill assumption)
        if c["low"] <= sl_price:
            pnl = (sl_price - entry_price) / entry_price * 100
            return {
                "status": "SL_HIT",
                "exit_price": sl_price,
                "exit_time": c_time.isoformat(),
                "pnl_pct": round(pnl, 4),
                "pnl_pct_net_fees": round(pnl - BINANCE_FEE_PCT * 2, 4),
                "hold_hours": hold_hours,
            }
        if c["high"] >= tp_price:
            pnl = (tp_price - entry_price) / entry_price * 100
            return {
                "status": "TP_HIT",
                "exit_price": tp_price,
                "exit_time": c_time.isoformat(),
                "pnl_pct": round(pnl, 4),
                "pnl_pct_net_fees": round(pnl - BINANCE_FEE_PCT * 2, 4),
                "hold_hours": hold_hours,
            }
        if hold_hours >= MAX_HOLD_HOURS:
            pnl = (c["close"] - entry_price) / entry_price * 100
            return {
                "status": "TIMEOUT",
                "exit_price": c["close"],
                "exit_time": c_time.isoformat(),
                "pnl_pct": round(pnl, 4),
                "pnl_pct_net_fees": round(pnl - BINANCE_FEE_PCT * 2, 4),
                "hold_hours": hold_hours,
            }

    return None


# ── Signal evaluation ─────────────────────────────────────────────────────────

def _check_for_signal(symbol: str, candles: list,
                       tp_pct: float, sl_pct: float,
                       candles_4h: list | None = None) -> dict | None:
    """
    Run _eval_bar on the latest fully-closed candle.
    Returns trade payload to open, or None.
    """
    if len(candles) < WARMUP_CANDLES + 2:
        logger.warning("%s: not enough candles (%d)", symbol, len(candles))
        return None

    # Use second-to-last candle as the signal bar (last is in-progress)
    signal_idx = len(candles) - 2
    window = candles[max(0, signal_idx - WARMUP_CANDLES):signal_idx + 1]
    ts_ms = candles[signal_idx]["open_time_ms"]

    from src.backtest.engine import _slice_4h_at
    slice_4h = _slice_4h_at(candles_4h, ts_ms) if candles_4h else None
    fired, snapshot = _eval_bar(window, ts_ms, tp_pct, sl_pct, 0.0, symbol,
                                candles_4h=slice_4h)
    if not fired:
        return None

    entry_price = candles[signal_idx + 1]["open"]
    entry_time = datetime.fromtimestamp(
        candles[signal_idx + 1]["open_time_ms"] / 1000, tz=timezone.utc
    ).isoformat()

    return {
        "symbol":         symbol,
        "entry_time":     entry_time,
        "entry_price":    entry_price,
        "tp_pct":         tp_pct,
        "sl_pct":         sl_pct,
        "tp_price":       round(entry_price * (1 + tp_pct / 100), 4),
        "sl_price":       round(entry_price * (1 - sl_pct / 100), 4),
        "total_score":    snapshot.get("total_score"),
        "layer_snapshot": json.dumps({
            k: v for k, v in snapshot.items()
            if k in ("total_score", "weekday", "hour_utc",
                     "l1", "l2", "l3", "l4", "l5", "l6", "l7", "l8", "l9", "l10")
        }, default=str),
    }


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_once(assets: list[str] | None = None,
             tp_pct: float | None = None,
             sl_pct: float | None = None) -> dict:
    """
    One iteration of paper-trading: update open trades, evaluate new signals,
    open trades on hits. Returns summary dict.

    Importable from the bot: just pass desired assets and TP/SL.
    Falls back to module-level defaults (env-driven) when args are None.
    """
    use_assets = assets if assets else ASSETS
    use_tp = tp_pct if tp_pct is not None else TP_PCT
    use_sl = sl_pct if sl_pct is not None else SL_PCT

    init_db()

    # Fetch candles once per asset
    candles_by_asset: dict[str, list] = {}
    candles_4h_by_asset: dict[str, list] = {}
    for symbol in use_assets:
        try:
            candles_by_asset[symbol] = _fetch_candles_full(symbol, days=14)
            candles_4h_by_asset[symbol] = _fetch_candles_full(symbol, days=60, interval="4h")
        except Exception as e:
            logger.error("Fetch failed for %s: %s", symbol, e)

    closed_count = 0
    opened_count = 0

    # 1. Update open trades — across ALL symbols (not just current config)
    for trade in get_open_paper_trades():
        symbol = trade["symbol"]
        candles = candles_by_asset.get(symbol)
        if not candles:
            try:
                candles = _fetch_candles_full(symbol, days=14)
                candles_by_asset[symbol] = candles
            except Exception as e:
                logger.error("Fetch for open-trade tracking failed (%s): %s", symbol, e)
                continue
        outcome = _check_open_trade(trade, candles)
        if outcome:
            close_paper_trade(
                trade["id"], outcome["status"], outcome["exit_price"],
                outcome["exit_time"], outcome["pnl_pct"],
                outcome["pnl_pct_net_fees"], outcome["hold_hours"],
            )
            closed_count += 1
            logger.info("CLOSED #%d %s %s: %.2f%% (%dh)",
                        trade["id"], symbol, outcome["status"],
                        outcome["pnl_pct_net_fees"], outcome["hold_hours"])
            emoji = "✅" if outcome["status"] == "TP_HIT" else "❌" if outcome["status"] == "SL_HIT" else "⏰"
            sent = notify(
                f"{emoji} <b>Paper trade #{trade['id']} closed</b>\n"
                f"Asset: <code>{symbol}</code>\n"
                f"Status: {outcome['status']}\n"
                f"P&L: <b>{outcome['pnl_pct_net_fees']:+.2f}%</b> (after 0.2% fees)\n"
                f"Hold: {outcome['hold_hours']}h\n"
                f"Entry: ${trade['entry_price']:.2f} → Exit: ${outcome['exit_price']:.2f}"
            )
            if sent:
                mark_paper_notified(trade["id"], "close")

    # 2. Look for new signals — only on the assets in this run's config
    for symbol in use_assets:
        candles = candles_by_asset.get(symbol)
        if not candles:
            continue
        if has_open_paper_trade(symbol):
            logger.info("%s: skipping (open trade exists)", symbol)
            continue
        signal = _check_for_signal(symbol, candles, use_tp, use_sl,
                                   candles_4h_by_asset.get(symbol))
        if not signal:
            continue
        trade_id = open_paper_trade(signal)
        opened_count += 1
        logger.info("OPENED #%d %s @ $%.2f (TP $%.2f, SL $%.2f)",
                    trade_id, symbol, signal["entry_price"],
                    signal["tp_price"], signal["sl_price"])
        sent = notify(
            f"📊 <b>Paper trade #{trade_id} opened</b>\n"
            f"Asset: <code>{symbol}</code>\n"
            f"Entry: ${signal['entry_price']:.2f}\n"
            f"TP: ${signal['tp_price']:.2f} (+{use_tp}%)\n"
            f"SL: ${signal['sl_price']:.2f} (-{use_sl}%)\n"
            f"Score: {signal['total_score']}/100"
        )
        if sent:
            mark_paper_notified(trade_id, "open")

    logger.info("Run complete: %d opened, %d closed", opened_count, closed_count)
    return {"opened": opened_count, "closed": closed_count}


def main() -> int:
    """CLI entry point — uses module-level config from env vars."""
    run_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())