"""
Ablation: does the live bot's L9 candle-pattern layer add signal or drag?

Canonical config (the headline 6-asset ETH/LINK-heavy book), run three ways:
  baseline  — L9 = real detect_candle_patterns (as shipped)
  L9=7      — L9 frozen to the "skipped/neutral-good" constant
  L9=5      — L9 frozen to pure-neutral midpoint
On two windows: 365d (recent / OOS-relevant) and 720d (in-sample, tuned here).

Decisive metric = per-trade expectancy (robust to differing trade counts) and
net $ on the recent window. If freezing L9 raises recent expectancy, the
candle layer is a drag — confirming the pattern-mining lead.
"""
from __future__ import annotations

import sys
import importlib

from src.signals.indicators import RECOMMENDED_ASSET_WEIGHTS
from scripts import paper_replay as PR
from src.backtest import engine as ENG

ASSETS = ["BTCUSDT", "SOLUSDT", "ETHUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT"]
_REAL = ENG.detect_candle_patterns


def _freeze(const: int):
    def _f(candles, candles_4h=None):
        return const, {"score": const, "pass": const >= 7, "frozen": True,
                       "pattern": "NEUTRALIZED"}
    return _f


def one(days: int, l9mode):
    if l9mode == "real":
        ENG.detect_candle_patterns = _REAL
    else:
        ENG.detect_candle_patterns = _freeze(int(l9mode))
    res = PR.run_replay(
        days=days, capital=10000.0, per_trade=1000.0, direction="LONG",
        tp_pct=3.0, sl_pct=1.5, assets=ASSETS,
        asset_weights=dict(RECOMMENDED_ASSET_WEIGHTS))
    ENG.detect_candle_patterns = _REAL
    tr = res["trades"]
    n = len(tr)
    net = sum(t["pnl_usd"] for t in tr)
    wins = sum(1 for t in tr if t["pnl_usd"] > 0)
    exp = net / n if n else 0.0
    # max drawdown from equity curve
    eq = [e for _, e in res["equity_curve"]] or [10000.0]
    peak = eq[0]
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    mo = net / (days / 30.0)
    return dict(days=days, mode=l9mode, n=n,
                wr=round(wins / n * 100, 1) if n else 0.0,
                net=round(net, 1), exp=round(exp, 2),
                mo=round(mo, 1), mdd=round(mdd * 100, 2),
                bh=round(res["buy_and_hold_pnl_usd"], 1))


def main():
    rows = []
    plan = [(365, "real"), (365, 7), (365, 5),
            (720, "real"), (720, 7), (720, 5)]
    for days, m in plan:
        r = one(days, m)
        rows.append(r)
        print(f"[{r['days']}d L9={str(r['mode']):>4}] "
              f"trades={r['n']:>4} WR={r['wr']:>5}%  net=${r['net']:>8}  "
              f"exp/trade=${r['exp']:>7}  ${r['mo']:>6}/mo  "
              f"maxDD={r['mdd']:>5}%  (B&H ${r['bh']})", flush=True)
    print("\n=== verdict ===")
    for days in (365, 720):
        sub = {x["mode"]: x for x in rows if x["days"] == days}
        b = sub["real"]
        for k in (7, 5):
            a = sub[k]
            dn = a["net"] - b["net"]
            de = a["exp"] - b["exp"]
            tag = "L9 is DRAG" if de > 0 else "L9 helps"
            print(f"{days}d: freeze→{k} vs real  Δnet=${dn:+.0f}  "
                  f"Δexp/trade=${de:+.2f}  → {tag}")


if __name__ == "__main__":
    main()