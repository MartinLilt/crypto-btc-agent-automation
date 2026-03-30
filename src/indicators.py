import datetime

# ── Layer 1 — Volatility ──────────────────────────────────────────────────────

ATR_THRESHOLD = 500       # USD — absolute minimum ATR
ATR_MA_PERIOD = 30        # periods to compute ATR moving average
ATR_MA_MULTIPLIER = 1.2   # ATR must be > ATR_MA × this to confirm expansion
VOLUME_MA_PERIOD = 20     # periods for volume average
ADX_PERIOD = 14           # ADX smoothing period
# minimum ADX to confirm trend (not a sideways market)
ADX_MIN = 25


def calculate_atr(candles: list, period: int = 14) -> float:
    """Average True Range using Wilder's method."""
    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)
    if not true_ranges:
        return 0.0
    atr = sum(true_ranges[-period:]) / min(period, len(true_ranges))
    return round(atr, 2)


def calculate_adx(candles: list, period: int = ADX_PERIOD) -> float:
    """
    Average Directional Index — measures trend strength (not direction).
    Returns 0–100. Above 25 = trending market.
    """
    if len(candles) < period + 2:
        return 0.0

    plus_dm_list, minus_dm_list, tr_list = [], [], []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_high = candles[i - 1]["high"]
        prev_low = candles[i - 1]["low"]
        prev_close = candles[i - 1]["close"]

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm_list.append(
            up_move if up_move > down_move and up_move > 0 else 0
        )
        minus_dm_list.append(
            down_move if down_move > up_move and down_move > 0 else 0
        )
        tr_list.append(max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        ))

    def wilder_smooth(data, p):
        result = [sum(data[:p])]
        for v in data[p:]:
            result.append(result[-1] - result[-1] / p + v)
        return result

    atr_s = wilder_smooth(tr_list, period)
    plus_s = wilder_smooth(plus_dm_list, period)
    minus_s = wilder_smooth(minus_dm_list, period)

    dx_list = []
    for a, p, m in zip(atr_s, plus_s, minus_s):
        if a == 0:
            continue
        plus_di = 100 * p / a
        minus_di = 100 * m / a
        denom = plus_di + minus_di
        if denom == 0:
            continue
        dx_list.append(100 * abs(plus_di - minus_di) / denom)

    if not dx_list:
        return 0.0
    adx = sum(dx_list[-period:]) / min(period, len(dx_list))
    return round(adx, 2)


def is_market_moving(candles: list) -> tuple[bool, dict]:
    """
    Layer 1: Is the market moving with real momentum?
    Checks:
      - ATR > ATR_THRESHOLD (absolute floor $500)
      - ATR > 30-period ATR avg × 1.2 (volatility expanding)
      - Last candle volume > 20-period avg (volume confirmation)
      - ADX > 25 (not a sideways chop)
    Returns (signal, details_dict)
    """
    atr = calculate_atr(candles)
    adx = calculate_adx(candles)

    # Rolling ATR series to compute ATR's own moving average
    atr_series = []
    for i in range(1, len(candles)):
        window = candles[max(0, i - 14):i + 1]
        atr_series.append(calculate_atr(window))
    atr_ma = (
        sum(atr_series[-ATR_MA_PERIOD:]) / min(ATR_MA_PERIOD, len(atr_series))
        if atr_series else 0.0
    )
    atr_expanding = atr > atr_ma * ATR_MA_MULTIPLIER

    # Volume spike check
    volumes = [c["volume"] for c in candles if "volume" in c]
    vol_avg = (
        sum(volumes[-VOLUME_MA_PERIOD:]) / min(VOLUME_MA_PERIOD, len(volumes))
        if volumes else 0
    )
    last_vol = volumes[-1] if volumes else 0
    volume_spike = last_vol > vol_avg

    signal = (
        atr > ATR_THRESHOLD
        and atr_expanding
        and volume_spike
        and adx > ADX_MIN
    )

    details = {
        "atr": atr,
        "atr_ma": round(atr_ma, 2),
        "atr_expanding": atr_expanding,
        "volume_spike": volume_spike,
        "last_vol": round(last_vol, 4),
        "vol_avg": round(vol_avg, 4),
        "adx": adx,
    }
    return signal, details


# ── Layer 2 — Trend ───────────────────────────────────────────────────────────

EMA_FAST = 50
EMA_SLOW = 200
GOLDEN_CROSS_LOOKBACK = 10  # candles to look back for a recent golden cross


def calculate_ema(candles: list, period: int) -> list:
    """
    Exponential Moving Average of close prices.
    Returns list of EMA values (warm-up period excluded).
    """
    closes = [c["close"] for c in candles]
    if len(closes) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def is_uptrend(candles: list) -> tuple[bool, dict]:
    """
    Layer 2: Is the trend up? (long-only filter)
    Checks:
      - Price > EMA50 > EMA200 (structural uptrend)
      - EMA50 slope rising (now > 5 candles ago)
      - Recent Golden Cross (EMA50 crossed EMA200 within last 10 candles)
        OR established uptrend (EMA50 > EMA200 for last 5 candles)
    Requires 201+ candles.
    Returns (signal, details_dict)
    """
    ema50_series = calculate_ema(candles, EMA_FAST)
    ema200_series = calculate_ema(candles, EMA_SLOW)

    if not ema50_series or not ema200_series:
        return False, {"error": "not enough candles"}

    price = candles[-1]["close"]
    ema50 = ema50_series[-1]
    ema200 = ema200_series[-1]

    # EMA50 slope: rising if now > 5 candles ago
    ema50_slope_ok = (
        len(ema50_series) > 5 and ema50_series[-1] > ema50_series[-6]
    )

    # Align series (ema200 starts EMA_SLOW - EMA_FAST candles later)
    offset = EMA_SLOW - EMA_FAST
    aligned50 = ema50_series[offset:]

    # Check for recent Golden Cross
    recent_cross = False
    for i in range(-GOLDEN_CROSS_LOOKBACK, 0):
        try:
            prev_below = aligned50[i - 1] <= ema200_series[i - 1]
            now_above = aligned50[i] > ema200_series[i]
            if prev_below and now_above:
                recent_cross = True
                break
        except IndexError:
            break

    # Established uptrend: EMA50 > EMA200 for last 5 candles
    established = (
        len(aligned50) >= 5
        and len(ema200_series) >= 5
        and all(aligned50[i] > ema200_series[i] for i in range(-5, 0))
    )

    signal = (
        price > ema50 > ema200
        and ema50_slope_ok
        and (recent_cross or established)
    )

    details = {
        "price": price,
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "ema50_slope_ok": ema50_slope_ok,
        "golden_cross": recent_cross,
        "established_uptrend": established,
    }
    return signal, details


# ── Layer 3 — Momentum ────────────────────────────────────────────────────────

RSI_MIN = 40    # below this — fear/crash zone, skip
RSI_MAX = 65    # above this — overbought, skip
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL_PERIOD = 9


def calculate_rsi(candles: list, period: int = 14) -> float:
    """RSI using Wilder's smoothing. Returns 0–100."""
    closes = [c["close"] for c in candles]
    if len(closes) < period + 1:
        return 0.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_macd(candles: list) -> tuple[float, float, float]:
    """
    MACD = EMA12 - EMA26
    Signal = EMA9 of MACD line
    Histogram = MACD - Signal
    Returns (macd, signal_line, histogram).
    """
    ema12 = calculate_ema(candles, MACD_FAST)
    ema26 = calculate_ema(candles, MACD_SLOW)
    if not ema12 or not ema26:
        return 0.0, 0.0, 0.0

    offset = MACD_SLOW - MACD_FAST
    macd_line = [e12 - e26 for e12, e26 in zip(ema12[offset:], ema26)]

    if len(macd_line) < MACD_SIGNAL_PERIOD:
        return 0.0, 0.0, 0.0

    k = 2 / (MACD_SIGNAL_PERIOD + 1)
    sig = [sum(macd_line[:MACD_SIGNAL_PERIOD]) / MACD_SIGNAL_PERIOD]
    for v in macd_line[MACD_SIGNAL_PERIOD:]:
        sig.append(v * k + sig[-1] * (1 - k))

    macd_val = macd_line[-1]
    signal_val = sig[-1]
    return round(macd_val, 2), round(signal_val, 2), round(macd_val - signal_val, 2)


def is_not_overbought(candles: list) -> tuple[bool, dict]:
    """
    Layer 3: Is momentum healthy for a long entry?
    Checks:
      - RSI in [40, 65] — not fearful, not overbought
      - MACD histogram > 0 — bullish momentum confirmed
    Returns (signal, details_dict)
    """
    rsi = calculate_rsi(candles)
    macd, macd_sig, macd_hist = calculate_macd(candles)

    rsi_ok = RSI_MIN < rsi < RSI_MAX
    macd_ok = macd_hist > 0

    signal = rsi_ok and macd_ok

    details = {
        "rsi": rsi,
        "rsi_ok": rsi_ok,
        "macd": macd,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "macd_ok": macd_ok,
    }
    return signal, details


# ── Layer 4 — Timing ──────────────────────────────────────────────────────────

# Best UTC hours by trading session:
#   Asian open:  02–03
#   London open: 07–08
#   NY overlap:  13–15
#   NY evening:  20
GOOD_HOURS_UTC = {2, 3, 7, 8, 13, 14, 15, 20}

# Skip weekends — lower volume, higher manipulation risk
SKIP_WEEKDAYS = {5, 6}  # Saturday=5, Sunday=6


def is_good_hour() -> tuple[bool, dict]:
    """
    Layer 4: Is the timing right?
    Checks:
      - Current UTC hour is in a high-volume session window
      - Not a weekend
    Returns (signal, details_dict)
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    hour = now.hour
    weekday = now.weekday()

    hour_ok = hour in GOOD_HOURS_UTC
    weekday_ok = weekday not in SKIP_WEEKDAYS

    signal = hour_ok and weekday_ok

    details = {
        "hour_utc": hour,
        "weekday": now.strftime("%A"),
        "hour_ok": hour_ok,
        "weekday_ok": weekday_ok,
    }
    return signal, details


# ── Layer 5 — Liquidity ───────────────────────────────────────────────────────

MAX_SPREAD_USD = 10           # USD — max bid/ask spread
MIN_DEPTH_BTC = 1.0           # BTC — min order book depth each side within $50
MIN_VOLUME_24H = 500_000_000  # USD — minimum 24h trading volume


def has_liquidity(
    spread: float,
    bid_depth: float,
    ask_depth: float,
    volume_24h: float,
) -> tuple[bool, dict]:
    """
    Layer 5: Can we enter without slippage?
    Checks:
      - Spread < $10
      - Order book depth > 1 BTC on each side within $50 of price
      - 24h volume > $500M
    Returns (signal, details_dict)
    """
    spread_ok = 0 < spread < MAX_SPREAD_USD
    depth_ok = bid_depth >= MIN_DEPTH_BTC and ask_depth >= MIN_DEPTH_BTC
    volume_ok = volume_24h >= MIN_VOLUME_24H

    signal = spread_ok and depth_ok and volume_ok

    details = {
        "spread": spread,
        "spread_ok": spread_ok,
        "bid_depth_btc": bid_depth,
        "ask_depth_btc": ask_depth,
        "depth_ok": depth_ok,
        "volume_24h_usd": volume_24h,
        "volume_ok": volume_ok,
    }
    return signal, details


# ── Layer 6 — Risk / Reward ───────────────────────────────────────────────────

BINANCE_FEE_RATE = 0.001   # 0.1% per side (taker fee)
MIN_RR_RATIO = 1.5         # minimum reward:risk ratio to enter


def check_risk_reward(
    budget: float,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> tuple[bool, dict]:
    """
    Layer 6 — Risk / Reward gate.

    Calculates net P&L after Binance taker fees (0.1% each side).
    Passes when:
      1. Net profit > 0  (TP covers fees)
      2. Reward / Risk ratio >= MIN_RR_RATIO  (default 1.5)

    budget          : USDT amount per position
    take_profit_pct : TP percentage (e.g. 2.0 → +2 %)
    stop_loss_pct   : SL percentage (e.g. 1.0 → -1 %)
    """
    fee_open = budget * BINANCE_FEE_RATE
    fee_close = budget * BINANCE_FEE_RATE
    total_fee = fee_open + fee_close

    gross_profit = budget * (take_profit_pct / 100)
    gross_loss = budget * (stop_loss_pct / 100)

    net_profit = gross_profit - total_fee
    net_loss = gross_loss + total_fee

    rr_ratio = net_profit / net_loss if net_loss > 0 else 0.0

    profit_ok = net_profit > 0
    rr_ok = rr_ratio >= MIN_RR_RATIO
    signal = profit_ok and rr_ok

    return signal, {
        "budget":         round(budget, 2),
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct":   stop_loss_pct,
        "gross_profit":   round(gross_profit, 4),
        "gross_loss":     round(gross_loss,   4),
        "total_fee":      round(total_fee,    4),
        "net_profit":     round(net_profit,   4),
        "net_loss":       round(net_loss,     4),
        "rr_ratio":       round(rr_ratio,     2),
        "profit_ok":      profit_ok,
        "rr_ok":          rr_ok,
    }


# ── Layer 7: News Sentiment ───────────────────────────────────────────────────

def check_news_sentiment(news_summary: dict) -> tuple[bool, dict]:
    """
    Layer 7 — Recent news sentiment for the asset.

    news_summary is returned by src.news_client.summarise_news().
    Expected keys: total, bullish, bearish, neutral, important, score, headlines.

    Pass logic:
      - If no news fetched (total == 0) → pass with skipped=True (neutral stance)
      - Fail only when bearish articles are the clear majority (>50% of total)
      - Otherwise pass (mixed or bullish sentiment is acceptable)
    """
    if not news_summary or news_summary.get("total", 0) == 0:
        return True, {
            "total": 0,
            "bullish": 0,
            "bearish": 0,
            "neutral": 0,
            "important": 0,
            "score": 0.0,
            "headlines": [],
            "skipped": True,
        }

    total = news_summary["total"]
    bearish = news_summary["bearish"]
    bullish = news_summary.get("bullish", 0)
    important = news_summary.get("important", 0)
    score = news_summary.get("score", 0.0)
    headlines = news_summary.get("headlines", [])

    # Fail only if bearish articles are more than half
    passes = bearish < total * 0.5

    return passes, {
        "total": total,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": news_summary.get("neutral", 0),
        "important": important,
        "score": score,
        "headlines": headlines,
        "skipped": False,
    }


# ── Entry Signal (all 7 layers) ───────────────────────────────────────────────

def check_entry_signal(
    candles: list,
    spread: float,
    bid_depth: float,
    ask_depth: float,
    volume_24h: float,
    budget: float = 100.0,
    take_profit_pct: float = 2.0,
    stop_loss_pct: float = 1.0,
    news_summary: dict | None = None,
) -> tuple[bool, dict]:
    """
    Run all 7 entry decision layers.
    Returns (should_enter, report_dict).
    All 7 layers must pass for should_enter = True.

    candles:         201+ hourly OHLCV candles → get_candles(limit=201)
    spread:          bid/ask spread            → get_order_book_spread()
    bid_depth:       BTC depth on bid side     → get_order_book_depth()
    ask_depth:       BTC depth on ask side     → get_order_book_depth()
    volume_24h:      24h volume in USD         → get_ticker_24h()["volume_usd"]
    budget:          USDT per position         → cfg["budget"]
    take_profit_pct: TP %                      → cfg["take_profit_pct"]
    stop_loss_pct:   SL %                      → cfg["stop_loss_pct"]
    news_summary:    output of summarise_news()→ src.news_client
    """
    l1_ok, l1 = is_market_moving(candles)
    l2_ok, l2 = is_uptrend(candles)
    l3_ok, l3 = is_not_overbought(candles)
    l4_ok, l4 = is_good_hour()
    l5_ok, l5 = has_liquidity(spread, bid_depth, ask_depth, volume_24h)
    l6_ok, l6 = check_risk_reward(budget, take_profit_pct, stop_loss_pct)
    l7_ok, l7 = check_news_sentiment(news_summary or {})

    should_enter = all([l1_ok, l2_ok, l3_ok, l4_ok, l5_ok, l6_ok, l7_ok])

    report = {
        "should_enter": should_enter,
        "layers": {
            "L1_volatility":  {"pass": l1_ok, **l1},
            "L2_trend":       {"pass": l2_ok, **l2},
            "L3_momentum":    {"pass": l3_ok, **l3},
            "L4_timing":      {"pass": l4_ok, **l4},
            "L5_liquidity":   {"pass": l5_ok, **l5},
            "L6_risk_reward": {"pass": l6_ok, **l6},
            "L7_news":        {"pass": l7_ok, **l7},
        },
    }
    return should_enter, report
