"""
News Client — fetches recent crypto headlines for a given asset.

Sources (tried in order):
1. CryptoPanic API — if CRYPTOPANIC_API_KEY is set in .env
2. CoinDesk RSS    — public, no auth required (fallback)

Returns a normalised list of news items with sentiment labels.
Sentiment is determined by simple keyword matching when vote data
is unavailable (RSS fallback).
"""

import os
import logging
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
CRYPTOPANIC_BASE = os.getenv("CRYPTOPANIC_URL", "https://cryptopanic.com/api/v1/posts/")

# CoinDesk RSS — works without auth
_coindesk_rss = os.getenv("COINDESK_RSS_URL", "https://www.coindesk.com/arc/outboundfeeds/rss/?category=markets")
COINDESK_RSS = {
    "BTC": _coindesk_rss,
    "ETH": _coindesk_rss,
    "LTC": _coindesk_rss,
    "SOL": _coindesk_rss,
    "LINK": _coindesk_rss,
}

NEWS_WINDOW_HOURS = 24
NEWS_LIMIT = 20
REQUEST_TIMEOUT = 8

# Keywords used for sentiment when vote data is absent (RSS path)
_BULLISH_KW = {
    "rally", "surge", "soar", "jump", "gain", "rise", "bull",
    "breakout", "milestone", "record", "high", "pump", "recovery",
    "inflow", "adoption", "approve", "approved", "etf", "upgrade",
}
_BEARISH_KW = {
    "crash", "dump", "plunge", "drop", "fall", "bear", "selloff",
    "hack", "exploit", "ban", "crackdown", "fine", "lawsuit", "fraud",
    "liquidation", "outflow", "reject", "rejected", "warning",
}


def _currency_for_symbol(symbol: str) -> str:
    """Convert BTCUSDT → BTC, ETHUSDT → ETH."""
    return symbol.replace("USDT", "").replace("BUSD", "").upper()


def _keyword_sentiment(title: str) -> str:
    words = set(title.lower().split())
    bull = len(words & _BULLISH_KW)
    bear = len(words & _BEARISH_KW)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


# ── Source 1: CryptoPanic (requires free account token) ───────────────────────

def _fetch_cryptopanic(currency: str) -> list[dict]:
    if not CRYPTOPANIC_API_KEY:
        return []
    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "currencies": currency,
        "kind":       "news",
        "limit":      NEWS_LIMIT,
    }
    try:
        resp = requests.get(
            CRYPTOPANIC_BASE, params=params, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("CryptoPanic API error: %s", e)
        return []

    news = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=NEWS_WINDOW_HOURS)

    for item in data.get("results", []):
        try:
            published_str = item.get("published_at", "")
            published = datetime.fromisoformat(
                published_str.replace("Z", "+00:00")
            )
            if published < cutoff:
                continue
            votes = item.get("votes", {})
            pos = votes.get("positive", 0)
            neg = votes.get("negative", 0)
            imp = votes.get("important", 0)
            if pos > neg and pos > 0:
                sentiment = "bullish"
            elif neg > pos and neg > 0:
                sentiment = "bearish"
            else:
                sentiment = "neutral"
            news.append({
                "title":     item.get("title", "").strip(),
                "url":       item.get("url", ""),
                "published": published_str,
                "votes":     {"positive": pos, "negative": neg, "important": imp},
                "sentiment": sentiment,
            })
        except Exception:
            continue

    logger.info("CryptoPanic: %d articles for %s", len(news), currency)
    return news


# ── Source 2: CoinDesk RSS (no auth) ──────────────────────────────────────────

def _fetch_coindesk_rss(currency: str) -> list[dict]:
    url = COINDESK_RSS.get(currency, COINDESK_RSS["BTC"])
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        logger.warning("CoinDesk RSS error: %s", e)
        return []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=NEWS_WINDOW_HOURS)
    kw = currency.lower()  # "btc" or "eth"
    kw_alt = "bitcoin" if currency == "BTC" else "ethereum"

    news = []
    # RSS: channel → item*
    channel = root.find("channel")
    if channel is None:
        return []

    for item in channel.findall("item"):
        try:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_raw = (item.findtext("pubDate") or "").strip()

            # Filter by keyword relevance
            title_lower = title.lower()
            if kw not in title_lower and kw_alt not in title_lower:
                continue

            # Parse RFC-2822 date
            from email.utils import parsedate_to_datetime
            try:
                published_dt = parsedate_to_datetime(pub_raw)
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
                if published_dt < cutoff:
                    continue
                published_str = published_dt.isoformat()
            except Exception:
                published_str = pub_raw

            sentiment = _keyword_sentiment(title)
            news.append({
                "title":     title,
                "url":       link,
                "published": published_str,
                "votes":     {"positive": 0, "negative": 0, "important": 0},
                "sentiment": sentiment,
            })
        except Exception:
            continue

    logger.info("CoinDesk RSS: %d relevant articles for %s",
                len(news), currency)
    return news[:NEWS_LIMIT]


# ── Public API ────────────────────────────────────────────────────────────────

def get_recent_news(symbol: str) -> list[dict]:
    """
    Fetch recent news for the trading pair symbol.
    Tries CryptoPanic first (if API key set), falls back to CoinDesk RSS.
    Returns list of normalised news dicts, newest first.
    """
    currency = _currency_for_symbol(symbol)

    # Try CryptoPanic if we have a key
    if CRYPTOPANIC_API_KEY:
        news = _fetch_cryptopanic(currency)
        if news:
            return news

    # Fallback: CoinDesk RSS
    return _fetch_coindesk_rss(currency)


def summarise_news(news: list[dict]) -> dict:
    """
    Aggregate a news list into the sentiment summary dict used by Layer 7.

    Returns:
    {
        "total":     int,
        "bullish":   int,
        "bearish":   int,
        "neutral":   int,
        "important": int,   articles with important votes > 0
        "score":     float, net sentiment -1.0 to +1.0
        "headlines": list[str],  up to 3 top titles
    }
    """
    if not news:
        return {
            "total": 0, "bullish": 0, "bearish": 0,
            "neutral": 0, "important": 0, "score": 0.0, "headlines": [],
        }

    bullish = sum(1 for n in news if n["sentiment"] == "bullish")
    bearish = sum(1 for n in news if n["sentiment"] == "bearish")
    neutral = len(news) - bullish - bearish
    important = sum(1 for n in news if n["votes"]["important"] > 0)

    total = len(news)
    score = round((bullish - bearish) / total, 2) if total else 0.0

    # Top headlines: important first, then newest
    sorted_news = sorted(
        news,
        key=lambda n: (n["votes"]["important"], n["votes"]["positive"]),
        reverse=True,
    )
    headlines = [n["title"] for n in sorted_news[:3]]

    return {
        "total": total,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "important": important,
        "score": score,
        "headlines": headlines,
    }
