"""Grid-stream runner — one cycle over the blue-chip basket.

For each admitted coin (TARGET_COINS): drip the stream (sell bags that hit their
micro take-profit) and, only while the coin is in an uptrend, add a bag on a
deeper dip. Bags that go underwater are frozen and held for the bull-cycle feast.

You switch only TRADING_MODE (paper|live). Run:
    source .venv/bin/activate
    python main.py
"""

from __future__ import annotations

from src.binance_api import get_candles
from src.config import config
from src.grid import is_uptrend, params_from_config, plan_actions
from src.grid_broker import get_grid_broker
from src import notify
from src.regime import Regime, detect_regime, resample, tf_factor
from src.universe import get_universe

_REGIME_ICON = {Regime.BULL: "🟢BULL", Regime.NEUTRAL: "⚪NEUTRAL", Regime.BEAR: "🔴BEAR"}


def main() -> None:
    p = params_from_config()
    universe = get_universe()
    print(f"=== Grid-stream | mode={config.trading_mode.upper()} | {config.interval} "
          f"| TP {p.tp_pct}% · step {p.step_pct}% · unit {p.unit_usdt} · "
          f"max {p.max_bags} bags · SMA{p.sma_win} ===")
    if config.trading_mode == "live":
        print("⚠️  LIVE — placing REAL Binance orders with real funds.\n")
    else:
        print()

    broker = get_grid_broker()
    print(f"storage           : {broker.backend}")
    prices: dict[str, float] = {}
    # Enough 4h candles for the grid SMA AND the higher-TF regime (resampled).
    factor = tf_factor(config.interval, config.regime_interval)
    regime_bars = (config.regime_ma + config.regime_slope_lookback + 5) * factor
    limit = max(p.sma_win + 60, regime_bars)
    actions: list[str] = []
    regimes: dict[str, Regime] = {}

    for coin in universe:
        try:
            candles = get_candles(symbol=coin, interval=config.interval, limit=limit)
        except Exception as exc:
            print(f"{coin:>9}  (skipped: {str(exc)[:40]})")
            continue
        closes = [c.close for c in candles]
        price = closes[-1]
        prices[coin] = price
        up = is_uptrend(closes, p.sma_win)

        # market regime for this coin, measured on the higher timeframe
        regime = detect_regime(resample(candles, factor)).regime
        regimes[coin] = regime
        c = coin.replace(config.quote_asset, "")
        note = ""

        try:
            if config.regime_adaptive and regime is Regime.BULL:
                # BULL: ride a buy-&-hold allocation instead of gridding.
                if not broker.has_hold(coin):
                    amt = min(config.bull_hold_pct * broker.capital_base, broker.cash)
                    if amt >= p.unit_usdt:
                        broker.buy_hold(coin, price, amt)
                        note = " HOLD-BUY"
                        actions.append(f"🟢 {c}: BULL — bought hold ${amt:.0f} to ride")
                else:
                    note = " HOLDING"
            else:
                # left BULL → liquidate the ride (take the bull gains)
                if broker.has_hold(coin):
                    pnl = broker.sell_hold(coin, price)
                    note = f" HOLD-SELL {pnl:+.0f}"
                    actions.append(f"🏁 {c}: exited BULL hold, pnl {pnl:+.0f}")
                # NEUTRAL / BEAR → adaptive grid
                sells, do_buy = plan_actions(regime, broker.bags(coin), price, broker.cash,
                                             up, p, config.regime_adaptive)
                sold = 0
                for i in sorted(sells, reverse=True):
                    broker.sell_bag(coin, i, price); sold += 1
                if do_buy:
                    broker.buy(coin, price, p.unit_usdt); note += " BUY"
                if sold:
                    note = f" SOLD {sold}" + note
                    actions.append(f"💰 {c}: sold {sold} bag(s) @ {price:g}")
                if "BUY" in note and "HOLD" not in note:
                    actions.append(f"🛒 {c}: bought a bag @ {price:g}")
        except Exception as exc:
            note = " ⚠ORDER-FAILED"
            actions.append(f"⚠️ {c}: order failed — {str(exc)[:70]}")

        n = len(broker.bags(coin))
        hv = broker.hold_value(coin, price)
        print(f"{coin:>9}  {price:>12.4f}  {_REGIME_ICON[regime]:>9}  bags {n:>2}  "
              f"hold {hv:>7.2f}  value {broker.coin_value(coin, price):>8.2f}{note}")

    broker.save()

    # live safety: warn if the exchange balances drift from our ledger
    if hasattr(broker, "reconcile"):
        for w in broker.reconcile(prices):
            actions.append(f"🔎 reconcile: {w}")

    # ── portfolio summary (mark-to-market) ───────────────────────────────
    equity = broker.equity(prices)
    start = broker.capital_base
    feast = broker.feast_value(p.tp_pct, prices)
    n_coins = sum(1 for s in broker.positions if broker.bags(s))
    n_holds = len(broker.holds)
    # Estimated tax LIABILITY on realised gains only (annual, above allowance) —
    # NOT deducted from equity: you pay it yearly from fiat, and open bags aren't taxed.
    est_tax = max(0.0, broker.realized - config.tax_allowance) * config.tax_rate
    print("\n" + "-" * 60)
    print(f"cash              : {broker.cash:>10.2f} {config.quote_asset}")
    print(f"open bags         : {broker.total_bags} across {n_coins} coins")
    print(f"bull holds        : {n_holds} ({', '.join(broker.holds) or '—'})")
    print(f"realized stream   : {broker.realized:>+10.2f} {config.quote_asset}")
    print(f"equity (MTM)      : {equity:>10.2f} {config.quote_asset}  ({(equity/start-1)*100:+.2f}%)")
    print(f"feast (bags recover): {feast:>8.2f} {config.quote_asset}  ({(feast/start-1)*100:+.2f}%)")
    print(f"est. tax liability: {est_tax:>10.2f} {config.quote_asset}  (on realised only, paid yearly)")

    reg_line = " · ".join(
        f"{c.replace(config.quote_asset,''):}:{regimes[c].value[0]}" for c in regimes
    )
    print(f"regime            : {reg_line}")

    # ── per-coin position detail (what each position is waiting for) ──────
    pos_lines: list[str] = []
    for coin in universe:
        price = prices.get(coin)
        if price is None:
            continue
        cn = coin.replace(config.quote_asset, "")
        hold = broker.holds.get(coin)
        bags = broker.bags(coin)
        if hold:
            pnlp = (hold["qty"] * price - hold["cost"]) / hold["cost"] * 100
            pos_lines.append(
                f"🟢 <b>{cn}</b> hold ${hold['cost']:.0f} · вход {hold['entry']:g} · "
                f"сейчас {price:g} ({pnlp:+.1f}%) · едет ↑, ждёт конца BULL")
        if bags:
            qty = sum(b["qty"] for b in bags)
            invested = sum(b["cost"] for b in bags)
            avg = sum(b["entry"] * b["qty"] for b in bags) / qty
            pnlp = (qty * price - invested) / invested * 100
            tp_lo = min(b["entry"] for b in bags) * (1 + p.tp_pct / 100)
            icon = "🔴" if regimes[coin] is Regime.BEAR else "⚪"
            pos_lines.append(
                f"{icon} <b>{cn}</b> {len(bags)} меш · ${invested:.0f} · "
                f"ср.вход {avg:g} · сейчас {price:g} ({pnlp:+.1f}%) · продаст от {tp_lo:g}")
    if not pos_lines:
        pos_lines = ["— открытых позиций нет —"]
    print("positions:\n  " + "\n  ".join(pos_lines))

    # ── Telegram results output ──────────────────────────────────────────
    if notify.enabled():
        lines = [
            f"<b>🌊 Grid-stream</b> · {config.trading_mode.upper()} · {config.interval}",
            f"📊 Счёт: <b>{equity:.0f}</b> ({(equity/start-1)*100:+.2f}%) · кэш {broker.cash:.0f}",
            f"💵 Ручеёк: <b>{broker.realized:+.2f}</b> · 🎉 Пир: {feast:.0f} ({(feast/start-1)*100:+.1f}%)",
            f"🏛 налог (оценка, с realized): {est_tax:.2f} · платится раз в год",
            f"🧺 {broker.total_bags} меш · 🟢 {n_holds} hold · 🗄 {broker.backend}",
            f"🧭 {reg_line}",
            "",
            "<b>📦 Позиции (чего ждут):</b>",
            *pos_lines,
        ]
        if actions:
            lines += ["", "<b>⚡ Действия за цикл:</b>", *actions]
        notify.send("\n".join(lines))


if __name__ == "__main__":
    main()