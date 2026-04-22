"""
Candlestick pattern detection (L9).

Checks the last 3 candles for bullish/bearish patterns.
Optionally cross-checks against 4h candles (2× weight).
Also penalises overextended bull streaks (5+ green candles in a row).

Score guide:
  9-10  Strong bullish pattern — high conviction entry signal
  7-8   Moderate bullish pattern — entry likely
  5-6   Neutral / no clear pattern
  3-4   Minor bearish signal — caution
  1-2   Strong bearish reversal — avoid entry

Used for both live analysis and backtest (pure candle computation, no external data).
"""

from __future__ import annotations


def _props(c: dict) -> dict:
    """Extract candle body/wick metrics."""
    body     = abs(c["close"] - c["open"])
    rng      = c["high"] - c["low"] or 1e-9
    upper_w  = c["high"] - max(c["close"], c["open"])
    lower_w  = min(c["close"], c["open"]) - c["low"]
    bull     = c["close"] >= c["open"]
    body_pct = body / rng
    return {
        "body": body, "range": rng,
        "upper_w": upper_w, "lower_w": lower_w,
        "bull": bull, "body_pct": body_pct,
    }


def _count_bull_streak(candles: list) -> int:
    """Count consecutive bullish (green) candles from the most recent."""
    count = 0
    for c in reversed(candles[-10:]):
        if c["close"] >= c["open"]:
            count += 1
        else:
            break
    return count


def _detect_raw(candles: list) -> tuple[int, dict]:
    """Core pattern detection on the last 3 candles. Returns (score, details)."""
    if len(candles) < 3:
        return 5, {
            "score": 5, "pass": False,
            "pattern": "NEUTRAL", "description": "insufficient candle data",
            "skipped": True,
        }

    c0 = _props(candles[-3])
    c1 = _props(candles[-2])
    c2 = _props(candles[-1])

    # ── Bullish ───────────────────────────────────────────────────────────────

    if (c2["bull"]
            and c2["body_pct"] >= 0.70
            and c2["upper_w"] <= c2["range"] * 0.15
            and c2["body"] >= c1["body"] * 1.3):
        return 10, _result(10, "STRONG_BULL",
                           "Large bullish candle — buyers fully in control", candles[-1])

    if (c2["bull"] and not c1["bull"]
            and candles[-1]["open"] <= candles[-2]["close"]
            and candles[-1]["close"] >= candles[-2]["open"]
            and c2["body"] > c1["body"]):
        return 9, _result(9, "BULLISH_ENGULFING",
                          "Buyers absorbed all sellers — strong reversal signal", candles[-1])

    if (c2["lower_w"] >= c2["body"] * 2.0
            and c2["upper_w"] <= c2["body"] * 0.5
            and c2["body_pct"] < 0.45):
        return 8, _result(8, "HAMMER",
                          "Hammer — sellers tried to push lower but buyers rejected it", candles[-1])

    midpoint_c0 = (candles[-3]["open"] + candles[-3]["close"]) / 2
    if (not c0["bull"] and c0["body_pct"] >= 0.50
            and c1["body_pct"] <= 0.30
            and c2["bull"] and candles[-1]["close"] >= midpoint_c0):
        return 8, _result(8, "MORNING_STAR",
                          "Morning star — 3-candle reversal, buyers taking back control", candles[-1])

    if c2["bull"] and c2["body_pct"] >= 0.45:
        return 6, _result(6, "BULLISH",
                          "Bullish candle — buyers slightly in control", candles[-1])

    # ── Neutral ───────────────────────────────────────────────────────────────

    if c2["body_pct"] <= 0.15:
        return 5, _result(5, "DOJI",
                          "Doji — market indecision, neither buyers nor sellers winning", candles[-1])

    if c2["bull"]:
        return 5, _result(5, "NEUTRAL_BULL",
                          "Weakly bullish candle — no strong conviction", candles[-1])

    # ── Bearish ───────────────────────────────────────────────────────────────

    if (c2["upper_w"] >= c2["body"] * 2.0
            and c2["lower_w"] <= c2["body"] * 0.5):
        return 2, _result(2, "SHOOTING_STAR",
                          "Shooting star — sellers rejected the highs, reversal risk", candles[-1])

    if (not c2["bull"] and c1["bull"]
            and candles[-1]["open"] >= candles[-2]["close"]
            and candles[-1]["close"] <= candles[-2]["open"]
            and c2["body"] > c1["body"]):
        return 1, _result(1, "BEARISH_ENGULFING",
                          "Bearish engulfing — sellers overwhelmed prior buyers, avoid entry", candles[-1])

    return 3, _result(3, "BEARISH",
                      "Bearish candle — sellers have slight advantage", candles[-1])


def detect_candle_patterns(
    candles: list,
    candles_4h: list | None = None,
) -> tuple[int, dict]:
    """
    Detect dominant pattern on last 3 candles.
    If candles_4h provided, 4h pattern has 2× weight in the combined score.
    Consecutive bull streak of 5+ penalises the score (exhausted move).
    Returns (score: int, details: dict).
    """
    score_1h, result = _detect_raw(candles)

    # Consecutive bull candle penalty — move likely exhausted
    bull_streak = _count_bull_streak(candles)
    streak_penalty = 0
    if bull_streak >= 8:
        streak_penalty = -2
    elif bull_streak >= 5:
        streak_penalty = -1
    score_1h = max(0, score_1h + streak_penalty)

    # 4h candle pattern — higher-timeframe confirmation (2× weight)
    tf4h_score = None
    tf4h_pattern = None
    if candles_4h and len(candles_4h) >= 3:
        tf4h_score, result_4h = _detect_raw(candles_4h)
        tf4h_pattern = result_4h.get("pattern")
        combined = min(10, max(0, round((score_1h + tf4h_score * 2) / 3)))
    else:
        combined = score_1h

    result["score"]        = combined
    result["pass"]         = combined >= 7
    result["bull_streak"]  = bull_streak
    result["streak_penalty"] = streak_penalty
    result["tf4h_score"]   = tf4h_score
    result["tf4h_pattern"] = tf4h_pattern
    result["skipped"]      = False
    return combined, result


def _result(score: int, pattern: str, description: str, candle: dict) -> dict:
    rng = candle["high"] - candle["low"] or 1e-9
    return {
        "score":       score,
        "pass":        score >= 7,
        "pattern":     pattern,
        "description": description,
        "c_open":      round(candle["open"],  2),
        "c_close":     round(candle["close"], 2),
        "c_high":      round(candle["high"],  2),
        "c_low":       round(candle["low"],   2),
        "body_pct":    round(abs(candle["close"] - candle["open"]) / rng * 100, 1),
        "skipped":     False,
    }