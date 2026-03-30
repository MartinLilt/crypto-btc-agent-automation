"""
Quick test for Layer 5 — Liquidity Check (Bid/Ask spread < $10)
Run: python test_layer5.py
"""

from src.binance_client import get_order_book_spread
from src.indicators import has_liquidity, MAX_SPREAD_USD


def test_layer5():
    print("=" * 52)
    print("  LAYER 5 TEST — Liquidity / Spread Check")
    print("=" * 52)

    symbol = "BTCUSDT"

    print(f"\n📡 Fetching order book for {symbol}...")
    spread, best_bid, best_ask = get_order_book_spread(symbol)

    print(f"\n📗 Best Bid:          ${best_bid:,.2f}")
    print(f"📕 Best Ask:          ${best_ask:,.2f}")
    print(f"📊 Spread:            ${spread:.2f}")
    print(f"📏 Max allowed:       ${MAX_SPREAD_USD}")

    signal, spread_value = has_liquidity(spread)

    print("\n" + "=" * 52)
    if signal:
        print(f"  ✅ LAYER 5 PASSED — Good liquidity!")
        print(
            f"     Spread ${spread_value:.2f} < ${MAX_SPREAD_USD} — safe to enter")
    else:
        print(f"  ❌ LAYER 5 FAILED — Spread too wide")
        print(
            f"     Spread ${spread_value:.2f} ≥ ${MAX_SPREAD_USD} — risk of slippage")
    print("=" * 52)


if __name__ == "__main__":
    test_layer5()
