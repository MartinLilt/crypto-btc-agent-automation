"""Plan B data: 4h bars of the basket at four different wake-up hours.

The 2026-08-22 noise-floor finding: shifting only WHEN the bot wakes up moved
results 16-24pp. A real improvement must survive that. So every plan B variant
is run on 4h bars built from 1h candles with the 4h boundary at +0/+1/+2/+3h
(offset 0 = the real Binance 4h bars, which the sanity check below asserts).

    python -m backtest.planb check      # download/cache 1h, verify offset 0
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


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        check()
