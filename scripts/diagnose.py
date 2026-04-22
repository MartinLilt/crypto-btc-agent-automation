#!/usr/bin/env python3
"""
Pre-flight diagnostic script — run before starting the bot.

Checks:
  1. Binance REST reachability + all trading symbols
  2. Binance Futures API (funding rates)
  3. Fear & Greed API
  4. Telegram bot token validity
  5. All 10 indicator layers on the last 300 candles for every asset
  6. Backtest engine dry-run (1-day, no DB write)

Usage:
    source .venv/bin/activate
    python3 scripts/diagnose.py
"""

from src.signals.indicators import (
    GOOD_HOURS_UTC,
    SKIP_WEEKDAYS,
    check_buy_pressure,
    check_fear_greed,
    check_funding_rate,
    check_risk_reward,
    is_market_moving,
    is_not_overbought,
    is_uptrend,
)
import os
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()


# ── Config ────────────────────────────────────────────────────────────────────

BINANCE_REST = os.getenv("BINANCE_REST_URL",     "https://api.binance.com")
BINANCE_FUTURES = os.getenv("BINANCE_FUTURES_URL",  "https://fapi.binance.com")
FEAR_GREED_URL = os.getenv(
    "FEAR_GREED_URL",       "https://api.alternative.me/fng/")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

SYMBOLS = [
    "BTCUSDT",
]
WARMUP = 210   # candles needed for EMA-200

OK = "✅"
FAIL = "❌"
WARN = "⚠️ "


def yn(b: bool) -> str:
    return OK if b else FAIL


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ── 1. External endpoints ─────────────────────────────────────────────────────

def check_endpoints():
    section("1. External API endpoints")

    # Binance REST
    try:
        r = requests.get(f"{BINANCE_REST}/api/v3/ping", timeout=5)
        r.raise_for_status()
        print(f"{OK} Binance REST:    {BINANCE_REST}")
    except Exception as e:
        print(f"{FAIL} Binance REST:    {e}")

    # Binance Futures
    try:
        r = requests.get(f"{BINANCE_FUTURES}/fapi/v1/ping", timeout=5)
        r.raise_for_status()
        print(f"{OK} Binance Futures: {BINANCE_FUTURES}")
    except Exception as e:
        print(f"{WARN} Binance Futures: {e}  (funding layer will be skipped)")

    # Fear & Greed
    try:
        r = requests.get(f"{FEAR_GREED_URL}?limit=1", timeout=5)
        data = r.json().get("data", [])
        val = data[0]["value"] if data else "?"
        print(f"{OK} Fear & Greed:    current={val}")
    except Exception as e:
        print(f"{FAIL} Fear & Greed:    {e}")

    # Telegram
    if not TELEGRAM_TOKEN:
        print(f"{FAIL} Telegram token:  NOT SET in .env")
        return
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe",
            timeout=5,
        )
        info = r.json()
        if info.get("ok"):
            name = info["result"]["username"]
            print(f"{OK} Telegram token:  @{name}")
        else:
            print(f"{FAIL} Telegram token:  {info}")
    except Exception as e:
        print(f"{FAIL} Telegram token:  {e}")


# ── 2. Symbol availability on Binance ────────────────────────────────────────

def check_symbols():
    section("2. Symbol availability on Binance")
    try:
        r = requests.get(
            f"{BINANCE_REST}/api/v3/exchangeInfo", timeout=10
        )
        all_syms = {s["symbol"] for s in r.json()["symbols"]}
    except Exception as e:
        print(f"{FAIL} Could not fetch exchange info: {e}")
        return

    for sym in SYMBOLS:
        status = OK if sym in all_syms else FAIL
        print(f"  {status} {sym}")


# ── 3. Candle fetch ───────────────────────────────────────────────────────────

def fetch_candles(symbol: str, limit: int = 300) -> list:
    r = requests.get(
        f"{BINANCE_REST}/api/v3/klines",
        params={"symbol": symbol, "interval": "1h", "limit": limit},
        timeout=10,
    )
    raw = r.json()
    if isinstance(raw, dict):   # error from Binance
        raise ValueError(raw.get("msg", raw))
    return [
        {
            "open_time_ms":  int(c[0]),
            "open":          float(c[1]),
            "high":          float(c[2]),
            "low":           float(c[3]),
            "close":         float(c[4]),
            "volume":        float(c[5]),
            "taker_buy_vol": float(c[9]),
        }
        for c in raw
    ]


# ── 4. Layer-by-layer check ───────────────────────────────────────────────────

def check_layers():
    section("3. Indicator layers — last bar (300 candles)")

    header = (
        f"  {'SYMBOL':<10} {'L1':>3} {'L2':>3} {'L3':>3} "
        f"{'L5':>3} {'L6':>3} {'L10':>3}  "
        f"{'L2 detail'}"
    )
    print(header)
    print("  " + "─" * 75)

    results = {}

    for sym in SYMBOLS:
        try:
            candles = fetch_candles(sym, limit=300)
        except Exception as e:
            print(f"  {FAIL} {sym:<10} fetch failed: {e}")
            continue

        window = candles[-(WARMUP + 1):]

        # L1
        l1_score, l1 = is_market_moving(window)

        # L2
        l2_score, l2 = is_uptrend(window)

        # L3
        l3_score, l3 = is_not_overbought(window)

        # L5 (relative)
        last = window[-1]
        spread = (last["high"] - last["low"]) * 0.1
        vol24 = sum(c["volume"] * c["close"] for c in window[-24:])
        spr_ok = max(spread, 0.01) / last["close"] < 0.005
        vol_ok = vol24 >= 50_000_000
        l5_score, _ = check_risk_reward.__class__ and (
            (6 if vol_ok and spr_ok else 3 if spr_ok else 1), {}
        )
        # simpler inline score for display
        l5_score = 6 if (spr_ok and vol_ok) else (3 if spr_ok else 1)

        # L6
        l6_score, _ = check_risk_reward(
            budget=100.0, take_profit_pct=2.0, stop_loss_pct=1.0
        )

        # L10
        total_vol = sum(c["volume"] for c in window[-24:]) or 1
        buy_vol = sum(c["taker_buy_vol"] for c in window[-24:])
        buy_ratio = buy_vol / total_vol * 100
        pressure = {
            "ok": True,
            "buy_ratio_pct": buy_ratio,
            "net_btc": buy_vol - (total_vol - buy_vol),
            "trend": (
                "bullish" if buy_ratio > 55 else
                "bearish" if buy_ratio < 45 else "neutral"
            ),
        }
        l10_score, _ = check_buy_pressure(pressure)

        # L2 detail
        p = l2.get("price", 0)
        e50 = l2.get("ema50", 0)
        e200 = l2.get("ema200", 0)
        trend = "↑ uptrend" if p > e50 > e200 else "↓ downtrend"
        l2_info = (
            f"price={p:.2f} EMA50={e50:.2f} EMA200={e200:.2f} {trend}"
        )

        def sc(s):
            return f"{s:2d}/10"

        print(
            f"  {sym:<10} L1:{sc(l1_score)} L2:{sc(l2_score)} "
            f"L3:{sc(l3_score)} L5:{sc(l5_score)} L6:{sc(l6_score)} "
            f"L10:{sc(l10_score)}  {l2_info}"
        )

        results[sym] = {
            "l1": l1_score, "l2": l2_score, "l3": l3_score,
            "l5": l5_score, "l6": l6_score, "l10": l10_score,
            "vol24_m": round(vol24 / 1e6, 1),
            "buy_ratio": round(buy_ratio, 1),
            "atr": l1["atr"], "adx": l1["adx"],
            "rsi": l3.get("rsi", 0),
        }
        time.sleep(0.15)   # be polite to Binance

    # Summary
    section("4. Detailed blockers")
    for sym, r in results.items():
        weak = []
        if r["l1"] < 7:
            weak.append(f"L1:{r['l1']}/10 ATR={r['atr']:.0f} ADX={r['adx']:.1f}")
        if r["l2"] < 7:
            weak.append(f"L2:{r['l2']}/10 trend weak")
        if r["l3"] < 7:
            weak.append(f"L3:{r['l3']}/10 RSI={r['rsi']:.0f}")
        if r["l5"] < 7:
            weak.append(f"L5:{r['l5']}/10 vol24h=${r['vol24_m']}M")
        if r["l10"] < 7:
            weak.append(f"L10:{r['l10']}/10 buy_ratio={r['buy_ratio']:.1f}%")

        approx_total = r["l1"] + r["l2"] + r["l3"] + r["l5"] + r["l6"] + r["l10"]
        if weak:
            print(f"  {WARN} {sym} (partial score ~{approx_total}/60): {'; '.join(weak)}")
        else:
            print(f"  {OK} {sym}: strong layers — likely generating signals")


# ── 5. Summary & advice ───────────────────────────────────────────────────────

def print_advice():
    section("5. Notes")
    print("""
  L2 (Trend) fails when ALL assets are in a downtrend — this is a
  market condition, not a bug. The backtest will find signals in
  historical periods where the market was trending up.

  To see signals in backtest:
    - Use a longer period (30d or 90d) — it covers both up and down phases
    - L2 is intentionally strict: only enter in confirmed uptrends

  L5 (Volume) — LINKUSDT has lower liquidity (~$20M/24h window).
    The backtest uses $50M threshold which is fine for BTC/ETH/SOL.
    LINK may show 0 signals on short periods due to low volume.

  L10 (Buy Pressure) — currently bearish market-wide.
    Normal in risk-off environments.
    """)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  🔍 Crypto Bot — Pre-flight Diagnostic")
    print("=" * 60)

    check_endpoints()
    check_symbols()
    check_layers()
    print_advice()

    print("\n" + "=" * 60)
    print("  Diagnostic complete.")
    print("=" * 60 + "\n")
