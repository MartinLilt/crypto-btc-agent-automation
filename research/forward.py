"""
Forward-paper tracker for the Conservative 50/50 book.

Accumulates a REAL forward track record: each daily `step()` refreshes live
data, marks the book to market, and appends to a persistent JSON. The
inception date is fixed at first run — everything before it is clearly
labelled as backfilled context, not a forward result.

Usage:
  python -m research.forward init      # start the forward record (today)
  python -m research.forward step      # run daily (cron / /loop)
  python -m research.forward report     # show status
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import pandas as pd

from research.book import book_series, target_weights

# On Railway / a server, point this at the mounted persistent volume so the
# track record survives redeploys. Default = next to this file (local dev).
STATE = pathlib.Path(os.getenv("FORWARD_STATE_PATH",
                               str(pathlib.Path(__file__).parent /
                                   "forward_state.json")))
CAPITAL0 = float(os.getenv("FORWARD_CAPITAL", "10000"))


def _load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def _save(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False))


def init() -> None:
    bs = book_series(refresh=True)
    today = datetime.now(timezone.utc).date().isoformat()
    s = {
        "inception": today,
        "capital0": CAPITAL0,
        "last_processed": str(bs["dt"].iloc[-1].date()),
        "forward_curve": [],            # [{date, equity_eur, ret}]
        "backfill_oos_cagr": None,
    }
    # context: OOS slice CAGR for reference (not a forward claim)
    k = int(len(bs) * 0.60)
    oos = bs.iloc[k:]
    eq = (1 + oos["ret"]).prod()
    yrs = len(oos) / 365.0
    s["backfill_oos_cagr"] = round((eq ** (1 / yrs) - 1) * 100, 1)
    _save(s)
    print(f"forward record started {today} | capital €{CAPITAL0:,.0f}")
    print(f"context (backfill OOS CAGR, NOT a forward result): "
          f"{s['backfill_oos_cagr']}%")
    print("\nToday's target book:")
    print(json.dumps(target_weights(refresh=False), indent=2, ensure_ascii=False))


def step() -> None:
    s = _load()
    if not s:
        return init()
    bs = book_series(refresh=True)
    last_done = pd.Timestamp(s["last_processed"], tz="UTC")
    incep = pd.Timestamp(s["inception"], tz="UTC")
    new = bs[(bs["dt"] > last_done) & (bs["dt"] >= incep)]
    if new.empty:
        print(f"no new bar since {s['last_processed']}")
    else:
        eq = (s["forward_curve"][-1]["equity_eur"]
              if s["forward_curve"] else CAPITAL0)
        for _, row in new.iterrows():
            eq *= (1 + float(row["ret"]))
            s["forward_curve"].append({
                "date": str(row["dt"].date()),
                "equity_eur": round(eq, 2),
                "ret": round(float(row["ret"]), 6),
            })
        s["last_processed"] = str(bs["dt"].iloc[-1].date())
        _save(s)
    report()


def report() -> None:
    s = _load()
    if not s:
        print("no forward record — run: python -m research.forward init")
        return
    fc = s["forward_curve"]
    print(f"=== Forward-paper: Conservative 50/50 book ===")
    print(f"inception {s['inception']} | capital €{s['capital0']:,.0f} | "
          f"context OOS CAGR {s['backfill_oos_cagr']}% (not a forward claim)")
    if not fc:
        print("forward days: 0 (track record starts on the next daily bar)")
        print("\nToday's target book:")
        print(json.dumps(target_weights(refresh=False), indent=2,
                         ensure_ascii=False))
        return
    eq = fc[-1]["equity_eur"]
    rets = pd.Series([d["ret"] for d in fc])
    days = len(fc)
    peak = max(d["equity_eur"] for d in fc)
    cur_dd = (1 - eq / peak) * 100
    tot = (eq / s["capital0"] - 1) * 100
    ann = ((eq / s["capital0"]) ** (365 / days) - 1) * 100 if days else 0
    print(f"forward days: {days} | equity €{eq:,.2f} | total {tot:+.2f}% | "
          f"ann ≈ {ann:+.1f}% | curr DD {cur_dd:.1f}%")
    print("\nToday's target book:")
    print(json.dumps(target_weights(refresh=False), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"init": init, "step": step, "report": report}.get(cmd, report)()