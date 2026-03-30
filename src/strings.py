"""
Localisation strings — EN / RU
Usage:
    from src.strings import t
    t("welcome_back", lang, name=name, asset=asset, ...)
"""

STRINGS = {
    # ── /start ───────────────────────────────────────────────────────────────
    "hello": {
        "en": (
            "👋 Hello, {name}!\n\n"
            "🤖 I analyse the market across *10 decision layers*.\n"
            "Choose a token to analyse:"
        ),
        "ru": (
            "👋 Привет, {name}!\n\n"
            "🤖 Я анализирую рынок по *10 уровням принятия решений*.\n"
            "Выбери токен для анализа:"
        ),
    },
    "btn_change_asset": {
        "en": "🔄 Change token",
        "ru": "🔄 Сменить токен",
    },
    "btn_start_analyse": {
        "en": "🔍 Analyse the market",
        "ru": "🔍 Анализировать рынок",
    },

    # ── /start — returning user ───────────────────────────────────────────────
    "welcome_back": {
        "en": (
            "👋 Welcome back, {name}!\n\n"
            "⚙️ *Current settings:*\n"
            "  Asset:       *{asset}*\n"
            "  Budget:      *${budget}*\n"
            "  Take Profit: *{tp}%*\n"
            "  Stop Loss:   *{sl}%*\n"
            "  Language:    *🇬🇧 English*\n\n"
            "What would you like to do?"
        ),
        "ru": (
            "👋 С возвращением, {name}!\n\n"
            "⚙️ *Текущие настройки:*\n"
            "  Актив:        *{asset}*\n"
            "  Бюджет:       *${budget}*\n"
            "  Тейк-профит:  *{tp}%*\n"
            "  Стоп-лосс:    *{sl}%*\n"
            "  Язык:         *🇷🇺 Русский*\n\n"
            "Что хочешь сделать?"
        ),
    },
    "btn_analyse": {
        "en": "📊 Analyse market now",
        "ru": "📊 Анализировать рынок",
    },
    "btn_settings": {
        "en": "⚙️ Change settings",
        "ru": "⚙️ Изменить настройки",
    },
    "btn_lang_to_ru": {
        "en": "🌐 Switch to RU 🇷🇺",
        "ru": "🌐 Switch to RU 🇷🇺",
    },
    "btn_lang_to_en": {
        "en": "🌐 Switch to EN 🇬🇧",
        "ru": "🌐 Switch to EN 🇬🇧",
    },

    # ── Language toggled ──────────────────────────────────────────────────────
    "lang_set": {
        "en": "✅ Language set to *🇬🇧 English*.\n\nUse /start to return to the main menu.",
        "ru": "✅ Язык изменён на *🇷🇺 Русский*.\n\nНажми /start чтобы вернуться в меню.",
    },

    # ── Setup flow ────────────────────────────────────────────────────────────
    "setup_step1": {
        "en": "📌 *Step 1 of 4 — Choose asset*\n\nWhich market do you want the agent to trade?",
        "ru": "📌 *Шаг 1 из 4 — Выбор актива*\n\nНа каком рынке должен торговать агент?",
    },
    "setup_asset_chosen": {
        "en": (
            "✅ Asset selected: *{label}*\n\n"
            "💰 *Step 2 of 4 — Budget*\n\n"
            "How much USDT do you want to trade per position?\n"
            "_(e.g. `100` for $100)_"
        ),
        "ru": (
            "✅ Актив выбран: *{label}*\n\n"
            "💰 *Шаг 2 из 4 — Бюджет*\n\n"
            "Сколько USDT выделить на одну позицию?\n"
            "_(например `100` для $100)_"
        ),
    },
    "setup_budget_bad": {
        "en": "⚠️ Please enter a valid amount (minimum $10).\nExample: `100`",
        "ru": "⚠️ Введи корректную сумму (минимум $10).\nПример: `100`",
    },
    "setup_step3": {
        "en": (
            "✅ Budget: *${budget}*\n\n"
            "🎯 *Step 3 of 4 — Take Profit*\n\n"
            "At what gain % should the agent sell?\n"
            "_(e.g. `2` for +2%)_"
        ),
        "ru": (
            "✅ Бюджет: *${budget}*\n\n"
            "🎯 *Шаг 3 из 4 — Тейк-профит*\n\n"
            "При каком росте % агент должен продать?\n"
            "_(например `2` для +2%)_"
        ),
    },
    "setup_tp_bad": {
        "en": "⚠️ Please enter a number between 0.1 and 100.\nExample: `2`",
        "ru": "⚠️ Введи число от 0.1 до 100.\nПример: `2`",
    },
    "setup_step4": {
        "en": (
            "✅ Take Profit: *{tp}%*\n\n"
            "🛡 *Step 4 of 4 — Stop Loss*\n\n"
            "At what loss % should the agent exit to protect capital?\n"
            "_(e.g. `1.5` for -1.5%)_\n\n"
            "_Tip: Stop loss is tracked silently in memory — "
            "no orders are placed on the exchange._"
        ),
        "ru": (
            "✅ Тейк-профит: *{tp}%*\n\n"
            "🛡 *Шаг 4 из 4 — Стоп-лосс*\n\n"
            "При каком убытке % агент должен выйти из позиции?\n"
            "_(например `1.5` для -1.5%)_\n\n"
            "_Подсказка: стоп-лосс отслеживается в памяти — "
            "никаких ордеров на бирже не размещается._"
        ),
    },
    "setup_sl_bad": {
        "en": "⚠️ Please enter a number between 0.1 and 100.\nExample: `1.5`",
        "ru": "⚠️ Введи число от 0.1 до 100.\nПример: `1.5`",
    },
    "setup_confirm": {
        "en": (
            "📋 *Summary — please confirm:*\n\n"
            "  Asset:       *{label}*\n"
            "  Pair:        *{asset}*\n"
            "  Budget:      *${budget} USDT*\n"
            "  Take Profit: *+{tp}%*\n"
            "  Stop Loss:   *-{sl}%*"
        ),
        "ru": (
            "📋 *Сводка — подтверди настройки:*\n\n"
            "  Актив:        *{label}*\n"
            "  Пара:         *{asset}*\n"
            "  Бюджет:       *${budget} USDT*\n"
            "  Тейк-профит:  *+{tp}%*\n"
            "  Стоп-лосс:    *-{sl}%*"
        ),
    },
    "btn_confirm": {
        "en": "✅ Confirm",
        "ru": "✅ Подтвердить",
    },
    "btn_start_over": {
        "en": "🔄 Start over",
        "ru": "🔄 Начать заново",
    },
    "setup_saved": {
        "en": (
            "🎉 *Settings saved!*\n\n"
            "The agent is configured for *{label}*.\n"
            "Press the button below to run a full market analysis."
        ),
        "ru": (
            "🎉 *Настройки сохранены!*\n\n"
            "Агент настроен для торговли *{label}*.\n"
            "Нажми кнопку ниже чтобы запустить полный анализ рынка."
        ),
    },
    "btn_lang_switch": {
        "en": "🌐 Switch language EN / RU",
        "ru": "🌐 Сменить язык EN / RU",
    },
    "cancel": {
        "en": "❌ Setup cancelled. Use /start to begin again.",
        "ru": "❌ Настройка отменена. Нажми /start чтобы начать заново.",
    },

    # ── Analysis ──────────────────────────────────────────────────────────────
    "no_config": {
        "en": "⚠️ No settings found. Use /start to configure the agent first.",
        "ru": "⚠️ Настройки не найдены. Используй /start для настройки агента.",
    },
    "analysing": {
        "en": "⏳ Analysing market... please wait.",
        "ru": "⏳ Анализирую рынок... подожди немного.",
    },
    "layer_volatility": {
        "en": "Volatility",
        "ru": "Волатильн.",
    },
    "layer_trend": {
        "en": "Trend     ",
        "ru": "Тренд     ",
    },
    "layer_momentum": {
        "en": "Momentum  ",
        "ru": "Импульс   ",
    },
    "layer_timing": {
        "en": "Timing    ",
        "ru": "Тайминг   ",
    },
    "layer_liquidity": {
        "en": "Liquidity ",
        "ru": "Ликвидн.  ",
    },
    "layer_risk_reward": {
        "en": "Risk/Rew. ",
        "ru": "Риск/Доход",
    },
    "layer_news": {
        "en": "News      ",
        "ru": "Новости   ",
    },
    "layer_funding": {
        "en": "Funding   ",
        "ru": "Фандинг   ",
    },
    "layer_fear_greed": {
        "en": "Fear/Greed",
        "ru": "Страх/Жад.",
    },
    "layer_pressure": {
        "en": "Buy/Sell  ",
        "ru": "Давление  ",
    },
    # Short names used in "failed layers" list
    "layer_volatility_short":  {"en": "Volatility",   "ru": "Волатильность"},
    "layer_trend_short":       {"en": "Trend",         "ru": "Тренд"},
    "layer_momentum_short":    {"en": "Momentum",      "ru": "Импульс"},
    "layer_timing_short":      {"en": "Timing",        "ru": "Тайминг"},
    "layer_liquidity_short":   {"en": "Liquidity",     "ru": "Ликвидность"},
    "layer_risk_reward_short": {"en": "Risk/Reward",   "ru": "Риск/Доход"},
    "layer_news_short":        {"en": "News",          "ru": "Новости"},
    "layer_funding_short":     {"en": "Funding Rate",  "ru": "Фандинг"},
    "layer_fear_greed_short":  {"en": "Fear & Greed",  "ru": "Страх/Жадность"},
    "layer_pressure_short":    {"en": "Buy Pressure",  "ru": "Давление"},
    "signal_enter": {
        "en": "🚀 *SIGNAL: ENTER — all layers passed!*",
        "ru": "🚀 *СИГНАЛ: ВХОД — все уровни пройдены!*",
    },
    "signal_wait": {
        "en": "🚫 *SIGNAL: WAIT*",
        "ru": "🚫 *СИГНАЛ: ЖДАТЬ*",
    },
    "signal_failed": {
        "en": "   Failed: _{failed}_",
        "ru": "   Не прошли: _{failed}_",
    },
    "ai_verdict_line": {
        "en": "🤖 *AI Analysis — {icon} {verdict}* ({conf}% confidence)",
        "ru": "🤖 *AI Анализ — {icon} {verdict}* (уверенность {conf}%)",
    },
    "btn_refresh": {
        "en": "🔄 Refresh analysis",
        "ru": "🔄 Обновить анализ",
    },
    "analysis_failed": {
        "en": "❌ Analysis failed: `{err}`\n\nTry again later.",
        "ru": "❌ Ошибка анализа: `{err}`\n\nПопробуй позже.",
    },
}


def t(key: str, lang: str, **kwargs) -> str:
    """
    Return localised string for key in given language.
    Falls back to English if key/lang not found.
    Formats with kwargs if provided.
    """
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en", f"[{key}]")
    if kwargs:
        text = text.format(**kwargs)
    return text
