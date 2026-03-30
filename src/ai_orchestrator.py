"""
AI Orchestration Layer — GPT reviews the 5-layer signal report
and makes the final entry decision with reasoning.

This is the meta-layer that sits above all technical indicators.
GPT acts as a senior trader reviewing the analyst's report.
"""

import os
import json
import logging
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is not set in .env"
            )
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


SYSTEM_PROMPT = """You are a professional but friendly trading assistant \
explaining market conditions to a regular person who is not a trader.

You receive a technical market analysis report with 7 layers of signals. \
For each layer write ONE short plain-English sentence that explains \
what is happening and whether it is good or bad for entering a trade right now. \
Then write a brief conclusion with the overall verdict.

Rules:
- Plain English only — no jargon, no abbreviations without explanation
- Each point is ONE sentence, direct and concrete, MAX 90 characters
- Use ✅ at the start of a point if that layer is positive, ❌ if negative
- Conclusion: max 120 characters — overall verdict and what to watch next
- Tone: calm, confident, like a trusted advisor
- Be honest — if conditions are poor, say so clearly

Respond ONLY with valid JSON in this exact format:
{
  "verdict": "WAIT" or "ENTER",
  "confidence": 0-100,
  "points": [
    "✅ or ❌ One sentence about market activity / volatility.",
    "✅ or ❌ One sentence about the price trend.",
    "✅ or ❌ One sentence about momentum.",
    "✅ or ❌ One sentence about timing.",
    "✅ or ❌ One sentence about liquidity.",
    "✅ or ❌ One sentence about risk/reward and fees.",
    "✅ or ❌ One sentence about recent news sentiment."
  ],
  "conclusion": "1-2 sentence overall verdict and what to watch for next."
}"""


def _build_user_message(symbol: str, price: float, report: dict) -> str:
    layers = report["layers"]
    l1 = layers["L1_volatility"]
    l2 = layers["L2_trend"]
    l3 = layers["L3_momentum"]
    l4 = layers["L4_timing"]
    l5 = layers["L5_liquidity"]
    l6 = layers["L6_risk_reward"]
    l7 = layers.get("L7_news", {})

    asset_name = "Bitcoin" if "BTC" in symbol else "Ethereum"
    price_vs_short = l2.get("ema50", 0)
    price_vs_long = l2.get("ema200", 0)
    trend_desc = (
        "above both its 50-day and 200-day average price (bullish structure)"
        if l2.get("pass")
        else (
            f"above its 50-day average (${price_vs_short:,.0f}) "
            f"but below its 200-day average (${price_vs_long:,.0f}) "
            f"— the big trend is still down"
            if price > price_vs_short
            else "below both its key moving averages — bearish"
        )
    )

    vol_desc = (
        "strong with expanding volatility and good volume"
        if l1["pass"]
        else (
            "low — the market is moving sideways without conviction "
            f"(trend strength ADX={l1['adx']:.0f}, needs >25)"
            if not l1["atr_expanding"]
            else "present but without enough volume confirmation"
        )
    )

    momentum_desc = (
        f"healthy — RSI is {l3['rsi']:.0f} (neutral zone) "
        f"and momentum indicators are bullish"
        if l3["pass"]
        else (
            f"RSI is {l3['rsi']:.0f} — "
            + ("overbought, risk of pullback" if l3['rsi'] >= 65
               else "in fear zone, market recovering")
        )
    )

    timing_desc = (
        f"good — {l4['weekday']} {l4['hour_utc']:02d}:00 UTC "
        f"is an active trading session"
        if l4["pass"]
        else (
            f"not ideal — {l4['weekday']} {l4['hour_utc']:02d}:00 UTC "
            f"is outside peak trading hours"
            if l4["weekday_ok"]
            else f"weekend ({l4['weekday']}) — lower volume, higher risk"
        )
    )

    liquidity_desc = (
        "excellent — tight spread, deep order book"
        if l5["pass"]
        else (
            f"order book is thin "
            f"(only {l5['bid_depth_btc']} BTC on the buy side) "
            f"— entering now risks slippage"
            if not l5["depth_ok"]
            else "spread is acceptable"
        )
    )

    rr_desc = (
        f"favourable — net profit ${l6['net_profit']:.2f} vs "
        f"net loss ${l6['net_loss']:.2f} after fees "
        f"(reward/risk ratio {l6['rr_ratio']:.2f}x)"
        if l6["pass"]
        else (
            f"poor — after Binance fees (${l6['total_fee']:.2f}), "
            f"net profit would only be ${l6['net_profit']:.2f} "
            f"vs potential loss of ${l6['net_loss']:.2f} "
            f"(reward/risk {l6['rr_ratio']:.2f}x, needs ≥1.5x)"
            if l6["profit_ok"]
            else
            f"trade not viable — fees alone (${l6['total_fee']:.2f}) "
            f"would eat the profit; "
            f"increase take-profit % or reduce budget"
        )
    )

    # L7 — News sentiment
    if l7.get("skipped") or l7.get("total", 0) == 0:
        news_desc = "no recent news found — neutral stance, cannot confirm macro context"
    else:
        total = l7["total"]
        bullish = l7.get("bullish", 0)
        bearish = l7.get("bearish", 0)
        neutral = l7.get("neutral", 0)
        score = l7.get("score", 0.0)
        headlines = l7.get("headlines", [])
        mood = (
            "mostly positive" if score > 0.2
            else "mostly negative" if score < -0.2
            else "mixed"
        )
        news_desc = (
            f"{mood} — {bullish} bullish, {bearish} bearish, "
            f"{neutral} neutral out of {total} articles in the last 24 h"
        )
        if headlines:
            news_desc += ". Recent headlines: " + " | ".join(
                f'"{h[:60]}"' for h in headlines[:2]
            )

    passed = sum(
        1 for v in [l1, l2, l3, l4, l5, l6, l7] if v.get("pass")
    )

    return f"""Asset: {asset_name} ({symbol})
Current price: ${price:,.2f}
Position size: ${l6['budget']:.2f} USDT
Take-profit at: +{l6['take_profit_pct']}%  →  gross +${l6['gross_profit']:.2f}
Stop-loss at:   -{l6['stop_loss_pct']}%   →  gross -${l6['gross_loss']:.2f}
Exchange fees:  ${l6['total_fee']:.2f} (0.1% each side)

Here is what the 7 analysis checks found:

1. Market activity: {vol_desc}
2. Price trend: {asset_name} is currently {trend_desc}
3. Momentum: {momentum_desc}
4. Timing: {timing_desc}
5. Liquidity: {liquidity_desc}
6. Risk/Reward: {rr_desc}
7. News sentiment: {news_desc}

Overall: {passed} out of 7 checks passed.
System recommendation: {"ENTER" if report["should_enter"] else "WAIT"}

Please write a short, plain-English verdict for a regular person."""


def translate_to_russian(points: list, conclusion: str) -> tuple:
    """
    Translate AI verdict points and conclusion to Russian.
    Returns (translated_points list, translated_conclusion str).
    Separate lightweight GPT call — no analysis, just translation.
    """
    client = _get_client()
    logger.info("Translating AI verdict to Russian...")

    # Build a single block to translate in one call
    combined = "\n".join(
        [f"{i+1}. {p}" for i, p in enumerate(points)]
        + [f"ВЫВОД: {conclusion}"]
    )

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Переведи следующий текст на русский язык. "
                    "Сохраняй ту же структуру нумерации и эмодзи. "
                    "Не добавляй пояснений — только перевод."
                ),
            },
            {"role": "user", "content": combined},
        ],
        temperature=0.1,
        max_tokens=600,
    )

    translated = response.choices[0].message.content.strip()
    lines = translated.splitlines()

    # Split back into points and conclusion
    tr_points = []
    tr_conclusion = conclusion  # fallback
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("ВЫВОД:") or line.upper().startswith("ВЫВОД "):
            tr_conclusion = line.split(":", 1)[-1].strip()
        elif line[0].isdigit() and line[1:3] in (". ", ") "):
            tr_points.append(line[3:].strip())
        else:
            # fallback — just append
            if len(tr_points) < 5:
                tr_points.append(line)

    # Pad if translation came back short
    while len(tr_points) < len(points):
        tr_points.append(points[len(tr_points)])

    return tr_points, tr_conclusion


def ai_review(
    symbol: str,
    price: float,
    report: dict,
) -> dict:
    """
    Send the 5-layer report to GPT for a structured verdict.

    Returns dict:
    {
        "verdict":    "ENTER" | "WAIT",
        "confidence": int 0-100,
        "points":     list of 5 plain-English sentences (one per layer),
        "conclusion": str  (1-2 sentence overall verdict),
        "raw":        str  (raw GPT response for debugging)
    }
    """
    client = _get_client()
    user_msg = _build_user_message(symbol, price, report)

    logger.info("Sending market report to GPT (%s)...", OPENAI_MODEL)

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=600,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    logger.info("GPT response: %s", raw)

    try:
        parsed = json.loads(raw)
        points = parsed.get("points", [])
        # Fallback: if GPT still returns summary instead of points
        if not points and parsed.get("summary"):
            points = [parsed["summary"]]
        return {
            "verdict":    parsed.get("verdict", "WAIT").upper(),
            "confidence": int(parsed.get("confidence", 0)),
            "points":     points,
            "conclusion": parsed.get("conclusion", ""),
            "raw":        raw,
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse GPT response: %s", e)
        return {
            "verdict":    "WAIT",
            "confidence": 0,
            "points":     [],
            "conclusion": f"AI analysis unavailable: {e}",
            "raw":        raw,
        }
