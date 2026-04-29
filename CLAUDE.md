# CLAUDE.md — Crypto BTC Agent Automation

## Project Overview
Telegram trading bot for spot crypto analysis. **No real trades executed** — analysis + backtest only.

Users interact via Telegram inline buttons. Supports EN/RU. Deployed via Docker.

## Stack
- **Python 3.13** + `.venv`
- **python-telegram-bot 21.9** — Telegram UI
- **Binance REST API** — market data (no auth for public endpoints)
- **OpenAI GPT-4o-mini** — optional AI meta-layer (L11 effectively)
- **SQLite** (`data/backtest.db`) — backtest run + trade storage
- **Redis** — Fear&Greed + funding rate history cache
- **Docker / docker-compose** — deployment

## Key Files
| File | Role |
|------|------|
| `main.py` | Telegram bot, all handlers, UI logic |

| `src/signals/indicators.py` | All 10 signal layers (pure functions) |
| `src/signals/pattern_analyzer.py` | Best-hour / best-weekday patterns from backtest DB |
| `src/backtest/engine.py` | Historical backtest over candle windows |
| `src/data/binance_client.py` | Binance REST calls (candles, OB, funding, ticker) |
| `src/data/news_client.py` | Crypto news fetch + sentiment scoring |
| `src/data/db.py` | SQLite init, save/load backtest runs & trades |
| `src/ai/orchestrator.py` | OpenAI GPT review of 10-layer report |
| `src/bot/strings.py` | All UI strings in EN + RU (`t("key", lang, **kw)`) |
| `scripts/diagnose.py` | Debug tool — shows live layer-by-layer status |

## Supported Assets
`BTCUSDT`, `SOLUSDT`, `ETHUSDT`

Backtest evidence (720d, TP=3%/SL=1.5%, after fees + LT 15% tax, 4h-wired):
- BTC: +21.08%, WR=37.8%, profitable both halves walk-forward
- ETH: +28.86%, WR=40.2%, walk-forward OOS = +29.41%
- SOL: +67.49%, WR=48.0%, walk-forward OOS = +47.34% (strongest)

Score-based system uses universal thresholds; per-asset tuning not currently needed.

## Signal Architecture — 10 Layers
All 10 must pass → `should_enter = True`.

| Layer | Name | Key Condition |
|-------|------|---------------|
| L1 | Volatility | ATR > $500, ADX > 25, volume spike |
| L2 | Trend | Price > EMA50 > EMA200, slope up |
| L3 | Momentum | RSI 40–65, MACD hist > 0 |
| L4 | Timing | UTC hour in {2,3,7,8,13,14,15,20}, not weekend |
| L5 | Liquidity | Spread < $10, depth > 1 BTC, vol24 > $500M |
| L6 | Risk/Reward | Net profit > 0, RR >= 1.5 after Binance 0.1% fees |
| L7 | News | Bearish < 50% of total articles |
| L8 | Funding | FR in [-0.05%, +0.05%], OI change > -3% |
| L9 | Fear & Greed | Index 15–74 (not extreme panic/greed) |
| L10 | Buy Pressure | Taker buy ratio >= 45%, net BTC > -500 |

**LTC/SOL/LINK specific:** L1 ADX threshold relaxed to 15, L5 volume threshold $10M (smaller cap assets).

## Backtest Engine
- Slides 220-candle window over history
- Entry = open of next candle after signal (slippage model)
- TP/SL checked on each candle's high/low
- Timeout = 48h if no TP/SL hit
- Saves runs + trades to SQLite

## Telegram Bot Flows
```
/start → Main Menu (Live / Backtest / Patterns)
  Live → asset picker → _run_analysis()
  Backtest → asset → period → bt_run() → result
  Patterns → asset → compute_patterns() → format
```

## Run Locally
```bash
cp .env.example .env  # fill TELEGRAM_BOT_TOKEN, OPENAI_API_KEY
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Run via Docker
```bash
docker-compose up --build
```

## Env Variables
| Var | Required | Default |
|-----|----------|---------|
| `TELEGRAM_BOT_TOKEN` | YES | — |
| `OPENAI_API_KEY` | NO | AI layer skipped |
| `OPENAI_MODEL` | NO | `gpt-4o-mini` |
| `BINANCE_REST_URL` | NO | `https://api.binance.com` |
| `BINANCE_FUTURES_URL` | NO | `https://fapi.binance.com` |

## Coding Conventions
- All UI strings go through `t("key", lang)` in `src/strings.py`
- Layer functions return `(bool, dict)` — signal + details
- New assets need threshold tuning (ADX, volume) — check L1 and L5
- Backtest window = 220 candles minimum (200 for EMA-200 warmup + 20 buffer)
- MarkdownV2 used in backtest results → escape with `_esc()` in main.py

## Obsidian Vault (Auto-Loaded)
Project knowledge base lives in `obsidian/`. Files are auto-included via `.claudeinclude`.

### Vault Structure
```
obsidian/
  00-Index/README.md       <- Hub: quick links, key numbers, status
  01-Architecture/         <- System design, layers, features
  02-DevLog/Dev Log.md     <- Reverse-chrono change log
  03-Roadmap/              <- Features, ideas, bugs
```

### When to Update
- **Architecture changes** → update relevant `01-Architecture/*.md`
- **New layers / thresholds** → update `Signal Layers Deep Dive.md`
- **Session changes** → prepend to `02-DevLog/Dev Log.md` (newest first)
- **Roadmap progress** → update `03-Roadmap/Roadmap & Ideas.md`

### Linking Convention
Use `[[Page Name]]` for internal links. Keep hub pages updated with new links.