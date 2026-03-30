"""Debug why LTCUSDT produces almost no signals."""
from src.indicators import compute_indicators
from src.data_fetcher import get_candles
import pandas as pd
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


df = get_candles('LTCUSDT', '1h', 90)
df = compute_indicators(df)

# L1 checks
atr_expanding = df['atr'] > df['atr'].shift(3)
vol_spike = df['volume'] > df['volume'].rolling(20).mean() * 1.2
adx_ok = df['adx'] > 20
l1_pass = atr_expanding & vol_spike & adx_ok

# L2
price_above = df['close'] > df['ema50']
slope_ok = df['ema50'] > df['ema50'].shift(3)
l2_pass = price_above | slope_ok

# L3
rsi_ok = (df['rsi'] >= 40) & (df['rsi'] <= 65)
macd_ok = df['macd_hist'] > 0
l3_pass = rsi_ok & macd_ok

total = len(df)
print(f"Total bars (90d @ 1h): {total}")
print()
print(
    f"L1 pass (ATR expand + vol spike + ADX>20): {l1_pass.sum()} = {l1_pass.sum()/total*100:.1f}%")
print(f"  - ATR expanding:  {atr_expanding.sum()/total*100:.1f}%")
print(f"  - Vol spike 1.2x: {vol_spike.sum()/total*100:.1f}%")
print(f"  - ADX > 20:       {adx_ok.sum()/total*100:.1f}%")
print()
print(f"L1+L2 pass: {(l1_pass & l2_pass).sum()}")
print(f"L1+L2+L3 pass: {(l1_pass & l2_pass & l3_pass).sum()}")
print()
print(f"Current LTC stats:")
print(f"  Price:   ${df['close'].iloc[-1]:.2f}")
print(
    f"  ATR:     {df['atr'].iloc[-1]:.3f}  ({df['atr'].iloc[-1]/df['close'].iloc[-1]*100:.2f}% of price)")
print(f"  ADX:     {df['adx'].iloc[-1]:.2f}")
print(f"  RSI:     {df['rsi'].iloc[-1]:.2f}")
print(f"  EMA50:   {df['ema50'].iloc[-1]:.2f}")
print(f"  EMA200:  {df['ema200'].iloc[-1]:.2f}")
print(f"  Avg vol/bar: {df['volume'].mean():,.0f} LTC")
