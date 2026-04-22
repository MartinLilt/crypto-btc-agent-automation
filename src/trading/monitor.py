"""
Async monitor loops — run as background tasks in the Telegram bot process.

scanner_loop  — every 15 min: evaluate signal, open position if score >= threshold
watcher_loop  — every 30 sec: check TP/SL/trailing stop for open position
"""

import asyncio
import logging

from src.trading.modes import (
    ADMIN_CHAT_ID,
    BUDGET,
    ENTRY_SCORE_MIN,
    SCAN_INTERVAL_SEC,
    SYMBOL,
    WATCH_INTERVAL_SEC,
    TradingMode,
)

logger = logging.getLogger(__name__)


async def _notify(app, text: str):
    """Send message to ADMIN_CHAT_ID (best-effort — never raises)."""
    if not ADMIN_CHAT_ID:
        return
    try:
        await app.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("Telegram notify failed: %s", e)


async def scanner_loop(app):
    """
    Runs every SCAN_INTERVAL_SEC.
    Skips if a position is already open.
    Opens position when total_score >= ENTRY_SCORE_MIN.
    """
    logger.info("Scanner loop started (interval=%ds)", SCAN_INTERVAL_SEC)
    await asyncio.sleep(10)   # give bot a moment to start up

    while True:
        try:
            mode_enum = app.bot_data.get("trading_mode", TradingMode.SIMULATION)
            mode_str = mode_enum.value

            from src.trading.position import get_position, new_position
            if get_position() is not None:
                logger.debug("Scanner: position already open — skip")
                await asyncio.sleep(SCAN_INTERVAL_SEC)
                continue

            # Fetch market data
            from src.data.binance_client import (
                get_candles,
                get_order_book_spread,
                get_order_book_depth,
                get_ticker_24h,
                get_taker_buy_pressure,
                get_funding_rate,
            )
            from src.signals.indicators import check_entry_signal
            from src.data.news_client import get_recent_news, summarise_news

            candles    = get_candles(symbol=SYMBOL, interval="1h", limit=250)
            candles_4h = get_candles(symbol=SYMBOL, interval="4h", limit=210)
            candles_1d = get_candles(symbol=SYMBOL, interval="1d", limit=100)
            candles_1w = get_candles(symbol=SYMBOL, interval="1w", limit=30)
            spread, _, _ = get_order_book_spread(SYMBOL)
            bid_depth, ask_depth = get_order_book_depth(SYMBOL)
            ticker = get_ticker_24h(SYMBOL)
            volume_24h = ticker["volume_usd"]

            try:
                news_summary = summarise_news(get_recent_news(SYMBOL))
            except Exception:
                news_summary = {}
            try:
                pressure_data = get_taker_buy_pressure(SYMBOL, hours=6)
            except Exception:
                pressure_data = {"ok": False}
            try:
                funding_data = get_funding_rate(SYMBOL)
            except Exception:
                funding_data = {"ok": False}

            should_enter, report = check_entry_signal(
                candles, spread, bid_depth, ask_depth, volume_24h,
                budget=BUDGET, take_profit_pct=2.0, stop_loss_pct=1.0,
                news_summary=news_summary,
                pressure_data=pressure_data,
                funding_data=funding_data,
                candles_4h=candles_4h,
                candles_1d=candles_1d,
                candles_1w=candles_1w,
            )

            total_score = report.get("total_score", 0)
            logger.info("Scanner: score=%d  should_enter=%s  mode=%s",
                        total_score, should_enter, mode_str)

            if should_enter and total_score >= ENTRY_SCORE_MIN:
                from src.trading.executor import execute_buy
                fill_price, qty = execute_buy(SYMBOL, BUDGET, mode_str)
                pos = new_position(
                    symbol=SYMBOL,
                    mode=mode_str,
                    entry_price=fill_price,
                    qty=qty,
                    budget=BUDGET,
                    total_score=total_score,
                )
                sl = pos["sl_price"]
                tp = pos["tp_price"]
                mode_label = "🧪 SIM" if mode_str == "simulation" else "🔴 LIVE"
                await _notify(app, (
                    f"🚀 *BUY signal — {mode_label}*\n\n"
                    f"Symbol:  `{SYMBOL}`\n"
                    f"Price:   `${fill_price:,.2f}`\n"
                    f"Qty:     `{qty:.5f} BTC`\n"
                    f"Budget:  `${BUDGET:.2f}`\n"
                    f"SL:      `${sl:,.2f}`  \\(-1%\\)\n"
                    f"TP:      `${tp:,.2f}`  \\(+2%\\)\n"
                    f"Score:   `{total_score}/100`"
                ))

        except Exception as e:
            logger.exception("Scanner loop error: %s", e)

        await asyncio.sleep(SCAN_INTERVAL_SEC)


async def watcher_loop(app):
    """
    Runs every WATCH_INTERVAL_SEC.
    Monitors open position for TP/SL hits and updates trailing stop.
    """
    logger.info("Watcher loop started (interval=%ds)", WATCH_INTERVAL_SEC)
    await asyncio.sleep(15)   # slightly stagger from scanner startup

    while True:
        try:
            from src.trading.position import get_position, check_and_update, close_position
            position = get_position()
            if position is None:
                await asyncio.sleep(WATCH_INTERVAL_SEC)
                continue

            from src.data.binance_client import get_current_price, get_candles
            current_price = get_current_price(SYMBOL)

            # Smart exits: RSI overbought or MACD bearish cross while in profit
            entry_price = position["entry_price"]
            pnl_now = (current_price - entry_price) / entry_price * 100
            smart_exit_reason = None
            if pnl_now > 0:
                try:
                    from src.signals.indicators import calculate_rsi, calculate_macd
                    candles_watch = get_candles(symbol=SYMBOL, interval="1h", limit=50)
                    rsi_val = calculate_rsi(candles_watch)
                    _, _, macd_hist = calculate_macd(candles_watch)
                    _, _, prev_hist = calculate_macd(candles_watch[:-1])
                    if rsi_val is not None and rsi_val > 75:
                        smart_exit_reason = "SMART_EXIT_RSI"
                    elif (macd_hist is not None and prev_hist is not None
                          and macd_hist < 0 < prev_hist):
                        smart_exit_reason = "SMART_EXIT_MACD"
                except Exception as e:
                    logger.debug("Smart exit check failed: %s", e)

            if smart_exit_reason:
                from src.trading.executor import execute_sell
                actual_price = execute_sell(SYMBOL, position["qty"], position.get("mode", "simulation"))
                actual_pnl = (actual_price - entry_price) / entry_price * 100
                close_position(position["id"], actual_price, smart_exit_reason, actual_pnl)
                mode_str = position.get("mode", "simulation")
                mode_label = "🧪 SIM" if mode_str == "simulation" else "🔴 LIVE"
                pnl_icon = "📈" if actual_pnl >= 0 else "📉"
                await _notify(app, (
                    f"🎯 *{smart_exit_reason.replace('_', ' ')} — {mode_label}*\n\n"
                    f"Symbol:   `{SYMBOL}`\n"
                    f"Entry:    `${entry_price:,.2f}`\n"
                    f"Exit:     `${actual_price:,.2f}`\n"
                    f"P&L:      {pnl_icon} `{actual_pnl:+.2f}%`\n"
                    f"Budget:   `${position['budget']:.2f}`"
                ))
                await asyncio.sleep(WATCH_INTERVAL_SEC)
                continue

            result = check_and_update(position, current_price)
            action = result["action"]
            mode_str = position.get("mode", "simulation")
            mode_label = "🧪 SIM" if mode_str == "simulation" else "🔴 LIVE"

            if action == "close":
                reason = result["reason"]
                exit_price = result["exit_price"]
                pnl_pct = result["pnl_pct"]

                from src.trading.executor import execute_sell
                actual_price = execute_sell(SYMBOL, position["qty"], mode_str)
                actual_pnl = (actual_price - position["entry_price"]) / position["entry_price"] * 100

                close_position(position["id"], actual_price, reason, actual_pnl)

                icon = "✅" if reason == "TP_HIT" else "🛑"
                pnl_icon = "📈" if actual_pnl >= 0 else "📉"
                await _notify(app, (
                    f"{icon} *{reason.replace('_', ' ')} — {mode_label}*\n\n"
                    f"Symbol:   `{SYMBOL}`\n"
                    f"Entry:    `${position['entry_price']:,.2f}`\n"
                    f"Exit:     `${actual_price:,.2f}`\n"
                    f"P&L:      {pnl_icon} `{actual_pnl:+.2f}%`\n"
                    f"Budget:   `${position['budget']:.2f}`"
                ))

            elif action == "update_sl":
                new_sl = result["new_sl"]
                pnl_pct = result["pnl_pct"]
                label = "Break-even" if not result.get("breakeven_hit") else "Trailing"
                await _notify(app, (
                    f"🔒 *{label} stop — {mode_label}*\n\n"
                    f"Symbol:   `{SYMBOL}`\n"
                    f"New SL:   `${new_sl:,.2f}`\n"
                    f"P&L now:  `{pnl_pct:+.2f}%`"
                ))

        except Exception as e:
            logger.exception("Watcher loop error: %s", e)

        await asyncio.sleep(WATCH_INTERVAL_SEC)