"""Shadow-flow signals: is the move carried by SPOT demand or by LEVERAGE?

The order book was ruled out in August — spoofed, and HFT-timescale. Funding is
the opposite: it cannot be faked, because it is an obligation, not an intention.
Positive funding means longs are PAYING to stay long, so a rally on high funding
is being pushed by leverage rather than bought by real demand — exactly the kind
of advance that later hands us frozen bags. The perp-spot basis says the same
thing continuously.

Both have YEARS of free history (funding via /fapi/v1/fundingRate paginated by
startTime, basis from perp vs spot klines), which is why they are testable at
all — open interest and long/short ratios only keep ~30 days and can therefore
be forward-tested but never backtested honestly.

    python -m backtest.shadow          # download (cached) + run the gate sweep

Findings 2026-08-22 (2.57y, EUR5000, live config, both disjoint halves):
  * no NEW HOLD while funding > ~16%/yr annualised: +67.1% vs +58.5%, and the
    11 / 16 / 22%/yr neighbours all beat the base in both halves.
  * no NEW BAG while funding is elevated: frozen capital 16.6% -> 8.5% and peak
    bags 77 -> 53, at a cost of ~8pp of return. A real dial for the freeze.
  * blocking too much kills it: at 5%/yr the gate is on 45% of the time and the
    return collapses to +31.7% — it starves the sleeve that actually earns.
CAVEAT: the absolute threshold was chosen on this sample, where funding was
structurally higher in the bull half — so it partly acts as a "bull-only"
filter. Forward-test in report-only mode before trusting it with money.
"""

import os
import pickle
import sys
import time
from datetime import datetime, timezone

from backtest.portfolio import simulate, WARM
from src.funding import _get
from backtest.portfolio import BASKET, load

CACHE = os.path.join(os.path.dirname(__file__), "shadow_cache.pkl")


def funding_history(symbol, start_ms, end_ms):
    """Paginated 8h funding rates — the public endpoint caps at 1000 per call."""
    out, cursor = [], start_ms
    while cursor < end_ms:
        raw = _get("/fapi/v1/fundingRate",
                   {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000})
        if not raw:
            break
        out += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in raw]
        nxt = int(raw[-1]["fundingTime"]) + 1
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.15)
    return out


def perp_klines(symbol, start_ms, end_ms):
    """4h PERP closes — paired with spot closes this gives the basis."""
    out, cursor = [], start_ms
    while cursor < end_ms:
        raw = _get("/fapi/v1/klines", {"symbol": symbol, "interval": "4h",
                                       "startTime": cursor, "limit": 1000})
        if not raw:
            break
        out += [(int(k[0]), float(k[4])) for k in raw]
        nxt = int(raw[-1][0]) + 1
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.15)
    return out


def build():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, "rb"))
    spot = load()
    times = {c: [int(k.open_time.timestamp() * 1000) for k in spot[c]] for c in BASKET}
    start = min(t[0] for t in times.values())
    end = max(t[-1] for t in times.values()) + 4 * 3600 * 1000
    out = {}
    for coin in BASKET:                       # USDT perps carry the liquidity
        fund = funding_history(coin, start, end)
        perp = dict(perp_klines(coin, start, end))
        # forward-fill the 8h funding onto our 4h grid (no lookahead: at bar t we
        # only know funding stamped at or before t)
        f_series, j, last = [], 0, 0.0
        for t in times[coin]:
            while j < len(fund) and fund[j][0] <= t:
                last = fund[j][1]; j += 1
            f_series.append(last)
        basis = [(perp.get(t, 0.0) / k.close - 1) * 100 if perp.get(t) else 0.0
                 for t, k in zip(times[coin], spot[coin])]
        out[coin] = {"funding": f_series, "basis": basis}
        print(f"  {coin}: {len(fund)} funding points, {len(perp)} perp bars, "
              f"funding {min(f_series)*100:.4f}%..{max(f_series)*100:.4f}% per 8h")
    pickle.dump(out, open(CACHE, "wb"))
    return out



# ── gate sweep ────────────────────────────────────────────────────────────

def main(argv):
    data, shadow = load(), build()
    times = [c.open_time for c in data["BTCUSDT"]]
    split = next(i for i, t in enumerate(times)
                 if t >= datetime(2025, 2, 1, tzinfo=timezone.utc))
    spans = [("full", WARM, len(times)), ("H1", WARM, split), ("H2", split, len(times))]
    base = {lab: simulate(data, hold_pct=0.25, hold_tp=40, lo=lo, hi=hi)
            for lab, lo, hi in spans}

    def gate(thr):
        return {c: [x > thr for x in shadow[c]["funding"]] for c in BASKET}

    def share(b):
        return sum(sum(v) for v in b.values()) / sum(len(v) for v in b.values()) * 100

    print("=== leverage-crowding gate | EUR5000 | hold 25% + partial TP ===")
    print(f"  {'gate':>18} {'on':>5} | {'full%':>7} {'H1%':>7} {'H2%':>7} | "
          f"{'mdd':>6} {'frozen':>7} {'peak':>5}")
    print("  " + "-" * 74)
    f = base["full"]
    print(f"  {'BASE (no gate)':>18} {'0%':>5} | {f['ret']:>+6.1f}% {base['H1']['ret']:>+6.1f}% "
          f"{base['H2']['ret']:>+6.1f}% | {f['mdd']:>5.1f}% {f['frozen']:>6.1f}% {f['peak_bags']:>5}")
    for thr in (0.00005, 0.0001, 0.00015, 0.0002, 0.0003):
        b = gate(thr)
        for mode, kw in (("no hold", dict(block_hold=b)), ("no bag", dict(block_bag=b)),
                         ("neither", dict(block_hold=b, block_bag=b))):
            r = {lab: simulate(data, hold_pct=0.25, hold_tp=40, lo=lo, hi=hi, **kw)
                 for lab, lo, hi in spans}
            f = r["full"]
            win = r["H1"]["ret"] > base["H1"]["ret"] and r["H2"]["ret"] > base["H2"]["ret"]
            lab = f"{thr * 3 * 365 * 100:.0f}%/yr {mode}"
            print(f"  {lab:>18} {share(b):>4.0f}% | "
                  f"{f['ret']:>+6.1f}% {r['H1']['ret']:>+6.1f}% {r['H2']['ret']:>+6.1f}% | "
                  f"{f['mdd']:>5.1f}% {f['frozen']:>6.1f}% {f['peak_bags']:>5}"
                  f"{'  <- both halves' if win else ''}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
