"""
Backtest Engine — runs all 10 signal layers over historical candles.

Flow:
  1. Fetch full candle history (Binance, free, no auth)
  2. Pre-fetch Fear & Greed history → Redis cache
  3. Pre-fetch Funding Rate history → Redis cache (futures only)
  4. Slide window over candles; at each bar run layers L1-L10
  5. On signal: simulate entry at open[i+1], track TP/SL/timeout
  6. Save run + trades to SQLite
  7. Return summary dict for Telegram report

Entry price = open of the NEXT candle after signal (realistic slippage model).
TP/SL are checked against high/low of each subsequent candle.
If neither hits within MAX_HOLD_HOURS → exit at close of last candle.
"""

import logging
import math
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from src.db import (
    cache_fear_greed_history,
    cache_funding_history,
    get_fear_greed_for_date,
    get_funding_for_timestamp,
    init_db,
    save_backtest_run,
    save_backtest_trades,
)
from src.indicators import (
    check_buy_pressure,
    check_fear_greed,
    check_funding_rate,
    check_risk_reward,
    has_liquidity,
    is_market_moving,
    is_not_overbought,
    is_uptrend,
    GOOD_HOURS_UTC,
    SKIP_WEEKDAYS,
)

load_dotenv()
logger = logging.getLogger(__name__)

_BINANCE_REST = os.getenv("BINANCE_REST_URL", "https://api.binance.com")
_BINANCE_FUTURES = os.getenv(
    "BINANCE_FUTURES_URL", "https://fapi.binance.com")
_FEAR_GREED_URL = os.getenv(
    "FEAR_GREED_URL", "https://api.alternative.me/fng/?limit=2")

MAX_HOLD_HOURS = 48     # exit timeout if neither TP nor SL hit
WARMUP_CANDLES = 210    # need 200+ for EMA-200


# ── 1. Data fetching ──────────────────────────────────────────────────────────

def _fetch_candles_full(symbol: str, days: int, interval: str = "1h") -> list:
    """
    Download full candle history for given days.
    Binance limit = 1000 per request; we paginate backwards.
    Each candle: {open_time_ms, open, high, low, close, volume, taker_buy_vol}
    """
    limit = 1000
    needed = days * 24 + WARMUP_CANDLES  # extra for indicators warmup
    url = f"{_BINANCE_REST}/api/v3/klines"
    all_candles = []
    end_time = None

    while len(all_candles) < needed:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if end_time:
            params["endTime"] = end_time

        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        parsed = []
        for c in batch:
            parsed.append({
                "open_time_ms":  int(c[0]),
                "open":          float(c[1]),
                "high":          float(c[2]),
                "low":           float(c[3]),
                "close":         float(c[4]),
                "volume":        float(c[5]),
                "taker_buy_vol": float(c[9]),
            })
        # prepend (we go backwards)
        all_candles = parsed + all_candles
        end_time = batch[0][0] - 1      # go further back
        if len(batch) < limit:
            break
        time.sleep(0.1)                 # be polite to Binance

    logger.info("Fetched %d candles for %s (%dd)", len(all_candles),
                symbol, days)
    return all_candles[-needed:]        # trim to exactly what we need


def _fetch_fear_greed_history() -> dict:
    """
    Download full F&G history (up to 365 days) → {YYYY-MM-DD: int_value}.
    Cached in Redis.
    """
    cached = get_fear_greed_for_date("_meta")  # sentinel key
    if cached:
        from src.db import cache_get
        return cache_get("fg:history") or {}

    try:
        url = _FEAR_GREED_URL.split("?")[0] + "?limit=365&date_format=us"
        resp = requests.get(url, timeout=10)
        data = resp.json().get("data", [])
        history = {}
        for item in data:
            # timestamp → YYYY-MM-DD
            ts = int(item["timestamp"])
            date_str = datetime.fromtimestamp(
                ts, tz=timezone.utc).strftime("%Y-%m-%d")
            history[date_str] = int(item["value"])
        history["_meta"] = 1            # sentinel so we know it's loaded
        cache_fear_greed_history(history)
        logger.info("Loaded F&G history: %d dates", len(history) - 1)
        return history
    except Exception as e:
        logger.warning("F&G history fetch failed: %s", e)
        return {}


def _fetch_funding_history(symbol: str) -> list:
    """
    Download funding rate history from Binance Futures.
    Returns list of {timestamp: str, rate: float} or [] for spot-only assets.
    Cached in Redis.
    """
    from src.db import cache_get
    cached = cache_get(f"funding:{symbol}:history")
    if cached:
        return cached

    try:
        url = f"{_BINANCE_FUTURES}/fapi/v1/fundingRate"
        resp = requests.get(
            url,
            params={"symbol": symbol, "limit": 1000},
            timeout=10,
        )
        data = resp.json()
        if isinstance(data, dict):      # error response
            return []
        history = []
        for item in data:
            ts_ms = int(item["fundingTime"])
            ts_str = datetime.fromtimestamp(
                ts_ms / 1000, tz=timezone.utc).isoformat()
            history.append({
                "timestamp": ts_str,
                "rate": float(item["fundingRate"]) * 100,
            })
        cache_funding_history(symbol, history)
        logger.info("Loaded funding history for %s: %d records",
                    symbol, len(history))
        return history
    except Exception as e:
        logger.warning("Funding history fetch failed for %s: %s", symbol, e)
        return []


# ── 2. Per-bar layer evaluation ───────────────────────────────────────────────

def _eval_bar(candles_window: list, ts_ms: int,
              fg_history: dict, funding_history: list,
              tp_pct: float, sl_pct: float,
              spread_approx: float, symbol: str) -> tuple[bool, dict]:
    """
    Run all layers on a window of candles ending at index i.
    Returns (signal: bool, layer_snapshot: dict)
    """
    # L1 — Volatility
    l1_pass, l1 = is_market_moving(candles_window)

    # L2 — Trend
    l2_pass, l2 = is_uptrend(candles_window)

    # L3 — Momentum
    l3_pass, l3 = is_not_overbought(candles_window)

    # L4 — Timing (evaluated against historical candle time, not now)
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    _hour_ok = dt.hour in GOOD_HOURS_UTC
    _day_ok = dt.weekday() not in SKIP_WEEKDAYS
    l4_pass = _hour_ok and _day_ok
    l4 = {
        "hour_utc": dt.hour,
        "weekday": dt.strftime("%A"),
        "hour_ok": _hour_ok,
        "weekday_ok": _day_ok,
    }

    # L5 — Liquidity (approximated from candle data)
    last = candles_window[-1]
    approx_spread = (last["high"] - last["low"]) * 0.1  # rough mid-spread
    volume_24h = sum(
        c["volume"] * c["close"] for c in candles_window[-24:]
    )
    l5_pass, l5 = has_liquidity(
        spread=max(approx_spread, 0.01),
        bid_depth=50.0,
        ask_depth=50.0,
        volume_24h=volume_24h,
    )

    # L6 — Risk/Reward
    l6_pass, l6 = check_risk_reward(
        budget=100.0,
        take_profit_pct=tp_pct,
        stop_loss_pct=sl_pct,
    )

    # L7 — News: always skip in backtest
    l7_pass = True
    l7 = {"pass": True, "skipped": True, "total": 0}

    # L8 — Funding Rate (from Redis history)
    rate = get_funding_for_timestamp(symbol, ts_ms)
    if rate is not None:
        funding_data = {
            "ok": True,
            "funding_rate": rate,
            "oi_change_pct": 0.0,   # not available in history
        }
    else:
        funding_data = {"ok": False}
    l8_pass, l8 = check_funding_rate(funding_data)

    # L9 — Fear & Greed (from Redis cache)
    date_str = dt.strftime("%Y-%m-%d")
    fg_val = fg_history.get(date_str)
    if fg_val is not None:
        fg_data = {
            "ok": True,
            "value": fg_val,
            "classification": _fg_class(fg_val),
            "change": 0,
        }
    else:
        fg_data = {"ok": False}
    l9_pass, l9 = check_fear_greed(fg_data)

    # L10 — Buy Pressure (taker_buy_vol from klines)
    total_vol = sum(c["volume"] for c in candles_window[-24:]) or 1
    buy_vol = sum(c["taker_buy_vol"] for c in candles_window[-24:])
    sell_vol = total_vol - buy_vol
    buy_ratio = buy_vol / total_vol * 100
    net_vol = buy_vol - sell_vol
    pressure_data = {
        "ok": True,
        "buy_ratio_pct": buy_ratio,
        "net_btc": net_vol,
        "trend": (
            "bullish" if buy_ratio > 55 else
            "bearish" if buy_ratio < 45 else "neutral"
        ),
    }
    l10_pass, l10 = check_buy_pressure(pressure_data)

    all_pass = all([
        l1_pass, l2_pass, l3_pass, l4_pass, l5_pass,
        l6_pass, l7_pass, l8_pass, l9_pass, l10_pass,
    ])

    snapshot = {
        "l1": l1, "l2": l2, "l3": l3, "l4": l4, "l5": l5,
        "l6": l6, "l7": l7, "l8": l8, "l9": l9, "l10": l10,
        "weekday": dt.strftime("%A"),
        "hour_utc": dt.hour,
        "entry_time": dt.isoformat(),
    }
    return all_pass, snapshot


def _fg_class(val: int) -> str:
    if val <= 24:
        return "Extreme Fear"
    if val <= 44:
        return "Fear"
    if val <= 54:
        return "Neutral"
    if val <= 74:
        return "Greed"
    return "Extreme Greed"


# ── 3. Trade simulation ───────────────────────────────────────────────────────

def _simulate_trade(candles: list, entry_idx: int,
                    tp_pct: float, sl_pct: float) -> dict:
    """
    Simulate a trade starting at candle entry_idx+1 (next open).
    Scans forward up to MAX_HOLD_HOURS candles for TP or SL.
    Returns outcome dict.
    """
    if entry_idx + 1 >= len(candles):
        return {"result": "NO_DATA", "pnl_pct": 0.0,
                "hold_hours": 0, "exit_price": 0.0,
                "exit_time": "", "max_drawdown_pct": 0.0}

    entry_price = candles[entry_idx + 1]["open"]
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)

    max_drawdown = 0.0
    result = "TIMEOUT"
    exit_price = entry_price
    exit_time = ""
    hold_hours = 0

    for j in range(1, MAX_HOLD_HOURS + 1):
        idx = entry_idx + 1 + j
        if idx >= len(candles):
            break
        c = candles[idx]
        hold_hours = j

        # Track max drawdown
        low_drop = (entry_price - c["low"]) / entry_price * 100
        max_drawdown = max(max_drawdown, low_drop)

        # Check SL first (conservative — assumes worst fills first)
        if c["low"] <= sl_price:
            result = "SL_HIT"
            exit_price = sl_price
            exit_time = datetime.fromtimestamp(
                c["open_time_ms"] / 1000, tz=timezone.utc).isoformat()
            break

        if c["high"] >= tp_price:
            result = "TP_HIT"
            exit_price = tp_price
            exit_time = datetime.fromtimestamp(
                c["open_time_ms"] / 1000, tz=timezone.utc).isoformat()
            break

        exit_price = c["close"]
        exit_time = datetime.fromtimestamp(
            c["open_time_ms"] / 1000, tz=timezone.utc).isoformat()

    pnl_pct = (exit_price - entry_price) / entry_price * 100

    return {
        "result":           result,
        "entry_price":      entry_price,
        "exit_price":       exit_price,
        "exit_time":        exit_time,
        "pnl_pct":          round(pnl_pct, 4),
        "hold_hours":       hold_hours,
        "max_drawdown_pct": round(max_drawdown, 4),
    }


# ── 4. Statistics ─────────────────────────────────────────────────────────────

def _calc_stats(trades: list, total_candles: int,
                tp_pct: float, sl_pct: float) -> dict:
    """Compute win rate, P&L, Sharpe, drawdown from trade list."""
    if not trades:
        return {
            "total_signals": 0, "wins": 0, "losses": 0, "timeouts": 0,
            "win_rate_pct": 0.0, "avg_profit_pct": 0.0, "avg_loss_pct": 0.0,
            "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0, "signal_freq": 0.0,
        }

    wins = [t for t in trades if t["result"] == "TP_HIT"]
    losses = [t for t in trades if t["result"] == "SL_HIT"]
    timeouts = [t for t in trades if t["result"] == "TIMEOUT"]

    win_rate = len(wins) / len(trades) * 100
    avg_profit = (
        sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0.0
    )
    avg_loss = (
        sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0.0
    )
    pnls = [t["pnl_pct"] for t in trades]
    total_pnl = sum(pnls)
    max_dd = max((t["max_drawdown_pct"] for t in trades), default=0.0)

    # Sharpe ratio (annualised, assume 1h bars, risk-free = 0)
    n = len(pnls)
    if n > 1:
        mean_r = total_pnl / n
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in pnls) / (n - 1))
        sharpe = (mean_r / std_r * math.sqrt(8760)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    signal_freq = len(trades) / total_candles * 100  # signals per 100 candles

    return {
        "total_signals":   len(trades),
        "wins":            len(wins),
        "losses":          len(losses),
        "timeouts":        len(timeouts),
        "win_rate_pct":    round(win_rate, 1),
        "avg_profit_pct":  round(avg_profit, 3),
        "avg_loss_pct":    round(avg_loss, 3),
        "total_pnl_pct":   round(total_pnl, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio":    round(sharpe, 2),
        "signal_freq":     round(signal_freq, 2),
    }


# ── 5. Main entry point ───────────────────────────────────────────────────────

def run_backtest(
    symbol: str,
    days: int,
    tp_pct: float = 2.0,
    sl_pct: float = 1.0,
    interval: str = "1h",
) -> dict:
    """
    Run full backtest for symbol over given days.
    Saves results to SQLite. Returns summary dict.
    """
    init_db()
    logger.info("Starting backtest: %s %dd TP=%.1f%% SL=%.1f%%",
                symbol, days, tp_pct, sl_pct)

    # Fetch data
    candles = _fetch_candles_full(symbol, days, interval)
    fg_history = _fetch_fear_greed_history()
    funding_history = _fetch_funding_history(symbol)

    total_candles = len(candles) - WARMUP_CANDLES
    trades_raw = []

    # Slide window
    for i in range(WARMUP_CANDLES, len(candles) - 1):
        window = candles[max(0, i - WARMUP_CANDLES):i + 1]
        ts_ms = candles[i]["open_time_ms"]

        signal, snapshot = _eval_bar(
            window, ts_ms, fg_history, funding_history,
            tp_pct, sl_pct, 0.0, symbol,
        )

        if not signal:
            continue

        outcome = _simulate_trade(candles, i, tp_pct, sl_pct)
        if outcome["result"] == "NO_DATA":
            continue

        l1 = snapshot["l1"]
        l2 = snapshot["l2"]
        l3 = snapshot["l3"]
        l8 = snapshot["l8"]
        l9 = snapshot["l9"]
        l10 = snapshot["l10"]

        trade = {
            "symbol":       symbol,
            "entry_time":   snapshot["entry_time"],
            "entry_price":  outcome["entry_price"],
            "weekday":      snapshot["weekday"],
            "hour_utc":     snapshot["hour_utc"],
            # Layer snapshots
            "l1_atr":       l1.get("atr"),
            "l1_adx":       l1.get("adx"),
            "l2_ema50":     l2.get("ema50"),
            "l2_ema200":    l2.get("ema200"),
            "l2_gap_pct":   l2.get("gap_pct"),
            "l3_rsi":       l3.get("rsi"),
            "l3_macd_hist": l3.get("macd_hist"),
            "l4_pass":      1,
            "l5_spread_pct": (
                snapshot["l5"].get("spread", 0) /
                candles[i]["close"] * 100
            ),
            "l6_rr_ratio":  snapshot["l6"].get("rr_ratio"),
            "l8_funding":   l8.get("funding_rate"),
            "l8_oi_chg":    l8.get("oi_change_pct"),
            "l9_fg_value":  l9.get("value"),
            "l10_buy_ratio": l10.get("buy_ratio_pct"),
            "l10_net_vol":  l10.get("net_btc"),
            # Outcome
            "result":       outcome["result"],
            "exit_price":   outcome["exit_price"],
            "exit_time":    outcome["exit_time"],
            "pnl_pct":      outcome["pnl_pct"],
            "hold_hours":   outcome["hold_hours"],
            "max_drawdown_pct": outcome["max_drawdown_pct"],
        }
        trades_raw.append(trade)

    # Compute stats
    stats = _calc_stats(trades_raw, total_candles, tp_pct, sl_pct)

    # Save to SQLite
    run_meta = {
        "symbol":   symbol,
        "interval": interval,
        "days":     days,
        "tp_pct":   tp_pct,
        "sl_pct":   sl_pct,
        "total_candles": total_candles,
        **stats,
    }
    run_id = save_backtest_run(run_meta)
    save_backtest_trades(run_id, trades_raw)

    logger.info(
        "Backtest done: %d signals, WR=%.1f%%, P&L=%.2f%%",
        stats["total_signals"], stats["win_rate_pct"], stats["total_pnl_pct"],
    )

    return {
        "run_id":   run_id,
        "symbol":   symbol,
        "days":     days,
        "tp_pct":   tp_pct,
        "sl_pct":   sl_pct,
        "trades":   trades_raw,
        **stats,
    }
