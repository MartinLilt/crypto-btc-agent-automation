"""
Order executor — simulation and live modes.

SIMULATION: reads current market price and fakes fill (no orders placed).
LIVE:       places real Binance market orders via python-binance client.
            Buy  uses quoteOrderQty (USDT amount → Binance calculates qty).
            Sell uses the exact qty returned by the buy order fill.
"""

import logging
import math

logger = logging.getLogger(__name__)

_BTC_STEP = 5   # decimal places for BTCUSDT quantity


def _round_down(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(value * factor) / factor


def execute_buy(symbol: str, budget: float, mode: str) -> tuple[float, float]:
    """
    Place a market buy for `budget` USDT worth of `symbol`.
    Returns (fill_price, qty_bought).
    Raises on failure.
    """
    from src.data.binance_client import get_current_price

    if mode == "simulation":
        price = get_current_price(symbol)
        qty = _round_down(budget / price, _BTC_STEP)
        logger.info("[SIM] BUY %s  qty=%.5f @ %.2f", symbol, qty, price)
        return price, qty

    # LIVE — real Binance market order
    from src.data.binance_client import client
    try:
        order = client.order_market_buy(
            symbol=symbol,
            quoteOrderQty=str(round(budget, 2)),
        )
        fills = order.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
            fill_price = round(total_cost / total_qty, 2) if total_qty else 0.0
            qty = _round_down(total_qty, _BTC_STEP)
        else:
            fill_price = float(order.get("price", 0))
            qty = _round_down(float(order.get("executedQty", 0)), _BTC_STEP)
        logger.info("[LIVE] BUY %s  orderId=%s  qty=%.5f @ %.2f",
                    symbol, order.get("orderId"), qty, fill_price)
        return fill_price, qty
    except Exception as e:
        logger.exception("LIVE buy failed: %s", e)
        raise


def execute_sell(symbol: str, qty: float, mode: str) -> float:
    """
    Sell `qty` of `symbol` at market.
    Returns fill_price.
    Raises on failure.
    """
    from src.data.binance_client import get_current_price

    if mode == "simulation":
        price = get_current_price(symbol)
        logger.info("[SIM] SELL %s  qty=%.5f @ %.2f", symbol, qty, price)
        return price

    # LIVE
    from src.data.binance_client import client
    try:
        sell_qty = str(_round_down(qty, _BTC_STEP))
        order = client.order_market_sell(symbol=symbol, quantity=sell_qty)
        fills = order.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
            fill_price = round(total_cost / total_qty, 2) if total_qty else 0.0
        else:
            fill_price = float(order.get("price", 0))
        logger.info("[LIVE] SELL %s  orderId=%s  qty=%s @ %.2f",
                    symbol, order.get("orderId"), sell_qty, fill_price)
        return fill_price
    except Exception as e:
        logger.exception("LIVE sell failed: %s", e)
        raise