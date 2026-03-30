"""
Quick test for Layer 1 — ATR Volatility Check
Run: python test_layer1.py
"""

from src.binance_client import get_candles, get_current_price
from src.indicators import calculate_atr, is_market_moving, ATR_THRESHOLD


def test_layer1():
    print("=" * 50)
    print("  LAYER 1 TEST — ATR Volatility Check")
    print("=" * 50)

    symbol = "BTCUSDT"

    print(f"\n📡 Fetching 15 hourly candles for {symbol}...")
    candles = get_candles(symbol=symbol, interval="1h", limit=15)
    print(f"✅ Got {len(candles)} candles")

    print("\n📊 Last 5 candles:")
    for i, c in enumerate(candles[-5:], 1):
        print(
            f"  {i}. O:{c['open']:.2f}  H:{c['high']:.2f}  L:{c['low']:.2f}  C:{c['close']:.2f}")

    current_price = get_current_price(symbol)
    print(f"\n💰 Current BTC price: ${current_price:,.2f}")

    atr = calculate_atr(candles)
    print(f"\n📈 ATR (14-period): ${atr:,.2f}")
    print(f"📏 ATR Threshold:   ${ATR_THRESHOLD:,.2f}")

    signal, atr_value = is_market_moving(candles)

    print("\n" + "=" * 50)
    if signal:
        print(f"  ✅ LAYER 1 PASSED — Market is moving!")
        print(f"     ATR ${atr_value:,.2f} > threshold ${ATR_THRESHOLD:,.2f}")
    else:
        print(f"  ❌ LAYER 1 FAILED — Market too quiet")
        print(f"     ATR ${atr_value:,.2f} ≤ threshold ${ATR_THRESHOLD:,.2f}")
    print("=" * 50)


if __name__ == "__main__":
    test_layer1()
