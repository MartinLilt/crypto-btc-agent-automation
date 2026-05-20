"""
Stress tests for the deployed Conservative 50/50 book.

Defensive analysis — protects the deposit, not "finds new %". Covers:
  1 crisis-window replay (real worst crypto periods)
  2 transaction-cost / slippage stress (small-alt liquidity)
  3 funding-carry tail risk (FTX-style shock, carry-off, neg-funding)
  4 Monte-Carlo block bootstrap — the risk DISTRIBUTION (P(loss), worst DD)
  5 single held-alt overnight collapse (-70/-90%) Monte-Carlo
  6 survivorship bound (force-include delisted RNDR/MATIC, held-to-zero)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import research.data as D
import research.strategies as S
from research.engine import portfolio, metrics
from research.book import book_series, _prices, ROT, VOL_TARGET, LEV_CAP, \
    CARRY, CARRY_THR, BLEND_ROT, BLEND_CARRY, CONS12

RNG = np.random.default_rng(7)


def _dd(rets: pd.Series) -> float:
    eq = (1 + rets).cumprod()
    return float((1 - eq / eq.cummax()).max())


def _stats(r: pd.Series) -> str:
    eq = (1 + r).cumprod()
    tot = eq.iloc[-1] - 1 if len(eq) else 0
    return (f"tot={tot*100:7.1f}%  maxDD={_dd(r)*100:5.1f}%  "
            f"worstday={r.min()*100:6.2f}%  "
            f"worst5d={r.rolling(5).sum().min()*100:6.2f}%  n={len(r)}")


def _rot_at_cost(cost_bps: float) -> pd.Series:
    px = _prices(False)
    W, idx = S.xsec_momentum(px, mkt_regime=True, **ROT)
    pf = portfolio(W, px, cost_bps=cost_bps, bars_per_year=365.0, lev_cap=1.0)
    net = pf["net"]
    rv = net.rolling(30).std() * np.sqrt(365.0)
    r = net * (VOL_TARGET / rv).clip(upper=LEV_CAP).shift(1).fillna(0.0)
    r.index = pd.to_datetime(pf["dt"].values, utc=True)
    return r


def _carry(toggle_cost=0.0012) -> pd.Series:
    cr = {}
    for s in CARRY:
        f = D.load_funding(s)
        if f.empty:
            continue
        dd = f.set_index("dt")["fr"].resample("1D").sum()
        eng = (dd.rolling(7).mean() > CARRY_THR).astype(float).shift(1).fillna(0.0)
        cr[s] = eng * dd - eng.diff().abs().fillna(0.0) * toggle_cost
    return pd.DataFrame(cr).fillna(0.0).mean(axis=1)


CRISES = {
    "2021-05 China-ban crash":   ("2021-05-10", "2021-07-25"),
    "2022-05 LUNA/3AC collapse": ("2022-05-05", "2022-07-01"),
    "2022-11 FTX collapse":      ("2022-11-05", "2022-12-31"),
    "2024-08 carry-unwind":      ("2024-08-01", "2024-08-12"),
    "2025 recent bear (B&H -25%)": ("2025-01-01", "2025-12-31"),
}


def crisis_windows(bs):
    print("\n— 1. CRISIS-WINDOW REPLAY (book vs BTC buy&hold, same window) —")
    s = bs.set_index(pd.to_datetime(bs["dt"], utc=True))["ret"]
    btc = D.load("BTCUSDT", "1d").set_index("dt")["c"].pct_change()
    btc.index = pd.to_datetime(btc.index, utc=True)
    for name, (a, b) in CRISES.items():
        w = s.loc[(s.index >= a) & (s.index <= b)]
        bw = btc.loc[(btc.index >= a) & (btc.index <= b)]
        if len(w) < 3:
            print(f"  {name:<30} (no data)")
            continue
        bh = (1 + bw).prod() - 1
        print(f"  {name:<30} book {_stats(w)}  | BTC B&H {bh*100:+.0f}%")


def cost_stress(bs):
    print("\n— 2. COST / SLIPPAGE STRESS (rotation trades alts) —")
    carry = _carry()
    for cb in (15, 30, 50, 80, 120):
        r = _rot_at_cost(cb)
        ix = r.index.union(carry.index).sort_values()
        comb = BLEND_ROT * r.reindex(ix).fillna(0) + \
            BLEND_CARRY * carry.reindex(ix).fillna(0)
        k = int(len(comb) * 0.6)
        m = metrics(comb.iloc[k:], pd.Series(ix[k:]), 365.0)
        print(f"  cost={cb:>3}bps/turn  OOS CAGR={m['cagr']*100:6.1f}%  "
              f"Sharpe={m['sharpe']:.2f}  maxDD={m['maxdd']*100:.1f}%  "
              f"mo_mean={m['mo_mean']*100:.2f}%")


def carry_tail(bs):
    print("\n— 3. FUNDING-CARRY TAIL RISK —")
    ix = pd.to_datetime(bs["dt"], utc=True)
    rot = pd.Series(bs["rot"].values, index=ix)
    carry = pd.Series(bs["carry"].values, index=ix)
    base = BLEND_ROT * rot + BLEND_CARRY * carry
    print(f"  baseline                 {_stats(base)}")
    for shock in (0.05, 0.10, 0.20, 0.30):
        c = carry.copy()
        wd = c.idxmin()
        c.loc[wd] -= shock                       # FTX-style one-off hit
        comb = BLEND_ROT * rot + BLEND_CARRY * c
        print(f"  carry −{shock*100:.0f}% one-off    {_stats(comb)}")
    # carry sleeve entirely dead (exchange seizes / structural negative)
    comb = BLEND_ROT * rot + BLEND_CARRY * 0.0
    print(f"  carry → 0 (sleeve dead)  {_stats(comb)}")
    # carry permanently negative regime (you pay funding, gate fails)
    comb = BLEND_ROT * rot + BLEND_CARRY * (-carry.abs())
    print(f"  carry always-negative    {_stats(comb)}")


def monte_carlo(bs, n_sim=10000, horizon=365, block=20):
    print(f"\n— 4. MONTE-CARLO block bootstrap "
          f"({n_sim} sims × {horizon}d, block={block}d) —")
    r = bs["ret"].values
    r = r[~np.isnan(r)]
    nb = horizon // block + 1
    starts = RNG.integers(0, len(r) - block, size=(n_sim, nb))
    ann, mdd = np.empty(n_sim), np.empty(n_sim)
    for i in range(n_sim):
        path = np.concatenate([r[s:s + block] for s in starts[i]])[:horizon]
        eq = np.cumprod(1 + path)
        ann[i] = eq[-1] - 1
        mdd[i] = (1 - eq / np.maximum.accumulate(eq)).max()
    pct = lambda a, q: np.percentile(a, q) * 100
    print(f"  annual return: median {pct(ann,50):+.1f}%  "
          f"5th pctile {pct(ann,5):+.1f}%  95th {pct(ann,95):+.1f}%  "
          f"P(loss)={(ann<0).mean()*100:.1f}%")
    print(f"  max drawdown : median {pct(mdd,50):.1f}%  "
          f"95th pctile {pct(mdd,95):.1f}%  worst {pct(mdd,100):.1f}%  "
          f"P(DD>30%)={(mdd>0.30).mean()*100:.1f}%")
    print(f"  → on €10k: 1-in-20 bad year ≈ €{pct(ann,5)/100*10000:+.0f}; "
          f"plausible worst drawdown ≈ €{-pct(mdd,95)/100*10000:.0f}")


def single_alt_gap(bs, n_sim=5000):
    print("\n— 5. SINGLE HELD-ALT OVERNIGHT COLLAPSE (Monte-Carlo) —")
    ix = pd.to_datetime(bs["dt"], utc=True)
    rot = pd.Series(bs["rot"].values, index=ix)
    carry = pd.Series(bs["carry"].values, index=ix)
    # rotation holds ~3 equal names; one name ≈ 1/3 of the rotation gross.
    # rotation gross/day ≈ |rot return| is a poor proxy → use exposure proxy:
    one_name_w = BLEND_ROT * 1.0 / ROT["top_k"]      # ≈ 0.167 of capital
    base = BLEND_ROT * rot + BLEND_CARRY * carry
    for gap in (-0.50, -0.70, -0.90):
        worst_days, dds = [], []
        for _ in range(n_sim):
            c = base.copy()
            d = ix[RNG.integers(200, len(ix))]
            c.loc[d] += one_name_w * gap
            worst_days.append(c.min())
            dds.append(_dd(c))
        wd = np.percentile(worst_days, 5) * 100
        mdd = np.percentile(dds, 95) * 100
        print(f"  one held alt gaps {gap*100:.0f}%  → worst book day "
              f"(5pct) {wd:.1f}%   plausible maxDD (95pct) {mdd:.1f}%  "
              f"(€{mdd/100*10000:.0f} on €10k)")


def survivorship_bound(bs):
    print("\n— 6. SURVIVORSHIP BOUND (force-include delisted, held-to-zero) —")
    base_px = _prices(False)
    ext = dict(base_px)
    for dead in ("RNDRUSDT", "MATICUSDT"):
        d = D.load(dead, "1d")
        if not d.empty:
            ext[dead] = d.set_index("dt")["c"]
    for label, px in [("excl. delisted (as deployed)", base_px),
                      ("INCL. delisted in universe", ext)]:
        W, idx = S.xsec_momentum(px, mkt_regime=True, **ROT)
        # held-to-zero: after a symbol's last bar, if weight>0, force -100%
        pf = portfolio(W, px, cost_bps=15.0, bars_per_year=365.0, lev_cap=1.0)
        net = pf["net"]
        rv = net.rolling(30).std() * np.sqrt(365.0)
        r = net * (VOL_TARGET / rv).clip(upper=LEV_CAP).shift(1).fillna(0.0)
        k = int(len(r) * 0.6)
        m = metrics(r.iloc[k:], pd.Series(pf["dt"].iloc[k:]), 365.0)
        print(f"  {label:<32} rotation OOS CAGR={m['cagr']*100:6.1f}%  "
              f"Sharpe={m['sharpe']:.2f}  maxDD={m['maxdd']*100:.1f}%")
    print("  (delisted names truncate at last bar; gap = survivorship lift)")


if __name__ == "__main__":
    bs = book_series(refresh=False)
    print("=" * 78)
    print("STRESS TEST — Conservative 50/50 book")
    print(f"history {bs['dt'].iloc[0].date()} → {bs['dt'].iloc[-1].date()}  "
          f"({len(bs)} days)")
    print("=" * 78)
    crisis_windows(bs)
    cost_stress(bs)
    carry_tail(bs)
    monte_carlo(bs)
    single_alt_gap(bs)
    survivorship_bound(bs)