import yfinance as yf
import pandas as pd
import numpy as np
import json, time
from datetime import datetime
from database import save_scan_results, set_meta, get_meta
from fundamentals import get_fundamentals, calc_beta_from_prices, calc_rs_rating

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
    'CNC','PFE','BMY','REGN','TGT','CMG','YUM','F','GM','NUE','STLD',
    'MRVL','WELL','O','SBAC','CEG','VST','FSLR','DECK','LULU','ONON',
    'AXON','PAYC','SMCI','DELL','HPE','RCL','CCL','MAR','EXPE','OXY',
    'MPC','HES','FANG','WMB','KMI','OKE','BX','KKR','APO',
    'HOOD','SOFI','AFRM','NU','DASH','CHWY','ETSY','MSTR','COIN',
]))

scan_progress = {}

def compute_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag = gain.ewm(com=period-1, min_periods=period).mean()
    al = loss.ewm(com=period-1, min_periods=period).mean()
    return 100 - (100 / (1 + ag/al))

def compute_atr(high, low, close, period=14):
    tr = pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def _wp(pf, data):
    if not pf: return
    try:
        with open(pf, 'w') as f: json.dump(data, f)
    except: pass

_spy = {'hist': None, 'close': None, 'ret6m': 0, 'ts': 0}

def get_spy():
    global _spy
    if time.time() - _spy['ts'] < 3600 and _spy['hist'] is not None: return _spy
    try:
        hist = yf.Ticker('SPY').history(period='2y', timeout=30)
        if hist is not None and len(hist) >= 130:
            close = hist['Close'].dropna()
            ret6m = (float(close.iloc[-1])/float(close.iloc[-126])-1)*100
            _spy  = {'hist': hist, 'close': close, 'ret6m': ret6m, 'ts': time.time()}
    except Exception as e: print(f"[spy] error: {e}")
    return _spy

def _technicals(symbol, spy_close, require_stage2=False):
    try:
        hist = yf.Ticker(symbol).history(period='2y', timeout=30)
        if hist is None or len(hist) < 210: return None

        close, high, low, volume = hist['Close'].dropna(), hist['High'].dropna(), hist['Low'].dropna(), hist['Volume'].dropna()
        if len(close) < 2: return None
        price = float(close.iloc[-1])
        if price <= 0: return None

        sma50  = float(close.rolling(50).mean().iloc[-1]) if len(close)>=50 else None
        sma200 = None; stage2 = False
        if len(close) >= 200:
            s200 = close.rolling(200).mean().dropna()
            if len(s200) >= 11:
                sma200 = float(s200.iloc[-1])
                stage2 = bool(price > sma200 and sma50 and sma50 > sma200 and (sma200 > float(s200.iloc[-11])))

        if require_stage2 and not stage2: return None

        atr_s = compute_atr(high,low,close).dropna()
        atr   = float(atr_s.iloc[-1]) if len(atr_s) else price*0.02
        rsi_s = compute_rsi(close).dropna()
        rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) else 50.0

        vol_nz  = volume[volume>0]
        avg_vol = float(vol_nz.tail(20).mean()) if len(vol_nz)>=20 else float(vol_nz.mean() or 1)
        vol_mult = round(float(volume.iloc[-1])/avg_vol, 2) if avg_vol else 1.0
        monthly_dv = price * avg_vol * 21

        rs_rating = calc_rs_rating(close, spy_close) or round(min(99, max(1, 40 + ((price/float(close.iloc[-252])-1)*100) * 0.8)), 1)

        swing_low = float(low.tail(20).min()) if len(low)>=20 else price*0.95
        stop_loss = round(swing_low*0.99, 2)
        risk      = max(price-stop_loss, price*0.02)
        return {
            'price': round(price,2), 'sma50': round(sma50,2) if sma50 else None, 'sma200': round(sma200,2) if sma200 else None,
            'stage2': stage2, 'atr': round(atr,2), 'atr_pct': round(atr/price*100,2), 'rsi': round(rsi,1), 'vol_mult': vol_mult,
            'monthly_dv': monthly_dv, 'rs_rating': rs_rating, 'stop_loss': stop_loss, 'target': round(price+risk*2.5, 2),
            'rr': round((round(price+risk*2.5, 2)-price)/risk, 2), 'chase_risk': (risk/atr) > 1.5 if atr else False, '_close': close,
        }
    except Exception as e: print(f"[tech] {symbol}: {e}"); return None

def get_technicals(symbol, spy_close): return _technicals(symbol, spy_close, require_stage2=True)
def _get_full_technicals(symbol, spy_close): return _technicals(symbol, spy_close, require_stage2=False)

def _to_native(v):
    if isinstance(v, np.bool_): return bool(v)
    if isinstance(v, np.integer): return int(v)
    if isinstance(v, np.floating): return float(v)
    return v

def score_stock(tech, fund):
    raw_eps = fund.get('eps_yoy')
    
    # 🚀 終極熔斷：只要原生數字低於 -100%（負基數公式污染），或者資料庫自帶由虧轉盈標籤
    is_turnaround = bool(fund.get('turned_profitable', False) or (isinstance(raw_eps, (int, float)) and raw_eps < -100))
    eps_yoy_passes = is_turnaround or (isinstance(raw_eps, (int, float)) and raw_eps > 20)

    # 🚀 資料清洗：強行將細目中的實際值從 -610.0% 改寫為 "由虧轉盈"
    display_eps_val = "由虧轉盈" if is_turnaround else raw_eps

    checks = [
        ('eps_yoy',           eps_yoy_passes,                              2, 'EPS年增長 > 20%',   display_eps_val),
        ('eps_accel_2q',      bool(fund.get('eps_accel_2q',False)),        2, 'EPS連續2季加速',     None),
        ('eps_accel_3q',      bool(fund.get('eps_accel_3q',False)),        1, 'EPS連續3季加速',     None),
        ('rev_growth',        bool((fund.get('rev_yoy') or 0)>15),         1, '收入增長 > 15%',     fund.get('rev_yoy')),
        ('eps_beat',          bool(fund.get('eps_beat',False)),             2, 'EPS超越分析師預期',  None),
        ('rev_beat',          bool(fund.get('rev_beat',False)),             1, '收入超越分析師預期', None),
        ('gm_expanding',      bool(fund.get('gm_expanding',False)),         1, '毛利率擴張',         None),
        ('turned_profitable', is_turnaround,                               1, '由虧轉盈',           None),
        ('rs_rating',         bool((tech.get('rs_rating') or 0)>70),       2, 'RS評級 > 70',        tech.get('rs_rating')),
        ('stage2',            bool(tech.get('stage2',False)),               2, 'Stage 2 確認',       None),
    ]
    
    score = 0; breakdown = {}
    for key, passed, pts, label, value in checks:
        if passed: score += pts
        entry = {'pass': bool(passed), 'points': int(pts), 'label': label}
        if value is not None:
            if isinstance(value, str): entry['value'] = value
            else:
                try: entry['value'] = round(float(value), 1)
                except: entry['value'] = _to_native(value)
        breakdown[key] = entry

    if score>=12: signal,sl = 'strong_buy','強烈買入'
    elif score>=9: signal,sl = 'buy','買入'
    elif score>=6: signal,sl = 'neutral_positive','中性偏好'
    else: signal,sl = 'watch','觀望'
    return score, signal, sl, breakdown

def run_full_scan(progress_file=None):
    global scan_progress
    tickers = SP500; total = len(tickers)
    init = {'is_scanning':True,'progress':0,'current':'取得SPY...','total':total,'done':0}
    scan_progress.update(init); _wp(progress_file,init); set_meta('scan_status',init)

    spy_close = get_spy()['close']
    results = []
    stats = {'not_stage2':0,'low_mktcap':0,'low_beta':0,'low_vol':0,'passed':0}

    for i, symbol in enumerate(tickers):
        state = {'is_scanning':True,'progress':int(i/total*100),'current':symbol,'total':total,'done':i}
        scan_progress.update(state)
        if i%10==0: _wp(progress_file,state); set_meta('scan_status',state)

        try:
            tech = get_technicals(symbol, spy_close)
            if not tech: stats['not_stage2']+=1; time.sleep(0.05); continue

            close_s = tech.pop('_close', None)
            try: 
                fund = get_fundamentals(symbol, close_s, spy_close)
            except Exception as e: 
                print(f"[scan api tier fallback] {symbol}: {e}")
                fund = {'_source': 'yfinance_fallback'}
            time.sleep(0.05)

            mkt_cap, beta_val = fund.get('market_cap', 0) or 0, fund.get('beta')
            if mkt_cap > 0 and mkt_cap < 1_500_000_000: stats['low_mktcap']+=1; continue
            if beta_val is not None and 0 < beta_val <= 0.5: stats['low_beta']+=1; continue
            if tech['monthly_dv'] > 0 and tech['monthly_dv'] < 50_000_000: stats['low_vol']+=1; continue

            # 🚀 終極淨化防線
            clean_rs = tech.get('rs_rating')
            if clean_rs is None or (isinstance(clean_rs, float) and np.isnan(clean_rs)):
                clean_rs = 50.0
                
            clean_beta = beta_val
            if clean_beta is None or (isinstance(clean_beta, float) and np.isnan(clean_beta)):
                clean_beta = 1.0

            stats['passed']+=1
            score, signal, signal_label, breakdown = score_stock(tech, fund)
            
            # 🚀 大盤同步強制清洗與遞迴過濾網
            raw_eps = fund.get('eps_yoy')
            is_turn = bool(fund.get('turned_profitable') or (isinstance(raw_eps, (int, float)) and raw_eps < -100))
            
            def _purge(obj):
                import math
                if isinstance(obj, dict): return {k: _purge(v) for k, v in obj.items()}
                if isinstance(obj, list): return [_purge(v) for v in obj]
                if isinstance(obj, float) and math.isnan(obj): return None
                return obj
            
            raw_item = {
                'symbol': symbol, 'company_name': fund.get('company_name', symbol),
                'sector': fund.get('sector', ''), 'industry': fund.get('industry', ''),
                'score': score, 'signal': signal, 'signal_label': signal_label,
                'breakdown': breakdown, 'price': tech['price'],
                'eps_yoy': "由虧轉盈" if is_turn else raw_eps,
                'rev_yoy': fund.get('rev_yoy'), 
                'rs_rating': clean_rs,
                'beta': clean_beta,
                'market_cap': mkt_cap,
                'stop_loss': tech['stop_loss'], 'target': tech['target'], 'rr': tech['rr'],
                'chase_risk': tech['chase_risk'], 'atr_pct': tech['atr_pct'], 
                'rsi': tech['rsi'], 'vol_mult': tech['vol_mult'], 'fund_source': fund.get('_source', '?')
            }
            results.append(_purge(raw_item))
        except Exception as e:
            print(f"[scan severe err] {symbol}: {e}")
            time.sleep(0.05)
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    for r, r_in in enumerate(results): r_in['rank'] = r + 1
    save_scan_results(results, total)

    done = {'is_scanning':False,'progress':100,'current':'完成！已存入','total':total,'done':total,'stats':stats}
    scan_progress.update(done); _wp(progress_file,done); set_meta('scan_status',done)

