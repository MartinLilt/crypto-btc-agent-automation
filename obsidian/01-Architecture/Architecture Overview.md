# Architecture Overview

## High-Level Flow
```
User (Telegram)
    │
    ▼
main.py  ──  Telegram Bot (python-telegram-bot 21.9)
    │            Inline keyboard UI
    │            EN/RU i18n via src/strings.py
    │
    ├──► src/binance_client.py  ── Binance REST API
    │       candles (1h), order book, funding, tickers, taker vol
    │
    ├──► src/indicators.py  ── 10-layer signal engine (pure)
    │       Returns (bool, dict) per layer
    │
    ├──► src/news_client.py  ── News sentiment (L7)
    │
    ├──► src/ai_orchestrator.py  ── OpenAI GPT meta-review (optional)
    │       gpt-4o-mini reviews all 10 layers, writes plain-English notes
    │
    ├──► src/backtest_engine.py  ── Historical simulation
    │       Slides 220-candle window, simulates TP/SL/timeout
    │       Redis cache for Fear&Greed + funding history
    │       SQLite for run + trade storage
    │
    ├──► src/pattern_analyzer.py  ── Best patterns from DB
    │       Best hour, best weekday, win-rate breakdown
    │
    └──► src/db.py  ── SQLite (data/backtest.db)
```

## Data Sources
| Source | Data | Auth |
|--------|------|------|
| Binance REST `/api/v3/klines` | OHLCV candles | None |
| Binance REST `/api/v3/depth` | Order book | None |
| Binance REST `/api/v3/ticker/24hr` | 24h volume | None |
| Binance Futures `/fapi/v1/fundingRate` | Funding rate | None |
| Binance Futures `/fapi/v1/openInterest` | OI | None |
| alternative.me/fng | Fear & Greed Index | None |
| News API (configurable) | Headlines | API key |
| OpenAI | GPT verdict | API key |

## Telegram Bot Architecture
- Single polling loop (`run_polling`)
- User state stored in `context.user_data[CFG]` (in-memory, lost on restart)
- Callback query pattern routing (regex)
- Backtest runs in executor to avoid blocking event loop

## Caching Strategy
- Redis: Fear&Greed history + funding rate history (backtest only)
- SQLite: backtest runs + trades (persistent)
- No candle caching — always fetched fresh

## Asset-Specific Thresholds
LTC/SOL/LINK have lower liquidity — thresholds relaxed in backtest engine:
- L1 ADX minimum: 25 → 15
- L5 volume 24h minimum: $500M → $10M