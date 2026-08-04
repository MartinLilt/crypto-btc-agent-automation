"""Backtest engine — replay the SMA strategy over historical candles.

Walks forward candle by candle: at each closed candle it feeds the history so
far to the SAME strategy the live runner uses (src.strategy.evaluate), then
simulates the resulting BUY/SELL on a virtual balance — identical accounting to
PaperBroker (fixed USDT per buy, taker fee both sides, single position).

No network state, no files: pure in-memory simulation over a candle list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.binance_api import Candle
from src.config import config
from src.exits import ExitRules, check_exit, rules_from_config
from src.policy import decide
from src.regime import Regime, detect_regime, resample, tf_factor
from src.strategy import Signal, get_strategy


def _regime_timeline(candles: list[Candle], factor: int) -> list[Regime]:
    """Per-source-candle regime measured on a higher timeframe, no lookahead.

    Source candles are resampled into higher-TF bars; the regime of each
    COMPLETED higher-TF bar is mapped back onto the source candles that follow
    it. A source candle uses the last fully-closed higher-TF bar only.
    """
    higher = resample(candles, factor)
    per_bar = [detect_regime(higher[: j + 1]).regime for j in range(len(higher))]
    timeline: list[Regime] = []
    for i in range(len(candles)):
        completed_bar = i // factor - 1  # exclude the still-forming bar
        timeline.append(per_bar[completed_bar] if completed_bar >= 0 else Regime.NEUTRAL)
    return timeline


@dataclass
class BacktestTrade:
    side: str
    time: str
    price: float
    base_qty: float
    quote_qty: float
    pnl: float = 0.0  # realised PnL in USDT, set on the closing SELL


@dataclass
class BacktestResult:
    start_balance: float
    end_equity: float           # after Binance fees, before tax
    buy_hold_equity: float
    total_fees: float = 0.0     # sum of Binance fees paid (buy + sell)
    warmup: int = 0             # first traded candle index (equity_curve[0] == here)
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    @property
    def return_pct(self) -> float:
        return (self.end_equity / self.start_balance - 1) * 100

    @property
    def buy_hold_pct(self) -> float:
        return (self.buy_hold_equity / self.start_balance - 1) * 100

    @property
    def realized_pnl(self) -> float:
        """Profit from CLOSED trades only (open bags/holds are NOT realised)."""
        return sum(t.pnl for t in self.trades if t.side == "SELL")

    def tax(self, rate: float | None = None, allowance: float | None = None) -> float:
        """Lithuania GPM-style tax on REALISED gains only (net of Binance fees).

        Correct model: tax hits realised profit (closed trades) above the annual
        allowance — NOT unrealised open positions, and not per-trade. Losses are
        never taxed. rate/allowance default to .env (TAX_RATE, TAX_ALLOWANCE).
        """
        rate = config.tax_rate if rate is None else rate
        allowance = config.tax_allowance if allowance is None else allowance
        taxable = max(0.0, self.realized_pnl - allowance)
        return taxable * rate

    def net_equity(self, rate: float | None = None, allowance: float | None = None) -> float:
        """Final balance after Binance fees AND tax — the real take-home."""
        return self.end_equity - self.tax(rate, allowance)

    def net_return_pct(self, rate: float | None = None, allowance: float | None = None) -> float:
        return (self.net_equity(rate, allowance) / self.start_balance - 1) * 100

    @property
    def n_trades(self) -> int:
        return sum(1 for t in self.trades if t.side == "SELL")

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.side == "SELL" and t.pnl > 0)

    @property
    def win_rate(self) -> float:
        closed = self.n_trades
        return (self.wins / closed * 100) if closed else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        peak = -1.0
        mdd = 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                mdd = max(mdd, (peak - eq) / peak)
        return mdd * 100


def run_backtest(
    candles: list[Candle],
    strategy_name: str | None = None,
    params: dict | None = None,
    start_balance: float | None = None,
    spend: float | None = None,
    fee_rate: float | None = None,
    use_regime: bool | None = None,
    warmup_override: int | None = None,
    trading_interval: str | None = None,
    regime_interval: str | None = None,
    exit_rules: ExitRules | None = None,
) -> BacktestResult:
    strat = get_strategy(strategy_name)
    params = params or strat.default_params
    use_regime = config.regime_gate if use_regime is None else use_regime
    exit_rules = exit_rules if exit_rules is not None else rules_from_config()

    # Multi-timeframe: measure the regime on `regime_interval` while trading on
    # `trading_interval`. factor = how many trading bars fit in one regime bar.
    trading_interval = trading_interval or config.interval
    regime_interval = regime_interval or config.regime_interval
    factor = tf_factor(trading_interval, regime_interval) if use_regime else 1
    regime_line = _regime_timeline(candles, factor) if use_regime else None

    # The regime filter needs enough higher-TF history; start once BOTH ready.
    warmup = strat.warmup(params)
    if use_regime:
        regime_bars_needed = config.regime_ma + config.regime_slope_lookback + 1
        warmup = max(warmup, regime_bars_needed * factor)
    # For a fair A/B across configs, callers can force a common start candle.
    if warmup_override is not None:
        warmup = max(warmup, warmup_override)
    start_balance = start_balance if start_balance is not None else config.paper_start_balance
    spend = spend if spend is not None else config.quote_order_qty
    fee_rate = fee_rate if fee_rate is not None else config.fee_rate

    quote = start_balance
    base = 0.0
    entry_cost = 0.0    # USDT spent to open the current position (incl. fee)
    entry_price = 0.0   # price we bought at (for TP/SL)
    peak_price = 0.0    # highest price since entry (for trailing stop)
    bars_held = 0       # bars since entry (for timeout)

    result = BacktestResult(
        start_balance=start_balance,
        end_equity=start_balance,
        buy_hold_equity=start_balance,
        warmup=warmup,
    )

    # Not enough history to trade after warmup — return a flat (no-op) result.
    if warmup >= len(candles):
        return result

    def _sell(ts: str, price: float) -> None:
        nonlocal quote, base, entry_cost
        gross = base * price
        fee = gross * fee_rate
        proceeds = gross - fee
        quote += proceeds
        result.total_fees += fee
        result.trades.append(
            BacktestTrade("SELL", ts, price, base, proceeds, proceeds - entry_cost))
        base = 0.0
        entry_cost = 0.0

    # Need `warmup` candles before the strategy can emit its first real signal.
    for i in range(warmup, len(candles)):
        window = candles[: i + 1]
        price = candles[i].close
        ts = f"{candles[i].open_time:%Y-%m-%d %H:%M}"

        # 1. In a position? Exit rules get first say (before the strategy).
        if base > 1e-12:
            bars_held += 1
            peak_price = max(peak_price, price)
            if exit_rules.any_active and check_exit(
                    exit_rules, entry_price, peak_price, bars_held, price):
                _sell(ts, price)
                result.equity_curve.append(quote)
                continue

        # 2. Strategy / regime decision.
        override = regime_line[i] if regime_line is not None else None
        sig = decide(window, strategy_name=strat.name, params=params,
                     use_regime=use_regime, regime_override=override).signal

        if sig is Signal.BUY and base <= 1e-12 and quote > 0:
            use = min(spend, quote)
            fee = use * fee_rate
            qty = (use - fee) / price
            quote -= use
            base += qty
            entry_cost = use
            entry_price = price
            peak_price = price
            bars_held = 0
            result.total_fees += fee
            result.trades.append(BacktestTrade("BUY", ts, price, qty, use))

        elif sig is Signal.SELL and base > 1e-12:
            _sell(ts, price)

        result.equity_curve.append(quote + base * price)

    last_price = candles[-1].close
    result.end_equity = quote + base * last_price

    # Buy & hold benchmark: spend the whole start balance at the first usable
    # candle and hold to the end.
    first_price = candles[warmup].close
    result.buy_hold_equity = start_balance / first_price * last_price

    return result