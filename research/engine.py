"""
Vectorized research engine — no lookahead, realistic costs, walk-forward.

Convention: a strategy maps OHLCV → a *target weight* series w_t in [-L, L].
Position w_t is decided using data up to and including bar t, and earns the
return from bar t → t+1. Costs are charged on |Δw| (turnover) each bar.

Everything is in returns/%, so any starting capital can be projected.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── Indicators (vectorized) ───────────────────────────────────────────────────

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["h"], df["l"], df["c"]
    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def realized_vol(close: pd.Series, n: int, bars_per_year: float) -> pd.Series:
    return close.pct_change().rolling(n).std() * np.sqrt(bars_per_year)


def donchian_hi(high: pd.Series, n: int) -> pd.Series:
    return high.rolling(n).max()


def donchian_lo(low: pd.Series, n: int) -> pd.Series:
    return low.rolling(n).min()


# ── Single-asset position simulator ───────────────────────────────────────────

def simulate(df: pd.DataFrame, weight: pd.Series, cost_bps: float = 10.0,
             bars_per_year: float = 365.0) -> pd.DataFrame:
    """
    df: OHLCV with 'c'. weight: target weight (decided at bar t, shifted to
    avoid lookahead). cost_bps: round-trip-ish per unit turnover (10 = 0.10%).
    Returns DataFrame with strat return per bar, equity, etc.
    """
    ret = df["c"].pct_change().fillna(0.0)
    w = weight.shift(1).fillna(0.0)              # act on next bar (no lookahead)
    turnover = w.diff().abs().fillna(w.abs())
    cost = turnover * (cost_bps / 1e4)
    strat = w * ret - cost
    eq = (1 + strat).cumprod()
    out = pd.DataFrame({
        "dt": df["dt"].values, "ret": ret.values, "w": w.values,
        "strat": strat.values, "eq": eq.values, "turnover": turnover.values,
    })
    return out


def metrics(strat: pd.Series, dt: pd.Series, bars_per_year: float) -> dict:
    strat = pd.Series(np.asarray(strat, dtype=float))
    dt = pd.to_datetime(pd.Series(dt).values, utc=True)
    eq = (1 + strat).cumprod()
    n = len(strat)
    if n < 2 or eq.iloc[-1] <= 0:
        return {"total_ret": -1.0, "cagr": -1.0, "sharpe": 0.0, "sortino": 0.0,
                "maxdd": 1.0, "calmar": 0.0, "mo_mean": -1.0, "mo_hit": 0.0,
                "exposure": 0.0, "n": n, "ann_turn": 0.0}
    years = n / bars_per_year
    total = eq.iloc[-1] - 1
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0.0
    mu, sd = strat.mean(), strat.std()
    dnsd = strat[strat < 0].std()
    sharpe = (mu / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0
    sortino = (mu / dnsd * np.sqrt(bars_per_year)) if dnsd and dnsd > 0 else 0.0
    run_max = eq.cummax()
    maxdd = (1 - eq / run_max).max()
    calmar = cagr / maxdd if maxdd > 0 else 0.0
    m = pd.DataFrame({"r": strat.values}, index=dt)
    mo = (1 + m["r"]).resample("ME").prod() - 1
    return {
        "total_ret": float(total), "cagr": float(cagr),
        "sharpe": float(sharpe), "sortino": float(sortino),
        "maxdd": float(maxdd), "calmar": float(calmar),
        "mo_mean": float(mo.mean()), "mo_med": float(mo.median()),
        "mo_hit": float((mo > 0).mean()), "mo_std": float(mo.std()),
        "exposure": float((pd.Series(strat).abs() > 1e-9).mean()),
        "n": int(n), "years": float(years),
    }


def fmt(m: dict) -> str:
    return (f"tot={m['total_ret']*100:7.1f}%  CAGR={m['cagr']*100:6.1f}%  "
            f"Shrp={m['sharpe']:5.2f}  Sortino={m['sortino']:5.2f}  "
            f"maxDD={m['maxdd']*100:5.1f}%  Calmar={m['calmar']:4.2f}  "
            f"mo_mean={m['mo_mean']*100:5.2f}%  mo_hit={m['mo_hit']*100:4.0f}%  "
            f"expo={m['exposure']*100:3.0f}%")


# ── Portfolio simulator (multi-asset, shared capital) ─────────────────────────

def portfolio(weights: dict, prices: dict, cost_bps: float = 10.0,
              bars_per_year: float = 365.0, lev_cap: float = 1.0,
              align: str = "1D") -> pd.DataFrame:
    """
    weights: {sym: Series of target weight indexed by dt}
    prices:  {sym: Series of close indexed by dt}
    Sums target weights across assets, caps gross at lev_cap, applies cost on
    aggregate turnover. Returns portfolio equity curve.
    """
    idx = None
    for s in prices:
        i = pd.to_datetime(prices[s].index, utc=True)
        idx = i if idx is None else idx.union(i)
    idx = idx.sort_values()
    rmat, wmat = {}, {}
    for s in prices:
        p = prices[s].copy()
        p.index = pd.to_datetime(p.index, utc=True)
        p = p.reindex(idx)
        rmat[s] = p.pct_change().fillna(0.0)
        w = weights[s].copy()
        w.index = pd.to_datetime(w.index, utc=True)
        wmat[s] = w.reindex(idx).ffill().fillna(0.0)
    W = pd.DataFrame(wmat)
    R = pd.DataFrame(rmat)
    gross = W.abs().sum(axis=1)
    scale = np.where(gross > lev_cap, lev_cap / gross.replace(0, np.nan), 1.0)
    W = W.mul(pd.Series(scale, index=W.index).fillna(1.0), axis=0)
    Wl = W.shift(1).fillna(0.0)
    port_ret = (Wl * R).sum(axis=1)
    turn = (W - W.shift(1)).abs().sum(axis=1).fillna(W.abs().sum(axis=1))
    cost = turn * (cost_bps / 1e4)
    net = port_ret - cost
    eq = (1 + net).cumprod()
    return pd.DataFrame({"dt": idx, "ret": port_ret.values,
                         "cost": cost.values, "net": net.values,
                         "eq": eq.values, "gross": gross.values})