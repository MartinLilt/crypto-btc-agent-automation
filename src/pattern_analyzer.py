"""
Pattern Analyzer — finds statistically significant patterns in backtest trades.

Reads trade history from SQLite and computes:
  - Win rate by hour of day
  - Win rate by weekday
  - Win rate by Fear & Greed band
  - Win rate by RSI band at entry
  - Win rate by funding rate bucket
  - Best/worst layer combinations
  - Optimal TP/SL suggestions

Results are cached in Redis (key: patterns:{symbol}).
"""

import logging
from collections import defaultdict
from typing import Optional

from src.db import cache_get, cache_set, get_trades

logger = logging.getLogger(__name__)

PATTERNS_TTL = 1800   # 30 min cache
MIN_SAMPLE = 3        # minimum trades in a group to report pattern


# ── Helpers ───────────────────────────────────────────────────────────────────

def _win_rate(trades: list) -> tuple[float, int]:
    """Returns (win_rate_pct, sample_size)."""
    if not trades:
        return 0.0, 0
    wins = sum(1 for t in trades if t["result"] == "TP_HIT")
    return round(wins / len(trades) * 100, 1), len(trades)


def _group_by(trades: list, key_fn) -> dict:
    """Group trades by key_fn(trade) → dict of lists."""
    groups = defaultdict(list)
    for t in trades:
        k = key_fn(t)
        if k is not None:
            groups[k].append(t)
    return dict(groups)


def _top_bottom(groups: dict, n: int = 3) -> tuple[list, list]:
    """
    Given {label: [trades]}, return top-n and bottom-n by win rate.
    Only groups with >= MIN_SAMPLE trades are included.
    """
    stats = []
    for label, group in groups.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE:
            avg_pnl = sum(t["pnl_pct"] for t in group) / size
            stats.append({
                "label": label,
                "win_rate": wr,
                "sample": size,
                "avg_pnl": round(avg_pnl, 3),
            })
    stats.sort(key=lambda x: x["win_rate"], reverse=True)
    return stats[:n], stats[-n:][::-1]


# ── Pattern computations ──────────────────────────────────────────────────────

def _by_hour(trades: list) -> dict:
    groups = _group_by(trades, lambda t: t.get("hour_utc"))
    top, bot = _top_bottom(groups, n=3)
    return {"best_hours": top, "worst_hours": bot}


def _by_weekday(trades: list) -> dict:
    order = ["Monday", "Tuesday", "Wednesday",
             "Thursday", "Friday", "Saturday", "Sunday"]
    groups = _group_by(trades, lambda t: t.get("weekday"))
    stats = []
    for day in order:
        if day in groups:
            wr, size = _win_rate(groups[day])
            if size >= MIN_SAMPLE:
                stats.append({"day": day, "win_rate": wr, "sample": size})
    best = sorted(stats, key=lambda x: x["win_rate"], reverse=True)[:2]
    worst = sorted(stats, key=lambda x: x["win_rate"])[:2]
    return {"best_days": best, "worst_days": worst}


def _by_fg_band(trades: list) -> dict:
    def fg_band(t):
        v = t.get("l9_fg_value")
        if v is None:
            return None
        if v <= 24:
            return "Extreme Fear (0-24)"
        if v <= 44:
            return "Fear (25-44)"
        if v <= 54:
            return "Neutral (45-54)"
        if v <= 74:
            return "Greed (55-74)"
        return "Extreme Greed (75-100)"

    groups = _group_by(trades, fg_band)
    result = []
    for label, group in groups.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE:
            result.append({"band": label, "win_rate": wr, "sample": size})
    result.sort(key=lambda x: x["win_rate"], reverse=True)
    return {"fg_bands": result}


def _by_rsi_band(trades: list) -> dict:
    def rsi_band(t):
        v = t.get("l3_rsi")
        if v is None:
            return None
        if v < 30:
            return "Oversold (<30)"
        if v < 40:
            return "Low RSI (30-40)"
        if v < 50:
            return "Mid-low (40-50)"
        if v < 60:
            return "Mid-high (50-60)"
        if v < 70:
            return "High RSI (60-70)"
        return "Overbought (>70)"

    groups = _group_by(trades, rsi_band)
    result = []
    for label, group in groups.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE:
            avg_pnl = sum(t["pnl_pct"] for t in group) / size
            result.append({
                "band": label, "win_rate": wr,
                "sample": size, "avg_pnl": round(avg_pnl, 3),
            })
    result.sort(key=lambda x: x["win_rate"], reverse=True)
    return {"rsi_bands": result}


def _by_funding_band(trades: list) -> dict:
    def fund_band(t):
        v = t.get("l8_funding")
        if v is None:
            return None
        if v < -0.03:
            return "Shorts squeezed (<-0.03%)"
        if v < 0:
            return "Negative (-0.03–0%)"
        if v < 0.02:
            return "Neutral (0–0.02%)"
        if v < 0.05:
            return "Elevated (0.02–0.05%)"
        return "Overheated (>0.05%)"

    groups = _group_by(trades, fund_band)
    result = []
    for label, group in groups.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE:
            result.append({"band": label, "win_rate": wr, "sample": size})
    result.sort(key=lambda x: x["win_rate"], reverse=True)
    return {"funding_bands": result}


def _power_combos(trades: list) -> dict:
    """
    Find the most profitable combinations of 2 conditions.
    Checks: FG band × RSI band, FG band × hour bucket, etc.
    """
    combos = []

    # FG + RSI combo
    def fg_rsi_key(t):
        fg = t.get("l9_fg_value")
        rsi = t.get("l3_rsi")
        if fg is None or rsi is None:
            return None
        fg_label = "Fear" if fg < 45 else ("Neutral" if fg < 55 else "Greed")
        rsi_label = "Low" if rsi < 45 else ("Mid" if rsi < 55 else "High")
        return f"FG={fg_label} + RSI={rsi_label}"

    groups = _group_by(trades, fg_rsi_key)
    for label, group in groups.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE and wr >= 65:
            avg_pnl = sum(t["pnl_pct"] for t in group) / size
            combos.append({
                "combo": label,
                "win_rate": wr,
                "sample": size,
                "avg_pnl": round(avg_pnl, 3),
            })

    # Buy pressure + RSI
    def pressure_rsi_key(t):
        ratio = t.get("l10_buy_ratio")
        rsi = t.get("l3_rsi")
        if ratio is None or rsi is None:
            return None
        p_label = "BuyDom" if ratio > 55 else (
            "SellDom" if ratio < 45 else "Balanced")
        rsi_label = "RSI<50" if rsi < 50 else "RSI≥50"
        return f"Pressure={p_label} + {rsi_label}"

    groups2 = _group_by(trades, pressure_rsi_key)
    for label, group in groups2.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE and wr >= 65:
            avg_pnl = sum(t["pnl_pct"] for t in group) / size
            combos.append({
                "combo": label,
                "win_rate": wr,
                "sample": size,
                "avg_pnl": round(avg_pnl, 3),
            })

    combos.sort(key=lambda x: x["win_rate"], reverse=True)
    return {"power_combos": combos[:5]}


def _optimal_hold(trades: list) -> dict:
    """
    Compute average hold time for wins vs losses.
    Suggests ideal hold window.
    """
    wins = [t for t in trades if t["result"] == "TP_HIT"]
    losses = [t for t in trades if t["result"] == "SL_HIT"]
    timeouts = [t for t in trades if t["result"] == "TIMEOUT"]

    def avg_hold(group):
        if not group:
            return 0
        return round(sum(t["hold_hours"] for t in group) / len(group), 1)

    timeout_pnl = (
        sum(t["pnl_pct"] for t in timeouts) / len(timeouts)
        if timeouts else 0
    )
    return {
        "avg_hold_win_h":     avg_hold(wins),
        "avg_hold_loss_h":    avg_hold(losses),
        "avg_hold_timeout_h": avg_hold(timeouts),
        "timeout_avg_pnl":    round(timeout_pnl, 3),
    }


def _layer_block_stats(trades: list) -> dict:
    """
    Estimate how often each layer WOULD have blocked a losing trade
    (retroactive filter analysis).
    """
    # We look at losing trades and check what each layer was
    losses = [t for t in trades if t["result"] == "SL_HIT"]
    if not losses:
        return {"layer_block": []}

    checks = {
        "L1 ADX>25":       lambda t: (t.get("l1_adx") or 0) > 25,
        "L3 RSI 35-65":    lambda t: 35 < (t.get("l3_rsi") or 50) < 65,
        "L9 FG 25-74":     lambda t: 25 < (t.get("l9_fg_value") or 50) < 74,
        "L10 Buy>50%":     lambda t: (t.get("l10_buy_ratio") or 50) > 50,
        "L4 Weekday(M-F)": lambda t: t.get("weekday") not in (
            "Saturday", "Sunday"),
    }

    result = []
    for name, fn in checks.items():
        blocked = sum(1 for t in losses if not fn(t))
        pct = round(blocked / len(losses) * 100, 1)
        result.append({
            "layer": name,
            "would_block_pct": pct,
            "sample": len(losses),
        })
    result.sort(key=lambda x: x["would_block_pct"], reverse=True)
    return {"layer_block": result}


# ── Public API ────────────────────────────────────────────────────────────────

def compute_patterns(symbol: str, days: Optional[int] = None) -> dict:
    """
    Load trades from SQLite, compute all patterns, cache in Redis.
    Returns patterns dict.
    """
    cache_key = f"patterns:{symbol}:{days or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        logger.debug("Patterns from cache: %s", cache_key)
        return cached

    trades = get_trades(symbol, days)
    if not trades:
        return {"error": "no_trades", "symbol": symbol}

    wr_overall, total = _win_rate(trades)

    patterns = {
        "symbol":      symbol,
        "total_trades": total,
        "overall_wr":  wr_overall,
        **_by_hour(trades),
        **_by_weekday(trades),
        **_by_fg_band(trades),
        **_by_rsi_band(trades),
        **_by_funding_band(trades),
        **_power_combos(trades),
        **_optimal_hold(trades),
        **_layer_block_stats(trades),
    }

    cache_set(cache_key, patterns, ttl=PATTERNS_TTL)
    return patterns


def format_patterns_message(patterns: dict, lang: str = "en") -> str:
    """Format patterns dict into a Telegram-ready Markdown message."""
    if "error" in patterns:
        msg = {
            "en": "⚠️ No backtest data yet. Run `/backtest` first.",
            "ru": "⚠️ Нет данных бэктеста. Сначала запусти `/backtest`.",
        }
        return msg.get(lang, msg["en"])

    sym = patterns["symbol"]
    total = patterns["total_trades"]
    wr = patterns["overall_wr"]

    if lang == "ru":
        lines = [
            f"🔬 *Паттерны — {sym}*  ({total} сделок, WR {wr}%)\n",
        ]
    else:
        lines = [
            f"🔬 *Patterns — {sym}*  ({total} trades, WR {wr}%)\n",
        ]

    # Best hours
    best_h = patterns.get("best_hours", [])
    if best_h:
        label = "⏰ Лучшие часы UTC" if lang == "ru" else "⏰ Best hours UTC"
        lines.append(f"*{label}*")
        for h in best_h:
            lines.append(
                f"  {h['label']:02d}:00 — {h['win_rate']}% WR  "
                f"({h['sample']} сд.)" if lang == "ru" else
                f"  {h['label']:02d}:00 — {h['win_rate']}% WR  "
                f"({h['sample']} trades)"
            )
        lines.append("")

    # Best weekdays
    best_d = patterns.get("best_days", [])
    if best_d:
        label = "📅 Лучшие дни" if lang == "ru" else "📅 Best days"
        lines.append(f"*{label}*")
        for d in best_d:
            lines.append(
                f"  {d['day']} — {d['win_rate']}% WR ({d['sample']})"
            )
        lines.append("")

    # FG bands
    fg_bands = patterns.get("fg_bands", [])
    if fg_bands:
        label = ("😱 Fear & Greed — лучшие зоны" if lang == "ru"
                 else "😱 Fear & Greed — best zones")
        lines.append(f"*{label}*")
        for b in fg_bands[:3]:
            lines.append(
                f"  {b['band']} — {b['win_rate']}% WR ({b['sample']})"
            )
        lines.append("")

    # Power combos
    combos = patterns.get("power_combos", [])
    if combos:
        label = ("🔥 Сильные комбинации" if lang == "ru"
                 else "🔥 Power combinations")
        lines.append(f"*{label}*")
        for c in combos[:3]:
            lines.append(
                f"  {c['combo']}\n"
                f"    → {c['win_rate']}% WR  avg {c['avg_pnl']:+.2f}%"
                f"  ({c['sample']})"
            )
        lines.append("")

    # Hold time
    hold = patterns.get("avg_hold_win_h")
    if hold:
        label = "⏱ Среднее время удержания" if lang == "ru" else "⏱ Avg hold time"
        lines.append(f"*{label}*")
        lines.append(
            f"  ✅ Win: {patterns['avg_hold_win_h']}h  "
            f"❌ Loss: {patterns['avg_hold_loss_h']}h"
        )
        lines.append("")

    # Layer block analysis
    blocks = patterns.get("layer_block", [])
    if blocks:
        label = ("🛡 Слои-защитники (блок убытков)" if lang == "ru"
                 else "🛡 Protective layers (loss block)")
        lines.append(f"*{label}*")
        for b in blocks[:3]:
            lines.append(
                f"  {b['layer']} — заблокировал бы {b['would_block_pct']}%"
                if lang == "ru" else
                f"  {b['layer']} — would block {b['would_block_pct']}%"
            )

    return "\n".join(lines)
