import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import time

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

def get_ticker_data(symbol, retries=3):
    """Fetch ticker with retry logic"""
    for attempt in range(retries):
        try:
            hist = yf.Ticker(symbol).history(period='1y', timeout=30)
            if hist is not None and len(hist) >= 60:
                break
            time.sleep(1)
        except Exception as e:
            print(f"[market] {symbol} attempt {attempt+1} error: {e}")
            time.sleep(2)
    else:
        return None

    try:
        close  = hist['Close'].dropna()
        high   = hist['High'].dropna()
        low    = hist['Low'].dropna()
        volume = hist['Volume'].dropna()

        if len(close) < 2:
            return None

        current    = float(close.iloc[-1])
        prev       = float(close.iloc[-2])
        change     = current - prev
        change_pct = (change / prev * 100) if prev else 0

        sma50  = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        sma200 = None
        stage  = None

        if len(close) >= 200:
            s200 = close.rolling(200).mean().dropna()
            if len(s200) >= 11:
                sma200     = float(s200.iloc[-1])
                sma200_old = float(s200.iloc[-11])
                rising = sma200 > sma200_old
                if   current > sma200 and sma50 and sma50 > sma200 and rising:     stage = 2
                elif current < sma200 and sma50 and sma50 < sma200 and not rising: stage = 4
                elif current > sma200:                                              stage = 1
                else:                                                               stage = 3

        atr_s   = compute_atr(high, low, close).dropna()
        atr     = float(atr_s.iloc[-1]) if len(atr_s) else current * 0.02
        atr_pct = atr / current * 100

        rsi_s = compute_rsi(close).dropna()
        rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) else 50.0

        vol_nz   = volume[volume > 0]
        avg_vol  = float(vol_nz.tail(20).mean()) if len(vol_nz) >= 20 else float(vol_nz.mean() or 1)
        vol_mult = round(float(volume.iloc[-1]) / avg_vol, 2) if avg_vol else 1.0

        high52        = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())
        pct_from_high = (current - high52) / high52 * 100

        return {
            'symbol':        symbol,
            'price':         round(current, 2),
            'change':        round(change, 2),
            'change_pct':    round(change_pct, 2),
            'rsi':           round(rsi, 1),
            'atr_pct':       round(atr_pct, 2),
            'vol_mult':      vol_mult,
            'sma50':         round(sma50, 2) if sma50 else None,
            'sma200':        round(sma200, 2) if sma200 else None,
            'stage':         stage,
            'pct_from_high': round(pct_from_high, 1),
        }
    except Exception as e:
        print(f"[market] {symbol} parse error: {e}")
        return None

def get_vix(retries=3):
    for attempt in range(retries):
        try:
            hist = yf.Ticker('^VIX').history(period='5d', timeout=20)
            if hist is not None and len(hist) >= 1:
                break
            time.sleep(1)
        except Exception as e:
            print(f"[market] VIX attempt {attempt+1}: {e}")
            time.sleep(2)
    else:
        return None

    try:
        current    = float(hist['Close'].iloc[-1])
        prev       = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else current
        change     = current - prev
        change_pct = (change / prev * 100) if prev else 0

        # Level: absolute value only (display label)
        abs_level = ('extreme_fear' if current > 30 else
                     'fear'         if current > 20 else
                     'neutral'      if current > 15 else 'calm')

        # Momentum: rate-of-change signal
        if change_pct > 10:   momentum = 'spike'      # 暴漲 > 10%
        elif change_pct > 5:  momentum = 'surge'      # 飆升 5-10%
        elif change_pct > 0:  momentum = 'rising'     # 上升
        elif change_pct > -5: momentum = 'falling'    # 下降
        else:                 momentum = 'plunging'   # 急跌

        return {
            'value':      round(current, 2),
            'change':     round(change, 2),
            'change_pct': round(change_pct, 2),
            'level':      abs_level,
            'momentum':   momentum,
        }
    except Exception as e:
        print(f"[market] VIX parse error: {e}")
        return None

def determine_regime(indices, vix):
    """
    市場環境判斷優先級：
    P0 緊急觸發 > P1 VIX絕對值 > P2 多指數跌幅 > P3 流動性危機 > P4 SPY Stage
    """
    spy = indices.get('SPY')
    qqq = indices.get('QQQ')
    iwm = indices.get('IWM')
    gld = indices.get('GLD')

    vix_val        = vix['value']      if vix else 18
    vix_change_pct = vix['change_pct'] if vix else 0
    vix_momentum   = vix.get('momentum', 'neutral') if vix else 'neutral'

    # ── P0: VIX 單日暴漲（無視絕對值）──
    if vix_momentum in ('spike', 'surge'):   # > 5%
        return 'risk_off'

    # ── P1: VIX 極端絕對值 ──
    if vix_val > 30:
        return 'risk_off'

    # ── P2: 全盤通殺（2個以上指數跌 > 1.5%）──
    broad_down = sum(1 for idx in [spy, qqq, iwm]
                     if idx and idx.get('change_pct', 0) < -1.5)
    if broad_down >= 2:
        return 'risk_off'

    # ── P3: 流動性危機（風險+避險資產同跌）──
    risk_down  = qqq and qqq.get('change_pct', 0) < -1.0
    haven_down = gld and gld.get('change_pct', 0) < -1.0
    if risk_down and haven_down:
        return 'risk_off'

    # ── SPY 基準缺失 ──
    if not spy:
        # 用 QQQ 作影子基準
        if not qqq:
            return 'no_data'
        qqq_stage = qqq.get('stage')
        qqq_rsi   = qqq.get('rsi', 50)
        if vix_val > 25 or qqq_stage in (3, 4):
            return 'risk_off'
        if qqq_stage == 2 and vix_val < 25 and qqq_rsi > 50:
            return 'risk_on'
        return 'neutral'

    # ── P4: SPY Stage 常規判斷 ──
    stage = spy.get('stage')
    rsi   = spy.get('rsi', 50)

    if vix_val > 25 or stage in (3, 4):
        return 'risk_off'
    if stage == 2 and vix_val < 20 and rsi > 50:
        return 'risk_on'
    if stage == 2 and vix_val < 25:
        return 'risk_on'

    return 'neutral'

def get_market_overview():
    indices = {}
    spy_ok = True

    for sym in ['SPY', 'QQQ', 'IWM', 'GLD']:
        d = get_ticker_data(sym)
        if d:
            indices[sym] = d
        elif sym == 'SPY':
            spy_ok = False
            print("[market] WARNING: SPY data unavailable, using shadow benchmark")

    vix    = get_vix()
    regime = determine_regime(indices, vix)

    # Extra signals
    signals = []
    qqq = indices.get('QQQ')
    gld = indices.get('GLD')
    iwm = indices.get('IWM')

    if vix and vix.get('momentum') in ('spike', 'surge'):
        signals.append({'type': 'warning', 'msg': f"VIX 單日暴漲 {vix['change_pct']:.1f}%，市場情緒急劇惡化"})
    if (qqq and qqq.get('change_pct', 0) < -1.0) and (gld and gld.get('change_pct', 0) < -1.0):
        signals.append({'type': 'danger', 'msg': '風險+黃金同步下跌：現金為王，流動性收縮警告'})
    if not spy_ok:
        signals.append({'type': 'info', 'msg': 'SPY 數據異常，已切換至 QQQ 影子基準'})

    return {
        'indices':    indices,
        'vix':        vix,
        'regime':     regime,
        'spy_ok':     spy_ok,
        'signals':    signals,
        'updated_at': datetime.now().isoformat(),
    }
