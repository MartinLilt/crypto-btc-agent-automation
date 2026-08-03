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
        print("!! live grid execution not wired yet — running the paper broker.\n")
    else:
        print()

    broker = get_grid_broker()
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

        # regime-adaptive plan: what to sell, whether to buy
        sells, do_buy = plan_actions(regime, broker.bags(coin), price, broker.cash,
                                     up, p, config.regime_adaptive)
        sold = 0
        for i in sorted(sells, reverse=True):
            broker.sell_bag(coin, i, price)
            sold += 1
        added = False
        if do_buy:
            broker.buy(coin, price, p.unit_usdt)
            added = True

        n = len(broker.bags(coin))
        note = (" SOLD %d" % sold if sold else "") + (" BUY" if added else "")
        print(f"{coin:>9}  {price:>12.4f}  {_REGIME_ICON[regime]:>9}  bags {n:>2}  "
              f"value {broker.coin_value(coin, price):>8.2f}{note}")
        c = coin.replace("USDT", "")
        if sold:
            actions.append(f"💰 {c}: sold {sold} bag(s) @ {price:g}")
        if added:
            actions.append(f"🛒 {c}: bought a bag @ {price:g}")
        if regime is Regime.BEAR and broker.bags(coin):
            actions.append(f"🔴 {c}: BEAR — defending, no new bags ({n} frozen)")

    broker.save()

    # ── portfolio summary (mark-to-market) ───────────────────────────────
    equity = broker.equity(prices)
    start = config.paper_start_balance
    feast = broker.feast_value(p.tp_pct)
    n_coins = sum(1 for s in broker.positions if broker.bags(s))
    print("\n" + "-" * 60)
    print(f"cash              : {broker.cash:>10.2f} USDT")
    print(f"open bags         : {broker.total_bags} across {n_coins} coins")
    print(f"realized stream   : {broker.realized:>+10.2f} USDT")
    print(f"equity (MTM)      : {equity:>10.2f} USDT  ({(equity/start-1)*100:+.2f}%)")
    print(f"feast (bags recover): {feast:>8.2f} USDT  ({(feast/start-1)*100:+.2f}%)")

    reg_line = " · ".join(
        f"{c.replace('USDT','')}:{regimes[c].value[0]}" for c in regimes
    )
    print(f"regime            : {reg_line}")

    # ── Telegram results output ──────────────────────────────────────────
    if notify.enabled():
        lines = [
            f"<b>🌊 Grid-stream</b> · {config.trading_mode.upper()} · {config.interval}",
            "",
            f"💵 Ручеёк (realized): <b>{broker.realized:+.2f}</b> USDT",
            f"📊 Equity (MTM): <b>{equity:.2f}</b> ({(equity/start-1)*100:+.2f}%)",
            f"🎉 Пир (recover): {feast:.2f} ({(feast/start-1)*100:+.2f}%)",
            f"🧺 Мешков: {broker.total_bags} на {n_coins} монетах · кэш {broker.cash:.0f}",
            f"🧭 Режим: {reg_line}",
        ]
        if actions:
            lines += ["", "<b>Действия:</b>", *actions]
        notify.send("\n".join(lines))


if __name__ == "__main__":
    main()