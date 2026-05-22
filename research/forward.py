"""
Forward-paper tracker for the Conservative 50/50 book.

Accumulates a REAL forward track record: each daily `step()` refreshes live
data, marks the book to market, and appends to a persistent JSON. The
inception date is fixed at first run — everything before it is clearly
labelled as backfilled context, not a forward result.

Usage:
  python -m research.forward init           # start the forward record (today)
  python -m research.forward step           # run daily (cron / /loop)
  python -m research.forward report         # show status
  python -m research.forward notify-preview # build weekly TG text, print only
  python -m research.forward notify         # force-send weekly TG report now
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

from research.book import book_series, target_weights

# On Railway / a server, point this at the mounted persistent volume so the
# track record survives redeploys. Default = next to this file (local dev).
STATE = pathlib.Path(os.getenv("FORWARD_STATE_PATH",
                               str(pathlib.Path(__file__).parent /
                                   "forward_state.json")))
CAPITAL0 = float(os.getenv("FORWARD_CAPITAL", "10000"))

# Weekly Telegram report (sent by the tracker — the bot can't see this volume).
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID") or os.getenv("ADMIN_CHAT_ID")
REPORT_WEEKDAY = int(os.getenv("REPORT_WEEKDAY", "3"))   # Mon=0 .. Sun=6; 3=Thu
GO_LIVE_DAY = 60
DECISION_DATE = "2026-07-19"


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
    notify_weekly()


def _status(s: dict) -> dict | None:
    """Compute forward metrics + GO_LIVE risk flags. None if no bars yet."""
    fc = s.get("forward_curve") or []
    if not fc:
        return None
    eq = fc[-1]["equity_eur"]
    rets = [d["ret"] for d in fc]
    days = len(fc)
    peak = max(d["equity_eur"] for d in fc)
    cur_dd = (1 - eq / peak) * 100
    tot = (eq / s["capital0"] - 1) * 100
    ann = ((eq / s["capital0"]) ** (365 / days) - 1) * 100
    # worst rolling 7-bar return (proxy for "no single-week DD > 8%" rule)
    worst_week = 0.0
    for i in range(len(rets)):
        window = rets[max(0, i - 6):i + 1]
        wk = 1.0
        for r in window:
            wk *= (1 + r)
        worst_week = min(worst_week, (wk - 1) * 100)
    flags = []
    if cur_dd > 12:
        flags.append(f"DD {cur_dd:.1f}% over the 12% GO_LIVE limit")
    if worst_week < -8:
        flags.append(f"single-week move {worst_week:.1f}% breached -8%")
    if days >= 14 and ann < 5:
        flags.append(f"ann ≈ {ann:+.1f}% below the +5% target")
    return {"days": days, "eq": eq, "tot": tot, "ann": ann,
            "cur_dd": cur_dd, "worst_week": worst_week, "flags": flags}


def report() -> None:
    s = _load()
    if not s:
        print("no forward record — run: python -m research.forward init")
        return
    print(f"=== Forward-paper: Conservative 50/50 book ===")
    print(f"inception {s['inception']} | capital €{s['capital0']:,.0f} | "
          f"context OOS CAGR {s['backfill_oos_cagr']}% (not a forward claim)")
    st = _status(s)
    if st is None:
        print("forward days: 0 (track record starts on the next daily bar)")
    else:
        print(f"forward days: {st['days']} | equity €{st['eq']:,.2f} | "
              f"total {st['tot']:+.2f}% | ann ≈ {st['ann']:+.1f}% | "
              f"curr DD {st['cur_dd']:.1f}%")
    print("\nToday's target book:")
    print(json.dumps(target_weights(refresh=False), indent=2, ensure_ascii=False))


def _telegram_text(s: dict) -> str:
    incep = s["inception"]
    st = _status(s)
    head = "📊 Forward 50/50 book — weekly report"
    if st is None:
        return (f"{head}\n\nDay 0 / {GO_LIVE_DAY} (since {incep}).\n"
                f"Track record starts on the next daily bar. Nothing to judge yet.")
    lines = [
        head,
        f"Day {st['days']} / {GO_LIVE_DAY}  (since {incep})",
        f"Equity €{st['eq']:,.2f}  |  total {st['tot']:+.2f}%  |  DD {st['cur_dd']:.1f}%",
        "",
    ]
    if st["flags"]:
        lines.append("⚠ GO_LIVE watch:")
        lines += [f"  • {f}" for f in st["flags"]]
    else:
        lines.append("✅ No GO_LIVE threshold breached.")
    lines += [
        "",
        f"Decision day: {DECISION_DATE} (day {GO_LIVE_DAY}). "
        f"Daily wiggles (±1.6%) are noise — judged only at day {GO_LIVE_DAY}.",
    ]
    return "\n".join(lines)


def _tg_send(text: str) -> bool:
    if not (BOT_TOKEN and REPORT_CHAT_ID):
        print("telegram not configured (need TELEGRAM_BOT_TOKEN + REPORT_CHAT_ID)")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": REPORT_CHAT_ID, "text": text,
                  "disable_web_page_preview": True},
            timeout=15,
        )
        if not r.ok:
            print(f"telegram send failed: {r.status_code} {r.text[:200]}")
        return r.ok
    except Exception as e:
        print(f"telegram send error: {e}")
        return False


def notify_weekly(force: bool = False) -> None:
    """Send the weekly report once per ISO week, on/after REPORT_WEEKDAY.

    Idempotent: rides the existing 5-min cron via step(); the per-week tag in
    state guarantees exactly one push per week even though step runs often.
    """
    s = _load()
    if not s:
        return
    today = datetime.now(timezone.utc).date()
    iso_year, iso_week, iso_wd = today.isocalendar()   # iso_wd: 1=Mon..7=Sun
    tag = f"{iso_year}-W{iso_week:02d}"
    if not force:
        if s.get("last_notify_week") == tag:
            return
        if (iso_wd - 1) < REPORT_WEEKDAY:   # not yet the target weekday
            return
    if _tg_send(_telegram_text(s)):
        s["last_notify_week"] = tag
        _save(s)
        print(f"weekly report sent ({tag})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {
        "init": init,
        "step": step,
        "report": report,
        "notify": lambda: notify_weekly(force=True),
        "notify-preview": lambda: print(_telegram_text(_load())),
    }.get(cmd, report)()