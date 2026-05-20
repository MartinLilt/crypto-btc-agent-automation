"""
Deployable strategy book — Conservative 50/50 (rotation + funding carry).

No lookahead: weights for day D are computed from data CLOSED on/before D-1
and held through D. `target_weights(asof)` returns the book to hold *into*
the next day. Sleeve A = vol-targeted cross-sectional alt momentum on a
survivorship-reduced 12-asset universe. Sleeve B = gated delta-neutral
funding carry (long spot / short perp) on 6 perps. Blend 50/50.

Robust central params (NOT backtest-optimised — IS→OOS corr was −0.33):
  lookback=25, hold=5, top_k=3, vol target 30% ann, leverage cap 1.5x.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import research.data as D
import research.strategies as S
from research.engine import portfolio

CONS12 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT",
          "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LTCUSDT", "ATOMUSDT"]
CARRY = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]

ROT = dict(lookback=25, hold=5, top_k=3)
VOL_TARGET = 0.30
LEV_CAP = 1.5
CARRY_THR = 0.00003          # engage carry when 7d-mean 8h funding > this
BLEND_ROT, BLEND_CARRY = 0.50, 0.50
MINVOL = 5e7                 # liquidity floor: 30d median quote-volume ≥ $50M
ROT_STOP = 0.99              # hard stop disabled (stress test: doesn't pay)


def _prices(refresh=False) -> dict:
    px = {}
    for s in CONS12:
        df = D.refresh(s, "1d") if refresh else D.load(s, "1d")
        if not df.empty:
            px[s] = df.set_index("dt")["c"]
    return px


def _qv(refresh=False) -> dict:
    qv = {}
    for s in CONS12:
        df = D.refresh(s, "1d") if refresh else D.load(s, "1d")
        if not df.empty:
            qv[s] = df.set_index("dt")["qv"]
    return qv


def book_series(refresh=False) -> pd.DataFrame:
    """Full historical equity of the deployed 50/50 book (for context/forward)."""
    px = _prices(refresh)
    qv = _qv(refresh)
    W, idx = S.xsec_momentum_hardened(px, qv, stop=ROT_STOP, minvol=MINVOL, **ROT)
    pf = portfolio(W, px, cost_bps=15.0, bars_per_year=365.0, lev_cap=1.0)
    net = pf["net"]
    rv = net.rolling(30).std() * np.sqrt(365.0)
    rot = (net * (VOL_TARGET / rv).clip(upper=LEV_CAP).shift(1).fillna(0.0))
    rot.index = pd.to_datetime(pf["dt"].values, utc=True)

    cr = {}
    for s in CARRY:
        f = D.refresh_funding(s) if refresh else D.load_funding(s)
        if f.empty:
            continue
        dd = f.set_index("dt")["fr"].resample("1D").sum()
        eng = (dd.rolling(7).mean() > CARRY_THR).astype(float).shift(1).fillna(0.0)
        cr[s] = eng * dd - eng.diff().abs().fillna(0.0) * 0.0012
    carry = pd.DataFrame(cr).fillna(0.0).mean(axis=1)

    ix = rot.index.union(carry.index).sort_values()
    r = rot.reindex(ix).fillna(0.0)
    c = carry.reindex(ix).fillna(0.0)
    comb = BLEND_ROT * r + BLEND_CARRY * c
    eq = (1 + comb).cumprod()
    out = pd.DataFrame({"dt": ix, "ret": comb.values, "eq": eq.values})
    out["rot"] = r.values
    out["carry"] = c.values
    return out


def target_weights(refresh=True) -> dict:
    """What to hold from the next bar. Returns rotation picks + carry engagement."""
    px = _prices(refresh)
    qv = _qv(refresh)
    W, idx = S.xsec_momentum_hardened(px, qv, stop=ROT_STOP, minvol=MINVOL, **ROT)
    last = idx[-1]
    rot_w = {s: float(W[s].loc[last]) for s in W if float(W[s].loc[last]) > 0}
    # vol-target scaler from the combined sleeve's recent realized vol
    pf = portfolio(W, px, cost_bps=15.0, bars_per_year=365.0, lev_cap=1.0)
    rv = (pf["net"].rolling(30).std() * np.sqrt(365.0)).iloc[-1]
    scaler = float(np.clip(VOL_TARGET / rv, 0, LEV_CAP)) if rv and rv > 0 else 0.0

    carry_on = {}
    for s in CARRY:
        f = D.refresh_funding(s) if refresh else D.load_funding(s)
        if f.empty:
            continue
        dd = f.set_index("dt")["fr"].resample("1D").sum()
        carry_on[s] = bool(dd.rolling(7).mean().iloc[-1] > CARRY_THR)
    return {
        "asof": str(last.date()),
        "sleeve_A_rotation": {s: round(w * scaler * BLEND_ROT, 4)
                              for s, w in rot_w.items()},
        "rotation_vol_scaler": round(scaler, 3),
        "sleeve_B_carry_legs": [s for s, on in carry_on.items() if on],
        "carry_weight_each": round(BLEND_CARRY / max(sum(carry_on.values()), 1), 4),
        "note": "carry = delta-neutral (long spot + short perp), market risk ≈ 0",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(target_weights(refresh=True), indent=2, ensure_ascii=False))