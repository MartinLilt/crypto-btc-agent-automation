# Development Log

Reverse-chronological. Add entry at top when significant changes land.

---

## 2026-04-27 — 4h timeframe wired into backtest (was live-only)

**Summary:** Found that `is_uptrend`, `is_not_overbought`, `detect_candle_patterns` accept `candles_4h` for multi-timeframe confirmation, but the backtest engine was calling them WITHOUT 4h candles. Live mode was using 4h, backtest was not — so all prior backtest numbers underrepresented the bot's actual behavior. Wiring this up was the highest-leverage improvement of the session.

### Effect on backtest (720d, TP=3%/SL=1.5%, after-tax)

| Asset | Before (no 4h) | After (with 4h) | Δ |
|-------|----------------|-----------------|---|
| BTC | +18.89% (69 sigs) | **+21.08%** (74 sigs) | +2.19pp |
| SOL | +51.17% (134 sigs) | **+67.49%** (173 sigs) | +16.32pp |
| ETH | +8.32% (90 sigs) | **+28.86%** (117 sigs) | +20.54pp |

More signals AND better quality — 4h adds orthogonal information (timeframe alignment) rather than restricting on existing features. When 4h is in uptrend, mediocre 1h signals get +2 score and become viable; when 4h is downtrend, weak setups get -2 and die.

### Walk-forward validation (4h-wired)

| Asset | In-sample | OOS | Verdict |
|-------|-----------|-----|---------|
| BTC | +11.14% | +9.95% | ✓ Stable |
| SOL | +20.14% | +47.34% | ✓ OOS stronger |
| ETH | -0.65% | +29.41% | ✓ Adapts to regime |

Unlike gap_pct and score-threshold experiments earlier in the session — which both failed walk-forward — the 4h fix survives OOS validation. This is real edge, not curve-fitting.

### Implementation

- **`src/backtest/engine.py`**:
  - `_fetch_candles_full()`: now correctly computes `needed` for 4h interval (`days * 6 + warmup` vs previous `days * 24 + warmup`)
  - `_eval_bar()`: accepts `candles_4h` param, passes to `is_uptrend`, `is_not_overbought`, `detect_candle_patterns`
  - `_slice_4h_at()` (new): for each 1h bar, returns the 4h candles available at that timestamp (matches what live receives from Binance)
  - `_run_window_loop()`: fetches 4h history once per backtest, slices per bar
  - `run_backtest`, `run_backtest_research`: fetch 4h candles before calling the loop
- **`scripts/paper_log.py`**: fetches 4h candles (60d window) and passes via `_slice_4h_at()` to the evaluator

### Why this was missed initially

Multi-timeframe code had been added to the layer functions for live mode but `_eval_bar` in the backtest engine never called it with `candles_4h`. Live and backtest silently diverged. CLAUDE.md numbers are now updated to reflect the 4h-wired baseline.

---

## 2026-04-27 — Paper trading mode (autonomous logger)

**Summary:** Added autonomous paper-trading infrastructure: signals are evaluated and tracked without manual interaction. Designed to run hourly via cron over 30-60 days for out-of-sample validation.

### Changes

- **`src/data/db.py`**: New `paper_trades` table with TP/SL prices, layer snapshot JSON, and notification flags. CRUD helpers: `open_paper_trade`, `get_open_paper_trades`, `has_open_paper_trade`, `close_paper_trade`, `mark_paper_notified`, `get_paper_trades`.
- **`scripts/paper_log.py`** (new): One-shot script that
  1. Fetches latest 14d of candles per asset
  2. Updates open trades — scans for TP/SL/timeout (48h), conservative (SL before TP)
  3. Evaluates new signals on the second-to-last candle, opens trade at next candle's open price
  4. Sends Telegram notifications on open/close (optional, requires `PAPER_NOTIFY_CHAT_ID` env var)
- **`scripts/paper_report.py`** (new): Report tool with `--symbol`, `--days` filters; compares live WR vs backtest baseline.

### Operating procedure

```bash
# Hourly cron entry:
0 * * * * cd /path/to/repo && .venv/bin/python -m scripts.paper_log >> /var/log/paper.log 2>&1

# Manual report:
python -m scripts.paper_report
python -m scripts.paper_report --days 30 --symbol SOLUSDT
```

### Backtest baseline (for comparison after 30 days)

| Asset | Expected WR | Net% per signal | Sigs/30d (estimated) |
|-------|-------------|-----------------|----------------------|
| BTC | 39.1% | +0.32% | 2.9 |
| ETH | 38.9% | +0.11% | 3.8 |
| SOL | 47.8% | +0.45% | 5.6 |

**Δ vs Backtest** column in the report shows live deviation in percentage points. ±5pp is statistical noise for the first 30 days; ±15pp+ would indicate genuine slippage/fee/regime drift.

---

## 2026-04-27 — Research session: cooldown experiment (reverted) + ETH/SOL walk-forward

**Summary:** Investigated Q1 2024 BTC losing quarter (−2.77%). Tested 6h signal cooldown hypothesis on BTC/ETH/SOL — negative result, change reverted. Discovered SOL is the strongest performer (4/4 profitable quarters). Found `l2_gap_pct` data integrity issue.

### Findings (no code changes shipped)

- **Cooldown 6h ≠ improvement.** BTC: −4.6% net. SOL: −45.6% net (catastrophic). ETH: +11.6% net (only winner). Universal cooldown destroys trend-continuation edge. See [[Roadmap & Ideas]] research section for the full table.
- **SOL >> BTC > ETH.** Over 720d: BTC +22.22%, ETH +9.79%, **SOL +60.20%**. SOL profitable in 4/4 quarters incl. Q1 2024 where BTC/ETH lost money.
- **Q1 2024 BTC losers had no distinguishing metric.** RSI ≈ winners (60.6 vs 60.8), score ≈ winners (73.1 vs 73.8), buy_ratio ≈ winners. ADX bimodal (either <25 or >40, not in danger zone) for both groups.
- **Bug found: `l2_gap_pct = 0.00` for all 18 Q1 trades.** Metric not being propagated from `is_uptrend()` snapshot to the trade row in `_run_window_loop()`. Logged as backlog issue.

### Realistic expectations updated

- BTC annual net (post-fees, post-LT-tax): ~10–15%
- SOL annual net: ~30%+
- Worst-case quarter: −3% (BTC) to −7% (ETH)
- Timeouts (13% of trades) contribute slightly positive — not a P&L leak

### Open questions

- Extend bot to SOL/ETH? CLAUDE.md says BTCUSDT only, but data suggests SOL is the better target
- Q1→Q4 monotonic improvement on BTC: real edge or in-sample overfit? Needs 90d out-of-sample validation
- Smart cooldown: block only consecutive SL_HIT (not TP_HIT) within N hours?

---

## 2026-04-27 — Removed weekly EMA21 macro-bear filter

**Summary:** Backtest evidence showed the weekly EMA21 hard-block was net negative across all tested periods. Removed the filter entirely along with its supporting code.

### Why

Live diagnostic showed BTC blocked from any signals (price=$76,498, EMA50=$76,877 — death cross, L2=1/10, ADX=29.6 in danger-zone hard-block). Investigation revealed weekly EMA21 hard-filter was contributing.

Backtest comparison on 720 days of BTC/USDT 1h candles (2024-05 → 2026-04):

| Period | TP/SL | Hard-block (old) | No-filter | Soft-2 penalty |
|--------|-------|------------------|-----------|----------------|
| 180d | 3.0/1.5 | **0** sigs | 7 sigs, **+10.64%** WR=57% | 3 sigs, +2.56% |
| 365d | 3.0/1.5 | 18 sigs, +7.5% | 26 sigs, **+19.64%** | 21 sigs, +10.05% |
| 720d | 3.0/1.5 | 57 sigs, +16.87% | 69 sigs, **+22.22%** | 62 sigs, +16.03% |

Hard-block killed entire 6-month windows of profitable trades. Soft-2 penalty (deducts 2 from total_score if price < weekly EMA21) was also worse than no-filter on every metric — it drops *winning* signals along with losers since WR was barely affected. ADX hard-block (25-40) and the score threshold already provide adequate downtrend protection.

### Changes

- **`src/backtest/engine.py`**:
  - Removed `_build_weekly_ema21_index()` function (~40 lines)
  - Removed `weekly_ema21` parameter from `_eval_bar()`
  - Removed `weekly_ema21_index` parameter from `_run_window_loop()`
  - Removed `_build_weekly_ema21_index()` calls in `run_backtest()` and `run_backtest_research()`
  - Removed weekly_block hard filter from entry logic

### Final research grid (all periods, no weekly filter)

Best by Sharpe: TP=3.0% / SL=1.5% over 90d → 5 signals, WR=80%, Net=+12.66%
Best by Net P&L: TP=3.0% / SL=1.5% over 365d → 26 signals, WR=46.2%, Net=+19.64%

### Notes

- Live-analysis path (main.py) was not affected — weekly EMA21 was backtest-only
- LT 15% capital-gains tax accounting unchanged
- Current bar still legitimately blocked by L2 trend score + ADX danger zone — that's correct behavior, downtrend
- Dataset spans 720 days (May 2024 - Apr 2026); no 2022-style deep bear market in the sample. If a longer dataset becomes available, may want to re-test whether weekly filter helps in extreme drawdowns

---

## 2026-04-24 — Obsidian vault auto-sync setup

**Summary:** Configured Obsidian vault to be automatically read by Claude and improved hub structure.

### Changes

- **`.claudeinclude`** (new) - Auto-loads vault files into Claude context:
  - `obsidian/00-Index/README.md`
  - `obsidian/01-Architecture/*.md`
  - `obsidian/02-DevLog/Dev Log.md`
  - `obsidian/03-Roadmap/Roadmap & Ideas.md`

- **`.claude/settings.json`** - Added hooks:
  - `PreToolUse` (Edit|Write) - logs file changes to temp file
  - `Stop` - reminds to update DevLog if changes were made

- **`CLAUDE.md`** - Updated Obsidian section with vault structure and update guidelines

- **`obsidian/00-Index/README.md`** - Rebuilt as main hub:
  - Quick links by topic (Signal System, Core Systems, Business Logic, History)
  - Key numbers table
  - File map
  - Update guidelines

### Result

Claude now auto-reads vault on session start. Changes tracked via hooks.

---

## 2026-04-22 — Research redesigned: fully automatic, no budget picker

**Summary:** Research flow changed from "asset + budget → 12 combos" to fully automatic "asset → 12 combos → best combo + budget projection table for all 6 budgets."

### Changes (`src/backtest/engine.py`, `main.py`)

- `run_backtest_research(symbol)` — removed `budget` param entirely; returns pure % metrics
- `_format_research_msg(results, symbol, lang)` — removed `budget` param; now shows:
  - Top-5 by Sharpe with `net %` column (not $/yr)
  - Best combo by max `total_pnl_after_tax_pct`
  - Budget projection table: `$100 / $250 / $500 / $1000 / $2500 / $5000` → `$/year`
- `research_asset_chosen` handler: no longer reads `bt_budget` from user_data
- `_project_budget(net_pct, days, budget)` helper: annualised $ return for any budget

### Rationale

Budget is a linear multiplier on % returns — it doesn't change WR or Sharpe ranking. Asking the user for a budget in Research was unnecessary friction. Research now shows all budgets in one projection table.

→ [[Research Feature]]

---

## 2026-04-22 — Simulator overhaul: fees/tax, TP picker, ADX filter, Research mode

**Summary:** Major simulator upgrade — correct Lithuanian tax calculation, TP/SL selection, ADX danger zone filter (data-driven), weekly EMA21 macro filter, local Ollama LLM replacing OpenAI, and new Research grid-search feature.

### Lithuanian tax fix (`src/backtest/engine.py`)
- Tax was incorrectly applied per winning trade; now applied on **net annual profit** (losses offset gains)
- Formula: `lt_tax = max(0, total_pnl_net_fees) * 0.15`
- Added `BINANCE_FEE_PCT = 0.1` (0.2% round-trip) and `LT_TAX_RATE = 0.15` constants
- `_simulate_trade` returns `pnl_pct_net_fees` (after fees); no per-trade tax
- `_calc_stats` shows: `total_pnl_net_fees_pct`, `total_pnl_after_tax_pct`, `lt_tax_pct`, `breakeven_wr_fees`

### Weekly EMA21 macro filter
- `_build_weekly_ema21_index(candles)` — O(n) pre-computation of weekly EMA21 per hourly bar
- Hard block in `_eval_bar`: price < weekly EMA21 → skip entry (macro bear regime)
- Same filter added to `check_entry_signal()` in `indicators.py` (live trading)
- Added `candles_1w` fetch in `monitor.py` and `main.py` (live analysis)
- Result: 365d Apr–Oct 2025 improved from 44% WR / +$15 → validated

### ADX danger zone filter (data-driven, 318 trades analysed)
- Pattern analysis revealed: ADX 25–40 = WR 5–33% vs ADX <25 or >40 = WR 54%+
- Hard block: `25 <= adx < 40` in both `_eval_bar` (backtest) and `check_entry_signal` (live)
- Message shown: "ADX X in danger zone 25–40 (backtest WR 5–33%)"
- Effect on bad period (Oct 2025–Feb 2026): -$41 → -$3 loss (93% reduction)

### Simulator TP/SL picker (`main.py`, `src/bot/strings.py`)
- New flow: Asset → Period → Budget → **TP picker** → Run
- TP options: 1% / 1.5% / 2% / 3% / 5%; SL auto = TP ÷ 2 (RR 2:1)
- Callback chain: `bt_period_` → `bt_budget_` → `bt_tp_` → run
- Result header now shows: `$250  TP 2.0% / SL 1%`
- Removed `{be_tax}` from template (tax now aggregate, not per-trade break-even)

### Research feature (new) (`src/backtest/engine.py`, `main.py`)
- New main menu button: 🧪 Research
- `run_backtest_research(symbol, budget)` — grid search: 4 TP/SL pairs × 3 periods = 12 runs
- Fetches candles **once** for 365d, slices for 180d/90d — no repeated Binance calls
- `_run_window_loop()` extracted as shared helper (used by both `run_backtest` and research)
- Results ranked by Sharpe ratio; shows top-5 + recommendation (best income / most stable)
- TP/SL pairs: (1.5/0.75), (2.0/1.0), (2.5/1.0), (3.0/1.5)

### Pattern analyzer enhancements (`src/signals/pattern_analyzer.py`)
- Added `_by_adx_band()` — WR by ADX zone (5 bands)
- Added `_by_score_band()` — WR by entry score threshold
- Added `_virtual_threshold_test()` — simulates WR at score ≥ 55/60/65/70/75/80
- Added RSI bands and ADX bands to `format_patterns_message()`
- `total_score` and `pnl_pct_net_fees` now saved to `backtest_trades` table

### DB schema migration (`src/data/db.py`)
- `init_db()` now runs ALTER TABLE migration for `total_score` and `pnl_pct_net_fees` columns
- `save_backtest_trades` cols updated to include both new fields

### Local Ollama LLM (`src/ai/orchestrator.py`)
- Replaced OpenAI API with local Ollama (model: `qwen2.5:3b` in Docker, `llama3.2:latest` locally)
- `OLLAMA_HOST` env var (default `http://localhost:11434`)
- `ai_review()` — structured JSON verdict for live analysis
- `ai_review_simulation()` — plain text paragraph for backtest results
- Removed `translate_to_russian()` function
- `docker-compose.yml`: added `ollama` service, auto-pulls model on start

### Backtest findings (key results)
| Period | Filter | WR | After-tax $500 |
|--------|--------|----|----------------|
| 365d bull (Apr–Oct 2025) | ADX filter | 47.4% | +$18/yr |
| 180d bear (Oct 2025–Feb 2026) | ADX filter | 37.5% | -$3 |
| 180d bear | No filter | 21.4% | -$41 |

---

## 2026-04-22 — BTC signal quality overhaul: 9 enhancements + profitable backtest validation

**Summary:** 9 BTC-specific signal improvements added, plus 2 hard-filter blockers. Backtest validated: 50% WR, +7% PnL over 90 days. System now profitable at RR 2:1.

### Signal enhancements

#### L1 — ADX Slope bonus (`src/signals/indicators.py`)
- Compare `adx_now` vs `adx[candles[:-5]]`; if rising by >1.0 pt → +2 score bonus
- Filters entries where ADX is high but already topping out

#### L2 — 24h VWAP confirmation (`src/signals/indicators.py`)
- `vwap = Σ(typical_price × volume) / Σ(volume)` over last 24 candles
- Price above VWAP → +1; below → -1
- Added to `is_uptrend()` details: `vwap`, `vwap_above`

#### L3 — 4h RSI cross-timeframe + RSI divergence (`src/signals/indicators.py`)
- 4h RSI: if 1h and 4h both in 40-65 → +2 bonus; if 4h overbought → -2
- `_rsi_divergence(candles, lookback=10)`: price new high but RSI lower → -2 (bearish div); reverse → +2
- Both applied in `is_not_overbought()`

#### L5 — Bid/Ask imbalance bonus (`src/signals/indicators.py`)
- `imbalance = bid_depth / ask_depth`
- ≥3.0 → +2; ≥1.5 → +1; ≤0.33 → -2; ≤0.67 → -1
- Applied in `has_liquidity()`

#### L6 — ATR-adaptive TP/SL validation (`src/signals/indicators.py`)
- `tp_atr_ratio = take_profit_pct / (atr / price * 100)`
- <0.8 → -2 (TP too tight vs volatility); <1.0 → -1; ≥2.0 → +1
- `check_risk_reward()` now takes `atr` and `price` params

#### L8 — Funding Rate as scorer in L10 (`src/signals/indicators.py`)
- FR moved from blocker to modifier: -0.02% to +0.05% range → -3 to +3 pts on L10
- `check_buy_pressure(pressure_data, funding_data=None)` extended

#### L9 — 4h candle pattern cross-timeframe + bull streak counter (`src/signals/candle_patterns.py`)
- `detect_candle_patterns(candles, candles_4h=None)`: combined score = `(score_1h + score_4h×2) / 3`
- Bull streak penalty: 5+ consecutive green candles → -1; 8+ → -2
- Returns: `bull_streak`, `streak_penalty`, `tf4h_score`, `tf4h_pattern`

### Hard-filter blockers

#### RSI > 65 hard filter
- Added to `check_entry_signal()` and `_eval_bar()` in backtest engine
- Blocks entry regardless of total score
- Root cause: avg RSI on losing trades was 71.9 — overbought entries

#### Daily trend filter (live mode only)
- Price < daily EMA50 → blocks entry with message
- Not applied in backtest (no daily candles available in window)

### Backtest results (BTCUSDT, threshold 70)
| Period | Signals | Win Rate | PnL |
|--------|---------|----------|-----|
| 60d    | 11      | 45.5%    | +4% |
| 90d    | 14      | 50.0%    | +7% |

Break-even for RR 2:1 = 33.3% → system now comfortably profitable ✅

### Key finding: threshold 75 paradox
Raising entry threshold from 70→75 made results **worse** (12.5% WR). High scores correlate with overheated market conditions (everyone already bought). Reverted to 70; RSI timing filter is the real fix.

### `src/backtest/engine.py`
- Added `is_volume_trending` import; L4 now uses real volume trend instead of hardcoded 5
- `check_risk_reward()` called with `atr` + `price` from L1 data
- `rsi_block = l3.get("rsi", 0) > 65` added to `_eval_bar`

### `main.py`
- `score_icon` threshold corrected: `>= 70` (was `>= 75` — inconsistency with ENTRY_SCORE_THRESHOLD)
- Display: adx_note (↑↓ slope), l2_vwap_note, l3_div_note (⚡⚠ divergence), l5_imb_note, l6_atr_note, l9_extra (4h pattern + streak)
- Hard blocks shown under WAIT signal: `🚫 _Hard filter: ..._`
- Added `candles_1d` fetch; passed to `check_entry_signal`

### `src/trading/monitor.py`
- Re-added `get_funding_rate` fetch; `funding_data` passed to `check_entry_signal`
- Added `candles_4h`, `candles_1d` fetches
- Smart exit function names fixed: `calculate_rsi`/`calculate_macd` (not private `_rsi`/`_macd`)

---

## 2026-04-22 — Serious-level analytics upgrade (L8/L9 replaced, MTF, smart exits)

**Summary:** Major pipeline upgrade — replaced spot-irrelevant layers with technically meaningful ones, added multi-timeframe trend confirmation, improved volume spike detection, and added smart exits.

### L8 — Funding Rate → S/R Proximity (`src/signals/support_resistance.py`, NEW)
- New file: fractal swing high detection + level clustering + resistance scoring
- `check_sr_proximity(candles, tp_pct=2.0)` — scores 0-10: clear path→10, 1 blocker by gap (≥1.5%→7, ≥1.0%→5, ≥0.5%→3), 2 blockers→2-4, 3+→1
- Returns: score, pass, price, tp_price, swing_highs, blocking_levels, nearest_resistance, n_blockers

### L9 — Fear & Greed → Candle Pattern (`src/signals/candle_patterns.py`, NEW)
- New file: checks last 3 candles for bullish/bearish patterns
- `detect_candle_patterns(candles)` — scoring: STRONG_BULL(10), BULLISH_ENGULFING(9), HAMMER/MORNING_STAR(8), BULLISH(6), DOJI/NEUTRAL_BULL(5), BEARISH(3), SHOOTING_STAR(2), BEARISH_ENGULFING(1)
- Returns: score, pass, pattern name, description, c_open/close/high/low, body_pct

### L2 — Multi-timeframe trend confirmation (`src/signals/indicators.py`)
- `is_uptrend(candles, candles_4h=None)` — optional 4h EMA50/EMA200 alignment
- 4h fully aligned (price > EMA50 > EMA200): +2 bonus; mixed: +1; misaligned: -2
- Details dict includes: tf4h_bonus, tf4h_aligned, tf4h_ema50, tf4h_ema200

### L4 — Volume spike uses 20-period SMA (`src/signals/indicators.py`)
- Replaced 4h vs 24h avg with: recent 3-candle avg vs SMA(20) excluding last 3
- More responsive to actual spike conditions vs the rolling 24h bucket

### L10 — Buy pressure lookback 24h → 6h
- `get_taker_buy_pressure(SYMBOL, hours=6)` — more reactive to current order flow

### `src/backtest/engine.py`
- Removed F&G/funding pre-fetches (Redis-cached external data no longer needed for L8/L9)
- L8: `check_sr_proximity(candles_window, tp_pct=tp_pct)` — pure candle computation
- L9: `detect_candle_patterns(candles_window)` — pure candle computation
- `_eval_bar` signature simplified (removed `fg_history`, `funding_history` params)

### `src/ai/orchestrator.py`
- L8 description: S/R blocking levels and nearest resistance
- L9 description: pattern name + body percentage
- SYSTEM_PROMPT points 8/9 updated to match new layers

### `src/bot/strings.py`
- `layer_funding` → `layer_sr_proximity` ("S/R Level")
- `layer_fear_greed` → `layer_candle_pattern` ("Candle")
- Added short versions: `layer_sr_proximity_short`, `layer_candle_pattern_short`

### `src/trading/monitor.py`
- Added 4h candles fetch; passes `candles_4h=candles_4h` to `check_entry_signal`
- Pressure: `hours=6` (was 24)
- Removed `get_funding_rate` / `get_fear_greed_index` imports
- **Smart exits** in `watcher_loop`: while in profit, RSI > 75 → SMART_EXIT_RSI; MACD bearish cross → SMART_EXIT_MACD; both close position immediately and notify

---

## 2026-04-22 — Trading module + L4 Volume Trend

**Changes:**

### L4 — replaced Timing with Volume Trend
- `_score_l4_vol_trend(ratio)` replaces `_score_l4(hour, weekday_ok)`
- `is_volume_trending(candles)` replaces `is_good_hour()`
- Scoring: ratio ≥1.5→10, ≥1.2→8, ≥0.8→6, ≥0.5→3, else 1
  (ratio = last-4h volume ÷ 24h-avg 4h bucket)
- Layer key renamed `L4_timing` → `L4_vol_trend` everywhere
- Updated `main.py`, `orchestrator.py`, `strings.py` for new key/label

### `src/trading/` module (new)
- **`modes.py`** — `TradingMode` enum (SIMULATION/LIVE), all constants
- **`position.py`** — SQLite-backed position tracker; `new_position()`, `check_and_update()`, `close_position()`, `get_position()`; trailing stop logic (break-even at +1%, trail at +1.5%)
- **`executor.py`** — `execute_buy()` / `execute_sell()` for both SIM (virtual fill) and LIVE (real Binance market orders via `quoteOrderQty`)
- **`monitor.py`** — `scanner_loop()` (15-min) + `watcher_loop()` (30-sec) async background tasks; Telegram notifications to `ADMIN_CHAT_ID`

### `src/data/db.py`
- Added `positions` table (SQLite): symbol, mode, entry/exit prices, qty, sl/tp, breakeven_hit, status, pnl
- Added helpers: `open_pos()`, `get_open_pos()`, `close_pos()`, `update_pos_sl()`, `get_closed_positions()`

### `main.py`
- Imports `TradingMode` at top
- Added `/mode sim|live` command — switches bot_data trading_mode
- Added `/status` command — shows open position + last 5 closed trades
- `post_init` now: calls `init_db()`, defaults to SIMULATION mode, starts `scanner_loop` + `watcher_loop` as `asyncio.create_task`
- New env var: `ADMIN_CHAT_ID` (notifications target), `TRADE_BUDGET` (default 100 USDT)

**New env vars needed:**
```
ADMIN_CHAT_ID=<your telegram user id>
TRADE_BUDGET=100
```

---

## 2026-04-22 — Scoring system (0-10 per layer, total 0-100)

**Changes:**
- Replaced binary pass/fail with 0-10 score per layer in `src/signals/indicators.py`
- Entry condition: `total_score >= 70` (was: all 10 must pass)
- Added `_score_l1..l10()` helper functions with graduated scoring logic
- `pass` field kept for backward compat (derived as `score >= 7`)
- Added `_score_icon()` → 🟢 (≥7) / 🟡 (4-6) / 🔴 (<4)
- `main.py` display: each layer shows `🟢/🟡/🔴 Name 7/10 — data`, total score shown
- WAIT message now shows top-3 weakest layers with their scores
- `backtest/engine.py`: `_eval_bar` uses score sum instead of all-pass logic; L4/L7/L9 get neutral score 5 in backtest
- `diagnose.py` updated to show per-layer scores

**Entry threshold:** 70/100 (configurable via `ENTRY_SCORE_THRESHOLD`)

---

## 2026-04-22 — BTC-only + src/ restructure

**Changes:**
- Removed ETH/LTC/SOL/LINK — only BTCUSDT remains (`main.py` ASSETS)
- Reorganised `src/` flat files into 5 subdirectories:
  - `src/signals/` — indicators.py, pattern_analyzer.py
  - `src/data/` — binance_client.py, news_client.py, db.py
  - `src/backtest/` — engine.py (was backtest_engine.py)
  - `src/ai/` — orchestrator.py (was ai_orchestrator.py)
  - `src/bot/` — strings.py
- Removed LTC/SOL/LINK threshold overrides in backtest engine (ADX min back to 20, volume floor $30M fixed)
- Updated all imports in main.py, scripts/diagnose.py, and cross-module imports
- Deleted scripts/ltc_debug.py (dead code for removed assets)

---

## 2026-04-22 — Obsidian vault + CLAUDE.md setup
**By:** Claude
- Created `CLAUDE.md` with full project docs
- Created `obsidian/` knowledge base (architecture, signals, backtest, roadmap)
- Created `.claude/settings.json` with hooks for auto-Obsidian updates

---

## ~2026-04-20 — Market context in backtest results
**Commits:** `a874137`
- Added `_build_market_context()` in `main.py`
- Shows ADX strength, volume level, trend direction after backtest
- Explains to user why low-ADX markets produce few signals

---

## ~2026-04-19 — LTC threshold relaxation
**Commits:** `b44fe47`, `917a4bb`
- L1 ADX min: 25→15 for LTCUSDT (small cap, low ADX normal)
- L5 volume min: $500M→$10M for LTCUSDT
- Fixed Redis retry loop bug
- Added `save_db` param to backtest
- Fixed `diagnose.py` symbol handling

---

## ~2026-04-18 — L9 Fear/Greed blocker removal
**Commit:** `3c91743`
- L9 no longer blocks signal in backtest (was causing 0 signals on many runs)
- L5 volume threshold lowered to $30M in backtest

---

## Earlier — Initial build (Phases 1+)
- 10-layer signal system built
- Telegram bot with inline keyboard UI
- EN/RU i18n via `src/strings.py`
- Backtest engine with SQLite persistence
- Pattern analyzer (best hours/weekdays)
- AI meta-layer (OpenAI GPT-4o-mini)
- Docker deployment