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
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from src.data.db import (
    init_db,
    save_backtest_run,
    save_backtest_trades,
)
from src.signals.indicators import (
    check_buy_pressure,
    check_risk_reward,
    is_market_moving,
    is_not_overbought,
    is_uptrend,
    is_volume_trending,
    GOOD_HOURS_UTC,
    SKIP_WEEKDAYS,
    ENTRY_SCORE_THRESHOLD,
    _score_l1,
    _score_l5,
    _score_l10,
)
from src.signals.support_resistance import check_sr_proximity
from src.signals.candle_patterns import detect_candle_patterns

load_dotenv()
logger = logging.getLogger(__name__)

_BINANCE_REST = os.getenv("BINANCE_REST_URL", "https://api.binance.com")

MAX_HOLD_HOURS = 48     # exit timeout if neither TP nor SL hit
WARMUP_CANDLES = 210    # need 200+ for EMA-200

BINANCE_FEE_PCT   = 0.1   # 0.1% per side (taker), 0.2% round-trip
LT_TAX_RATE       = 0.15  # Lithuania 15% capital gains tax (≤€120k/year, 2026)


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
        from src.data.db import cache_get
        return cache_get("fg:history") or {}

    try:
        # No date_format param → API returns unix timestamps (integers)
        url = _FEAR_GREED_URL.split("?")[0] + "?limit=365"
        resp = requests.get(url, timeout=10)
        data = resp.json().get("data", [])
        history = {}
        for item in data:
            raw_ts = item["timestamp"]
            # API returns unix timestamp as string, e.g. "1711497600"
            ts = int(raw_ts)
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
    from src.data.db import cache_get
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


# ── 1b. Weekly EMA21 pre-computation ─────────────────────────────────────────

def _build_weekly_ema21_index(candles: list) -> dict:
    """
    Derive weekly OHLCV from hourly candles and pre-compute EMA21 on weekly closes.
    Returns {hourly_index: ema21_value | None} for every candle index.
    ema21 is the value valid at the START of that hour (i.e. known before the bar).
    """
    if not candles:
        return {}

    # Assign each candle to its week (Mon 00:00 UTC)
    week_closes: dict[tuple, float] = {}   # (year, isoweek) → last close seen
    weekly_ema21_at: list = [None] * len(candles)

    k = 2 / (21 + 1)
    weekly_closes_list: list[float] = []
    current_ema: float | None = None
    current_week: tuple | None = None
    last_ema_at_week_end: float | None = None

    for i, c in enumerate(candles):
        dt = datetime.fromtimestamp(c["open_time_ms"] / 1000, tz=timezone.utc)
        iso = dt.isocalendar()
        week_key = (iso[0], iso[1])

        if week_key != current_week:
            # New week started — lock in previous week's close and update EMA
            if current_week is not None and current_week in week_closes:
                prev_close = week_closes[current_week]
                weekly_closes_list.append(prev_close)
                n = len(weekly_closes_list)
                if n >= 21:
                    if n == 21:
                        current_ema = sum(weekly_closes_list) / 21
                    else:
                        current_ema = prev_close * k + current_ema * (1 - k)
                    last_ema_at_week_end = current_ema
            current_week = week_key

        week_closes[week_key] = c["close"]
        weekly_ema21_at[i] = last_ema_at_week_end

    return weekly_ema21_at


# ── 2. Per-bar layer evaluation ───────────────────────────────────────────────

def _eval_bar(candles_window: list, ts_ms: int,
              tp_pct: float, sl_pct: float,
              spread_approx: float, symbol: str,
              weekly_ema21: float | None = None) -> tuple[bool, dict]:
    """
    Run all layers on a window of candles ending at index i.
    Returns (signal: bool, layer_snapshot: dict)
    """
    # L1 — Volatility (score-based, relaxed ADX floor for backtest)
    l1_score, l1 = is_market_moving(candles_window)
    if l1_score < 4:
        # Re-score with relaxed ADX floor (20 instead of 25)
        adx = l1.get("adx", 0)
        override_score = _score_l1(max(adx, 20.1) if adx >= 20 else adx,
                                   l1.get("atr_expanding", False),
                                   l1.get("volume_spike", False))
        l1_score = max(l1_score, override_score)
        l1["bt_override_score"] = l1_score

    # L2 — Trend (relaxed for backtest: give partial credit for price > EMA50)
    l2_score, l2 = is_uptrend(candles_window)
    if l2_score < 4:
        price_ok = l2.get("price", 0) > l2.get("ema50", 0)
        slope_ok = l2.get("ema50_slope_ok", False)
        if price_ok or slope_ok:
            l2_score = max(l2_score, 4)
            l2["bt_override_score"] = l2_score

    # L3 — Momentum
    l3_score, l3 = is_not_overbought(candles_window)

    # L4 — Volume Spike (real computation from candle window)
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    l4_score, l4 = is_volume_trending(candles_window)

    # L5 — Liquidity (BTC: $30M/24h floor, score-based)
    last = candles_window[-1]
    approx_spread = (last["high"] - last["low"]) * 0.1
    volume_24h = sum(c["volume"] * c["close"] for c in candles_window[-24:])
    spread_ok = max(approx_spread, 0.01) / last["close"] < 0.005
    vol_ok = volume_24h >= 30_000_000
    l5_score = _score_l5(approx_spread, spread_ok and vol_ok, volume_24h)
    l5 = {
        "score":           l5_score,
        "pass":            l5_score >= 7,
        "spread":          round(approx_spread, 6),
        "spread_ok":       spread_ok,
        "volume_24h_usd":  round(volume_24h, 2),
        "volume_ok":       vol_ok,
        "min_volume_usd":  30_000_000,
    }

    # L6 — Risk/Reward (with ATR validation)
    l6_score, l6 = check_risk_reward(
        budget=100.0,
        take_profit_pct=tp_pct,
        stop_loss_pct=sl_pct,
        atr=l1.get("atr"),
        price=candles_window[-1]["close"],
    )

    # L7 — News: neutral in backtest
    l7_score = 5
    l7 = {"score": l7_score, "pass": True, "skipped": True, "total": 0}

    # L8 — S/R Proximity (computed from candle window — no external data needed)
    l8_score, l8 = check_sr_proximity(candles_window, tp_pct=tp_pct)

    # L9 — Candle Pattern (last 3 candles of window)
    l9_score, l9 = detect_candle_patterns(candles_window)

    # L10 — Buy Pressure (6h lookback — more reactive than 24h)
    total_vol = sum(c["volume"] for c in candles_window[-6:]) or 1
    buy_vol = sum(c["taker_buy_vol"] for c in candles_window[-6:])
    buy_ratio = buy_vol / total_vol * 100
    net_vol = buy_vol - (total_vol - buy_vol)
    pressure_data = {
        "ok": True,
        "buy_ratio_pct": buy_ratio,
        "net_btc": net_vol,
        "trend": (
            "bullish" if buy_ratio > 55 else
            "bearish" if buy_ratio < 45 else "neutral"
        ),
    }
    l10_score, l10 = check_buy_pressure(pressure_data)

    total_score = (l1_score + l2_score + l3_score + l4_score + l5_score +
                   l6_score + l7_score + l8_score + l9_score + l10_score)

    # Hard filter: RSI > 65 blocks entry (backtest verified: avg loss RSI = 71.9)
    rsi_block = l3.get("rsi", 0) > 65

    # Hard filter: ADX danger zone 25-40 (backtest data: WR=5-33% vs 54% outside)
    adx_val = l1.get("adx", 0)
    adx_block = 25 <= adx_val < 40

    # Hard filter: weekly EMA21 — skip entries in macro bear regime
    weekly_block = (
        weekly_ema21 is not None
        and candles_window[-1]["close"] < weekly_ema21
    )

    all_pass = (total_score >= ENTRY_SCORE_THRESHOLD) and not rsi_block and not adx_block and not weekly_block

    snapshot = {
        "l1": l1, "l2": l2, "l3": l3, "l4": l4, "l5": l5,
        "l6": l6, "l7": l7, "l8": l8, "l9": l9, "l10": l10,
        "total_score": total_score,
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
    fee_pct = BINANCE_FEE_PCT * 2              # entry + exit
    pnl_pct_net_fees = pnl_pct - fee_pct      # after Binance fees
    # Per-trade tax NOT applied here — LT tax is on net annual profit (see _calc_stats)

    return {
        "result":           result,
        "entry_price":      entry_price,
        "exit_price":       exit_price,
        "exit_time":        exit_time,
        "pnl_pct":          round(pnl_pct, 4),
        "pnl_pct_net_fees": round(pnl_pct_net_fees, 4),
        "fee_pct":          round(fee_pct, 4),
        "hold_hours":       hold_hours,
        "max_drawdown_pct": round(max_drawdown, 4),
    }


# ── 4. Statistics ─────────────────────────────────────────────────────────────

def _calc_stats(trades: list, total_candles: int,
                tp_pct: float, sl_pct: float) -> dict:
    """Compute win rate, P&L (gross / net-fees / after-tax), Sharpe, drawdown."""
    if not trades:
        return {
            "total_signals": 0, "wins": 0, "losses": 0, "timeouts": 0,
            "win_rate_pct": 0.0, "avg_profit_pct": 0.0, "avg_loss_pct": 0.0,
            "total_pnl_pct": 0.0, "total_pnl_net_fees_pct": 0.0,
            "total_pnl_after_tax_pct": 0.0,
            "max_drawdown_pct": 0.0, "sharpe_ratio": 0.0, "signal_freq": 0.0,
            "breakeven_wr_fees": 0.0, "lt_tax_pct": 0.0,
        }

    wins     = [t for t in trades if t["result"] == "TP_HIT"]
    losses   = [t for t in trades if t["result"] == "SL_HIT"]
    timeouts = [t for t in trades if t["result"] == "TIMEOUT"]

    win_rate   = len(wins) / len(trades) * 100
    avg_profit = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0.0
    avg_loss   = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0.0

    pnls          = [t["pnl_pct"] for t in trades]
    pnls_net_fees = [t["pnl_pct_net_fees"] for t in trades]

    total_pnl          = sum(pnls)
    total_pnl_net_fees = sum(pnls_net_fees)

    # LT law: 15% tax on NET annual profit (losses offset gains within the year)
    lt_tax = max(0.0, total_pnl_net_fees) * LT_TAX_RATE
    total_pnl_after_tax = total_pnl_net_fees - lt_tax

    max_dd = max((t["max_drawdown_pct"] for t in trades), default=0.0)

    # Sharpe ratio (annualised, risk-free = 0)
    n = len(pnls_net_fees)
    if n > 1:
        mean_r = total_pnl_net_fees / n
        std_r  = math.sqrt(sum((r - mean_r) ** 2 for r in pnls_net_fees) / (n - 1))
        sharpe = (mean_r / std_r * math.sqrt(8760)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    signal_freq = len(trades) / total_candles * 100

    # Break-even WR after Binance fees
    fee = BINANCE_FEE_PCT * 2
    win_net_fees  = tp_pct - fee
    loss_net_fees = sl_pct + fee
    be_fees = loss_net_fees / (win_net_fees + loss_net_fees) * 100

    return {
        "total_signals":           len(trades),
        "wins":                    len(wins),
        "losses":                  len(losses),
        "timeouts":                len(timeouts),
        "win_rate_pct":            round(win_rate, 1),
        "avg_profit_pct":          round(avg_profit, 3),
        "avg_loss_pct":            round(avg_loss, 3),
        "total_pnl_pct":           round(total_pnl, 2),
        "total_pnl_net_fees_pct":  round(total_pnl_net_fees, 2),
        "total_pnl_after_tax_pct": round(total_pnl_after_tax, 2),
        "lt_tax_pct":              round(lt_tax, 2),
        "max_drawdown_pct":        round(max_dd, 2),
        "sharpe_ratio":            round(sharpe, 2),
        "signal_freq":             round(signal_freq, 2),
        "breakeven_wr_fees":       round(be_fees, 1),
    }


# ── 5. Shared simulation loop ─────────────────────────────────────────────────

RESEARCH_TP_SL = [
    (1.5, 0.75),
    (2.0, 1.0),
    (2.5, 1.0),
    (3.0, 1.5),
]
RESEARCH_PERIODS = [90, 180, 365]


def _run_window_loop(
    symbol: str,
    candles: list,
    weekly_ema21_index: list,
    tp_pct: float,
    sl_pct: float,
) -> tuple[list, int]:
    """
    Slide the signal window over candles and simulate trades.
    Returns (trades_raw, total_candles_evaluated).
    Shared by run_backtest and run_backtest_research.
    """
    total_candles = len(candles) - WARMUP_CANDLES
    trades_raw = []

    for i in range(WARMUP_CANDLES, len(candles) - 1):
        window = candles[max(0, i - WARMUP_CANDLES):i + 1]
        ts_ms  = candles[i]["open_time_ms"]

        signal, snapshot = _eval_bar(
            window, ts_ms, tp_pct, sl_pct, 0.0, symbol,
            weekly_ema21=weekly_ema21_index[i],
        )
        if not signal:
            continue

        outcome = _simulate_trade(candles, i, tp_pct, sl_pct)
        if outcome["result"] == "NO_DATA":
            continue

        l1  = snapshot["l1"];  l2  = snapshot["l2"];  l3 = snapshot["l3"]
        l8  = snapshot["l8"];  l9  = snapshot["l9"];  l10 = snapshot["l10"]

        trades_raw.append({
            "symbol":        symbol,
            "entry_time":    snapshot["entry_time"],
            "entry_price":   outcome["entry_price"],
            "weekday":       snapshot["weekday"],
            "hour_utc":      snapshot["hour_utc"],
            "l1_atr":        l1.get("atr"),
            "l1_adx":        l1.get("adx"),
            "l2_ema50":      l2.get("ema50"),
            "l2_ema200":     l2.get("ema200"),
            "l2_gap_pct":    l2.get("gap_pct"),
            "l3_rsi":        l3.get("rsi"),
            "l3_macd_hist":  l3.get("macd_hist"),
            "l4_pass":       1,
            "l5_spread_pct": snapshot["l5"].get("spread", 0) / candles[i]["close"] * 100,
            "l6_rr_ratio":   snapshot["l6"].get("rr_ratio"),
            "l8_funding":    l8.get("score"),
            "l8_oi_chg":     l8.get("n_blockers", 0),
            "l9_fg_value":   l9.get("score"),
            "l10_buy_ratio": l10.get("buy_ratio_pct"),
            "l10_net_vol":   l10.get("net_btc"),
            "result":              outcome["result"],
            "exit_price":          outcome["exit_price"],
            "exit_time":           outcome["exit_time"],
            "pnl_pct":             outcome["pnl_pct"],
            "pnl_pct_net_fees":    outcome["pnl_pct_net_fees"],
            "hold_hours":          outcome["hold_hours"],
            "max_drawdown_pct":    outcome["max_drawdown_pct"],
            "total_score":         snapshot["total_score"],
        })

    return trades_raw, total_candles


# ── 6. Main entry point ───────────────────────────────────────────────────────

def run_backtest(
    symbol: str,
    days: int,
    tp_pct: float = 2.0,
    sl_pct: float = 1.0,
    interval: str = "1h",
    save_db: bool = True,
) -> dict:
    """
    Run full backtest for symbol over given days.
    Saves results to SQLite (unless save_db=False). Returns summary dict.
    """
    init_db()
    logger.info("Starting backtest: %s %dd TP=%.1f%% SL=%.1f%%",
                symbol, days, tp_pct, sl_pct)

    candles = _fetch_candles_full(symbol, days, interval)
    weekly_ema21_index = _build_weekly_ema21_index(candles)
    trades_raw, total_candles = _run_window_loop(
        symbol, candles, weekly_ema21_index, tp_pct, sl_pct)

    # Compute stats
    stats = _calc_stats(trades_raw, total_candles, tp_pct, sl_pct)

    # Save to SQLite (skipped when save_db=False, e.g. diagnostic runs)
    run_id = 0
    if save_db:
        run_meta = {
            "symbol":        symbol,
            "interval":      interval,
            "days":          days,
            "tp_pct":        tp_pct,
            "sl_pct":        sl_pct,
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
        "run_id": run_id,
        "symbol": symbol,
        "days":   days,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "trades": trades_raw,
        "stats":  stats,
        **stats,
    }


# ── 7. Research: grid search over TP/SL × periods ────────────────────────────

def run_backtest_research(
    symbol: str,
    interval: str = "1h",
) -> list[dict]:
    """
    Run all RESEARCH_TP_SL × RESEARCH_PERIODS combinations automatically.
    Fetches candles once for the longest period, slices for shorter ones.
    Returns list of result dicts sorted by Sharpe ratio descending.
    Budget is NOT a parameter — results are in % so callers can project any budget.
    """
    init_db()
    max_days = max(RESEARCH_PERIODS)
    logger.info("Research: fetching %dd candles for %s", max_days, symbol)
    candles_full = _fetch_candles_full(symbol, max_days, interval)
    weekly_full  = _build_weekly_ema21_index(candles_full)

    results = []
    for days in RESEARCH_PERIODS:
        needed   = days * 24 + WARMUP_CANDLES
        candles  = candles_full[-needed:] if len(candles_full) >= needed else candles_full
        weekly   = weekly_full[-len(candles):]

        for tp_pct, sl_pct in RESEARCH_TP_SL:
            trades_raw, total_candles = _run_window_loop(
                symbol, candles, weekly, tp_pct, sl_pct)
            stats     = _calc_stats(trades_raw, total_candles, tp_pct, sl_pct)
            date_from = trades_raw[0]["entry_time"][:10]  if trades_raw else "—"
            date_to   = trades_raw[-1]["entry_time"][:10] if trades_raw else "—"

            results.append({
                "symbol":    symbol,
                "days":      days,
                "tp_pct":    tp_pct,
                "sl_pct":    sl_pct,
                "date_from": date_from,
                "date_to":   date_to,
                **stats,
            })
            logger.info(
                "Research %s TP=%.1f SL=%.2f %dd → WR=%.1f%% net_pct=%.2f%%",
                symbol, tp_pct, sl_pct, days,
                stats["win_rate_pct"], stats["total_pnl_after_tax_pct"],
            )

    results.sort(key=lambda r: r["sharpe_ratio"], reverse=True)
    return results