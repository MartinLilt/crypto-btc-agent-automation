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


def _fmt_price(x: float) -> str:
    """Compact, readable price across magnitudes (BTC 64k … XRP 1.04)."""
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:.2f}"
    return f"{x:.4f}"


# Positions rendered as a monospace, column-aligned table — no emoji INSIDE it
# (emoji are double-width and break alignment in Telegram's <pre> block).
_POS_HEADER = ("Монета", "Поз.", "Влож", "PnL%", "Ждёт")
_POS_ALIGN = ("<", "<", ">", ">", "<")


def _pos_table(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Aligned plain-text table (wrap in <pre> for Telegram, print raw to logs)."""
    if not rows:
        return "— открытых позиций нет —"
    widths = [max(len(str(c)) for c in col) for col in zip(_POS_HEADER, *rows)]
    def _row(r: tuple) -> str:
        return "  ".join(f"{str(v):{a}{w}}" for v, a, w in zip(r, _POS_ALIGN, widths)).rstrip()
    return "\n".join([_row(_POS_HEADER)] + [_row(r) for r in rows])


def _tax_line(realized: float, quote: str) -> str:
    """Dynamic Lithuania GPM estimate on YTD realised gains (recomputed each cycle
    from the current realised total, after this iteration's trades). Lithuania taxes
    gains from financial instruments at TAX_RATE (15%) above a TAX_ALLOWANCE
    (€500/yr) exemption. Not deducted from equity — paid yearly from fiat."""
    rate_pct = config.tax_rate * 100
    allow = config.tax_allowance
    est = max(0.0, realized - allow) * config.tax_rate
    if realized <= allow:
        return (f"🏛 Налог (Литва): 0.00 {quote} · прибыль {realized:+.2f} "
                f"в пределах льготы {allow:.0f}€/год")
    return (f"🏛 Налог (Литва, GPM {rate_pct:.0f}%): ~{est:.2f} {quote} · "
            f"с прибыли {realized:.2f} за год свыше {allow:.0f}€")


_TREND_WORD = {Regime.BULL: "растут", Regime.NEUTRAL: "в боковике", Regime.BEAR: "падают"}
_TREND_LABEL = {Regime.BULL: "📈 Растут", Regime.NEUTRAL: "➡️ Боковик", Regime.BEAR: "📉 Падают"}


def _trend_summary(regimes: dict) -> list[str]:
    """Per-coin market tendency spelled out in plain words — coins grouped by state,
    no per-coin circles and no legend to decode. Only non-empty groups are shown;
    when every coin sits in one state we collapse it to a single 'все …' line."""
    buckets = {Regime.BULL: [], Regime.NEUTRAL: [], Regime.BEAR: []}
    for sym, reg in regimes.items():
        buckets[reg].append(sym.replace(config.quote_asset, ""))
    filled = [(reg, coins) for reg, coins in buckets.items() if coins]
    if len(filled) == 1:
        return [f"🧭 Тренд: все {_TREND_WORD[filled[0][0]]}"]
    return [f"{_TREND_LABEL[reg]}: {', '.join(coins)}" for reg, coins in filled]


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
    errors: list[str] = []          # order failures / reconcile drift (own section)
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
            # Full error to stdout (Railway logs) — Telegram only gets a truncated
            # copy, so the exact Binance code/msg was invisible in the cloud logs.
            print(f"  ⚠ {coin} ORDER FAILED — {type(exc).__name__}: {exc}")
            errors.append(f"❌ {c}: {str(exc)[:90]}")

        n = len(broker.bags(coin))
        hv = broker.hold_value(coin, price)
        print(f"{coin:>9}  {price:>12.4f}  {_REGIME_ICON[regime]:>9}  bags {n:>2}  "
              f"hold {hv:>7.2f}  value {broker.coin_value(coin, price):>8.2f}{note}")

    broker.save()

    # live safety: warn if the exchange balances drift from our ledger
    if hasattr(broker, "reconcile"):
        for w in broker.reconcile(prices):
            errors.append(f"🔎 reconcile: {w}")

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
    rows: list[tuple[str, str, str, str, str]] = []
    for coin in universe:
        price = prices.get(coin)
        if price is None:
            continue
        cn = coin.replace(config.quote_asset, "")
        hold = broker.holds.get(coin)
        bags = broker.bags(coin)
        if hold:
            pnlp = (hold["qty"] * price - hold["cost"]) / hold["cost"] * 100
            rows.append((cn, "hold", f"${hold['cost']:.0f}", f"{pnlp:+.1f}%", "конца BULL"))
        if bags:
            qty = sum(b["qty"] for b in bags)
            invested = sum(b["cost"] for b in bags)
            pnlp = (qty * price - invested) / invested * 100
            tp_lo = min(b["entry"] for b in bags) * (1 + p.tp_pct / 100)
            rows.append((cn, f"меш×{len(bags)}", f"${invested:.0f}",
                         f"{pnlp:+.1f}%", f"≥{_fmt_price(tp_lo)}"))
    pos_table = _pos_table(rows)
    print("positions:\n" + pos_table)

    # ── Telegram results output ──────────────────────────────────────────
    if notify.enabled():
        equity_pct = (equity / start - 1) * 100
        feast_pct = (feast / start - 1) * 100
        pos_block = f"<pre>{pos_table}</pre>" if rows else f"<i>{pos_table}</i>"
        lines = [
            f"<b>🌊 Grid-stream</b> · {config.trading_mode.upper()} · {config.interval}",
            "",
            f"📊 Счёт: <b>{equity:.0f}</b> ({equity_pct:+.2f}%) · кэш {broker.cash:.0f}",
            f"💵 Ручеёк: <b>{broker.realized:+.2f}</b> {config.quote_asset}",
            f"🎉 Пир: {feast:.0f} ({feast_pct:+.1f}%)",
            "",
            f"🧺 Мешков: {broker.total_bags} · 💼 Холдов: {n_holds}",
            *_trend_summary(regimes),
            "<b>📦 Позиции (чего ждут):</b>",
            pos_block,
            "",
            _tax_line(broker.realized, config.quote_asset),
            "",
            "<b>⚡️ Действия за цикл:</b>",
            *(actions or ["<i>— без сделок в этом цикле —</i>"]),
        ]
        if errors:
            lines += ["", "<b>⚠️ Ошибки за цикл:</b>", *errors]
        notify.send("\n".join(lines))


if __name__ == "__main__":
    main()