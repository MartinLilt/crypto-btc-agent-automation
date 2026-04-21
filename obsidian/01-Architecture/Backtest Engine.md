# Backtest Engine

File: `src/backtest_engine.py`

## Flow
1. `_fetch_candles_full(symbol, days)` — paginated Binance fetch (1000/request)
2. `_fetch_fear_greed_history()` — Redis-cached, daily F&G values
3. `_fetch_funding_history(symbol)` — Redis-cached, 8h funding rate snapshots
4. Slide 220-candle window over history, call `_eval_bar()` at each step
5. On signal: record entry at `open[i+1]` (next candle open)
6. Track position: check TP/SL on each subsequent candle's high/low
7. Timeout at 48h → exit at close
8. Save run + trades to SQLite via `src/db.py`

## Entry/Exit Model
```
Entry price = open of candle AFTER signal bar
TP = entry × (1 + tp_pct/100)
SL = entry × (1 - sl_pct/100)

Each bar after entry:
  if high >= TP → TP_HIT
  if low <= SL  → SL_HIT
  if held > 48h → TIMEOUT (exit at close)
```

## Window Size
- `WARMUP_CANDLES = 210` — needed for EMA-200 (200 + buffer)
- Actual sliding window = 220 candles
- Window `[-WIN-1:]` passed to `_eval_bar`

## Asset-Specific Thresholds in Backtest
```python
ASSET_OVERRIDES = {
    "LTCUSDT": {"adx_min": 15, "vol_min": 10_000_000},
    "SOLUSDT": {"adx_min": 15, "vol_min": 10_000_000},
    "LINKUSDT": {"adx_min": 15, "vol_min": 10_000_000},
}
```

## Output (result dict)
```python
{
  "total_signals": int,
  "trades": [...],          # list of trade dicts
  "win_rate_pct": str,
  "avg_profit_pct": float,
  "avg_loss_pct": float,
  "total_pnl_pct": float,
  "max_drawdown_pct": str,
  "sharpe_ratio": str,
}
```

## Trade Dict
```python
{
  "symbol": str,
  "entry_time": str,    # ISO timestamp
  "exit_time": str,
  "entry_price": float,
  "exit_price": float,
  "pnl_pct": float,
  "result": "TP_HIT" | "SL_HIT" | "TIMEOUT",
}
```

## SQLite Schema (src/db.py)
- `backtest_runs` — one row per run (symbol, days, win_rate, etc.)
- `backtest_trades` — one row per trade, FK to run
- `fear_greed_cache` — date → value cache
- `funding_cache` — symbol + timestamp → rate cache