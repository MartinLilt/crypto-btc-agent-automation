"""
Support & Resistance proximity check (L8).

Algorithm:
  1. Find fractal swing highs in the candle window (local maxima)
  2. Cluster nearby levels within 0.3%
  3. Score 0-10: clear path to TP → high score; resistance blocking path → low score

Used for both live analysis and backtest (computed from candle history, no external data).
"""

from __future__ import annotations


def _detect_swing_highs(candles: list, window: int = 3) -> list[float]:
    """
    Return prices of fractal swing highs.
    candles[i] is a swing high if its high is strictly greater than
    every high in the surrounding window on both sides.
    """
    highs = []
    n = len(candles)
    for i in range(window, n - window):
        h = candles[i]["high"]
        left_ok  = all(candles[j]["high"] < h for j in range(i - window, i))
        right_ok = all(candles[j]["high"] < h for j in range(i + 1, i + window + 1))
        if left_ok and right_ok:
            highs.append(h)
    return highs


def _cluster_levels(levels: list[float], threshold_pct: float = 0.3) -> list[float]:
    """Merge levels that are within threshold_pct% of each other."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters: list[list[float]] = [[levels[0]]]
    for lvl in levels[1:]:
        if (lvl - clusters[-1][0]) / clusters[-1][0] * 100 <= threshold_pct:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    return [sum(c) / len(c) for c in clusters]


def _score_sr(blocking: list[float], price: float) -> int:
    if not blocking:
        return 10   # clean path to TP

    if len(blocking) >= 3:
        return 1    # multiple walls — very unlikely to reach TP

    nearest = min(blocking)
    gap_pct = (nearest - price) / price * 100

    if len(blocking) == 1:
        if gap_pct >= 1.5:
            return 7   # resistance near TP — some risk
        if gap_pct >= 1.0:
            return 5
        if gap_pct >= 0.5:
            return 3
        return 1       # resistance immediately above
    else:
        # 2 blockers
        if gap_pct >= 1.0:
            return 4
        return 2


def check_sr_proximity(
    candles: list,
    tp_pct: float = 2.0,
    window: int = 3,
) -> tuple[int, dict]:
    """
    Score (0-10) whether the path from current price to TP is clear of resistance.
    High score = clear path, low score = resistance walls in the way.
    """
    if len(candles) < window * 2 + 5:
        return 7, {
            "score": 7, "pass": True,
            "blocking_levels": [], "nearest_resistance": None,
            "swing_highs": [], "skipped": True,
        }

    # Exclude current (not-yet-closed) candle from S/R calculation
    price    = candles[-1]["close"]
    tp_price = price * (1 + tp_pct / 100)

    raw_highs   = _detect_swing_highs(candles[:-1], window=window)
    clustered   = _cluster_levels(raw_highs)

    # Resistance levels strictly between current price and TP (+0.3% buffer)
    blocking = [h for h in clustered if price < h <= tp_price * 1.003]

    # Nearest resistance above entry (for display)
    above   = [h for h in clustered if h > price]
    nearest = round(min(above), 2) if above else None

    score = _score_sr(blocking, price)

    return score, {
        "score":           score,
        "pass":            score >= 7,
        "price":           price,
        "tp_price":        round(tp_price, 2),
        "swing_highs":     [round(h, 2) for h in clustered[-10:]],
        "blocking_levels": [round(h, 2) for h in blocking],
        "nearest_resistance": nearest,
        "n_blockers":      len(blocking),
        "skipped":         False,
    }


# ── SHORT direction: support proximity ────────────────────────────────────────

def _detect_swing_lows(candles: list, window: int = 3) -> list[float]:
    """Mirror of _detect_swing_highs — fractal swing lows (local minima)."""
    lows = []
    n = len(candles)
    for i in range(window, n - window):
        l = candles[i]["low"]
        left_ok  = all(candles[j]["low"] > l for j in range(i - window, i))
        right_ok = all(candles[j]["low"] > l for j in range(i + 1, i + window + 1))
        if left_ok and right_ok:
            lows.append(l)
    return lows


def _score_sr_short(blocking: list[float], price: float) -> int:
    """For shorts: blocking = supports between current price and TP (below)."""
    if not blocking:
        return 10
    if len(blocking) >= 3:
        return 1
    nearest = max(blocking)             # closest support BELOW price
    gap_pct = (price - nearest) / price * 100
    if len(blocking) == 1:
        if gap_pct >= 1.5:
            return 7
        if gap_pct >= 1.0:
            return 5
        if gap_pct >= 0.5:
            return 3
        return 1
    else:
        if gap_pct >= 1.0:
            return 4
        return 2


def check_sr_proximity_short(
    candles: list,
    tp_pct: float = 2.0,
    window: int = 3,
) -> tuple[int, dict]:
    """
    Mirror of check_sr_proximity for shorts.
    TP is BELOW entry price; checks for support levels blocking the path down.
    """
    if len(candles) < window * 2 + 5:
        return 7, {"score": 7, "pass": True, "blocking_levels": [],
                   "nearest_support": None, "swing_lows": [], "skipped": True}

    price    = candles[-1]["close"]
    tp_price = price * (1 - tp_pct / 100)

    raw_lows  = _detect_swing_lows(candles[:-1], window=window)
    clustered = _cluster_levels(raw_lows)

    # Supports between TP and current price (with 0.3% buffer)
    blocking = [l for l in clustered if tp_price * 0.997 <= l < price]

    below   = [l for l in clustered if l < price]
    nearest = round(max(below), 2) if below else None

    score = _score_sr_short(blocking, price)

    return score, {
        "score":           score,
        "pass":            score >= 7,
        "price":           price,
        "tp_price":        round(tp_price, 2),
        "swing_lows":      [round(l, 2) for l in clustered[-10:]],
        "blocking_levels": [round(l, 2) for l in blocking],
        "nearest_support": nearest,
        "n_blockers":      len(blocking),
        "skipped":         False,
    }