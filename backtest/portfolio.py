"""Portfolio simulator + sleeve attribution — WHO actually earns, the grid or the
BULL buy-&-hold overlay?

Every other backtest here models the grid sleeve ALONE with a per-coin balance.
This one mirrors `main.run_account`: ONE shared USDC pool for the whole basket,
capital-base-scaled bag size, the per-coin daily regime, and — for the first time
— the BULL hold overlay that in live carries most of the P&L.

    python -m backtest.portfolio            # attribution, full period + halves

Findings 2026-08-22 (2.57y, EUR5000 book, live config): hold P&L +1211 vs grid
+187; per DEPLOYED dollar that is +38.6%/yr vs +8.7%/yr, and the grid sleeve is
NEGATIVE in the 2025-02+ half. 66% of the book never leaves cash.
"""
import sys
from datetime import datetime, timezone

from src.binance_api import get_candles
from src.config import config
from src.grid import params_from_config

P = params_from_config()
BASKET = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
TOTAL = 5000.0                  # book size the attribution is quoted at


def load(interval="4h", limit=6000):
    return {c: get_candles(symbol=c, interval=interval, limit=limit) for c in BASKET}


def sma_arr(closes, win):
    """Rolling SMA per bar (None until warm)."""
    out, s = [], 0.0
    for i, v in enumerate(closes):
        s += v
        if i >= win:
            s -= closes[i - win]
        out.append(s / win if i >= win - 1 else None)
    return out

FEE = config.fee_rate
MIN_UNIT = config.grid_min_unit
UNIT_PCT = config.grid_unit_pct or 0.004
HOLD_PCT = config.bull_hold_pct
RG_MA, RG_LOOK, RG_FLAT = config.regime_ma, config.regime_slope_lookback, config.regime_flat_pct
FACTOR = 6                      # 4h -> 1d
WARM = max(P.sma_win, (RG_MA + RG_LOOK + 2) * FACTOR)
BULL, BEAR, NEUTRAL = "BULL", "BEAR", "NEUTRAL"


def regime_series(closes, ma_win=RG_MA, look=RG_LOOK, flat=RG_FLAT,
                  exit_flat=None, exit_band=0.0):
    """Regime per 4h bar, no lookahead.

    `resample` keeps the LAST close of each group, so the daily series visible at
    bar i is just closes[i::-FACTOR] — a stride-6 sample ending at i. Prefix sums
    make the MA/slope at each bar O(1).

    Hysteresis (exit_flat / exit_band): once BULL, a coin STAYS bull until the
    slope drops under `exit_flat` or price falls `exit_band`% under the MA. The
    live rule (exit_flat=flat, exit_band=0) flips on the same line it entered on,
    which is what produces 110 holds averaging 9 days.
    """
    exit_flat = flat if exit_flat is None else exit_flat
    n = len(closes)
    ma = [None] * n
    slope = [None] * n
    for phase in range(FACTOR):
        idx = list(range(phase, n, FACTOR))
        d = [closes[j] for j in idx]
        pre = [0.0]
        for v in d:
            pre.append(pre[-1] + v)
        for k, bar in enumerate(idx):
            if k + 1 < ma_win + look:
                continue
            m = (pre[k + 1] - pre[k + 1 - ma_win]) / ma_win
            e = k + 1 - look
            past = (pre[e] - pre[e - ma_win]) / ma_win
            if past <= 0:
                continue
            ma[bar] = m
            slope[bar] = (m / past - 1) * 100

    out = [NEUTRAL] * n
    bull = False
    for i in range(n):
        if ma[i] is None:
            bull = False
            continue
        price = closes[i]
        if bull:
            bull = slope[i] > exit_flat and price >= ma[i] * (1 - exit_band / 100)
        else:
            bull = slope[i] > flat and price >= ma[i]
        if bull:
            out[i] = BULL
        elif slope[i] < -flat and price < ma[i]:
            out[i] = BEAR
    return out


def simulate(data, *, use_hold=True, use_grid=True, adaptive=True,
             hold_pct=HOLD_PCT, max_bags=None, start=TOTAL, lo=0, hi=None,
             reg=None, hold_trail=0.0, hold_tp=0.0, hold_tp_frac=0.5,
             max_deploy=1.0, exec_mode="close", tp_pct=None, decide_every=1, phase=0,
             block_hold=None, block_bag=None, basket=None):
    max_bags = P.max_bags if max_bags is None else max_bags
    tp_pct = P.tp_pct if tp_pct is None else tp_pct
    # basket order matters: hold allocations are first-come, so whoever
    # flips BULL first takes its slice of the shared cash
    coins = [c for c in (basket or BASKET) if c in data]
    closes = {c: [k.close for k in data[c]] for c in coins}
    # "close": decide on the 4h close, as the live cron does — a take-profit
    #          touched INSIDE the bar is only noticed hours later, at whatever
    #          price the bar happens to end on.
    # "high" : the bag's TP is checked against the bar's HIGH and fills at the
    #          TP price exactly — what a resting limit order on the exchange
    #          would have captured. The upper bound on what the blindness costs.
    highs = {c: [k.high for k in data[c]] for c in coins}
    n = min(len(closes[c]) for c in coins)
    sma100 = {c: sma_arr(closes[c], P.sma_win) for c in coins}
    reg = reg or {c: regime_series(closes[c]) for c in coins}

    cash = start
    bags = {c: [] for c in coins}
    holds = {c: None for c in coins}
    # after a trail/TP exit the coin is locked out until it LEAVES bull, else it
    # would just re-buy on the next bar and the trail becomes a churn machine
    locked = {c: False for c in coins}
    r_grid = r_hold = 0.0
    n_grid = n_hold = 0
    hold_bars = []
    dep_grid = dep_hold = 0.0        # Σ over bars of cost deployed per sleeve
    frozen = 0.0
    peak_eq = start; mdd = 0.0; bars = 0
    peak_bags = 0
    dep_hist: list[float] = []
    bull_now: list[int] = []

    n = n if hi is None else min(n, hi)
    for i in range(max(WARM, lo), n):
        # decide_every>1 = a SLOWER cron (2 = every 8h, 3 = 12h). Indicators are
        # still the 4h ones; the bot simply wakes up less often. Testing whether
        # the "being slow pays" effect keeps going.
        if decide_every > 1 and i % decide_every != phase:
            equity = cash + sum(b["qty"] * closes[c][i] for c in coins for b in bags[c]) \
                + sum(holds[c]["qty"] * closes[c][i] for c in coins if holds[c])
            peak_eq = max(peak_eq, equity)
            mdd = max(mdd, (peak_eq - equity) / peak_eq)
            continue
        invested = sum(b["cost"] for c in coins for b in bags[c]) \
            + sum(h["cost"] for h in holds.values() if h)
        capital_base = cash + invested
        unit = max(MIN_UNIT, UNIT_PCT * capital_base)

        for c in coins:
            price = closes[c][i]
            regime = reg[c][i] if adaptive else NEUTRAL

            if use_hold and regime == BULL:
                # shadow-flow gate: refuse to START a ride while the move is
                # being carried by leverage (an existing hold is untouched)
                if holds[c] is None and block_hold and block_hold[c][i]:
                    continue
                h = holds[c]
                if h is not None:
                    h["peak"] = max(h["peak"], price)
                    # partial take-profit: bank `hold_tp_frac` of the ride once
                    if hold_tp and not h["banked"] and price >= h["entry"] * (1 + hold_tp / 100):
                        q = h["qty"] * hold_tp_frac
                        proceeds = q * price * (1 - FEE)
                        cash += proceeds; r_hold += proceeds - h["cost"] * hold_tp_frac
                        h["qty"] -= q; h["cost"] *= (1 - hold_tp_frac); h["banked"] = True
                        n_hold += 1
                    # trailing stop: leave the ride when it gives back `hold_trail`%
                    if hold_trail and price <= h["peak"] * (1 - hold_trail / 100):
                        proceeds = h["qty"] * price * (1 - FEE)
                        cash += proceeds; r_hold += proceeds - h["cost"]
                        n_hold += 1; hold_bars.append(i - h["i"])
                        holds[c] = None; locked[c] = True
                elif not locked[c]:
                    amt = min(hold_pct * capital_base, cash)
                    if amt >= unit:
                        qty = (amt - amt * FEE) / price
                        holds[c] = {"qty": qty, "cost": amt, "i": i,
                                    "entry": price, "peak": price, "banked": False}
                        cash -= amt
                continue                      # BULL: grid frozen

            locked[c] = False                 # left BULL — the ride can be re-taken

            if holds[c] is not None:          # left BULL -> liquidate the ride
                h = holds[c]
                proceeds = h["qty"] * price * (1 - FEE)
                cash += proceeds; r_hold += proceeds - h["cost"]
                n_hold += 1; hold_bars.append(i - h["i"]); holds[c] = None

            if not use_grid:
                continue
            keep = []                          # bank every bag at its TP
            touch = highs[c][i] if exec_mode == "high" else price
            for b in bags[c]:
                tp = b["entry"] * (1 + tp_pct / 100)
                if touch >= tp:
                    fill = tp if exec_mode == "high" else price
                    proceeds = b["qty"] * fill * (1 - FEE)
                    cash += proceeds; r_grid += proceeds - b["cost"]; n_grid += 1
                else:
                    keep.append(b)
            bags[c] = keep

            if adaptive and regime == BEAR:
                continue                       # defensive: no new bags
            up = sma100[c][i] is not None and price > sma100[c][i]
            lowest = min((b["entry"] for b in bags[c]), default=None)
            want = lowest is None or price <= lowest * (1 - P.step_pct / 100)
            deployed_grid = sum(b["cost"] for cc in coins for b in bags[cc])
            room = deployed_grid + unit <= max_deploy * capital_base
            if block_bag and block_bag[c][i]:
                continue                       # shadow-flow gate on new bags
            if up and want and cash >= unit and len(bags[c]) < max_bags and room:
                qty = (unit - unit * FEE) / price
                bags[c].append({"entry": price, "qty": qty, "cost": unit, "i": i})
                cash -= unit

        g_val = sum(b["qty"] * closes[c][i] for c in coins for b in bags[c])
        h_val = sum(holds[c]["qty"] * closes[c][i] for c in coins if holds[c])
        equity = cash + g_val + h_val
        peak_eq = max(peak_eq, equity)
        mdd = max(mdd, (peak_eq - equity) / peak_eq)
        dep_grid += sum(b["cost"] for c in coins for b in bags[c])
        dep_hold += sum(holds[c]["cost"] for c in coins if holds[c])
        frozen += sum(b["cost"] for c in coins for b in bags[c]
                      if closes[c][i] < b["entry"])
        peak_bags = max(peak_bags, sum(len(bags[c]) for c in coins))
        # how much of the book is actually AT WORK this bar, and how many coins
        # are in a BULL ride — the two numbers that explain the idle cash
        if equity > 0:
            dep_hist.append((g_val + h_val) / equity)
        bull_now.append(sum(1 for c in coins if holds[c]))
        bars += 1

    last = {c: closes[c][n - 1] for c in coins}
    u_grid = sum(b["qty"] * last[c] - b["cost"] for c in coins for b in bags[c])
    u_hold = sum(holds[c]["qty"] * last[c] - holds[c]["cost"] for c in coins if holds[c])
    equity = cash + sum(b["qty"] * last[c] for c in coins for b in bags[c]) \
        + sum(holds[c]["qty"] * last[c] for c in coins if holds[c])
    years = bars * 4 / 24 / 365
    return dict(equity=equity, ret=(equity / start - 1) * 100, mdd=mdd * 100,
                r_grid=r_grid, r_hold=r_hold, u_grid=u_grid, u_hold=u_hold,
                pnl_grid=r_grid + u_grid, pnl_hold=r_hold + u_hold,
                dep_grid=dep_grid / bars, dep_hold=dep_hold / bars,
                cash_idle=(start - dep_grid / bars - dep_hold / bars),
                frozen=frozen / bars / start * 100, n_grid=n_grid, n_hold=n_hold,
                hold_days=(sum(hold_bars) / len(hold_bars) / 6) if hold_bars else 0,
                open_bags=sum(len(bags[c]) for c in coins), peak_bags=peak_bags,
                years=years, dep_hist=dep_hist, bull_now=bull_now)


# ── attribution report ────────────────────────────────────────────────────

def main(argv):
    data = load()
    times = [c.open_time for c in data["BTCUSDT"]]


    def bar_of(day):
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return next(i for i, t in enumerate(times) if t >= dt)


    def bh(lo, hi):
        """Equal-weight buy & hold of the basket over the same bars."""
        per = TOTAL / len(BASKET)
        end = sum(per * (data[c][hi - 1].close / data[c][lo].close) for c in BASKET)
        return (end / TOTAL - 1) * 100


    VARIANTS = [
        ("FULL (live: grid + hold 15%)", dict()),
        ("grid only (adaptive)",         dict(use_hold=False)),
        ("grid only (plain, no regime)", dict(use_hold=False, adaptive=False)),
        ("hold only 15%",                dict(use_grid=False)),
        ("hold only 25%",                dict(use_grid=False, hold_pct=0.25)),
        ("FULL, hold 25%",               dict(hold_pct=0.25)),
        ("FULL, hold 35%",               dict(hold_pct=0.35)),
        ("FULL, hold 25% + max 15 bags", dict(hold_pct=0.25, max_bags=15)),
    ]

    SPANS = [("full 2.57y", WARM, len(times)),
             ("H1 → 2025-02", WARM, bar_of("2025-02-01")),
             ("H2 2025-02 →", bar_of("2025-02-01"), len(times))]

    for label, lo, hi in SPANS:
        print(f"\n=== {label} | book €{TOTAL:.0f} | buy&hold basket = {bh(lo, hi):+.1f}% ===")
        print(f"  {'variant':<30} {'equity%':>8} {'mdd%':>6} | {'grid P&L':>9} "
              f"{'hold P&L':>9} | {'dep.grid':>8} {'dep.hold':>8} {'idle':>7} | {'holds':>5}")
        print("  " + "-" * 103)
        for name, kw in VARIANTS:
            r = simulate(data, lo=lo, hi=hi, **kw)
            print(f"  {name:<30} {r['ret']:>+7.1f}% {r['mdd']:>5.1f}% | "
                  f"{r['pnl_grid']:>+9.0f} {r['pnl_hold']:>+9.0f} | "
                  f"{r['dep_grid']:>8.0f} {r['dep_hold']:>8.0f} {r['cash_idle']:>7.0f} | "
                  f"{r['n_hold']:>5}")

    r = simulate(data)
    print(f"\n=== return ON DEPLOYED capital (full {r['years']:.2f}y) ===")
    for sleeve in ("grid", "hold"):
        pnl, dep = r[f"pnl_{sleeve}"], r[f"dep_{sleeve}"]
        ann = ((1 + pnl / dep) ** (1 / r["years"]) - 1) * 100 if dep else 0
        print(f"  {sleeve:>5}: P&L {pnl:>+8.0f} on avg deployed ${dep:>6.0f} "
              f"→ {pnl / dep * 100:>+6.1f}% total, {ann:>+6.1f}%/yr")
    print(f"  idle cash: ${r['cash_idle']:.0f} ({r['cash_idle'] / TOTAL * 100:.0f}% of the book never deployed)")
    print(f"  hold trades {r['n_hold']} · avg {r['hold_days']:.1f} days each · "
          f"grid trades {r['n_grid']} · peak bags {r['peak_bags']} · frozen {r['frozen']:.1f}%")


if __name__ == "__main__":
    main(sys.argv[1:])
