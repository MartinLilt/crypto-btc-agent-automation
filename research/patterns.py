"""
Candlestick pattern mining — with anti-overfit discipline.

For each pattern: sample size, forward-return distribution at horizon H,
EXCESS over the unconditional base rate (the honest metric — beating "just
being in the market"), in-sample vs out-of-sample, and a PERMUTATION p-value
(same number of random signals, 2000 shuffles) which is robust to fat tails
and autocorrelation. Costs (0.2% round trip) shown so "tradeable" is honest.
Multiple-testing: we report how many patterns *should* look significant by
chance so you can judge the survivors.

No lookahead: a pattern at bar i uses bars ≤ i; the trade is entered at the
OPEN of bar i+1 and exited at the close of bar i+H.
"""
from __future__ import annotations

import sys
import numpy as np
import pandas as pd

import research.data as D

RNG = np.random.default_rng(42)
COST = 0.002  # 0.2% round-trip (Binance taker both sides)


# ── candle feature primitives (vectorized, no lookahead) ──────────────────────

def feats(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["o"], df["h"], df["l"], df["c"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o)
    f = pd.DataFrame(index=df.index)
    f["o"], f["h"], f["l"], f["c"], f["v"] = o, h, l, c, df["v"]
    f["body"] = body
    f["body_abs"] = body.abs()
    f["body_frac"] = (body.abs() / rng).fillna(0)
    f["up_wick"] = (h - c.where(c > o, o)) / rng
    f["dn_wick"] = (c.where(c < o, o) - l) / rng
    f["ret"] = c.pct_change()
    f["bull"] = (c > o).astype(int)
    f["clr"] = c.pct_change().rolling(2).sum()
    f["ema20"] = c.ewm(span=20, adjust=False).mean()
    f["sma200"] = c.rolling(200).mean()
    f["regime_up"] = (c > f["sma200"]).astype(int)
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    f["rsi"] = (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50)
    f["atr"] = (pd.concat([h - l, (h - c.shift()).abs(),
                           (l - c.shift()).abs()], axis=1).max(axis=1)
                .ewm(alpha=1 / 14, adjust=False).mean())
    return f


def named_patterns(f: pd.DataFrame) -> dict:
    """Return {name: boolean Series} for classic candlestick patterns."""
    o, h, l, c = f["o"], f["h"], f["l"], f["c"]
    body = c - o
    bsz = body.abs()
    rng = (h - l).replace(0, np.nan)
    rng_m = rng.rolling(20).mean()
    o1, c1, h1, l1 = o.shift(1), c.shift(1), h.shift(1), l.shift(1)
    b1 = (c1 - o1)
    o2, c2 = o.shift(2), c.shift(2)
    up = f["up_wick"]
    dn = f["dn_wick"]
    P = {}
    P["bull_engulf"] = (b1 < 0) & (body > 0) & (c > o1) & (o < c1)
    P["bear_engulf"] = (b1 > 0) & (body < 0) & (o > c1) & (c < o1)
    P["hammer"] = (dn > 0.55) & (up < 0.15) & (bsz / rng < 0.35)
    P["inv_hammer"] = (up > 0.55) & (dn < 0.15) & (bsz / rng < 0.35)
    P["shooting_star"] = P["inv_hammer"] & (c1 > o1)
    P["hanging_man"] = P["hammer"] & (c1 > o1)
    P["doji"] = (bsz / rng < 0.1)
    P["marubozu_bull"] = (body > 0) & (f["body_frac"] > 0.93)
    P["marubozu_bear"] = (body < 0) & (f["body_frac"] > 0.93)
    P["3w_soldiers"] = ((c > o) & (c.shift(1) > o.shift(1)) &
                        (c.shift(2) > o.shift(2)) & (c > c1) & (c1 > c2))
    P["3b_crows"] = ((c < o) & (c.shift(1) < o.shift(1)) &
                     (c.shift(2) < o.shift(2)) & (c < c1) & (c1 < c2))
    P["bull_harami"] = (b1 < 0) & (body > 0) & (o > c1) & (c < o1)
    P["bear_harami"] = (b1 > 0) & (body < 0) & (o < c1) & (c > o1)
    P["morning_star"] = ((c2 < o2) & (bsz.shift(1) / rng_m.shift(1) < 0.4) &
                         (body > 0) & (c > (o2 + c2) / 2))
    P["evening_star"] = ((c2 > o2) & (bsz.shift(1) / rng_m.shift(1) < 0.4) &
                         (body < 0) & (c < (o2 + c2) / 2))
    P["tweezer_bottom"] = (abs(l - l1) / rng < 0.05) & (b1 < 0) & (body > 0)
    P["tweezer_top"] = (abs(h - h1) / rng < 0.05) & (b1 > 0) & (body < 0)
    P["inside_bar"] = (h < h1) & (l > l1)
    P["outside_bar"] = (h > h1) & (l < l1)
    P["pin_bar_bull"] = (dn > 0.66) & (bsz / rng < 0.3)
    P["pin_bar_bear"] = (up > 0.66) & (bsz / rng < 0.3)
    P["gap_up"] = (o > h1)
    P["gap_dn"] = (o < l1)
    P["big_green"] = (body > 0) & (bsz > 1.5 * rng_m)
    P["big_red"] = (body < 0) & (bsz > 1.5 * rng_m)
    # context-conditioned (regime / oversold) variants of the strongest two
    P["hammer@dip"] = P["hammer"] & (f["rsi"] < 35) & (f["regime_up"] == 1)
    P["bull_engulf@dip"] = P["bull_engulf"] & (f["rsi"] < 40)
    P["3w_soldiers@up"] = P["3w_soldiers"] & (f["regime_up"] == 1)
    return {k: v.fillna(False) for k, v in P.items()}


def fwd_ret(f: pd.DataFrame, H: int) -> pd.Series:
    """Enter at open[i+1], exit at close[i+1+H-1]; minus round-trip cost."""
    entry = f["o"].shift(-1)
    exit_ = f["c"].shift(-H)
    return (exit_ / entry - 1) - COST


def perm_pvalue(mask: np.ndarray, r: np.ndarray, obs_mean: float,
                n_iter: int = 2000) -> float:
    """Empirical p: how often a RANDOM signal of the same count beats obs."""
    n = mask.sum()
    if n < 5:
        return 1.0
    idx = np.flatnonzero(~np.isnan(r))
    r = r[idx]
    if len(r) < n + 5:
        return 1.0
    hits = 0
    for _ in range(n_iter):
        s = RNG.choice(len(r), size=n, replace=False)
        if r[s].mean() >= obs_mean:
            hits += 1
    return (hits + 1) / (n_iter + 1)


def analyse(f: pd.DataFrame, H: int, split: float = 0.6) -> pd.DataFrame:
    r = fwd_ret(f, H)
    base = r.mean()
    pats = named_patterns(f)
    k = int(len(f) * split)
    rows = []
    rv = r.values
    for name, m in pats.items():
        mv = m.values & ~np.isnan(rv)
        n = int(mv.sum())
        if n < 20:
            continue
        sel = rv[mv]
        mean = float(np.nanmean(sel))
        excess = mean - base
        win = float((sel > 0).mean())
        t = float(mean / (np.nanstd(sel) / np.sqrt(n))) if n > 1 else 0.0
        mi = m.values[:k] & ~np.isnan(rv[:k])
        mo = m.values[k:] & ~np.isnan(rv[k:])
        is_ex = (np.nanmean(rv[:k][mi]) - np.nanmean(rv[:k])
                 if mi.sum() > 10 else np.nan)
        oo_ex = (np.nanmean(rv[k:][mo]) - np.nanmean(rv[k:])
                 if mo.sum() > 10 else np.nan)
        p = perm_pvalue(m.values, rv, mean)
        rows.append({
            "pattern": name, "n": n, "freq%": round(n / len(f) * 100, 2),
            "mean%": round(mean * 100, 3), "base%": round(base * 100, 3),
            "excess%": round(excess * 100, 3), "win%": round(win * 100, 1),
            "t": round(t, 2), "IS_ex%": round(is_ex * 100, 3),
            "OOS_ex%": round(oo_ex * 100, 3), "perm_p": round(p, 4),
            "OOS_same_sign": (np.sign(is_ex) == np.sign(oo_ex)
                              if np.isfinite(is_ex) and np.isfinite(oo_ex)
                              else False),
        })
    return pd.DataFrame(rows).sort_values("excess%", ascending=False)


def run(symbols, interval, H, label):
    frames = []
    for s in symbols:
        df = D.load(s, interval)
        if df.empty or len(df) < 400:
            continue
        frames.append(feats(df))
    print(f"\n{'='*92}\n{label}  |  interval={interval}  horizon={H} bars  "
          f"|  pooled {len(frames)} assets\n{'='*92}")
    res = []
    for fr in frames:
        res.append(analyse(fr, H))
    # pool: weight by n
    allp = pd.concat(res)
    agg = (allp.groupby("pattern")
           .apply(lambda g: pd.Series({
               "n": g["n"].sum(),
               "excess%": np.average(g["excess%"], weights=g["n"]),
               "win%": np.average(g["win%"], weights=g["n"]),
               "OOS_ex%": np.average(g["OOS_ex%"].fillna(0), weights=g["n"]),
               "perm_p_med": g["perm_p"].median(),
               "OOS_same_sign_frac": g["OOS_same_sign"].mean(),
           }), include_groups=False)
           .sort_values("excess%", ascending=False))
    pd.set_option("display.width", 130)
    print(agg.round(3).to_string())
    npat = len(agg)
    print(f"\n  patterns tested: {npat} × {len(frames)} assets. "
          f"Expected false 'hits' at perm_p<0.05 by pure chance: "
          f"~{0.05*npat:.1f} per asset.")
    surv = agg[(agg["perm_p_med"] < 0.05) & (agg["OOS_same_sign_frac"] >= 0.6)
               & (agg["excess%"].abs() > 0.05)]
    print(f"  SURVIVORS (perm_p<0.05 AND OOS sign-consistent ≥60% AND "
          f"|excess|>0.05% after cost): {len(surv)}")
    if len(surv):
        print(surv.round(3).to_string())
    return agg


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "1h"
    if mode == "1h":
        for H in (5, 10, 24):
            run(D.MAJORS, "1h", H, "1h MAJORS")
    elif mode == "1d":
        for H in (3, 5, 10):
            run(D.UNIVERSE, "1d", H, "1d UNIVERSE")
