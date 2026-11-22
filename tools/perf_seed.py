"""Seed (or extend) an account's performance ledger with past deposits/withdrawals.

The bot books flows itself from the first cycle that runs src/performance.py on.
Flows from before that — or one the bot failed to book (it says so in Telegram) —
go in here. Each flow buys/sells the benchmark at the price of the 1h candle
that contains its timestamp, exactly as the bot would have at that moment.

    # dry run: prints the ledger and what it is worth now, writes nothing
    python -m tools.perf_seed --account 1 \
        --flow 2026-08-05T12:40=41.24 --flow 2026-10-09T12:50=-4

    # write it (refuses if the account already has flows)
    python -m tools.perf_seed --account 1 --flow ... --write

    # add a flow after the fact to an existing ledger
    python -m tools.perf_seed --account 1 --flow 2026-12-01T08:00=50 --append --write

Flows must be given oldest first and, with --append, be newer than what is
already booked: the benchmark is path-dependent (a withdrawal sells a fraction
of whatever the basket is worth at that moment), so order matters.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.binance_api import _get, get_prices  # noqa: E402
from src.performance import apply_flow, bench_value  # noqa: E402
from src.store import get_store  # noqa: E402
from src.universe import get_universe  # noqa: E402


def parse_flow(s: str) -> tuple[datetime, float]:
    when, _, amount = s.partition("=")
    ts = datetime.fromisoformat(when)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts, float(amount)


def price_at(symbol: str, ts: datetime) -> float:
    """Open of the 1h candle containing `ts` (= price at that moment, ±1h)."""
    hour = int(ts.timestamp()) // 3600 * 3600 * 1000
    k = _get("/api/v3/klines", {"symbol": symbol, "interval": "1h",
                                "startTime": hour, "limit": 1})
    if not k:
        raise SystemExit(f"no {symbol} candle at {ts.isoformat()}")
    return float(k[0][1])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--account", type=int, default=1)
    ap.add_argument("--flow", action="append", required=True,
                    help="ISO-UTC-time=amount, e.g. 2026-08-22T16:00=54.61 (repeatable)")
    ap.add_argument("--append", action="store_true",
                    help="add to an existing ledger instead of starting one")
    ap.add_argument("--write", action="store_true", help="commit (default: dry run)")
    a = ap.parse_args()

    flows = [parse_flow(f) for f in a.flow]
    if flows != sorted(flows):
        raise SystemExit("flows must be oldest first")

    store = get_store(a.account)
    if not hasattr(store, "load_perf"):
        raise SystemExit("store has no performance ledger")
    st = store.load_perf()
    if st is not None and not a.append:
        raise SystemExit(f"account {a.account} already has {st['n_flows']} flow(s); "
                         f"use --append to add, or clear flows/perf by hand")
    if st is None and a.append:
        raise SystemExit("nothing to append to: seed first (without --append)")

    universe = list(get_universe())
    bench = dict(st["bench"]) if st else {}
    total = st["net_deposits"] if st else 0.0
    steps = []
    for ts, amount in flows:
        px = {c: price_at(c, ts) for c in universe}
        bench = apply_flow(bench, amount, px, universe)
        total += amount
        steps.append((ts, amount, dict(bench)))
        print(f"{ts:%Y-%m-%d %H:%M}  {amount:+9.2f}  net {total:9.2f}  "
              f"bench@then {bench_value(bench, px):9.2f}  "
              + " ".join(f"{c[:3]}={px[c]:g}" for c in universe))

    now = get_prices(universe)
    print(f"\nstore {store.backend} · account {a.account} · coins {', '.join(universe)}")
    print(f"net deposits {total:.2f} · benchmark now {bench_value(bench, now):.2f}")

    if not a.write:
        print("dry run — nothing written (add --write)")
        return
    for ts, amount, b in steps:
        store.record_flow(amount, "seed" if not a.append else "manual",
                          "tools/perf_seed.py", b, ts=ts)
    print(f"written: {len(steps)} flow(s)")


if __name__ == "__main__":
    main()
