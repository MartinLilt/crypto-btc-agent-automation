# ADX Danger Zone Analysis

Discovery from analysing 318 backtest trades (BTCUSDT, 2024–2025).

→ See also: [[Filters & Hard Blocks]] | [[Signal Layers Deep Dive]] | [[Backtest Engine]]

---

## Key Finding

ADX 25–40 is the **danger zone** — counterintuitive because ADX 25+ is the classic "trend is strong" threshold.

| ADX Band | Description | Win Rate | Action |
|----------|-------------|----------|--------|
| < 20 | Weak trend / sideways | ~54%+ | Allow |
| 20–25 | Borderline | ~54%+ | Allow |
| 25–30 | Moderate (entering danger) | ~33% | **BLOCK** |
| 30–40 | Strong (worst zone!) | ~5% | **BLOCK** |
| 40–60 | Very strong | ~54%+ | Allow |
| 60+ | Extreme momentum | ~54%+ | Allow |

**Why is ADX 30–40 the worst?** The trend is forming but not yet established. Entries here catch the middle of a volatile, directionally uncertain move — price whipsaws around the trend before it commits.

ADX <25 = early/sideways = chop but manageable.
ADX >40 = established trend = momentum carries the trade.
ADX 25–40 = transition zone = worst of both worlds.

---

## Before vs After Filter

| Period | Without ADX Filter | With ADX Filter |
|--------|-------------------|-----------------|
| 180d bear (Oct 2025–Feb 2026) | WR 21.4% / -$41 | WR 37.5% / -$3 |
| 365d bull (Apr–Oct 2025) | WR ~44% | WR 47.4% / +$18 |

---

## Implementation

```python
# src/backtest/engine.py — _eval_bar()
adx_val = l1.get("adx", 0)
adx_block = 25 <= adx_val < 40

# src/signals/indicators.py — check_entry_signal()
adx_val = l1.get("adx", 0)
if 25 <= adx_val < 40:
    hard_blocks.append(f"ADX {adx_val:.1f} in danger zone 25–40 (backtest WR 5–33%)")
```

---

## Pattern Analyzer

`src/signals/pattern_analyzer.py` — `_by_adx_band(trades)` breaks down WR by all 5 ADX bands, visible in the Patterns screen.