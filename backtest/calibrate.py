"""Plan B0: does the simulator reproduce what the live bot actually did?

Replays the live config from the REAL book at the first DigitalOcean cycle
(2026-10-09, the 2026-08-27 ledger minus the 4.00 USDC booked as a withdrawal)
through today on the USDC pairs the bot trades, with the real deposits, and
matches every simulated trade against the real ones from the trades table.

    python -m backtest.calibrate state/bt-cache/live_trades.csv

The CSV is `id,ts,symbol,side,kind,price,usdt,pnl` for trades id >= 58
(exported from the box). Time mapping: a live cycle at time T decides on the
4h bar that CLOSED at T, i.e. the sim bar that OPENED at T-4h.

Pass mark (plan B): >= 80% of real trades matched within +-1 bar, and every
miss explained. If the simulator cannot replay reality, nothing it says about
B1/B2 is worth anything.
"""

import csv
import os
import pickle
import sys
from datetime import datetime, timedelta, timezone

from backtest.planb import closed_only
from backtest.portfolio import simulate
from src.binance_api import get_candles

COINS = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]
CACHE = "state/bt-cache/usdc_4h.pkl"
H4 = timedelta(hours=4)

# Live book at the start of the 2026-10-09 12:50 cycle (Postgres dump of
# 2026-08-27; the cycle then found 4.00 less USDC than stored).
INIT = {
    "cash": 5.31801132 - 4.00,
    "bags": {
        "BTCUSDC": [{"entry": 77093.64, "qty": 0.00007, "cost": 5.3965548}],
        "ETHUSDC": [{"entry": 1918.63, "qty": 0.0031, "cost": 5.947753},
                    {"entry": 1894.90, "qty": 0.0031, "cost": 5.87419}],
        "SOLUSDC": [{"entry": 93.25, "qty": 0.064, "cost": 5.968}],
    },
    "holds": {
        "BNBUSDC": {"entry": 673.57, "qty": 0.016, "cost": 10.77712},
        "BTCUSDC": {"entry": 76988.12, "qty": 0.00029, "cost": 22.3265548},
        "ETHUSDC": {"entry": 1915.30, "qty": 0.0036, "cost": 6.89508},
        "SOLUSDC": {"entry": 94.33, "qty": 0.242, "cost": 22.82786},
    },
}
START = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)   # cycle slot of 12:50
DEPOSITS = {datetime(2026, 10, 18, 20, 0, tzinfo=timezone.utc): 3.75,
            datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc): 31.12}


def slot(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0, hour=ts.hour - ts.hour % 4)


def load():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, "rb"))
    data = closed_only({c: get_candles(symbol=c, interval="4h", limit=1000) for c in COINS})
    pickle.dump(data, open(CACHE, "wb"))
    return data


def real_trades(path):
    out = []
    for r in csv.reader(open(path)):
        ts = datetime.fromisoformat(r[1]).replace(tzinfo=timezone.utc)
        kind = "hold" if r[4].startswith("hold") and r[4] != "hold-part" else r[4]
        out.append(dict(id=int(r[0]), slot=slot(ts), coin=r[2], side=r[3], kind=kind,
                        price=float(r[5])))
    return out


def run(data, **kw):
    times = [k.open_time for k in data["BTCUSDC"]]
    bar_of = {t + H4: i for i, t in enumerate(times)}          # close time -> bar
    lo = bar_of[START]
    flows = {bar_of[t]: a for t, a in DEPOSITS.items()}
    trace = []
    r = simulate(data, basket=COINS, init=INIT, flows=flows, trace=trace, lo=lo,
                 hold_tp=40, **kw)
    sim = [dict(slot=times[i] + H4, coin=c, side=s, kind=k, price=data[c][i].close)
           for i, c, s, k in trace]
    return r, sim


def match(real, sim, tol=1):
    used, pairs, missed = set(), [], []
    for t in real:
        best = None
        for j, s in enumerate(sim):
            if j in used or (s["coin"], s["side"], s["kind"]) != (t["coin"], t["side"], t["kind"]):
                continue
            d = abs((s["slot"] - t["slot"]) / H4)
            if d <= tol and (best is None or d < best[0]):
                best = (d, j)
        if best:
            used.add(best[1]); pairs.append((t, sim[best[1]], best[0]))
        else:
            missed.append(t)
    extra = [s for j, s in enumerate(sim) if j not in used]
    return pairs, missed, extra


def main(argv):
    data = load()
    real = real_trades(argv[0])
    r, sim = run(data)
    pairs, missed, extra = match(real, sim)
    exact = sum(1 for _, _, d in pairs if d == 0)
    print(f"real trades {len(real)} · sim trades {len(sim)} · matched {len(pairs)} "
          f"({len(pairs) / len(real) * 100:.0f}%), same bar {exact} · "
          f"missed {len(missed)} · sim-only {len(extra)}")
    print(f"sim end equity {r['equity']:.2f} (book incl. deposits)")
    fmt = lambda x: f"{x['slot']:%m-%d %H:%M} {x['coin'][:3]} {x['side']:<4} {x['kind']:<9} @{x['price']:g}"
    print("\n-- real, not in sim --")
    for t in missed:
        print(f"  #{t['id']:<3} {fmt(t)}")
    print("-- sim, not in real --")
    for s in extra:
        print(f"       {fmt(s)}")
    print("-- matched off by one bar --")
    for t, s, d in pairs:
        if d:
            print(f"  #{t['id']:<3} real {fmt(t)} | sim {s['slot']:%m-%d %H:%M}")


if __name__ == "__main__":
    main(sys.argv[1:])
