"""
Hardened 50/50 book vs deployed — defensive, not return-chasing.

Hardening: top_k 3→5 (smaller per-name weight), hard per-position stop
(exit a held name if it falls > STOP from entry), liquidity floor (only hold
names whose 30d median $-volume ≥ MINVOL). Same carry sleeve, same vol-target.

Honest split of the single-alt overnight-collapse tail into:
  • gap-day impact  — only position SIZE helps (1/5 vs 1/3); stop can't
  • post-gap path    — the hard stop caps further bleed
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import research.data as D
from research.engine import portfolio, metrics
from research.book import (_prices, ROT, VOL_TARGET, LEV_CAP, CARRY,
                           CARRY_THR, BLEND_ROT, BLEND_CARRY, CONS12)

RNG = np.random.default_rng(11)
STOP = 0.15          # hard per-position stop from entry (−15%)
MINVOL = 5e6         # 30d median daily quote-volume floor (USDT)


def _qv(syms):
    out = {}
    for s in syms:
        d = D.load(s, "1d")
        if not d.empty:
            out[s] = d.set_index("dt")["qv"]
    return out


def hardened_rotation(px, qv, lookback=25, hold=5, top_k=5,
                      stop=STOP, minvol=MINVOL):
    idx = None
    for s in px:
        idx = px[s].index if idx is None else idx.union(px[s].index)
    idx = idx.sort_values()
    P = pd.DataFrame({s: px[s].reindex(idx) for s in px})
    QV = pd.DataFrame({s: qv[s].reindex(idx) for s in px if s in qv})
    mom = P / P.shift(lookback) - 1
    trend_ok = P > P.rolling(100).mean()
    liq_ok = QV.rolling(30).median() >= minvol
    btc = P["BTCUSDT"]
    mkt = (btc > btc.rolling(100).mean()).values
    cols = list(P.columns)
    ci = {c: j for j, c in enumerate(cols)}
    Pv, Mv = P.values, mom.values
    Tv = trend_ok.reindex(columns=cols).values
    Lv = liq_ok.reindex(columns=cols).fillna(False).values
    W = np.zeros((len(idx), len(cols)))
    held: dict[str, float] = {}                    # sym -> entry price
    last = -10**9
    for i in range(len(idx)):
        # hard stop / regime / liquidity exits (checked daily)
        for s in list(held):
            j = ci[s]
            p = Pv[i, j]
            if (np.isnan(p) or not mkt[i] or not Lv[i, j]
                    or p <= held[s] * (1 - stop)):
                held.pop(s)
        # periodic rebalance
        if i - last >= hold and mkt[i]:
            last = i
            row = pd.Series(Mv[i], index=cols)
            bad = (~Tv[i]) | (~Lv[i]) | P.iloc[i].isna().values
            row[bad] = np.nan
            rank = row.dropna().sort_values(ascending=False)
            target = list(rank.index[:top_k])
            for s in list(held):
                if s not in target:
                    held.pop(s)
            for s in target:
                if s not in held and len(held) < top_k:
                    held[s] = Pv[i, ci[s]]
        if held:
            w = 1.0 / top_k                        # cash if < top_k held
            for s in held:
                W[i, ci[s]] = w
    Wd = {c: pd.Series(W[:, ci[c]], index=idx) for c in cols}
    return Wd, idx


def _carry():
    cr = {}
    for s in CARRY:
        f = D.load_funding(s)
        if f.empty:
            continue
        dd = f.set_index("dt")["fr"].resample("1D").sum()
        eng = (dd.rolling(7).mean() > CARRY_THR).astype(float).shift(1).fillna(0.0)
        cr[s] = eng * dd - eng.diff().abs().fillna(0.0) * 0.0012
    return pd.DataFrame(cr).fillna(0.0).mean(axis=1)


def book_from_rotation(Wd, px):
    pf = portfolio(Wd, px, cost_bps=15.0, bars_per_year=365.0, lev_cap=1.0)
    net = pf["net"]
    rv = net.rolling(30).std() * np.sqrt(365.0)
    rot = net * (VOL_TARGET / rv).clip(upper=LEV_CAP).shift(1).fillna(0.0)
    rot.index = pd.to_datetime(pf["dt"].values, utc=True)
    carry = _carry()
    ix = rot.index.union(carry.index).sort_values()
    r = rot.reindex(ix).fillna(0.0)
    c = carry.reindex(ix).fillna(0.0)
    comb = BLEND_ROT * r + BLEND_CARRY * c
    return pd.DataFrame({"dt": ix, "ret": comb.values,
                         "rot": r.values, "carry": c.values})


def _dd(r):
    eq = (1 + r).cumprod()
    return float((1 - eq / eq.cummax()).max())


def report(label, bs, top_k):
    ix = pd.to_datetime(bs["dt"], utc=True)
    ret = pd.Series(bs["ret"].values, index=ix)
    k = int(len(ret) * 0.6)
    m = metrics(ret.iloc[k:], pd.Series(ix[k:]), 365.0)
    mf = metrics(ret, pd.Series(ix), 365.0)
    print(f"\n[{label}]")
    print(f"  OOS  CAGR={m['cagr']*100:6.1f}%  Sharpe={m['sharpe']:.2f}  "
          f"maxDD={m['maxdd']*100:5.1f}%  mo_mean={m['mo_mean']*100:.2f}%  "
          f"mo_hit={m['mo_hit']*100:.0f}%")
    print(f"  FULL CAGR={mf['cagr']*100:6.1f}%  Sharpe={mf['sharpe']:.2f}  "
          f"maxDD={mf['maxdd']*100:5.1f}%")
    # single-alt overnight collapse, split honestly
    one_w = BLEND_ROT * 1.0 / top_k
    for gap in (-0.70, -0.90):
        gd, dd = [], []
        for _ in range(4000):
            cc = ret.copy()
            d = ix[RNG.integers(200, len(ix))]
            cc.loc[d] += one_w * gap                    # gap day (size only)
            gd.append(cc.min())
            dd.append(_dd(cc))
        print(f"  alt {gap*100:.0f}% overnight: worst day(5pct) "
              f"{np.percentile(gd,5)*100:6.1f}%  tail maxDD(95pct) "
              f"{np.percentile(dd,95)*100:5.1f}%  "
              f"(€{np.percentile(dd,95)*10000:.0f})")
    # Monte-Carlo 1y
    rr = ret.values
    rr = rr[~np.isnan(rr)]
    nb = 365 // 20 + 1
    st = RNG.integers(0, len(rr) - 20, size=(8000, nb))
    ann = np.array([np.prod(1 + np.concatenate([rr[s:s+20] for s in st[i]])[:365]) - 1
                    for i in range(8000)])
    print(f"  MC 1y: median {np.percentile(ann,50)*100:+.1f}%  "
          f"5pct {np.percentile(ann,5)*100:+.1f}%  "
          f"P(loss)={(ann<0).mean()*100:.1f}%")


if __name__ == "__main__":
    px = _prices(False)
    qv = _qv(CONS12)
    print("=" * 74)
    print("HARDENED vs DEPLOYED 50/50 book")
    print(f"hardening: top_k 3→5, hard stop −{STOP*100:.0f}% from entry, "
          f"liquidity floor ${MINVOL/1e6:.0f}M/30d")
    print("=" * 74)
    # deployed: top_k=3, no stop, no liq filter (use book.book_series logic)
    from research.book import book_series
    report("DEPLOYED  top3, no stop", book_series(refresh=False), top_k=3)
    Wh, _ = hardened_rotation(px, qv, lookback=ROT["lookback"],
                              hold=ROT["hold"], top_k=5)
    report("HARDENED  top5+stop+liq", book_from_rotation(Wh, px), top_k=5)
    # sensitivity: hardened top5 but stop variants
    for stp in (0.10, 0.20, 0.25):
        Ws, _ = hardened_rotation(px, qv, lookback=ROT["lookback"],
                                  hold=ROT["hold"], top_k=5, stop=stp)
        report(f"HARDENED  top5, stop −{stp*100:.0f}%",
                book_from_rotation(Ws, px), top_k=5)