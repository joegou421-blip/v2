import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

def compute_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag = gain.ewm(com=period-1, min_periods=period).mean()
    al = loss.ewm(com=period-1, min_periods=period).mean()
    return 100 - (100 / (1 + ag/al))

def compute_atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def get_ticker_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period='1y', timeout=30)
        if hist is None or len(hist) < 60:
            return None

        close  = hist['Close'].dropna()
        high   = hist['High'].dropna()
        low    = hist['Low'].dropna()
        volume = hist['Volume'].dropna()

        current    = float(close.iloc[-1])
        prev       = float(close.iloc[-2])
        change     = current - prev
        change_pct = (change / prev * 100) if prev else 0

        sma50  = float(close.rolling(50).mean().iloc[-1])
        sma200 = None
        stage  = None

        if len(close) >= 200:
            s200 = close.rolling(200).mean().dropna()
            if len(s200) >= 11:
                sma200     = float(s200.iloc[-1])
                sma200_old = float(s200.iloc[-11])
                rising = sma200 > sma200_old
                if   current > sma200 and sma50 > sma200 and rising:     stage = 2
                elif current < sma200 and sma50 < sma200 and not rising: stage = 4
                elif current > sma200:                                    stage = 1
                else:                                                     stage = 3

        atr_s   = compute_atr(high, low, close).dropna()
        atr     = float(atr_s.iloc[-1]) if len(atr_s) else current * 0.02
        atr_pct = atr / current * 100

        rsi_s = compute_rsi(close).dropna()
        rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) else 50.0

        vol_nz   = volume[volume > 0]
        avg_vol  = float(vol_nz.tail(20).mean()) if len(vol_nz) >= 20 else float(vol_nz.mean() or 1)
        vol_mult = round(float(volume.iloc[-1]) / avg_vol, 2) if avg_vol else 1.0

        # % from 52-week high
        high52 = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())
        pct_from_high = (current - high52) / high52 * 100

        return {
            'symbol':        symbol,
            'price':         round(current, 2),
            'change':        round(change, 2),
            'change_pct':    round(change_pct, 2),
            'rsi':           round(rsi, 1),
            'atr_pct':       round(atr_pct, 2),
            'vol_mult':      vol_mult,
            'sma50':         round(sma50, 2),
            'sma200':        round(sma200, 2) if sma200 else None,
            'stage':         stage,
            'pct_from_high': round(pct_from_high, 1),
        }
    except Exception as e:
        print(f"[market] {symbol} error: {e}")
        return None

def get_vix():
    try:
        hist = yf.Ticker('^VIX').history(period='5d', timeout=20)
        if hist is None or len(hist) < 1:
            return None
        current    = float(hist['Close'].iloc[-1])
        prev       = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else current
        change     = current - prev
        change_pct = (change / prev * 100) if prev else 0
        level = ('extreme_fear' if current > 30 else
                 'fear'         if current > 20 else
                 'neutral'      if current > 15 else 'calm')
        return {
            'value':      round(current, 2),
            'change':     round(change, 2),
            'change_pct': round(change_pct, 2),
            'level':      level,
        }
    except Exception as e:
        print(f"[market] VIX error: {e}")
        return None

def determine_regime(indices, vix):
    """
    更精確的市場環境判斷，納入：
    - VIX 絕對值 + 單日漲幅
    - 多指數同步跌幅（清倉式下跌）
    - SPY Stage + RSI
    """
    spy = indices.get('SPY')
    qqq = indices.get('QQQ')
    iwm = indices.get('IWM')
    gld = indices.get('GLD')

    vix_val        = vix['value']      if vix else 18
    vix_change_pct = vix['change_pct'] if vix else 0

    # ── 緊急 Risk-Off 條件（立即觸發）──
    # 1. VIX 單日暴漲 > 5%
    if vix_change_pct > 5:
        return 'risk_off'
    # 2. VIX 極端恐懼
    if vix_val > 30:
        return 'risk_off'
    # 3. 多個指數同步大跌 > 1.5%（全盤通殺）
    broad_selloff_count = sum(1 for idx in [spy, qqq, iwm]
                              if idx and idx.get('change_pct', 0) < -1.5)
    if broad_selloff_count >= 2:
        return 'risk_off'
    # 4. 風險+避險資產同步跌（流動性危機）
    risk_down   = qqq and qqq.get('change_pct', 0) < -1.0
    haven_down  = gld and gld.get('change_pct', 0) < -1.0
    if risk_down and haven_down and vix_change_pct > 3:
        return 'risk_off'

    # ── SPY Stage 判斷 ──
    if not spy:
        return 'neutral'
    stage = spy.get('stage')
    rsi   = spy.get('rsi', 50)

    if vix_val > 25 or stage == 4 or stage == 3:
        return 'risk_off'
    if stage == 2 and vix_val < 20 and rsi > 50:
        return 'risk_on'
    if stage == 2 and vix_val < 25:
        return 'risk_on'

    return 'neutral'

def get_market_overview():
    indices = {}
    for sym in ['SPY', 'QQQ', 'IWM', 'GLD']:
        d = get_ticker_data(sym)
        if d:
            indices[sym] = d

    vix    = get_vix()
    regime = determine_regime(indices, vix)

    return {
        'indices':    indices,
        'vix':        vix,
        'regime':     regime,
        'updated_at': datetime.now().isoformat(),
    }
