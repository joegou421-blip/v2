import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
from datetime import datetime
from database import (get_fundamental_cache, save_fundamental_cache,
                       save_scan_results, set_meta, get_meta)

FMP_KEY  = 'hL75f5DvVpnbuzMiibTuz0QCEb7lBDEI'
FMP_BASE = 'https://financialmodelingprep.com/api/v3'

# ── S&P 500 tickers (deduplicated) ───────────────────────────────────────────
SP500 = list(dict.fromkeys([
    'AAPL','MSFT','NVDA','AMZN','GOOGL','GOOG','META','TSLA','BRK-B','JPM',
    'XOM','UNH','JNJ','V','PG','MA','HD','CVX','MRK','ABBV',
    'PEP','COST','AVGO','LLY','ADBE','CSCO','WMT','ACN','MCD','CRM',
    'BAC','TMO','ABT','TXN','NEE','QCOM','DHR','PM','AMGN','UPS',
    'RTX','HON','SBUX','IBM','INTC','INTU','SPGI','GS','MS','CAT',
    'BLK','GE','AXP','ADI','GILD','ISRG','DE','ADP','MMC','SYK',
    'CB','BKNG','CVS','REGN','ZTS','LRCX','ETN','NOC','TJX','MO',
    'SO','DUK','EMR','VRTX','AON','HUM','CI','APD','PSA','NSC',
    'ITW','ECL','ROP','EW','SHW','MCO','ORLY','CME','MNST','PCAR',
    'FDX','KLAC','ROST','STZ','DXCM','CTAS','MRNA','SNPS','CDNS','BDX',
    'FTNT','BIIB','DG','CTSH','MSI','TFC','PAYX','VLO','PH','OTIS',
    'CARR','ALL','HLT','EA','FAST','MTD','IDXX','KEYS','PPG','EFX',
    'MCHP','ON','TROW','DLR','WEC','AWK','IFF','NKE','DIS','CMCSA',
    'NFLX','PLTR','SNOW','DDOG','NET','ZS','CRWD','PANW','VEEV','HUBS',
    'MDB','TTD','ABNB','UBER','COIN','NOW','WDAY','ADSK','ANSS','AMAT',
    'AMD','MU','ORCL','NXPI','SWKS','MPWR','LIN','FCX','NEM','LMT',
    'BA','GD','ODFL','SAIA','MELI','SE','PYPL','APP','ZM','EPAM',
    'WFC','USB','PNC','COF','AMT','PLD','CCI','EQIX','COP','EOG',
    'DVN','HAL','SLB','PSX','HCA','CNC','PFE','BMY','REGN','ALNY',
    'WMT','TGT','CMG','YUM','F','GM','NUE','STLD','MRVL','QRVO',
    'WELL','O','SBAC','AWK','AEP','EXC','PCG','SRE','D','ES',
    'GEHC','KVUE','CEG','VST','FSLR','ENPH','RUN','SEDG','BE',
    'DECK','LULU','ONON','CROX','SKX','TPR','PVH','RL',
    'AXON','TNET','PAYC','PCTY','FIVN','ZI','BRZE','GTLB','CFLT',
    'SMCI','ARM','DELL','HPE','STX','WDC','NTAP','PSTG',
    'RCL','CCL','NCLH','MAR','HLT','H','EXPE','BKNG',
    'OXY','MPC','HES','APA','FANG','PR','CTRA',
    'WMB','KMI','OKE','ET','EPD','MMP',
    'BX','KKR','APO','ARES','CG','BAM',
    'UBER','LYFT','DASH','ABNB','CHWY','W','ETSY',
    'HOOD','SOFI','AFRM','UPST','NU','SQ','PYPL',
    'MEDP','ALGN','IRTC','TMDX','RXRX','ROIV',
]))

scan_progress = {}

# ── helpers ──────────────────────────────────────────────────────────────────
def compute_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag = gain.ewm(com=period-1, min_periods=period).mean()
    al = loss.ewm(com=period-1, min_periods=period).mean()
    rs = ag / al
    return 100 - (100 / (1 + rs))

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
        with open(pf, 'w') as f:
            json.dump(data, f)
    except: pass

# ── FMP fundamental data (cached 7 days) ────────────────────────────────────
def fmp_get(endpoint, params=None):
    p = dict(params or {})
    p['apikey'] = FMP_KEY
    try:
        r = requests.get(f'{FMP_BASE}{endpoint}', params=p, timeout=15)
        if r.status_code == 200:
            d = r.json()
            if isinstance(d, dict) and 'Error Message' in d:
                return None
            return d
    except: pass
    return None

def get_fundamentals(symbol):
    cached = get_fundamental_cache(symbol)
    if cached:
        return cached

    try:
        income    = fmp_get(f'/income-statement/{symbol}',    {'period':'quarter','limit':8})
        estimates = fmp_get(f'/analyst-estimates/{symbol}',   {'period':'quarter','limit':4})
        profile   = fmp_get(f'/profile/{symbol}')
        result    = {}

        if profile and len(profile) > 0:
            p = profile[0]
            result.update({
                'market_cap':   p.get('mktCap', 0),
                'beta':         p.get('beta'),
                'company_name': p.get('companyName', symbol),
                'sector':       p.get('sector', ''),
                'industry':     p.get('industry', ''),
            })
        else:
            # FMP profile unavailable — fallback to yfinance fast_info
            try:
                info = yf.Ticker(symbol).fast_info
                result.update({
                    'market_cap':   getattr(info, 'market_cap', 0) or 0,
                    'beta':         None,  # fast_info has no beta
                    'company_name': symbol,
                    'sector':       '',
                    'industry':     '',
                })
                # Try yfinance .info for beta (slower but has it)
                try:
                    yinfo = yf.Ticker(symbol).info
                    result['beta'] = yinfo.get('beta')
                    result['company_name'] = yinfo.get('shortName', symbol)
                    result['sector']  = yinfo.get('sector', '')
                    result['industry'] = yinfo.get('industry', '')
                except: pass
            except: pass

        if not income or len(income) < 2:
            # No income data — use None so frontend shows '--' instead of '0.0%'
            result.update({'eps_yoy': None, 'rev_yoy': None, 'eps_accel_2q': False,
                           'eps_accel_3q': False, 'gm_expanding': False,
                           'turned_profitable': False, 'eps_beat': False, 'rev_beat': False})
            save_fundamental_cache(symbol, result)
            return result

        eps_l, rev_l, gm_l = [], [], []
        for q in income[:6]:
            eps = q.get('eps', 0) or 0
            rev = q.get('revenue', 0) or 0
            gp  = q.get('grossProfit', 0) or 0
            eps_l.append(eps); rev_l.append(rev)
            gm_l.append(gp/rev*100 if rev else 0)

        eps_yoy = ((eps_l[0]-eps_l[4])/abs(eps_l[4])*100) if len(eps_l)>=5 and eps_l[4] else 0
        rev_yoy = ((rev_l[0]-rev_l[4])/rev_l[4]*100)      if len(rev_l)>=5 and rev_l[4] else 0

        gr = []
        for i in range(min(4, len(eps_l)-1)):
            if eps_l[i+1]: gr.append((eps_l[i]-eps_l[i+1])/abs(eps_l[i+1])*100)

        eps_beat = rev_beat = False
        if estimates and income:
            est = estimates[0]
            ae  = income[0].get('eps',0) or 0
            ar  = income[0].get('revenue',0) or 0
            ee  = est.get('estimatedEpsAvg',0) or 0
            er  = est.get('estimatedRevenueAvg',0) or 0
            eps_beat = ae > ee if ee else False
            rev_beat = ar > er if er else False

        result.update({
            'eps_yoy':          round(eps_yoy, 1),
            'rev_yoy':          round(rev_yoy, 1),
            'eps_accel_2q':     len(gr)>=2 and gr[0]>gr[1],
            'eps_accel_3q':     len(gr)>=3 and gr[0]>gr[1]>gr[2],
            'gm_expanding':     len(gm_l)>=2 and gm_l[0]>gm_l[1],
            'turned_profitable':len(eps_l)>=2 and eps_l[0]>0 and eps_l[1]<=0,
            'eps_beat':         eps_beat,
            'rev_beat':         rev_beat,
        })
        save_fundamental_cache(symbol, result)
        return result
    except Exception as e:
        print(f"[fund] {symbol}: {e}")
        return {}

# ── Technical analysis via yfinance ─────────────────────────────────────────
def get_technicals(symbol, spy_ret6m=0):
    try:
        hist = yf.Ticker(symbol).history(period='2y', timeout=20)
        if hist is None or len(hist) < 210:
            return None

        close  = hist['Close'].dropna()
        high   = hist['High'].dropna()
        low    = hist['Low'].dropna()
        volume = hist['Volume'].dropna()

        price = float(close.iloc[-1])
        if price <= 0: return None

        sma50  = float(close.rolling(50).mean().iloc[-1])
        s200   = close.rolling(200).mean().dropna()
        if len(s200) < 15: return None

        sma200     = float(s200.iloc[-1])
        sma200_old = float(s200.iloc[-11])
        rising     = sma200 > sma200_old

        # Stage 2: price > SMA200, SMA50 > SMA200, SMA200 rising
        if not (price > sma200 and sma50 > sma200 and rising):
            return None

        atr_s = compute_atr(high, low, close).dropna()
        atr   = float(atr_s.iloc[-1]) if len(atr_s) else price * 0.02
        atr_pct = atr / price * 100

        rsi_s = compute_rsi(close).dropna()
        rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) else 50.0

        vol_nz  = volume[volume > 0]
        avg_vol = float(vol_nz.tail(20).mean()) if len(vol_nz)>=20 else float(vol_nz.mean() or 1)
        vol_mult = round(float(volume.iloc[-1]) / avg_vol, 2) if avg_vol else 1.0
        monthly_dv = price * avg_vol * 21

        # RS Rating vs SPY — outperformance in pct pts mapped to 0-100
        ret6m = (price / float(close.iloc[-126]) - 1)*100 if len(close)>=126 else 0
        if spy_ret6m and spy_ret6m != 0:
            rel = ret6m - spy_ret6m            # outperform pct pts
            rs_rating = round(min(100, max(0, 50 + rel * 1.5)), 1)
        else:
            # No SPY data: use absolute 6m return (-30→0, 0→40, +15→65, +40→100)
            rs_rating = round(min(100, max(0, 40 + ret6m * 1.2)), 1)

        # Trade levels
        swing_low  = float(low.tail(20).min())
        stop_loss  = round(swing_low * 0.99, 2)
        risk       = max(price - stop_loss, price * 0.02)
        target     = round(price + risk * 2.5, 2)
        rr         = round((target - price) / risk, 2)
        chase_risk = (risk / atr) > 1.5 if atr else False

        return {
            'price':          round(price, 2),
            'sma50':          round(sma50, 2),
            'sma200':         round(sma200, 2),
            'stage2':         True,
            'atr':            round(atr, 2),
            'atr_pct':        round(atr_pct, 2),
            'rsi':            round(rsi, 1),
            'vol_mult':       vol_mult,
            'monthly_dv':     monthly_dv,
            'rs_rating':      rs_rating,
            'stop_loss':      stop_loss,
            'target':         target,
            'rr':             rr,
            'chase_risk':     chase_risk,
        }
    except Exception as e:
        print(f"[tech] {symbol}: {e}")
        return None

# ── Scoring ───────────────────────────────────────────────────────────────────
def score_stock(tech, fund):
    checks = [
        ('eps_yoy',           fund.get('eps_yoy',0) > 20,              2, 'EPS年增長 > 20%',     fund.get('eps_yoy',0)),
        ('eps_accel_2q',      fund.get('eps_accel_2q', False),          2, 'EPS連續2季加速',       None),
        ('eps_accel_3q',      fund.get('eps_accel_3q', False),          1, 'EPS連續3季加速',       None),
        ('rev_growth',        fund.get('rev_yoy',0) > 15,               1, '收入增長 > 15%',       fund.get('rev_yoy',0)),
        ('eps_beat',          fund.get('eps_beat', False),               2, 'EPS超越分析師預期',    None),
        ('rev_beat',          fund.get('rev_beat', False),               1, '收入超越分析師預期',   None),
        ('gm_expanding',      fund.get('gm_expanding', False),           1, '毛利率擴張',           None),
        ('turned_profitable', fund.get('turned_profitable', False),      1, '由虧轉盈',             None),
        ('rs_rating',         tech.get('rs_rating',0) > 70,             2, 'RS評級 > 70',          tech.get('rs_rating',0)),
        ('stage2',            tech.get('stage2', False),                 2, 'Stage 2 確認',         None),
    ]
    score = 0
    breakdown = {}
    for key, passed, pts, label, value in checks:
        if passed: score += pts
        entry = {'pass': passed, 'points': pts, 'label': label}
        if value is not None: entry['value'] = value
        breakdown[key] = entry

    if   score >= 12: signal, sl = 'strong_buy',      '強烈買入'
    elif score >= 9:  signal, sl = 'buy',              '買入'
    elif score >= 6:  signal, sl = 'neutral_positive', '中性偏好'
    else:             signal, sl = 'watch',             '觀望'
    return score, signal, sl, breakdown

# ── Get SPY 6-month return for RS benchmark ──────────────────────────────────
def get_spy_ret6m():
    try:
        hist = yf.Ticker('SPY').history(period='1y', timeout=15)
        if hist is not None and len(hist) >= 126:
            c = hist['Close'].dropna()
            return (float(c.iloc[-1]) / float(c.iloc[-126]) - 1) * 100
    except: pass
    return 0

# ── Main scan ─────────────────────────────────────────────────────────────────
def run_full_scan(progress_file=None):
    global scan_progress
    total = len(SP500)
    scan_progress = {'is_scanning': True, 'progress': 0, 'current': '初始化...', 'total': total, 'done': 0}
    _wp(progress_file, scan_progress)

    set_meta('scan_status', {'is_scanning': True, 'progress': 0, 'current': '取得基準數據...', 'total': total, 'done': 0})

    # SPY benchmark
    spy_ret6m = get_spy_ret6m()

    results = []
    stats   = {'not_stage2': 0, 'low_mktcap': 0, 'low_beta': 0, 'low_vol': 0, 'passed': 0}

    for i, symbol in enumerate(SP500):
        progress = int(i / total * 100)
        state = {'is_scanning': True, 'progress': progress, 'current': symbol, 'total': total, 'done': i}
        scan_progress.update(state)
        if i % 10 == 0:
            _wp(progress_file, scan_progress)
            set_meta('scan_status', state)

        try:
            # ── Technical (yfinance) ──
            tech = get_technicals(symbol, spy_ret6m)
            if not tech:
                stats['not_stage2'] += 1
                time.sleep(0.3)   # gentle rate limit
                continue

            # ── Fundamental (FMP, cached) ──
            fund = get_fundamentals(symbol)
            time.sleep(0.5)       # FMP rate limit

            mkt_cap  = fund.get('market_cap', 0)
            beta_val = fund.get('beta')   # None if FMP failed — skip filter, show '--'

            # Filters (only reject when data is present)
            if mkt_cap and mkt_cap > 0 and mkt_cap < 2_000_000_000:
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
                'eps_yoy':      fund.get('eps_yoy', 0),
                'rev_yoy':      fund.get('rev_yoy', 0),
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
            })

        except Exception as e:
            print(f"[scan] {symbol} error: {e}")
            time.sleep(0.3)
            continue

    # Sort + rank
    results.sort(key=lambda x: x['score'], reverse=True)
    for i, r in enumerate(results):
        r['rank'] = i + 1

    # Persist to SQLite
    save_scan_results(results, total)

    done_state = {'is_scanning': False, 'progress': 100,
                  'current': f'完成！找到 {len(results)} 支股票',
                  'total': total, 'done': total, 'stats': stats}
    scan_progress.update(done_state)
    _wp(progress_file, scan_progress)
    set_meta('scan_status', done_state)

    print(f"[scan] Done. passed={stats['passed']} not_stage2={stats['not_stage2']} stats={stats}")
    return results

# ── Score single stock (for search page, live) ───────────────────────────────
def _get_full_technicals(symbol, spy_ret6m=0):
    """Like get_technicals but WITHOUT stage2 filter — for single stock search"""
    try:
        hist = yf.Ticker(symbol).history(period='2y', timeout=30)
        if hist is None or len(hist) < 60:
            return None

        close  = hist['Close'].dropna()
        high   = hist['High'].dropna()
        low    = hist['Low'].dropna()
        volume = hist['Volume'].dropna()

        if len(close) < 2:
            return None

        price = float(close.iloc[-1])
        if price <= 0:
            return None

        sma50  = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        sma200 = None
        stage2 = False

        if len(close) >= 200:
            s200 = close.rolling(200).mean().dropna()
            if len(s200) >= 11:
                sma200     = float(s200.iloc[-1])
                sma200_old = float(s200.iloc[-11])
                rising     = sma200 > sma200_old
                stage2     = bool(price > sma200 and sma50 and sma50 > sma200 and rising)

        atr_s = compute_atr(high, low, close).dropna()
        atr   = float(atr_s.iloc[-1]) if len(atr_s) else price * 0.02
        atr_pct = atr / price * 100

        rsi_s = compute_rsi(close).dropna()
        rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) else 50.0

        vol_nz  = volume[volume > 0]
        avg_vol = float(vol_nz.tail(20).mean()) if len(vol_nz) >= 20 else float(vol_nz.mean() or 1)
        vol_mult = round(float(volume.iloc[-1]) / avg_vol, 2) if avg_vol else 1.0
        monthly_dv = price * avg_vol * 21

        ret6m = (price / float(close.iloc[-126]) - 1)*100 if len(close) >= 126 else 0
        if spy_ret6m and spy_ret6m != 0:
            rel = ret6m - spy_ret6m
            rs_rating = round(min(100, max(0, 50 + rel * 1.5)), 1)
        else:
            rs_rating = round(min(100, max(0, 40 + ret6m * 1.2)), 1)

        swing_low = float(low.tail(20).min()) if len(low) >= 20 else price * 0.95
        stop_loss = round(swing_low * 0.99, 2)
        risk      = max(price - stop_loss, price * 0.02)
        target    = round(price + risk * 2.5, 2)
        rr        = round((target - price) / risk, 2)
        chase_risk = (risk / atr) > 1.5 if atr else False

        return {
            'price':      round(price, 2),
            'sma50':      round(sma50, 2) if sma50 else None,
            'sma200':     round(sma200, 2) if sma200 else None,
            'stage2':     stage2,
            'atr':        round(atr, 2),
            'atr_pct':    round(atr_pct, 2),
            'rsi':        round(rsi, 1),
            'vol_mult':   vol_mult,
            'monthly_dv': monthly_dv,
            'rs_rating':  rs_rating,
            'stop_loss':  stop_loss,
            'target':     target,
            'rr':         rr,
            'chase_risk': chase_risk,
        }
    except Exception as e:
        print(f"[full_tech] {symbol}: {e}")
        return None


def score_single_stock(symbol):
    """Score any stock — always returns a dict, never None"""
    empty = {
        'symbol': symbol, 'company_name': symbol, 'sector': '', 'industry': '',
        'price': 0, 'score': 0, 'signal': 'watch', 'signal_label': '觀望',
        'breakdown': {}, 'not_stage2': True, 'stage2': False,
        'eps_yoy': None, 'rev_yoy': None, 'beta': None,
        'rs_rating': None, 'stop_loss': None, 'target': None, 'rr': None,
        'chase_risk': False, 'atr_pct': None, 'rsi': None, 'vol_mult': None,
    }

    # Step 1: fundamentals (FMP)
    try:
        fund = get_fundamentals(symbol)
        empty.update({
            'company_name': fund.get('company_name', symbol),
            'sector':       fund.get('sector', ''),
            'industry':     fund.get('industry', ''),
            'eps_yoy':      fund.get('eps_yoy'),
            'rev_yoy':      fund.get('rev_yoy'),
            'beta':         fund.get('beta'),
        })
    except Exception as e:
        print(f"[single] {symbol} fund error: {e}")
        fund = {}

    # Step 2: technicals (yfinance, no stage2 gate)
    try:
        spy_ret6m = get_spy_ret6m()
        tech_full = _get_full_technicals(symbol, spy_ret6m)
    except Exception as e:
        print(f"[single] {symbol} tech error: {e}")
        tech_full = None

    if not tech_full:
        print(f"[single] {symbol}: no technical data — returning partial result")
        return empty   # still has fundamental data if FMP worked

    # Step 3: score
    try:
        score, signal, signal_label, breakdown = score_stock(tech_full, fund)
    except Exception as e:
        print(f"[single] {symbol} score error: {e}")
        score, signal, signal_label, breakdown = 0, 'watch', '觀望', {}

    return {
        'symbol':       symbol,
        'company_name': fund.get('company_name', symbol),
        'sector':       fund.get('sector', ''),
        'industry':     fund.get('industry', ''),
        'eps_yoy':      fund.get('eps_yoy'),
        'rev_yoy':      fund.get('rev_yoy'),
        'beta':         fund.get('beta'),
        'price':        tech_full['price'],
        'score':        score,
        'signal':       signal,
        'signal_label': signal_label,
        'breakdown':    breakdown,
        'rs_rating':    tech_full['rs_rating'],
        'stop_loss':    tech_full['stop_loss'],
        'target':       tech_full['target'],
        'rr':           tech_full['rr'],
        'chase_risk':   tech_full['chase_risk'],
        'atr_pct':      tech_full['atr_pct'],
        'rsi':          tech_full['rsi'],
        'vol_mult':     tech_full['vol_mult'],
        'stage2':       tech_full['stage2'],
        'not_stage2':   not tech_full['stage2'],
    }
