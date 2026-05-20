"""
Research data layer — fetch + disk-cache OHLCV from Binance public API.

No auth needed for klines. Caches as parquet under research/cache/.
Re-run is cheap: only missing (symbol, interval) pairs are fetched.
"""
from __future__ import annotations

import os
import time
import pathlib

import pandas as pd
import requests

REST = os.getenv("BINANCE_REST_URL", "https://api.binance.com")
CACHE = pathlib.Path(__file__).parent / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

# Wide liquid USDT universe (cross-sectional momentum needs breadth).
# Binance returns [] for pairs that don't exist / weren't listed yet — safe.
UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT",
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
    "ATOMUSDT", "NEARUSDT", "UNIUSDT", "FILUSDT", "ETCUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "TIAUSDT", "SEIUSDT",
    "AAVEUSDT", "RNDRUSDT", "FETUSDT", "TRXUSDT", "MATICUSDT", "ICPUSDT",
]
MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT"]


def _fetch(symbol: str, interval: str, start_ms: int) -> pd.DataFrame:
    url = f"{REST}/api/v3/klines"
    rows: list = []
    cur = start_ms
    while True:
        r = requests.get(url, params={
            "symbol": symbol, "interval": interval,
            "startTime": cur, "limit": 1000,
        }, timeout=15)
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        nxt = batch[-1][0] + 1
        if nxt <= cur or len(batch) < 1000:
            cur = nxt
            if len(batch) < 1000:
                break
        cur = nxt
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "ot", "o", "h", "l", "c", "v", "ct", "qv", "n",
        "tbv", "tqv", "ig"])
    for col in ["o", "h", "l", "c", "v", "qv", "tbv"]:
        df[col] = df[col].astype(float)
    df["dt"] = pd.to_datetime(df["ot"], unit="ms", utc=True)
    df = df[["dt", "ot", "o", "h", "l", "c", "v", "qv", "tbv"]]
    df = df.drop_duplicates("ot").sort_values("ot").reset_index(drop=True)
    return df


def load(symbol: str, interval: str, start: str = "2019-01-01") -> pd.DataFrame:
    """Load (cached) OHLCV. Columns: dt, o,h,l,c,v (volume), qv (quote vol), tbv (taker buy vol)."""
    fp = CACHE / f"{symbol}_{interval}.pkl"
    if fp.exists():
        return pd.read_pickle(fp)
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    df = _fetch(symbol, interval, start_ms)
    if not df.empty:
        df.to_pickle(fp)
    return df


FUTURES = os.getenv("BINANCE_FUTURES_URL", "https://fapi.binance.com")


def load_funding(symbol: str) -> pd.DataFrame:
    """8h funding-rate history for the USDT-perp. Cached. Columns: dt, fr."""
    fp = CACHE / f"{symbol}_funding.pkl"
    if fp.exists():
        return pd.read_pickle(fp)
    rows, cur = [], int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1000)
    while True:
        r = requests.get(f"{FUTURES}/fapi/v1/fundingRate",
                         params={"symbol": symbol, "startTime": cur,
                                 "limit": 1000}, timeout=15)
        if r.status_code != 200:
            break
        b = r.json()
        if not b:
            break
        rows.extend(b)
        nxt = b[-1]["fundingTime"] + 1
        if nxt <= cur or len(b) < 1000:
            break
        cur = nxt
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["fr"] = df["fundingRate"].astype(float)
    df["dt"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df[["dt", "fr"]].drop_duplicates("dt").sort_values("dt").reset_index(drop=True)
    df.to_pickle(fp)
    return df


def refresh(symbol: str, interval: str) -> pd.DataFrame:
    """Append new bars since the last cached one. Safe to call daily."""
    fp = CACHE / f"{symbol}_{interval}.pkl"
    if not fp.exists():
        return load(symbol, interval)
    df = pd.read_pickle(fp)
    last = int(df["ot"].iloc[-1])
    new = _fetch(symbol, interval, last + 1)
    if not new.empty:
        df = (pd.concat([df, new]).drop_duplicates("ot")
              .sort_values("ot").reset_index(drop=True))
        df.to_pickle(fp)
    return df


def refresh_funding(symbol: str) -> pd.DataFrame:
    """Append new 8h funding rows since the last cached one."""
    fp = CACHE / f"{symbol}_funding.pkl"
    if not fp.exists():
        return load_funding(symbol)
    df = pd.read_pickle(fp)
    last_ms = int(df["dt"].iloc[-1].timestamp() * 1000)
    rows, cur = [], last_ms + 1
    while True:
        r = requests.get(f"{FUTURES}/fapi/v1/fundingRate",
                         params={"symbol": symbol, "startTime": cur,
                                 "limit": 1000}, timeout=15)
        if r.status_code != 200:
            break
        b = r.json()
        if not b:
            break
        rows.extend(b)
        nxt = b[-1]["fundingTime"] + 1
        if nxt <= cur or len(b) < 1000:
            break
        cur = nxt
        time.sleep(0.12)
    if rows:
        n = pd.DataFrame(rows)
        n["fr"] = n["fundingRate"].astype(float)
        n["dt"] = pd.to_datetime(n["fundingTime"], unit="ms", utc=True)
        df = (pd.concat([df, n[["dt", "fr"]]]).drop_duplicates("dt")
              .sort_values("dt").reset_index(drop=True))
        df.to_pickle(fp)
    return df


def load_universe(interval: str, symbols=None, start="2019-01-01") -> dict:
    out = {}
    for s in (symbols or UNIVERSE):
        df = load(s, interval, start)
        if not df.empty and len(df) > 200:
            out[s] = df
        print(f"  {s:<10} {interval:>3} {len(df):>6} bars"
              f" {'' if df.empty else df['dt'].iloc[0].date()}"
              f"{'' if df.empty else ' → ' + str(df['dt'].iloc[-1].date())}")
    return out


if __name__ == "__main__":
    import sys
    iv = sys.argv[1] if len(sys.argv) > 1 else "1d"
    syms = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    print(f"=== fetching interval={iv} ===")
    u = load_universe(iv, syms)
    print(f"loaded {len(u)} symbols for {iv}")