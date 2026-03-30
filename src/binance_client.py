import os
import requests
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

client = Client(
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET")
)

# URLs from .env (with sensible defaults so the bot works even without them)
_BINANCE_REST = os.getenv("BINANCE_REST_URL", "https://api.binance.com")
_BINANCE_FUTURES = os.getenv("BINANCE_FUTURES_URL", "https://fapi.binance.com")
_FEAR_GREED_URL = os.getenv("FEAR_GREED_URL", "https://api.alternative.me/fng/?limit=2")


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


def get_funding_rate(symbol="BTCUSDT") -> dict:
    """
    Fetch current funding rate and open interest from Binance Futures.
    Funding rate > +0.05% means longs are overheated (bearish signal).
    Funding rate < -0.05% means shorts are overheated (bullish signal).
    Returns dict with funding_rate (%), open_interest_usd, oi_change_pct.
    """
    base = _BINANCE_FUTURES
    try:
        # Current funding rate
        fr_resp = requests.get(
            f"{base}/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 3},
            timeout=5,
        )
        fr_data = fr_resp.json()
        if not fr_data or isinstance(fr_data, dict):
            funding_rate = 0.0
        else:
            funding_rate = float(
                fr_data[-1]["fundingRate"]) * 100  # convert to %

        # Current open interest
        oi_resp = requests.get(
            f"{base}/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=5,
        )
        oi_data = oi_resp.json()
        open_interest = float(oi_data.get("openInterest", 0))

        # Historical OI (last 2 periods = ~16h) to compute change
        oi_hist_resp = requests.get(
            f"{base}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": "1h", "limit": 8},
            timeout=5,
        )
        oi_hist = oi_hist_resp.json()
        if isinstance(oi_hist, list) and len(oi_hist) >= 2:
            oi_old = float(oi_hist[0]["sumOpenInterest"])
            oi_new = float(oi_hist[-1]["sumOpenInterest"])
            oi_change_pct = round((oi_new - oi_old) /
                                  oi_old * 100, 2) if oi_old else 0.0
        else:
            oi_change_pct = 0.0

        return {
            "funding_rate": round(funding_rate, 4),
            "open_interest": round(open_interest, 2),
            "oi_change_pct": oi_change_pct,
            "ok": True,
        }
    except Exception as e:
        return {"funding_rate": 0.0, "open_interest": 0.0, "oi_change_pct": 0.0, "ok": False, "error": str(e)}


def get_fear_greed_index() -> dict:
    """
    Fetch the Crypto Fear & Greed Index from alternative.me (no API key needed).
    Returns dict with value (0-100) and classification string.
    Extreme Fear (0-25): market oversold, good entry opportunity.
    Extreme Greed (75-100): market overheated, risky entry.
    """
    try:
        resp = requests.get(
            _FEAR_GREED_URL,
            timeout=5,
        )
        data = resp.json()
        items = data.get("data", [])
        if not items:
            return {"value": 50, "classification": "Neutral", "ok": False}

        current = items[0]
        previous = items[1] if len(items) > 1 else items[0]
        value = int(current["value"])
        prev_value = int(previous["value"])

        return {
            "value": value,
            "classification": current["value_classification"],
            "prev_value": prev_value,
            "change": value - prev_value,
            "ok": True,
        }
    except Exception as e:
        return {"value": 50, "classification": "Neutral", "prev_value": 50, "change": 0, "ok": False, "error": str(e)}


def get_taker_buy_pressure(symbol="BTCUSDT", hours=24) -> dict:
    """
    Exchange Buy/Sell Pressure via Binance Taker Volume (free, no key needed).

    Uses kline field [9] = Taker Buy Base Volume — the volume of trades
    where the buyer was the aggressor (market buy orders).
    Sell pressure = Total volume - Taker buy volume.

    This is the best free proxy for exchange netflow:
      - Buy ratio > 55%: buyers dominate → bullish pressure
      - Buy ratio < 45%: sellers dominate → bearish pressure
      - 45–55%: balanced market

    Returns dict with buy_btc, sell_btc, net_btc, buy_ratio_pct, trend.
    """
    try:
        resp = requests.get(
            f"{_BINANCE_REST}/api/v3/klines",
            params={"symbol": symbol, "interval": "1h", "limit": hours},
            timeout=5,
        )
        klines = resp.json()
        if not klines or not isinstance(klines, list):
            return {"ok": False, "buy_ratio_pct": 50.0}

        total_buy = sum(float(k[9]) for k in klines)   # taker buy BTC
        total_vol = sum(float(k[5]) for k in klines)   # total volume BTC
        total_sell = total_vol - total_buy

        buy_ratio = (total_buy / total_vol * 100) if total_vol > 0 else 50.0
        net_btc = total_buy - total_sell

        if buy_ratio > 55:
            trend = "bullish"
        elif buy_ratio < 45:
            trend = "bearish"
        else:
            trend = "neutral"

        return {
            "buy_btc": round(total_buy, 2),
            "sell_btc": round(total_sell, 2),
            "net_btc": round(net_btc, 2),
            "buy_ratio_pct": round(buy_ratio, 1),
            "trend": trend,
            "hours": hours,
            "ok": True,
        }
    except Exception as e:
        return {
            "buy_btc": 0.0, "sell_btc": 0.0, "net_btc": 0.0,
            "buy_ratio_pct": 50.0, "trend": "neutral",
            "hours": hours, "ok": False, "error": str(e),
        }
