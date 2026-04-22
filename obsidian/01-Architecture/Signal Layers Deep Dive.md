# Signal Layers Deep Dive

All defined in `src/signals/indicators.py`. Each returns `(score: int, dict)`.
Entry threshold: `total_score >= 70` (out of 100).

## L1 — Volatility (`is_market_moving`)
**Purpose:** Is the market actually moving? Filters dead/sideways markets.
- ATR > $500 (absolute floor)
- ATR > 30-period ATR MA × 1.2 (volatility expanding)
- Last candle volume > 20-period average (real participation)
- ADX > 25 (not choppy sideways)
- **ADX slope bonus:** if ADX rising by >1 pt over last 5 candles → +2

## L2 — Trend (`is_uptrend`)
**Purpose:** Long-only filter. Only trade in structural uptrend.
- Price > EMA50 > EMA200, EMA50 slope rising
- Golden cross or established uptrend (EMA50 > EMA200 for 5 bars)
- **4h MTF bonus:** 4h EMA50 > EMA200 → +2; mixed → +1; misaligned → -2
- **24h VWAP:** price above VWAP → +1; below → -1

**Requires:** 201+ candles

## L3 — Momentum (`is_not_overbought`)
**Purpose:** Enter at a healthy point, not at exhaustion.
- RSI in [40, 65]; MACD histogram > 0
- **4h RSI cross-timeframe:** both TFs in 40-65 → +2; 4h overbought → -2
- **RSI divergence:** price new high + RSI lower → -2; reverse → +2
- **Hard filter:** RSI > 65 blocks entry entirely (overbought = high reversal risk)

## L4 — Volume Trend (`is_volume_trending`)
**Purpose:** Confirm market participation is increasing.
- ratio = last-4h volume ÷ 24h-avg 4h bucket
- Scoring: ≥1.5→10, ≥1.2→8, ≥0.8→6, ≥0.5→3, else 1

## L5 — Liquidity (`has_liquidity`)
**Purpose:** Can we enter without getting killed by slippage?
- Spread < $10; order book depth ≥ 1 BTC; 24h volume > $500M
- **Bid/Ask imbalance:** bid/ask ≥ 3.0 → +2; ≥1.5 → +1; ≤0.33 → -2; ≤0.67 → -1

## L6 — Risk/Reward (`check_risk_reward`)
**Purpose:** Is the trade worth taking after fees?
- Binance taker fee 0.1%/side; net RR ≥ 1.5; default TP=2%, SL=1% → RR ≈ 1.8
- **ATR validation:** `tp_atr_ratio = TP% / (ATR/price%)`; <0.8 → -2; <1.0 → -1; ≥2.0 → +1

## L7 — News Sentiment (`check_news_sentiment`)
**Purpose:** Block entry when news is overwhelmingly negative.
- Pass if bearish articles < 50% of total; skip if no data

## L8 — S/R Proximity (`check_sr_proximity`)
**Purpose:** Is there a resistance level blocking the TP path?
- Swing high detection + level clustering; scores clear path → 10, each blocker penalises
- 1 blocker: gap ≥1.5%→7, ≥1.0%→5, ≥0.5%→3; 2 blockers→2-4; 3+→1

## L9 — Candle Pattern (`detect_candle_patterns`)
**Purpose:** What is the last 3-candle structure telling us?
- STRONG_BULL(10), BULLISH_ENGULFING(9), HAMMER/MORNING_STAR(8), BULLISH(6), DOJI/NEUTRAL(5), BEARISH(3), SHOOTING_STAR(2), BEARISH_ENGULFING(1)
- **4h cross-timeframe:** `combined = (score_1h + score_4h × 2) / 3`
- **Bull streak penalty:** 5+ consecutive green candles → -1; 8+ → -2

## L10 — Buy Pressure + Funding Rate (`check_buy_pressure`)
**Purpose:** Is smart money buying? Is futures market healthy?
- Taker buy ratio ≥ 45%; net BTC ≥ -500 BTC
- **Funding rate modifier:** FR -0.02% to +0.05% → score -3 to +3

## Hard Filters (override total score)
```python
# RSI > 65 — overbought, high reversal risk
# Daily price < EMA50 — bearish daily trend (live mode only)
should_enter = (total_score >= 70) and not hard_blocks
```

## Signal Decision
Entry when `total_score >= 70` AND no hard blocks active.
Most frequent blockers in practice: L1 (ADX), L2 (trend), RSI hard filter.