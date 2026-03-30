# BTC Trading Agent — Roadmap

## Status: 🚧 In Development

---

## Strategy: Autonomous Spot Trading (BTCUSDT)

Agent enters short-term long positions targeting **+2% take profit**.
No stop-loss or take-profit orders are placed on the exchange — all levels
are tracked **in memory only** so market makers cannot see them.

---

## Decision Layers (Entry Signal)

All 5 must be YES to enter a trade:

| #   | Layer          | Question                       | Data Source             |
| --- | -------------- | ------------------------------ | ----------------------- |
| 1   | **Volatility** | Is the market moving?          | ATR > threshold         |
| 2   | **Trend**      | Is the trend up?               | Price > MA50 > MA200    |
| 3   | **Momentum**   | Is the market not overbought?  | RSI < 70                |
| 4   | **Timing**     | Is it the right hour?          | Hour UTC ∈ {13, 15, 20} |
| 5   | **Liquidity**  | Can we enter without slippage? | Bid/Ask spread < $10    |

All data comes from **Binance hourly candles** — no external APIs needed.

---

## Exit Rules (Silent, In-Memory Only)

| Trigger     | Condition                   | Action      |
| ----------- | --------------------------- | ----------- |
| Take Profit | price ≥ entry × 1.02        | Market sell |
| Stop Loss   | price ≤ entry − (ATR × 1.5) | Market sell |
| Timeout     | time held ≥ 48h             | Market sell |

Exchange sees only: **buy → hold → sell**. Nothing else.

---

## Phase 1: Infrastructure ✅

- [x] Docker container
- [x] Telegram bot connected
- [x] Bot commands menu (`/start`, `/restart`)
- [x] `.env` config

## Phase 2: Binance Integration 🔲

- [ ] Connect Binance API
- [ ] Fetch real-time BTC price
- [ ] Fetch account balance

## Phase 3: Trading Logic 🔲

- [ ] Calculate ATR, RSI, MA50, MA200 from candles
- [ ] Check all 5 entry conditions
- [ ] Silent stop-loss & take-profit (in-memory only)
- [ ] Position monitoring loop (every minute)

## Phase 4: Execution 🔲

- [ ] Market buy order
- [ ] Market sell order
- [ ] Trade logging to DB

## Phase 5: Reporting 🔲

- [ ] Daily stats via Telegram
- [ ] Trade history
- [ ] PnL summary
