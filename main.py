"""Autonomous runner — one decision cycle for short-term spot.

    autopilot (coin -> tendency -> strategy) -> policy gate -> broker -> report

The system chooses coin, tendency and strategy on its own. The ONLY thing you
switch by hand is TRADING_MODE (paper | live). Autonomy switches in .env:
    AUTO_TARGET    true = find the coin           (else trade SYMBOL)
    AUTO_STRATEGY  true = pick the strategy        (else use STRATEGY)
    REGIME_GATE    true = read tendency & adapt
Run:
    source .venv/bin/activate
    python main.py
"""

from __future__ import annotations

import time

from src.autopilot import plan
from src.binance_api import get_candles, get_price
from src.broker import execute, get_broker
from src.config import config
from src.exits import check_exit, rules_from_config
from src.policy import decide
from src.regime import _INTERVAL_SECONDS
from src.strategy import Signal, evaluate


def main() -> None:
    print("Autopilot: choosing coin → tendency → strategy ...")
    p = plan()

    if p.symbol is None:
        print(f"→ {p.reason}. Nothing to do.")
        return

    symbol, strategy = p.symbol, p.strategy
    if p.strategy_pick is not None:
        sp = p.strategy_pick
        print(f"🎯 coin: {symbol}   📈 tendency: {p.regime.value if p.regime else '—'}   "
              f"🧠 strategy: {strategy.upper()} "
              f"(backtest {sp.net_return_pct:+.2f}% net, {sp.win_rate:.0f}% win)\n")
    else:
        print(f"🎯 coin: {symbol}   📈 tendency: {p.regime.value if p.regime else '—'}   "
              f"🧠 strategy: {strategy.upper()} (fixed)\n")

    print(f"=== {symbol} | mode={config.trading_mode.upper()} "
          f"| strategy={strategy.upper()} | regime_gate={config.regime_gate} "
          f"| trade={config.interval} regime={config.regime_interval} ===\n")

    candles = get_candles(symbol=symbol)
    price = get_price(symbol)

    # Strategy's own view (for display), then the regime-gated decision.
    result = evaluate(candles, name=strategy)
    decision = decide(candles, strategy_name=strategy, regime_override=p.regime)

    print(f"price now   : {price:.4f} USDT")
    if decision.regime is not None:
        print(f"regime      : {decision.regime.value}  (on {config.regime_interval})")
    for name, value in result.indicators.items():
        if value is not None:
            print(f"{name:<12}: {value:.4f}")
    print(f"raw signal  : {decision.raw_signal.value}  ({result.reason})")
    print(f"decision    : {decision.signal.value}  ({decision.reason})")

    base_asset = symbol.replace("USDT", "").replace("EUR", "")
    broker = get_broker()
    pos = broker.position
    print(f"\nposition    : {pos.base:.6f} {base_asset} + {pos.quote:.2f} USDT "
          f"(equity {pos.equity(price):.2f} USDT)")

    # Exit management gets first say — keeps "longs" short (TP/SL/trailing/timeout).
    final_signal = decision.signal
    if pos.is_long:
        broker.update_peak(price)
        pos = broker.position
        rules = rules_from_config()
        bar_s = _INTERVAL_SECONDS.get(config.interval, 3600)
        bars_held = int((time.time() - pos.entry_epoch) / bar_s) if pos.entry_epoch else 0
        if rules.any_active:
            reason = check_exit(rules, pos.entry_price, pos.peak_price, bars_held, price)
            if reason:
                final_signal = Signal.SELL
                print(f"exit rule   : {reason} → SELL (held ~{bars_held} bars)")

    trade = execute(broker, final_signal, price)
    if trade:
        print(f"EXECUTED    : {trade.side} {trade.base_qty:.6f} {base_asset} "
              f"@ {trade.price:.4f}  (fee {trade.fee:.4f})")
        pos = broker.position
        print(f"new position: {pos.base:.6f} {base_asset} + {pos.quote:.2f} USDT "
              f"(equity {pos.equity(price):.2f} USDT)")
    else:
        print("no action   : position unchanged")


if __name__ == "__main__":
    main()