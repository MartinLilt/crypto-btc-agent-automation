import datetime

from src.signals.support_resistance import check_sr_proximity
from src.signals.candle_patterns import detect_candle_patterns

# ── Thresholds ────────────────────────────────────────────────────────────────

ATR_THRESHOLD = 500
ATR_MA_PERIOD = 30
ATR_MA_MULTIPLIER = 1.2
VOLUME_MA_PERIOD = 20
SPIKE_PERIOD = 20   # L4: SMA period for volume spike detection
ADX_PERIOD = 14
ADX_MIN = 25

EMA_FAST = 50
EMA_SLOW = 200
GOLDEN_CROSS_LOOKBACK = 10

RSI_MIN = 40
RSI_MAX = 65
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL_PERIOD = 9

GOOD_HOURS_UTC = {2, 3, 7, 8, 13, 14, 15, 20}
PEAK_HOURS_UTC = {13, 14, 15}
SKIP_WEEKDAYS = {5, 6}

MAX_SPREAD_USD = 10
MIN_DEPTH_BTC = 1.0
MIN_VOLUME_24H = 500_000_000

BINANCE_FEE_RATE = 0.001
MIN_RR_RATIO = 1.5

FUNDING_RATE_MAX = 0.05
FUNDING_RATE_MIN = -0.05
OI_CHANGE_MIN = -3.0

FG_MAX = 74
FG_MIN = 15

BUY_RATIO_MIN = 45.0
NET_SELL_MAX = -500.0

# Entry requires total score >= this (out of 100)
ENTRY_SCORE_THRESHOLD = 70


# ── Score helpers (0-10 per layer) ────────────────────────────────────────────

def _score_icon(score: int) -> str:
    if score >= 7:
        return "🟢"
    if score >= 4:
        return "🟡"
    return "🔴"


def _score_l1(adx: float, atr_expanding: bool, volume_spike: bool) -> int:
    if adx >= 30:
        adx_pts = 6
    elif adx >= 25:
        adx_pts = 5
    elif adx >= 20:
        adx_pts = 4
    elif adx >= 15:
        adx_pts = 2
    else:
        adx_pts = 0
    return min(10, adx_pts + (2 if atr_expanding else 0) + (2 if volume_spike else 0))


def _score_l2(price: float, ema50: float, ema200: float,
              slope_ok: bool, golden_cross: bool, established: bool) -> int:
    if price > ema50 > ema200 and slope_ok and (golden_cross or established):
        return 10
    if price > ema50 > ema200 and slope_ok:
        return 8
    if price > ema50 > ema200:
        return 6
    if price > ema50:
        return 4
    if ema200 > 0 and price > ema200:
        return 2
    return 0


def _score_l3(rsi: float, macd_hist: float) -> int:
    if 50 <= rsi <= 60:
        rsi_pts = 5
    elif (45 <= rsi < 50) or (60 < rsi <= 65):
        rsi_pts = 4
    elif 40 <= rsi < 45:
        rsi_pts = 3
    else:
        rsi_pts = 0
    if macd_hist > 20:
        macd_pts = 5
    elif macd_hist > 0:
        macd_pts = 3
    elif macd_hist > -10:
        macd_pts = 1
    else:
        macd_pts = 0
    return min(10, rsi_pts + macd_pts)


def _score_l4_vol_trend(ratio: float) -> int:
    if ratio >= 1.5:
        return 10
    if ratio >= 1.2:
        return 8
    if ratio >= 0.8:
        return 6
    if ratio >= 0.5:
        return 3
    return 1


def _score_l5(spread: float, depth_ok: bool, volume_24h: float) -> int:
    if volume_24h >= 2_000_000_000:
        vol_pts = 5
    elif volume_24h >= 1_000_000_000:
        vol_pts = 4
    elif volume_24h >= 500_000_000:
        vol_pts = 3
    elif volume_24h >= 200_000_000:
        vol_pts = 2
    else:
        vol_pts = 0
    if 0 < spread < 2:
        spread_pts = 3
    elif spread < 5:
        spread_pts = 2
    elif spread < 10:
        spread_pts = 1
    else:
        spread_pts = 0
    return min(10, vol_pts + spread_pts + (2 if depth_ok else 0))


def _score_l6(rr_ratio: float, net_profit: float) -> int:
    if net_profit <= 0:
        return 0
    if rr_ratio >= 3.0:
        return 10
    if rr_ratio >= 2.5:
        return 9
    if rr_ratio >= 2.0:
        return 8
    if rr_ratio >= 1.75:
        return 7
    if rr_ratio >= 1.5:
        return 6
    if rr_ratio >= 1.25:
        return 4
    if rr_ratio >= 1.0:
        return 2
    return 0


def _score_l7(total: int, bearish: int, score_val: float) -> int:
    if total == 0:
        return 5
    if bearish >= total * 0.5:
        return max(0, int((1 - bearish / total) * 4))
    raw = int((score_val + 1) * 4.5) + 1
    return max(1, min(10, raw))


def _score_l8(funding_rate: float, oi_change_pct: float, skipped: bool) -> int:
    if skipped:
        return 7
    fr_abs = abs(funding_rate)
    if fr_abs < 0.01:
        fr_pts = 6
    elif fr_abs < 0.02:
        fr_pts = 5
    elif fr_abs < 0.03:
        fr_pts = 4
    elif fr_abs < 0.05:
        fr_pts = 2
    else:
        fr_pts = 0
    if oi_change_pct > 2:
        oi_pts = 4
    elif oi_change_pct > 0:
        oi_pts = 3
    elif oi_change_pct > -3:
        oi_pts = 2
    else:
        oi_pts = 0
    return min(10, fr_pts + oi_pts)


def _score_l9(value: int, skipped: bool) -> int:
    if skipped:
        return 5
    if 35 <= value <= 55:
        return 10
    if 25 <= value <= 65:
        return 8
    if 15 <= value <= 74:
        return 6
    if value < 15:
        return 2
    return 1


def _score_l10(buy_ratio: float, net_btc: float, skipped: bool) -> int:
    if skipped:
        return 5
    if buy_ratio >= 60:
        ratio_pts = 6
    elif buy_ratio >= 55:
        ratio_pts = 5
    elif buy_ratio >= 50:
        ratio_pts = 4
    elif buy_ratio >= 45:
        ratio_pts = 2
    else:
        ratio_pts = 0
    if net_btc > 1000:
        net_pts = 4
    elif net_btc > 0:
        net_pts = 3
    elif net_btc > -500:
        net_pts = 2
    else:
        net_pts = 0
    return min(10, ratio_pts + net_pts)


# ── ATR / ADX calculators ─────────────────────────────────────────────────────

def calculate_atr(candles: list, period: int = 14) -> float:
    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    if not true_ranges:
        return 0.0
    return round(sum(true_ranges[-period:]) / min(period, len(true_ranges)), 2)


def calculate_adx(candles: list, period: int = ADX_PERIOD) -> float:
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
        plus_dm_list.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm_list.append(down_move if down_move > up_move and down_move > 0 else 0)
        tr_list.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

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
    return round(sum(dx_list[-period:]) / min(period, len(dx_list)), 2)


# ── Layer 1 — Volatility ──────────────────────────────────────────────────────

def is_market_moving(candles: list) -> tuple[int, dict]:
    atr = calculate_atr(candles)
    adx = calculate_adx(candles)

    atr_series = []
    for i in range(1, len(candles)):
        window = candles[max(0, i - 14):i + 1]
        atr_series.append(calculate_atr(window))
    atr_ma = (
        sum(atr_series[-ATR_MA_PERIOD:]) / min(ATR_MA_PERIOD, len(atr_series))
        if atr_series else 0.0
    )
    atr_expanding = atr > atr_ma * ATR_MA_MULTIPLIER

    volumes = [c["volume"] for c in candles if "volume" in c]
    vol_avg = (
        sum(volumes[-VOLUME_MA_PERIOD:]) / min(VOLUME_MA_PERIOD, len(volumes))
        if volumes else 0
    )
    last_vol = volumes[-1] if volumes else 0
    volume_spike = last_vol > vol_avg

    # ADX slope: compare current ADX vs 5 candles ago — rising trend = forming
    adx_prev = calculate_adx(candles[:-5]) if len(candles) > ADX_PERIOD + 7 else adx
    adx_rising = adx > adx_prev + 1.0   # require at least +1 pt to avoid noise
    slope_bonus = 2 if adx_rising else 0

    score = min(10, _score_l1(adx, atr_expanding, volume_spike) + slope_bonus)
    details = {
        "score":        score,
        "pass":         score >= 7,
        "atr":          atr,
        "atr_ma":       round(atr_ma, 2),
        "atr_expanding": atr_expanding,
        "volume_spike": volume_spike,
        "last_vol":     round(last_vol, 4),
        "vol_avg":      round(vol_avg, 4),
        "adx":          adx,
        "adx_prev":     round(adx_prev, 2),
        "adx_rising":   adx_rising,
    }
    return score, details


# ── Layer 2 — Trend ───────────────────────────────────────────────────────────

def calculate_ema(candles: list, period: int) -> list:
    closes = [c["close"] for c in candles]
    if len(closes) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def is_uptrend(candles: list, candles_4h: list | None = None) -> tuple[int, dict]:
    ema50_series = calculate_ema(candles, EMA_FAST)
    ema200_series = calculate_ema(candles, EMA_SLOW)

    if not ema50_series or not ema200_series:
        return 0, {"error": "not enough candles", "score": 0, "pass": False}

    price = candles[-1]["close"]
    ema50 = ema50_series[-1]
    ema200 = ema200_series[-1]

    ema50_slope_ok = len(ema50_series) > 5 and ema50_series[-1] > ema50_series[-6]

    offset = EMA_SLOW - EMA_FAST
    aligned50 = ema50_series[offset:]

    recent_cross = False
    for i in range(-GOLDEN_CROSS_LOOKBACK, 0):
        try:
            if aligned50[i - 1] <= ema200_series[i - 1] and aligned50[i] > ema200_series[i]:
                recent_cross = True
                break
        except IndexError:
            break

    established = (
        len(aligned50) >= 5
        and len(ema200_series) >= 5
        and all(aligned50[i] > ema200_series[i] for i in range(-5, 0))
    )

    score = _score_l2(price, ema50, ema200, ema50_slope_ok, recent_cross, established)

    # ── 4h timeframe alignment bonus/penalty ──────────────────────────────────
    tf4h_bonus   = 0
    tf4h_aligned = False
    tf4h_ema50   = None
    tf4h_ema200  = None

    if candles_4h and len(candles_4h) >= EMA_SLOW:
        e50_series_4h  = calculate_ema(candles_4h, EMA_FAST)
        e200_series_4h = calculate_ema(candles_4h, EMA_SLOW)
        if e50_series_4h and e200_series_4h:
            tf4h_ema50  = e50_series_4h[-1]
            tf4h_ema200 = e200_series_4h[-1]
            price_4h    = candles_4h[-1]["close"]
            if price_4h > tf4h_ema50 > tf4h_ema200:
                tf4h_bonus   = 2    # strong 4h uptrend — significant boost
                tf4h_aligned = True
            elif price_4h < tf4h_ema50 or tf4h_ema50 < tf4h_ema200:
                tf4h_bonus   = -2   # 4h downtrend — penalise entry
                tf4h_aligned = False
            else:
                tf4h_bonus   = 1    # 4h partial alignment
                tf4h_aligned = True

    score = min(10, max(0, score + tf4h_bonus))

    # 24h VWAP: institutional anchor level
    vwap_window = candles[-24:] if len(candles) >= 24 else candles
    vwap_vol = sum(c["volume"] for c in vwap_window)
    vwap = (
        sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in vwap_window)
        / vwap_vol if vwap_vol > 0 else price
    )
    vwap_above = price > vwap
    vwap_bonus = 1 if vwap_above else -1
    score = min(10, max(0, score + vwap_bonus))

    details = {
        "score":               score,
        "pass":                score >= 7,
        "price":               price,
        "ema50":               round(ema50, 2),
        "ema200":              round(ema200, 2),
        "gap_pct":             round((ema50 - ema200) / ema200 * 100, 3) if ema200 else 0.0,
        "ema50_slope_ok":      ema50_slope_ok,
        "golden_cross":        recent_cross,
        "established_uptrend": established,
        "tf4h_bonus":          tf4h_bonus,
        "tf4h_aligned":        tf4h_aligned,
        "tf4h_ema50":          round(tf4h_ema50, 2) if tf4h_ema50 else None,
        "tf4h_ema200":         round(tf4h_ema200, 2) if tf4h_ema200 else None,
        "vwap":                round(vwap, 2),
        "vwap_above":          vwap_above,
        "vwap_bonus":          vwap_bonus,
    }
    return score, details


# ── Layer 3 — Momentum ────────────────────────────────────────────────────────

def calculate_rsi(candles: list, period: int = 14) -> float:
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
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def calculate_macd(candles: list) -> tuple[float, float, float]:
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


def _rsi_divergence(candles: list, lookback: int = 10) -> int:
    """
    Returns +2 (bullish divergence), -2 (bearish divergence), or 0.
    Bearish: price makes higher high but RSI makes lower high → reversal risk.
    Bullish: price makes lower low but RSI makes higher low → recovery signal.
    """
    if len(candles) < lookback * 2 + 15:
        return 0
    rsi_now  = calculate_rsi(candles)
    rsi_prev = calculate_rsi(candles[:-lookback])
    high_now  = max(c["high"] for c in candles[-lookback:])
    high_prev = max(c["high"] for c in candles[-lookback * 2:-lookback])
    low_now   = min(c["low"]  for c in candles[-lookback:])
    low_prev  = min(c["low"]  for c in candles[-lookback * 2:-lookback])
    if high_now > high_prev * 1.001 and rsi_now < rsi_prev - 3:
        return -2   # bearish divergence — momentum fading on new high
    if low_now < low_prev * 0.999 and rsi_now > rsi_prev + 3:
        return +2   # bullish divergence — momentum recovering on new low
    return 0


def is_not_overbought(candles: list, candles_4h: list | None = None) -> tuple[int, dict]:
    rsi = calculate_rsi(candles)
    macd, macd_sig, macd_hist = calculate_macd(candles)

    score = _score_l3(rsi, macd_hist)

    # 4h RSI confirmation
    tf4h_rsi = None
    rsi4h_bonus = 0
    if candles_4h and len(candles_4h) >= 15:
        tf4h_rsi = calculate_rsi(candles_4h)
        if 40 <= tf4h_rsi <= 65:
            rsi4h_bonus = 2    # 4h momentum healthy — strong confirmation
        elif tf4h_rsi > 70:
            rsi4h_bonus = -2   # 4h overbought — high reversal risk
        elif tf4h_rsi < 35:
            rsi4h_bonus = -1   # 4h oversold — trend might be weak

    # RSI divergence — price/momentum disagreement
    div_signal = _rsi_divergence(candles)

    score = min(10, max(0, score + rsi4h_bonus + div_signal))

    details = {
        "score":          score,
        "pass":           score >= 7,
        "rsi":            rsi,
        "rsi_ok":         RSI_MIN < rsi < RSI_MAX,
        "macd":           macd,
        "macd_signal":    macd_sig,
        "macd_hist":      macd_hist,
        "macd_ok":        macd_hist > 0,
        "tf4h_rsi":       round(tf4h_rsi, 2) if tf4h_rsi is not None else None,
        "rsi4h_bonus":    rsi4h_bonus,
        "divergence":     div_signal,   # +2 bullish, -2 bearish, 0 none
    }
    return score, details


# ── Layer 4 — Volume Spike ────────────────────────────────────────────────────

def is_volume_trending(candles: list) -> tuple[int, dict]:
    """
    Compare last 3-candle average volume vs 20-period SMA (spike detection).
    A volume spike confirms that market participation is rising NOW, not just
    over the last 4h, giving a more reactive entry signal.
    """
    needed = SPIKE_PERIOD + 3
    if len(candles) < needed:
        score = 5
        return score, {"score": score, "pass": False, "ratio": 1.0, "skipped": True}

    # 20-period volume SMA: exclude the 3 most recent candles to avoid self-reference
    sma_window = [c["volume"] for c in candles[-(needed):-3]]
    vol_sma = sum(sma_window) / len(sma_window)

    # Recent spike: avg of last 3 completed candles
    recent_avg = sum(c["volume"] for c in candles[-3:]) / 3

    ratio = recent_avg / vol_sma if vol_sma > 0 else 1.0

    score = _score_l4_vol_trend(ratio)
    return score, {
        "score":      score,
        "pass":       score >= 7,
        "recent_avg": round(recent_avg, 4),
        "vol_sma20":  round(vol_sma, 4),
        "ratio":      round(ratio, 2),
        "skipped":    False,
    }


# ── Layer 5 — Liquidity ───────────────────────────────────────────────────────

def has_liquidity(
    spread: float,
    bid_depth: float,
    ask_depth: float,
    volume_24h: float,
) -> tuple[int, dict]:
    spread_ok = 0 < spread < MAX_SPREAD_USD
    depth_ok = bid_depth >= MIN_DEPTH_BTC and ask_depth >= MIN_DEPTH_BTC
    volume_ok = volume_24h >= MIN_VOLUME_24H

    score = _score_l5(spread, depth_ok, volume_24h)

    # Bid/Ask imbalance — order book sentiment
    imbalance = bid_depth / ask_depth if ask_depth > 0 else 1.0
    if imbalance >= 3.0:      imbalance_bonus = 2    # strong buyer wall
    elif imbalance >= 1.5:    imbalance_bonus = 1
    elif imbalance <= 0.33:   imbalance_bonus = -2   # strong seller wall
    elif imbalance <= 0.67:   imbalance_bonus = -1
    else:                     imbalance_bonus = 0
    score = min(10, max(0, score + imbalance_bonus))

    details = {
        "score":            score,
        "pass":             score >= 7,
        "spread":           spread,
        "spread_ok":        spread_ok,
        "bid_depth_btc":    bid_depth,
        "ask_depth_btc":    ask_depth,
        "depth_ok":         depth_ok,
        "volume_24h_usd":   volume_24h,
        "volume_ok":        volume_ok,
        "ob_imbalance":     round(imbalance, 2),
        "imbalance_bonus":  imbalance_bonus,
    }
    return score, details


# ── Layer 6 — Risk / Reward ───────────────────────────────────────────────────

def check_risk_reward(
    budget: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    atr: float | None = None,
    price: float | None = None,
) -> tuple[int, dict]:
    fee_open = budget * BINANCE_FEE_RATE
    fee_close = budget * BINANCE_FEE_RATE
    total_fee = fee_open + fee_close

    gross_profit = budget * (take_profit_pct / 100)
    gross_loss = budget * (stop_loss_pct / 100)
    net_profit = gross_profit - total_fee
    net_loss = gross_loss + total_fee
    rr_ratio = net_profit / net_loss if net_loss > 0 else 0.0

    score = _score_l6(rr_ratio, net_profit)

    # ATR validation: is our TP reachable given current volatility?
    atr_pct = round(atr / price * 100, 3) if atr and price and price > 0 else None
    atr_tp_suggested = round(atr_pct * 1.5, 2) if atr_pct else None
    atr_sl_suggested = round(atr_pct * 0.75, 2) if atr_pct else None
    atr_modifier = 0
    if atr_pct:
        tp_atr_ratio = take_profit_pct / atr_pct
        if tp_atr_ratio < 0.8:    atr_modifier = -2  # TP inside 1 ATR — unlikely to reach
        elif tp_atr_ratio < 1.0:  atr_modifier = -1  # TP slightly tight
        elif tp_atr_ratio >= 2.0: atr_modifier = +1  # TP well above ATR — clean target
    score = min(10, max(0, score + atr_modifier))

    return score, {
        "score":            score,
        "pass":             score >= 7,
        "budget":           round(budget, 2),
        "take_profit_pct":  take_profit_pct,
        "stop_loss_pct":    stop_loss_pct,
        "gross_profit":     round(gross_profit, 4),
        "gross_loss":       round(gross_loss, 4),
        "total_fee":        round(total_fee, 4),
        "net_profit":       round(net_profit, 4),
        "net_loss":         round(net_loss, 4),
        "rr_ratio":         round(rr_ratio, 2),
        "profit_ok":        net_profit > 0,
        "rr_ok":            rr_ratio >= MIN_RR_RATIO,
        "atr_pct":          atr_pct,
        "atr_tp_suggested": atr_tp_suggested,
        "atr_sl_suggested": atr_sl_suggested,
        "atr_modifier":     atr_modifier,
    }


# ── Layer 7 — News Sentiment ──────────────────────────────────────────────────

def check_news_sentiment(news_summary: dict) -> tuple[int, dict]:
    if not news_summary or news_summary.get("total", 0) == 0:
        score = 5
        return score, {
            "score": score, "pass": False,
            "total": 0, "bullish": 0, "bearish": 0, "neutral": 0,
            "important": 0, "score_val": 0.0, "headlines": [], "skipped": True,
        }

    total = news_summary["total"]
    bearish = news_summary["bearish"]
    bullish = news_summary.get("bullish", 0)
    important = news_summary.get("important", 0)
    score_val = news_summary.get("score", 0.0)
    headlines = news_summary.get("headlines", [])

    score = _score_l7(total, bearish, score_val)
    return score, {
        "score":     score,
        "pass":      score >= 7,
        "total":     total,
        "bullish":   bullish,
        "bearish":   bearish,
        "neutral":   news_summary.get("neutral", 0),
        "important": important,
        "score_val": score_val,
        "headlines": headlines,
        "skipped":   False,
    }


# ── Layer 8 — Funding Rate + Open Interest ────────────────────────────────────

def check_funding_rate(funding_data: dict) -> tuple[int, dict]:
    if not funding_data.get("ok", False):
        score = 7
        return score, {
            "score": score, "pass": True,
            "funding_rate": 0.0, "oi_change_pct": 0.0,
            "funding_ok": True, "oi_ok": True, "skipped": True,
        }

    fr = funding_data["funding_rate"]
    oi_chg = funding_data["oi_change_pct"]
    funding_ok = FUNDING_RATE_MIN <= fr <= FUNDING_RATE_MAX
    oi_ok = oi_chg >= OI_CHANGE_MIN

    score = _score_l8(fr, oi_chg, skipped=False)
    return score, {
        "score":        score,
        "pass":         score >= 7,
        "funding_rate": fr,
        "open_interest": funding_data.get("open_interest", 0),
        "oi_change_pct": oi_chg,
        "funding_ok":   funding_ok,
        "oi_ok":        oi_ok,
        "skipped":      False,
    }


# ── Layer 9 — Fear & Greed ────────────────────────────────────────────────────

def check_fear_greed(fg_data: dict) -> tuple[int, dict]:
    if not fg_data.get("ok", False):
        score = 5
        return score, {
            "score": score, "pass": False,
            "value": 50, "classification": "Neutral",
            "change": 0, "fg_ok": True, "skipped": True,
        }

    value = fg_data["value"]
    change = fg_data.get("change", 0)
    fg_ok = FG_MIN <= value <= FG_MAX

    score = _score_l9(value, skipped=False)
    return score, {
        "score":          score,
        "pass":           score >= 7,
        "value":          value,
        "classification": fg_data.get("classification", ""),
        "change":         change,
        "fg_ok":          fg_ok,
        "skipped":        False,
    }


# ── Layer 10 — Buy/Sell Pressure ─────────────────────────────────────────────

def check_buy_pressure(pressure_data: dict, funding_data: dict | None = None) -> tuple[int, dict]:
    if not pressure_data.get("ok", False):
        score = 5
        return score, {
            "score": score, "pass": False,
            "buy_ratio_pct": 50.0, "net_btc": 0.0, "trend": "neutral",
            "ratio_ok": True, "net_ok": True, "skipped": True,
            "funding_rate": None, "fr_modifier": 0,
        }

    ratio = pressure_data["buy_ratio_pct"]
    net = pressure_data["net_btc"]
    ratio_ok = ratio >= BUY_RATIO_MIN
    net_ok = net >= NET_SELL_MAX

    score = _score_l10(ratio, net, skipped=False)

    # Funding rate modifier (BTC perp market positioning)
    fr_modifier = 0
    funding_rate = None
    if funding_data and funding_data.get("ok"):
        funding_rate = funding_data.get("funding_rate", 0.0)
        if funding_rate < -0.02:       fr_modifier = +3   # shorts overloaded → squeeze setup
        elif funding_rate < 0:         fr_modifier = +2   # shorts paying → long-friendly
        elif funding_rate < 0.01:      fr_modifier = +1   # neutral → slightly bullish
        elif funding_rate < 0.03:      fr_modifier = -1   # longs warming up
        elif funding_rate < 0.05:      fr_modifier = -2   # longs overheating
        else:                          fr_modifier = -3   # extreme longs → correction risk

    score = min(10, max(0, score + fr_modifier))

    return score, {
        "score":         score,
        "pass":          score >= 7,
        "buy_btc":       pressure_data.get("buy_btc", 0.0),
        "sell_btc":      pressure_data.get("sell_btc", 0.0),
        "net_btc":       net,
        "buy_ratio_pct": ratio,
        "trend":         pressure_data.get("trend", "neutral"),
        "hours":         pressure_data.get("hours", 24),
        "ratio_ok":      ratio_ok,
        "net_ok":        net_ok,
        "skipped":       False,
        "funding_rate":  funding_rate,
        "fr_modifier":   fr_modifier,
    }


# ── Entry Signal (all 10 layers) ──────────────────────────────────────────────

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
    funding_data: dict | None = None,    # supplementary display only (not scored)
    fg_data: dict | None = None,         # supplementary display only (not scored)
    pressure_data: dict | None = None,
    candles_4h: list | None = None,      # multi-timeframe L2/L3 alignment
    candles_1d: list | None = None,      # daily trend hard filter
    candles_1w: list | None = None,      # weekly trend hard filter
) -> tuple[bool, dict]:
    l1_score, l1   = is_market_moving(candles)
    l2_score, l2   = is_uptrend(candles, candles_4h=candles_4h)
    l3_score, l3   = is_not_overbought(candles, candles_4h=candles_4h)
    l4_score, l4   = is_volume_trending(candles)
    l5_score, l5   = has_liquidity(spread, bid_depth, ask_depth, volume_24h)
    l6_score, l6   = check_risk_reward(
        budget, take_profit_pct, stop_loss_pct,
        atr=l1.get("atr"), price=candles[-1]["close"] if candles else None,
    )
    l7_score, l7   = check_news_sentiment(news_summary or {})
    l8_score, l8   = check_sr_proximity(candles, tp_pct=take_profit_pct)
    l9_score, l9   = detect_candle_patterns(candles, candles_4h=candles_4h)
    l10_score, l10 = check_buy_pressure(pressure_data or {}, funding_data=funding_data)

    # Supplementary display data
    _, supp_funding  = check_funding_rate(funding_data or {})
    _, supp_fg       = check_fear_greed(fg_data or {})

    total_score = (l1_score + l2_score + l3_score + l4_score + l5_score +
                   l6_score + l7_score + l8_score + l9_score + l10_score)

    # ── Hard filters — block entry regardless of score ────────────────────────
    hard_blocks = []

    # RSI overbought: backtest shows avg loss RSI = 71.9, filter RSI > 65
    if l3["rsi"] > 65:
        hard_blocks.append(f"RSI {l3['rsi']:.0f} > 65 (overbought — high reversal risk)")

    # ADX danger zone 25-40: backtest data shows WR=5-33% vs 54%+ outside this range
    adx_val = l1.get("adx", 0)
    if 25 <= adx_val < 40:
        hard_blocks.append(
            f"ADX {adx_val:.1f} in danger zone 25–40 "
            f"(backtest WR 5–33% — trend developing but unstable)"
        )

    # Daily trend: only enter when price > daily EMA50 (bull market)
    if candles_1d and len(candles_1d) >= 50:
        ema50_1d = calculate_ema(candles_1d, 50)
        if ema50_1d:
            price_1d = candles_1d[-1]["close"]
            ema50_1d_val = ema50_1d[-1]
            if price_1d < ema50_1d_val:
                hard_blocks.append(
                    f"Daily trend bearish "
                    f"(price ${price_1d:,.0f} < daily EMA50 ${ema50_1d_val:,.0f})"
                )

    # Weekly trend: only enter when price > weekly EMA21 (macro bull regime)
    if candles_1w and len(candles_1w) >= 21:
        ema21_1w = calculate_ema(candles_1w, 21)
        if ema21_1w:
            price_1w = candles_1w[-1]["close"]
            ema21_1w_val = ema21_1w[-1]
            if price_1w < ema21_1w_val:
                hard_blocks.append(
                    f"Weekly trend bearish "
                    f"(price ${price_1w:,.0f} < weekly EMA21 ${ema21_1w_val:,.0f})"
                )

    should_enter = (total_score >= ENTRY_SCORE_THRESHOLD) and not hard_blocks

    report = {
        "should_enter": should_enter,
        "total_score":  total_score,
        "hard_blocks":  hard_blocks,
        "layers": {
            "L1_volatility":    {"pass": l1["pass"],  "score": l1_score,  **l1},
            "L2_trend":         {"pass": l2["pass"],  "score": l2_score,  **l2},
            "L3_momentum":      {"pass": l3["pass"],  "score": l3_score,  **l3},
            "L4_vol_trend":     {"pass": l4["pass"],  "score": l4_score,  **l4},
            "L5_liquidity":     {"pass": l5["pass"],  "score": l5_score,  **l5},
            "L6_risk_reward":   {"pass": l6["pass"],  "score": l6_score,  **l6},
            "L7_news":          {"pass": l7["pass"],  "score": l7_score,  **l7},
            "L8_sr_proximity":  {"pass": l8["pass"],  "score": l8_score,  **l8},
            "L9_candle_pattern":{"pass": l9["pass"],  "score": l9_score,  **l9},
            "L10_pressure":     {"pass": l10["pass"], "score": l10_score, **l10},
        },
        "supplementary": {
            "funding":    supp_funding,
            "fear_greed": supp_fg,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# SHORT-DIRECTION INDICATORS (mirror of long-direction)
# ══════════════════════════════════════════════════════════════════════════════
# Used by short backtest path. Same data, inverted interpretation:
#   L2 — is_downtrend: price < EMA50 < EMA200, slope DOWN, death-cross
#   L3 — is_not_oversold: RSI 35-60 (avoid extreme oversold), MACD hist < 0
#   L10 — sell pressure: taker buy ratio < 50% (sellers in control)
# Symmetric layers (L1 volatility, L4 volume, L5 liquidity, L6 R/R) reuse long
# functions unchanged. L7 news, L8 S/R, L9 candle patterns get direction-aware
# wrappers in the engine.

def is_downtrend(candles: list, candles_4h: list | None = None) -> tuple[int, dict]:
    """L2 — Mirror of is_uptrend for short entries."""
    ema50_series  = calculate_ema(candles, EMA_FAST)
    ema200_series = calculate_ema(candles, EMA_SLOW)
    if not ema50_series or not ema200_series:
        return 0, {"error": "not enough candles", "score": 0, "pass": False}

    price  = candles[-1]["close"]
    ema50  = ema50_series[-1]
    ema200 = ema200_series[-1]

    ema50_slope_down = len(ema50_series) > 5 and ema50_series[-1] < ema50_series[-6]

    offset = EMA_SLOW - EMA_FAST
    aligned50 = ema50_series[offset:]

    # Recent death cross
    recent_cross = False
    for i in range(-GOLDEN_CROSS_LOOKBACK, 0):
        try:
            if aligned50[i - 1] >= ema200_series[i - 1] and aligned50[i] < ema200_series[i]:
                recent_cross = True
                break
        except IndexError:
            break

    # Established downtrend
    established = (
        len(aligned50) >= 5 and len(ema200_series) >= 5
        and all(aligned50[i] < ema200_series[i] for i in range(-5, 0))
    )

    # Mirror _score_l2 logic (inline because direction-flipped)
    if price < ema50 < ema200:
        score = 8 if established else 7
    elif price < ema50 and ema50 < ema200:
        score = 6
    elif price < ema50:
        score = 5
    else:
        score = 2
    if ema50_slope_down:
        score = min(10, score + 1)
    if recent_cross:
        score = min(10, score + 1)

    # 4h alignment for shorts
    tf4h_bonus = 0
    tf4h_aligned = False
    tf4h_ema50 = None
    tf4h_ema200 = None
    if candles_4h and len(candles_4h) >= EMA_SLOW:
        e50_4h  = calculate_ema(candles_4h, EMA_FAST)
        e200_4h = calculate_ema(candles_4h, EMA_SLOW)
        if e50_4h and e200_4h:
            tf4h_ema50  = e50_4h[-1]
            tf4h_ema200 = e200_4h[-1]
            price_4h = candles_4h[-1]["close"]
            if price_4h < tf4h_ema50 < tf4h_ema200:
                tf4h_bonus = 2
                tf4h_aligned = True
            elif price_4h > tf4h_ema50 or tf4h_ema50 > tf4h_ema200:
                tf4h_bonus = -2
            else:
                tf4h_bonus = 1
                tf4h_aligned = True

    score = min(10, max(0, score + tf4h_bonus))

    # 24h VWAP — for shorts price BELOW VWAP is bullish for entry
    vwap_window = candles[-24:] if len(candles) >= 24 else candles
    vwap_vol = sum(c["volume"] for c in vwap_window)
    vwap = (sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"]
                for c in vwap_window) / vwap_vol) if vwap_vol > 0 else price
    vwap_below = price < vwap
    vwap_bonus = 1 if vwap_below else -1
    score = min(10, max(0, score + vwap_bonus))

    return score, {
        "score":               score,
        "pass":                score >= 7,
        "price":               price,
        "ema50":               round(ema50, 2),
        "ema200":              round(ema200, 2),
        "gap_pct":             round((ema50 - ema200) / ema200 * 100, 3) if ema200 else 0.0,
        "ema50_slope_down":    ema50_slope_down,
        "death_cross":         recent_cross,
        "established_downtrend": established,
        "tf4h_bonus":          tf4h_bonus,
        "tf4h_aligned":        tf4h_aligned,
        "tf4h_ema50":          round(tf4h_ema50, 2) if tf4h_ema50 else None,
        "tf4h_ema200":         round(tf4h_ema200, 2) if tf4h_ema200 else None,
        "vwap":                round(vwap, 2),
        "vwap_below":          vwap_below,
        "vwap_bonus":          vwap_bonus,
    }


def _score_l3_short(rsi: float, macd_hist: float) -> int:
    """L3 short — want RSI 35-60 (not extreme oversold), MACD hist < 0."""
    if 40 <= rsi <= 60:
        rsi_pts = 6
    elif 35 <= rsi <= 65:
        rsi_pts = 4
    elif rsi < 30:    # extreme oversold — high reversal risk for shorts
        rsi_pts = 0
    else:
        rsi_pts = 2
    macd_pts = 4 if macd_hist < 0 else 0
    return min(10, rsi_pts + macd_pts)


def is_not_oversold(candles: list, candles_4h: list | None = None) -> tuple[int, dict]:
    """L3 — Mirror of is_not_overbought for short entries."""
    rsi = calculate_rsi(candles)
    macd, macd_sig, macd_hist = calculate_macd(candles)

    score = _score_l3_short(rsi, macd_hist)

    tf4h_rsi = None
    rsi4h_bonus = 0
    if candles_4h and len(candles_4h) >= 15:
        tf4h_rsi = calculate_rsi(candles_4h)
        if 35 <= tf4h_rsi <= 60:
            rsi4h_bonus = 2
        elif tf4h_rsi < 30:
            rsi4h_bonus = -2     # extreme oversold — bounce risk
        elif tf4h_rsi > 65:
            rsi4h_bonus = -1

    score = min(10, max(0, score + rsi4h_bonus))

    return score, {
        "score": score, "pass": score >= 7,
        "rsi": rsi, "rsi_ok": 30 < rsi < 65,
        "macd": macd, "macd_signal": macd_sig, "macd_hist": macd_hist,
        "macd_ok": macd_hist < 0,
        "tf4h_rsi": round(tf4h_rsi, 2) if tf4h_rsi is not None else None,
        "rsi4h_bonus": rsi4h_bonus,
    }


def check_sell_pressure(pressure_data: dict, funding_data: dict | None = None) -> tuple[int, dict]:
    """L10 — Mirror of check_buy_pressure for short entries.
    High score when sellers are in control (taker buy ratio low, net negative).
    """
    if not pressure_data.get("ok", False):
        return 5, {"score": 5, "pass": False, "skipped": True,
                   "buy_ratio_pct": 50.0, "net_btc": 0.0, "trend": "neutral"}

    ratio = pressure_data["buy_ratio_pct"]   # taker BUY ratio
    net = pressure_data["net_btc"]

    # Inverted thresholds: low buy ratio = sellers winning
    if ratio <= 40:
        ratio_pts = 6
    elif ratio <= 45:
        ratio_pts = 5
    elif ratio <= 50:
        ratio_pts = 4
    elif ratio <= 55:
        ratio_pts = 2
    else:
        ratio_pts = 0
    if net < -1000:
        net_pts = 4
    elif net < 0:
        net_pts = 3
    elif net < 500:
        net_pts = 2
    else:
        net_pts = 0
    score = min(10, ratio_pts + net_pts)

    # Funding rate: for shorts, high positive funding (longs overheated) = +mod
    fr_modifier = 0
    funding_rate = None
    if funding_data and funding_data.get("ok"):
        funding_rate = funding_data.get("funding_rate", 0.0)
        if   funding_rate >  0.05: fr_modifier = +3   # extreme longs → short squeeze setup
        elif funding_rate >  0.03: fr_modifier = +2
        elif funding_rate >  0.01: fr_modifier = +1
        elif funding_rate > -0.01: fr_modifier = -1
        elif funding_rate > -0.03: fr_modifier = -2
        else:                       fr_modifier = -3

    score = min(10, max(0, score + fr_modifier))

    return score, {
        "score": score, "pass": score >= 7,
        "buy_ratio_pct": ratio, "net_btc": net,
        "trend": pressure_data.get("trend", "neutral"),
        "skipped": False,
        "funding_rate": funding_rate, "fr_modifier": fr_modifier,
    }
    return should_enter, report