import requests
import pandas as pd
import numpy as np
from datetime import datetime

FMP_API_KEY = 'hL75f5DvVpnbuzMiibTuz0QCEb7lBDEI'
FMP_BASE = 'https://financialmodelingprep.com/api/v3'

def fmp_get(endpoint, params=None):
    if params is None:
        params = {}
    params['apikey'] = FMP_API_KEY
    try:
        r = requests.get(f'{FMP_BASE}{endpoint}', params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and 'Error Message' in data:
                return None
            return data
    except:
        pass
    return None

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

def get_ticker_data(symbol):
    try:
        data = fmp_get(f'/historical-price-full/{symbol}', {'timeseries': 300})
        if not data or 'historical' not in data or len(data['historical']) < 60:
            return None

        hist = list(reversed(data['historical']))
        closes = pd.Series([float(d.get('close', 0)) for d in hist], dtype=float)
        highs  = pd.Series([float(d.get('high',  d.get('close', 0))) for d in hist], dtype=float)
        lows   = pd.Series([float(d.get('low',   d.get('close', 0))) for d in hist], dtype=float)
        vols   = pd.Series([float(d.get('volume', 0)) for d in hist], dtype=float)

        current = float(closes.iloc[-1])
        prev    = float(closes.iloc[-2])
        change  = current - prev
        change_pct = (change / prev * 100) if prev != 0 else 0

        sma50 = float(closes.rolling(50).mean().iloc[-1])
        sma200 = None
        stage = None

        if len(closes) >= 200:
            sma200_s = closes.rolling(200).mean().dropna()
            if len(sma200_s) >= 11:
                sma200 = float(sma200_s.iloc[-1])
                sma200_prev = float(sma200_s.iloc[-11])
                rising = sma200 > sma200_prev
                if current > sma200 and sma50 > sma200 and rising:
                    stage = 2
                elif current < sma200 and sma50 < sma200 and not rising:
                    stage = 4
                elif current > sma200:
                    stage = 1
                else:
                    stage = 3

        atr_s = compute_atr(highs, lows, closes).dropna()
        atr = float(atr_s.iloc[-1]) if len(atr_s) > 0 else current * 0.02
        atr_pct = (atr / current * 100) if current > 0 else 0

        rsi_s = compute_rsi(closes).dropna()
        rsi = float(rsi_s.iloc[-1]) if len(rsi_s) > 0 else 50.0

        vol_nz = vols[vols > 0]
        avg_vol = float(vol_nz.tail(20).mean()) if len(vol_nz) >= 20 else float(vol_nz.mean()) if len(vol_nz) > 0 else 1
        vol_mult = round(float(vols.iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0

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
            'stage': stage,
        }
    except:
        return None

def get_vix():
    try:
        # FMP has VIX quote
        data = fmp_get('/quote/%5EVIX')
        if data and len(data) > 0:
            d = data[0]
            current = d.get('price', 0)
            change = d.get('change', 0)
            change_pct = d.get('changesPercentage', 0)
            level = 'extreme_fear' if current > 30 else 'fear' if current > 20 else 'neutral' if current > 15 else 'calm'
            return {
                'value': round(current, 2),
                'change': round(change, 2),
                'change_pct': round(change_pct, 2),
                'level': level
            }
    except:
        pass
    return None

def determine_market_regime(spy_data, vix_data):
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
