"""
Research driver. Phases:
  1  single-asset systematic strategies on majors (daily), full + OOS
  2  cross-sectional alt-momentum rotation (the "buy movers" idea), regime-gated
  3  return/drawdown frontier via vol-targeting + leverage
  4  funding-carry (delta-neutral) structural edge

Honest framing: every result is compared to buy&hold over the SAME window and
split into in-sample / out-of-sample. The number that matters is OOS.
"""
from __future__ import annotations

import sys
import numpy as np
import pandas as pd

import research.data as D
from research.engine import simulate, metrics, fmt, portfolio
import research.strategies as S

BPY = 365.0  # daily bars/year
SPLIT = 0.60  # first 60% in-sample, last 40% out-of-sample


def oos_split(df):
    k = int(len(df) * SPLIT)
    return df.iloc[:k], df.iloc[k:]


def single_asset(sym, df):
    df = df.reset_index(drop=True)
    grid = {
        "buy&hold":      (S.w_buyhold, {}),
        "ema20/100":     (S.w_ema_cross, dict(fast=20, slow=100, regime=200)),
        "ema10/50":      (S.w_ema_cross, dict(fast=10, slow=50, regime=200)),
        "donch20 ATR3":  (S.w_donchian_trail, dict(brk=20, regime=200, atr_mult=3.0)),
        "donch55 ATR4":  (S.w_donchian_trail, dict(brk=55, regime=200, atr_mult=4.0)),
        "meanrev z2":    (S.w_meanrev_z, dict(n=20, z_in=2.0, z_out=0.3)),
        "rsi2 dip":      (S.w_rsi2, dict(regime=200, rsi_in=10, rsi_out=60)),
    }
    print(f"\n=== {sym}  ({df['dt'].iloc[0].date()} → {df['dt'].iloc[-1].date()}, "
          f"{len(df)} d) ===")
    print(f"  {'strategy':<14} | FULL: {'':<2}| OOS (last 40%):")
    rows = []
    for name, (fn, p) in grid.items():
        w = fn(df, **p)
        full = metrics(simulate(df, w)["strat"], df["dt"], BPY)
        _, oo = oos_split(df)
        wo = fn(oo.reset_index(drop=True), **p)
        om = metrics(simulate(oo.reset_index(drop=True), wo)["strat"],
                     oo["dt"], BPY)
        rows.append((name, full, om))
        print(f"  {name:<14} | {fmt(full)}")
        print(f"  {'':<14} |   OOS {fmt(om)}")
    return rows


def phase1():
    print("\n" + "#" * 78)
    print("# PHASE 1 — single-asset systematic strategies, daily, vs buy&hold")
    print("#" * 78)
    for sym in D.MAJORS:
        df = D.load(sym, "1d")
        if not df.empty:
            single_asset(sym, df)


def phase2():
    print("\n" + "#" * 78)
    print("# PHASE 2 — cross-sectional alt momentum rotation (regime-gated)")
    print("#   'buy the strongest movers, rotate, flatten when market is off'")
    print("#" * 78)
    uni = D.load_universe("1d") if False else {}
    for s in D.UNIVERSE:
        d = D.load(s, "1d")
        if not d.empty and len(d) > 400:
            uni[s] = d.set_index("dt")["c"]
    print(f"  universe: {len(uni)} assets")
    configs = [
        dict(lookback=30, hold=5, top_k=3),
        dict(lookback=60, hold=7, top_k=3),
        dict(lookback=90, hold=7, top_k=4),
        dict(lookback=20, hold=3, top_k=2),
        dict(lookback=120, hold=14, top_k=5),
    ]
    prices = uni
    for cfg in configs:
        W, idx = S.xsec_momentum(prices, mkt_regime=True, **cfg)
        port = portfolio(W, prices, cost_bps=15.0, bars_per_year=BPY,
                         lev_cap=1.0)
        m = metrics(port["net"], port["dt"], BPY)
        k = int(len(port) * SPLIT)
        mo = metrics(port["net"].iloc[k:], port["dt"].iloc[k:], BPY)
        print(f"\n  cfg {cfg}")
        print(f"    FULL {fmt(m)}")
        print(f"    OOS  {fmt(mo)}")
    # benchmark: equal-weight buy&hold of the universe
    bh = {s: pd.Series(1.0 / len(prices), index=prices[s].index) for s in prices}
    p = portfolio(bh, prices, cost_bps=0.0, bars_per_year=BPY, lev_cap=1.0)
    print(f"\n  [benchmark] equal-weight buy&hold universe: {fmt(metrics(p['net'], p['dt'], BPY))}")


def _rotation_net(prices, cfg, cost_bps=15.0):
    W, idx = S.xsec_momentum(prices, mkt_regime=True, **cfg)
    return portfolio(W, prices, cost_bps=cost_bps, bars_per_year=BPY, lev_cap=1.0)


def pf_vol_target(net: pd.Series, dt: pd.Series, target_ann: float,
                  lev_cap: float, vol_n: int = 30) -> pd.Series:
    rv = net.rolling(vol_n).std() * np.sqrt(BPY)
    scale = (target_ann / rv).clip(upper=lev_cap).shift(1).fillna(0.0)
    return net * scale


def phase3():
    print("\n" + "#" * 78)
    print("# PHASE 3 — risk control: vol-targeting + leverage frontier")
    print("#   same rotation signal, scaled to a target volatility")
    print("#" * 78)
    full = {s: D.load(s, "1d").set_index("dt")["c"]
            for s in D.UNIVERSE if not D.load(s, "1d").empty
            and len(D.load(s, "1d")) > 400}
    # conservative subset = listed by 2021, still liquid (less survivorship lift)
    cons = {s: full[s] for s in
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT",
             "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LTCUSDT", "ATOMUSDT"]
            if s in full}
    cfg = dict(lookback=30, hold=5, top_k=3)
    for label, px in [("FULL-30 (survivorship-biased)", full),
                      ("CONSERV-12 (since-2021 survivors)", cons)]:
        base = _rotation_net(px, cfg)
        k = int(len(base) * SPLIT)
        print(f"\n  [{label}]  raw rotation:")
        print(f"    OOS {fmt(metrics(base['net'].iloc[k:], base['dt'].iloc[k:], BPY))}")
        for tgt, cap in [(0.20, 1.0), (0.30, 1.5), (0.40, 2.0), (0.60, 3.0)]:
            vt = pf_vol_target(base["net"], base["dt"], tgt, cap)
            mo = metrics(vt.iloc[k:], base["dt"].iloc[k:], BPY)
            print(f"    OOS volTgt={tgt*100:.0f}% cap={cap}x → {fmt(mo)}")


def phase4():
    print("\n" + "#" * 78)
    print("# PHASE 4 — delta-neutral FUNDING CARRY (structural, low-DD edge)")
    print("#   long spot + short perp; collect funding when it's favorable")
    print("#" * 78)
    majors = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
    daily = {}
    for s in majors:
        f = D.load_funding(s)
        if f.empty:
            print(f"  {s}: no funding data"); continue
        f = f.set_index("dt")
        # daily sum of 8h funding the SHORT leg receives (fr>0 ⇒ short earns +fr)
        dd = f["fr"].resample("1D").sum()
        daily[s] = dd
        ann = dd.mean() * 365 * 100
        print(f"  {s:<9} funding history {f.index[0].date()}→{f.index[-1].date()}"
              f"  mean carry ≈ {ann:5.1f}%/yr gross (always-on short)")
    if not daily:
        return
    # Strategy: engage carry only when trailing 7d mean funding > 0.003%/8h.
    # Net daily = funding received − 0 (price-neutral) − toggle cost.
    rets = {}
    for s, dd in daily.items():
        thr = 0.00003  # 0.003% per 8h ≈ ~3.3%/yr engage floor
        eng = (dd.rolling(7).mean() > thr).astype(float).shift(1).fillna(0.0)
        toggle = eng.diff().abs().fillna(0.0)
        # costs: perp+spot round trip on toggle ≈ 0.12% ; perp short borrow ~0
        r = eng * dd - toggle * 0.0012
        rets[s] = r
    R = pd.DataFrame(rets).fillna(0.0)
    port = R.mean(axis=1)  # equal-weight across carry legs
    m = metrics(port, pd.Series(port.index), BPY)
    k = int(len(port) * SPLIT)
    mo = metrics(port.iloc[k:], pd.Series(port.index[k:]), BPY)
    print("\n  equal-weight selective funding carry (gated, after costs):")
    print(f"    FULL {fmt(m)}")
    print(f"    OOS  {fmt(mo)}")
    # always-on (no gate) for reference
    ao = R if False else pd.DataFrame({s: daily[s] for s in daily}).fillna(0.0).mean(axis=1)
    print(f"    [always-on, ungated] FULL {fmt(metrics(ao, pd.Series(ao.index), BPY))}")
    return port


def phase5():
    print("\n" + "#" * 78)
    print("# PHASE 5 — COMBINED book: vol-targeted rotation + funding carry")
    print("#   the actual candidate. 70% risk sleeve / 30% carry sleeve.")
    print("#" * 78)
    full = {s: D.load(s, "1d").set_index("dt")["c"]
            for s in D.UNIVERSE if not D.load(s, "1d").empty
            and len(D.load(s, "1d")) > 400}
    base = _rotation_net(full, dict(lookback=30, hold=5, top_k=3))
    sleeve = pf_vol_target(base["net"], base["dt"], 0.30, 1.5)
    sleeve.index = pd.to_datetime(base["dt"].values, utc=True)
    # carry
    majors = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
    cr = {}
    for s in majors:
        f = D.load_funding(s)
        if f.empty:
            continue
        dd = f.set_index("dt")["fr"].resample("1D").sum()
        eng = (dd.rolling(7).mean() > 0.00003).astype(float).shift(1).fillna(0.0)
        cr[s] = eng * dd - eng.diff().abs().fillna(0.0) * 0.0012
    carry = pd.DataFrame(cr).fillna(0.0).mean(axis=1)
    idx = sleeve.index.union(carry.index).sort_values()
    sub = sleeve.reindex(idx).fillna(0.0)
    cab = carry.reindex(idx).fillna(0.0)
    for ws, wc in [(0.7, 0.3), (0.5, 0.5), (1.0, 0.0), (0.0, 1.0), (0.6, 0.4)]:
        comb = ws * sub + wc * cab
        k = int(len(comb) * SPLIT)
        mo = metrics(comb.iloc[k:], pd.Series(idx[k:]), BPY)
        fu = metrics(comb, pd.Series(idx), BPY)
        print(f"\n  {int(ws*100)}% rotation / {int(wc*100)}% carry")
        print(f"    FULL {fmt(fu)}")
        print(f"    OOS  {fmt(mo)}")


if __name__ == "__main__":
    ph = sys.argv[1] if len(sys.argv) > 1 else "1"
    if ph in ("1", "all"):
        phase1()
    if ph in ("2", "all"):
        phase2()
    if ph in ("3", "all"):
        phase3()
    if ph in ("4", "all"):
        phase4()
    if ph in ("5", "all"):
        phase5()