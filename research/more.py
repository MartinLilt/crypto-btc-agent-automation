"""
Round 2 strategy search — TSMOM, multi-factor XS, let-winners-run, BTC lead-lag.

Same discipline: full vs OOS (last 40%), and the decision metric is
INCREMENTAL value — does adding the sleeve to the deployed 50/50 book raise
its OOS Sharpe / cut its drawdown? A standalone-great sleeve that doesn't
diversify the book is worthless here.
"""
from __future__ import annotations

import sys
import numpy as np
import pandas as pd

import research.data as D
import research.strategies as S
from research.engine import portfolio, metrics, fmt

BPY = 365.0
SPLIT = 0.60
CONS12 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT",
          "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LTCUSDT", "ATOMUSDT"]


def _px(syms):
    out = {}
    for s in syms:
        d = D.load(s, "1d")
        if not d.empty and len(d) > 400:
            out[s] = d.set_index("dt")["c"]
    return out


def _vt(net, target=0.30, cap=1.5):
    rv = net.rolling(30).std() * np.sqrt(BPY)
    return net * (target / rv).clip(upper=cap).shift(1).fillna(0.0)


def _oos(series, dt):
    k = int(len(series) * SPLIT)
    return metrics(series.iloc[k:], dt.iloc[k:], BPY), metrics(series, dt, BPY)


# ── A. Time-series momentum ensemble (managed-futures, long-only spot) ─────────

def tsmom(px, lbs=(20, 40, 60, 120)):
    idx = None
    for s in px:
        idx = px[s].index if idx is None else idx.union(px[s].index)
    idx = idx.sort_values()
    P = pd.DataFrame({s: px[s].reindex(idx) for s in px})
    btc = P["BTCUSDT"]
    mkt = btc > btc.rolling(100).mean()
    sig = sum((P / P.shift(L) - 1 > 0).astype(float) for L in lbs) / len(lbs)
    iv = 1.0 / P.pct_change().rolling(30).std()
    raw = (sig * iv)
    raw = raw.div(raw.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    W = raw.mul(mkt.astype(float), axis=0)
    return {s: W[s] for s in P.columns}, P


# ── B. Multi-factor cross-section (momentum + low-vol [+ carry]) ──────────────

def multifactor(px, lookback=25, hold=5, top_k=3, use_carry=True):
    idx = None
    for s in px:
        idx = px[s].index if idx is None else idx.union(px[s].index)
    idx = idx.sort_values()
    P = pd.DataFrame({s: px[s].reindex(idx) for s in px})
    mom = P / P.shift(lookback) - 1
    lowvol = -P.pct_change().rolling(30).std()
    z = lambda d: (d.sub(d.mean(axis=1), axis=0)
                   .div(d.std(axis=1).replace(0, np.nan), axis=0))
    comp = z(mom) + 0.5 * z(lowvol)
    if use_carry:
        fr = {}
        for s in px:
            f = D.load_funding(s)
            if not f.empty:
                fr[s] = f.set_index("dt")["fr"].resample("1D").sum().reindex(idx)
        if fr:
            F = pd.DataFrame(fr).reindex(columns=P.columns)
            comp = comp + 0.5 * z(F.rolling(14).mean())
    trend_ok = P > P.rolling(100).mean()
    btc = P["BTCUSDT"]
    mkt = btc > btc.rolling(100).mean()
    W = pd.DataFrame(0.0, index=idx, columns=P.columns)
    last, cur = -10**9, pd.Series(0.0, index=P.columns)
    for i, t in enumerate(idx):
        if i - last >= hold:
            last = i
            c = comp.loc[t].copy()
            c[~trend_ok.loc[t].fillna(False)] = np.nan
            c[P.loc[t].isna()] = np.nan
            r = c.dropna().sort_values(ascending=False)
            cur = pd.Series(0.0, index=P.columns)
            if mkt.loc[t] and len(r):
                cur[r.index[:top_k]] = 1.0 / min(top_k, len(r))
        W.loc[t] = cur if mkt.loc[t] else 0.0
    return {s: W[s] for s in P.columns}, P


# ── C. Let-winners-run rotation (ATR-trail exit, the user's "ride the spike") ──

def winners_run(px, lookback=25, top_k=3, atr_n=20, atr_mult=4.0):
    idx = None
    for s in px:
        idx = px[s].index if idx is None else idx.union(px[s].index)
    idx = idx.sort_values()
    P = pd.DataFrame({s: px[s].reindex(idx) for s in px})
    mom = P / P.shift(lookback) - 1
    vol = P.pct_change().rolling(atr_n).std()           # proxy ATR%
    btc = P["BTCUSDT"]
    mkt = (btc > btc.rolling(100).mean()).values
    cols = list(P.columns)
    W = pd.DataFrame(0.0, index=idx, columns=cols)
    held: dict[str, float] = {}                          # sym -> peak price
    Pv = P.values
    Mv = mom.values
    Vv = vol.values
    cidx = {c: j for j, c in enumerate(cols)}
    for i, t in enumerate(idx):
        # exits: trailing stop or market off
        for s in list(held):
            j = cidx[s]
            p = Pv[i, j]
            if np.isnan(p):
                held.pop(s); continue
            held[s] = max(held[s], p)
            stop = held[s] * (1 - atr_mult * (Vv[i, j] if not np.isnan(Vv[i, j]) else 0.05))
            if (not mkt[i]) or p < stop:
                held.pop(s)
        # entries: top_k momentum names not already held
        if mkt[i]:
            row = pd.Series(Mv[i], index=cols)
            row[P.iloc[i].isna().values] = np.nan
            rank = row.dropna().sort_values(ascending=False)
            for s in rank.index[:top_k]:
                if s not in held and len(held) < top_k:
                    held[s] = Pv[i, cidx[s]]
        if held:
            w = 1.0 / len(held)
            for s in held:
                W.iat[i, cidx[s]] = w
    return {s: W[s] for s in cols}, P


# ── D. BTC lead-lag gate on the rotation ──────────────────────────────────────

def rotation_btc_leadlag(px, lookback=25, hold=5, top_k=3, btc_lb=5):
    W, idx = S.xsec_momentum(px, mkt_regime=True,
                             lookback=lookback, hold=hold, top_k=top_k)
    btc = px["BTCUSDT"].reindex(W["BTCUSDT"].index)
    gate = (btc / btc.shift(btc_lb) - 1 > 0).astype(float).reindex(W["BTCUSDT"].index).fillna(0.0)
    return {s: W[s] * gate.values for s in W}, idx


# ── Market-neutral relative-value sleeves (the only path that can stack) ──────

def carry_bestN(symbols, top_n=4):
    """Daily: engage long-spot/short-perp only on the N richest-funding perps.
    Higher-capacity, still delta-neutral. Returns a daily PnL Series."""
    fr = {}
    for s in symbols:
        f = D.load_funding(s)
        if f.empty:
            f = D.refresh_funding(s)
        if not f.empty:
            fr[s] = f.set_index("dt")["fr"].resample("1D").sum()
    if not fr:
        return pd.Series(dtype=float)
    F = pd.DataFrame(fr).sort_index()
    sig = F.rolling(7).mean()
    rank = sig.rank(axis=1, ascending=False)
    eng = (rank <= top_n).astype(float).shift(1).fillna(0.0)
    eng = eng[(sig.shift(1) > 0)]                       # only when actually +
    eng = eng.reindex(F.index).fillna(0.0)
    toggle = eng.diff().abs().fillna(0.0)
    pnl = (eng * F - toggle * 0.0012).sum(axis=1) / top_n
    return pnl


def pair_ethbtc():
    """Market-neutral ETH/BTC ratio mean-reversion (z-score of log ratio)."""
    e = D.load("ETHUSDT", "1d").set_index("dt")["c"]
    b = D.load("BTCUSDT", "1d").set_index("dt")["c"]
    ix = e.index.union(b.index).sort_values()
    e, b = e.reindex(ix).ffill(), b.reindex(ix).ffill()
    ratio = np.log(e / b)
    z = (ratio - ratio.rolling(30).mean()) / ratio.rolling(30).std()
    pos = (-z.clip(-3, 3) / 3.0).shift(1).fillna(0.0)    # fade extremes
    re, rb = e.pct_change().fillna(0), b.pct_change().fillna(0)
    spread_ret = pos * (re - rb)
    cost = pos.diff().abs().fillna(0) * 0.0012
    return pd.Series((spread_ret - cost).values, index=ix)


def eval_series(name, ser, dt, book_net, book_dt):
    ser = pd.Series(ser.values, index=pd.DatetimeIndex(
        pd.to_datetime(pd.Series(dt).values, utc=True)))
    k = int(len(ser) * SPLIT)
    print(f"\n  [{name}]")
    print(f"    FULL {fmt(metrics(ser, pd.Series(ser.index), BPY))}")
    print(f"    OOS  {fmt(metrics(ser.iloc[k:], pd.Series(ser.index[k:]), BPY))}")
    bi = pd.DatetimeIndex(pd.to_datetime(pd.Series(book_dt).values, utc=True))
    ix = bi.union(ser.index).sort_values()
    b = pd.Series(book_net.values, index=bi).reindex(ix).fillna(0.0)
    s = ser.reindex(ix).fillna(0.0)
    k = int(len(ix) * SPLIT)
    base = metrics(b.iloc[k:], pd.Series(ix[k:]), BPY)
    best = None
    for wn in (0.2, 0.3, 0.4, 0.5):
        mo = metrics(((1 - wn) * b + wn * s).iloc[k:], pd.Series(ix[k:]), BPY)
        if best is None or mo["sharpe"] > best[1]["sharpe"]:
            best = (wn, mo)
    wn, mo = best
    tag = ("ADDS edge" if mo["sharpe"] > base["sharpe"] + 0.03
           and mo["maxdd"] <= base["maxdd"] + 0.02 else "no real add")
    print(f"    book alone OOS: Shrp {base['sharpe']:.2f} DD {base['maxdd']*100:.1f}% "
          f"CAGR {base['cagr']*100:.1f}%")
    print(f"    + {int(wn*100)}% sleeve OOS: Shrp {mo['sharpe']:.2f} "
          f"DD {mo['maxdd']*100:.1f}% CAGR {mo['cagr']*100:.1f}%  → {tag}")
    corr = pd.concat([b, s], axis=1).iloc[k:].corr().iloc[0, 1]
    print(f"    corr(sleeve, book) OOS = {corr:+.2f}  "
          f"({'uncorrelated → can stack' if abs(corr) < 0.3 else 'correlated'})")


def evaluate(name, Wdict, px, book_net=None, book_dt=None):
    pf = portfolio(Wdict, px, cost_bps=15.0, bars_per_year=BPY, lev_cap=1.0)
    vt = _vt(pf["net"])
    oo, fu = _oos(vt, pf["dt"])
    print(f"\n  [{name}]")
    print(f"    FULL {fmt(fu)}")
    print(f"    OOS  {fmt(oo)}")
    if book_net is not None:
        bi = pd.DatetimeIndex(pd.to_datetime(pd.Series(book_dt).values, utc=True))
        si = pd.DatetimeIndex(pd.to_datetime(pf["dt"].values, utc=True))
        ix = bi.union(si).sort_values()
        b = pd.Series(book_net.values, index=bi).reindex(ix).fillna(0.0)
        s = pd.Series(vt.values, index=si).reindex(ix).fillna(0.0)
        k = int(len(ix) * SPLIT)
        base = metrics(b.iloc[k:], pd.Series(ix[k:]), BPY)
        best = None
        for wn in (0.2, 0.3, 0.4, 0.5):
            comb = (1 - wn) * b + wn * s
            mo = metrics(comb.iloc[k:], pd.Series(ix[k:]), BPY)
            if best is None or mo["sharpe"] > best[1]["sharpe"]:
                best = (wn, mo)
        wn, mo = best
        tag = ("ADDS edge" if mo["sharpe"] > base["sharpe"] + 0.03
               and mo["maxdd"] <= base["maxdd"] + 0.02 else "no real add")
        print(f"    book alone OOS: Shrp {base['sharpe']:.2f} DD {base['maxdd']*100:.1f}% "
              f"CAGR {base['cagr']*100:.1f}%")
        print(f"    + {int(wn*100)}% sleeve  OOS: Shrp {mo['sharpe']:.2f} "
              f"DD {mo['maxdd']*100:.1f}% CAGR {mo['cagr']*100:.1f}%  → {tag}")


if __name__ == "__main__":
    from research.book import book_series
    px = _px(CONS12)
    bs = book_series(refresh=False)
    bn, bd = bs["ret"], bs["dt"]
    print("=" * 80)
    print("ROUND 2 — new families vs deployed 50/50 book (incremental value)")
    print("=" * 80)
    Wa, _ = tsmom(px);                    evaluate("A TSMOM ensemble", Wa, px, bn, bd)
    Wb, _ = multifactor(px);              evaluate("B multi-factor XS", Wb, px, bn, bd)
    Wc, _ = winners_run(px);              evaluate("C let-winners-run", Wc, px, bn, bd)
    Wd, _ = rotation_btc_leadlag(px);     evaluate("D BTC lead-lag rot", Wd, px, bn, bd)
    print("\n" + "=" * 80)
    print("ROUND 3 — market-neutral relative-value (the only thing that can stack)")
    print("=" * 80)
    cb = carry_bestN(CONS12, top_n=4)
    eval_series("E carry best-4 of 12 (funding dispersion)", cb,
                pd.Series(cb.index), bn, bd)
    pe = pair_ethbtc()
    eval_series("F ETH/BTC pair mean-reversion", pe,
                pd.Series(pe.index), bn, bd)