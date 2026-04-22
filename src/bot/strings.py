"""
Localisation strings — EN / RU
Usage:
    from src.bot.strings import t
    t("welcome_back", lang, name=name, asset=asset, ...)
"""

STRINGS = {
    # ── /start ───────────────────────────────────────────────────────────────
    "hello": {
        "en": (
            "👋 Hello, {name}!\n\n"
            "🤖 I analyse the market across *10 decision layers*.\n\n"
            "Choose what you want to do:"
        ),
        "ru": (
            "👋 Привет, {name}!\n\n"
            "🤖 Я анализирую рынок по *10 уровням принятия решений*.\n\n"
            "Выбери что хочешь сделать:"
        ),
    },
    "menu_pick_mode": {
        "en": "Choose analysis mode:",
        "ru": "Выбери режим анализа:",
    },
    "btn_mode_live": {
        "en": "📡 Live analysis",
        "ru": "📡 Живой анализ",
    },
    "btn_mode_backtest": {
        "en": "🧮 Simulator",
        "ru": "🧮 Симулятор",
    },
    "btn_mode_patterns": {
        "en": "🔬 Patterns",
        "ru": "🔬 Паттерны",
    },
    "btn_mode_research": {
        "en": "🧪 Research",
        "ru": "🧪 Исследование",
    },
    "pick_asset_live": {
        "en": "📡 *Live analysis*\n\nChoose a token:",
        "ru": "📡 *Живой анализ*\n\nВыбери токен:",
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
    "layer_vol_trend": {
        "en": "Vol.Trend ",
        "ru": "Объём↑↓   ",
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
    "layer_sr_proximity": {
        "en": "S/R Level ",
        "ru": "Уровни S/R",
    },
    "layer_candle_pattern": {
        "en": "Candle    ",
        "ru": "Свеча     ",
    },
    "layer_pressure": {
        "en": "Buy/Sell  ",
        "ru": "Давление  ",
    },
    # Short names used in "failed layers" list
    "layer_volatility_short":  {"en": "Volatility",   "ru": "Волатильность"},
    "layer_trend_short":       {"en": "Trend",         "ru": "Тренд"},
    "layer_momentum_short":    {"en": "Momentum",      "ru": "Импульс"},
    "layer_vol_trend_short":   {"en": "Vol.Trend",     "ru": "Объём тренд"},
    "layer_liquidity_short":   {"en": "Liquidity",     "ru": "Ликвидность"},
    "layer_risk_reward_short": {"en": "Risk/Reward",   "ru": "Риск/Доход"},
    "layer_news_short":        {"en": "News",          "ru": "Новости"},
    "layer_sr_proximity_short":   {"en": "S/R Proximity",   "ru": "Уровни S/R"},
    "layer_candle_pattern_short": {"en": "Candle Pattern", "ru": "Свечной паттерн"},
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

    # ── Simulation calculator ─────────────────────────────────────────────────
    "research_pick_asset": {
        "en": (
            "🧪 *Research* — choose asset:\n\n"
            "_Runs 12 combinations: 4 TP/SL pairs × 3 periods (90d / 180d / 365d)._\n"
            "_Takes about 60 seconds._"
        ),
        "ru": (
            "🧪 *Исследование* — выбери актив:\n\n"
            "_Запустит 12 комбинаций: 4 пары TP/SL × 3 периода (90 / 180 / 365 дней)._\n"
            "_Займёт около 60 секунд._"
        ),
    },
    "research_running": {
        "en": (
            "🧪 *Research — {symbol}*\n\n"
            "⏳ Running 12 simulations...\n"
            "_4 TP/SL pairs × 3 periods (90d / 180d / 365d)_\n\n"
            "_Takes 60-90 seconds. Please wait._"
        ),
        "ru": (
            "🧪 *Исследование — {symbol}*\n\n"
            "⏳ Запускаю 12 симуляций...\n"
            "_4 пары TP/SL × 3 периода (90 / 180 / 365 дней)_\n\n"
            "_Займёт 60-90 секунд. Подожди._"
        ),
    },
    "research_failed": {
        "en": "❌ Research failed: `{err}`",
        "ru": "❌ Ошибка исследования: `{err}`",
    },
    "bt_pick_asset": {
        "en": "🧮 *Simulation* — choose asset:",
        "ru": "🧮 *Симуляция* — выбери актив:",
    },
    "bt_pick_period": {
        "en": "🧮 *{symbol}* — choose period to simulate:",
        "ru": "🧮 *{symbol}* — выбери период симуляции:",
    },
    "bt_pick_budget": {
        "en": (
            "🧮 *{symbol} · {days}d* — choose capital per trade:\n\n"
            "_Each trade uses this amount\\. Fees and LT tax are applied\\._"
        ),
        "ru": (
            "🧮 *{symbol} · {days}д* — выбери капитал на сделку:\n\n"
            "_Каждая сделка использует эту сумму\\. Учитываются комиссии и налог ЛТ\\._"
        ),
    },
    "bt_pick_tp": {
        "en": (
            "🧮 *{symbol} · {days}d · ${budget}* — choose take profit:\n\n"
            "_Stop loss is set automatically at TP÷2 \\(2:1 risk/reward\\)\\._"
        ),
        "ru": (
            "🧮 *{symbol} · {days}д · ${budget}* — выбери тейк\\-профит:\n\n"
            "_Стоп\\-лосс устанавливается автоматически TP÷2 \\(RR 2:1\\)\\._"
        ),
    },
    "bt_running": {
        "en": (
            "⏳ Running simulation *{symbol}* — *{days} days*\n\n"
            "_Downloading {candles} candles from Binance..._\n"
            "_This may take 20–40 seconds._"
        ),
        "ru": (
            "⏳ Запускаю симуляцию *{symbol}* — *{days} дней*\n\n"
            "_Загружаю ~{candles} свечей с Binance..._\n"
            "_Это займёт 20–40 секунд._"
        ),
    },
    "bt_result": {
        "en": (
            "🧮 *{symbol} — Simulation {days}d*\n"
            "_{date_from}  →  {date_to}_  ·  *${budget}*  TP {tp}% / SL {sl}%\n\n"
            "Signals:   *{signals}*  _{freq}_\n"
            "Win rate:  *{wr}%*  \\({wins}✅ / {losses}❌ / {timeouts}⏱\\)\n"
            "_Break\\-even \\(after Binance fees\\): {be_fees}%_\n\n"
            "Avg win:   *{avg_win_usd}$*  /  Avg loss: *{avg_loss_usd}$*\n\n"
            "💰 *Total result over {days} days:*\n"
            "  Gross \\(no fees\\):      *{gross_usd}$*\n"
            "  After Binance 0\\.2%:  *{net_fees_usd}$*\n"
            "  After LT tax 15%:    *{after_tax_usd}$*\n"
            "  📅 Projected annual:  *{annual_usd}$*\n\n"
            "Max drawdown:  *{max_dd}%*  ·  Sharpe: *{sharpe}*\n\n"
            "Best:  *{best_pnl}%*  _{best_time}_\n"
            "Worst: *{worst_pnl}%*  _{worst_time}_\n\n"
            "📊 *Market conditions now:*\n"
            "{market_ctx}"
        ),
        "ru": (
            "🧮 *{symbol} — Симуляция {days}д*\n"
            "_{date_from}  →  {date_to}_  ·  *${budget}*  TP {tp}% / SL {sl}%\n\n"
            "Сигналов:  *{signals}*  _{freq}_\n"
            "Win rate:  *{wr}%*  \\({wins}✅ / {losses}❌ / {timeouts}⏱\\)\n"
            "_Безубыток \\(после комис\\. Binance\\): {be_fees}%_\n\n"
            "Avg выигрыш:  *{avg_win_usd}$*  /  Avg убыток: *{avg_loss_usd}$*\n\n"
            "💰 *Итого за {days} дней:*\n"
            "  Брутто \\(без комис\\):      *{gross_usd}$*\n"
            "  После комис Binance 0\\.2%: *{net_fees_usd}$*\n"
            "  После налога Литва 15%:   *{after_tax_usd}$*\n"
            "  📅 Прогноз на год:         *{annual_usd}$*\n\n"
            "Max просадка:  *{max_dd}%*  ·  Sharpe: *{sharpe}*\n\n"
            "Лучшая:  *{best_pnl}%*  _{best_time}_\n"
            "Худшая:  *{worst_pnl}%*  _{worst_time}_\n\n"
            "📊 *Состояние рынка сейчас:*\n"
            "{market_ctx}"
        ),
    },
    "bt_no_signals": {
        "en": (
            "⚠️ *No signals found* for {symbol} in {days} days\\.\n\n"
            "📊 *Market conditions now:*\n"
            "{market_ctx}\n\n"
            "_Filters are calibrated for active markets\\. "
            "Low volatility or volume may reduce signals\\._"
        ),
        "ru": (
            "⚠️ *Сигналов не найдено* для {symbol} за {days} дней\\.\n\n"
            "📊 *Состояние рынка сейчас:*\n"
            "{market_ctx}\n\n"
            "_Фильтры настроены на активные рынки\\. "
            "Низкая волатильность или объём сокращают сигналы\\._"
        ),
    },
    "bt_failed": {
        "en": "❌ Simulation failed: `{err}`",
        "ru": "❌ Ошибка симуляции: `{err}`",
    },
    "btn_bt_patterns": {
        "en": "🔬 View patterns",
        "ru": "🔬 Паттерны",
    },
    "btn_bt_change_budget": {
        "en": "💵 Change budget",
        "ru": "💵 Изменить бюджет",
    },
    "btn_bt_again": {
        "en": "🔄 New simulation",
        "ru": "🔄 Новая симуляция",
    },

    # ── Patterns ──────────────────────────────────────────────────────────────
    "pat_no_data": {
        "en": "⚠️ No backtest data for *{symbol}*.\nRun `/backtest` first.",
        "ru": "⚠️ Нет данных бэктеста для *{symbol}*.\nСначала запусти `/backtest`.",
    },
    "btn_back_to_bt": {
        "en": "◀️ Back",
        "ru": "◀️ Назад",
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
