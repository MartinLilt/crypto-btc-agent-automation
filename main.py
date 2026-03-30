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


def _full_keyboard(lang: str):
    """Language button + asset picker rows."""
    return InlineKeyboardMarkup([[_lang_btn(lang)]] + _asset_keyboard())


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    lang = _lang(context)

    await update.message.reply_text(
        t("hello", lang, name=name),
        parse_mode="Markdown",
        reply_markup=_full_keyboard(lang),
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


# ── Bot setup ─────────────────────────────────────────────────────────────────

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Start / choose asset"),
    ])


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(
        lang_toggle,  pattern="^lang_(en|ru)$"))
    app.add_handler(CallbackQueryHandler(choose_asset, pattern="^asset_"))
    app.add_handler(CallbackQueryHandler(analyse,      pattern="^analyse$"))
    app.add_handler(CallbackQueryHandler(pick_asset,   pattern="^pick_asset$"))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()

    """Return current language for this user, default 'en'."""
