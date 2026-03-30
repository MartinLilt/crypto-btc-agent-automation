import os
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

client = Client(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET")
)


def get_candles(symbol="BTCUSDT", interval="1h", limit=15):
    """Fetch last N hourly candles from Binance (OHLCV)"""
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    candles = []
    for c in raw:
        candles.append({
            "open":   float(c[1]),
            "high":   float(c[2]),
            "low":    float(c[3]),
            "close":  float(c[4]),
            "volume": float(c[5]),
        })
    return candles


def get_current_price(symbol="BTCUSDT"):
    """Fetch current BTC price"""
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def get_order_book_spread(symbol="BTCUSDT") -> tuple[float, float, float]:
    """
    Fetch best bid/ask from order book.
    Returns (spread, best_bid, best_ask)
    """
    book = client.get_order_book(symbol=symbol, limit=5)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])
    spread = round(best_ask - best_bid, 2)
    return spread, best_bid, best_ask


def get_order_book_depth(
    symbol="BTCUSDT",
    price_range_usd=50
) -> tuple[float, float]:
    """
    Sum bid/ask volume within price_range_usd of best price.
    Returns (bid_depth_btc, ask_depth_btc)
    """
    book = client.get_order_book(symbol=symbol, limit=50)
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])

    bid_depth = sum(
        float(qty) for price, qty in book["bids"]
        if float(price) >= best_bid - price_range_usd
    )
    ask_depth = sum(
        float(qty) for price, qty in book["asks"]
        if float(price) <= best_ask + price_range_usd
    )
    return round(bid_depth, 4), round(ask_depth, 4)


def get_ticker_24h(symbol="BTCUSDT") -> dict:
    """
    Fetch 24h stats: volume, price change percent.
    Returns dict with volume_usd and price_change_pct.
    """
    t = client.get_ticker(symbol=symbol)
    return {
        "volume_usd":       round(float(t["quoteVolume"]), 0),
        "price_change_pct": round(float(t["priceChangePercent"]), 2),
    }
