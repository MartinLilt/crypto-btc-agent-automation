"""Grid-stream runner — one cycle over the blue-chip basket.

For each admitted coin (TARGET_COINS): drip the stream (sell bags that hit their
micro take-profit) and, only while the coin is in an uptrend, add a bag on a
deeper dip. Bags that go underwater are frozen and held for the bull-cycle feast.

You switch only TRADING_MODE (paper|live). Run:
    source .venv/bin/activate
    python main.py
"""

from __future__ import annotations

import time
import traceback

from src import net
from src.binance_api import get_candles
from src.config import config
from src.ratelimit import BinanceBanned, preflight, wait_out, weight_report
from dataclasses import replace

from src.grid import effective_unit, is_uptrend, params_from_config, plan_actions
from src.grid_broker import get_grid_broker
from src import notify
from src.subscribers import get_subscribers
from src.regime import Regime, detect_regime, resample, tf_factor
from src.universe import get_universe

_REGIME_ICON = {Regime.BULL: "🟢BULL", Regime.NEUTRAL: "⚪NEUTRAL", Regime.BEAR: "🔴BEAR"}

# The cron fires once every 4h and Railway never restarts a failed run, so a
# single blipped HTTP call used to cost a whole cycle — no take-profits, no
# report. Reads are retried; ORDERS ARE NOT (a resend could double-trade when
# the first request actually reached Binance and only the reply was lost).
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 5
# How many times one step may sit out an IP ban. One is enough for a real ban
# (we sleep until its stated expiry); a second means it was re-armed while we
# waited, and then hanging around only invites a longer one.
_BAN_WAITS = 1


def _retry(fn, what: str, attempts: int = _RETRY_ATTEMPTS, delay: float = _RETRY_DELAY):
    """Run a READ-ONLY step, retrying transient failures with a growing pause.

    An IP ban (418/429) is handled apart from a blip: retrying into one is what
    escalates it — Binance counts every request sent while banned — so we sleep
    until its stated expiry instead, and give up at once when it outlasts the
    BAN_MAX_WAIT_MIN cap. Sitting out a ban does not spend a blip attempt.

    Never wrap anything that places an order in this — see the note above.
    """
    attempt, ban_waits = 0, 0
    while True:
        attempt += 1
        try:
            return fn()
        except BinanceBanned as banned:
            print(f"  ⛔ {what}: {banned.describe()}")
            if ban_waits >= _BAN_WAITS or not wait_out(banned, config.ban_max_wait_min * 60):
                raise
            ban_waits += 1
            attempt -= 1          # waiting out a ban is not a failed attempt
        except Exception as exc:
            if attempt >= attempts:
                raise
            print(f"  ⚠ {what}: {type(exc).__name__}: {str(exc)[:70]} — "
                  f"retry {attempt}/{attempts - 1} in {delay:.0f}s")
            time.sleep(delay)
            delay *= 2


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


_MARKET_WORD = {Regime.BULL: "растёт 📈", Regime.NEUTRAL: "спокойный, боковик 😴",
                Regime.BEAR: "падает 📉"}
_TREND_LABEL = {Regime.BULL: "📈 Растут", Regime.NEUTRAL: "➡️ Боковик", Regime.BEAR: "📉 Падают"}


def _trend_summary(regimes: dict) -> list[str]:
    """Per-coin market tendency spelled out in plain words — coins grouped by state,
    no per-coin circles and no legend to decode. Only non-empty groups are shown;
    when every coin sits in one state we collapse it to a single 'рынок …' line."""
    buckets = {Regime.BULL: [], Regime.NEUTRAL: [], Regime.BEAR: []}
    for sym, reg in regimes.items():
        buckets[reg].append(sym.replace(config.quote_asset, ""))
    filled = [(reg, coins) for reg, coins in buckets.items() if coins]
    if len(filled) == 1:
        return [f"🧭 Рынок: {_MARKET_WORD[filled[0][0]]}"]
    return [f"{_TREND_LABEL[reg]}: {', '.join(coins)}" for reg, coins in filled]


def _sells_message(sells: list[dict], quote: str) -> str | None:
    """Second Telegram message — the last N closed sells as a plain win/loss ledger.

    One row per sell: date, coin, profit in USDC, and % return, with a ✅/❌ mark
    so a non-trader can see at a glance whether each trade landed in the green.
    The mark sits LAST on each row (emoji are double-width and would otherwise
    shift the monospace columns)."""
    if not sells:
        return None
    wins = sum(1 for s in sells if (s.get("pnl") or 0.0) >= 0)
    losses = len(sells) - wins
    total = sum((s.get("pnl") or 0.0) for s in sells)

    body: list[tuple[str, str, str, str, str]] = []
    for s in sells:
        pnl = s.get("pnl") or 0.0
        cost = (s.get("usdt") or 0.0) - pnl          # proceeds − pnl = original cost
        pct = (pnl / cost * 100) if cost > 0 else 0.0
        ts = s.get("ts")
        date = ts.strftime("%d.%m") if hasattr(ts, "strftime") else "—"
        coin = s["symbol"].replace(quote, "")
        body.append((date, coin, f"{pnl:+.2f}", f"{pct:+.1f}%",
                     "✅" if pnl >= 0 else "❌"))

    header = ("Дата", "Монета", "Прибыль", "%")
    align = ("<", "<", ">", ">")
    widths = [max(len(str(c)) for c in col)
              for col in zip(header, *[r[:4] for r in body])]
    def _row4(r: tuple) -> str:
        return "  ".join(f"{str(v):{a}{w}}" for v, a, w in zip(r, align, widths))
    table = "\n".join([_row4(header)] + [f"{_row4(r[:4])}  {r[4]}" for r in body])

    return (f"<b>📜 История продаж (последние {len(sells)})</b>\n"
            f"🟢 в плюс: {wins} · 🔴 в минус: {losses} · "
            f"итог: <b>{total:+.2f}</b> {quote}\n"
            f"<pre>{table}</pre>")


def run_account(account) -> tuple[str | None, str | None]:
    """One trading cycle for ONE account. Trades its Binance account, prints the
    console report, and RETURNS (main_message, history_message) for Telegram —
    the caller fans them out to all subscribers. Returns (None, None) on no data."""
    p = params_from_config()
    universe = get_universe()
    who = f"{account.name} (@{account.tg_username})" if account.tg_username else account.name
    print(f"=== Grid-stream | {who} | mode={config.trading_mode.upper()} | {config.interval} "
          f"| TP {p.tp_pct}% · step {p.step_pct}% · unit {p.unit_usdt} · "
          f"max {p.max_bags} bags · SMA{p.sma_win} ===")
    if config.trading_mode == "live":
        print("⚠️  LIVE — placing REAL Binance orders with real funds.\n")
    else:
        print()

    # reads the store AND the live Binance balance — the exact call whose
    # network blip used to take down the whole cycle for every account
    broker = _retry(lambda: get_grid_broker(account), f"broker init [{account.name}]")
    print(f"storage           : {broker.backend} [slot {account.slot}]")

    if config.trading_mode == "live":
        # One weight-20 exchangeInfo for the whole basket instead of one per
        # coin — takes the cycle from ~110 request weight down to ~30.
        try:
            from src import binance_trade
            binance_trade.preload_filters(universe)
        except BinanceBanned:
            raise
        except Exception as exc:
            print(f"  ⚠ filter preload: {type(exc).__name__}: {str(exc)[:70]} "
                  f"— falling back to per-coin lookups")

    # Bag size for this cycle. When GRID_UNIT_PCT>0 it tracks the live balance,
    # so a deposit auto-scales the bags (with a min-notional floor).
    unit = effective_unit(broker.capital_base)
    p = replace(p, unit_usdt=unit)
    if config.grid_unit_pct > 0:
        print(f"bag sizing        : {config.grid_unit_pct * 100:.2f}% of "
              f"{broker.capital_base:.2f} → unit {unit:.2f} "
              f"(floor {config.grid_min_unit:.2f})")

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
            candles = _retry(lambda: get_candles(symbol=coin, interval=config.interval,
                                                 limit=limit), f"candles {coin}", delay=3)
        except Exception as exc:
            # a skipped coin means no take-profit for it this cycle, so it is
            # an error worth reporting — not a silent console line
            print(f"{coin:>9}  (skipped: {str(exc)[:40]})")
            errors.append(f"⏭ {coin.replace(config.quote_asset, '')}: нет данных, "
                          f"пропущен ({type(exc).__name__})")
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

        # Per-coin bag floor. LOT_SIZE truncation eats up to a step on the buy
        # AND on the sell, so a bag sized just above NOTIONAL becomes unsellable
        # (BTC, 2026-08-22: $6 bag -> bought $5.40 -> sell $4.71 -> -1013).
        pc = p
        floor = broker.min_unit(coin, price)
        if floor > p.unit_usdt:
            pc = replace(p, unit_usdt=floor)
            print(f"{coin:>9}  unit raised {p.unit_usdt:.2f} → {floor:.2f} "
                  f"(exchange filters)")

        try:
            if config.regime_adaptive and regime is Regime.BULL:
                # BULL: ride a buy-&-hold allocation instead of gridding.
                if not broker.has_hold(coin):
                    amt = min(config.bull_hold_pct * broker.capital_base, broker.cash)
                    if amt >= pc.unit_usdt:
                        broker.buy_hold(coin, price, amt)
                        note = " HOLD-BUY"
                        actions.append(f"🟢 {c}: BULL — bought hold ${amt:.0f} to ride")
                else:
                    note = " HOLDING"
                    # Partial take-profit: once the ride is deep in profit, bank
                    # part of it so a sharp retrace can't round-trip the whole
                    # position. Fires once per hold (`banked`), and only when
                    # BOTH halves still clear the exchange's NOTIONAL floor.
                    h = broker.holds[coin]
                    if (config.hold_tp_pct > 0 and not h.get("banked")
                            and price >= h["entry"] * (1 + config.hold_tp_pct / 100)
                            and broker.hold_splittable(coin, price, config.hold_tp_frac)):
                        pnl = broker.sell_hold_part(coin, price, config.hold_tp_frac)
                        note = f" HOLD-TP {pnl:+.2f}"
                        actions.append(
                            f"💵 {c}: +{config.hold_tp_pct:.0f}% — забрал "
                            f"{config.hold_tp_frac * 100:.0f}% холда, pnl {pnl:+.2f}")
            else:
                # left BULL → liquidate the ride (take the bull gains)
                if broker.has_hold(coin):
                    pnl = broker.sell_hold(coin, price)
                    note = f" HOLD-SELL {pnl:+.0f}"
                    actions.append(f"🏁 {c}: exited BULL hold, pnl {pnl:+.0f}")
                # NEUTRAL / BEAR → adaptive grid
                sells, do_buy = plan_actions(regime, broker.bags(coin), price, broker.cash,
                                             up, pc, config.regime_adaptive)
                sold = 0
                for i in sorted(sells, reverse=True):
                    broker.sell_bag(coin, i, price); sold += 1
                if do_buy:
                    broker.buy(coin, price, pc.unit_usdt); note += " BUY"
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

        # Persist per coin: the ledger is only in memory until this call, so a
        # crash mid-loop used to leave orders that Binance executed and our
        # state never recorded (a phantom bag). Now each coin is committed as
        # soon as it is done.
        broker.save()

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
    # deposit/withdrawal since last cycle (live) — ignore fee/dust noise
    flow = broker.external_flow
    flow_line = None
    if abs(flow) > max(1.0, 0.005 * start):
        flow_line = (f"💰 Пополнение: +{flow:.2f} {config.quote_asset}" if flow > 0
                     else f"🏧 Вывод: {flow:.2f} {config.quote_asset}")
    print("\n" + "-" * 60)
    if flow_line:
        print(flow_line)
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
    # Binance's own instruction is to watch this header. Our whole cycle costs
    # ~30, so a high reading is a neighbour on the shared egress IP — the early
    # warning we did not have before the 2026-08-23/24 bans.
    pressure = weight_report()
    if pressure:
        print(pressure)
    egress = net.report()
    if egress:
        print(egress)

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

    # ── build Telegram messages (caller sends them to all subscribers) ────
    equity_pct = (equity / start - 1) * 100
    feast_pct = (feast / start - 1) * 100
    invested = equity - broker.cash          # value sitting inside open positions
    # plain-language verdict on the banked profit — the bottom-line number
    verdict = ("✅ в плюсе" if broker.realized > 0
               else "❌ пока в минусе" if broker.realized < 0 else "· по нулям")
    pos_block = f"<pre>{pos_table}</pre>" if rows else f"<i>{pos_table}</i>"
    header = f"<b>🌊 {account.name}</b>"
    if account.tg_username:
        header += f" · @{account.tg_username}"
    lines = [
        f"{header} · {config.trading_mode.upper()} · {config.interval}",
        "",
        f"💰 Всего на счету: <b>{equity:.0f}</b> {config.quote_asset} ({equity_pct:+.1f}%)",
        f"     свободно {broker.cash:.0f} · в закупках {invested:.0f}",
        f"💵 Заработано всего: <b>{broker.realized:+.2f}</b> {config.quote_asset} — {verdict}",
        f"🎯 Если все закупки отработают: {feast:.0f} ({feast_pct:+.1f}%)",
        *([f"<b>{flow_line}</b>"] if flow_line else []),
        "",
        f"🛒 Открытых закупок: {broker.total_bags} · 💼 Холдов: {n_holds}",
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
    history = _sells_message(broker.store.recent_sells(50), config.quote_asset)
    return "\n".join(lines), history


def _process_subscriptions() -> None:
    """Poll Telegram once for /start messages; register whitelisted users as
    subscribers, refuse everyone else. Best-effort — never blocks a cycle."""
    if not config.telegram_bot_token or not config.telegram_whitelist:
        return
    subs = get_subscribers()
    offset = subs.offset()
    new_offset = offset
    for u in notify.get_updates(offset):
        new_offset = max(new_offset, u.get("update_id", 0) + 1)
        msg = u.get("message")
        if not msg:
            continue
        chat_id = (msg.get("chat") or {}).get("id")
        uname = ((msg.get("from") or {}).get("username") or "").lower()
        if not chat_id:
            continue
        if uname in config.telegram_whitelist:
            subs.add(chat_id, uname)
            notify.send("✅ Доступ подтверждён — отчёты Grid-stream будут приходить "
                        "сюда каждые 4 часа.", chat_id)
        else:
            notify.send("⛔ Доступ только для участников бота из белого списка.", chat_id)
    if new_offset != offset:
        subs.set_offset(new_offset)


def _recipients() -> list:
    """All chat_ids to push reports to: registered subscribers + the bootstrap
    (owner) chat, de-duplicated."""
    ids = {str(cid) for cid, _ in get_subscribers().all()}
    if config.telegram_chat_id:
        ids.add(str(config.telegram_chat_id))
    return sorted(ids)


def _ban_message(banned: BinanceBanned) -> str:
    """What the owner sees when Binance has shut the whole IP out.

    Which diagnosis is true depends on WHOSE address the cycle went out on, and
    saying the wrong one sends the reader hunting in the wrong place. On the
    shared Railway IP a ban is a neighbour's doing and there is nothing to fix
    in our code. On our own dedicated egress that explanation is gone — one
    cycle spends ~38 weight per 4h against a 6000/min budget, so a ban there
    means either the traffic is not taking the proxy or the box is not ours
    alone, and both are worth waking up for."""
    head = ("<b>⛔ Binance: IP забанен</b>\n\n"
            f"<code>{banned.describe()}</code>\n\n"
            "Цикл пропущен, состояние не тронуто — ни одна сделка не потеряна.\n")
    if net.on_own_egress():
        return head + ("‼️ И это <b>наш собственный</b> адрес, а не общий с соседями. "
                       "Мы тратим ~38 единиц веса раз в 4 часа при лимите 6000 в "
                       "минуту — забанить нас там нечем. Значит либо трафик идёт "
                       "мимо прокси, либо IP коробки не выделенный. Надо смотреть.")
    return head + ("Это не наш трафик: мы тратим ~38 единиц веса раз в 4 часа при "
                   "лимите 6000 в минуту. Статический IP на Railway общий с другими "
                   "клиентами — банят соседа, прилетает нам. Лечится своим egress "
                   "(BINANCE_PROXY_URL), см. reference/Binance API policy.md.")


def _failure_message(account, exc: Exception) -> str:
    """What the owner sees when an account's cycle could not run at all."""
    return (f"<b>‼️ {account.name}</b> · цикл не отработал\n\n"
            f"<code>{type(exc).__name__}: {str(exc)[:250]}</code>\n\n"
            f"Состояние не изменено — ни одна сделка не потеряна. "
            f"Следующая попытка через 4 часа.")


def main() -> None:
    # Whitelist enrollment first, so a just-approved user gets this cycle's
    # report. Best-effort: the DB or Telegram being down must not cost a cycle
    # of TRADING, which is the part that actually moves money.
    try:
        _process_subscriptions()
    except Exception as exc:
        print(f"⚠ subscription poll failed: {type(exc).__name__}: {str(exc)[:80]}")

    live = config.trading_mode == "live"
    to_run = [a for a in config.accounts if (a.active if live else True)]
    skipped = [a for a in config.accounts if live and not a.active]
    for a in skipped:
        print(f"— skipping {a.name} (@{a.tg_username}): no API keys yet —")

    try:
        targets = _recipients()
    except Exception as exc:
        print(f"⚠ recipient lookup failed: {type(exc).__name__}: {str(exc)[:80]}")
        targets = [config.telegram_chat_id] if config.telegram_chat_id else []

    # Weight-1 gate before anything expensive: if this IP is banned, every
    # further request only feeds the escalation counter.
    banned = preflight(config.ban_max_wait_min * 60)
    if banned is not None:
        print(f"‼️ cycle skipped — {banned.describe()}")
        for chat in targets:
            notify.send(_ban_message(banned), chat)
        return

    failed = 0
    for account in to_run:
        # One account's blown cycle must not take the others down with it: each
        # trades its OWN Binance keys and its own state slot, so they are
        # independent by construction — the code just never honoured that.
        try:
            main_msg, history = run_account(account)
        except Exception as exc:
            failed += 1
            print(f"‼️ {account.name}: cycle FAILED — {type(exc).__name__}: {exc}")
            traceback.print_exc()
            main_msg, history = _failure_message(account, exc), None
        for chat in targets:
            if main_msg:
                notify.send(main_msg, chat)
            if history:
                notify.send(history, chat)
        print()  # blank line between accounts in the console log

    if failed:
        # Exit 0 on purpose. A non-zero exit marks the deployment CRASHED, and a
        # crashed deployment stops firing its cron: on 2026-08-23 the 08:00 ban
        # crashed the run and the 12:00 cycle never started — the bot only came
        # back on a manual redeploy. The failure is loud in the log and in
        # Telegram; keeping the schedule alive matters more than a red badge.
        print(f"‼️ {failed}/{len(to_run)} account cycle(s) failed — "
              f"schedule kept alive, next run in 4h")


if __name__ == "__main__":
    main()