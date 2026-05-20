"""
Strategy families. Each returns a target-weight Series aligned to df.index.
No lookahead: weight at bar t uses only data through bar t; engine shifts it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.engine import ema, sma, rsi, atr, realized_vol, donchian_hi, donchian_lo


def w_buyhold(df: pd.DataFrame, **k) -> pd.Series:
    return pd.Series(1.0, index=df.index)


def w_ema_cross(df: pd.DataFrame, fast=20, slow=100, regime=200, **k) -> pd.Series:
    c = df["c"]
    long = (ema(c, fast) > ema(c, slow)) & (c > sma(c, regime))
    return long.astype(float)


def _trail_state(df: pd.DataFrame, entry: pd.Series, atr_n: int, atr_mult: float,
                 regime: pd.Series | None = None) -> pd.Series:
    """Long/flat state machine with ATR trailing stop. Loop (data is small)."""
    c = df["c"].values
    a = atr(df, atr_n).values
    e = entry.values
    reg = (regime.values if regime is not None
           else np.ones(len(c), dtype=bool))
    w = np.zeros(len(c))
    in_pos = False
    peak = 0.0
    for i in range(len(c)):
        if not in_pos:
            if e[i] and reg[i] and not np.isnan(a[i]):
                in_pos = True
                peak = c[i]
                w[i] = 1.0
        else:
            peak = max(peak, c[i])
            stop = peak - atr_mult * a[i]
            if c[i] < stop:
                in_pos = False
                w[i] = 0.0
            else:
                w[i] = 1.0
    return pd.Series(w, index=df.index)


def w_donchian_trail(df: pd.DataFrame, brk=20, exitn=10, regime=200,
                     atr_n=14, atr_mult=3.0, **k) -> pd.Series:
    c = df["c"]
    entry = c > donchian_hi(df["h"], brk).shift(1)
    reg = c > sma(c, regime)
    return _trail_state(df, entry, atr_n, atr_mult, reg)


def w_meanrev_z(df: pd.DataFrame, n=20, z_in=2.0, z_out=0.3, regime=200,
                stop=0.10, **k) -> pd.Series:
    c = df["c"]
    mu, sd = sma(c, n), c.rolling(n).std()
    z = (c - mu) / sd
    reg = c > sma(c, regime)
    w = np.zeros(len(c))
    cv = c.values
    zv = z.values
    rv = reg.values
    inpos = False
    entry_px = 0.0
    for i in range(len(cv)):
        if not inpos:
            if rv[i] and not np.isnan(zv[i]) and zv[i] < -z_in:
                inpos = True
                entry_px = cv[i]
                w[i] = 1.0
        else:
            dd = cv[i] / entry_px - 1
            if zv[i] > -z_out or dd < -stop:
                inpos = False
            else:
                w[i] = 1.0
    return pd.Series(w, index=df.index)


def w_rsi2(df: pd.DataFrame, regime=200, rsi_in=10, rsi_out=60, **k) -> pd.Series:
    """Connors RSI(2) dip-buy inside an uptrend regime."""
    c = df["c"]
    r = rsi(c, 2)
    reg = c > sma(c, regime)
    w = np.zeros(len(c))
    rv, rgv = r.values, reg.values
    inpos = False
    for i in range(len(c)):
        if not inpos:
            if rgv[i] and rv[i] < rsi_in:
                inpos = True
                w[i] = 1.0
        else:
            if rv[i] > rsi_out:
                inpos = False
            else:
                w[i] = 1.0
    return pd.Series(w, index=df.index)


def vol_target(w: pd.Series, df: pd.DataFrame, target_ann: float,
               bars_per_year: float, lev_cap: float = 3.0,
               vol_n: int = 30) -> pd.Series:
    """Scale a 0/1 weight so the position's realized vol ≈ target_ann."""
    rv = realized_vol(df["c"], vol_n, bars_per_year).shift(1)
    scale = (target_ann / rv).clip(upper=lev_cap).fillna(0.0)
    return (w * scale).clip(lower=0, upper=lev_cap)


# ── Cross-sectional momentum rotation (the "buy movers, ride spike" done right) ─

def xsec_momentum_hardened(prices: dict, qv: dict, lookback=25, hold=5,
                            top_k=3, stop: float = 0.99, minvol: float = 5e7,
                            asset_trend=100, btc_regime=100,
                            btc_sym="BTCUSDT") -> tuple[dict, pd.Index]:
    """
    Hardened cross-sectional momentum: top_k by momentum, gated by asset
    trend filter (SMA-asset_trend), market regime (BTC > SMA-btc_regime), AND
    a rolling 30d quote-volume floor (`minvol`). Stateful exits: a held name
    is dropped if its price falls > `stop` from entry OR liquidity drops OR
    market goes off. Returns {sym: weight} aligned to a sorted union index.
    """
    idx = None
    for s in prices:
        idx = prices[s].index if idx is None else idx.union(prices[s].index)
    idx = idx.sort_values()
    P = pd.DataFrame({s: prices[s].reindex(idx) for s in prices})
    QV = pd.DataFrame({s: qv[s].reindex(idx) for s in prices if s in qv})
    mom = P / P.shift(lookback) - 1
    trend_ok = P > P.rolling(asset_trend).mean()
    liq_ok = QV.rolling(30).median() >= minvol
    btc = P[btc_sym]
    mkt = (btc > btc.rolling(btc_regime).mean()).values
    cols = list(P.columns)
    ci = {c: j for j, c in enumerate(cols)}
    Pv, Mv = P.values, mom.values
    Tv = trend_ok.reindex(columns=cols).values
    Lv = liq_ok.reindex(columns=cols).fillna(False).values
    W = np.zeros((len(idx), len(cols)))
    held: dict[str, float] = {}
    last = -10**9
    for i in range(len(idx)):
        for s in list(held):
            j = ci[s]
            p = Pv[i, j]
            if (np.isnan(p) or not mkt[i] or not Lv[i, j]
                    or p <= held[s] * (1 - stop)):
                held.pop(s)
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
            w = 1.0 / top_k
            for s in held:
                W[i, ci[s]] = w
    return {c: pd.Series(W[:, ci[c]], index=idx) for c in cols}, idx


def xsec_momentum(prices: dict, lookback=30, hold=5, top_k=3,
                  asset_trend=100, mkt_regime=True, btc_regime=100):
    """
    prices: {sym: close Series indexed by dt} (daily). Returns {sym: weight}.
    Rank universe by trailing `lookback`-bar return; hold equal-weight top_k,
    rebalanced every `hold` bars. Only hold names above their own SMA;
    flatten everything when BTC is below its regime SMA (market off).
    """
    idx = None
    for s in prices:
        idx = prices[s].index if idx is None else idx.union(prices[s].index)
    idx = idx.sort_values()
    P = pd.DataFrame({s: prices[s].reindex(idx) for s in prices})
    mom = P / P.shift(lookback) - 1
    trend_ok = P > P.rolling(asset_trend).mean()
    btc = P["BTCUSDT"] if "BTCUSDT" in P else P.iloc[:, 0]
    mkt_ok = (btc > btc.rolling(btc_regime).mean()) if mkt_regime else pd.Series(True, index=idx)

    W = pd.DataFrame(0.0, index=idx, columns=P.columns)
    last = -10**9
    cur = pd.Series(0.0, index=P.columns)
    for i, t in enumerate(idx):
        if i - last >= hold:
            last = i
            m = mom.loc[t].copy()
            m[~trend_ok.loc[t].fillna(False)] = np.nan
            m[P.loc[t].isna()] = np.nan
            ranked = m.dropna().sort_values(ascending=False)
            cur = pd.Series(0.0, index=P.columns)
            if mkt_ok.loc[t] and len(ranked) > 0:
                pick = ranked.index[:top_k]
                cur[pick] = 1.0 / max(len(pick), 1)
        W.loc[t] = (cur if mkt_ok.loc[t] else 0.0)
    return {s: W[s] for s in P.columns}, idx