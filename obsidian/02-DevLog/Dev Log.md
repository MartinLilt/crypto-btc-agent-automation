# Development Log

Reverse-chronological. Add entry at top when significant changes land.

---

## 2026-04-22 — Serious-level analytics upgrade (L8/L9 replaced, MTF, smart exits)

**Summary:** Major pipeline upgrade — replaced spot-irrelevant layers with technically meaningful ones, added multi-timeframe trend confirmation, improved volume spike detection, and added smart exits.

### L8 — Funding Rate → S/R Proximity (`src/signals/support_resistance.py`, NEW)
- New file: fractal swing high detection + level clustering + resistance scoring
- `check_sr_proximity(candles, tp_pct=2.0)` — scores 0-10: clear path→10, 1 blocker by gap (≥1.5%→7, ≥1.0%→5, ≥0.5%→3), 2 blockers→2-4, 3+→1
- Returns: score, pass, price, tp_price, swing_highs, blocking_levels, nearest_resistance, n_blockers

### L9 — Fear & Greed → Candle Pattern (`src/signals/candle_patterns.py`, NEW)
- New file: checks last 3 candles for bullish/bearish patterns
- `detect_candle_patterns(candles)` — scoring: STRONG_BULL(10), BULLISH_ENGULFING(9), HAMMER/MORNING_STAR(8), BULLISH(6), DOJI/NEUTRAL_BULL(5), BEARISH(3), SHOOTING_STAR(2), BEARISH_ENGULFING(1)
- Returns: score, pass, pattern name, description, c_open/close/high/low, body_pct

### L2 — Multi-timeframe trend confirmation (`src/signals/indicators.py`)
- `is_uptrend(candles, candles_4h=None)` — optional 4h EMA50/EMA200 alignment
- 4h fully aligned (price > EMA50 > EMA200): +2 bonus; mixed: +1; misaligned: -2
- Details dict includes: tf4h_bonus, tf4h_aligned, tf4h_ema50, tf4h_ema200

### L4 — Volume spike uses 20-period SMA (`src/signals/indicators.py`)
- Replaced 4h vs 24h avg with: recent 3-candle avg vs SMA(20) excluding last 3
- More responsive to actual spike conditions vs the rolling 24h bucket

### L10 — Buy pressure lookback 24h → 6h
- `get_taker_buy_pressure(SYMBOL, hours=6)` — more reactive to current order flow

### `src/backtest/engine.py`
- Removed F&G/funding pre-fetches (Redis-cached external data no longer needed for L8/L9)
- L8: `check_sr_proximity(candles_window, tp_pct=tp_pct)` — pure candle computation
- L9: `detect_candle_patterns(candles_window)` — pure candle computation
- `_eval_bar` signature simplified (removed `fg_history`, `funding_history` params)

### `src/ai/orchestrator.py`
- L8 description: S/R blocking levels and nearest resistance
- L9 description: pattern name + body percentage
- SYSTEM_PROMPT points 8/9 updated to match new layers

### `src/bot/strings.py`
- `layer_funding` → `layer_sr_proximity` ("S/R Level")
- `layer_fear_greed` → `layer_candle_pattern` ("Candle")
- Added short versions: `layer_sr_proximity_short`, `layer_candle_pattern_short`

### `src/trading/monitor.py`
- Added 4h candles fetch; passes `candles_4h=candles_4h` to `check_entry_signal`
- Pressure: `hours=6` (was 24)
- Removed `get_funding_rate` / `get_fear_greed_index` imports
- **Smart exits** in `watcher_loop`: while in profit, RSI > 75 → SMART_EXIT_RSI; MACD bearish cross → SMART_EXIT_MACD; both close position immediately and notify

---

## 2026-04-22 — Trading module + L4 Volume Trend

**Changes:**

### L4 — replaced Timing with Volume Trend
- `_score_l4_vol_trend(ratio)` replaces `_score_l4(hour, weekday_ok)`
- `is_volume_trending(candles)` replaces `is_good_hour()`
- Scoring: ratio ≥1.5→10, ≥1.2→8, ≥0.8→6, ≥0.5→3, else 1
  (ratio = last-4h volume ÷ 24h-avg 4h bucket)
- Layer key renamed `L4_timing` → `L4_vol_trend` everywhere
- Updated `main.py`, `orchestrator.py`, `strings.py` for new key/label

### `src/trading/` module (new)
- **`modes.py`** — `TradingMode` enum (SIMULATION/LIVE), all constants
- **`position.py`** — SQLite-backed position tracker; `new_position()`, `check_and_update()`, `close_position()`, `get_position()`; trailing stop logic (break-even at +1%, trail at +1.5%)
- **`executor.py`** — `execute_buy()` / `execute_sell()` for both SIM (virtual fill) and LIVE (real Binance market orders via `quoteOrderQty`)
- **`monitor.py`** — `scanner_loop()` (15-min) + `watcher_loop()` (30-sec) async background tasks; Telegram notifications to `ADMIN_CHAT_ID`

### `src/data/db.py`
- Added `positions` table (SQLite): symbol, mode, entry/exit prices, qty, sl/tp, breakeven_hit, status, pnl
- Added helpers: `open_pos()`, `get_open_pos()`, `close_pos()`, `update_pos_sl()`, `get_closed_positions()`

### `main.py`
- Imports `TradingMode` at top
- Added `/mode sim|live` command — switches bot_data trading_mode
- Added `/status` command — shows open position + last 5 closed trades
- `post_init` now: calls `init_db()`, defaults to SIMULATION mode, starts `scanner_loop` + `watcher_loop` as `asyncio.create_task`
- New env var: `ADMIN_CHAT_ID` (notifications target), `TRADE_BUDGET` (default 100 USDT)

**New env vars needed:**
```
ADMIN_CHAT_ID=<your telegram user id>
TRADE_BUDGET=100
```

---

## 2026-04-22 — Scoring system (0-10 per layer, total 0-100)

**Changes:**
- Replaced binary pass/fail with 0-10 score per layer in `src/signals/indicators.py`
- Entry condition: `total_score >= 70` (was: all 10 must pass)
- Added `_score_l1..l10()` helper functions with graduated scoring logic
- `pass` field kept for backward compat (derived as `score >= 7`)
- Added `_score_icon()` → 🟢 (≥7) / 🟡 (4-6) / 🔴 (<4)
- `main.py` display: each layer shows `🟢/🟡/🔴 Name 7/10 — data`, total score shown
- WAIT message now shows top-3 weakest layers with their scores
- `backtest/engine.py`: `_eval_bar` uses score sum instead of all-pass logic; L4/L7/L9 get neutral score 5 in backtest
- `diagnose.py` updated to show per-layer scores

**Entry threshold:** 70/100 (configurable via `ENTRY_SCORE_THRESHOLD`)

---

## 2026-04-22 — BTC-only + src/ restructure

**Changes:**
- Removed ETH/LTC/SOL/LINK — only BTCUSDT remains (`main.py` ASSETS)
- Reorganised `src/` flat files into 5 subdirectories:
  - `src/signals/` — indicators.py, pattern_analyzer.py
  - `src/data/` — binance_client.py, news_client.py, db.py
  - `src/backtest/` — engine.py (was backtest_engine.py)
  - `src/ai/` — orchestrator.py (was ai_orchestrator.py)
  - `src/bot/` — strings.py
- Removed LTC/SOL/LINK threshold overrides in backtest engine (ADX min back to 20, volume floor $30M fixed)
- Updated all imports in main.py, scripts/diagnose.py, and cross-module imports
- Deleted scripts/ltc_debug.py (dead code for removed assets)

---

## 2026-04-22 — Obsidian vault + CLAUDE.md setup
**By:** Claude
- Created `CLAUDE.md` with full project docs
- Created `obsidian/` knowledge base (architecture, signals, backtest, roadmap)
- Created `.claude/settings.json` with hooks for auto-Obsidian updates

---

## ~2026-04-20 — Market context in backtest results
**Commits:** `a874137`
- Added `_build_market_context()` in `main.py`
- Shows ADX strength, volume level, trend direction after backtest
- Explains to user why low-ADX markets produce few signals

---

## ~2026-04-19 — LTC threshold relaxation
**Commits:** `b44fe47`, `917a4bb`
- L1 ADX min: 25→15 for LTCUSDT (small cap, low ADX normal)
- L5 volume min: $500M→$10M for LTCUSDT
- Fixed Redis retry loop bug
- Added `save_db` param to backtest
- Fixed `diagnose.py` symbol handling

---

## ~2026-04-18 — L9 Fear/Greed blocker removal
**Commit:** `3c91743`
- L9 no longer blocks signal in backtest (was causing 0 signals on many runs)
- L5 volume threshold lowered to $30M in backtest

---

## Earlier — Initial build (Phases 1+)
- 10-layer signal system built
- Telegram bot with inline keyboard UI
- EN/RU i18n via `src/strings.py`
- Backtest engine with SQLite persistence
- Pattern analyzer (best hours/weekdays)
- AI meta-layer (OpenAI GPT-4o-mini)
- Docker deployment