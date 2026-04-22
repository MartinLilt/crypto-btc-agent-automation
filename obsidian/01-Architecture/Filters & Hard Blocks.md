# Filters & Hard Blocks

Hard blocks prevent entry regardless of total score. Applied in both live (`indicators.py`) and backtest (`engine.py → _eval_bar`).

→ See also: [[Signal Layers Deep Dive]] | [[Backtest Engine]] | [[ADX Danger Zone Analysis]]

---

## Active Hard Blocks

### 1. RSI > 65 — Overbought Filter
- **Where:** `check_entry_signal()` + `_eval_bar()`
- **Logic:** `if rsi > 65 → block`
- **Why:** Analysis of 318 backtest trades — avg RSI on losing trades was 71.9. High RSI = late entry into overextended move.
- **Message:** "RSI {val} — overbought, high reversal risk"

### 2. ADX Danger Zone (25–40)
- **Where:** `check_entry_signal()` + `_eval_bar()`
- **Logic:** `if 25 <= adx < 40 → block`
- **Why:** Data-driven from 318 trades. ADX 25–40 = WR 5–33% (trend developing but unstable). ADX <25 or >40 = WR 54%+.
- **Message:** "ADX {val} in danger zone 25–40 (backtest WR 5–33% — trend developing but unstable)"
- **Effect:** Bear period Oct 2025–Feb 2026: loss reduced from -$41 → -$3 (93% reduction)
- **Details:** [[ADX Danger Zone Analysis]]

### 3. Weekly EMA21 Macro Bear Filter
- **Where:** `_eval_bar()` via `weekly_ema21` param; `check_entry_signal()` via `candles_1w`
- **Logic:** `if price < weekly_ema21 → block`
- **Why:** Macro bear regime filter. Entering longs when price is below weekly EMA21 = trading against the macro trend.
- **Pre-computation:** `_build_weekly_ema21_index(candles)` — O(n) pass before the main loop, one weekly EMA21 value per hourly bar
- **Live mode:** `candles_1w` fetched in `main.py` and `monitor.py`

---

## Score-Based Threshold

Entry fires only when **all** conditions are met:
```python
all_pass = (total_score >= ENTRY_SCORE_THRESHOLD) and not rsi_block and not adx_block and not weekly_block
```

`ENTRY_SCORE_THRESHOLD = 70` (out of 100).

**Threshold paradox:** Raising to 75 made WR *worse* (12.5%). High scores correlate with overheated markets — everyone already bought. 70 is the sweet spot.

---

## Block Priority

| Priority | Block | Scope |
|----------|-------|-------|
| 1st | Weekly EMA21 (macro bear) | Backtest + Live |
| 2nd | ADX danger zone 25–40 | Backtest + Live |
| 3rd | RSI > 65 (overbought) | Backtest + Live |
| 4th | Score < 70 | Backtest + Live |

---

## Planned / Considered

- [ ] Volume profile node proximity filter (avoid entries inside HVN clusters)
- [ ] Time-of-day filter based on pattern DB data (L4 only allows 8 specific UTC hours)