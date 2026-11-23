"""Plan B data: 4h bars of the basket at four different wake-up hours.

The 2026-08-22 noise-floor finding: shifting only WHEN the bot wakes up moved
results 16-24pp. A real improvement must survive that. So every plan B variant
is run on 4h bars built from 1h candles with the 4h boundary at +0/+1/+2/+3h
(offset 0 = the real Binance 4h bars, which the sanity check below asserts).

    python -m backtest.planb check      # download/cache 1h, verify offset 0
    python -m backtest.planb run        # B1/B2/B1+B2 vs base, verdict by the plan rules

Rules are the ones written in plans/2026-11-22 Plan B.md BEFORE the first run;
this code applies them, it does not choose them.
"""

import os
import pickle
import sys

from backtest.portfolio import BASKET
from src.binance_api import Candle, get_candles

CACHE_1H = "state/bt-cache/usdt_1h.pkl"
CACHE_4H = "state/bt-cache/usdt_4h.pkl"


def closed_only(data):
    """Drop candles still forming when they were fetched. Binance returns the
    current, unfinished candle last; caching it froze a fake close into the
    data (found 2026-11-23: the last cached 4h bar was 2% off the real one)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return {c: [k for k in v if k.close_time < now] for c, v in data.items()}


def load_4h(limit=6000):
    if os.path.exists(CACHE_4H):
        return pickle.load(open(CACHE_4H, "rb"))
    from backtest.portfolio import load
    data = closed_only(load(limit=limit))
    pickle.dump(data, open(CACHE_4H, "wb"))
    return data


def load_1h(limit=24000):
    if os.path.exists(CACHE_1H):
        return pickle.load(open(CACHE_1H, "rb"))
    data = closed_only({c: get_candles(symbol=c, interval="1h", limit=limit) for c in BASKET})
    pickle.dump(data, open(CACHE_1H, "wb"))
    return data


def to_4h(candles_1h, offset):
    """4h bars whose boundary sits at (hour % 4 == offset). Partial groups at
    either end are dropped. open_time is shifted back by `offset` hours so the
    regime code's "bar opening at 20:00 closes the UTC day" rule keeps meaning
    "the bar that closes the day" in this bar's own clock."""
    from datetime import timedelta
    out, i = [], 0
    while i < len(candles_1h) and candles_1h[i].open_time.hour % 4 != offset:
        i += 1
    while i + 4 <= len(candles_1h):
        g = candles_1h[i:i + 4]
        out.append(Candle(open_time=g[0].open_time - timedelta(hours=offset),
                          open=g[0].open, high=max(k.high for k in g),
                          low=min(k.low for k in g), close=g[-1].close,
                          volume=sum(k.volume for k in g), close_time=g[-1].close_time))
        i += 4
    return out


def shifted(offset):
    """Basket 4h data at a given wake-up offset, aligned to common bars."""
    h1 = load_1h()
    data = {c: to_4h(h1[c], offset) for c in h1}
    start = max(v[0].open_time for v in data.values())
    data = {c: [k for k in v if k.open_time >= start] for c, v in data.items()}
    n = min(len(v) for v in data.values())
    return {c: v[:n] for c, v in data.items()}


def check():
    h1 = load_1h()
    native = load_4h()
    for c in BASKET:
        print(f"{c}: 1h {len(h1[c])} bars {h1[c][0].open_time:%Y-%m-%d} -> "
              f"{h1[c][-1].open_time:%Y-%m-%d %H:%M}")
    bad = 0
    for c in BASKET:
        mine = {k.open_time: k for k in to_4h(h1[c], 0)}
        both = [k for k in native[c] if k.open_time in mine]
        diff = [k for k in both if abs(mine[k.open_time].close - k.close) > 1e-9
                or abs(mine[k.open_time].high - k.high) > 1e-9]
        bad += len(diff)
        print(f"{c}: offset-0 vs native 4h — {len(both)} common bars, {len(diff)} differ")
    for off in range(4):
        d = shifted(off)
        print(f"offset +{off}h: {len(d['BTCUSDT'])} bars per coin")
    print("CHECK", "OK" if not bad else "FAILED")




# ── plan B runs ──────────────────────────────────────────────────────────

BASE = dict(hold_tp=40)                         # live: hold 25%, partial TP 40%/50%
VARIANTS = {
    "base":  {},
    "B1":    dict(bull_bank_bags=True),
    "B2":    dict(regime_mode="utc_daily"),
    "B1+B2": dict(bull_bank_bags=True, regime_mode="utc_daily"),
}


def spans(data):
    from datetime import datetime, timezone
    from backtest.portfolio import WARM
    times = [k.open_time for k in data["BTCUSDT"]]
    def bar(day):
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return next(i for i, t in enumerate(times) if t >= dt)
    n = len(times)
    return {"full": (WARM, n), "H1": (WARM, bar("2025-02-01")),
            "H2": (bar("2025-02-01"), bar("2026-08-22")), "H3": (bar("2026-08-22"), n)}


def run():
    from backtest.portfolio import simulate
    data = load_4h()
    sp = spans(data)
    res = {}
    print(f"bars {len(data['BTCUSDT'])}: {data['BTCUSDT'][0].open_time:%Y-%m-%d} → "
          f"{data['BTCUSDT'][-1].open_time:%Y-%m-%d %H:%M} · book 5000 · live config")
    for span, (lo, hi) in sp.items():
        print(f"\n=== {span}: {data['BTCUSDT'][lo].open_time:%Y-%m-%d} → "
              f"{data['BTCUSDT'][hi - 1].open_time:%Y-%m-%d} ===")
        print(f"  {'variant':<6} {'ret%':>7} {'Δ':>6} {'mdd%':>6} | {'grid':>6} {'hold':>6} | "
              f"{'holds':>5} {'days':>5} {'red':>4} {'red$':>6} | {'bullbank':>8} {'$':>5}")
        for name, kw in VARIANTS.items():
            r = simulate(data, lo=lo, hi=hi, **BASE, **kw)
            res[(span, name)] = r
            d = r["ret"] - res[(span, "base")]["ret"]
            print(f"  {name:<6} {r['ret']:>+7.1f} {d:>+6.1f} {r['mdd']:>6.1f} | "
                  f"{r['pnl_grid']:>+6.0f} {r['pnl_hold']:>+6.0f} | {r['n_hold']:>5} "
                  f"{r['hold_days']:>5.1f} {r['hold_loss_n']:>4} {r['hold_loss_sum']:>+6.0f} | "
                  f"{r['n_bull_bank']:>8} {r['r_bull_bank']:>+5.0f}")
    print("\n=== wake-up offset (full period, 4h from 1h) ===")
    print(f"  {'offset':<7}" + "".join(f"{v:>9}" for v in VARIANTS))
    for off in range(4):
        d = shifted(off)
        lo, hi = spans(d)["full"]
        row = {name: simulate(d, lo=lo, hi=hi, **BASE, **kw)["ret"] for name, kw in VARIANTS.items()}
        for name in VARIANTS:
            res[(f"off{off}", name)] = row[name]
        print(f"  +{off}h    " + "".join(f"{row[v]:>+8.1f}%" for v in VARIANTS))

    print("\n=== verdict (plan B rule 4) ===")
    for name in ("B1", "B2", "B1+B2"):
        dd = {s: res[(s, name)]["ret"] - res[(s, "base")]["ret"] for s in sp}
        offs = sum(1 for o in range(4) if res[(f"off{o}", name)] > res[(f"off{o}", "base")])
        mdd = res[("full", name)]["mdd"] - res[("full", "base")]["mdd"]
        checks = {
            "full better": dd["full"] > 0, "H1 better": dd["H1"] > 0, "H2 better": dd["H2"] > 0,
            "H3 not worse": dd["H3"] >= 0, f"offsets {offs}/4 >= 3": offs >= 3,
            f"mdd {mdd:+.1f}pp <= +2": mdd <= 2.0,
        }
        ok = all(checks.values())
        print(f"  {name:<6} {'PASS' if ok else 'FAIL'} (mechanism checked by hand) · "
              + " · ".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items())
              + " · Δ " + " ".join(f"{s} {v:+.1f}" for s, v in dd.items()))


if __name__ == "__main__":
    {"check": check, "run": run}[sys.argv[1]]()
