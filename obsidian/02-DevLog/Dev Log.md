# Development Log

Reverse-chronological. Add entry at top when significant changes land.

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