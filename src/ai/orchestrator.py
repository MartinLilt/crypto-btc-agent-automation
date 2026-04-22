"""
AI Orchestration Layer — local LLM via Ollama reviews the 10-layer signal report.

Uses Ollama HTTP API (no cloud, no API key needed).
Default model: qwen2.5:3b (~2GB RAM, runs on CPU, knows EN+RU).

Env vars:
  OLLAMA_HOST  — default http://localhost:11434 (http://ollama:11434 in Docker)
  OLLAMA_MODEL — default qwen2.5:3b
"""

import json
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
_TIMEOUT     = 90  # seconds — local inference can be slow on CPU


SYSTEM_PROMPT_EN = """You are a professional but friendly trading assistant \
explaining market conditions to a regular person who is not a trader.

You receive a technical market analysis report with 10 layers of signals. \
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
    "✅ or ❌ One sentence about volume trend.",
    "✅ or ❌ One sentence about liquidity.",
    "✅ or ❌ One sentence about risk/reward and fees.",
    "✅ or ❌ One sentence about recent news sentiment.",
    "✅ or ❌ One sentence about support/resistance levels blocking the target.",
    "✅ or ❌ One sentence about the candlestick pattern on the last 3 candles.",
    "✅ or ❌ One sentence about buyer vs seller pressure in the last 6h."
  ],
  "conclusion": "1-2 sentence overall verdict and what to watch for next."
}"""


SYSTEM_PROMPT_RU = """Ты профессиональный, но дружелюбный торговый ассистент, \
объясняющий рыночные условия обычному человеку без опыта трейдинга.

Ты получаешь технический отчёт с 10 слоями сигналов. \
По каждому слою напиши ОДНО короткое предложение: \
что происходит и хорошо ли это для входа в сделку прямо сейчас. \
Затем напиши краткий вывод с общим вердиктом.

Правила:
- Только простой русский язык — никаких непонятных аббревиатур без объяснений
- Каждый пункт — ОДНО предложение, конкретное, максимум 90 символов
- Начинай пункт с ✅ если слой позитивный, ❌ если негативный
- Вывод: максимум 120 символов — общий вердикт и на что смотреть дальше
- Тон: спокойный, уверенный, как доверенный советник
- Будь честен — если условия плохие, скажи прямо

Отвечай ТОЛЬКО валидным JSON в точно таком формате:
{
  "verdict": "WAIT" или "ENTER",
  "confidence": 0-100,
  "points": [
    "✅ или ❌ Одно предложение об активности рынка / волатильности.",
    "✅ или ❌ Одно предложение о тренде цены.",
    "✅ или ❌ Одно предложение об импульсе.",
    "✅ или ❌ Одно предложение о тренде объёма.",
    "✅ или ❌ Одно предложение о ликвидности.",
    "✅ или ❌ Одно предложение о соотношении риск/доход и комиссиях.",
    "✅ или ❌ Одно предложение о свежих новостях.",
    "✅ или ❌ Одно предложение об уровнях поддержки/сопротивления.",
    "✅ или ❌ Одно предложение о свечном паттерне на последних 3 свечах.",
    "✅ или ❌ Одно предложение о давлении покупателей vs продавцов за 6 часов."
  ],
  "conclusion": "1-2 предложения: общий вердикт и на что смотреть."
}"""


def _build_user_message(symbol: str, price: float, report: dict) -> str:
    layers = report["layers"]
    l1  = layers["L1_volatility"]
    l2  = layers["L2_trend"]
    l3  = layers["L3_momentum"]
    l4  = layers["L4_vol_trend"]
    l5  = layers["L5_liquidity"]
    l6  = layers["L6_risk_reward"]
    l7  = layers.get("L7_news", {})
    l8  = layers.get("L8_sr_proximity", {})
    l9  = layers.get("L9_candle_pattern", {})
    l10 = layers.get("L10_pressure", {})

    price_vs_short = l2.get("ema50", 0)
    price_vs_long  = l2.get("ema200", 0)
    trend_desc = (
        "above both 50 and 200 EMA (bullish structure)"
        if l2.get("pass") else
        f"above EMA50 (${price_vs_short:,.0f}) but below EMA200 (${price_vs_long:,.0f})"
        if price > price_vs_short else
        "below both key moving averages (bearish)"
    )

    vol_desc = (
        f"strong — ADX={l1['adx']:.0f}, ATR expanding, good volume"
        if l1["pass"] else
        f"weak — ADX={l1['adx']:.0f} (needs >25), sideways market"
    )

    momentum_desc = (
        f"healthy — RSI={l3['rsi']:.0f} (neutral), MACD bullish"
        if l3["pass"] else
        f"RSI={l3['rsi']:.0f} — {'overbought' if l3['rsi'] >= 65 else 'oversold'}"
    )

    ratio = l4.get("ratio", 1.0)
    timing_desc = (
        f"volume {ratio:.2f}× 24h average — elevated participation"
        if l4["pass"] else
        f"volume only {ratio:.2f}× avg — thin market"
    )

    liquidity_desc = (
        "tight spread, deep order book"
        if l5["pass"] else
        f"spread/depth issues — slippage risk"
    )

    rr_desc = (
        f"RR={l6['rr_ratio']:.2f}x, net profit ${l6['net_profit']:.2f} after fees"
        if l6["pass"] else
        f"poor RR={l6['rr_ratio']:.2f}x — fees eat profit"
    )

    if l7.get("skipped") or l7.get("total", 0) == 0:
        news_desc = "no recent news — neutral"
    else:
        mood = (
            "mostly positive" if l7.get("score", 0) > 0.2 else
            "mostly negative" if l7.get("score", 0) < -0.2 else "mixed"
        )
        news_desc = (
            f"{mood} — {l7.get('bullish',0)} bullish, "
            f"{l7.get('bearish',0)} bearish of {l7['total']} articles"
        )

    n_blockers = l8.get("n_blockers", 0)
    sr_desc = (
        f"path to TP (${l8.get('tp_price',0):,.0f}) is clear"
        if n_blockers == 0 else
        f"{n_blockers} resistance levels blocking TP (nearest ${l8.get('nearest_resistance',0):,.0f})"
    )

    pattern  = l9.get("pattern", "UNKNOWN").replace("_", " ").title()
    body_pct = l9.get("body_pct", 0)
    candle_desc = f"{pattern} — body {body_pct:.0f}% of range"

    buy_ratio = l10.get("buy_ratio_pct", 50.0)
    net_btc   = l10.get("net_btc", 0.0)
    pressure_desc = (
        f"last 6h: buyers {buy_ratio:.1f}% of volume, "
        f"net {net_btc:+,.0f} BTC taker flow"
    )

    passed = sum(1 for v in [l1,l2,l3,l4,l5,l6,l7,l8,l9,l10] if v.get("pass"))

    return (
        f"Asset: {symbol}\n"
        f"Price: ${price:,.2f}\n"
        f"Budget/trade: ${l6['budget']:.0f}  TP: +{l6['take_profit_pct']}%  SL: -{l6['stop_loss_pct']}%\n\n"
        f"1. Volatility/Activity: {vol_desc}\n"
        f"2. Trend: {trend_desc}\n"
        f"3. Momentum: {momentum_desc}\n"
        f"4. Volume trend: {timing_desc}\n"
        f"5. Liquidity: {liquidity_desc}\n"
        f"6. Risk/Reward: {rr_desc}\n"
        f"7. News: {news_desc}\n"
        f"8. Support/Resistance: {sr_desc}\n"
        f"9. Candle pattern: {candle_desc}\n"
        f"10. Buy pressure: {pressure_desc}\n\n"
        f"Passed: {passed}/10  System verdict: {'ENTER' if report['should_enter'] else 'WAIT'}\n\n"
        "Write your verdict as JSON."
    )


def _is_available() -> bool:
    """Quick check if Ollama is reachable."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def ai_review_simulation(
    symbol: str,
    stats: dict,
    budget: float,
    days: int,
    lang: str = "en",
) -> str:
    """
    Ask local LLM to comment on simulation results in plain language.
    Returns a plain text paragraph (2-4 sentences). Empty string on failure.
    """
    if not _is_available():
        return ""

    wr         = stats.get("win_rate_pct", 0)
    signals    = stats.get("total_signals", 0)
    after_tax  = stats.get("total_pnl_after_tax_pct", 0) * budget / 100
    net_fees   = stats.get("total_pnl_net_fees_pct", 0)  * budget / 100
    be_fees    = stats.get("breakeven_wr_fees", 40.0)
    be_tax     = stats.get("breakeven_wr_tax",  44.0)
    max_dd     = stats.get("max_drawdown_pct", 0)
    sharpe     = stats.get("sharpe_ratio", 0)
    wins       = stats.get("wins", 0)
    losses     = stats.get("losses", 0)
    timeouts   = stats.get("timeouts", 0)
    annual_usd = after_tax * (365 / max(days, 1))

    if lang == "ru":
        system = (
            "Ты опытный трейдер, объясняющий результаты бэктеста обычному человеку. "
            "Дай честную практическую оценку в 2-4 предложениях на русском языке. "
            "Без жаргона. Скажи: стратегия прибыльна или нет, безопасен ли такой бюджет, "
            "что может пойти не так, стоит ли запускать вживую."
        )
        user = (
            f"Актив: {symbol}, период: {days} дней, капитал/сделку: ${budget:.0f}\n"
            f"Сигналов: {signals} ({wins} прибыльных / {losses} убыточных / {timeouts} таймаут)\n"
            f"Win rate: {wr:.1f}%  (безубыток с комис.: {be_fees:.1f}%, с налогом: {be_tax:.1f}%)\n"
            f"Итого после комиссий и налога 15% за {days}д: ${net_fees:+.2f} → ${after_tax:+.2f}\n"
            f"Прогноз на год: ${annual_usd:+.0f}\n"
            f"Max просадка: {max_dd:.1f}%  Sharpe: {sharpe:.2f}\n\n"
            "Дай короткую практическую оценку."
        )
    else:
        system = (
            "You are an experienced trader explaining backtest results to a regular person. "
            "Give an honest practical assessment in 2-4 sentences in English. "
            "No jargon. State: is the strategy profitable, is the budget safe, "
            "what can go wrong, is it worth running live."
        )
        user = (
            f"Asset: {symbol}, period: {days} days, capital/trade: ${budget:.0f}\n"
            f"Signals: {signals} ({wins} wins / {losses} losses / {timeouts} timeouts)\n"
            f"Win rate: {wr:.1f}%  (break-even with fees: {be_fees:.1f}%, with tax: {be_tax:.1f}%)\n"
            f"Total after fees + 15% LT tax over {days}d: ${net_fees:+.2f} → ${after_tax:+.2f}\n"
            f"Projected annual: ${annual_usd:+.0f}\n"
            f"Max drawdown: {max_dd:.1f}%  Sharpe: {sharpe:.2f}\n\n"
            "Give a short practical assessment."
        )

    logger.info("Requesting simulation AI commentary (%s)...", OLLAMA_MODEL)
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model":   OLLAMA_MODEL,
                "stream":  False,
                "options": {"temperature": 0.4, "num_predict": 300},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json()["message"]["content"].strip()
        logger.info("Simulation AI commentary received (%d chars)", len(text))
        return text
    except Exception as e:
        logger.warning("Simulation AI commentary failed: %s", e)
        return ""


def ai_review(
    symbol: str,
    price: float,
    report: dict,
    lang: str = "en",
) -> dict:
    """
    Send the 10-layer report to local Ollama LLM for a structured verdict.

    Returns dict with: verdict, confidence, points (list), conclusion, raw.
    Falls back gracefully if Ollama is unavailable.
    """
    if not _is_available():
        logger.warning("Ollama not available at %s — skipping AI review", OLLAMA_HOST)
        return {"verdict": "WAIT", "confidence": 0, "points": [], "conclusion": "", "raw": ""}

    system_prompt = SYSTEM_PROMPT_RU if lang == "ru" else SYSTEM_PROMPT_EN
    user_msg      = _build_user_message(symbol, price, report)

    logger.info("Sending report to Ollama (%s)...", OLLAMA_MODEL)

    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model":    OLLAMA_MODEL,
                "stream":   False,
                "format":   "json",
                "options":  {"temperature": 0.3, "num_predict": 700},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_msg},
                ],
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"]
        logger.info("Ollama response received (%d chars)", len(raw))
    except Exception as e:
        logger.error("Ollama request failed: %s", e)
        return {"verdict": "WAIT", "confidence": 0, "points": [], "conclusion": str(e), "raw": ""}

    try:
        parsed = json.loads(raw)
        return {
            "verdict":    parsed.get("verdict", "WAIT").upper(),
            "confidence": int(parsed.get("confidence", 0)),
            "points":     parsed.get("points", []),
            "conclusion": parsed.get("conclusion", ""),
            "raw":        raw,
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse Ollama response: %s\n%s", e, raw[:300])
        return {"verdict": "WAIT", "confidence": 0, "points": [], "conclusion": "", "raw": raw}