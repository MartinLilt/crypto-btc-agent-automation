"""Plan S review — run NOT BEFORE 2027-02-15. Applies the rules fixed in
`obsidian/vault/plans/2026-11-23 Plan S (shadow).md` before the first logged
cycle; it does not choose them.

Inputs (export from the box, see the plan):
    shadow.csv   id,ts,symbol,live_regime,daily_regime,funding,hold_gate,bag_gate,action
    trades.csv   id,ts,symbol,side,kind,price,qty,usdt,pnl            (account 1)
    flows.csv    id,ts,amount                                          (account 1)
    book.json    the live book at the first shadow cycle: {ts, cash, bags, holds}

    python -m backtest.shadow_review shadow.csv trades.csv flows.csv book.json

Replay scale: the rule says "real book at the start, real deposits, book 5000".
Both hold together by scaling the real book and every deposit by 5000/equity
at the start — the $6 exchange floor would otherwise make a $120 book behave
unlike any larger one.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from backtest.planb import closed_only
from backtest.portfolio import regime_series, simulate, utc_daily
from src.binance_api import get_candles

COINS = ["BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC"]
H4 = timedelta(hours=4)
EARLIEST = datetime(2027, 2, 15, tzinfo=timezone.utc)


def ts(s):
    t = datetime.fromisoformat(s.replace(" ", "T"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def slot(t):
    return t.replace(minute=0, second=0, microsecond=0, hour=t.hour - t.hour % 4)


def rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def b(x):
    return {"t": True, "true": True, "f": False, "false": False}.get(str(x).lower())


def main(argv):
    sh_path, tr_path, fl_path, book_path = argv[:4]
    now = datetime.now(timezone.utc)
    if now < EARLIEST and "--early" not in argv:
        sys.exit(f"too early: the plan allows results from {EARLIEST:%Y-%m-%d} "
                 f"(pass --early only to test the script, and do not read the verdict)")

    sh = rows(sh_path)
    trades = rows(tr_path)
    book = json.load(open(book_path))
    start = ts(book["ts"])
    data = closed_only({c: get_candles(symbol=c, interval="4h", limit=1000) for c in COINS})
    times = [k.open_time for k in data["BTCUSDC"]]
    bar_closing = {t + H4: i for i, t in enumerate(times)}
    print(f"shadow rows {len(sh)} · from {start:%Y-%m-%d %H:%M} · candles to "
          f"{times[-1] + H4:%Y-%m-%d %H:%M}")

    # ── S1.1 consistency with the simulator ──────────────────────────────
    sim_daily = {c: utc_daily(regime_series([k.close for k in data[c]]), data[c]) for c in COINS}
    agree = total = 0
    for r in sh:
        i = bar_closing.get(slot(ts(r["ts"])))
        if i is None or not r["daily_regime"]:
            continue
        total += 1
        agree += sim_daily[r["symbol"]][i] == r["daily_regime"]
    cons = agree / total * 100 if total else 0.0
    print(f"\nS1.1 consistency: {agree}/{total} = {cons:.1f}% (need >= 95%)")

    # ── S1.2 evidence: BULL disagreements (episodes per coin) ────────────
    episodes, prev = 0, defaultdict(bool)
    for r in sorted(sh, key=lambda r: (r["symbol"], r["ts"])):
        d = (r["live_regime"] == "BULL") != (r["daily_regime"] == "BULL")
        if d and not prev[r["symbol"]]:
            episodes += 1
        prev[r["symbol"]] = d
    print(f"S1.2 BULL disagreement episodes: {episodes} (need >= 3)")

    # ── S1.3 replay, scaled to 5000 ──────────────────────────────────────
    lo = bar_closing[slot(start)]
    px = {c: data[c][lo].close for c in COINS}
    eq0 = book["cash"] + sum(x["qty"] * px[c] for c, v in book["bags"].items() for x in v) \
        + sum(h["qty"] * px[c] for c, h in book["holds"].items() if h)
    k = 5000 / eq0
    init = {"cash": book["cash"] * k,
            "bags": {c: [dict(x, qty=x["qty"] * k, cost=x["cost"] * k) for x in v]
                     for c, v in book["bags"].items()},
            "holds": {c: dict(h, qty=h["qty"] * k, cost=h["cost"] * k)
                      for c, h in book["holds"].items() if h}}
    flows = {}
    for f in rows(fl_path):
        t = ts(f["ts"])
        if t > start and slot(t) in bar_closing:
            flows[bar_closing[slot(t)]] = flows.get(bar_closing[slot(t)], 0) + float(f["amount"]) * k
    res = {name: simulate(data, basket=COINS, init=init, flows=flows, lo=lo, hold_tp=40, **kw)
           for name, kw in (("base", {}), ("S1", dict(regime_mode="utc_daily")))}
    for name, r in res.items():
        print(f"S1.3 {name:<4} equity {r['equity']:8.1f} ({r['ret']:+.2f}% of 5000) · "
              f"holds {r['n_hold']} · red exits {r['hold_loss_n']} ({r['hold_loss_sum']:+.0f})")
    s1_pass = (cons >= 95 and episodes >= 3 and res["S1"]["equity"] >= res["base"]["equity"]
               and res["S1"]["hold_loss_n"] <= res["base"]["hold_loss_n"])
    print(f"S1 verdict: {'PASS → candidate (owner decides, H2 caveat stands)' if s1_pass else 'FAIL / not enough evidence'}"
          + ("" if cons >= 95 else " — consistency below 95%: fix the measurement first")
          + ("" if episodes >= 3 else " — < 3 episodes: extend the period"))

    # ── S2 hold gate ─────────────────────────────────────────────────────
    last = {c: data[c][-1].close for c in COINS}
    gated, bull_rows, gate_on = [], 0, 0
    for r in sh:
        if r["live_regime"] == "BULL":
            bull_rows += 1
            gate_on += bool(b(r["hold_gate"]))
        if b(r["hold_gate"]) and "HOLD-BUY" in (r["action"] or ""):
            gated.append(r)
    tr = sorted(trades, key=lambda t: int(t["id"]))
    pnl_gated = 0.0
    for g in gated:
        t0 = ts(g["ts"]) - timedelta(minutes=10)
        buy = next(t for t in tr if t["symbol"] == g["symbol"] and t["side"] == "BUY"
                   and t["kind"] == "hold" and ts(t["ts"]) >= t0)
        qty, cost, pnl = float(buy["qty"]), float(buy["usdt"]), 0.0
        for t in tr:
            if int(t["id"]) <= int(buy["id"]) or t["symbol"] != g["symbol"] or t["side"] != "SELL":
                continue
            if t["kind"] == "hold-part":
                pnl += float(t["pnl"]); qty -= float(t["qty"])
            elif t["kind"] == "hold":
                pnl += float(t["pnl"]); qty = 0
                break
        if qty > 1e-12:                                  # still open: mark to market
            frac = qty / float(buy["qty"])
            pnl += qty * last[g["symbol"]] - cost * frac
        pnl_gated += pnl
        print(f"S2 gated hold {g['symbol']} {g['ts'][:16]} funding {float(g['funding']):.5f} → pnl {pnl:+.2f}")
    share = gate_on / bull_rows * 100 if bull_rows else 0.0
    s2_pass = len(gated) >= 3 and pnl_gated < 0 and share <= 30
    print(f"S2: {len(gated)} gated holds (need >= 3), their P&L {pnl_gated:+.2f} (need < 0), "
          f"gate on {share:.0f}% of BULL rows (need <= 30%) → "
          f"{'PASS → candidate (owner decides)' if s2_pass else 'FAIL / not enough evidence'}")

    # ── S2 bag gate: observation only ────────────────────────────────────
    on, off = [], []
    for r in sh:
        a = r["action"] or ""
        if a.endswith("BUY") and "HOLD" not in a and r["bag_gate"] != "":
            buy = next((t for t in tr if t["symbol"] == r["symbol"] and t["side"] == "BUY"
                        and t["kind"] == "bag" and abs((ts(t["ts"]) - ts(r["ts"])).total_seconds()) < 900), None)
            if buy:
                move = last[r["symbol"]] / float(buy["price"]) - 1
                (on if b(r["bag_gate"]) else off).append(move)
    avg = lambda v: sum(v) / len(v) * 100 if v else 0.0
    print(f"S2 bags (observe): gate on {len(on)} bags, avg move since buy {avg(on):+.1f}% · "
          f"gate off {len(off)}, {avg(off):+.1f}%")


if __name__ == "__main__":
    main(sys.argv[1:])
