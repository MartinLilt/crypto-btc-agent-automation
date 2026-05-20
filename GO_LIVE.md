# Go-Live Criteria — Conservative 50/50 Book

These criteria were chosen on **2026-05-20 with a cool head**, before any
real money was at stake. Their entire value is that they were set BEFORE the
forward result is known, so you can't rationalise relaxing them in 60 days
when emotion takes over.

If you ever feel like changing a threshold below: don't. Re-read this and
either accept the rule or kill the project. There is no middle path.

---

## Hard prerequisites (none can be skipped)

1. **€10k is fully losable money.** Not rent, not debt, not next-month
   survival. Confirm again now, in writing, with yourself. If no — STOP.
2. **Tracker forward record uninterrupted ≥ 60 days** since inception
   2026-05-20. Earliest possible live-money decision: **2026-07-19**.
   Ideal: 90 days → 2026-08-18.
3. **You have read OPERATING.md** within the last week.

---

## Pass criteria (ALL of them, evaluated at day 60)

Measured on `python -m research.forward report` for the deployed tracker.

| Metric | Threshold | Why |
|---|---|---|
| Annualised return | ≥ +5% | Lower bound is bigger than zero by enough to not be noise (1σ band) |
| Maximum drawdown | ≤ 12% | Backtest predicted 16% over a year — 60d slice should be tighter |
| Monthly hit-rate | ≥ 40% (1 of 2 months green or both modestly positive) | mo_hit was 36% in backtest — anything well below 30% means regime shifted |
| Sharpe (since inception) | ≥ 0.6 | Backtest OOS 1.18; live haircut to ~0.6+ is acceptable |
| No single-week drawdown > 8% | Hard | A week worse than that = stress test hidden tail materialised |

If **any** criterion fails: do not go live. Either run forward another 60
days and re-evaluate, or abandon the book and accept this was a learning
project at the cost of the time invested.

## Phase 1 — initial real deployment (only if pass criteria met)

- **Capital deployed: €1 000–2 000 max.** Not the whole €10k.
- **Source: separate sub-account / wallet.** No mixing with paper account.
- **Duration of Phase 1: 30 days.**
- **Phase 1 kill switch (any one):**
  - Real Phase-1 drawdown > 10% (= €100-200 on €1-2k)
  - Real Phase-1 returns diverge from paper by > 5 percentage points in either direction (positive divergence is suspicious too — means model mismatch)
  - Any execution problem you can't explain in 24h (failed fill, wrong price, exchange downtime, etc.)
- **Daily routine during Phase 1:** check the tracker AND the live wallet ONCE per day at the same time. Reconcile.

## Phase 2 — scale up (only after Phase 1 fully clean)

- Phase 2 capital: up to €5 000.
- Duration: 60 days.
- Same kill switches.
- After clean Phase 2: full €10k (or scale toward €25-30k for the €500/mo target).

## Things that DO NOT count as evidence to go live

- "It feels like it's working."
- A great week or month.
- A friend / Telegram channel showing better numbers.
- "Just a small test with €5k" — that's not Phase 1, that's emotional override.
- A backtest that suddenly looks better after a parameter tweak.

## Things that ARE evidence to walk away

- Forward Sharpe negative after 60 days.
- Two consecutive months down > 5% each.
- Backtest stopped representing live by > 50% return gap in either direction.
- Realised you can't actually afford to lose this money.

---

Last reviewed: 2026-05-20. If you change any threshold, log it here with
date and reason — and add 30 days to the minimum forward duration as a
penalty for goalpost-moving.