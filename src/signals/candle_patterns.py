"""
Candlestick pattern detection (L9).

Checks the last 3 candles for bullish/bearish patterns.
Returns score 0-10 with pattern name and description.

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


def detect_candle_patterns(candles: list) -> tuple[int, dict]:
    """
    Detect the dominant pattern on the last 3 candles.
    Returns (score: int, details: dict).
    """
    if len(candles) < 3:
        return 5, {
            "score": 5, "pass": False,
            "pattern": "NEUTRAL", "description": "insufficient candle data",
            "skipped": True,
        }

    c0 = _props(candles[-3])   # oldest of 3
    c1 = _props(candles[-2])   # previous candle
    c2 = _props(candles[-1])   # current candle

    # ── Bullish patterns ──────────────────────────────────────────────────────

    # Strong bullish candle: body ≥ 70% of range, closes in top 15%, dominates previous
    if (c2["bull"]
            and c2["body_pct"] >= 0.70
            and c2["upper_w"] <= c2["range"] * 0.15
            and c2["body"] >= c1["body"] * 1.3):
        return 10, _result(10, "STRONG_BULL",
                           "Large bullish candle — buyers fully in control", candles[-1])

    # Bullish engulfing: current green candle's body fully covers previous red body
    if (c2["bull"] and not c1["bull"]
            and candles[-1]["open"] <= candles[-2]["close"]
            and candles[-1]["close"] >= candles[-2]["open"]
            and c2["body"] > c1["body"]):
        return 9, _result(9, "BULLISH_ENGULFING",
                          "Buyers absorbed all sellers — strong reversal signal", candles[-1])

    # Hammer: small body at top, long lower wick (≥2× body), tiny upper wick
    if (c2["lower_w"] >= c2["body"] * 2.0
            and c2["upper_w"] <= c2["body"] * 0.5
            and c2["body_pct"] < 0.45):
        return 8, _result(8, "HAMMER",
                          "Hammer — sellers tried to push lower but buyers rejected it", candles[-1])

    # Morning star: bearish → small doji → bullish closing above midpoint of first candle
    midpoint_c0 = (candles[-3]["open"] + candles[-3]["close"]) / 2
    if (not c0["bull"] and c0["body_pct"] >= 0.50
            and c1["body_pct"] <= 0.30
            and c2["bull"] and candles[-1]["close"] >= midpoint_c0):
        return 8, _result(8, "MORNING_STAR",
                          "Morning star — 3-candle reversal, buyers taking back control", candles[-1])

    # Moderate bullish: decent bullish body ≥ 45% of range
    if c2["bull"] and c2["body_pct"] >= 0.45:
        return 6, _result(6, "BULLISH",
                          "Bullish candle — buyers slightly in control", candles[-1])

    # ── Neutral ───────────────────────────────────────────────────────────────

    # Doji (indecision): very small body
    if c2["body_pct"] <= 0.15:
        return 5, _result(5, "DOJI",
                          "Doji — market indecision, neither buyers nor sellers winning", candles[-1])

    # Weak bullish
    if c2["bull"]:
        return 5, _result(5, "NEUTRAL_BULL",
                          "Weakly bullish candle — no strong conviction", candles[-1])

    # ── Bearish patterns ──────────────────────────────────────────────────────

    # Shooting star: small body at bottom, long upper wick (≥2× body)
    if (c2["upper_w"] >= c2["body"] * 2.0
            and c2["lower_w"] <= c2["body"] * 0.5):
        return 2, _result(2, "SHOOTING_STAR",
                          "Shooting star — sellers rejected the highs, reversal risk", candles[-1])

    # Bearish engulfing: previous green candle fully covered by current red
    if (not c2["bull"] and c1["bull"]
            and candles[-1]["open"] >= candles[-2]["close"]
            and candles[-1]["close"] <= candles[-2]["open"]
            and c2["body"] > c1["body"]):
        return 1, _result(1, "BEARISH_ENGULFING",
                          "Bearish engulfing — sellers overwhelmed prior buyers, avoid entry", candles[-1])

    # Default bearish
    return 3, _result(3, "BEARISH",
                      "Bearish candle — sellers have slight advantage", candles[-1])


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