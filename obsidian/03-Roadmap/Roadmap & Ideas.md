# Roadmap & Ideas

→ See also: [[Architecture Overview]] | [[Dev Log]]

---

## Currently Working ✅

- 10-layer signal analysis (live)
- Hard blocks: RSI >65, ADX 25–40, weekly EMA21 — [[Filters & Hard Blocks]]
- Backtest engine (all 5 assets, all periods) — [[Backtest Engine]]
- Simulator: user picks asset / period / budget / TP/SL
- Research: fully automatic grid search (12 combos + budget projections) — [[Research Feature]]
- Pattern analyzer (best hours/weekdays, ADX bands, score bands from DB)
- ADX danger zone filter (data-driven, 318 trades) — [[ADX Danger Zone Analysis]]
- Lithuanian GPM 15% tax (net annual, losses offset gains) — [[Tax & Fees Model]]
- Local Ollama LLM meta-review (qwen2.5:3b / llama3.2)
- EN/RU Telegram UI
- Docker deployment

---

## In Progress / Next Up 🔧

### Real Trade Execution (Phase 2–4)
- [ ] Binance API key auth (spot trading)
- [ ] Fetch account balance
- [ ] Market buy / sell orders
- [ ] In-memory TP/SL tracking loop (every 30s)
- [ ] Trade logging to DB

### Signal Quality
- [ ] Per-asset threshold calibration (SOL, LINK not yet tuned)
- [ ] L2 trend: add volume confirmation to Golden Cross
- [ ] L3 momentum: consider Stochastic RSI

### Bot UX
- [ ] Trade history command `/history`
- [ ] Daily PnL stats via scheduled Telegram message

---

## Ideas / Backlog 💡

- Persistent user state (survive bot restart) — currently in-memory
- Telegram alert on signal (push without user action)
- Web dashboard for backtest results
- Portfolio mode (scan all 5 assets simultaneously)
- Webhook mode instead of polling (production)
- Candle cache (SQLite or Redis) — currently always fetches fresh from Binance

---

## Known Issues / Bugs 🐛

- User state lost on bot restart (`context.user_data` is in-memory)
- Backtest slow for 1-year periods (Research: 1 fetch for 12 combos mitigates this)
- MarkdownV2 escaping fragile — `_esc()` must be applied to all dynamic values; Research uses plain `Markdown` instead to avoid issues with float dots

---

## Threshold Tuning Notes

- **BTC/ETH:** ADX >25, vol >$500M — original thresholds work well
- **LTC:** ADX 10–18 typical, vol $30–80M → relaxed to ADX >15, vol >$10M
- **SOL:** Higher volume, ADX varies — watch L1 pass rate
- **LINK:** Spot only (no futures) → L8 funding always skipped/pass