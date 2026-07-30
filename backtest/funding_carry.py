"""Funding-carry screener — measure the delta-neutral carry yield per coin.

Delta-neutral carry (long spot + short perp of equal size): price cancels, you
collect funding each 8h. Realized carry over the window:

    gross  = Σ funding_rate_i           (short perp receives when rate > 0)
    net    = gross − round-trip fees     (open+close both legs ≈ 4 × taker fee)
    annualised = net × 365 / window_days

A coin is worth carrying when net annualised > 0 with funding mostly positive
(stable). This is a STRUCTURAL yield, not a price prediction — no OOS overfit
issue the way TA has. Execution needs a futures short leg (beyond pure spot).

    source .venv/bin/activate
    python -m backtest.funding_carry
"""

from __future__ import annotations

from src.config import config
from src.funding import annualized, get_current_funding, get_funding_history

_LEGS_ROUND_TRIP = 4  # open spot+perp, close spot+perp


def main() -> None:
    fee = config.fee_rate
    print(f"Funding-carry screen | delta-neutral | fee {fee*100:.2f}%/leg "
          f"× {_LEGS_ROUND_TRIP} round-trip\n")
    print(f"universe: {', '.join(config.target_coins)}\n")

    rows = []
    for coin in config.target_coins:
        try:
            h = get_funding_history(coin, 1000)
            if len(h) < 30:
                continue
            rates = [p.rate for p in h]
            days = (h[-1].time - h[0].time).total_seconds() / 86400
            gross = sum(rates)                       # fraction over the window
            net = gross - _LEGS_ROUND_TRIP * fee     # one-time round-trip cost
            ann_gross = gross * 365 / days * 100
            ann_net = net * 365 / days * 100
            pct_pos = sum(1 for r in rates if r > 0) / len(rates) * 100
            cur = get_current_funding(coin)
            rows.append({
                "coin": coin, "ann_net": ann_net, "ann_gross": ann_gross,
                "pos": pct_pos, "cur_ann": annualized(cur), "days": days,
            })
        except Exception as exc:
            print(f"  ({coin} skipped: {str(exc)[:50]})")

    rows.sort(key=lambda x: x["ann_net"], reverse=True)

    print(f"{'coin':>10} {'net %/yr':>9} {'gross %/yr':>11} {'funding+ %':>11} "
          f"{'now %/yr':>9}")
    print("-" * 56)
    for x in rows:
        print(f"{x['coin']:>10} {x['ann_net']:>+8.2f}% {x['ann_gross']:>+10.2f}% "
              f"{x['pos']:>9.0f}% {x['cur_ann']:>+8.2f}%")

    good = [x for x in rows if x["ann_net"] > 0 and x["pos"] >= 55]
    print()
    if good:
        print(f"🏦 Carry-worthy now ({len(good)}): "
              f"{', '.join(x['coin'] for x in good)}")
        best = good[0]
        print(f"   Best: {best['coin']} ~{best['ann_net']:+.1f}%/yr net "
              f"({best['pos']:.0f}% of periods positive).")
        print("   NOTE: needs a futures short leg to run delta-neutral.")
    else:
        print("No coin offers a positive, stable net carry right now — "
              "funding is too thin in this market (bearish → low long demand).")


if __name__ == "__main__":
    main()