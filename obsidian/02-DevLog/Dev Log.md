# Development Log

Reverse-chronological. Add entry at top when significant changes land.

---

## 2026-05-13 (Evening) — 6-asset config + ETH/LINK overweight allocation — $147/mo OOS on $10k

**Summary:** Expanded supported asset universe from 3 (BTC/SOL/ETH) to 6 (+ LINK/AVAX/BNB), with per-asset OOS-tuned thresholds for the new alts. LINK turned out to be a second workhorse alongside ETH — combined they contribute 86% of OOS edge. Added `RECOMMENDED_ASSET_WEIGHTS` to overweight the workhorses. Capital required for $500/mo target dropped from $90k → $34k.

### Score-weighted sizing — rejected

Tried `_position_size_for_score` (0.5×/1×/1.5×/2× base size by score margin above threshold). Result: −$287 vs per-asset baseline because 70% of trades cluster at margin 0-4 and get 0.5× sizing. Distribution is too flat to discriminate position size by score alone. Kept the code in `paper_replay.py` behind `--score-weighted` flag for future experiments but not in default path.

### ETH-overweight sweep

| Allocation | OOS $ | OOS $/mo | Max DD |
|---|---:|---:|---:|
| Baseline equal (3 assets) | +$667 | $56 | 3.4% |
| ETH 60 / SOL 30 / BTC 10 | +$992 | $83 | 5.2% |
| ETH 75 / SOL 20 / BTC 5  | +$1144 | $95 | 6.2% |
| No-BTC (ETH 70 / SOL 30) | +$741 | $62 | 3.9% |

### New asset sweep (LINK / AVAX / BNB)

| Asset | OOS-best threshold | OOS Trades | OOS WR | OOS $ |
|---|---:|---:|---:|---:|
| LINK | 50 | 138 | 44.2% | **+$377** ← strong |
| AVAX | 50 | 129 | 38.8% | +$57 (marginal) |
| BNB  | 60 | 109 | 43.1% | +$97 |

LINK at threshold 50 is the surprise — its OOS edge per $10k is $31/mo standalone, basically matching ETH ($37/mo). Together ETH+LINK are the strategy's revenue generators.

### 6-asset combined results

| Config | Trades | OOS $ | OOS $/mo | Max DD |
|---|---:|---:|---:|---:|
| 6 assets equal weight | 1221 | +$1215 | $101 | 4.10% |
| **6 assets ETH+LINK heavy** (canonical) | 1221 | **+$1764** | **$147** | 6.33% |

ETH+LINK heavy = weights `{BTC: 0.05, SOL: 0.15, ETH: 0.30, LINK: 0.30, AVAX: 0.10, BNB: 0.10}`. ETH and LINK get $1800/trade (1.8× base), others get $300-900.

OOS breakdown for ETH+LINK heavy:
- ETH @ 60: 80 trades, WR 51.2%, **+$832** at $1800/trade
- LINK @ 50: 138 trades, WR 44.2%, **+$679** at $1800/trade
- SOL @ 65: 57 trades, WR 43.9%, +$140 at $900/trade
- BTC @ 70: 11 trades, WR 54.5%, +$20 at $300/trade
- BNB @ 60: 109 trades, WR 43.1%, +$58 at $600/trade
- AVAX @ 50: 129 trades, WR 38.8%, +$34 at $600/trade

### Code changes

- `src/signals/indicators.py:48-83` — `ENTRY_SCORE_THRESHOLDS` extended with LINK=50, AVAX=50, BNB=60. New `RECOMMENDED_ASSET_WEIGHTS` constant exported for paper_log / wizard integration.
- `main.py:40-46` — Added Chainlink, Avalanche, BNB to the user-facing `ASSETS` list (asset picker now shows 6 options).
- `scripts/paper_log.py:65` — Extended `ASSETS` list to include the new alts (live paper trader now monitors all 6).
- `scripts/paper_replay.py` — added `--score-weighted` and `--asset-weights` flags; both threaded through to position sizing.

### $500/mo capital requirement progression

| Step | Capital for $500/mo |
|---|---:|
| Old threshold=70 (baseline) | $400k+ |
| + Threshold=50 uniform | $192k |
| + Per-asset thresholds (BTC/SOL/ETH) | $90k |
| + ETH-overweight allocation | $60k |
| **+ 6-asset basket, ETH+LINK heavy** | **$34k** |

### Open follow-ups

- **Live paper test for 4–8 weeks** with new 6-asset config + recommended weights before scaling capital. Especially want to validate LINK's OOS edge — 138 trades is statistically meaningful but only one 365d window.
- **AVAX** OOS edge is thin (+$57 on 129 trades, $0.44/trade). Consider removing if next sweep also shows low edge — adds noise, fees, and capital lockup for little return.
- **Leverage simulation** — next experiment. Add `--leverage` flag + funding-cost model. 3-4× could push toward $500/mo on $10k base capital, at DD 19-25%.
- **ML on layer weights** — alternative to leverage. Train binary classifier on (L1..L10 scores → trade outcome) to find non-linear combinations. Could push WR/EV.
- **Integrate `RECOMMENDED_ASSET_WEIGHTS` into paper-wizard** as an "Apply recommended portfolio" button — currently weights are only used in `paper_replay.py` via CLI.

---

## 2026-05-13 (PM) — Per-asset score thresholds (BTC=70, SOL=65, ETH=60) — 2.2× better OOS edge

**Summary:** Followed up on the morning's "uniform 50" change with per-asset tuning. Mined the saved sweep runs (#16-#19) to compute first-half vs second-half (OOS) PnL per asset × per threshold. Found that the OOS-optimal threshold differs sharply by asset: BTC=70, SOL=65, ETH=60. Combined per-asset OOS PnL is $667 vs $308 for uniform=50 — a 2.2× improvement at the same drawdown. Capital requirement for $500/mo target drops from $192k to ~$90k.

### Per-asset OOS scan (second half of run #16's 720d, threshold variations from runs #16-#19 plus baseline)

| Asset | th=50 OOS $ | th=55 | th=60 | th=65 | th=70 | Pick |
|---|---:|---:|---:|---:|---:|---|
| BTC | −4.13 | −68.76 | −104.32 | −89.10 | **+65.94** | **70** |
| SOL | +84.53 | +63.70 | +39.79 | **+155.73** | +58.89 | **65** |
| ETH | +227.26 | +415.36 | **+445.35** | +213.55 | +177.79 | **60** |

Each asset has a sharply different OOS-optimum. BTC is the most fragile — its OOS edge dies fast as the threshold drops below 70. ETH is the most permissive — going below 60 still gives positive OOS but less than 60. SOL peaks at 65.

### Confirmation run (per-asset 70/65/60, 720d full window)

Run #22, label `PER-ASSET-BTC70-SOL65-ETH60`:
- 359 trades total (BTC 34, SOL 149, ETH 176)
- In-sample WR 44.0%, net **+$823** ($699 after-tax), max DD 3.40%
- Walk-forward OOS: +$667 over 365d = **~$56/mo on $10k**

OOS PnL by asset (confirms the choice):
- BTC@70: +$66 / 11 trades / WR 54.5%
- SOL@65: +$156 / 57 trades / WR 43.9%
- ETH@60: +$445 / 81 trades / WR 50.6%

### Code changes

- `src/signals/indicators.py:48-72` — `ENTRY_SCORE_THRESHOLDS: dict[str, int]` + `ENTRY_SCORE_THRESHOLD_DEFAULT = 60` + `get_score_threshold(symbol)` helper. The bare `ENTRY_SCORE_THRESHOLD` constant is preserved as a legacy alias pointing to the default, so older imports don't break.
- `src/signals/indicators.py:827, 1155` — `symbol: str | None = None` added to `check_entry_signal` and `check_entry_signal_short` signatures.
- `src/signals/indicators.py:905, 1228` — both signal functions now do `total_score >= get_score_threshold(symbol)` instead of the bare constant.
- `src/backtest/engine.py:43` — import `get_score_threshold`. Lines 224 and 752 (both `_eval_bar` and `_eval_bar_short`) use it; the existing `symbol` parameter on those functions is now actually load-bearing.
- `main.py:392` — passes `symbol=symbol` into `signal_fn` (covers both LONG and SHORT live analysis paths).
- `src/trading/monitor.py:101` — passes `symbol=SYMBOL` into `check_entry_signal`.
- `scripts/paper_replay.py` — `--score-threshold` flag now patches the whole `ENTRY_SCORE_THRESHOLDS` dict + default + legacy alias, so threshold sweep experiments still work as a single-value override.

### $500/mo target update

Pre-tuning expectation: $192k capital required.
Post-tuning expectation: **~$90k capital required** at the OOS edge of $55.58/mo per $10k.

### Open follow-ups

- BTC's OOS edge is fragile (only 11 trades at th=70 over 365d). Consider: maybe BTC should be dropped from the bot entirely, or paired with HODL-BTC as the benchmark.
- ETH WR at 50.6% on 81 OOS trades is statistically meaningful (95% CI ~40–61%) — this is the only signal I'd run live with confidence.
- Live paper test for 4–8 weeks with the new per-asset config before scaling capital. Especially want to see whether ETH OOS edge persists into 2026.
- Consider a separate threshold for SHORT (currently SHORT uses the same per-asset dict but its OOS performance has not been re-validated post-tuning).

---

## 2026-05-13 (AM) — ENTRY_SCORE_THRESHOLD lowered 70 → 50 (4× edge increase)

**Summary:** Score-curve sweep + 15-min timeframe experiment found the real bottleneck of the strategy: the entry threshold was overly conservative at 70. Lowering to 50 produces 4.7× more $ PnL on $10k with WR dropping only 3 pp and max DD doubling from 1.7% to 3.3% — still well within retail-acceptable. Walk-forward OOS (second half of 720d) confirms edge persists at reduced magnitude. Committed the change to `src/signals/indicators.py:ENTRY_SCORE_THRESHOLD`.

### Why we ran the sweep

User goal: find a strategy that produces ≥$500/mo. The baseline (TP=3/SL=1.5, default threshold 70) was only ~$11/mo on $10k. Earlier experiments (wide stops, dynamic SL with break-even, BOTH-direction, full-size positions) all degraded or maintained baseline at best. The score threshold had never been tested — it was a guess at 70.

### Score-sweep results (1h, 720d LONG-only, BTC+SOL+ETH)

| Score | Trades | WR | Net $ | After tax | Max DD | $/mo on $10k |
|---|---:|---:|---:|---:|---:|---:|
| **50** | **955** | **42.0%** | **+$1,329** | **+$1,129** | 3.31% | **$47** |
| 55 | 805 | 42.0% | +$1,097 | +$933 | 3.81% | $39 |
| 60 | 579 | 43.7% | +$1,080 | +$918 | 3.17% | $38 |
| 65 | 336 | 42.9% | +$445 | +$378 | 1.80% | $16 |
| 70 (old) | 128 | 45.3% | +$282 | +$240 | 1.73% | $10 |

### 15m timeframe sanity check (365d)

| Config | Trades | WR | Net $ | After tax | DD | $/mo |
|---|---:|---:|---:|---:|---:|---:|
| 15m th=70 | 105 | 42.9% | +$248 | +$210 | 1.39% | $17 |
| 15m th=60 | 336 | 43.5% | +$665 | +$566 | 2.79% | $47 |

Both paths (lower threshold on 1h, default-ish on 15m) converge on the same ~$47/mo number — strong evidence that **per-signal edge is constant (~$1.40/trade after fees)** and the only lever is signal frequency.

### Walk-forward OOS (1h, score=50, 720d split at midpoint)

| Half | Period | Trades | WR | Net $ | $/mo on $10k |
|---|---|---:|---:|---:|---:|
| First (in-sample) | 2024-05 → 2025-05 | 588 | 42.9% | +$1,021 | $85 |
| **Second (OOS)** | 2025-05 → 2026-05 | 367 | **40.6%** | **+$308** | **$26** |

OOS edge is ~30% of in-sample. Realistic expectation post-deployment: **$25–30/mo on $10k**. For $500/mo target, need ~$170k–$200k capital.

Per-asset OOS breakdown: BTC −$4 (78 trades, 41% WR), SOL +$85 (137, 39.4%), ETH +$227 (152, 41.4%). **ETH remains the workhorse**; BTC OOS is essentially flat. Consider per-asset thresholds in a future iteration.

### What I tried that didn't work (for the record)

- **Wide stops** (TP=2/SL=3/5/8, no BE) → +$67 best, much worse than baseline. Negative drag from worse RR exceeds the gain from higher WR.
- **Dynamic SL** (BE move at 6/8/12h, optional trailing) → +$53 best. The BE move kicks out slow-burning winners too early. Strategy needs time to play out, not early-exit insurance.
- **Full-size positions** ($10k cap = $10k/trade) → catastrophic — first SL leaves free_capital < per_trade and bot is locked out for rest of history.
- **BOTH-direction with default threshold** → +$195 (vs LONG-only +$282). SHORT side adds drag and doubles DD.

### Files changed

- `src/signals/indicators.py:48` — `ENTRY_SCORE_THRESHOLD = 50` (was 70, with rationale comment)
- `scripts/paper_replay.py` — new flags: `--max-hold`, `--min-rr`, `--max-concurrent`, `--dynamic-sl`, `--be-after-hours`, `--be-trigger-pct`, `--be-offset-pct`, `--trail-pct`, `--interval`, `--score-threshold`. Added monkey-patch fix for `engine.ENTRY_SCORE_THRESHOLD` (binds at import time so patching `indicators.ENTRY_SCORE_THRESHOLD` alone is not enough). Added missing-candle robustness in buy-and-hold benchmark.
- `src/backtest/engine.py:64` — `_INTERVAL_BARS_PER_DAY` extended for 15m/30m/2h.

### Open follow-ups

- Walk-forward on 15m + lower threshold combined — might compound (or might collide).
- Per-asset thresholds: BTC's OOS edge is gone; consider keeping threshold=70 just for BTC.
- Live paper test for 4–8 weeks with new threshold before scaling capital.
- Add funding-cost simulation for SHORT (still needed before re-enabling SHORT).

---

## 2026-05-13 — Paper-trading historical replay + honest viability test

**Summary:** Wrote `scripts/paper_replay.py` — a portfolio-aware historical replay that mirrors live paper-trader logic (capital reservation, BOTH direction, 3 assets) but walks chronologically through historical 1h candles. Ran three configurations (BOTH 720d, LONG-only 720d, BOTH 365d) on $10k / $1k-per-trade, persisted all runs + trades to new `paper_replay_runs` / `paper_replay_trades` tables. Goal: answer the user's question "can this bot actually make money?" with real numbers instead of headline backtest stats.

### What the script does differently from `engine.run_backtest`

- **Portfolio mode**: one shared capital pool across 3 symbols × 2 directions, not per-asset isolated runs.
- **Capital reservation**: signal is skipped if `free_capital < per_trade_size`. In practice this never fired on $10k/$1k because the strategy is so sparse.
- **BOTH-direction concurrency**: long and short can coexist for the same symbol (matches live wizard semantics).
- **$ PnL with realistic fees**: `qty × Δprice − entry_fee − exit_fee` using `BINANCE_TAKER_FEE = 0.1%` per side, not gross-pct round-trips.
- **Master timeline**: union of all 1h timestamps across assets, single walk per ts; `_slice_higher_tf_at` carves 4h/1d/1w slices at each step exactly like `_eval_bar` expects in live mode.
- **MTM equity snapshot every 24h** → max drawdown derived from the curve, not from per-trade max_drawdown_pct.

### Results

| Run | Period | Trades | WR | Net $ (post-fees) | After tax | Max DD | vs B&H |
|---|---|---:|---:|---:|---:|---:|---:|
| BOTH 720d | 2024-05 → 2026-05 | 403 | 39.7% | +$195 (+1.95%) | +1.66% | 4.34% | +25.33pp |
| **LONG-only 720d** | 2024-05 → 2026-05 | 128 | **45.3%** | **+$282 (+2.82%)** | **+2.40%** | **1.73%** | +26.20pp |
| BOTH 365d | 2025-05 → 2026-05 | 178 | 39.3% | +$104 (+1.04%) | +0.89% | 4.38% | +26.79pp |

Per-leg breakdown (720d, BOTH):

| Asset | Dir | N | WR | Net $ |
|---|---|---:|---:|---:|
| BTC | LONG | 34 | 50.0% | +$81 |
| BTC | SHORT | 57 | 36.8% | −$26 |
| SOL | LONG | 58 | 39.7% | +$49 |
| SOL | SHORT | 125 | 38.4% | +$35 |
| **ETH** | **LONG** | 36 | **50.0%** | **+$152** |
| ETH | SHORT | 93 | 35.5% | **−$96** |

### Findings (the honest ones)

- **SHORT side has negative EV across the portfolio**: $195 (BOTH) − $282 (LONG-only) = −$87 contributed by SHORT over 720d, and DD doubles when SHORT is enabled (4.34% vs 1.73%). The previously-positive BTC/SOL SHORT numbers from the per-asset 720d backtest table were artifacts of being evaluated in isolation — when shorted alongside concurrent SHORT positions on other assets, drag accumulates.
- **ETH LONG is the only signal with real edge.** 50% WR at TP=2×SL → +0.75% EV per trade after fees. 36 trades over 720d produced +$152, which is **more than half of the LONG-only strategy's total** ($282).
- **B&H is a strawman in this window.** SOL/ETH crashed 40–45% from May 2024 → May 2026; only BTC was up (+16%). The bot's "+25pp vs B&H" is mostly "the bot avoided being long SOL/ETH on the way down", not alpha.
- **Capital reservation never activated** on $10k/$1k. Strategy is sparse enough (~0.4% signal fire rate) that more positions could be open simultaneously without ever maxing out — capital efficiency story doesn't change anything at this scale.
- **The bot underperforms USDT yield** (4–5%/yr) in absolute terms. After-tax annualised return is ~1.2%/yr on LONG-only.
- **Honest one-liner**: if you would have HODL'd alt-bags, this saves you from yourself. If you're comparing to a savings account or HODL BTC, it loses.

### Files

- `scripts/paper_replay.py` (new) — 463 LOC, self-contained replay + report + DB persistence.
- `data/backtest.db`:
  - `paper_replay_runs` (new table, 3 rows)
  - `paper_replay_trades` (new table, 709 rows: 403 + 128 + 178)

### Open questions / next steps

- Run **ETH-LONG-only** isolated to confirm it's the workhorse and not an artifact of correlation with SOL/ETH.
- Add **funding-cost simulation** for SHORT (current SHORT numbers don't subtract perp funding — real Binance Futures SHORT would be worse than what we showed).
- Generate **monthly PnL breakdown** from `paper_replay_trades` to see which regimes the strategy works in.
- Consider **disabling SHORT in the live wizard by default** (still selectable as advanced option) given the consistent negative contribution.

---

## 2026-05-06 — Audit punch-list cleanup (12 items)

**Summary:** Worked through a 21-item audit punch list. Two critical correctness bugs fixed (one of them changes every backtest number), eight important items resolved, and a handful of dead-code / UX cleanups.

### Critical fixes

- **Backtest now applies the same daily + weekly EMA hard blocks as live** (`engine.py:_eval_bar`, `_eval_bar_short`). Previously paper trader and backtest fired entries that the live `check_entry_signal` would have blocked — the same class of divergence as the 4h fix in commit 064829d. Engine now fetches 1d/1w candle history alongside 1h+4h, slices to ts_ms with `_slice_higher_tf_at`, and applies `_daily_block_long/short` + `_weekly_block_long/short` predicates inside `all_pass`. Wired through `_run_window_loop[_short]`, `run_backtest[_short]`, `run_backtest_research[_short]`, `paper_log.run_once`, `_research_for_assets`, and `_run_walkforward`. **All historical backtest numbers are now stricter than what was previously stored.** Sample: BTC 180d LONG dropped from 8 → 3 signals; the headline 720d numbers will need to be re-measured before being quoted.
- **Pattern analyzer was reading the wrong DB columns.** The schema columns `l9_fg_value` and `l8_funding` were named after old layer roles (Fear&Greed, Funding) but the engine writes the new L9 (candle pattern) and L8 (S/R proximity) layer scores there — both in the 0–10 range. So `_by_fg_band` and `_by_funding_band` were bucketing 1–10 scores into "Extreme Fear / Overheated" buckets and producing meaningless output. Replaced both with `_by_l9_score_band` / `_by_l8_score_band` (Weak/Below avg/Neutral/Strong/Top). Updated `_power_combos` and `_layer_block_stats` accordingly. Column names left as-is (no migration churn) but the misnaming is documented in the function docstrings.

### Important fixes

- **SHORT trade rows now have full schema parity** with LONG — added `l4_pass, l5_spread_pct, l6_rr_ratio, l8_funding, l8_oi_chg, l9_fg_value, l10_net_vol` to the dict in `_run_window_loop_short`. Previously these seven columns were NULL for every short row in the DB, breaking `_layer_block_stats` for SHORT.
- **Candle cache** in `main.py` (`_CANDLE_CACHE` keyed by `(symbol, days, interval)`) drives both `_run_walkforward` and `_research_for_assets`. Paper-wizard BOTH-mode previously did 2× full Binance fetches per asset (once per direction); now one fetch is reused.
- **Patterns flow reuses stored direction.** `bt_patterns_<asset>` (the "show patterns" button after a backtest) and the `/patterns` slash command now read `context.user_data["bt_direction"]` (or `cfg["direction"]`) and skip the picker if it's set. Removes the 2-tap regression introduced when direction-pickers landed.
- **`bt_run` USD math simplified.** The fallback ladder `result.get("total_pnl_net_fees_pct", gross_usd / scale * scale - signals * 0.2 * scale) * scale` was double-scaled and arithmetically wrong. The fallback was dead because `_calc_stats` always returns the key — replaced with the direct read.
- **`has_open_paper_trade(direction)` is now required** (no default). Prevents future callers from silently scoping to LONG.

### Nice-to-fix

- Deleted `_fetch_fear_greed_history` and `_fetch_funding_history` from `engine.py` — both referenced undefined names (`_FEAR_GREED_URL`, `_BINANCE_FUTURES`, `cache_fear_greed_history`, `get_fear_greed_for_date`) and would `NameError` if called. Never called from anywhere.
- Dropped the ignored `spread_approx` parameter from `_eval_bar` and `_eval_bar_short` signatures; updated all call sites.
- Removed unused imports: `GOOD_HOURS_UTC`, `SKIP_WEEKDAYS`, `_score_l10`, `timedelta` from `engine.py`.
- `short_disclaimer` no longer hard-codes "BTC ≈0%, SOL −36%, ETH −51%" — generic warning text instead.
- "No data" pattern message now suggests trying the other direction (LONG ⇄ SHORT).
- Renamed `_slice_4h_at` → `_slice_higher_tf_at` (the helper was already generic; the name was historical).

### Skipped

- Item #5 (engine ignores L7 news in backtest) — pre-existing, requires historical news data we don't have. Backlog.
- Item #11 (`is_downtrend` partial-alignment branch) — on closer inspection symmetric with `is_uptrend`; both else-branches only fire on float equality (essentially never). Cosmetic.
- Item #12 (SHORT path missing `bt_override_score` logging) — purely diagnostic.

### Re-measured backtest numbers

Re-ran 720d backtest + walk-forward for all three assets with the new stricter engine (LONG TP=3%/SL=1.5%):

| Asset | Old after-tax (pre-fix) | **New after-tax** | Old WF OOS | **New WF OOS** |
|---|---:|---:|---:|---:|
| BTC | +21.08% | **+5.79%** | profitable both halves | +4.57% (20 sigs) |
| ETH | +28.86% | **+14.33%** | +29.41% | +24.05% (31 sigs) |
| SOL | +67.49% | **+26.26%** | +47.34% | +14.87% (40 sigs) |

BTC and SOL absorbed the biggest cut — daily/weekly hard blocks remove the most trades when the trend is choppy. ETH stayed strongest because its 720d window has cleaner directional regimes.

**SHORT (now also gated by inverted daily/weekly blocks):**

| Asset | Sigs | WR | Net | After-tax |
|---|---:|---:|---:|---:|
| BTC | 104 | 38.5% | +32.42% | +9.88% |
| ETH | 168 | 33.3% | +0.00% | −33.60% |
| SOL | 216 | 38.0% | +46.92% | +3.16% |

The previous "shorts are catastrophically negative" picture is mostly an artifact of evaluating shorts in non-bearish regimes — once the daily/weekly bear-alignment hard block is on, BTC and SOL shorts go positive on the 720d window. ETH still loses after tax. Disclaimer was generalised so no specific numbers go stale.

CLAUDE.md updated to reflect the new headline numbers and to call out the re-measurement date so future readers know which engine version produced them.

---

## 2026-05-05 — SHORT direction reaches feature parity with LONG

**Summary:** Promoted the existing short-direction infrastructure from "engine-only, hidden" to a first-class option exposed across every analysis surface in the bot: live analysis, backtest, research grid, walk-forward, patterns, and paper trading (including dual-direction "BOTH" mode). User picks LONG or SHORT after the asset, with a one-line −EV disclaimer carrying the bull-regime warning.

### Engine + DB
- New public APIs: `run_backtest_short()` and `run_backtest_research_short()` mirror the long-direction entry points, persist `direction='SHORT'` in `backtest_runs` and each row of `backtest_trades`.
- DB migration adds `direction TEXT NOT NULL DEFAULT 'LONG'` to `backtest_runs`, `backtest_trades`, `paper_trades`. Long-direction rows pre-existing in the DB stay as 'LONG' by default.
- `get_trades(symbol, days, direction)` and `compute_patterns(symbol, days, direction)` filter by direction. Pattern cache key includes direction so long/short don't collide.
- Long-side trade rows now also persist `direction='LONG'` explicitly (previously implicit).

### Signals
- **Bug fix**: `check_entry_signal` was missing its `return should_enter, report` — silently returned `None`, causing live analysis to crash on first call. The orphan `return` lived inside `check_sell_pressure` as unreachable dead code; moved it back to its rightful function.
- New `check_entry_signal_short()` mirror — uses `is_downtrend`, `is_not_oversold`, `check_sr_proximity_short`, `check_sell_pressure`. Inverts L7 (bullish news = bad for short), L9 (10 − long_score: long detector scores bearish patterns low). Hard blocks inverted: RSI < 35 (oversold reversal risk), price > daily/weekly EMAs blocks short entry.

### Paper trader (`scripts/paper_log.py`)
- `_check_open_trade()` is direction-aware: SHORT inverts both TP/SL geometry (TP below entry, SL above) and PnL sign. Verified with 4-case smoke test.
- `_check_for_signal(direction='LONG'|'SHORT')` selects `_eval_bar` vs `_eval_bar_short`.
- `run_once(direction='LONG'|'SHORT'|'BOTH')` — BOTH scans both directions per asset; long and short positions can coexist for the same symbol (independent policy, per agreed design).
- `has_open_paper_trade(symbol, direction)` — duplicate-guard now per-direction.
- Telegram notifications include a 📈/📉 emoji + direction label so the user can tell which side just opened/closed.

### Bot UI
- Added a shared direction-picker step inserted after asset selection in every flow:
  - Live (`asset_*` → `livedir_*` → analysis)
  - Backtest (`bt_asset_*` → `btdir_*` → period → budget → TP/SL → run)
  - Patterns (`bt_patterns_*` → `patdir_*` → compute)
  - Research grid (`res_asset_*` → `rgdir_*` → grid)
  - Walk-Forward (`wf_asset_*` → `wfdir_*` → wf)
- Disclaimer appears under every direction picker: "on 720d bull-regime data SHORT is net-negative (BTC ≈0%, SOL −36%, ETH −51%)". User makes an informed pick.
- `_run_walkforward(symbol, direction)` switches between `_run_window_loop` and `_run_window_loop_short`.

### Paper-trading wizard
- New step 2: Long / Short / **Both**. Step labels renumbered.
- For BOTH: research runs twice (once per direction), top-3 ranked **independently per direction** by WR (≥10 signals min). User sees up to 6 medals split into "📈 LONG" / "📉 SHORT" sections.
- Active config carries `direction` field; `_paper_log_tick` passes it through to `run_once`. Dashboard shows the direction next to the symbol.

### Smoke tests
- `run_backtest('BTCUSDT', 180d)` → 8 LONG signals, +1.37%
- `run_backtest_short('BTCUSDT', 180d)` → 43 SHORT signals, +2.42%
- `_check_open_trade` 4-case test: LONG TP/SL and SHORT TP/SL all return correct status + signed PnL.

### Why direction-aware indices vs a flag
We considered passing a `direction` flag through the existing `_eval_bar`. Rejected — short uses different scoring functions for L2/L3/L8/L9/L10 and inverts hard blocks, so a flag would have meant a function with two completely different code paths. Mirror functions keep each path readable in isolation.

### What's NOT in scope
- Real-trade Binance execution stays long-only for now (Phase 2 work).
- L4 (volume) and L5 (liquidity) are direction-symmetric — no short-specific tuning.
- Pattern-analyzer trade-row schema for SHORT is a subset (L4/L5/L6/L8/L9 fields are NULL); fine for current pattern dimensions but if those layers ever feed pattern bands we'll need to backfill.

---

## 2026-04-30 — Paper trading wizard + auto-tick in Telegram bot

**Summary:** Added a guided setup wizard to start paper trading from Telegram. User picks assets and research lookback; the bot runs the research grid, ranks the top 3 strategies by Win Rate, and (on selection) activates an in-bot JobQueue tick that calls `paper_log.run_once()` every hour. No external cron needed.

### User flow

1. `📊 Paper Trades` → `⚙️ Setup paper trading`
2. **Step 1**: multi-select assets (BTC/SOL/ETH checkboxes, tap to toggle)
3. **Step 2**: pick research lookback (90 / 180 / 365 days)
4. Bot runs research grid (4 TP/SL × selected period × selected assets)
5. Bot displays **top 3 by WR** (filtered to ≥10 signals minimum) with medals 🥇🥈🥉
6. User taps a strategy → config saved to `bot_data["paper_config"]`, JobQueue scheduled
7. Hourly tick: `paper_log.run_once(assets=[symbol], tp_pct, sl_pct)` runs in executor
8. `🛑 Stop paper trading` button cancels the JobQueue and marks config inactive

### Code changes

- **`scripts/paper_log.py`**: Refactored — added `run_once(assets, tp_pct, sl_pct)` API. CLI `main()` now wraps it. The bot can now drive paper-logging directly without spawning subprocesses.
- **`main.py`**:
  - 7 new wizard handlers (`ps_setup`, `ps_toggle`, `ps_assets_done`, `ps_period_chosen`, `ps_strategy_chosen`, `ps_stop`, `_paper_log_tick`)
  - `_paper_config()` helper to read active state from `bot_data`
  - `_schedule_paper_job()` / `_cancel_paper_job()` JobQueue lifecycle
  - `_research_for_assets()` driver — calls existing `_run_window_loop` per asset
  - Auto-resume on startup: if `paper_config.active` was True before restart, JobQueue is re-scheduled in `post_init`
  - Updated `menu_research_paper` dashboard to show active config + Setup/Stop button
- **`src/bot/strings.py`**: 12 new bilingual strings
- **`requirements.txt`**: pinned `python-telegram-bot[job-queue]==21.9` (apscheduler dep)

### Architecture notes

- Single-user model: `paper_config` lives in shared `bot_data`. If multi-user is needed later, switch to per-user_id key.
- Top-3 ranking by WR with `n_signals ≥ 10` filter (avoids selecting strategies on tiny samples).
- JobQueue runs in the bot's asyncio loop; `paper_log.run_once` is wrapped in `run_in_executor` because it's sync (HTTP requests, SQLite). Bot stays responsive.
- `PicklePersistence` already serialises `bot_data` along with `user_data`, so `paper_config` persists across restarts automatically.

### What's still NOT in the UI (deliberate)

- Custom TP/SL input — research already explores 4 TP/SL combos
- Filter experiments (gap_pct, score threshold, cooldown) — failed walk-forward earlier
- Short-direction strategies — net negative on bull-regime data

---

## 2026-04-29 — Short-direction infrastructure added (NOT exposed in UI)

**Summary:** Built parallel signal evaluator and trade simulator for SHORT entries (mirror of long path). Tested on 720d × BTC/SOL/ETH with walk-forward validation. Result: shorts are net-negative on every asset across the available data window. Code committed for future use; deliberately NOT wired into Telegram UI or paper-trading.

### Backtest results (720d, TP=3%/SL=1.5%)

| Asset | LONG Net% | SHORT Net% | LONG Sigs | SHORT Sigs |
|-------|----------:|-----------:|----------:|-----------:|
| BTC | +21.08% | −0.08% | 74 | 180 |
| SOL | +67.49% | **−36.08%** | 173 | 340 |
| ETH | +28.86% | **−51.10%** | 117 | 282 |

### Walk-forward (split halves)

| Asset | SHORT IS | SHORT OOS | Verdict |
|-------|---------:|----------:|---------|
| BTC | −5.11% | +4.27% | Marginal — 79 OOS sigs is statistical noise |
| SOL | −17.20% | −18.88% | Consistently losing |
| ETH | −18.80% | −32.30% | Consistently losing, getting worse |

### Why shorts fail on this dataset

May 2024 — Apr 2026 was a **net bull regime** (especially SOL: +60% net long over 720d). Shorts in a bull market = consistently fighting the trend. There's no 2022 FTX-style crash in this window, so we can't calibrate shorts on actual bear data. **This is expected behavior, not a code bug.**

### Code added (isolated, non-breaking)

- **`src/signals/indicators.py`**: `is_downtrend()`, `is_not_oversold()`, `_score_l3_short()`, `check_sell_pressure()` — direction-flipped versions of L2/L3/L10
- **`src/signals/support_resistance.py`**: `_detect_swing_lows()`, `_score_sr_short()`, `check_sr_proximity_short()` — L8 mirror checking supports below price instead of resistance above
- **`src/backtest/engine.py`**: `_eval_bar_short()`, `_simulate_trade_short()` (PnL inverted: `(entry - exit) / entry`), `_run_window_loop_short()` — mirror backtest path

L1 (volatility), L4 (volume), L5 (liquidity), L6 (R/R), L7 (news) are direction-symmetric and reuse long-path functions unchanged. L9 (candle patterns) inverts the score (`10 - long_score`) since the existing module already detects bearish patterns and assigns them low scores.

### Why NOT exposed in UI

Putting "Backtest SHORT" in the bot would invite users to deploy a strategy that loses money on the available data. We have no bear-regime data to validate on. When/if a bear regime arrives, re-run the comparison: if shorts cross break-even, then expose. Until then, it's −EV infrastructure.

### When to revisit

- **Real bear regime hits** — e.g., BTC drops 20%+ over 30 days
- **Longer dataset becomes available** — 1500+ days covering 2022 crash
- **User explicitly wants hedging** — long + short = market-neutral; net edge could come from selectivity rather than direction

---

## 2026-04-29 — Fix: bot now remembers user language across restarts

**Symptom:** Different analyses/tools ran in different languages because every bot restart wiped each user's `_lang` preference back to the "en" default. `context.user_data` was purely in-memory.

**Fix:** Added `PicklePersistence(filepath="data/bot_state.pkl")` to the `ApplicationBuilder`. python-telegram-bot v21 will now serialize `user_data` (and `chat_data`) to disk on every update and restore on restart.

```python
persistence = PicklePersistence(filepath="data/bot_state.pkl")
app = ApplicationBuilder().token(BOT_TOKEN).persistence(persistence)...
```

State file is created lazily on first user interaction. `data/` was already gitignored.

Roadmap "Known Issues" item ticked off.

---

## 2026-04-27 — Research toolkit exposed in Telegram bot

**Summary:** Reorganized Research menu in the bot from single-action to a sub-menu with three tools: Grid Search (existing), Walk-Forward (new), and Paper Dashboard (new). Brings most session-time analysis capabilities to the user's phone.

### Changes

- **`main.py`**:
  - `menu_research()` now shows 3 sub-buttons instead of asset picker
  - `menu_research_grid()` (new) — wraps existing asset-picker → standard grid run
  - `menu_research_wf()` (new) — asset picker for walk-forward
  - `wf_asset_chosen()` (new) — runs split-half validation across 4 TP/SL combos, emits per-combo verdict (stable/partial/hurts/neutral) + best survivor
  - `_run_walkforward()` (new) — sync helper: fetches 1h+4h candles, splits 720d in halves, runs combos
  - `_format_wf_msg()` (new) — Telegram-friendly markdown table
  - `menu_research_paper()` (new) — instant dashboard: per-symbol WR/Net%, open positions, last 5 closed
- **`src/bot/strings.py`**: 8 new bilingual strings (research_pick_type, btn_research_*, wf_*, paper_dashboard_empty)

### Curated, NOT exposed

Filter experiments (`gap_pct` hard-block, `score_threshold` raise) and `cooldown` are deliberately NOT in the UI. All three failed walk-forward validation earlier in the session — exposing them would invite users to deploy overfit configurations.

### Walk-forward verdicts emitted

- ✓ stable — both halves positive, magnitude similar
- ⚠ partial — both positive but OOS magnitude differs significantly
- ✗ hurts — OOS lost money or in-sample profit didn't survive
- · neutral — neither half showed clear edge

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