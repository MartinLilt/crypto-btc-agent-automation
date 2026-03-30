"""
Quick test for Layer 3 — RSI Momentum Check (RSI < 70)
Run: python test_layer3.py
"""

from src.binance_client import get_candles, get_current_price
from src.indicators import calculate_rsi, is_not_overbought, RSI_OVERBOUGHT


def test_layer3():
    print("=" * 52)
    print("  LAYER 3 TEST — RSI Momentum Check")
    print("=" * 52)

    symbol = "BTCUSDT"

    # Need at least 15 candles for RSI-14
    print(f"\n📡 Fetching 50 hourly candles for {symbol}...")
    candles = get_candles(symbol=symbol, interval="1h", limit=50)
    print(f"✅ Got {len(candles)} candles")

    current_price = get_current_price(symbol)
    rsi = calculate_rsi(candles)

    print(f"\n💰 Current BTC price: ${current_price:,.2f}")
    print(f"📊 RSI (14-period):   {rsi:.2f}")
    print(f"📏 Overbought level:  {RSI_OVERBOUGHT}")

    print("\n🔍 Zones:")
    if rsi < 30:
        zone = "🟢 Oversold (strong buy zone)"
    elif rsi < 50:
        zone = "🟡 Neutral-bearish"
    elif rsi < 70:
        zone = "🟡 Neutral-bullish"
    else:
        zone = "🔴 Overbought"
    print(f"   RSI {rsi:.2f} → {zone}")

    signal, rsi_value = is_not_overbought(candles)

    print("\n" + "=" * 52)
    if signal:
        print(f"  ✅ LAYER 3 PASSED — Room to grow!")
        print(f"     RSI {rsi_value:.2f} < {RSI_OVERBOUGHT} (not overbought)")
    else:
        print(f"  ❌ LAYER 3 FAILED — Market overbought")
        print(f"     RSI {rsi_value:.2f} ≥ {RSI_OVERBOUGHT}")
    print("=" * 52)


if __name__ == "__main__":
    test_layer3()
