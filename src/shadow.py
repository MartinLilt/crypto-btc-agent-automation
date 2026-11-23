"""Shadow signals: computed and logged every cycle, never traded on.

Plan S (obsidian/vault/plans/2026-11-23 Plan S (shadow).md) forward-tests two
ideas that a backtest could not settle:

  S1  regime from the last CLOSED UTC daily candle (plan B's B2), instead of the
      live 24h window ending at the current 4h bar;
  S2  leverage crowding: the last settled funding rate of the coin's USDT perp.
      Hold gate > 0.00015 per 8h (~16%/yr, the 2026-08-22 pick); bag gate
      > 0.0001 (~11%/yr), observation only.

The evaluation rules were written before the first logged cycle. This module
only measures. Everything here is wrapped so that no failure can reach the
trading loop: on any error the cycle reports "shadow: error" and goes on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .regime import Regime, detect_regime, resample

HOLD_GATE = 0.00015          # per 8h funding — no NEW hold above this (S2)
BAG_GATE = 0.0001            # per 8h funding — no NEW bag above this (observe)
FACTOR = 6                   # 4h bars per day


def daily_regime(candles, now: datetime | None = None) -> Regime | None:
    """Regime from the daily candles closed at 00:00 UTC — the live twin of the
    simulator's utc_daily(). Uses only 4h candles already fetched this cycle:
    drop the one still forming, cut at the last bar that closed a UTC day
    (opens 20:00), and resample in groups of 6 ending exactly there.
    None when there is not enough history."""
    now = now or datetime.now(timezone.utc)
    closed = [c for c in candles if c.close_time <= now]
    j = next((i for i in range(len(closed) - 1, -1, -1)
              if closed[i].open_time.hour == 20), None)
    if j is None:
        return None
    upto = closed[: j + 1]
    upto = upto[len(upto) % FACTOR:]           # whole days, ending at the close
    r = detect_regime(resample(upto, FACTOR))
    if r.slope_pct is None:
        return None
    return r.regime


def funding_now(symbols: list[str], quote: str) -> dict[str, float | None]:
    """Last SETTLED funding rate per coin (USDT perp: where the leverage is).
    One weight-1 request per coin on the futures API; a failure is None."""
    from .funding import _get
    out: dict[str, float | None] = {}
    for s in symbols:
        try:
            raw = _get("/fapi/v1/fundingRate",
                       {"symbol": s.replace(quote, "USDT"), "limit": 1})
            out[s] = float(raw[-1]["fundingRate"]) if raw else None
        except Exception:
            out[s] = None
    return out


def rows(coins, candles_by, regimes, notes, funding, now=None) -> list[dict]:
    out = []
    for c in coins:
        f = funding.get(c)
        d = daily_regime(candles_by[c], now)
        out.append(dict(
            symbol=c, live_regime=regimes[c].value,
            daily_regime=d.value if d else None, funding=f,
            hold_gate=None if f is None else f > HOLD_GATE,
            bag_gate=None if f is None else f > BAG_GATE,
            action=notes.get(c, "") or None,
        ))
    return out


def _yr(f: float) -> str:
    return f"{f * 3 * 365 * 100:+.0f}%/год"


def report(rs: list[dict], quote: str) -> tuple[list[str], list[str]]:
    """(console lines, Telegram lines). Telegram only gets a line when an idea
    DISAGREES with what the bot did; a quiet shadow stays out of the report."""
    con, tg = [], []
    parts = []
    for r in rs:
        c = r["symbol"].replace(quote, "")
        d = (r["daily_regime"] or "?")[0]
        f = "n/a" if r["funding"] is None else _yr(r["funding"])
        parts.append(f"{c}:{d} {f}")
        if r["daily_regime"] and r["daily_regime"] != r["live_regime"]:
            tg.append(f"{c}: по дневной свече {r['daily_regime']} (бот: {r['live_regime']})")
        act = r["action"] or ""
        if r["hold_gate"] and r["live_regime"] == "BULL" and "HOLD-BUY" in act:
            tg.append(f"{c}: funding {_yr(r['funding'])} — фильтр запретил бы этот холд")
        if r["bag_gate"] and act.endswith("BUY") and "HOLD" not in act:
            tg.append(f"{c}: funding {_yr(r['funding'])} — фильтр мешков запретил бы покупку")
    con.append("shadow (S1 daily · S2 funding): " + " · ".join(parts))
    return con, (["🧪 <b>Тень</b> (не торгует):", *tg] if tg else [])


def run(store, coins, candles_by, regimes, notes, quote) -> tuple[list[str], list[str]]:
    """Compute, log, report. Never raises."""
    try:
        rs = rows(coins, candles_by, regimes, notes, funding_now(coins, quote))
        try:
            store.log_shadow(rs)
        except Exception as exc:
            print(f"  ⚠ shadow log: {type(exc).__name__}: {str(exc)[:80]}")
        return report(rs, quote)
    except Exception as exc:
        return [f"shadow            : error {type(exc).__name__}: {str(exc)[:70]}"], []
