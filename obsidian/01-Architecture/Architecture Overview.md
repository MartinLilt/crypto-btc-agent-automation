# Architecture Overview

→ See also: [[Signal Layers Deep Dive]] | [[Backtest Engine]] | [[Filters & Hard Blocks]] | [[Research Feature]]

---

## High-Level Flow

```
User (Telegram)
    │
    ▼
main.py  ──  Telegram Bot (python-telegram-bot 21.9)
    │            Inline keyboard UI
    │            EN/RU i18n via src/bot/strings.py
    │
    ├──► src/data/binance_client.py  ── Binance REST API
    │       candles (1h, 4h, 1w), order book, funding, tickers, taker vol
    │
    ├──► src/signals/indicators.py  ── 10-layer signal engine (pure)
    │       Returns (score: int, dict) per layer
    │       Hard blocks: RSI >65, ADX 25–40, weekly EMA21
    │       → [[Signal Layers Deep Dive]]  [[Filters & Hard Blocks]]
    │
    ├──► src/data/news_client.py  ── News sentiment (L7)
    │
    ├──► src/ai/orchestrator.py  ── Local Ollama LLM meta-review (optional)
    │       Model: qwen2.5:3b (Docker) / llama3.2 (local)
    │       ai_review() — structured JSON for live analysis
    │       ai_review_simulation() — plain text for backtest results
    │
    ├──► src/backtest/engine.py  ── Historical simulation
    │       Slides 220-candle window, simulates TP/SL/timeout
    │       SQLite for run + trade storage
    │       Research grid: 4 TP/SL × 3 periods = 12 combos
    │       → [[Backtest Engine]]  [[Research Feature]]
    │
    ├──► src/signals/pattern_analyzer.py  ── Patterns from DB
    │       Best hour, best weekday, ADX band WR, score band WR
    │
    └──► src/data/db.py  ── SQLite (data/backtest.db)
```

---

## Data Sources

| Source | Data | Auth |
|--------|------|------|
| Binance REST `/api/v3/klines` | OHLCV candles (1h, 4h, 1w) | None |
| Binance REST `/api/v3/depth` | Order book | None |
| Binance REST `/api/v3/ticker/24hr` | 24h volume | None |
| Binance Futures `/fapi/v1/fundingRate` | Funding rate | None |
| Binance Futures `/fapi/v1/openInterest` | Open interest | None |
| alternative.me/fng | Fear & Greed Index | None |
| News API (configurable) | Headlines | API key |
| Ollama (local) | LLM verdict | None (local) |

---

## Telegram Bot Flows

```
/start → Main Menu
    ├── 📊 Live Analysis  → asset picker → _run_analysis()
    ├── 🧮 Simulator      → asset → period → budget → TP/SL → bt_run()
    ├── 📈 Patterns       → asset → compute_patterns() → format
    └── 🧪 Research       → asset → [auto] run_backtest_research()
```

User state stored in `context.user_data[CFG]` (in-memory, lost on restart).
Backtest and Research run in `executor` to avoid blocking the event loop.

---

## Caching Strategy

- **SQLite:** backtest runs + trades (persistent) — `data/backtest.db`
- **Redis:** Fear&Greed history + funding rate history (backtest only, optional)
- **No candle caching** — always fetched fresh from Binance

---

## Asset-Specific Thresholds

LTC/SOL/LINK have lower liquidity — thresholds relaxed:

```python
ASSET_OVERRIDES = {
    "LTCUSDT":  {"adx_min": 15, "vol_min": 10_000_000},
    "SOLUSDT":  {"adx_min": 15, "vol_min": 10_000_000},
    "LINKUSDT": {"adx_min": 15, "vol_min": 10_000_000},
}
# BTC/ETH: adx_min=25, vol_min=500_000_000
```

---

## Key Files

| File | Role |
|------|------|
| `main.py` | Bot handlers, UI logic, all flows |
| `src/signals/indicators.py` | All 10 signal layers (pure functions) |
| `src/signals/pattern_analyzer.py` | ADX/score/hour/weekday pattern analysis |
| `src/backtest/engine.py` | Backtest + Research grid search |
| `src/data/binance_client.py` | Binance REST calls |
| `src/data/news_client.py` | News fetch + sentiment scoring |
| `src/data/db.py` | SQLite init, save/load |
| `src/ai/orchestrator.py` | Ollama LLM review |
| `src/bot/strings.py` | All UI strings EN+RU via `t("key", lang)` |
| `scripts/diagnose.py` | Debug tool — layer-by-layer live status |