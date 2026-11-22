"""Honest performance: result vs the money actually put in, and vs just holding.

The old headline was `equity / capital_base`, where capital_base = cash + cost of
the open positions. That base moves on every sale (realised profit becomes cash
at cost 1:1), so the percentage jumped around: +16% on 2026-10-09, +7.5% the next
day with nothing traded. It answered no question anyone asks.

This module answers two that do:
  1. How much did I put in, and what is it worth now?   (net deposits vs equity)
  2. Would I have done better just holding the coins?   (benchmark)

The benchmark is a virtual equal-weight basket of the traded coins bought with
the SAME cash flows at the SAME moments: every deposit is split equally across
the coins at that cycle's prices; a withdrawal shrinks every coin's amount by
the same fraction. Its value needs nothing but the prices the cycle already
has, so it costs zero API calls.

Pure functions only — persistence is in src/store.py.
"""

from __future__ import annotations

from dataclasses import dataclass


def apply_flow(units: dict[str, float], amount: float,
               prices: dict[str, float], coins: list[str]) -> dict[str, float]:
    """Return the benchmark after a deposit (amount > 0) or withdrawal (< 0).

    Deposit: split equally over `coins` that have a price. Withdrawal: scale all
    holdings by (1 - |amount| / value), floored at zero — a withdrawal larger than
    the benchmark (possible: the bot may have outperformed) empties it rather
    than going negative.
    """
    out = dict(units)
    if amount > 0:
        priced = [c for c in coins if prices.get(c, 0) > 0]
        if not priced:
            raise ValueError("no prices to buy the benchmark with")
        share = amount / len(priced)
        for c in priced:
            out[c] = out.get(c, 0.0) + share / prices[c]
    elif amount < 0:
        value = bench_value(out, prices)
        if value is None or value <= 0:
            return out
        keep = max(0.0, 1.0 + amount / value)
        out = {c: q * keep for c, q in out.items()}
    return out


def bench_value(units: dict[str, float], prices: dict[str, float]) -> float | None:
    """Benchmark value now, or None when a held coin has no price this cycle
    (a wrong number is worse than no number)."""
    total = 0.0
    for c, q in units.items():
        if q <= 0:
            continue
        p = prices.get(c)
        if not p:
            return None
        total += q * p
    return total


@dataclass(frozen=True)
class Performance:
    net_deposits: float          # sum of all flows (deposits − withdrawals)
    equity: float                # bot value now (mark-to-market)
    bench: float | None          # same flows, just held

    @property
    def result(self) -> float:
        return self.equity - self.net_deposits

    @property
    def result_pct(self) -> float:
        return self.result / self.net_deposits * 100 if self.net_deposits > 0 else 0.0

    @property
    def bench_pct(self) -> float | None:
        if self.bench is None or self.net_deposits <= 0:
            return None
        return (self.bench / self.net_deposits - 1) * 100

    @property
    def edge(self) -> float | None:
        """Bot minus benchmark, in quote currency (positive = bot is better)."""
        return None if self.bench is None else self.equity - self.bench


def coins_label(coins: list[str], quote: str) -> str:
    return "/".join(c.replace(quote, "") for c in coins)


def report_lines(perf: Performance, coins: list[str], quote: str) -> list[str]:
    """Telegram lines (HTML). Two lines: what you put in vs now, and vs holding."""
    lines = [f"💼 Внесено: {perf.net_deposits:.2f} · сейчас {perf.equity:.2f} · "
             f"итог <b>{perf.result:+.2f}</b> {quote} ({perf.result_pct:+.1f}%)"]
    if perf.bench is None:
        lines.append("📊 Сравнение с «просто держать»: нет цен в этом цикле")
    else:
        word = "лучше" if perf.edge >= 0 else "хуже"
        lines.append(f"📊 Просто держать {coins_label(coins, quote)}: {perf.bench:.2f} "
                     f"({perf.bench_pct:+.1f}%) → бот {word} на <b>{abs(perf.edge):.2f}</b>")
    return lines


def console_lines(perf: Performance, quote: str) -> list[str]:
    out = [f"net deposits      : {perf.net_deposits:>10.2f} {quote}",
           f"result vs deposits: {perf.result:>+10.2f} {quote}  ({perf.result_pct:+.2f}%)"]
    if perf.bench is None:
        out.append("buy-&-hold bench  :        n/a (missing price)")
    else:
        out.append(f"buy-&-hold bench  : {perf.bench:>10.2f} {quote}  ({perf.bench_pct:+.2f}%)"
                   f"  bot edge {perf.edge:+.2f}")
    return out
