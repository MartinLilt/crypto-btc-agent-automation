"""Grid-stream broker — many concurrent bags across a basket of coins.

Paper broker: one shared USDT cash pool, a list of bags per coin, persisted to
state/grid_state.json between runs. Equity is always MARK-TO-MARKET (cash +
every bag valued at the current price) so frozen bags show their real value.

  bag = {entry, qty, cost, time}
  buy(symbol, price)      -> open a bag (unit_usdt from cash)
  sell_bag(symbol, i, p)  -> close a bag at its take-profit (adds to the stream)

Live grid execution (many real orders per coin) is not wired yet — paper only;
live stays user-gated as everywhere else.
"""

from __future__ import annotations

import time

from .config import config
from .store import get_store


class BrokerError(RuntimeError):
    """Raised when a live order can't be placed or is left unfilled."""


class GridBroker:
    mode = "paper"

    def __init__(self, account=None) -> None:
        self.account = account
        self.store = get_store(account.slot if account else 1)
        s = self.store.load()
        self.cash: float = s["cash"]
        self.realized: float = s["realized"]
        self.positions: dict[str, list[dict]] = s["positions"]
        self.holds: dict[str, dict] = s["holds"]   # per-coin BULL buy-&-hold
        self.external_flow: float = 0.0             # deposit/withdrawal since last cycle (live)
        # capital reference for hold sizing / % display (real balance in live)
        self.capital_base: float = config.paper_start_balance

    @property
    def backend(self) -> str:
        return self.store.backend

    # ── persistence ──────────────────────────────────────────────────────
    def save(self) -> None:
        self.store.save(self.cash, self.realized, self.positions, self.holds)

    # ── orders ───────────────────────────────────────────────────────────
    def bags(self, symbol: str) -> list[dict]:
        return self.positions.setdefault(symbol, [])

    def buy(self, symbol: str, price: float, unit_usdt: float) -> dict:
        fee = unit_usdt * config.fee_rate
        qty = (unit_usdt - fee) / price
        bag = {"entry": price, "qty": qty, "cost": unit_usdt, "time": time.time()}
        self.bags(symbol).append(bag)
        self.cash -= unit_usdt
        self.store.log_trade(symbol=symbol, side="BUY", kind="bag",
                             price=price, qty=qty, usdt=unit_usdt, pnl=0.0)
        return bag

    def sell_bag(self, symbol: str, index: int, price: float) -> float:
        bag = self.bags(symbol).pop(index)
        gross = bag["qty"] * price
        fee = gross * config.fee_rate
        proceeds = gross - fee
        self.cash += proceeds
        pnl = proceeds - bag["cost"]
        self.realized += pnl
        self.store.log_trade(symbol=symbol, side="SELL", kind="bag",
                             price=price, qty=bag["qty"], usdt=proceeds, pnl=pnl)
        return pnl

    # ── BULL hold-allocation (ride the trend) ────────────────────────────
    def has_hold(self, symbol: str) -> bool:
        return symbol in self.holds

    def buy_hold(self, symbol: str, price: float, amount_usdt: float) -> None:
        fee = amount_usdt * config.fee_rate
        qty = (amount_usdt - fee) / price
        self.holds[symbol] = {"entry": price, "qty": qty, "cost": amount_usdt,
                              "time": time.time(), "banked": False}
        self.cash -= amount_usdt
        self.store.log_trade(symbol=symbol, side="BUY", kind="hold",
                             price=price, qty=qty, usdt=amount_usdt, pnl=0.0)

    def sell_hold(self, symbol: str, price: float) -> float:
        h = self.holds.pop(symbol)
        gross = h["qty"] * price
        proceeds = gross - gross * config.fee_rate
        self.cash += proceeds
        pnl = proceeds - h["cost"]
        self.realized += pnl
        self.store.log_trade(symbol=symbol, side="SELL", kind="hold",
                             price=price, qty=h["qty"], usdt=proceeds, pnl=pnl)
        return pnl

    def hold_splittable(self, symbol: str, price: float, frac: float) -> bool:
        """Whether a hold can be cut in two. Paper has no exchange filters."""
        return symbol in self.holds

    def sell_hold_part(self, symbol: str, price: float, frac: float) -> float:
        """Bank `frac` of a BULL hold and leave the rest riding.

        A trend that is already deep in profit has given us most of what it will
        give; taking part of it off converts paper gains into cash so a sharp
        retrace can't round-trip the whole position, while the remainder keeps
        the upside. Fires ONCE per hold (the `banked` flag), so it never turns
        into a churn machine.
        """
        h = self.holds[symbol]
        qty, cost = h["qty"] * frac, h["cost"] * frac
        gross = qty * price
        proceeds = gross - gross * config.fee_rate
        self.cash += proceeds
        pnl = proceeds - cost
        self.realized += pnl
        h["qty"] -= qty
        h["cost"] -= cost
        h["banked"] = True
        self.store.log_trade(symbol=symbol, side="SELL", kind="hold-part",
                             price=price, qty=qty, usdt=proceeds, pnl=pnl)
        return pnl

    def hold_value(self, symbol: str, price: float) -> float:
        h = self.holds.get(symbol)
        return h["qty"] * price if h else 0.0

    # ── valuation ────────────────────────────────────────────────────────
    def coin_value(self, symbol: str, price: float) -> float:
        return sum(b["qty"] * price for b in self.bags(symbol)) + self.hold_value(symbol, price)

    def equity(self, prices: dict[str, float]) -> float:
        """Mark-to-market: cash + every bag AND hold at the current price."""
        symbols = set(self.positions) | set(self.holds)
        held = sum(self.coin_value(s, prices.get(s, 0.0)) for s in symbols)
        return self.cash + held

    def feast_value(self, tp_pct: float, prices: dict[str, float] | None = None) -> float:
        """Equity if every frozen bag sells at its TP; holds valued at market."""
        prices = prices or {}
        pending = 0.0
        for bags in self.positions.values():
            for b in bags:
                pending += b["qty"] * b["entry"] * (1 + tp_pct / 100) * (1 - config.fee_rate)
        holds = sum(self.hold_value(s, prices.get(s, 0.0)) for s in self.holds)
        return self.cash + pending + holds

    def min_unit(self, symbol: str, price: float) -> float:
        """Exchange-imposed floor on the bag size. Paper has no filters → 0."""
        return 0.0

    @property
    def total_bags(self) -> int:
        return sum(len(b) for b in self.positions.values())


class LiveGridBroker(GridBroker):
    """Same logical ledger as GridBroker, but orders hit REAL Binance spot.

    The store (Postgres/JSON) holds the logical bags/holds — the exchange only
    knows total coin balances. Cash = real free USDT (source of truth). Bag qty
    & cost come from actual fills. Market orders only. Errors are raised so the
    runner can log & skip; a failed order never leaves a phantom bag.
    """

    mode = "live"

    def __init__(self, account=None) -> None:
        if config.trading_mode != "live":
            raise BrokerError("LiveGridBroker requires TRADING_MODE=live")
        from . import binance_trade as bt
        if account is not None:            # point signed calls at THIS account's keys
            bt.set_credentials(account.api_key, account.api_secret)
        super().__init__(account)          # logical bags/holds/realized from store
        stored_cash = self.cash            # cash we persisted at the end of last cycle
        self._bt = bt
        self.cash = bt.free_quote()        # real quote (USDC) balance is the truth
        # External flow since last save = deposits/withdrawals (nothing but our own
        # orders touches the balance between cycles, and those were baked into
        # stored_cash). Small noise (fees/dust) is ignored by the report threshold.
        self.external_flow = self.cash - stored_cash
        # capital base = free cash + invested cost of all open bags/holds, so the
        # %-return baseline reflects the WHOLE book, not just the un-deployed cash.
        invested = sum(b["cost"] for bags in self.positions.values() for b in bags) \
            + sum(h["cost"] for h in self.holds.values())
        self.capital_base = self.cash + invested

    @property
    def backend(self) -> str:
        return f"{self.store.backend}+LIVE"

    def buy(self, symbol: str, price: float, unit_usdt: float) -> dict:
        resp = self._bt.market_buy(symbol, unit_usdt)
        qty, quote, avg = self._bt.fill_amounts(resp, price)
        if qty <= 0 or quote <= 0:      # unfilled order → never record a phantom bag
            raise BrokerError(f"{symbol}: buy returned no fill (qty={qty}, quote={quote})")
        bag = {"entry": avg, "qty": qty, "cost": quote, "time": time.time()}
        self.bags(symbol).append(bag)
        self.cash -= quote
        self.store.log_trade(symbol=symbol, side="BUY", kind="bag",
                             price=avg, qty=qty, usdt=quote, pnl=0.0)
        return bag

    def min_unit(self, symbol: str, price: float) -> float:
        """Bag floor from this symbol's real filters — below it a bought bag
        cannot be sold again (see binance_trade.min_round_trip_unit)."""
        try:
            return self._bt.min_round_trip_unit(symbol, price)
        except Exception:          # filters unreachable → keep the configured unit
            return 0.0

    def _sellable(self, symbol: str, want: float) -> float:
        """Cap the sell qty at the REAL free base balance. The buy fee is charged
        in the base asset, so our ledger qty is a hair more than what's actually
        on the exchange — selling the full ledger qty gets rejected (-2010)."""
        base = symbol.replace(config.quote_asset, "")
        free = self._bt.get_free_balances().get(base, 0.0)
        return min(want, free)

    def sell_bag(self, symbol: str, index: int, price: float) -> float:
        bag = self.bags(symbol)[index]
        resp = self._bt.market_sell(symbol, self._sellable(symbol, bag["qty"]), price)
        qty, quote, avg = self._bt.fill_amounts(resp, price)
        self.bags(symbol).pop(index)
        self.cash += quote
        pnl = quote - bag["cost"]
        self.realized += pnl
        self.store.log_trade(symbol=symbol, side="SELL", kind="bag",
                             price=avg, qty=qty, usdt=quote, pnl=pnl)
        return pnl

    def buy_hold(self, symbol: str, price: float, amount_usdt: float) -> None:
        resp = self._bt.market_buy(symbol, amount_usdt)
        qty, quote, avg = self._bt.fill_amounts(resp, price)
        if qty <= 0 or quote <= 0:      # unfilled order → never record a phantom hold
            raise BrokerError(f"{symbol}: hold-buy returned no fill (qty={qty}, quote={quote})")
        self.holds[symbol] = {"entry": avg, "qty": qty, "cost": quote,
                              "time": time.time(), "banked": False}
        self.cash -= quote
        self.store.log_trade(symbol=symbol, side="BUY", kind="hold",
                             price=avg, qty=qty, usdt=quote, pnl=0.0)

    def hold_splittable(self, symbol: str, price: float, frac: float) -> bool:
        """Both the banked slice AND the remainder must clear the exchange's
        NOTIONAL floor. Splitting a hold that is too small would leave an
        unsellable remainder — exactly what -1013 did to the BTC bag on
        2026-08-22 — so in that case we ride the whole position instead."""
        h = self.holds.get(symbol)
        if not h:
            return False
        try:
            floor = self._bt.min_notional(symbol)
        except Exception:
            return False                       # can't verify → don't split
        return (h["qty"] * frac * price >= floor
                and h["qty"] * (1 - frac) * price >= floor)

    def sell_hold_part(self, symbol: str, price: float, frac: float) -> float:
        """Live half-exit: market-sell `frac` of the hold, keep the rest riding."""
        h = self.holds[symbol]
        resp = self._bt.market_sell(symbol, self._sellable(symbol, h["qty"] * frac), price)
        qty, quote, avg = self._bt.fill_amounts(resp, price)
        cost = h["cost"] * frac
        self.cash += quote
        pnl = quote - cost
        self.realized += pnl
        h["qty"] -= qty                        # what the exchange actually sold
        h["cost"] -= cost
        h["banked"] = True
        self.store.log_trade(symbol=symbol, side="SELL", kind="hold-part",
                             price=avg, qty=qty, usdt=quote, pnl=pnl)
        return pnl

    def sell_hold(self, symbol: str, price: float) -> float:
        h = self.holds[symbol]
        resp = self._bt.market_sell(symbol, self._sellable(symbol, h["qty"]), price)
        qty, quote, avg = self._bt.fill_amounts(resp, price)
        self.holds.pop(symbol)
        self.cash += quote
        pnl = quote - h["cost"]
        self.realized += pnl
        self.store.log_trade(symbol=symbol, side="SELL", kind="hold",
                             price=avg, qty=qty, usdt=quote, pnl=pnl)
        return pnl

    # Drift must exceed BOTH a relative and an absolute floor to be worth a
    # warning: bags are tiny ($5), so sub-dollar dust trips 5% on its own and
    # spams the report with harmless noise.
    RECONCILE_REL = 0.05        # 5% of the logical qty
    RECONCILE_ABS_USD = 1.0     # ...and at least $1 of coin

    def reconcile(self, prices: dict[str, float]) -> list[str]:
        """Safety: warn if exchange coin balances drift from our logical ledger.

        Only surfaces material drift (> RECONCILE_REL *and* > RECONCILE_ABS_USD).
        Read-only: never trades — a warning is a heads-up, not an action.
        """
        warnings = []
        try:
            free = self._bt.get_free_balances()
        except Exception as exc:
            return [f"balance read failed: {str(exc)[:60]}"]
        for sym in set(self.positions) | set(self.holds):
            base = sym.replace(config.quote_asset, "")
            logical = sum(b["qty"] for b in self.bags(sym)) + \
                (self.holds[sym]["qty"] if sym in self.holds else 0.0)
            actual = free.get(base, 0.0)
            if logical <= 0:
                continue
            diff = abs(actual - logical)
            usd = diff * prices.get(sym, 0.0)
            if diff / logical > self.RECONCILE_REL and usd > self.RECONCILE_ABS_USD:
                warnings.append(
                    f"{base}: ledger {logical:.6f} vs exchange {actual:.6f} "
                    f"(~${usd:.2f})")
        return warnings


def get_grid_broker(account=None) -> GridBroker:
    """Live broker only when explicitly in live mode WITH this account's keys;
    else paper. `account` namespaces state and selects signing keys."""
    if account is None:
        account = config.accounts[0]
    if config.trading_mode == "live" and account.api_key and account.api_secret:
        return LiveGridBroker(account)
    return GridBroker(account)