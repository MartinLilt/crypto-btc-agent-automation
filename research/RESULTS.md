# Research Results — Strategy Search (2026-05-17)

Goal asked: €500/mo on €10k = **5%/mo ≈ 80% CAGR**. Honest finding: not
achievable at tolerable risk on €10k. But a strategy with **~2–3× the current
bot's real edge** was found and validated out-of-sample.

## Method
- Vectorized engine, no lookahead (weight at t earns t→t+1), costs 10–15 bps/turn.
- Data: Binance public klines. 1d for 30-coin universe (2019→2026),
  1h/4h majors. Funding history (8h) for 6 perps from 2020.
- Every result split 60% in-sample / **40% out-of-sample**. OOS is the verdict.

## Key numbers (OOS, after costs)

| Strategy | OOS CAGR | maxDD | Sharpe | note |
|---|--:|--:|--:|---|
| Current bot (paper_replay, real) | ~17.6% | 6% | ~ | low-vol, mostly in cash through bear |
| Single-asset trend (only BNB robust) | ~41% | 37% | 1.07 | works on BNB only, not generalizable |
| Cross-sec alt momentum, raw (FULL-30) | 82% | 55% | 1.23 | survivorship-inflated, lumpy |
| Cross-sec, conservative-12, raw | 63% | 54% | 1.16 | less survivorship |
| …vol-targeted 30% / 1.5x cap | 40% | 36% | 1.20 | risk-controlled |
| **Combined 50/50 rotation+carry** | **26.6%** | **16%** | **1.46** | best risk-adjusted |
| **Combined 70/30 rotation+carry** | **35.4%** | **22%** | 1.38 | higher return |
| Funding carry alone (delta-neutral) | ~5–9% | 1–8% | high | structural, market-neutral, stacks |
| Rotation volTgt60/3x (push for €500) | 97% | 54% | 1.32 | fragile, ruinous DD |

## Robustness (the important part)
128-cell parameter sweep of the rotation, OOS, conservative universe,
vol-targeted: **100% of cells OOS-positive, 97% > 20% CAGR, median 31%.**
IS→OOS param correlation = **−0.33** → do NOT optimize the backtest; use
robust central params (lookback ~20–30, hold 5, top_k 2–3).

## Honesty haircuts applied
Survivorship FULL→CONSERV ≈ −23% relative. Apply a further ~−30% live haircut
(slippage on small caps, the conservative-12 are themselves survivors, funding
carry idealized — real ≈ half). Realistic deployable: **combined book ≈ 18–25%
CAGR, ~20–27% DD** → ~€150–190/mo on €10k.

## Verdict
- €500/mo on €10k ⇒ ~3× leverage on rotation ⇒ ~50%+ drawdowns (account-killer).
- €500/mo at the *safe* blended edge ⇒ needs ~€25–30k capital.
- Best honest use of €10k: the **rotation + funding-carry book**, ~€200/mo
  expected at ~20% drawdown — real positive expectancy, not luck.

## Candlestick pattern mining (2026-05-18)
`research/patterns.py` — 28 patterns × 6 majors (1h) and 24 × 30 (1d),
forward-return excess over base, IS/OOS, 2000-shuffle permutation p, after
0.2% cost. **1d: 0 patterns survive. 1h: only `big_green` (large-body
momentum-ignition candle) survives** (excess +0.24–0.32%, OOS +0.05–0.10%
net, perm_p<0.05). All classic *reversal* patterns (hammer, doji,
morning/evening star, harami, engulfing-at-dip, pin/tweezer) are **zero or
negative after cost**, robustly. Conclusion: no hidden candle goldmine — the
crypto edge is momentum/trend persistence (what the rotation book already
exploits), not *isolated* candle shapes.

**L9 ablation (2026-05-18) — lead REFUTED.** Tested the live bot's actual L9
in the canonical 6-asset config: freezing `detect_candle_patterns` to a
constant CUTS net P&L 28–52% and per-trade expectancy 36–53% on BOTH the
recent-365d and 720d windows, and raises drawdown. L9 is NOT a drag — it
contributes ~$1.2–1.5k of the ~$3.5k in-sample edge. Reason: the bot's L9 is
a graded, 4h-confirmed, streak-penalised score evaluated only in the context
of 9 other aligned layers — a contextual confirmation/veto, not the standalone
single-bar predictor `patterns.py` tested. Lesson: ablate the real mechanism,
not a proxy. Repro: `python -m research.ablate_l9`.

## Round 2–3 — extended search CONVERGED (2026-05-19)
`research/more.py`. Decision metric = incremental value vs the deployed 50/50
book (OOS Sharpe 1.42 / CAGR 23.6% / DD 17.6%), not standalone.

| sleeve | standalone OOS | +to book | verdict |
|---|---|---|---|
| A TSMOM ensemble | 29% CAGR Shrp .95 | Shrp 1.42→1.30 | redundant (=trend) |
| B multi-factor XS | 46% CAGR Shrp 1.35 | Shrp 1.42→1.40 | redundant (=trend) |
| C let-winners-run | 31% CAGR Shrp .99 | Shrp 1.42→1.32 | redundant (=trend) |
| D BTC lead-lag | 40% CAGR Shrp 1.23 | Shrp 1.42→1.42 | no add |
| E carry best-4/12 | IS Shrp 4.07 / **OOS −3.24** | cuts CAGR | **OOS-refuted (overfit)** |
| F ETH/BTC pair MR | −22% CAGR | cuts Shrp | refuted (ratio trends) |

**Conclusion: the search has converged.** The only robust blocks are
(1) vol-targeted cross-sectional alt-momentum rotation and (2) simple fixed
long-only funding carry — the 50/50 book. All trend variants are the same bet
(no Sharpe gain, just more DD). The two market-neutral RV attempts fail OOS.
Round-3-E is a live demo of the overfit trap (Sharpe 4 IS → −3 OOS): more
"cleverness" now yields mirages, not edge. Ceiling of robust public-data spot:
~Sharpe 1.4, ~24% CAGR, ~18% DD OOS. The lever is capital + discipline + the
forward test — not another strategy. Repro: `python -m research.more`.

## Stress test (2026-05-19, `research/stress.py`)
- **Crisis windows:** book sidesteps majors via the BTC regime gate —
  China-ban −0.8% (BTC −39%), LUNA/3AC −0.2% (−51%), FTX −1.5% (−22%),
  2025 bear +17%. Only 2024-08 carry-unwind bit (−6.6%/12d).
- **Carry tail:** −30% one-off shock survivable (maxDD unchanged); carry→0
  still +321%. Carry is additive, not load-bearing — its failure ≠ blow-up.
- **Survivorship:** force-including delisted RNDR/MATIC held-to-zero did NOT
  hurt (trend filter exits early). cons-12 not survivorship-inflated.
- **Monte-Carlo (10k×1yr):** median +26%, 5th pctile −6.8%, P(loss) 10.5%,
  maxDD median 10% / 95th 20% / P(DD>30%) 0.2%. Asymmetric, low blow-up.
- **Vulnerabilities (real, manageable):** (1) cost-sensitive — Sharpe 1.42→
  0.96 from 15→80bps ⇒ liquid names + limit orders mandatory; (2) single
  held-alt −70% overnight ⇒ ~−13% book day, ~39% tail DD ⇒ mitigate with
  top_k=5, hard per-position stop, liquidity floor.
- **Honest €10k risk:** typical bad year ≈ −€700; plausible bad DD ≈ −€2000;
  single-alt-collapse tail ≈ −€4000. Not a blow-up; real money at risk.
Repro: `python -m research.stress`.

Reproduce: `python -m research.run all` ; `python -m research.patterns 1h|1d`