#!/usr/bin/env python3
"""
Paper-trading historical replay.

Mirrors live paper trader logic (portfolio capital, BOTH direction, 3 assets)
but walks through historical 1h candles chronologically instead of fetching live.
Produces a full PnL report + buy-and-hold benchmark over the same window.

Usage:
    python -m scripts.paper_replay
    python -m scripts.paper_replay --days 720 --capital 10000 --per-trade 1000
    python -m scripts.paper_replay --direction BOTH --tp 3.0 --sl 1.5
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import (
    _eval_bar,
    _eval_bar_short,
    _fetch_candles_full,
    _slice_higher_tf_at,
    MAX_HOLD_HOURS,
    WARMUP_CANDLES,
)
from src.backtest import engine as _eng  # for monkey-patch (it imports threshold by name)
from src.data import db as _db_mod  # for monkey-patch checks
from src.data.db import SQLITE_PATH
from src.signals import indicators as _ind

BINANCE_TAKER_FEE = 0.001  # 0.1% per side
LT_TAX_RATE = 0.15

ASSETS = ["BTCUSDT", "SOLUSDT", "ETHUSDT"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("replay")


# ─── Position tracking ────────────────────────────────────────────────────────


def _position_size_for_score(base_size: float, score: int, threshold: int) -> float:
    """
    Scale position size by signal strength above the per-asset threshold.
    Margin = score - threshold:
      < 5  → 0.5× base (low-conviction, barely cleared the bar)
      5-14 → 1.0× base (baseline)
      15-24 → 1.5× base (strong)
      ≥ 25 → 2.0× base (very high conviction)
    """
    margin = score - threshold
    if margin < 5:
        return base_size * 0.5
    if margin < 15:
        return base_size * 1.0
    if margin < 25:
        return base_size * 1.5
    return base_size * 2.0


def _update_dynamic_sl(pos: dict, bar: dict, hours_held: int,
                       be_after_hours: int, be_trigger_pct: float,
                       be_offset_pct: float, trail_pct: float | None) -> None:
    """
    Mutate pos in place: move SL to break-even (or trail behind peak) when
    conditions met. Wide initial SL lets the trade breathe early; after the
    break-even trigger fires, SL never moves backwards.
    """
    pos["max_high"] = max(pos.get("max_high", pos["entry_price"]), bar["high"])
    pos["min_low"] = min(pos.get("min_low", pos["entry_price"]), bar["low"])

    if pos["direction"] == "LONG":
        if not pos.get("be_triggered") and hours_held >= be_after_hours:
            trigger_price = pos["entry_price"] * (1 + be_trigger_pct / 100)
            if pos["max_high"] >= trigger_price:
                new_sl = pos["entry_price"] * (1 + be_offset_pct / 100)
                if new_sl > pos["sl_price"]:
                    pos["sl_price"] = new_sl
                    pos["be_triggered"] = True
        if trail_pct is not None and pos.get("be_triggered"):
            trail_sl = pos["max_high"] * (1 - trail_pct / 100)
            if trail_sl > pos["sl_price"]:
                pos["sl_price"] = trail_sl
    else:  # SHORT
        if not pos.get("be_triggered") and hours_held >= be_after_hours:
            trigger_price = pos["entry_price"] * (1 - be_trigger_pct / 100)
            if pos["min_low"] <= trigger_price:
                new_sl = pos["entry_price"] * (1 - be_offset_pct / 100)
                if new_sl < pos["sl_price"]:
                    pos["sl_price"] = new_sl
                    pos["be_triggered"] = True
        if trail_pct is not None and pos.get("be_triggered"):
            trail_sl = pos["min_low"] * (1 + trail_pct / 100)
            if trail_sl < pos["sl_price"]:
                pos["sl_price"] = trail_sl


def _check_position(pos: dict, bar: dict, hours_held: int,
                    max_hold_hours: int = MAX_HOLD_HOURS,
                    funding_rate_per_8h: float = 0.0) -> dict | None:
    """
    Check if `bar` closes the position. Returns close payload or None.
    SHORT inverts TP/SL geometry and PnL sign. SL checked before TP (conservative).
    Liquidation: if leverage > 1 and adverse move breaches (1/L - 0.005) ×100%
    from entry, position closes at the liquidation price (collateral wiped).
    Funding cost is debited on close for the actual hold duration.
    """
    direction = pos["direction"]
    entry = pos["entry_price"]
    tp_price = pos["tp_price"]
    sl_price = pos["sl_price"]
    leverage = pos.get("leverage", 1.0)

    # Liquidation distance with 0.5% maintenance margin buffer
    liq_price = None
    if leverage > 1:
        liq_distance = max(0.0, (1.0 / leverage - 0.005))
        if direction == "LONG":
            liq_price = entry * (1 - liq_distance)
        else:
            liq_price = entry * (1 + liq_distance)

    status = None
    exit_price = None

    # Liquidation checked first — worst outcome
    if liq_price is not None:
        if direction == "LONG" and bar["low"] <= liq_price:
            status, exit_price = "LIQUIDATED", liq_price
        elif direction == "SHORT" and bar["high"] >= liq_price:
            status, exit_price = "LIQUIDATED", liq_price

    if status is None:
        if direction == "LONG":
            if bar["low"] <= sl_price:
                status, exit_price = "SL_HIT", sl_price
            elif bar["high"] >= tp_price:
                status, exit_price = "TP_HIT", tp_price
        else:  # SHORT
            if bar["high"] >= sl_price:
                status, exit_price = "SL_HIT", sl_price
            elif bar["low"] <= tp_price:
                status, exit_price = "TP_HIT", tp_price

    if status is None and hours_held >= max_hold_hours:
        status = "TIMEOUT"
        exit_price = bar["close"]

    if status is None:
        return None

    qty = pos["qty"]  # already reflects leverage
    entry_fee = pos["entry_fee"]
    exit_notional = qty * exit_price
    exit_fee = exit_notional * BINANCE_TAKER_FEE

    if direction == "SHORT":
        gross = qty * (entry - exit_price)
    else:
        gross = qty * (exit_price - entry)

    # Funding cost — paid every 8h on perp notional. Same sign for LONG/SHORT
    # to model worst-case drag (real-world: shorts often *receive* funding).
    funding_cost = qty * entry * funding_rate_per_8h * (hours_held / 8.0)

    pnl_usd = gross - entry_fee - exit_fee - funding_cost

    # Cap loss at collateral (extra safety; liquidation should already cap)
    collateral = pos.get("position_size") or 0
    if pnl_usd < -collateral:
        pnl_usd = -collateral

    pnl_pct = (exit_price - entry) / entry * 100
    if direction == "SHORT":
        pnl_pct = -pnl_pct
    fee_pct = BINANCE_TAKER_FEE * 2 * 100

    return {
        "status": status,
        "exit_price": exit_price,
        "exit_ts": bar["open_time_ms"],
        "exit_fee": exit_fee,
        "funding_cost": funding_cost,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "pnl_pct_net_fees": pnl_pct - fee_pct,
        "hold_hours": hours_held,
    }


# ─── Main replay ──────────────────────────────────────────────────────────────


def run_replay(days: int, capital: float, per_trade: float,
               direction: str, tp_pct: float, sl_pct: float,
               assets: list[str],
               max_hold_hours: int = MAX_HOLD_HOURS,
               min_rr_override: float | None = None,
               max_concurrent_per_leg: int = 1,
               dynamic_sl: bool = False,
               be_after_hours: int = 6,
               be_trigger_pct: float = 1.0,
               be_offset_pct: float = 0.2,
               trail_pct: float | None = None,
               interval: str = "1h",
               score_threshold_override: float | None = None,
               score_weighted_sizing: bool = False,
               asset_weights: dict[str, float] | None = None,
               leverage: float = 1.0,
               funding_rate_per_8h: float = 0.0001) -> dict:
    """Replay paper-trader over `days` of history. Returns full result dict.

    max_hold_hours: max bars to hold before forced TIMEOUT exit
    min_rr_override: if set, monkey-patches indicators.MIN_RR_RATIO for the run
    max_concurrent_per_leg: how many concurrent positions allowed per (symbol, direction)
    """
    direction = direction.upper()
    assert direction in ("LONG", "SHORT", "BOTH")
    dirs_to_scan = ["LONG", "SHORT"] if direction == "BOTH" else [direction]

    # Optional RR relaxation for high-WR / low-RR strategy experiments.
    # _score_l6 reads MIN_RR_RATIO at call time, so a module-level assign works.
    original_min_rr = _ind.MIN_RR_RATIO
    if min_rr_override is not None:
        _ind.MIN_RR_RATIO = min_rr_override
        log.info("MIN_RR_RATIO temporarily relaxed: %.2f → %.2f",
                 original_min_rr, min_rr_override)

    # Optional ENTRY_SCORE_THRESHOLD override for score-curve sweeps.
    # We need to force ALL assets to the override value, so patch:
    #   - the legacy bare constants in indicators + engine (back-compat fallback)
    #   - the per-asset dict in indicators (current production path)
    original_threshold = _ind.ENTRY_SCORE_THRESHOLD
    original_thresholds_dict = dict(_ind.ENTRY_SCORE_THRESHOLDS)
    original_default = _ind.ENTRY_SCORE_THRESHOLD_DEFAULT
    if score_threshold_override is not None:
        _ind.ENTRY_SCORE_THRESHOLD = score_threshold_override
        _ind.ENTRY_SCORE_THRESHOLD_DEFAULT = score_threshold_override
        _eng.ENTRY_SCORE_THRESHOLD = score_threshold_override
        for k in list(_ind.ENTRY_SCORE_THRESHOLDS.keys()):
            _ind.ENTRY_SCORE_THRESHOLDS[k] = score_threshold_override
        log.info("ENTRY_SCORE_THRESHOLD (all assets) temporarily set → %s",
                 score_threshold_override)

    # Bar duration in milliseconds (for hours_held / per-bar logic)
    _bar_ms = {"15m": 15*60*1000, "30m": 30*60*1000, "1h": 3600*1000,
               "2h": 2*3600*1000, "4h": 4*3600*1000}.get(interval, 3600*1000)
    bar_hours = _bar_ms / (3600*1000)

    # Normalise asset weights so an equal split → multiplier 1.0
    # (i.e. raw weights are shares; effective multiplier = share × len(assets))
    if asset_weights:
        total_weight = sum(asset_weights.get(a, 0) for a in assets)
        if total_weight > 0:
            asset_size_mult = {
                a: asset_weights.get(a, 0) / total_weight * len(assets)
                for a in assets
            }
            log.info("Asset size multipliers: %s",
                     {a: f"{m:.2f}×" for a, m in asset_size_mult.items()})
        else:
            asset_size_mult = {a: 1.0 for a in assets}
    else:
        asset_size_mult = {a: 1.0 for a in assets}

    log.info("Fetching candle history for %d days × %d assets (base=%s + 4h/1d/1w)...",
             days, len(assets), interval)
    t0 = time.time()
    candles_1h: dict[str, list] = {}  # name kept for compat; holds the BASE timeframe
    candles_4h: dict[str, list] = {}
    candles_1d: dict[str, list] = {}
    candles_1w: dict[str, list] = {}
    for sym in assets:
        candles_1h[sym] = _fetch_candles_full(sym, days=days, interval=interval)
        candles_4h[sym] = _fetch_candles_full(sym, days=days, interval="4h")
        candles_1d[sym] = _fetch_candles_full(sym, days=days + 60, interval="1d")
        candles_1w[sym] = _fetch_candles_full(sym, days=days + 240, interval="1w")
        log.info("  %s: %d × %s, %d × 4h, %d × 1d, %d × 1w",
                 sym, len(candles_1h[sym]), interval, len(candles_4h[sym]),
                 len(candles_1d[sym]), len(candles_1w[sym]))
    log.info("Fetch complete in %.1fs", time.time() - t0)

    # Build ts→idx map per asset for fast lookup
    idx_by_ts: dict[str, dict[int, int]] = {
        sym: {c["open_time_ms"]: i for i, c in enumerate(candles_1h[sym])}
        for sym in assets
    }

    # Master timeline: union of all 1h timestamps, sorted
    all_ts = sorted({c["open_time_ms"] for sym in assets for c in candles_1h[sym]})
    # Skip first WARMUP_CANDLES bars (need history for indicators)
    earliest_valid = max(candles_1h[sym][WARMUP_CANDLES]["open_time_ms"] for sym in assets)
    timeline = [ts for ts in all_ts if ts >= earliest_valid]

    log.info("Timeline: %d hourly bars from %s to %s",
             len(timeline),
             datetime.fromtimestamp(timeline[0] / 1000, tz=timezone.utc).date(),
             datetime.fromtimestamp(timeline[-1] / 1000, tz=timezone.utc).date())

    # Portfolio state
    free_capital = capital
    open_positions: list[dict] = []
    closed_trades: list[dict] = []
    skipped_for_capital = 0
    skipped_for_open = 0
    signals_evaluated = 0
    signals_fired = 0
    equity_curve: list[tuple[int, float]] = []

    last_log_pct = -1
    total_steps = len(timeline)

    for step, ts_ms in enumerate(timeline):
        # Progress logging
        pct = int(step / total_steps * 100)
        if pct != last_log_pct and pct % 10 == 0:
            log.info("Replay %d%% (%d/%d) — open=%d closed=%d free=$%.0f",
                     pct, step, total_steps, len(open_positions),
                     len(closed_trades), free_capital)
            last_log_pct = pct

        # 1. Update open positions
        still_open: list[dict] = []
        for pos in open_positions:
            sym = pos["symbol"]
            i = idx_by_ts[sym].get(ts_ms)
            if i is None:
                still_open.append(pos)
                continue
            bar = candles_1h[sym][i]
            hours_held = (ts_ms - pos["entry_ts"]) // (3600 * 1000)
            if (ts_ms - pos["entry_ts"]) < _bar_ms:
                still_open.append(pos)
                continue
            if dynamic_sl:
                _update_dynamic_sl(pos, bar, hours_held,
                                   be_after_hours, be_trigger_pct,
                                   be_offset_pct, trail_pct)
            outcome = _check_position(pos, bar, hours_held, max_hold_hours,
                                      funding_rate_per_8h=funding_rate_per_8h)
            if outcome:
                free_capital += pos["position_size"] + outcome["pnl_usd"]
                closed_trades.append({**pos, **outcome})
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. Look for new signals (per asset × direction)
        for sym in assets:
            i = idx_by_ts[sym].get(ts_ms)
            if i is None or i < WARMUP_CANDLES or i + 1 >= len(candles_1h[sym]):
                continue
            window = candles_1h[sym][i - WARMUP_CANDLES:i + 1]
            slice_4h = _slice_higher_tf_at(candles_4h[sym], ts_ms, lookback=210)
            slice_1d = _slice_higher_tf_at(candles_1d[sym], ts_ms, lookback=60)
            slice_1w = _slice_higher_tf_at(candles_1w[sym], ts_ms, lookback=30)

            for dir_name in dirs_to_scan:
                concurrent = sum(1 for p in open_positions
                                 if p["symbol"] == sym and p["direction"] == dir_name)
                if concurrent >= max_concurrent_per_leg:
                    skipped_for_open += 1
                    continue
                eval_fn = _eval_bar_short if dir_name == "SHORT" else _eval_bar
                signals_evaluated += 1
                fired, snap = eval_fn(
                    window, ts_ms, tp_pct, sl_pct, sym,
                    candles_4h=slice_4h, candles_1d=slice_1d, candles_1w=slice_1w,
                )
                if not fired:
                    continue
                signals_fired += 1

                # Compute actual position size — apply per-asset weight multiplier
                # then optional score-weighted boost. Capital reservation uses
                # the final computed size.
                score = snap.get("total_score") or 0
                threshold = _ind.get_score_threshold(sym)
                base = per_trade * asset_size_mult.get(sym, 1.0)
                size = _position_size_for_score(base, score, threshold) \
                    if score_weighted_sizing else base

                if free_capital < size:
                    skipped_for_capital += 1
                    continue

                entry_bar = candles_1h[sym][i + 1]
                entry_price = entry_bar["open"]
                # `size` is collateral; notional = size × leverage
                notional = size * leverage
                qty = notional / entry_price
                entry_fee = notional * BINANCE_TAKER_FEE
                if dir_name == "SHORT":
                    tp_price = entry_price * (1 - tp_pct / 100)
                    sl_price = entry_price * (1 + sl_pct / 100)
                else:
                    tp_price = entry_price * (1 + tp_pct / 100)
                    sl_price = entry_price * (1 - sl_pct / 100)

                open_positions.append({
                    "symbol":        sym,
                    "direction":     dir_name,
                    "entry_ts":      entry_bar["open_time_ms"],
                    "entry_price":   entry_price,
                    "qty":           qty,
                    "position_size": size,       # collateral
                    "leverage":      leverage,
                    "notional":      notional,
                    "entry_fee":     entry_fee,
                    "tp_price":      tp_price,
                    "sl_price":      sl_price,
                    "tp_pct":        tp_pct,
                    "sl_pct":        sl_pct,
                    "score":         score,
                })
                free_capital -= size

        # 3. Mark-to-market equity snapshot (once per day, to keep curve small)
        if step % 24 == 0:
            equity = free_capital
            for pos in open_positions:
                sym = pos["symbol"]
                i = idx_by_ts[sym].get(ts_ms)
                if i is None:
                    equity += pos["position_size"]
                    continue
                cur_price = candles_1h[sym][i]["close"]
                if pos["direction"] == "LONG":
                    mtm = pos["qty"] * (cur_price - pos["entry_price"]) - pos["entry_fee"]
                else:
                    mtm = pos["qty"] * (pos["entry_price"] - cur_price) - pos["entry_fee"]
                equity += pos["position_size"] + mtm
            equity_curve.append((ts_ms, equity))

    # Force-close any remaining open positions at last bar's close
    last_ts = timeline[-1]
    for pos in open_positions:
        sym = pos["symbol"]
        i = idx_by_ts[sym].get(last_ts)
        if i is None:
            continue
        bar = candles_1h[sym][i]
        hours_held = (last_ts - pos["entry_ts"]) // (3600 * 1000)
        # Force timeout-style close
        outcome = _check_position(pos, {**bar, "open_time_ms": last_ts},
                                  max(hours_held, max_hold_hours), max_hold_hours,
                                  funding_rate_per_8h=funding_rate_per_8h)
        if outcome is None:
            # Fall back to manual close at current close price (qty already leveraged)
            exit_price = bar["close"]
            qty = pos["qty"]
            exit_fee = qty * exit_price * BINANCE_TAKER_FEE
            if pos["direction"] == "SHORT":
                gross = qty * (pos["entry_price"] - exit_price)
                pnl_pct = -(exit_price - pos["entry_price"]) / pos["entry_price"] * 100
            else:
                gross = qty * (exit_price - pos["entry_price"])
                pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
            funding_cost = (qty * pos["entry_price"]
                            * funding_rate_per_8h * (hours_held / 8.0))
            pnl_usd = gross - pos["entry_fee"] - exit_fee - funding_cost
            collateral = pos.get("position_size") or 0
            if pnl_usd < -collateral:
                pnl_usd = -collateral
            outcome = {
                "status": "FORCED_CLOSE",
                "exit_price": exit_price,
                "exit_ts": last_ts,
                "exit_fee": exit_fee,
                "funding_cost": funding_cost,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "pnl_pct_net_fees": pnl_pct - BINANCE_TAKER_FEE * 2 * 100,
                "hold_hours": int(hours_held),
            }
        free_capital += pos["position_size"] + outcome["pnl_usd"]
        closed_trades.append({**pos, **outcome})
    open_positions = []

    # Buy-and-hold benchmark: split capital equally across assets at first bar
    bh_pnl = 0.0
    bh_details = {}
    per_asset_capital = capital / len(assets)
    for sym in assets:
        sym_bars = candles_1h[sym]
        first_bar = next((c for c in sym_bars if c["open_time_ms"] >= timeline[0]), None)
        # Latest available bar at or before last_ts (handles non-aligned end of history)
        last_bar = next((c for c in reversed(sym_bars)
                        if c["open_time_ms"] <= last_ts), None)
        if first_bar is None or last_bar is None:
            continue
        entry_p, exit_p = first_bar["open"], last_bar["close"]
        qty = per_asset_capital / entry_p
        # Round-trip fees on the way in and out
        bh_pnl_sym = qty * (exit_p - entry_p) - per_asset_capital * BINANCE_TAKER_FEE * 2
        bh_pnl += bh_pnl_sym
        bh_details[sym] = {
            "entry": entry_p, "exit": exit_p,
            "pnl_usd": bh_pnl_sym,
            "pnl_pct": (exit_p - entry_p) / entry_p * 100,
        }

    # Restore monkey-patched globals
    if min_rr_override is not None:
        _ind.MIN_RR_RATIO = original_min_rr
    if score_threshold_override is not None:
        _ind.ENTRY_SCORE_THRESHOLD = original_threshold
        _ind.ENTRY_SCORE_THRESHOLD_DEFAULT = original_default
        _eng.ENTRY_SCORE_THRESHOLD = original_threshold
        _ind.ENTRY_SCORE_THRESHOLDS.clear()
        _ind.ENTRY_SCORE_THRESHOLDS.update(original_thresholds_dict)

    return {
        "params": {
            "days": days, "capital": capital, "per_trade": per_trade,
            "direction": direction, "tp_pct": tp_pct, "sl_pct": sl_pct,
            "assets": assets, "max_hold_hours": max_hold_hours,
            "min_rr_override": min_rr_override,
            "max_concurrent_per_leg": max_concurrent_per_leg,
            "interval": interval,
            "score_threshold_override": score_threshold_override,
            "score_weighted_sizing": score_weighted_sizing,
            "asset_weights": asset_weights,
            "leverage": leverage,
            "funding_rate_per_8h": funding_rate_per_8h,
            "dynamic_sl": dynamic_sl,
            "be_after_hours": be_after_hours,
            "be_trigger_pct": be_trigger_pct,
            "be_offset_pct": be_offset_pct,
            "trail_pct": trail_pct,
        },
        "trades": closed_trades,
        "equity_curve": equity_curve,
        "final_free_capital": free_capital,
        "signals_evaluated": signals_evaluated,
        "signals_fired": signals_fired,
        "skipped_for_capital": skipped_for_capital,
        "skipped_for_open": skipped_for_open,
        "buy_and_hold_pnl_usd": bh_pnl,
        "buy_and_hold_per_asset": bh_details,
        "first_ts": timeline[0],
        "last_ts": last_ts,
    }


# ─── DB persistence ───────────────────────────────────────────────────────────


REPLAY_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_replay_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT,
    days INTEGER NOT NULL,
    direction TEXT NOT NULL,
    capital REAL NOT NULL,
    per_trade REAL NOT NULL,
    tp_pct REAL NOT NULL,
    sl_pct REAL NOT NULL,
    assets TEXT NOT NULL,
    first_ts INTEGER, last_ts INTEGER,
    signals_evaluated INTEGER, signals_fired INTEGER,
    trades_total INTEGER,
    pnl_usd REAL, pnl_pct REAL,
    bh_pnl_usd REAL, bh_pnl_pct REAL,
    max_drawdown_pct REAL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS paper_replay_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES paper_replay_runs(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_ts INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_ts INTEGER, exit_price REAL,
    qty REAL, position_size REAL,
    tp_price REAL, sl_price REAL, tp_pct REAL, sl_pct REAL,
    entry_fee REAL, exit_fee REAL,
    status TEXT, score INTEGER,
    pnl_usd REAL, pnl_pct REAL, pnl_pct_net_fees REAL,
    hold_hours INTEGER
);
CREATE INDEX IF NOT EXISTS idx_replay_trades_run ON paper_replay_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_replay_trades_sym ON paper_replay_trades(symbol, direction);
"""


def save_to_db(res: dict, label: str | None) -> int:
    """Persist replay run + trades. Returns run_id."""
    p = res["params"]
    final_eq = res["final_free_capital"]
    pnl_usd = final_eq - p["capital"]

    # Compute max DD from equity curve
    max_dd = 0.0
    if res["equity_curve"]:
        peak = res["equity_curve"][0][1]
        for _, eq in res["equity_curve"]:
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100)

    con = sqlite3.connect(SQLITE_PATH)
    try:
        con.executescript(REPLAY_RUNS_SCHEMA)
        cur = con.cursor()
        cur.execute("""
            INSERT INTO paper_replay_runs
            (label, days, direction, capital, per_trade, tp_pct, sl_pct, assets,
             first_ts, last_ts, signals_evaluated, signals_fired, trades_total,
             pnl_usd, pnl_pct, bh_pnl_usd, bh_pnl_pct, max_drawdown_pct)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            label, p["days"], p["direction"], p["capital"], p["per_trade"],
            p["tp_pct"], p["sl_pct"], json.dumps(p["assets"]),
            res["first_ts"], res["last_ts"],
            res["signals_evaluated"], res["signals_fired"], len(res["trades"]),
            pnl_usd, pnl_usd / p["capital"] * 100,
            res["buy_and_hold_pnl_usd"], res["buy_and_hold_pnl_usd"] / p["capital"] * 100,
            max_dd,
        ))
        run_id = cur.lastrowid
        for t in res["trades"]:
            cur.execute("""
                INSERT INTO paper_replay_trades
                (run_id, symbol, direction, entry_ts, entry_price, exit_ts, exit_price,
                 qty, position_size, tp_price, sl_price, tp_pct, sl_pct,
                 entry_fee, exit_fee, status, score,
                 pnl_usd, pnl_pct, pnl_pct_net_fees, hold_hours)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run_id, t["symbol"], t["direction"], t["entry_ts"], t["entry_price"],
                t.get("exit_ts"), t.get("exit_price"), t["qty"], t["position_size"],
                t["tp_price"], t["sl_price"], t["tp_pct"], t["sl_pct"],
                t["entry_fee"], t.get("exit_fee"), t.get("status"), t.get("score"),
                t.get("pnl_usd"), t.get("pnl_pct"), t.get("pnl_pct_net_fees"),
                t.get("hold_hours"),
            ))
        con.commit()
        log.info("Saved run #%d with %d trades to %s", run_id, len(res["trades"]), SQLITE_PATH)
        return run_id
    finally:
        con.close()


# ─── Reporting ────────────────────────────────────────────────────────────────


def _fmt_money(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def print_report(res: dict) -> None:
    p = res["params"]
    trades = res["trades"]
    capital = p["capital"]

    print("\n" + "=" * 70)
    print(f"PAPER REPLAY — {p['days']}d, ${p['capital']:,.0f} capital, "
          f"${p['per_trade']:,.0f}/trade, {p['direction']}, "
          f"TP {p['tp_pct']}% / SL {p['sl_pct']}%")
    print("=" * 70)
    print(f"Period: {datetime.fromtimestamp(res['first_ts']/1000, tz=timezone.utc).date()}"
          f" → {datetime.fromtimestamp(res['last_ts']/1000, tz=timezone.utc).date()}")
    print(f"Assets: {', '.join(p['assets'])}")
    print()

    print(f"Signals evaluated: {res['signals_evaluated']:,}")
    print(f"Signals fired:     {res['signals_fired']:,} "
          f"({res['signals_fired']/max(res['signals_evaluated'],1)*100:.2f}%)")
    print(f"Skipped (busy):    {res['skipped_for_open']:,}")
    print(f"Skipped (capital): {res['skipped_for_capital']:,}")
    print(f"Trades opened:     {len(trades):,}")
    print()

    if not trades:
        print("No trades fired. Nothing to summarise.")
        return

    # Aggregate by (symbol, direction)
    print(f"{'Asset':<10} {'Dir':<6} {'N':>4} {'WR':>6} {'Gross $':>11} {'Net $':>11}")
    print("-" * 55)
    total_pnl = 0.0
    for sym in p["assets"]:
        for d in (["LONG", "SHORT"] if p["direction"] == "BOTH" else [p["direction"]]):
            subset = [t for t in trades if t["symbol"] == sym and t["direction"] == d]
            if not subset:
                continue
            wins = sum(1 for t in subset if t["pnl_usd"] > 0)
            wr = wins / len(subset) * 100
            gross = sum(t["qty"] * abs(t["exit_price"] - t["entry_price"])
                        * (1 if t["pnl_usd"] > 0 else -1) for t in subset)
            net = sum(t["pnl_usd"] for t in subset)
            total_pnl += net
            print(f"{sym:<10} {d:<6} {len(subset):>4} {wr:>5.1f}% "
                  f"{_fmt_money(gross):>11} {_fmt_money(net):>11}")
    print("-" * 55)
    print(f"{'TOTAL':<10} {'':<6} {len(trades):>4} "
          f"{sum(1 for t in trades if t['pnl_usd']>0)/len(trades)*100:>5.1f}% "
          f"{'':>11} {_fmt_money(total_pnl):>11}")
    print()

    # Status breakdown
    status_ct: dict[str, int] = {}
    for t in trades:
        status_ct[t["status"]] = status_ct.get(t["status"], 0) + 1
    print("Exits:", ", ".join(f"{k}={v}" for k, v in sorted(status_ct.items())))
    print()

    # Headline
    final_equity = res["final_free_capital"]
    total_return_pct = (final_equity - capital) / capital * 100
    after_tax_pnl = total_pnl * (1 - LT_TAX_RATE) if total_pnl > 0 else total_pnl
    after_tax_pct = after_tax_pnl / capital * 100

    print("PAPER STRATEGY:")
    print(f"  Starting capital:  {_fmt_money(capital)}")
    print(f"  Final equity:      {_fmt_money(final_equity)}")
    print(f"  Net PnL:           {_fmt_money(total_pnl)} ({total_return_pct:+.2f}%)")
    print(f"  After LT 15% tax:  {_fmt_money(after_tax_pnl)} ({after_tax_pct:+.2f}%)")
    print()

    # Drawdown from equity curve
    if res["equity_curve"]:
        peak = res["equity_curve"][0][1]
        max_dd = 0.0
        for _, eq in res["equity_curve"]:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100
            max_dd = max(max_dd, dd)
        print(f"  Max drawdown:      {max_dd:.2f}%")
        print()

    # Buy-and-hold benchmark
    bh_pnl = res["buy_and_hold_pnl_usd"]
    bh_pct = bh_pnl / capital * 100
    bh_after_tax = bh_pnl * (1 - LT_TAX_RATE) if bh_pnl > 0 else bh_pnl
    print("BUY & HOLD BENCHMARK (equal split across assets):")
    for sym, d in res["buy_and_hold_per_asset"].items():
        print(f"  {sym:<10} ${d['entry']:>10,.2f} → ${d['exit']:>10,.2f} "
              f"= {d['pnl_pct']:+.2f}% ({_fmt_money(d['pnl_usd'])})")
    print(f"  TOTAL HOLD:        {_fmt_money(bh_pnl)} ({bh_pct:+.2f}%)")
    print(f"  After LT 15% tax:  {_fmt_money(bh_after_tax)} "
          f"({bh_after_tax/capital*100:+.2f}%)")
    print()

    diff = total_pnl - bh_pnl
    print("VERDICT:")
    if diff > 0:
        print(f"  Strategy BEATS buy-and-hold by {_fmt_money(diff)} "
              f"({diff/capital*100:+.2f}pp)")
    else:
        print(f"  Strategy LOSES to buy-and-hold by {_fmt_money(-diff)} "
              f"({-diff/capital*100:.2f}pp)")
    print("=" * 70 + "\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=720)
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--per-trade", type=float, default=1000.0)
    ap.add_argument("--direction", default="BOTH", choices=["LONG", "SHORT", "BOTH"])
    ap.add_argument("--tp", type=float, default=3.0)
    ap.add_argument("--sl", type=float, default=1.5)
    ap.add_argument("--assets", nargs="+", default=ASSETS)
    ap.add_argument("--save-db", action="store_true", help="Persist run + trades to SQLite")
    ap.add_argument("--label", type=str, default=None, help="Optional run label")
    ap.add_argument("--max-hold", type=int, default=MAX_HOLD_HOURS,
                    help=f"Max hold hours before TIMEOUT exit (default {MAX_HOLD_HOURS})")
    ap.add_argument("--min-rr", type=float, default=None,
                    help="Override MIN_RR_RATIO for L6 layer (default 1.5)")
    ap.add_argument("--max-concurrent", type=int, default=1,
                    help="Max concurrent positions per (symbol, direction)")
    ap.add_argument("--dynamic-sl", action="store_true",
                    help="Enable dynamic SL (BE move + optional trail)")
    ap.add_argument("--be-after-hours", type=int, default=6,
                    help="Hours before checking break-even trigger")
    ap.add_argument("--be-trigger-pct", type=float, default=1.0,
                    help="Profit %% required to trigger BE move")
    ap.add_argument("--be-offset-pct", type=float, default=0.2,
                    help="Offset above entry where BE-SL moves to (covers fees)")
    ap.add_argument("--trail-pct", type=float, default=None,
                    help="Trail SL this %% below peak (after BE). None = no trail.")
    ap.add_argument("--interval", default="1h",
                    choices=["15m", "30m", "1h", "2h", "4h"],
                    help="Base timeframe interval")
    ap.add_argument("--score-threshold", type=float, default=None,
                    help="Override ENTRY_SCORE_THRESHOLD (default per-asset)")
    ap.add_argument("--score-weighted", action="store_true",
                    help="Scale position size by score margin above threshold "
                         "(0.5×/1×/1.5×/2× base for margin <5/<15/<25/≥25)")
    ap.add_argument("--asset-weights", type=str, default=None,
                    help="Comma-separated asset shares, e.g. "
                         "'BTCUSDT:0.1,SOLUSDT:0.3,ETHUSDT:0.6'. "
                         "Normalised so equal-split → 1.0× multiplier per asset.")
    ap.add_argument("--leverage", type=float, default=1.0,
                    help="Position leverage (1.0 = spot, 3.0 = 3× futures). "
                         "Notional = collateral × leverage. Liquidation modelled "
                         "at (1/L - 0.005) × 100%% adverse move.")
    ap.add_argument("--funding-rate", type=float, default=0.0001,
                    help="Perp funding rate per 8h (default 0.0001 = 0.01%%/8h "
                         "≈ 0.03%%/day). Set to 0 to disable funding cost.")
    args = ap.parse_args()

    asset_weights = None
    if args.asset_weights:
        asset_weights = {}
        for pair in args.asset_weights.split(","):
            sym, w = pair.split(":")
            asset_weights[sym.strip()] = float(w.strip())

    res = run_replay(
        days=args.days, capital=args.capital, per_trade=args.per_trade,
        direction=args.direction, tp_pct=args.tp, sl_pct=args.sl,
        assets=args.assets,
        max_hold_hours=args.max_hold,
        min_rr_override=args.min_rr,
        max_concurrent_per_leg=args.max_concurrent,
        dynamic_sl=args.dynamic_sl,
        be_after_hours=args.be_after_hours,
        be_trigger_pct=args.be_trigger_pct,
        be_offset_pct=args.be_offset_pct,
        trail_pct=args.trail_pct,
        interval=args.interval,
        score_threshold_override=args.score_threshold,
        score_weighted_sizing=args.score_weighted,
        asset_weights=asset_weights,
        leverage=args.leverage,
        funding_rate_per_8h=args.funding_rate,
    )
    print_report(res)
    if args.save_db:
        save_to_db(res, args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())