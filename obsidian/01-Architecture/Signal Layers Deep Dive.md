# Signal Layers Deep Dive

All defined in `src/indicators.py`. Each returns `(bool, dict)`.

## L1 — Volatility (`is_market_moving`)
**Purpose:** Is the market actually moving? Filters dead/sideways markets.
- ATR > $500 (absolute floor)
- ATR > 30-period ATR MA × 1.2 (volatility expanding, not contracting)
- Last candle volume > 20-period average (real participation)
- ADX > 25 (not choppy sideways)

**LTC/SOL/LINK:** ADX threshold relaxed to 15

## L2 — Trend (`is_uptrend`)
**Purpose:** Long-only filter. Only trade in structural uptrend.
- Price > EMA50 > EMA200
- EMA50 slope rising (now > 5 candles ago)
- Either: recent Golden Cross (EMA50 crossed EMA200 in last 10 bars)
- Or: established uptrend (EMA50 > EMA200 for last 5 consecutive bars)

**Requires:** 201+ candles

## L3 — Momentum (`is_not_overbought`)
**Purpose:** Enter at a healthy point, not at exhaustion.
- RSI in [40, 65] — not fearful (<40), not overbought (>65)
- MACD histogram > 0 — bullish momentum confirmed

## L4 — Timing (`is_good_hour`)
**Purpose:** Enter during high-volume session windows.
- Good UTC hours: {2, 3, 7, 8, 13, 14, 15, 20}
  - 02-03: Asian open
  - 07-08: London open
  - 13-15: NY overlap (highest volume)
  - 20: NY evening
- Skip weekends (Sat=5, Sun=6) — lower volume, manipulation risk

## L5 — Liquidity (`has_liquidity`)
**Purpose:** Can we enter without getting killed by slippage?
- Spread < $10
- Order book depth ≥ 1 BTC each side within $50
- 24h volume > $500M (BTC/ETH) or $10M (others)

## L6 — Risk/Reward (`check_risk_reward`)
**Purpose:** Is the trade worth taking after fees?
- Binance taker fee: 0.1% per side (0.2% round-trip)
- Net profit (after fees) > 0
- Net RR ratio ≥ 1.5
- Default: TP=2%, SL=1% → RR ≈ 1.8 after fees

## L7 — News Sentiment (`check_news_sentiment`)
**Purpose:** Block entry when news is overwhelmingly negative.
- Pass if bearish articles < 50% of total
- Pass unconditionally if no news data (neutral stance)

## L8 — Funding Rate (`check_funding_rate`)
**Purpose:** Futures market health — are longs overheated?
- Funding rate in [-0.05%, +0.05%]
  - > +0.05%: longs paying too much → potential squeeze down
  - < -0.05%: shorts panicking → unstable
- OI change > -3% (not mass position closing)
- Skipped (pass) for spot-only assets (no futures pair)

## L9 — Fear & Greed (`check_fear_greed`)
**Purpose:** Market sentiment sanity check.
- Source: alternative.me/fng (free)
- Pass zone: 15–74
- Fail: < 15 (extreme fear/panic) or > 74 (extreme greed)
- Skipped (pass) if API unavailable

## L10 — Buy Pressure (`check_buy_pressure`)
**Purpose:** Is smart money buying or selling?
- Taker buy ratio ≥ 45% (sellers not overwhelming)
- Net BTC ≥ -500 BTC (no extreme dump in 24h)
- Skipped (pass) if data unavailable

## Signal Decision
```python
should_enter = all([l1_ok, l2_ok, ..., l10_ok])
```
Zero exceptions — all 10 must pass. In practice, L1 (ADX) and L2 (trend) are the most frequent blockers.