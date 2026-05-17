"""
scanner.py — 主掃描引擎
技術面: yfinance history (OHLCV)
基本面: fundamentals.py (三層備援)
"""
import yfinance as yf
import pandas as pd
import numpy as np
import json, time
from datetime import datetime
from database import save_scan_results, set_meta, get_meta
from fundamentals import get_fundamentals

SP500 = list(dict.fromkeys([
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','JPM','XOM','UNH',
    'JNJ','V','PG','MA','HD','CVX','MRK','ABBV','PEP','COST',
    'AVGO','LLY','ADBE','CSCO','WMT','ACN','MCD','CRM','BAC','TMO',
    'ABT','TXN','NEE','QCOM','DHR','PM','AMGN','UPS','RTX','HON',
    'SBUX','IBM','INTC','INTU','SPGI','GS','MS','CAT','BLK','GE',
    'AXP','ADI','GILD','ISRG','DE','ADP','MMC','SYK','CB','BKNG',
    'CVS','REGN','ZTS','LRCX','ETN','NOC','TJX','MO','SO','DUK',
    'EMR','VRTX','AON','HUM','CI','APD','PSA','NSC','ITW','ECL',
    'ROP','EW','SHW','MCO','ORLY','CME','MNST','PCAR','FDX','KLAC',
    'ROST','STZ','DXCM','CTAS','MRNA','SNPS','CDNS','BDX','FTNT','BIIB',
    'DG','CTSH','MSI','TFC','PAYX','VLO','PH','OTIS','CARR','ALL',
    'HLT','EA','FAST','MTD','IDXX','KEYS','PPG','EFX','MCHP','ON',
    'TROW','DLR','WEC','AWK','IFF','NKE','DIS','CMCSA','NFLX','PLTR',
    'SNOW','DDOG','NET','ZS','CRWD','PANW','VEEV','HUBS','MDB','TTD',
    'ABNB','UBER','COIN','NOW','WDAY','ADSK','ANSS','AMAT','AMD','MU',
    'ORCL','NXPI','MPWR','LIN','FCX','NEM','LMT','BA','GD','ODFL',
    'MELI','SE','PYPL','APP','EPAM','WFC','USB','PNC','COF','AMT',
    'PLD','CCI','EQIX','COP','EOG','DVN','HAL','SLB','PSX','HCA',
    'CNC','PFE','BMY','REGN','WMT','TGT','CMG','YUM','F','GM',
    'NUE','STLD','MRVL','WELL','O','SBAC','CEG','VST','FSLR',
    'DECK','LULU','ONON','AXON','PAYC','SMCI','DELL','HPE','RCL',
    'CCL','MAR','EXPE','OXY','MPC','HES','FANG','WMB','KMI','OKE',
    'BX','KKR','APO','HOOD','SOFI','AFRM','NU','DASH','CHWY','ETSY',
]))

scan_progress = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
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

def _wp(pf, data):
    if not pf: return
    try:
        with open(pf, 'w') as f: json.dump(data, f)
    except: pass

# ── SPY cache ─────────────────────────────────────────────────────────────────
_spy_cache = {'hist': None, 'ret6m': 0, 'close': None, 'ts': 0}

def get_spy_hist():
    global _spy_cache
    if time.time() - _spy_cache['ts'] < 3600 and _spy_cache['hist'] is not None:
        return _spy_cache
    try:
        hist = yf.Ticker('SPY').history(period='2y', timeout=30)
        if hist is not None and len(hist) >= 126:
            close = hist['Close'].dropna()
            ret6m = (float(close.iloc[-1]) / float(close.iloc[-126]) - 1) * 100
            _spy_cache = {'hist': hist, 'ret6m': ret6m, 'close': close, 'ts': time.time()}
            print(f"[spy] loaded {len(hist)} bars, ret6m={ret6m:.1f}%")
    except Exception as e:
        print(f"[spy] error: {e}")
    return _spy_cache

def get_spy_ret6m():
    return get_spy_hist()['ret6m']

# ── Technicals ────────────────────────────────────────────────────────────────
def _get_technicals_core(symbol, spy_ret6m=0, require_stage2=False):
    try:
        hist = yf.Ticker(symbol).history(period='2y', timeout=30)
        if hist is None or len(hist) < 210:
            return None

        close  = hist['Close'].dropna()
        high   = hist['High'].dropna()
        low    = hist['Low'].dropna()
        volume = hist['Volume'].dropna()
        if len(close) < 2: return None

        price = float(close.iloc[-1])
        if price <= 0: return None

        sma50  = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        sma200 = None
        stage2 = False

        if len(close) >= 200:
            s200 = close.rolling(200).mean().dropna()
            if len(s200) >= 11:
                sma200 = float(s200.iloc[-1])
                rising = sma200 > float(s200.iloc[-11])
                stage2 = bool(price > sma200 and sma50 and sma50 > sma200 and rising)

        if require_stage2 and not stage2:
            return None

        atr_s   = compute_atr(high, low, close).dropna()
        atr     = float(atr_s.iloc[-1]) if len(atr_s) else price * 0.02
        atr_pct = atr / price * 100

        rsi_s = compute_rsi(close).dropna()
        rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) else 50.0

        vol_nz   = volume[volume > 0]
        avg_vol  = float(vol_nz.tail(20).mean()) if len(vol_nz) >= 20 else float(vol_nz.mean() or 1)
        vol_mult = round(float(volume.iloc[-1]) / avg_vol, 2) if avg_vol else 1.0
        monthly_dv = price * avg_vol * 21

        ret6m = (price / float(close.iloc[-126]) - 1)*100 if len(close) >= 126 else 0
        if spy_ret6m and spy_ret6m != 0:
            rs_rating = round(min(100, max(0, 50 + (ret6m - spy_ret6m) * 1.5)), 1)
        else:
            rs_rating = round(min(100, max(0, 40 + ret6m * 1.2)), 1)

        swing_low  = float(low.tail(20).min()) if len(low) >= 20 else price * 0.95
        stop_loss  = round(swing_low * 0.99, 2)
        risk       = max(price - stop_loss, price * 0.02)
        target     = round(price + risk * 2.5, 2)
        rr         = round((target - price) / risk, 2)
        chase_risk = (risk / atr) > 1.5 if atr else False

        return {
            'price': round(price, 2),
            'sma50': round(sma50, 2) if sma50 else None,
            'sma200': round(sma200, 2) if sma200 else None,
            'stage2': stage2,
            'atr': round(atr, 2), 'atr_pct': round(atr_pct, 2),
            'rsi': round(rsi, 1), 'vol_mult': vol_mult,
            'monthly_dv': monthly_dv, 'rs_rating': rs_rating,
            'stop_loss': stop_loss, 'target': target,
            'rr': rr, 'chase_risk': chase_risk,
            'close_series': close,
        }
    except Exception as e:
        print(f"[tech] {symbol}: {e}")
        return None

def get_technicals(symbol, spy_ret6m=0):
    return _get_technicals_core(symbol, spy_ret6m, require_stage2=True)

def _get_full_technicals(symbol, spy_ret6m=0):
    return _get_technicals_core(symbol, spy_ret6m, require_stage2=False)

# ── Scoring ───────────────────────────────────────────────────────────────────
def score_stock(tech, fund):
    checks = [
        ('eps_yoy',           (fund.get('eps_yoy') or 0) > 20,         2, 'EPS年增長 > 20%',    fund.get('eps_yoy')),
        ('eps_accel_2q',      fund.get('eps_accel_2q', False),          2, 'EPS連續2季加速',      None),
        ('eps_accel_3q',      fund.get('eps_accel_3q', False),          1, 'EPS連續3季加速',      None),
        ('rev_growth',        (fund.get('rev_yoy') or 0) > 15,          1, '收入增長 > 15%',      fund.get('rev_yoy')),
        ('eps_beat',          fund.get('eps_beat', False),               2, 'EPS超越分析師預期',   None),
        ('rev_beat',          fund.get('rev_beat', False),               1, '收入超越分析師預期',  None),
        ('gm_expanding',      fund.get('gm_expanding', False),           1, '毛利率擴張',          None),
        ('turned_profitable', fund.get('turned_profitable', False),      1, '由虧轉盈',            None),
        ('rs_rating',         (tech.get('rs_rating') or 0) > 70,        2, 'RS評級 > 70',         tech.get('rs_rating')),
        ('stage2',            tech.get('stage2', False),                 2, 'Stage 2 確認',        None),
    ]
    score = 0
    breakdown = {}
    for key, passed, pts, label, value in checks:
        if passed: score += pts
        entry = {'pass': passed, 'points': pts, 'label': label}
        if value is not None:
            try: entry['value'] = round(float(value), 1)
            except: entry['value'] = value
        breakdown[key] = entry

    if   score >= 12: signal, sl = 'strong_buy',      '強烈買入'
    elif score >= 9:  signal, sl = 'buy',              '買入'
    elif score >= 6:  signal, sl = 'neutral_positive', '中性偏好'
    else:             signal, sl = 'watch',             '觀望'
    return score, signal, sl, breakdown

# ── Full scan ─────────────────────────────────────────────────────────────────
def run_full_scan(progress_file=None):
    global scan_progress
    tickers = SP500
    total   = len(tickers)
    init    = {'is_scanning': True, 'progress': 0, 'current': '取得 SPY 基準...', 'total': total, 'done': 0}
    scan_progress.update(init)
    _wp(progress_file, init)
    set_meta('scan_status', init)

    spy = get_spy_hist()
    spy_ret6m = spy['ret6m']
    spy_close = spy['close']

    results = []
    stats   = {'not_stage2': 0, 'low_mktcap': 0, 'low_beta': 0, 'low_vol': 0, 'passed': 0}

    for i, symbol in enumerate(tickers):
        state = {'is_scanning': True, 'progress': int(i/total*100),
                 'current': symbol, 'total': total, 'done': i}
        scan_progress.update(state)
        if i % 10 == 0:
            _wp(progress_file, state)
            set_meta('scan_status', state)

        try:
            # Step 1: technicals with Stage 2 filter
            tech = get_technicals(symbol, spy_ret6m)
            if not tech:
                stats['not_stage2'] += 1
                time.sleep(0.3)
                continue

            # Step 2: fundamentals (three-layer)
            close_series = tech.pop('close_series', None)
            fund = get_fundamentals(symbol, close_series, spy_close)
            time.sleep(0.3)

            # Step 3: filters
            mkt_cap  = fund.get('market_cap', 0) or 0
            beta_val = fund.get('beta')

            if mkt_cap > 0 and mkt_cap < 2_000_000_000:
                stats['low_mktcap'] += 1; continue
            if beta_val is not None and 0 < beta_val <= 0.8:
                stats['low_beta'] += 1; continue
            if tech['monthly_dv'] > 0 and tech['monthly_dv'] < 100_000_000:
                stats['low_vol'] += 1; continue

            stats['passed'] += 1
            score, signal, signal_label, breakdown = score_stock(tech, fund)

            results.append({
                'symbol':       symbol,
                'company_name': fund.get('company_name', symbol),
                'sector':       fund.get('sector', ''),
                'industry':     fund.get('industry', ''),
                'score':        score,
                'signal':       signal,
                'signal_label': signal_label,
                'breakdown':    breakdown,
                'price':        tech['price'],
                'eps_yoy':      fund.get('eps_yoy'),
                'rev_yoy':      fund.get('rev_yoy'),
                'rs_rating':    tech['rs_rating'],
                'beta':         beta_val,
                'market_cap':   mkt_cap,
                'stop_loss':    tech['stop_loss'],
                'target':       tech['target'],
                'rr':           tech['rr'],
                'chase_risk':   tech['chase_risk'],
                'atr_pct':      tech['atr_pct'],
                'rsi':          tech['rsi'],
                'vol_mult':     tech['vol_mult'],
                'fund_source':  fund.get('_source', 'unknown'),
            })
        except Exception as e:
            print(f"[scan] {symbol}: {e}")
            time.sleep(0.3)
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    for i, r in enumerate(results): r['rank'] = i + 1

    save_scan_results(results, total)
    done = {'is_scanning': False, 'progress': 100,
            'current': f'完成！找到 {len(results)} 支股票',
            'total': total, 'done': total, 'stats': stats}
    scan_progress.update(done)
    _wp(progress_file, done)
    set_meta('scan_status', done)
    print(f"[scan] Done: {stats}")
    return results

# ── Single stock (search page) ────────────────────────────────────────────────
def score_single_stock(symbol):
    """Always returns a dict — never None"""
    empty = {
        'symbol': symbol, 'company_name': symbol, 'sector': '', 'industry': '',
        'price': 0, 'score': 0, 'signal': 'watch', 'signal_label': '觀望',
        'breakdown': {}, 'stage2': False, 'not_stage2': True,
        'eps_yoy': None, 'rev_yoy': None, 'beta': None, 'rs_rating': None,
        'stop_loss': None, 'target': None, 'rr': None,
        'chase_risk': False, 'atr_pct': None, 'rsi': None, 'vol_mult': None,
    }

    # Technicals
    try:
        spy = get_spy_hist()
        tech = _get_full_technicals(symbol, spy['ret6m'])
    except Exception as e:
        print(f"[single] {symbol} tech: {e}")
        tech = None

    # Fundamentals (three-layer)
    try:
        close_s = tech.pop('close_series', None) if tech else None
        spy_c   = get_spy_hist()['close']
        fund    = get_fundamentals(symbol, close_s, spy_c)
    except Exception as e:
        print(f"[single] {symbol} fund: {e}")
        fund = {}

    # Update empty with what we got
    empty['company_name'] = fund.get('company_name', symbol)
    empty['sector']       = fund.get('sector', '')
    empty['industry']     = fund.get('industry', '')
    empty['eps_yoy']      = fund.get('eps_yoy')
    empty['rev_yoy']      = fund.get('rev_yoy')
    empty['beta']         = fund.get('beta')

    if not tech:
        print(f"[single] {symbol}: no tech data")
        return empty

    try:
        score, signal, signal_label, breakdown = score_stock(tech, fund)
    except Exception as e:
        print(f"[single] {symbol} score: {e}")
        score, signal, signal_label, breakdown = 0, 'watch', '觀望', {}

    return {
        'symbol':       symbol,
        'company_name': fund.get('company_name', symbol),
        'sector':       fund.get('sector', ''),
        'industry':     fund.get('industry', ''),
        'price':        tech['price'],
        'score':        score,
        'signal':       signal,
        'signal_label': signal_label,
        'breakdown':    breakdown,
        'eps_yoy':      fund.get('eps_yoy'),
        'rev_yoy':      fund.get('rev_yoy'),
        'beta':         fund.get('beta'),
        'rs_rating':    tech['rs_rating'],
        'stop_loss':    tech['stop_loss'],
        'target':       tech['target'],
        'rr':           tech['rr'],
        'chase_risk':   tech['chase_risk'],
        'atr_pct':      tech['atr_pct'],
        'rsi':          tech['rsi'],
        'vol_mult':     tech['vol_mult'],
        'stage2':       tech['stage2'],
        'not_stage2':   not tech['stage2'],
        'fund_source':  fund.get('_source', 'unknown'),
    }
