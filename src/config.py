"""Configuration loaded from the local .env file.

Market data is public, so keys may be empty in paper mode; they are only
required when TRADING_MODE=live. Everything is read once at import time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env sitting at the project root (one level above src/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

PROJECT_ROOT = _PROJECT_ROOT

# Where runtime state lives. Local: <project>/state. On Railway: set STATE_DIR to
# a mounted volume path (e.g. /data) so grid state survives redeploys & cron runs.
STATE_DIR = Path(os.getenv("STATE_DIR", str(_PROJECT_ROOT / "state")))


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    trading_mode: str          # "paper" | "live"
    testnet: bool
    symbol: str
    quote_asset: str           # quote currency (USDC in the EU; USDT elsewhere)
    interval: str
    candle_limit: int
    target_coins: tuple[str, ...]  # static fallback universe
    auto_universe: bool            # build the universe live from top-volume coins
    universe_size: int             # how many coins in the dynamic universe
    min_quote_volume: float        # min 24h quote volume (liquidity filter), USDT
    auto_target: bool              # let the screener override SYMBOL
    target_screen_interval: str    # timeframe used to JUDGE coins (e.g. 1d)
    target_screen_limit: int       # candles per coin during screening
    strategy: str              # "sma" | "ema" | "rsi"
    auto_strategy: bool        # auto-pick the best strategy for the target coin
    sma_fast: int
    sma_slow: int
    ema_fast: int
    ema_slow: int
    rsi_period: int
    rsi_oversold: float
    rsi_overbought: float
    breakout_period: int
    regime_interval: str       # timeframe the regime is measured on (e.g. 1d)
    regime_ma: int
    regime_slope_lookback: int
    regime_flat_pct: float
    regime_gate: bool
    quote_order_qty: float
    fee_rate: float
    tax_rate: float
    tax_allowance: float
    paper_start_balance: float
    take_profit_pct: float     # exit at +X% (0 = off)
    stop_loss_pct: float       # exit at -X% (0 = off)
    trailing_stop_pct: float   # exit X% below the peak since entry (0 = off)
    max_hold_bars: int         # force exit after N bars (0 = off)
    grid_tp_pct: float         # micro take-profit per bag (the stream)
    grid_step_pct: float       # add a bag when price drops this % below lowest bag
    grid_unit_usdt: float      # USDT per bag
    grid_max_bags: int         # max concurrent bags per coin
    grid_sma: int              # uptrend filter window (no new bags below it)
    regime_adaptive: bool      # switch grid behaviour by market regime
    bull_hold_pct: float       # capital fraction to buy-&-hold per coin in BULL
    telegram_bot_token: str    # Telegram bot token (results output); empty = off
    telegram_chat_id: str      # chat to send results to
    database_url: str          # Postgres DSN; empty = JSON-file fallback


config = Config(
    api_key=os.getenv("BINANCE_API_KEY", "").strip(),
    api_secret=os.getenv("BINANCE_API_SECRET", "").strip(),
    trading_mode=os.getenv("TRADING_MODE", "paper").strip().lower(),
    testnet=_bool("BINANCE_TESTNET", False),
    symbol=os.getenv("SYMBOL", "LTCUSDC").strip().upper(),
    quote_asset=os.getenv("QUOTE_ASSET", "USDC").strip().upper(),
    interval=os.getenv("INTERVAL", "1h").strip(),
    candle_limit=int(os.getenv("CANDLE_LIMIT", "200")),
    target_coins=tuple(
        c.strip().upper()
        for c in os.getenv(
            "TARGET_COINS",
            "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,"
            "LTCUSDT,DOGEUSDT,LINKUSDT,AVAXUSDT",
        ).split(",")
        if c.strip()
    ),
    auto_universe=_bool("AUTO_UNIVERSE", False),
    universe_size=int(os.getenv("UNIVERSE_SIZE", "30")),
    min_quote_volume=float(os.getenv("MIN_QUOTE_VOLUME", "10000000")),
    auto_target=_bool("AUTO_TARGET", False),
    target_screen_interval=os.getenv("TARGET_SCREEN_INTERVAL", "1d").strip(),
    target_screen_limit=int(os.getenv("TARGET_SCREEN_LIMIT", "365")),
    strategy=os.getenv("STRATEGY", "sma").strip().lower(),
    auto_strategy=_bool("AUTO_STRATEGY", False),
    sma_fast=int(os.getenv("SMA_FAST", "20")),
    sma_slow=int(os.getenv("SMA_SLOW", "50")),
    ema_fast=int(os.getenv("EMA_FAST", "12")),
    ema_slow=int(os.getenv("EMA_SLOW", "26")),
    rsi_period=int(os.getenv("RSI_PERIOD", "14")),
    rsi_oversold=float(os.getenv("RSI_OVERSOLD", "30")),
    rsi_overbought=float(os.getenv("RSI_OVERBOUGHT", "70")),
    breakout_period=int(os.getenv("BREAKOUT_PERIOD", "20")),
    regime_interval=os.getenv("REGIME_INTERVAL", "1d").strip(),
    regime_ma=int(os.getenv("REGIME_MA", "50")),
    regime_slope_lookback=int(os.getenv("REGIME_SLOPE_LOOKBACK", "10")),
    regime_flat_pct=float(os.getenv("REGIME_FLAT_PCT", "2.0")),
    regime_gate=_bool("REGIME_GATE", True),
    quote_order_qty=float(os.getenv("QUOTE_ORDER_QTY", "20")),
    fee_rate=float(os.getenv("FEE_RATE", "0.001")),
    tax_rate=float(os.getenv("TAX_RATE", "0.15")),
    tax_allowance=float(os.getenv("TAX_ALLOWANCE", "500")),
    paper_start_balance=float(os.getenv("PAPER_START_BALANCE", "1000")),
    take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0")),
    stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0")),
    trailing_stop_pct=float(os.getenv("TRAILING_STOP_PCT", "0")),
    max_hold_bars=int(os.getenv("MAX_HOLD_BARS", "0")),
    grid_tp_pct=float(os.getenv("GRID_TP_PCT", "0.8")),
    grid_step_pct=float(os.getenv("GRID_STEP_PCT", "1.5")),
    grid_unit_usdt=float(os.getenv("GRID_UNIT_USDT", "40")),
    grid_max_bags=int(os.getenv("GRID_MAX_BAGS", "8")),
    grid_sma=int(os.getenv("GRID_SMA", "100")),
    regime_adaptive=_bool("REGIME_ADAPTIVE", True),
    bull_hold_pct=float(os.getenv("BULL_HOLD_PCT", "0.15")),
    telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
    telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    database_url=os.getenv("DATABASE_URL", "").strip(),
)