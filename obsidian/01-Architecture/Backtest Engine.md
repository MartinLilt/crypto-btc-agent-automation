# Backtest Engine

File: `src/backtest/engine.py`

→ See also: [[Research Feature]] | [[Filters & Hard Blocks]] | [[Tax & Fees Model]] | [[Architecture Overview]]

---

## Two Entry Points

| Function | Purpose |
|----------|---------|
| `run_backtest(symbol, days, tp_pct, sl_pct)` | Single run — user-chosen params |
| `run_backtest_research(symbol)` | Grid search — all 12 combos automatically |

Both share `_run_window_loop()` internally.

---

## Flow (Single Backtest)

1. `_fetch_candles_full(symbol, days)` — paginated Binance fetch (1000/request)
2. `_build_weekly_ema21_index(candles)` — O(n) pre-compute of weekly EMA21 per hourly bar
3. Slide 220-candle window, call `_eval_bar()` at each step
4. On signal: record entry at `open[i+1]` (next candle open, slippage model)
5. Track position: check TP/SL on each subsequent candle's high/low
6. Timeout at 48h → exit at close
7. `_calc_stats()` — aggregate metrics including fees + Lithuanian tax
8. Save run + trades to SQLite via `src/data/db.py`

---

## Entry/Exit Model

```
Entry price = open of candle AFTER signal bar

Each bar after entry:
  if high >= TP_price  → TP_HIT
  if low  <= SL_price  → SL_HIT
  if held > 48h        → TIMEOUT (exit at close)
```

---

## Hard Blocks in `_eval_bar`

```python
adx_val      = l1.get("adx", 0)
rsi_block    = l3.get("rsi", 0) > 65
adx_block    = 25 <= adx_val < 40
weekly_block = not weekly_ema21   # True when price < weekly EMA21

all_pass = (total_score >= ENTRY_SCORE_THRESHOLD) and not rsi_block and not adx_block and not weekly_block
```

→ Details: [[Filters & Hard Blocks]] | [[ADX Danger Zone Analysis]]

---

## Window & Constants

```python
WARMUP_CANDLES = 210       # EMA-200 warmup + buffer
ENTRY_SCORE_THRESHOLD = 70
BINANCE_FEE_PCT = 0.1      # per side (0.2% round-trip)
LT_TAX_RATE = 0.15         # 15% GPM on net annual profit
TIMEOUT_CANDLES = 48       # max hold in hours
```

---

## Research Grid

```python
RESEARCH_TP_SL = [(1.5, 0.75), (2.0, 1.0), (2.5, 1.0), (3.0, 1.5)]
RESEARCH_PERIODS = [90, 180, 365]
```

Candles fetched **once** for 365d, sliced for shorter periods. → [[Research Feature]]

---

## Asset-Specific Thresholds

```python
ASSET_OVERRIDES = {
    "LTCUSDT":  {"adx_min": 15, "vol_min": 10_000_000},
    "SOLUSDT":  {"adx_min": 15, "vol_min": 10_000_000},
    "LINKUSDT": {"adx_min": 15, "vol_min": 10_000_000},
}
```

---

## Stats Dict (from `_calc_stats`)

```python
{
    "total_signals":             int,
    "win_rate_pct":              float,
    "avg_profit_pct":            float,
    "avg_loss_pct":              float,
    "total_pnl_pct":             float,   # gross
    "total_pnl_net_fees_pct":   float,   # after 0.2% fees
    "lt_tax_pct":                float,   # 15% GPM amount
    "total_pnl_after_tax_pct":  float,   # final net
    "breakeven_wr_fees":         float,   # min WR to profit after fees
    "max_drawdown_pct":          float,
    "sharpe_ratio":              float,
    "trades":                    list,
}
```

→ Fee/tax details: [[Tax & Fees Model]]

---

## Trade Dict (stored to SQLite)

```python
{
    "symbol":            str,
    "entry_time":        str,      # ISO timestamp
    "exit_time":         str,
    "entry_price":       float,
    "exit_price":        float,
    "weekday":           int,      # 0=Mon, 6=Sun
    "hour_utc":          int,
    "l1_atr":            float,
    "l1_adx":            float,    # ADX at entry — used for pattern analysis
    "l3_rsi":            float,
    "pnl_pct":           float,    # gross
    "pnl_pct_net_fees":  float,    # after 0.2% fees
    "total_score":       int,      # entry score (0–100)
    "result":            str,      # TP_HIT | SL_HIT | TIMEOUT
    "hold_hours":        float,
    "max_drawdown_pct":  float,
}
```

---

## SQLite Schema (`src/data/db.py`)

- `backtest_runs` — one row per run (symbol, days, win_rate, etc.)
- `backtest_trades` — one row per trade, FK to run; includes `total_score`, `pnl_pct_net_fees`
- Migration: `init_db()` runs `ALTER TABLE ADD COLUMN` on startup (safe, catches duplicates)

---

## Backtest Findings (BTCUSDT, key results)

| Period | TP/SL | Filter | Win Rate | After-tax ($500) |
|--------|-------|--------|----------|-----------------|
| 365d bull (Apr–Oct 2025) | 2%/1% | ADX filter | 47.4% | +$18/yr |
| 180d bear (Oct–Feb 2026) | 2%/1% | ADX filter | 37.5% | -$3 |
| 180d bear | 2%/1% | No filter | 21.4% | -$41 |

→ ADX filter detail: [[ADX Danger Zone Analysis]]