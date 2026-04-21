# Roadmap & Ideas

## Currently Working ✅
- 10-layer signal analysis (live)
- Backtest engine (all 5 assets, all periods)
- Pattern analyzer (best hours/weekdays from DB)
- AI meta-layer (GPT-4o-mini review)
- EN/RU Telegram UI
- Docker deployment

## In Progress / Next Up 🔧

### Real Trade Execution (Phase 2-4)
Original roadmap items still pending:
- [ ] Binance API key auth (spot trading)
- [ ] Fetch account balance
- [ ] Market buy order
- [ ] Market sell order
- [ ] In-memory TP/SL tracking loop (every minute)
- [ ] Trade logging to DB

### Bot UX Improvements
- [ ] User-configurable TP/SL (currently hardcoded 2%/1%)
- [ ] User-configurable budget
- [ ] Trade history command `/history`
- [ ] Daily PnL stats via scheduled Telegram message

### Signal Quality
- [ ] Per-asset threshold calibration (SOL, LINK not yet tuned)
- [ ] L2 trend: add volume confirmation to Golden Cross
- [ ] L3 momentum: consider Stochastic RSI as alternative

## Ideas / Backlog 💡
- Paper trading mode (simulate trades without real money)
- Multi-timeframe confirmation (4h trend + 1h entry)
- Telegram alert on signal (push notification without waiting for user)
- Web dashboard for backtest results
- Portfolio mode (scan all 5 assets simultaneously)
- Webhook mode instead of polling (for production)

## Known Issues / Bugs 🐛
- User state lost on bot restart (in-memory `context.user_data`)
- Backtest can be slow for 1-year periods (many API calls, no candle cache)
- MarkdownV2 escaping is fragile — `_esc()` must be applied carefully
- Redis not strictly required (SQLite fallback exists for F&G + funding)

## Threshold Tuning Notes
- LTC: ADX regularly 10-18, volume $30-80M → had to relax thresholds
- SOL: Higher volume but ADX varies — watch L1 pass rate
- LINK: Spot only (no futures) → L8 always skipped/pass
- BTC/ETH: Original thresholds work well (high liquidity)