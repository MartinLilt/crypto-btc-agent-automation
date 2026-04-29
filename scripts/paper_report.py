#!/usr/bin/env python3
"""
Paper-trading report — summarize paper_trades table and compare vs backtest expectation.

Usage:
    python -m scripts.paper_report                  # all assets, all-time
    python -m scripts.paper_report --days 30        # last 30 days
    python -m scripts.paper_report --symbol SOLUSDT # single asset
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import get_paper_trades, init_db

logging.basicConfig(level=logging.WARNING)

# Backtest expectations from 720d research (2026-04-27)
BACKTEST_BASELINE = {
    "BTCUSDT": {"wr_pct": 39.1, "net_pct_per_signal": 22.22 / 69, "sigs_per_30d": 69 / 24},
    "ETHUSDT": {"wr_pct": 38.9, "net_pct_per_signal": 9.79 / 90,  "sigs_per_30d": 90 / 24},
    "SOLUSDT": {"wr_pct": 47.8, "net_pct_per_signal": 60.20 / 134, "sigs_per_30d": 134 / 24},
}


def _summary(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    closed = [t for t in trades if t["status"] != "OPEN"]
    open_n = len(trades) - len(closed)
    if not closed:
        return {"n": len(trades), "open": open_n, "closed": 0}
    wins = [t for t in closed if t["status"] == "TP_HIT"]
    losses = [t for t in closed if t["status"] == "SL_HIT"]
    timeouts = [t for t in closed if t["status"] == "TIMEOUT"]
    total_net = sum(t["pnl_pct_net_fees"] or 0 for t in closed)
    avg_hold = sum(t["hold_hours"] or 0 for t in closed) / len(closed)
    return {
        "n":         len(trades),
        "open":      open_n,
        "closed":    len(closed),
        "wins":      len(wins),
        "losses":    len(losses),
        "timeouts":  len(timeouts),
        "wr_pct":    len(wins) / len(closed) * 100,
        "total_net": total_net,
        "avg_hold":  avg_hold,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Filter by symbol (e.g. BTCUSDT)")
    parser.add_argument("--days", type=int, help="Filter to last N days by entry_time")
    args = parser.parse_args()

    init_db()
    trades = get_paper_trades(symbol=args.symbol, limit=10_000)

    if args.days:
        cutoff = datetime.now(timezone.utc).timestamp() - args.days * 86400
        trades = [
            t for t in trades
            if datetime.fromisoformat(
                t["entry_time"].replace("Z", "+00:00")
            ).timestamp() >= cutoff
        ]

    if not trades:
        print("No paper trades found.")
        return 0

    earliest = min(t["entry_time"] for t in trades)[:10]
    latest   = max(t["entry_time"] for t in trades)[:10]
    print(f"Paper trades range: {earliest} → {latest}")
    if args.symbol:
        print(f"Symbol filter: {args.symbol}")
    if args.days:
        print(f"Last {args.days} days")
    print()

    print("=" * 96)
    print(f'{"Symbol":<10} | {"N":>4} | {"Open":>4} | {"WR%":>5} | {"Net%":>7} | '
          f'{"AvgHold":>7} | {"Backtest WR":>11} | {"Δ vs BT":>8}')
    print("=" * 96)

    for sym in sorted({t["symbol"] for t in trades}):
        sym_trades = [t for t in trades if t["symbol"] == sym]
        s = _summary(sym_trades)
        if s["n"] == 0:
            continue
        wr = s.get("wr_pct", 0)
        net = s.get("total_net", 0)
        hold = s.get("avg_hold", 0)
        bt = BACKTEST_BASELINE.get(sym, {})
        bt_wr = bt.get("wr_pct", 0)
        delta = wr - bt_wr if s.get("closed", 0) > 0 else 0
        print(f'{sym:<10} | {s["n"]:>4} | {s["open"]:>4} | {wr:>5.1f} | '
              f'{net:>7.2f} | {hold:>7.1f}h | {bt_wr:>10.1f}% | {delta:>+7.1f}pp')

    print("-" * 96)
    s = _summary(trades)
    if s.get("closed", 0):
        print(f'{"TOTAL":<10} | {s["n"]:>4} | {s["open"]:>4} | {s["wr_pct"]:>5.1f} | '
              f'{s["total_net"]:>7.2f} | {s["avg_hold"]:>7.1f}h')

    print()
    print(f'Wins: {s.get("wins", 0)}  Losses: {s.get("losses", 0)}  Timeouts: {s.get("timeouts", 0)}')

    return 0


if __name__ == "__main__":
    sys.exit(main())