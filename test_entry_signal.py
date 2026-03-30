"""
Full entry signal test — all 5 expanded layers.
Run: python test_entry_signal.py
"""

from src.binance_client import (
    get_candles,
    get_current_price,
    get_order_book_spread,
    get_order_book_depth,
    get_ticker_24h,
)
from src.indicators import check_entry_signal


def fmt(val, ok) -> str:
    return "✅" if ok else "❌"


def test_entry_signal():
    print("=" * 60)
    print("  ENTRY SIGNAL CHECK — All 5 Layers (Extended)")
    print("=" * 60)

    symbol = "BTCUSDT"

    print(f"\n📡 Fetching data for {symbol}...")
    candles = get_candles(symbol=symbol, interval="1h", limit=201)
    spread, best_bid, best_ask = get_order_book_spread(symbol)
    bid_depth, ask_depth = get_order_book_depth(symbol)
    ticker = get_ticker_24h(symbol)
    volume_24h = ticker["volume_usd"]
    price = get_current_price(symbol)

    print(f"✅ {len(candles)} candles fetched")
    print(f"💰 Price: ${price:,.2f}")
    print(f"📊 Spread: ${spread:.2f} | Bid depth: {bid_depth} BTC"
          f" | Ask depth: {ask_depth} BTC")
    print(f"📦 24h Volume: ${volume_24h:,.0f}")

    should_enter, report = check_entry_signal(
        candles, spread, bid_depth, ask_depth, volume_24h
    )

    print("\n" + "─" * 60)
    layers = report["layers"]

    l1 = layers["L1_volatility"]
    print(f"  {fmt(None, l1['pass'])} L1 Volatility")
    print(f"       ATR ${l1['atr']:,.2f} (MA ${l1['atr_ma']:,.2f})"
          f"  expanding={l1['atr_expanding']}")
    print(f"       Volume spike={l1['volume_spike']}"
          f"  ADX={l1['adx']:.1f}")

    l2 = layers["L2_trend"]
    print(f"  {fmt(None, l2['pass'])} L2 Trend")
    if "error" not in l2:
        print(f"       Price ${l2['price']:,.0f} | EMA50 ${l2['ema50']:,.0f}"
              f" | EMA200 ${l2['ema200']:,.0f}")
        print(f"       Slope={l2['ema50_slope_ok']}"
              f"  GoldenCross={l2['golden_cross']}"
              f"  Established={l2['established_uptrend']}")

    l3 = layers["L3_momentum"]
    print(f"  {fmt(None, l3['pass'])} L3 Momentum")
    print(f"       RSI={l3['rsi']:.1f} (ok={l3['rsi_ok']})"
          f"  MACD hist={l3['macd_hist']:.2f} (ok={l3['macd_ok']})")

    l4 = layers["L4_timing"]
    print(f"  {fmt(None, l4['pass'])} L4 Timing")
    print(f"       {l4['weekday']} {l4['hour_utc']:02d}:00 UTC"
          f"  hour_ok={l4['hour_ok']}"
          f"  weekday_ok={l4['weekday_ok']}")

    l5 = layers["L5_liquidity"]
    print(f"  {fmt(None, l5['pass'])} L5 Liquidity")
    print(f"       Spread=${l5['spread']:.2f}"
          f"  Depth bid={l5['bid_depth_btc']} ask={l5['ask_depth_btc']} BTC"
          f"  Vol ok={l5['volume_ok']}")

    print("─" * 60)
    if should_enter:
        print("  🚀 ENTER SIGNAL: YES — all layers passed!")
    else:
        failed = [k for k, v in layers.items() if not v["pass"]]
        print(f"  🚫 ENTER SIGNAL: NO")
        print(f"     Failed: {', '.join(failed)}")
    print("=" * 60)


if __name__ == "__main__":
    test_entry_signal()
