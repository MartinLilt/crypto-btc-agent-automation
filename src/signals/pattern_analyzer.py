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

from src.data.db import cache_get, cache_set, get_trades

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


def _by_l9_score_band(trades: list) -> dict:
    """L9 candle-pattern score bands (0-10). Column `l9_fg_value` historically
    misnamed — engine writes the L9 score there, not Fear&Greed.
    """
    def band(t):
        v = t.get("l9_fg_value")
        if v is None:
            return None
        if v <= 2:
            return "Weak (0-2)"
        if v <= 4:
            return "Below avg (3-4)"
        if v <= 6:
            return "Neutral (5-6)"
        if v <= 8:
            return "Strong (7-8)"
        return "Top (9-10)"

    groups = _group_by(trades, band)
    result = []
    for label, group in groups.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE:
            result.append({"band": label, "win_rate": wr, "sample": size})
    result.sort(key=lambda x: x["win_rate"], reverse=True)
    return {"l9_score_bands": result}


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


def _by_l8_score_band(trades: list) -> dict:
    """L8 S/R-proximity score bands (0-10). Column `l8_funding` historically
    misnamed — engine writes the L8 score there, not funding rate.
    """
    def band(t):
        v = t.get("l8_funding")
        if v is None:
            return None
        if v <= 2:
            return "Weak (0-2)"
        if v <= 4:
            return "Below avg (3-4)"
        if v <= 6:
            return "Neutral (5-6)"
        if v <= 8:
            return "Strong (7-8)"
        return "Top (9-10)"

    groups = _group_by(trades, band)
    result = []
    for label, group in groups.items():
        wr, size = _win_rate(group)
        if size >= MIN_SAMPLE:
            result.append({"band": label, "win_rate": wr, "sample": size})
    result.sort(key=lambda x: x["win_rate"], reverse=True)
    return {"l8_score_bands": result}


def _power_combos(trades: list) -> dict:
    """
    Find the most profitable combinations of 2 conditions.
    Checks: FG band × RSI band, FG band × hour bucket, etc.
    """
    combos = []

    # L9 candle-pattern score + RSI combo (column l9_fg_value is L9 score 0-10)
    def l9_rsi_key(t):
        l9 = t.get("l9_fg_value")
        rsi = t.get("l3_rsi")
        if l9 is None or rsi is None:
            return None
        l9_label = "Weak" if l9 < 5 else ("Neutral" if l9 < 7 else "Strong")
        rsi_label = "Low" if rsi < 45 else ("Mid" if rsi < 55 else "High")
        return f"L9={l9_label} + RSI={rsi_label}"

    groups = _group_by(trades, l9_rsi_key)
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


def _by_adx_band(trades: list) -> dict:
    def adx_band(t):
        v = t.get("l1_adx")
        if v is None:
            return None
        if v < 20:
            return "Weak (<20)"
        if v < 25:
            return "Borderline (20-25)"
        if v < 30:
            return "Moderate (25-30)"
        if v < 40:
            return "Strong (30-40)"
        return "Very strong (40+)"

    groups = _group_by(trades, adx_band)
    result = []
    order = ["Weak (<20)", "Borderline (20-25)", "Moderate (25-30)",
             "Strong (30-40)", "Very strong (40+)"]
    for label in order:
        if label in groups:
            wr, size = _win_rate(groups[label])
            if size >= MIN_SAMPLE:
                avg_pnl = sum(t["pnl_pct"] for t in groups[label]) / size
                result.append({
                    "band": label, "win_rate": wr,
                    "sample": size, "avg_pnl": round(avg_pnl, 3),
                })
    return {"adx_bands": result}


def _by_score_band(trades: list) -> dict:
    def score_band(t):
        v = t.get("total_score")
        if v is None:
            return None
        if v < 55:
            return "Low (< 55)"
        if v < 60:
            return "Below avg (55-60)"
        if v < 65:
            return "Average (60-65)"
        if v < 70:
            return "Good (65-70)"
        if v < 75:
            return "Strong (70-75)"
        return "Top (75+)"

    groups = _group_by(trades, score_band)
    result = []
    order = ["Low (< 55)", "Below avg (55-60)", "Average (60-65)",
             "Good (65-70)", "Strong (70-75)", "Top (75+)"]
    for label in order:
        if label in groups:
            wr, size = _win_rate(groups[label])
            if size >= MIN_SAMPLE:
                avg_pnl = sum(t["pnl_pct"] for t in groups[label]) / size
                result.append({
                    "band": label, "win_rate": wr,
                    "sample": size, "avg_pnl": round(avg_pnl, 3),
                })
    return {"score_bands": result}


def _virtual_threshold_test(trades: list) -> list:
    """
    Simulate: what if we only entered when score >= threshold?
    Returns list of {threshold, wr, signals, estimated_pnl}.
    """
    thresholds = [55, 60, 65, 70, 75, 80]
    results = []
    for thr in thresholds:
        subset = [t for t in trades if (t.get("total_score") or 0) >= thr]
        if len(subset) < MIN_SAMPLE:
            continue
        wr, size = _win_rate(subset)
        avg_pnl = sum(t["pnl_pct"] for t in subset) / size
        results.append({
            "threshold": thr,
            "signals":   size,
            "win_rate":  wr,
            "avg_pnl":   round(avg_pnl, 3),
        })
    return results


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
    losses = [t for t in trades if t["result"] == "SL_HIT"]
    if not losses:
        return {"layer_block": []}

    checks = {
        "L1 ADX>25": lambda t: (t.get("l1_adx") or 0) > 25,
        "L3 RSI 35-65": lambda t: 35 < (t.get("l3_rsi") or 50) < 65,
        "L9 candle ≥6": lambda t: (t.get("l9_fg_value") or 0) >= 6,
        "L10 Buy>50%": lambda t: (t.get("l10_buy_ratio") or 50) > 50,
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

def compute_patterns(symbol: str, days: Optional[int] = None,
                     direction: str = "LONG") -> dict:
    """
    Load trades from SQLite for symbol+direction, compute all patterns,
    cache in Redis. Returns patterns dict.
    """
    cache_key = f"patterns:{symbol}:{direction}:{days or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        logger.debug("Patterns from cache: %s", cache_key)
        return cached

    trades = get_trades(symbol, days, direction=direction)
    if not trades:
        return {"error": "no_trades", "symbol": symbol, "direction": direction}

    wr_overall, total = _win_rate(trades)

    patterns = {
        "symbol":       symbol,
        "direction":    direction,
        "total_trades": total,
        "overall_wr":   wr_overall,
        **_by_hour(trades),
        **_by_weekday(trades),
        **_by_adx_band(trades),
        **_by_score_band(trades),
        "score_thresholds": _virtual_threshold_test(trades),
        **_by_rsi_band(trades),
        **_by_l9_score_band(trades),
        **_by_l8_score_band(trades),
        **_power_combos(trades),
        **_optimal_hold(trades),
        **_layer_block_stats(trades),
    }

    cache_set(cache_key, patterns, ttl=PATTERNS_TTL)
    return patterns


def format_patterns_message(patterns: dict, lang: str = "en") -> str:
    """Format patterns dict into a Telegram-ready Markdown message."""
    if "error" in patterns:
        direction = patterns.get("direction", "LONG")
        other = "SHORT" if direction == "LONG" else "LONG"
        msg = {
            "en": (f"⚠️ No backtest data for {direction}. Run `/backtest` first, "
                   f"or try the {other} direction."),
            "ru": (f"⚠️ Нет данных бэктеста для {direction}. Сначала запусти "
                   f"`/backtest` или попробуй направление {other}."),
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

    # Score threshold simulation
    score_thr = patterns.get("score_thresholds", [])
    if score_thr:
        label = "🎯 Если поднять порог входа (score)" if lang == "ru" else "🎯 What if we raised the entry threshold?"
        lines.append(f"*{label}*")
        for s in score_thr:
            arrow = "→"
            wr_icon = "✅" if s["win_rate"] >= 50 else ("⚠️" if s["win_rate"] >= 40 else "❌")
            lines.append(
                f"  score ≥ {s['threshold']}  {arrow}  {wr_icon} {s['win_rate']}% WR  "
                f"({s['signals']} сд.  avg {s['avg_pnl']:+.2f}%)" if lang == "ru" else
                f"  score ≥ {s['threshold']}  {arrow}  {wr_icon} {s['win_rate']}% WR  "
                f"({s['signals']} trades  avg {s['avg_pnl']:+.2f}%)"
            )
        lines.append("")

    # ADX bands
    adx_bands = patterns.get("adx_bands", [])
    if adx_bands:
        label = "📈 Win rate по силе тренда (ADX)" if lang == "ru" else "📈 Win rate by trend strength (ADX)"
        lines.append(f"*{label}*")
        for b in adx_bands:
            wr_icon = "✅" if b["win_rate"] >= 50 else ("⚠️" if b["win_rate"] >= 40 else "❌")
            lines.append(
                f"  ADX {b['band']} — {wr_icon} {b['win_rate']}% WR  "
                f"(avg {b['avg_pnl']:+.2f}%  {b['sample']} сд.)" if lang == "ru" else
                f"  ADX {b['band']} — {wr_icon} {b['win_rate']}% WR  "
                f"(avg {b['avg_pnl']:+.2f}%  {b['sample']} trades)"
            )
        lines.append("")

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

    # RSI bands
    rsi_bands = patterns.get("rsi_bands", [])
    if rsi_bands:
        label = "📊 Win rate по RSI при входе" if lang == "ru" else "📊 Win rate by RSI at entry"
        lines.append(f"*{label}*")
        for b in rsi_bands[:4]:
            wr_icon = "✅" if b["win_rate"] >= 50 else ("⚠️" if b["win_rate"] >= 40 else "❌")
            lines.append(
                f"  RSI {b['band']} — {wr_icon} {b['win_rate']}% WR  "
                f"(avg {b['avg_pnl']:+.2f}%  {b['sample']} сд.)" if lang == "ru" else
                f"  RSI {b['band']} — {wr_icon} {b['win_rate']}% WR  "
                f"(avg {b['avg_pnl']:+.2f}%  {b['sample']} trades)"
            )
        lines.append("")

    # L9 candle-pattern score bands
    l9_bands = patterns.get("l9_score_bands", [])
    if l9_bands:
        label = ("🕯 L9 свечной паттерн — лучшие зоны" if lang == "ru"
                 else "🕯 L9 candle pattern — best zones")
        lines.append(f"*{label}*")
        for b in l9_bands[:3]:
            lines.append(
                f"  {b['band']} — {b['win_rate']}% WR ({b['sample']})"
            )
        lines.append("")

    # L8 S/R-proximity score bands
    l8_bands = patterns.get("l8_score_bands", [])
    if l8_bands:
        label = ("🎯 L8 близость к S/R — лучшие зоны" if lang == "ru"
                 else "🎯 L8 S/R proximity — best zones")
        lines.append(f"*{label}*")
        for b in l8_bands[:3]:
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