# Research Feature

Automatic grid search over all TP/SL × period combinations. No user input needed after asset selection.

→ See also: [[Backtest Engine]] | [[Tax & Fees Model]] | [[Architecture Overview]]

---

## Purpose

**Simulator** = user manually picks period, budget, TP/SL → sees one result.
**Research** = fully automatic → tests 12 combinations → shows best combo + budget projection table.

---

## Grid

```python
RESEARCH_TP_SL = [
    (1.5, 0.75),   # conservative, tight SL
    (2.0, 1.0),    # default, RR 2:1
    (2.5, 1.0),    # wider TP, same SL
    (3.0, 1.5),    # aggressive
]
RESEARCH_PERIODS = [90, 180, 365]  # days
```

Total: **4 × 3 = 12 combinations** per asset.

---

## Efficiency

Candles fetched **once** for 365d (max period), then sliced for 180d / 90d:
```python
candles_full = _fetch_candles_full(symbol, 365)
# For 90d:
needed = 90 * 24 + WARMUP_CANDLES
candles_90 = candles_full[-needed:]
```
→ 1 Binance API call instead of 12.

---

## Ranking

Results sorted by **Sharpe ratio** (risk-adjusted return). Also separately identifies:
- **Best by net %** — max `total_pnl_after_tax_pct`
- **Best by Sharpe** — most consistent risk-adjusted return

Budget does not affect ranking (% metrics are budget-independent).

---

## Output Format

```
🏆 Top-5 by Sharpe:
🥇 TP 2% / SL 1% — 365d
   ✅ WR 47.4%  |  net +8.3%  |  Sharpe 1.2  |  19 signals
...

💡 Best combinations:
  Max profit:   TP 2% / SL 1% (365d)  →  +8.3% net
  Most stable:  TP 1.5% / SL 0.75% (180d)  →  Sharpe 1.4

💰 Budget projection (best combo):
  $  100 / trade  →  +$83 / year
  $  250 / trade  →  +$207 / year
  $  500 / trade  →  +$415 / year
  $1,000 / trade  →  +$830 / year
  $2,500 / trade  →  +$2,075 / year
  $5,000 / trade  →  +$4,150 / year
```

Budget projection formula: `net_pct / 100 × budget × 365 / days`

---

## Code Locations

| Function | File |
|----------|------|
| `run_backtest_research(symbol)` | `src/backtest/engine.py` |
| `research_asset_chosen` handler | `main.py` |
| `_format_research_msg(results, symbol, lang)` | `main.py` |
| `_project_budget(net_pct, days, budget)` | `main.py` |
| Strings | `src/bot/strings.py` → `research_*` |

---

## Telegram Flow

```
Main Menu → 🧪 Research
    → asset picker (BTCUSDT / ETHUSDT / LTCUSDT / SOLUSDT / LINKUSDT)
    → [auto] run_backtest_research(symbol) in executor thread
    → show results (plain Markdown, not V2)
```

Plain Markdown used (not MarkdownV2) because MarkdownV2 chokes on float values like `1.5`, `2.5`, `0.75` unless every `.` is escaped.