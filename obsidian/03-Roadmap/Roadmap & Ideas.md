# Roadmap & Ideas

→ See also: [[Architecture Overview]] | [[Dev Log]]

---

## Currently Working ✅

- 10-layer signal analysis (live)
- Hard blocks: RSI >65, ADX 25–40 — [[Filters & Hard Blocks]] _(weekly EMA21 removed 2026-04-27)_
- Backtest engine (all 5 assets, all periods) — [[Backtest Engine]]
- Simulator: user picks asset / period / budget / TP/SL
- Research: fully automatic grid search (12 combos + budget projections) — [[Research Feature]]
- Pattern analyzer (best hours/weekdays, ADX bands, score bands from DB)
- ADX danger zone filter (data-driven, 318 trades) — [[ADX Danger Zone Analysis]]
- Lithuanian GPM 15% tax (net annual, losses offset gains) — [[Tax & Fees Model]]
- Local Ollama LLM meta-review (qwen2.5:3b / llama3.2)
- EN/RU Telegram UI
- Docker deployment

---

## In Progress / Next Up 🔧

### Real Trade Execution (Phase 2–4)
- [ ] Binance API key auth (spot trading)
- [ ] Fetch account balance
- [ ] Market buy / sell orders
- [ ] In-memory TP/SL tracking loop (every 30s)
- [ ] Trade logging to DB

### Signal Quality
- [ ] Per-asset threshold calibration (SOL, LINK not yet tuned)
- [ ] L2 trend: add volume confirmation to Golden Cross
- [ ] L3 momentum: consider Stochastic RSI
- [ ] **Fix `l2_gap_pct` saved as 0.00 for all trades in DB** — metric not propagated from `is_uptrend` to trade row in `_run_window_loop`. Blocks pattern analysis on EMA50/200 spread. (Found 2026-04-27)
- [ ] **Smart cooldown / dedup**: only block consecutive SL_HIT within N hours of a previous SL — duplicates in trends are mostly winners (don't filter them) but in choppy regimes they multiply losses. (See research note below)

### Bot UX
- [ ] Trade history command `/history`
- [ ] Daily PnL stats via scheduled Telegram message

---

## Ideas / Backlog 💡

- ~~Persistent user state~~ — done 2026-04-29 (PicklePersistence)
- **Re-test SHORT strategy** when bear regime arrives — infrastructure exists in `_eval_bar_short()` etc., currently NOT exposed in UI because shorts are net −0% to −51% across BTC/ETH/SOL on 720d bull-regime data. See 2026-04-29 Dev Log entry.
- Telegram alert on signal (push without user action)
- Web dashboard for backtest results
- Portfolio mode (scan all 5 assets simultaneously)
- Webhook mode instead of polling (production)
- Candle cache (SQLite or Redis) — currently always fetches fresh from Binance

---

## Known Issues / Bugs 🐛

- ~~User state lost on bot restart~~ — fixed 2026-04-29 via `PicklePersistence` to `data/bot_state.pkl`
- Backtest slow for 1-year periods (Research: 1 fetch for 12 combos mitigates this)
- MarkdownV2 escaping fragile — `_esc()` must be applied to all dynamic values; Research uses plain `Markdown` instead to avoid issues with float dots

---

## Threshold Tuning Notes

- **BTC/ETH:** ADX >25, vol >$500M — original thresholds work well
- **LTC:** ADX 10–18 typical, vol $30–80M → relaxed to ADX >15, vol >$10M
- **SOL:** Higher volume, ADX varies — watch L1 pass rate
- **LINK:** Spot only (no futures) → L8 funding always skipped/pass

---

## Research Findings — 2026-04-27

**Strategy is profitable across BTC/ETH/SOL** (TP=3% / SL=1.5%, last 720d):

| Asset | Signals | WR% | Net% | After-tax% | Sharpe | Profitable Q |
|-------|---------|-----|------|------------|--------|--------------|
| BTC | 69 | 39.1 | +22.22 | +18.89 | 14.16 | 3/4 |
| ETH | 90 | 38.9 | +9.79 | +8.32 | 4.64 | 3/4 |
| **SOL** | **134** | **47.8** | **+60.20** | **+51.17** | **18.64** | **4/4** |

**Open question — extend bot to ETH/SOL?** CLAUDE.md currently restricts to BTCUSDT, but SOL produces ~3× the net P&L with 4/4 profitable quarters and Q1 2024 not losing money (BTC and ETH lost in Q1 2024).

### Cooldown experiment (NEGATIVE RESULT)

Hypothesis: 6h cooldown between signals would prevent SL clusters seen in Q1 2024 BTC.

Reality: cooldown removes _winning_ clusters in trending markets. Result on 720d:

| Asset | No cooldown | 6h cooldown |
|-------|------------:|------------:|
| BTC | +22.22% | +17.61% (worse) |
| ETH | +9.79% | +21.39% (better) |
| SOL | +60.20% | +14.60% (catastrophic) |

**Lesson:** duplicate signals are a tape-reading feature in trends, not a bug. Universal cooldown destroys edge. A smart cooldown would need to be conditional (e.g., only block after a prior SL_HIT within N hours, not after a TP_HIT).

### Walk-forward (BTC, no cooldown, no weekly filter)

| Q | Range | Sigs | WR% | Net% |
|---|-------|------|-----|------|
| Q1 | May–Oct 2024 | 18 | 27.8 | **−2.77** |
| Q2 | Nov 2024 – May 2025 | 26 | 38.5 | +5.74 |
| Q3 | May–Oct 2025 | 18 | 44.4 | +8.60 |
| Q4 | Jan–Apr 2026 | 7 | 57.1 | +10.64 |

Monotonic improvement Q1→Q4 is suspicious — could be genuine (recent fixes worked) or in-sample overfit. Need 90-day out-of-sample validation before trusting recent numbers.

### Realistic expectations (post-research)

- **Annual net (after fees + LT 15% tax):** ~10–15% on BTC, ~30%+ on SOL
- **Worst quarter:** can lose 3–7% (BTC Q1 2024, ETH Q1 2024)
- **Signal frequency:** ~1.5–2/month on BTC, ~3/month on SOL
- **Timeouts contribute slightly positive avg PnL** (+0.30%, 13% of trades) — not a leak