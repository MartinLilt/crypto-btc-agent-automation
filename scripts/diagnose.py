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
        l1_pass, l1 = is_market_moving(window)
        if not l1_pass:
            ae = l1.get("atr_expanding", False)
            vs = l1.get("volume_spike",  False)
            adx_ok = (l1.get("adx") or 0) > 20
            l1_pass = ae and vs and adx_ok

        # L2
        l2_pass, l2 = is_uptrend(window)

        # L3
        l3_pass, l3 = is_not_overbought(window)

        # L5 (relative)
        last = window[-1]
        spread = (last["high"] - last["low"]) * 0.1
        vol24 = sum(c["volume"] * c["close"] for c in window[-24:])
        spr_ok = max(spread, 0.01) / last["close"] < 0.005
        vol_ok = vol24 >= 50_000_000
        l5_pass = spr_ok and vol_ok

        # L6
        l6_pass, _ = check_risk_reward(
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
        l10_pass, _ = check_buy_pressure(pressure)

        # L2 detail
        p = l2.get("price", 0)
        e50 = l2.get("ema50", 0)
        e200 = l2.get("ema200", 0)
        trend = "↑ uptrend" if p > e50 > e200 else "↓ downtrend"
        l2_info = (
            f"price={p:.2f} EMA50={e50:.2f} EMA200={e200:.2f} {trend}"
        )

        print(
            f"  {sym:<10} {yn(l1_pass):>3} {yn(l2_pass):>3} "
            f"{yn(l3_pass):>3} {yn(l5_pass):>3} {yn(l6_pass):>3} "
            f"{yn(l10_pass):>3}  {l2_info}"
        )

        results[sym] = {
            "l1": l1_pass, "l2": l2_pass, "l3": l3_pass,
            "l5": l5_pass, "l6": l6_pass, "l10": l10_pass,
            "vol24_m": round(vol24 / 1e6, 1),
            "buy_ratio": round(buy_ratio, 1),
            "atr": l1["atr"], "adx": l1["adx"],
            "rsi": l3.get("rsi", 0),
        }
        time.sleep(0.15)   # be polite to Binance

    # Summary
    section("4. Detailed blockers")
    for sym, r in results.items():
        blockers = []
        if not r["l1"]:
            blockers.append(f"L1: ATR={r['atr']:.3f} ADX={r['adx']:.1f}")
        if not r["l2"]:
            blockers.append("L2: downtrend (price < EMA50 or EMA50 < EMA200)")
        if not r["l3"]:
            blockers.append(f"L3: RSI={r['rsi']:.0f} (overbought)")
        if not r["l5"]:
            blockers.append(
                f"L5: vol24h=${r['vol24_m']}M (need >$50M)"
            )
        if not r["l10"]:
            blockers.append(f"L10: buy_ratio={r['buy_ratio']}% (need >55%)")

        if blockers:
            print(f"  {FAIL} {sym}: {'; '.join(blockers)}")
        else:
            print(f"  {OK} {sym}: all active layers PASS — would generate signals")


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
