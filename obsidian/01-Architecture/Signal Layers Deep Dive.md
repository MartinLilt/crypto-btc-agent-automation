# Signal Layers Deep Dive

All defined in `src/signals/indicators.py`. Each returns `(score: int, dict)`.
Entry threshold: `total_score >= 70` (out of 100).

→ See also: [[Filters & Hard Blocks]] | [[ADX Danger Zone Analysis]] | [[Architecture Overview]]

---

## L1 — Volatility (`is_market_moving`)

**Purpose:** Is the market actually moving? Filters dead/sideways markets.

- ATR > $500 (absolute floor)
- ATR > 30-period ATR MA × 1.2 (volatility expanding)
- Last candle volume > 20-period average (real participation)
- ADX > 25 (not choppy sideways) — *but see [[ADX Danger Zone Analysis]]*
- **ADX slope bonus:** if ADX rising by >1 pt over last 5 candles → +2

**Asset overrides:** LTC/SOL/LINK use ADX > 15, volume > $10M (vs BTC/ETH: ADX > 25, vol > $500M)

---

## L2 — Trend (`is_uptrend`)

**Purpose:** Long-only filter. Only trade in structural uptrend.

- Price > EMA50 > EMA200, EMA50 slope rising
- Golden cross or established uptrend (EMA50 > EMA200 for 5 bars)
- **4h MTF bonus:** 4h EMA50 > EMA200 → +2; mixed → +1; misaligned → -2
- **24h VWAP:** price above VWAP → +1; below → -1

**Requires:** 201+ candles

---

## L3 — Momentum (`is_not_overbought`)

**Purpose:** Enter at a healthy point, not at exhaustion.

- RSI in [40, 65]; MACD histogram > 0
- **4h RSI cross-timeframe:** both TFs in 40-65 → +2; 4h overbought → -2
- **RSI divergence:** price new high + RSI lower → -2; reverse → +2
- **Hard filter:** RSI > 65 blocks entry entirely → [[Filters & Hard Blocks]]

---

## L4 — Timing (`check_timing`)

**Purpose:** Only trade during high-liquidity hours.

- UTC hours allowed: {2, 3, 7, 8, 13, 14, 15, 20}
- Weekend entries blocked (Saturday/Sunday)

---

## L5 — Liquidity (`has_liquidity`)

**Purpose:** Can we enter without getting killed by slippage?

- Spread < $10; order book depth ≥ 1 BTC; 24h volume > $500M
- **Bid/Ask imbalance:** bid/ask ≥ 3.0 → +2; ≥1.5 → +1; ≤0.33 → -2; ≤0.67 → -1

**Asset overrides:** LTC/SOL/LINK use volume > $10M

---

## L6 — Risk/Reward (`check_risk_reward`)

**Purpose:** Is the trade worth taking after fees?

- Binance taker fee 0.1%/side (0.2% round-trip) → [[Tax & Fees Model]]
- Net RR ≥ 1.5; default TP=2%, SL=1% → RR ≈ 1.8
- **ATR validation:** `tp_atr_ratio = TP% / (ATR/price%)`; <0.8 → -2; <1.0 → -1; ≥2.0 → +1

---

## L7 — News Sentiment (`check_news_sentiment`)

**Purpose:** Block entry when news is overwhelmingly negative.

- Pass if bearish articles < 50% of total
- Skip (pass) if no news data available

---

## L8 — Funding Rate (`check_funding`)

**Purpose:** Is the futures market healthy?

- Funding rate in [-0.05%, +0.05%] → pass
- OI change > -3% (no mass liquidation)
- FR acts as score modifier: range -0.02% to +0.05% → score -3 to +3 pts

---

## L9 — Fear & Greed (`check_fear_greed`)

**Purpose:** Avoid extreme sentiment conditions.

- Index 15–74: pass (not extreme panic/greed)
- Index <15 (extreme fear) or ≥75 (extreme greed) → fail

---

## L10 — Buy Pressure (`check_buy_pressure`)

**Purpose:** Is smart money actively buying?

- Taker buy ratio ≥ 45%
- Net BTC ≥ -500 BTC
- Funding rate modifier: applied as score adjustment

---

## Hard Filters (override total score)

→ Full details: [[Filters & Hard Blocks]]

```python
rsi_block    = l3.get("rsi", 0) > 65           # overbought
adx_block    = 25 <= l1.get("adx", 0) < 40     # danger zone
weekly_block = price < weekly_ema21             # macro bear

all_pass = (total_score >= 70) and not rsi_block and not adx_block and not weekly_block
```

---

## Signal Decision

Entry when `total_score >= 70` AND no hard blocks active.
Most frequent blockers in practice: L1 (ADX), L2 (trend), RSI hard filter, ADX danger zone.

Pattern analysis shows score band WR: visible in bot's Patterns screen via `_virtual_threshold_test()` and `_by_adx_band()`.