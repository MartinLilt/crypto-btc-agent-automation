"""
Quick test for Layer 2 — Trend Check (Price > MA50 > MA200)
Run: python test_layer2.py
"""

from src.binance_client import get_candles, get_current_price
from src.indicators import calculate_ma, is_uptrend


def test_layer2():
    print("=" * 52)
    print("  LAYER 2 TEST — Trend Check (Long Only)")
    print("=" * 52)

    symbol = "BTCUSDT"

    # Need 201 candles to calculate MA200
    print(f"\n📡 Fetching 201 hourly candles for {symbol}...")
    candles = get_candles(symbol=symbol, interval="1h", limit=201)
    print(f"✅ Got {len(candles)} candles")

    current_price = get_current_price(symbol)
    ma50 = calculate_ma(candles, 50)
    ma200 = calculate_ma(candles, 200)

    print(f"\n💰 Current BTC price: ${current_price:,.2f}")
    print(f"📊 MA50:              ${ma50:,.2f}")
    print(f"📊 MA200:             ${ma200:,.2f}")

    print(f"\n🔍 Condition check:")
    print(
        f"   Price > MA50  → ${current_price:,.2f} > ${ma50:,.2f}  → {'✅' if current_price > ma50 else '❌'}")
    print(
        f"   MA50  > MA200 → ${ma50:,.2f} > ${ma200:,.2f}  → {'✅' if ma50 > ma200 else '❌'}")

    signal, details = is_uptrend(candles)

    print("\n" + "=" * 52)
    if signal:
        print("  ✅ LAYER 2 PASSED — Uptrend confirmed!")
        print(
            f"     Price ${details['price']:,.2f} > MA50 ${details['ma50']:,.2f} > MA200 ${details['ma200']:,.2f}")
    else:
        print("  ❌ LAYER 2 FAILED — No uptrend")
        print(
            f"     Price ${details['price']:,.2f} | MA50 ${details['ma50']:,.2f} | MA200 ${details['ma200']:,.2f}")
    print("=" * 52)


if __name__ == "__main__":
    test_layer2()
