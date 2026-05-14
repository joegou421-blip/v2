import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def get_vix():
    try:
        vix = yf.Ticker('^VIX')
        hist = vix.history(period='5d')
        if len(hist) == 0:
            return None
        current = round(float(hist['Close'].iloc[-1]), 2)
        prev = round(float(hist['Close'].iloc[-2]), 2) if len(hist) >= 2 else current
        change = round(current - prev, 2)
        change_pct = round((change / prev) * 100, 2) if prev != 0 else 0
        level = 'extreme_fear' if current > 30 else 'fear' if current > 20 else 'neutral' if current > 15 else 'calm'
        return {
            'value': current,
            'change': change,
            'change_pct': change_pct,
            'level': level
        }
    except:
        return None

def get_ticker_data(symbol):
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period='6mo')
    if len(hist) < 50:
        return None

    close = hist['Close']
    high = hist['High']
    low = hist['Low']
    volume = hist['Volume']

    current = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    change = current - prev
    change_pct = (change / prev) * 100

    sma50 = float(close.rolling(50).mean().iloc[-1])
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    rsi = float(compute_rsi(close).iloc[-1])
    atr = float(compute_atr(high, low, close).iloc[-1])
    atr_pct = (atr / current) * 100

    # Volume multiplier (current vs 20d avg)
    avg_vol_20 = float(volume.rolling(20).mean().iloc[-1])
    curr_vol = float(volume.iloc[-1])
    vol_mult = round(curr_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

    # % from 52w high
    high_52w = float(high.rolling(252).max().iloc[-1]) if len(high) >= 252 else float(high.max())
    pct_from_high = ((current - high_52w) / high_52w) * 100

    # Stage detection (simplified for index)
    stage = None
    if sma200:
        sma200_slope = float(close.rolling(200).mean().diff(10).iloc[-1])
        if current > sma200 and sma50 > sma200 and sma200_slope > 0:
            stage = 2
        elif current < sma200 and sma50 < sma200 and sma200_slope < 0:
            stage = 4
        elif current > sma200:
            stage = 1
        else:
            stage = 3

    return {
        'symbol': symbol,
        'price': round(current, 2),
        'change': round(change, 2),
        'change_pct': round(change_pct, 2),
        'rsi': round(rsi, 1),
        'atr_pct': round(atr_pct, 2),
        'vol_mult': vol_mult,
        'sma50': round(sma50, 2),
        'sma200': round(sma200, 2) if sma200 else None,
        'pct_from_high': round(pct_from_high, 2),
        'stage': stage,
    }

def determine_market_regime(spy_data, vix_data):
    """Risk-On / Neutral / Risk-Off based on SPY stage + VIX"""
    if spy_data is None:
        return 'neutral'
    stage = spy_data.get('stage')
    vix_val = vix_data['value'] if vix_data else 18
    rsi = spy_data.get('rsi', 50)

    if stage == 2 and vix_val < 20 and rsi > 50:
        return 'risk_on'
    elif stage == 4 or vix_val > 30:
        return 'risk_off'
    elif stage == 2 and vix_val < 25:
        return 'risk_on'
    elif vix_val > 25 or stage == 3:
        return 'risk_off'
    return 'neutral'

def get_market_overview():
    symbols = ['SPY', 'QQQ', 'IWM', 'GLD']
    indices = {}
    for sym in symbols:
        data = get_ticker_data(sym)
        if data:
            indices[sym] = data

    vix = get_vix()
    spy = indices.get('SPY')
    regime = determine_market_regime(spy, vix)

    return {
        'indices': indices,
        'vix': vix,
        'regime': regime,
        'updated_at': datetime.now().isoformat()
    }
