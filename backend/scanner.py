import requests
import numpy as np
import pandas as pd
import json
import time
import os
from datetime import datetime, timedelta

FMP_API_KEY = 'hL75f5DvVpnbuzMiibTuz0QCEb7lBDEI'
FMP_BASE = 'https://financialmodelingprep.com/api/v3'

scan_progress = {'progress': 0, 'current': '', 'total': 500, 'done': 0}

SP500_TICKERS = [
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','JPM','XOM','UNH',
    'JNJ','V','PG','MA','HD','CVX','MRK','ABBV','PEP','COST',
    'AVGO','LLY','ADBE','CSCO','WMT','ACN','MCD','CRM','BAC','TMO',
    'ABT','TXN','NEE','QCOM','DHR','PM','AMGN','UPS','RTX','HON',
    'SBUX','IBM','INTC','INTU','SPGI','GS','MS','CAT','BLK','GE',
    'AXP','MDLZ','ADI','GILD','ISRG','DE','ADP','MMC','SYK','CB',
    'BKNG','CVS','REGN','ZTS','LRCX','ETN','NOC','TJX','MO','SO',
    'DUK','EMR','VRTX','AON','HUM','CI','APD','PSA','NSC','ITW',
    'F','GM','ECL','ROP','EW','SHW','MCO','ORLY','CME','MNST',
    'PCAR','FDX','KLAC','ROST','STZ','DXCM','CTAS','MRNA','SNPS','CDNS',
    'BDX','FTNT','BIIB','DG','CTSH','MSI','TFC','PAYX','VLO','PH',
    'OTIS','CARR','ALL','HLT','EA','FAST','MTD','IDXX','KEYS','WST',
    'PPG','EFX','MCHP','ON','HPQ','TROW','DLR','WEC','AWK','IFF',
    'NKE','DIS','CMCSA','NFLX','PLTR','SNOW','DDOG','NET','ZS','CRWD',
    'PANW','OKTA','VEEV','HUBS','MDB','TTD','RBLX','ABNB','UBER','DASH',
    'COIN','NOW','WDAY','ADSK','ANSS','AMAT','AMD','MU','ORCL',
    'NXPI','SWKS','MPWR','LIN','NUE','FCX','NEM','GD','LMT','BA',
    'ODFL','SAIA','MELI','SE','PYPL','APP','ZM','DOCU','EPAM','MANH',
    'JPM','WFC','USB','PNC','COF','AMT','PLD','CCI','EQIX','PSA',
    'COP','EOG','DVN','HAL','SLB','PSX','UNH','HCA','CNC','PFE',
    'BMY','BIIB','REGN','ALNY','WMT','TGT','EBAY','CMG','YUM','QSR',
]
SP500_TICKERS = list(dict.fromkeys(SP500_TICKERS))

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

def _write_progress(progress_file, data):
    if not progress_file:
        return
    try:
        with open(progress_file, 'w') as f:
            json.dump(data, f)
    except:
        pass

def get_spy_data():
    try:
        data = fmp_get('/historical-price-full/SPY', {'serietype': 'line', 'timeseries': 200})
        if data and 'historical' in data:
            hist = list(reversed(data['historical']))
            closes = [d['close'] for d in hist]
            if len(closes) >= 126:
                ret_6m = (closes[-1] / closes[-126] - 1) * 100
                return {'ret_6m': ret_6m}
    except:
        pass
    return {'ret_6m': 0}

def get_technical_data_fmp(symbol, spy_data=None):
    try:
        # Get OHLCV history
        data = fmp_get(f'/historical-price-full/{symbol}', {'timeseries': 500})
        if not data or 'historical' not in data:
            return None
        hist = list(reversed(data['historical']))
        if len(hist) < 210:
            return None

        closes = pd.Series([float(d.get('close', 0)) for d in hist], dtype=float)
        highs  = pd.Series([float(d.get('high',  d.get('close', 0))) for d in hist], dtype=float)
        lows   = pd.Series([float(d.get('low',   d.get('close', 0))) for d in hist], dtype=float)
        vols   = pd.Series([float(d.get('volume', 0)) for d in hist], dtype=float)

        current_price = float(closes.iloc[-1])
        if current_price <= 0:
            return None

        sma50 = float(closes.rolling(50).mean().iloc[-1])
        sma200_s = closes.rolling(200).mean().dropna()
        if len(sma200_s) < 15:
            return None
        sma200 = float(sma200_s.iloc[-1])
        sma200_prev = float(sma200_s.iloc[-11])

        sma200_rising = sma200 > sma200_prev
        stage2 = current_price > sma200 and sma50 > sma200 and sma200_rising
        if not stage2:
            return None

        atr_s = compute_atr(highs, lows, closes).dropna()
        atr = float(atr_s.iloc[-1]) if len(atr_s) > 0 else current_price * 0.02
        atr_pct = (atr / current_price) * 100

        rsi_s = compute_rsi(closes).dropna()
        rsi = float(rsi_s.iloc[-1]) if len(rsi_s) > 0 else 50.0

        vol_nz = vols[vols > 0]
        avg_vol = float(vol_nz.tail(20).mean()) if len(vol_nz) >= 20 else float(vol_nz.mean()) if len(vol_nz) > 0 else 1
        vol_mult = round(float(vols.iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0
        monthly_dollar_vol = current_price * avg_vol * 21

        ret_6m = (current_price / float(closes.iloc[-126]) - 1) * 100 if len(closes) >= 126 else 0
        spy_ret = spy_data.get('ret_6m', 0) if spy_data else 0
        ratio = ret_6m / abs(spy_ret) if spy_ret != 0 else 1
        rs_rating = round(min(100, max(0, 50 + ratio * 25)), 1)

        swing_low = float(lows.tail(20).min())
        stop_loss = round(swing_low * 0.99, 2)
        risk = max(current_price - stop_loss, current_price * 0.02)
        target = round(current_price + risk * 2.5, 2)
        rr = round((target - current_price) / risk, 2)
        chase_risk = (risk / atr) > 1.5 if atr > 0 else False

        return {
            'price': round(current_price, 2),
            'sma50': round(sma50, 2),
            'sma200': round(sma200, 2),
            'stage2': stage2,
            'atr': round(atr, 2),
            'atr_pct': round(atr_pct, 2),
            'rsi': round(rsi, 1),
            'vol_mult': vol_mult,
            'monthly_dollar_vol': monthly_dollar_vol,
            'rs_rating': rs_rating,
            'stop_loss': stop_loss,
            'target': target,
            'rr': rr,
            'chase_risk': chase_risk,
        }
    except:
        return None

def get_fundamental_data(symbol):
    try:
        income = fmp_get(f'/income-statement/{symbol}', {'period': 'quarter', 'limit': 8})
        estimates = fmp_get(f'/analyst-estimates/{symbol}', {'period': 'quarter', 'limit': 4})
        profile = fmp_get(f'/profile/{symbol}')
        result = {}

        if profile and len(profile) > 0:
            p = profile[0]
            result.update({
                'market_cap': p.get('mktCap', 0),
                'beta': p.get('beta', 1.5),
                'company_name': p.get('companyName', symbol),
                'sector': p.get('sector', ''),
                'industry': p.get('industry', ''),
            })

        if not income or len(income) < 2:
            result.update({'eps_yoy':0,'rev_yoy':0,'eps_accel_2q':False,'eps_accel_3q':False,
                           'gm_expanding':False,'turned_profitable':False,'eps_beat':False,'rev_beat':False})
            return result

        eps_list, rev_list, gm_list = [], [], []
        for q in income[:6]:
            eps = q.get('eps', 0) or 0
            rev = q.get('revenue', 0) or 0
            gp  = q.get('grossProfit', 0) or 0
            eps_list.append(eps)
            rev_list.append(rev)
            gm_list.append((gp / rev * 100) if rev > 0 else 0)

        eps_yoy = ((eps_list[0]-eps_list[4])/abs(eps_list[4])*100) if len(eps_list)>=5 and eps_list[4]!=0 else 0
        rev_yoy = ((rev_list[0]-rev_list[4])/rev_list[4]*100) if len(rev_list)>=5 and rev_list[4]>0 else 0

        gr = []
        for i in range(min(4, len(eps_list)-1)):
            if eps_list[i+1] != 0:
                gr.append((eps_list[i]-eps_list[i+1])/abs(eps_list[i+1])*100)

        eps_beat = rev_beat = False
        if estimates and len(estimates) > 0 and income:
            est = estimates[0]
            eps_beat = (income[0].get('eps',0) or 0) > (est.get('estimatedEpsAvg',0) or 0) if est.get('estimatedEpsAvg',0) else False
            rev_beat = (income[0].get('revenue',0) or 0) > (est.get('estimatedRevenueAvg',0) or 0) if est.get('estimatedRevenueAvg',0) else False

        result.update({
            'eps_yoy': round(eps_yoy, 1),
            'rev_yoy': round(rev_yoy, 1),
            'eps_accel_2q': len(gr)>=2 and gr[0]>gr[1],
            'eps_accel_3q': len(gr)>=3 and gr[0]>gr[1]>gr[2],
            'gm_expanding': len(gm_list)>=2 and gm_list[0]>gm_list[1],
            'turned_profitable': len(eps_list)>=2 and eps_list[0]>0 and eps_list[1]<=0,
            'eps_beat': eps_beat,
            'rev_beat': rev_beat,
        })
        return result
    except:
        return {}

def score_stock(tech, fund):
    score = 0
    breakdown = {}
    checks = [
        ('eps_yoy',           fund.get('eps_yoy',0)>20,              2, 'EPS年增長 > 20%',      fund.get('eps_yoy',0)),
        ('eps_accel_2q',      fund.get('eps_accel_2q',False),         2, 'EPS連續2季加速',        None),
        ('eps_accel_3q',      fund.get('eps_accel_3q',False),         1, 'EPS連續3季加速',        None),
        ('rev_growth',        fund.get('rev_yoy',0)>15,               1, '收入增長 > 15%',         fund.get('rev_yoy',0)),
        ('eps_beat',          fund.get('eps_beat',False),              2, 'EPS超越分析師預期',      None),
        ('rev_beat',          fund.get('rev_beat',False),              1, '收入超越分析師預期',     None),
        ('gm_expanding',      fund.get('gm_expanding',False),          1, '毛利率擴張',             None),
        ('turned_profitable', fund.get('turned_profitable',False),     1, '由虧轉盈',               None),
        ('rs_rating',         tech.get('rs_rating',0)>70,              2, 'RS評級 > 70',            tech.get('rs_rating',0)),
        ('stage2',            tech.get('stage2',False),                2, 'Stage 2 確認',           None),
    ]
    for key, passed, pts, label, value in checks:
        if passed:
            score += pts
        entry = {'pass': passed, 'points': pts, 'label': label}
        if value is not None:
            entry['value'] = value
        breakdown[key] = entry

    if score >= 12:   signal, sl = 'strong_buy',       '強烈買入'
    elif score >= 9:  signal, sl = 'buy',               '買入'
    elif score >= 6:  signal, sl = 'neutral_positive',  '中性偏好'
    else:             signal, sl = 'watch',              '觀望'
    return score, signal, sl, breakdown

def run_full_scan(cache_file, progress_file=None):
    global scan_progress
    tickers = SP500_TICKERS
    total = len(tickers)
    scan_progress = {'is_scanning': True, 'progress': 0, 'current': '初始化...', 'total': total, 'done': 0}
    _write_progress(progress_file, dict(scan_progress))

    scan_progress['current'] = '取得 SPY 基準數據...'
    _write_progress(progress_file, dict(scan_progress))
    spy_data = get_spy_data()

    results = []
    debug = {'not_stage2': 0, 'low_mktcap': 0, 'low_beta': 0, 'low_vol': 0, 'passed': 0}

    for i, symbol in enumerate(tickers):
        scan_progress.update({'current': symbol, 'done': i, 'progress': int(i/total*100)})
        if i % 5 == 0:
            _write_progress(progress_file, dict(scan_progress))

        try:
            tech = get_technical_data_fmp(symbol, spy_data)
            if not tech:
                debug['not_stage2'] += 1
                continue

            fund = get_fundamental_data(symbol)
            mkt_cap = fund.get('market_cap', 0)
            beta_val = fund.get('beta', 1.5)

            if mkt_cap and mkt_cap > 0 and mkt_cap < 2_000_000_000:
                debug['low_mktcap'] += 1
                continue
            if beta_val and 0 < beta_val <= 0.8:
                debug['low_beta'] += 1
                continue
            if tech['monthly_dollar_vol'] > 0 and tech['monthly_dollar_vol'] < 100_000_000:
                debug['low_vol'] += 1
                continue

            debug['passed'] += 1
            score, signal, signal_label, breakdown = score_stock(tech, fund)

            results.append({
                'symbol': symbol,
                'company_name': fund.get('company_name', symbol),
                'sector': fund.get('sector', ''),
                'industry': fund.get('industry', ''),
                'score': score, 'signal': signal, 'signal_label': signal_label,
                'breakdown': breakdown,
                'price': tech['price'],
                'eps_yoy': fund.get('eps_yoy', 0),
                'rev_yoy': fund.get('rev_yoy', 0),
                'rs_rating': tech['rs_rating'],
                'beta': beta_val,
                'market_cap': mkt_cap,
                'stop_loss': tech['stop_loss'],
                'target': tech['target'],
                'rr': tech['rr'],
                'chase_risk': tech['chase_risk'],
                'atr_pct': tech['atr_pct'],
                'rsi': tech['rsi'],
                'vol_mult': tech['vol_mult'],
            })
            time.sleep(0.1)
        except:
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    for i, r in enumerate(results):
        r['rank'] = i + 1

    with open(cache_file, 'w') as f:
        json.dump({'results': results, 'scanned_at': datetime.now().isoformat(),
                   'total_scanned': total, 'passed': len(results), 'debug': debug}, f)

    scan_progress.update({'progress': 100, 'current': f'完成！找到 {len(results)} 支股票', 'done': total})
    _write_progress(progress_file, dict(scan_progress))
    return results

def get_cached_results(cache_file):
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        if datetime.now() - datetime.fromisoformat(data['scanned_at']) > timedelta(hours=24):
            return None
        return data
    except:
        return None

def score_single_stock(symbol):
    try:
        spy_data = get_spy_data()
        tech = get_technical_data_fmp(symbol, spy_data)
        fund = get_fundamental_data(symbol)

        if not tech:
            price_data = fmp_get(f'/historical-price-full/{symbol}', {'serietype': 'line', 'timeseries': 5})
            price = price_data['historical'][0]['close'] if price_data and 'historical' in price_data and price_data['historical'] else 0
            return {
                'symbol': symbol,
                'company_name': fund.get('company_name', symbol),
                'sector': fund.get('sector', ''),
                'price': price, 'score': 0,
                'signal': 'watch', 'signal_label': '觀望',
                'breakdown': {}, 'not_stage2': True,
                'eps_yoy': fund.get('eps_yoy', 0),
                'rev_yoy': fund.get('rev_yoy', 0),
            }

        score, signal, signal_label, breakdown = score_stock(tech, fund)
        return {
            'symbol': symbol,
            'company_name': fund.get('company_name', symbol),
            'sector': fund.get('sector', ''),
            'industry': fund.get('industry', ''),
            'score': score, 'signal': signal, 'signal_label': signal_label,
            'breakdown': breakdown,
            'price': tech['price'],
            'eps_yoy': fund.get('eps_yoy', 0),
            'rev_yoy': fund.get('rev_yoy', 0),
            'rs_rating': tech['rs_rating'],
            'beta': fund.get('beta', 1.5),
            'stop_loss': tech['stop_loss'],
            'target': tech['target'],
            'rr': tech['rr'],
            'chase_risk': tech['chase_risk'],
            'atr_pct': tech['atr_pct'],
            'rsi': tech['rsi'],
            'vol_mult': tech['vol_mult'],
            'stage2': tech['stage2'],
        }
    except:
        return None
