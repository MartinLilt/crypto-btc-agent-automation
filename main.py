import asyncio
import os
import logging
from dotenv import load_dotenv

from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from src.strings import t

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ── Storage key in user_data ──────────────────────────────────────────────────
CFG = "agent_config"

# ── Supported assets ──────────────────────────────────────────────────────────
ASSETS = [
    ("₿ Bitcoin",  "BTCUSDT"),
    ("Ξ Ethereum", "ETHUSDT"),
    ("Ł Litecoin", "LTCUSDT"),
    ("◎ Solana",   "SOLUSDT"),
    ("⬡ Chainlink", "LINKUSDT"),
]

# Default trading params (no user input needed)
DEFAULT_BUDGET = 100.0
DEFAULT_TP_PCT = 2.0
DEFAULT_SL_PCT = 1.0


def _lang(context) -> str:
    lang = context.user_data.get(CFG, {}).get("language")
    if not lang:
        lang = context.user_data.get("_lang", "en")
    return lang


def _set_lang(context, lang: str):
    context.user_data["_lang"] = lang
    cfg = context.user_data.get(CFG)
    if cfg is not None:
        cfg["language"] = lang


def _lang_btn(lang: str):
    if lang == "en":
        return InlineKeyboardButton(t("btn_lang_to_ru", lang),
                                    callback_data="lang_ru")
    return InlineKeyboardButton(t("btn_lang_to_en", lang),
                                callback_data="lang_en")


def _asset_keyboard():
    """2-column keyboard with all 5 assets."""
    buttons = [
        InlineKeyboardButton(label, callback_data=f"asset_{sym}")
        for label, sym in ASSETS
    ]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    return rows  # list of rows, ready to embed in InlineKeyboardMarkup


def _main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Main menu: Live / Backtest / Patterns + language toggle."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_mode_live",     lang),
                              callback_data="menu_live")],
        [InlineKeyboardButton(t("btn_mode_backtest", lang),
                              callback_data="menu_backtest")],
        [InlineKeyboardButton(t("btn_mode_patterns", lang),
                              callback_data="menu_patterns")],
        [_lang_btn(lang)],
    ])


def _full_keyboard(lang: str):
    """Language button + asset picker rows (kept for backwards compat)."""
    return InlineKeyboardMarkup([[_lang_btn(lang)]] + _asset_keyboard())


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    lang = _lang(context)

    await update.message.reply_text(
        t("hello", lang, name=name),
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(lang),
    )


# ── Main menu callbacks ───────────────────────────────────────────────────────

async def menu_live(update: Update,
                    context: ContextTypes.DEFAULT_TYPE):
    """User picked 'Live analysis' → show asset picker."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)
    buttons = [
        InlineKeyboardButton(label, callback_data=f"asset_{sym}")
        for label, sym in ASSETS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(
        "⬅️ " + ("Back" if lang == "en" else "Назад"),
        callback_data="menu_back",
    )])
    await query.edit_message_text(
        t("pick_asset_live", lang),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def menu_backtest(update: Update,
                        context: ContextTypes.DEFAULT_TYPE):
    """User picked 'Backtest' → show asset picker (backtest flow)."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)
    buttons = [
        InlineKeyboardButton(label, callback_data=f"bt_asset_{sym}")
        for label, sym in ASSETS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(
        "⬅️ " + ("Back" if lang == "en" else "Назад"),
        callback_data="menu_back",
    )])
    await query.edit_message_text(
        t("bt_pick_asset", lang),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def menu_patterns(update: Update,
                        context: ContextTypes.DEFAULT_TYPE):
    """User picked 'Patterns' → show asset picker for patterns."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)
    buttons = [
        InlineKeyboardButton(label,
                             callback_data=f"bt_patterns_{sym}")
        for label, sym in ASSETS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(
        "⬅️ " + ("Back" if lang == "en" else "Назад"),
        callback_data="menu_back",
    )])
    await query.edit_message_text(
        t("btn_mode_patterns", lang) + " — " +
        ("choose token:" if lang == "en" else "выбери токен:"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def menu_back(update: Update,
                    context: ContextTypes.DEFAULT_TYPE):
    """Back button → return to main menu."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)
    name = update.effective_user.first_name
    await query.edit_message_text(
        t("hello", lang, name=name),
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(lang),
    )


# ── Asset chosen → run analysis immediately ───────────────────────────────────

async def choose_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = _lang(context)

    symbol = query.data[len("asset_"):]          # e.g. "BTCUSDT"
    label = next((l for l, s in ASSETS if s == symbol), symbol)

    # Save minimal config
    context.user_data[CFG] = {
        "asset":           symbol,
        "asset_label":     label,
        "budget":          DEFAULT_BUDGET,
        "take_profit_pct": DEFAULT_TP_PCT,
        "stop_loss_pct":   DEFAULT_SL_PCT,
        "language":        lang,
    }

    await query.edit_message_text(
        t("analysing", lang),
        parse_mode="Markdown",
    )
    await _run_analysis(query, context, lang)


# ── Refresh button ────────────────────────────────────────────────────────────

async def analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = _lang(context)

    cfg = context.user_data.get(CFG)
    if not cfg:
        # No config yet — show asset picker
        await query.edit_message_text(
            t("hello", lang, name=update.effective_user.first_name),
            parse_mode="Markdown",
            reply_markup=_full_keyboard(lang),
        )
        return

    await query.edit_message_text(t("analysing", lang))
    await _run_analysis(query, context, lang)


# ── Core analysis logic ───────────────────────────────────────────────────────

async def _run_analysis(query, context, lang: str):
    cfg = context.user_data[CFG]

    try:
        from src.binance_client import (
            get_candles,
            get_current_price,
            get_order_book_spread,
            get_order_book_depth,
            get_ticker_24h,
            get_funding_rate,
            get_fear_greed_index,
            get_taker_buy_pressure,
        )
        from src.indicators import check_entry_signal
        from src.news_client import get_recent_news, summarise_news

        symbol = cfg["asset"]
        candles = get_candles(symbol=symbol, interval="1h", limit=201)
        spread, _, _ = get_order_book_spread(symbol)
        bid_depth, ask_depth = get_order_book_depth(symbol)
        ticker = get_ticker_24h(symbol)
        volume_24h = ticker["volume_usd"]
        price = get_current_price(symbol)

        try:
            raw_news = get_recent_news(symbol)
            news_summary = summarise_news(raw_news)
        except Exception as news_err:
            logger.warning("News fetch failed: %s", news_err)
            news_summary = {}

        try:
            funding_data = get_funding_rate(symbol)
        except Exception as fund_err:
            logger.warning("Funding rate fetch failed: %s", fund_err)
            funding_data = {"ok": False}

        try:
            fg_data = get_fear_greed_index()
        except Exception as fg_err:
            logger.warning("Fear & Greed fetch failed: %s", fg_err)
            fg_data = {"ok": False}

        try:
            pressure_data = get_taker_buy_pressure(symbol)
        except Exception as pr_err:
            logger.warning("Buy pressure fetch failed: %s", pr_err)
            pressure_data = {"ok": False}

        should_enter, report = check_entry_signal(
            candles, spread, bid_depth, ask_depth, volume_24h,
            budget=cfg["budget"],
            take_profit_pct=cfg["take_profit_pct"],
            stop_loss_pct=cfg["stop_loss_pct"],
            news_summary=news_summary,
            funding_data=funding_data,
            fg_data=fg_data,
            pressure_data=pressure_data,
        )

        ai_result = None
        if os.getenv("OPENAI_API_KEY"):
            try:
                from src.ai_orchestrator import ai_review
                ai_result = ai_review(symbol, price, report)
                logger.info("AI verdict: %s", ai_result.get("verdict"))
            except Exception as ai_err:
                logger.warning("AI orchestration skipped: %s", ai_err)

        layers = report["layers"]
        l1 = layers["L1_volatility"]
        l2 = layers["L2_trend"]
        l3 = layers["L3_momentum"]
        l4 = layers["L4_timing"]
        l5 = layers["L5_liquidity"]
        l6 = layers["L6_risk_reward"]
        l7 = layers["L7_news"]
        l8 = layers["L8_funding"]
        l9 = layers["L9_fear_greed"]
        l10 = layers["L10_pressure"]

        def icon(key):
            return "✅" if layers[key]["pass"] else "❌"

        layer_short = {
            "L1_volatility":  t("layer_volatility_short",  lang),
            "L2_trend":       t("layer_trend_short",       lang),
            "L3_momentum":    t("layer_momentum_short",    lang),
            "L4_timing":      t("layer_timing_short",      lang),
            "L5_liquidity":   t("layer_liquidity_short",   lang),
            "L6_risk_reward": t("layer_risk_reward_short", lang),
            "L7_news":        t("layer_news_short",        lang),
            "L8_funding":     t("layer_funding_short",     lang),
            "L9_fear_greed":  t("layer_fear_greed_short",  lang),
            "L10_pressure":   t("layer_pressure_short",    lang),
        }

        # AI translation
        ai_points, ai_conclusion, ai_verdict, ai_conf = [], "", "WAIT", 0
        if ai_result:
            ai_verdict = ai_result["verdict"]
            ai_conf = ai_result["confidence"]
            ai_points = ai_result["points"]
            ai_conclusion = ai_result["conclusion"]
            if lang == "ru" and (ai_points or ai_conclusion):
                try:
                    from src.ai_orchestrator import translate_to_russian
                    ai_points, ai_conclusion = translate_to_russian(
                        ai_points, ai_conclusion
                    )
                except Exception as tr_err:
                    logger.warning("Translation skipped: %s", tr_err)

        # News summary text
        if l7.get("skipped") or l7.get("total", 0) == 0:
            news_str = "нет данных" if lang == "ru" else "no data"
        else:
            mood = ("📈" if l7["bullish"] > l7["bearish"] else
                    "📉" if l7["bearish"] > l7["bullish"] else "➡️")
            b, br, n = l7["bullish"], l7["bearish"], l7["neutral"]
            if lang == "ru":
                news_str = f"{mood} +{b}б -{br}м {n}н"
            else:
                news_str = f"{mood} +{b}b -{br}br {n}n"

        # Funding rate text
        if l8.get("skipped"):
            funding_str = "N/A (spot)"
        else:
            fr = l8.get("funding_rate", 0.0)
            oi = l8.get("oi_change_pct", 0.0)
            funding_str = f"FR {fr:+.3f}%  OI {oi:+.1f}%"

        # Fear & Greed text
        if l9.get("skipped"):
            fg_str = "N/A"
        else:
            fg_val = l9.get("value", 50)
            fg_cls = l9.get("classification", "")
            fg_chg = l9.get("change", 0)
            chg_sign = "+" if fg_chg >= 0 else ""
            fg_str = f"{fg_val}/100 {fg_cls} ({chg_sign}{fg_chg})"

        # Buy/Sell pressure text
        if l10.get("skipped"):
            pressure_str = "N/A"
        else:
            ratio = l10.get("buy_ratio_pct", 50.0)
            net = l10.get("net_btc", 0.0)
            trend_icon = (
                "📈" if l10.get("trend") == "bullish" else
                "📉" if l10.get("trend") == "bearish" else "➡️"
            )
            pressure_str = (
                f"{trend_icon} {ratio:.1f}% buy  net {net:+,.0f} BTC"
            )

        layer_data = [
            ("L1_volatility",  t("layer_volatility_short",  lang),
             f"ATR ${l1['atr']:,.0f}  ADX {l1['adx']:.0f}"),
            ("L2_trend",       t("layer_trend_short",       lang),
             f"EMA50 ${l2.get('ema50', 0):,.0f}  "
             f"EMA200 ${l2.get('ema200', 0):,.0f}"),
            ("L3_momentum",    t("layer_momentum_short",    lang),
             f"RSI {l3['rsi']:.1f}  MACD {l3['macd_hist']:+.1f}"),
            ("L4_timing",      t("layer_timing_short",      lang),
             f"{l4['weekday']} {l4['hour_utc']:02d}:00 UTC"),
            ("L5_liquidity",   t("layer_liquidity_short",   lang),
             ("спред" if lang == "ru" else "spread")
             + f" ${l5['spread']:.2f}"),
            ("L6_risk_reward", t("layer_risk_reward_short", lang),
             f"+${l6['net_profit']:.2f} / -${l6['net_loss']:.2f}"
             f"  RR {l6['rr_ratio']:.2f}"),
            ("L7_news",        t("layer_news_short",        lang),
             news_str),
            ("L8_funding",     t("layer_funding_short",     lang),
             funding_str),
            ("L9_fear_greed",  t("layer_fear_greed_short",  lang),
             fg_str),
            ("L10_pressure",   t("layer_pressure_short",    lang),
             pressure_str),
        ]

        ai_verdict_icon = "🟢" if ai_verdict == "ENTER" else "🔴"
        lines = [f"📊 *{symbol}*   💰 ${price:,.2f}", ""]

        for i, (key, name, data) in enumerate(layer_data):
            lines.append(f"{icon(key)} *{name}* — {data}")
            if i < len(ai_points):
                comment = ai_points[i].strip().lstrip("✅❌ ")
                lines.append(f"  _└ {comment}_")
            lines.append("")

        if should_enter:
            lines.append(t("signal_enter", lang))
        else:
            failed = [k for k, v in layers.items() if not v["pass"]]
            failed_str = ", ".join(layer_short.get(k, k) for k in failed)
            lines.append(t("signal_wait", lang))
            lines.append(t("signal_failed", lang, failed=failed_str))

        if ai_result:
            lines.append("")
            lines.append(t("ai_verdict_line", lang,
                           icon=ai_verdict_icon, verdict=ai_verdict,
                           conf=ai_conf))
            if ai_conclusion:
                lines.append(f"💬 _{ai_conclusion.strip()[:120]}_")

        message = "\n".join(lines)
        if len(message) > 4090:
            message = message[:4087] + "…"

        keyboard = InlineKeyboardMarkup([
            [_lang_btn(lang)],
            [InlineKeyboardButton(t("btn_refresh", lang),
                                  callback_data="analyse")],
            [InlineKeyboardButton(t("btn_change_asset", lang),
                                  callback_data="pick_asset")],
        ])
        await query.edit_message_text(
            message, parse_mode="Markdown", reply_markup=keyboard,
        )

    except Exception as e:
        logger.exception("Analysis failed")
        await query.edit_message_text(
            t("analysis_failed", lang, err=str(e)),
            parse_mode="Markdown",
        )


# ── Change asset (show picker again) ─────────────────────────────────────────

async def pick_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = _lang(context)
    name = update.effective_user.first_name

    await query.edit_message_text(
        t("hello", lang, name=name),
        parse_mode="Markdown",
        reply_markup=_full_keyboard(lang),
    )


# ── Language toggle ───────────────────────────────────────────────────────────

async def lang_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    new_lang = "ru" if query.data == "lang_ru" else "en"
    _set_lang(context, new_lang)
    name = update.effective_user.first_name

    await query.edit_message_text(
        t("hello", new_lang, name=name),
        parse_mode="Markdown",
        reply_markup=_full_keyboard(new_lang),
    )


# ── MarkdownV2 helpers ────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape a plain string for MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


# ── Market context helper ─────────────────────────────────────────────────────

def _build_market_context(symbol: str, result: dict, lang: str) -> str:
    """
    Build a short market-conditions block from the last bar of a backtest.
    Shows ADX (trend strength), 24h volume, and trend direction.
    Explains WHY signals may be few/none for low-activity assets like LTC.
    """
    trades = result.get("trades", [])
    signals = result.get("total_signals", 0)

    # Pull last-bar snapshot from backtest engine directly
    try:
        from src.backtest_engine import _fetch_candles_full, _eval_bar, _fetch_fear_greed_history
        candles = _fetch_candles_full(symbol, 7)
        WIN = 220
        if len(candles) > WIN:
            window = candles[-WIN - 1:]
            fg = _fetch_fear_greed_history()
            snap_sig, snap = _eval_bar(
                window, 0, fg, [], 2.0, 1.0, 0.001, symbol)
        else:
            return "_(not enough data)_" if lang == "en" else "_(недостаточно данных)_"
    except Exception:
        return "_(unavailable)_" if lang == "en" else "_(недоступно)_"

    l1 = snap.get("l1", {})
    l2 = snap.get("l2", {})
    l5 = snap.get("l5", {})

    adx = l1.get("adx", 0)
    price = l2.get("price", 0)
    ema50 = l2.get("ema50", 0)
    vol24 = l5.get("volume_24h_usd", 0)
    atr = l1.get("atr", 0)

    # Trend label
    if price > ema50 and l2.get("ema50_slope_ok"):
        trend_en, trend_ru = "📈 uptrend", "📈 восходящий"
    elif price < ema50 and not l2.get("ema50_slope_ok"):
        trend_en, trend_ru = "📉 downtrend", "📉 нисходящий"
    else:
        trend_en, trend_ru = "➡️ sideways", "➡️ боковик"

    # ADX label
    if adx >= 25:
        adx_label_en, adx_label_ru = "strong 💪", "сильный 💪"
    elif adx >= 15:
        adx_label_en, adx_label_ru = "weak ⚠️", "слабый ⚠️"
    else:
        adx_label_en, adx_label_ru = "very weak 😴", "очень слабый 😴"

    # Volume label
    if vol24 >= 500_000_000:
        vol_label_en, vol_label_ru = "high 🔥", "высокий 🔥"
    elif vol24 >= 50_000_000:
        vol_label_en, vol_label_ru = "normal ✅", "нормальный ✅"
    elif vol24 >= 10_000_000:
        vol_label_en, vol_label_ru = "low ⚠️", "низкий ⚠️"
    else:
        vol_label_en, vol_label_ru = "very low 😴", "очень низкий 😴"

    vol_m = vol24 / 1_000_000

    # Warning if market is quiet (explains low signal count)
    warn_en = warn_ru = ""
    if adx < 20 or vol24 < 30_000_000:
        warn_en = (
            "\n⚠️ _Market is currently quiet — "
            "fewer signals is expected behavior, not a bug\\._"
        )
        warn_ru = (
            "\n⚠️ _Рынок сейчас тихий — "
            "мало сигналов это норма, не баг\\._"
        )

    if lang == "ru":
        return (
            f"Тренд: {trend_ru}\n"
            f"ADX \\(сила тренда\\): *{adx:.1f}* — {adx_label_ru}\n"
            f"ATR \\(волатильность\\): *{atr:.3f}*\n"
            f"Объём 24h: *${vol_m:.1f}M* — {vol_label_ru}"
            f"{warn_ru}"
        )
    else:
        return (
            f"Trend: {trend_en}\n"
            f"ADX \\(trend strength\\): *{adx:.1f}* — {adx_label_en}\n"
            f"ATR \\(volatility\\): *{atr:.3f}*\n"
            f"Volume 24h: *${vol_m:.1f}M* — {vol_label_en}"
            f"{warn_en}"
        )


# ── Backtest — /backtest command ─────────────────────────────────────────────

# Periods: (label_en, label_ru, days, candles_approx)
BT_PERIODS = [
    ("7 days",   "7 дней",   7,   168),
    ("30 days",  "30 дней",  30,  720),
    ("3 months", "3 месяца", 90,  2160),
    ("6 months", "6 мес.",   180, 4320),
    ("1 year",   "1 год",    365, 8760),
]


async def backtest_cmd(update: Update,
                       context: ContextTypes.DEFAULT_TYPE):
    """Entry point: /backtest — show asset picker."""
    lang = _lang(context)
    buttons = [
        InlineKeyboardButton(label, callback_data=f"bt_asset_{sym}")
        for label, sym in ASSETS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    await update.message.reply_text(
        t("bt_pick_asset", lang),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def bt_asset_chosen(update: Update,
                          context: ContextTypes.DEFAULT_TYPE):
    """Asset selected — show period picker."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)

    symbol = query.data[len("bt_asset_"):]
    context.user_data["bt_symbol"] = symbol

    label_en = next((lb for lb, sym in ASSETS if sym == symbol), symbol)
    buttons = []
    for en, ru, days, _ in BT_PERIODS:
        period_label = ru if lang == "ru" else en
        buttons.append(
            InlineKeyboardButton(period_label,
                                 callback_data=f"bt_run_{days}")
        )
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    await query.edit_message_text(
        t("bt_pick_period", lang, symbol=symbol),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def bt_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Period selected — run backtest (blocking, shows progress first)."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)

    days = int(query.data[len("bt_run_"):])
    symbol = context.user_data.get(
        "bt_symbol",
        context.user_data.get(CFG, {}).get("asset", "BTCUSDT"),
    )
    cfg = context.user_data.get(CFG, {})
    tp_pct = cfg.get("take_profit_pct", DEFAULT_TP_PCT)
    sl_pct = cfg.get("stop_loss_pct",   DEFAULT_SL_PCT)

    candles_approx = next(
        (c for _, _, d, c in BT_PERIODS if d == days), days * 24)

    await query.edit_message_text(
        t("bt_running", lang,
          symbol=symbol, days=days, candles=candles_approx),
        parse_mode="Markdown",
    )

    try:
        from src.backtest_engine import run_backtest
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: run_backtest(symbol, days, tp_pct, sl_pct)
        )
    except Exception as err:
        logger.exception("Backtest failed")
        await query.edit_message_text(
            t("bt_failed", lang, err=str(err)[:120]),
            parse_mode="Markdown",
        )
        return

    signals = result.get("total_signals", 0)
    market_ctx = _build_market_context(symbol, result, lang)
    if signals == 0:
        await query.edit_message_text(
            t("bt_no_signals", lang, symbol=symbol, days=days,
              market_ctx=market_ctx),
            parse_mode="MarkdownV2",
        )
        return

    trades = result.get("trades", [])
    wins = [tr for tr in trades if tr["result"] == "TP_HIT"]
    losses = [tr for tr in trades if tr["result"] == "SL_HIT"]
    timeouts = [tr for tr in trades if tr["result"] == "TIMEOUT"]

    # Best / worst trade
    if trades:
        best = max(trades, key=lambda x: x["pnl_pct"])
        worst = min(trades, key=lambda x: x["pnl_pct"])
        best_time = best["exit_time"][:10] if best["exit_time"] else "—"
        worst_time = worst["exit_time"][:10] if worst["exit_time"] else "—"
    else:
        best = worst = {"pnl_pct": 0.0}
        best_time = worst_time = "—"

    # Date range from trades
    if trades:
        date_from = trades[0]["entry_time"][:10]
        date_to = trades[-1]["entry_time"][:10]
    else:
        date_from = date_to = "—"

    freq_str = (
        f"~1/{days // max(signals, 1)}d"
        if signals > 0 else "—"
    )

    msg = t(
        "bt_result", lang,
        symbol=_esc(symbol),
        days=days,
        date_from=_esc(date_from),
        date_to=_esc(date_to),
        signals=signals,
        freq=_esc(freq_str),
        wr=_esc(result["win_rate_pct"]),
        wins=len(wins),
        losses=len(losses),
        timeouts=len(timeouts),
        avg_profit=_esc(f"{result['avg_profit_pct']:+.2f}"),
        avg_loss=_esc(f"{result['avg_loss_pct']:+.2f}"),
        total_pnl=_esc(f"{result['total_pnl_pct']:+.1f}"),
        max_dd=_esc(result["max_drawdown_pct"]),
        sharpe=_esc(result["sharpe_ratio"]),
        best_pnl=_esc(f"{best['pnl_pct']:+.2f}"),
        best_time=_esc(best_time),
        worst_pnl=_esc(f"{worst['pnl_pct']:+.2f}"),
        worst_time=_esc(worst_time),
        market_ctx=market_ctx,
    )
    if len(msg) > 4090:
        msg = msg[:4087] + "…"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            t("btn_bt_patterns", lang),
            callback_data=f"bt_patterns_{symbol}",
        )],
        [InlineKeyboardButton(
            t("btn_bt_again", lang),
            callback_data=f"bt_asset_{symbol}",
        )],
        [InlineKeyboardButton(
            t("btn_change_asset", lang),
            callback_data="bt_start",
        )],
    ])
    await query.edit_message_text(
        msg, parse_mode="MarkdownV2", reply_markup=keyboard,
    )


async def bt_patterns(update: Update,
                      context: ContextTypes.DEFAULT_TYPE):
    """Show computed patterns for the last backtest symbol."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)

    symbol = query.data[len("bt_patterns_"):]

    try:
        from src.pattern_analyzer import (
            compute_patterns,
            format_patterns_message,
        )
        patterns = compute_patterns(symbol)
        msg = format_patterns_message(patterns, lang)
    except Exception as err:
        logger.exception("Patterns failed")
        msg = t("pat_no_data", lang, symbol=symbol)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            t("btn_back_to_bt", lang),
            callback_data=f"bt_asset_{symbol}",
        )
    ]])
    await query.edit_message_text(
        msg, parse_mode="Markdown", reply_markup=keyboard,
    )


async def patterns_cmd(update: Update,
                       context: ContextTypes.DEFAULT_TYPE):
    """/patterns — show patterns for last backtested symbol."""
    lang = _lang(context)
    symbol = context.user_data.get("bt_symbol",
                                   context.user_data.get(CFG, {})
                                   .get("asset", "BTCUSDT"))
    try:
        from src.pattern_analyzer import (
            compute_patterns,
            format_patterns_message,
        )
        patterns = compute_patterns(symbol)
        msg = format_patterns_message(patterns, lang)
    except Exception as err:
        logger.exception("Patterns failed")
        msg = t("pat_no_data", lang, symbol=symbol)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            t("btn_back_to_bt", lang),
            callback_data=f"bt_asset_{symbol}",
        )
    ]])
    await update.message.reply_text(
        msg, parse_mode="Markdown", reply_markup=keyboard,
    )


# ── Bot setup ─────────────────────────────────────────────────────────────
async def bt_start(update: Update,
                   context: ContextTypes.DEFAULT_TYPE):
    """Re-show asset picker (from 'Change asset' button inside backtest)."""
    query = update.callback_query
    await query.answer()
    lang = _lang(context)
    buttons = [
        InlineKeyboardButton(label, callback_data=f"bt_asset_{sym}")
        for label, sym in ASSETS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    await query.edit_message_text(
        t("bt_pick_asset", lang),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",    "Start / choose asset"),
        BotCommand("backtest", "Run historical backtest 📊"),
        BotCommand("patterns", "Best patterns from backtest 🔬"),
    ])


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # ── Core ──────────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CallbackQueryHandler(
        lang_toggle,  pattern="^lang_(en|ru)$"))
    app.add_handler(CallbackQueryHandler(menu_back,     pattern="^menu_back$"))
    app.add_handler(CallbackQueryHandler(menu_live,     pattern="^menu_live$"))
    app.add_handler(CallbackQueryHandler(
        menu_backtest, pattern="^menu_backtest$"))
    app.add_handler(CallbackQueryHandler(
        menu_patterns, pattern="^menu_patterns$"))
    app.add_handler(CallbackQueryHandler(choose_asset,  pattern="^asset_"))
    app.add_handler(CallbackQueryHandler(analyse,       pattern="^analyse$"))
    app.add_handler(CallbackQueryHandler(
        pick_asset,    pattern="^pick_asset$"))

    # ── Backtest ──────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("backtest", backtest_cmd))
    app.add_handler(CommandHandler("patterns", patterns_cmd))
    app.add_handler(CallbackQueryHandler(
        bt_start,        pattern="^bt_start$"))
    app.add_handler(CallbackQueryHandler(
        bt_asset_chosen, pattern="^bt_asset_"))
    app.add_handler(CallbackQueryHandler(bt_run,          pattern="^bt_run_"))
    app.add_handler(CallbackQueryHandler(
        bt_patterns,     pattern="^bt_patterns_"))

    logger.info("Bot started (backtest enabled)")
    app.run_polling()


if __name__ == "__main__":
    main()
