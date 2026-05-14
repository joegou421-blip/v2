import yfinance as yf
import numpy as np
import pandas as pd
import requests
import json
import time
import os
from datetime import datetime, timedelta

FMP_API_KEY = 'hL75f5DvVpnbuzMiibTuz0QCEb7lBDEI'
FMP_BASE = 'https://financialmodelingprep.com/api/v3'

scan_progress = {'progress': 0, 'current': '', 'total': 500, 'done': 0}

SP500_TICKERS = [
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','BRK-B','JPM','XOM',
    'UNH','JNJ','V','PG','MA','HD','CVX','MRK','ABBV','PEP',
    'COST','AVGO','LLY','ADBE','CSCO','WMT','ACN','MCD','CRM','BAC',
    'TMO','ABT','TXN','NEE','QCOM','DHR','PM','AMGN','UPS','RTX',
    'HON','SBUX','IBM','INTC','INTU','SPGI','GS','MS','CAT','BLK',
    'GE','AXP','MDLZ','ADI','GILD','ISRG','DE','ADP','MMC','SYK',
    'CB','BKNG','CVS','REGN','ZTS','LRCX','ETN','NOC','TJX','MO',
    'SO','DUK','EMR','VRTX','AON','HUM','CI','APD','PSA','NSC',
    'ITW','F','GM','KMB','ECL','ROP','EW','D','SHW','MCO',
    'ORLY','CME','MNST','PCAR','FDX','A','OXY','TEL','WM','KLAC',
    'ROST','STZ','DXCM','CTAS','MRNA','SNPS','CDNS','AIG','HCA','BDX',
    'FTNT','BIIB','DG','CTSH','WBA','MSI','TFC','PAYX','VLO','PH',
    'GPN','OTIS','CARR','ALL','HLT','EA','EBAY','FAST','MTD','IDXX',
    'KEYS','WST','PPG','EFX','MCHP','ON','HPQ','BAX','TROW','DLR',
    'WEC','AWK','ES','XEL','IFF','LUV','AAL','DAL','UAL','ALK',
    'KHC','CPB','GIS','K','SJM','CAG','MKC','CLX','CHD','EL',
    'RL','TPR','PVH','HBI','NKE','VFC','GPS','M','JWN','KSS',
    'MAR','H','IHG','RCL','CCL','NCLH','MGM','LVS','WYNN','CZR',
    'DIS','CMCSA','NFLX','PARA','WBD','FOX','FOXA','OMC','IPG','NYT',
    'T','VZ','TMUS','LUMN','FYBR','WBD','CHTR','DISH','SIRI','OMF',
    'PFE','BMY','LLY','AMGN','GILD','BIIB','VRTX','REGN','ILMN','ALNY',
    'DXCM','HOLX','BAX','BSX','EW','ISRG','MDT','SYK','ZBH','XRAY',
    'C','WFC','BAC','USB','PNC','TFC','COF','AXP','DFS','SYF',
    'JPM','GS','MS','BLK','SCHW','ICE','CME','NDAQ','CBOE','FDS',
    'AMT','PLD','CCI','EQIX','PSA','AVB','EQR','WY','DLR','SBAC',
    'O','WELL','VTR','SPG','MAC','KIM','REG','BXP','SLG','VNO',
    'XOM','CVX','COP','EOG','PXD','DVN','MRO','APA','HAL','SLB',
    'BKR','FTI','NOV','HP','RIG','VLO','PSX','MPC','HES','OXY',
    'LIN','APD','EMN','CE','HUN','FMC','CF','MOS','NUE','STLD',
    'X','CLF','AA','FCX','NEM','GOLD','AEM','WPM','FNV','PAAS',
    'CAT','DE','CMI','PCAR','TEX','OSK','WNC','AGCO','CNH','TXT',
    'GD','LMT','NOC','RTX','BA','HII','L3H','TDG','HEI','SPR',
    'FDX','UPS','XPO','CHRW','EXPD','JBHT','KNX','WERN','ODFL','SAIA',
    'AMZN','BABA','JD','PDD','MELI','SHOP','SE','GRAB','DIDI','LCID',
    'PLTR','SNOW','DDOG','NET','ZS','CRWD','S','PANW','OKTA','VEEV',
    'HUBS','TWLO','MDB','DOCN','GTLB','CFLT','BILL','APP','TTD','RBLX',
    'U','ABNB','UBER','LYFT','DASH','ABNB','VRBO','EXPE','TRIP','BKNG',
    'COIN','HOOD','SOFI','AFRM','UPST','LC','OPEN','OFFERPAD','OPENDOOR',
    'ZM','DOCU','ESTC','SPLK','SUMO','NEWR','DDOG','FSLY','CLDR','BOX',
    'NOW','WDAY','CRM','ADSK','ANSS','PTC','CTXS','VRNT','MANH','EPAM',
    'AMAT','LRCX','KLAC','ASML','ENTG','MKSI','ONTO','ACLS','CAMT','PLAB',
    'AMD','INTC','QCOM','AVGO','MRVL','SWKS','QRVO','MPWR','AAON','SLAB',
    'NXPI','TI','STM','IFNNY','TSM','MU','WDC','STX','NTAP','PSTG',
    'MSFT','ORCL','SAP','IBM','INFY','WIT','CTSH','ACN','EPAM','GLOB',
    'GOOGL','META','SNAP','PINS','TWTR','MTCH','BMBL','YELP','ANGI','ZG',
    'AMZN','MSFT','GOOGL','ORCL','CRM','WDAY','NOW','VEEV','HUBS','ADSK'
]

# Deduplicate
SP500_TICKERS = list(dict.fromkeys(SP500_TICKERS))

def fmp_get(endpoint, params=None):
    if params is None:
        params = {}
    params['apikey'] = FMP_API_KEY
    try:
        r = requests.get(f'{FMP_BASE}{endpoint}', params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
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

def get_swing_low(low_series, lookback=20):
    """Get recent swing low"""
    recent = low_series.iloc[-lookback:]
    return float(recent.min())

def compute_rs_rating(stock_returns, spy_returns):
    """RS Rating 0-100 vs SPY"""
    if spy_returns == 0 or spy_returns is None:
        return 50
    ratio = stock_returns / abs(spy_returns) if spy_returns != 0 else 1
    # Normalize to 0-100
    rating = min(100, max(0, 50 + ratio * 25))
    return round(rating, 1)

def get_technical_data(symbol, spy_hist=None):
    """Get technical indicators for a symbol"""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period='1y')
        if len(hist) < 60:
            return None

        close = hist['Close']
        high = hist['High']
        low = hist['Low']
        volume = hist['Volume']

        current_price = float(close.iloc[-1])

        # Moving averages
        sma50 = float(close.rolling(50).mean().iloc[-1])
        sma200_series = close.rolling(200).mean()
        if len(close) < 200:
            return None
        sma200 = float(sma200_series.iloc[-1])
        sma200_prev = float(sma200_series.iloc[-11]) if len(sma200_series) >= 11 else sma200

        # Stage 2 criteria
        sma200_rising = sma200 > sma200_prev
        stage2 = (current_price > sma200 and sma50 > sma200 and sma200_rising)

        if not stage2:
            return None  # Pre-filter

        # ATR
        atr = float(compute_atr(high, low, close).iloc[-1])
        atr_pct = (atr / current_price) * 100

        # RSI
        rsi = float(compute_rsi(close).iloc[-1])

        # Volume
        avg_vol_20 = float(volume.rolling(20).mean().iloc[-1])
        curr_vol = float(volume.iloc[-1])
        vol_mult = round(curr_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

        # Monthly dollar volume
        monthly_dollar_vol = current_price * float(volume.tail(21).mean()) * 21

        # RS Rating vs SPY
        ret_6m = (current_price / float(close.iloc[-126]) - 1) * 100 if len(close) >= 126 else 0
        spy_ret_6m = 0
        if spy_hist is not None and len(spy_hist) >= 126:
            spy_ret_6m = (float(spy_hist['Close'].iloc[-1]) / float(spy_hist['Close'].iloc[-126]) - 1) * 100
        rs_rating = compute_rs_rating(ret_6m, spy_ret_6m)

        # Swing low & stop loss
        swing_low = get_swing_low(low, lookback=20)
        stop_loss = round(swing_low * 0.99, 2)
        risk = current_price - stop_loss
        target = round(current_price + risk * 2.5, 2)
        rr = round((target - current_price) / risk, 2) if risk > 0 else 0

        # ATR chase filter
        atr_distance = risk / atr if atr > 0 else 0
        chase_risk = atr_distance > 1.5

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
    except Exception as e:
        return None

def get_fundamental_data(symbol):
    """Get fundamental data from FMP - earnings, revenue, margins"""
    try:
        # Income statement (quarterly)
        income = fmp_get(f'/income-statement/{symbol}', {'period': 'quarter', 'limit': 8})
        if not income or len(income) < 4:
            return {}

        # Analyst estimates
        estimates = fmp_get(f'/analyst-estimates/{symbol}', {'period': 'quarter', 'limit': 4})

        # Company profile (market cap, beta)
        profile = fmp_get(f'/profile/{symbol}')

        result = {}

        # Market cap & beta from profile
        if profile and len(profile) > 0:
            p = profile[0]
            result['market_cap'] = p.get('mktCap', 0)
            result['beta'] = p.get('beta', 1.0)
            result['company_name'] = p.get('companyName', symbol)
            result['sector'] = p.get('sector', '')
            result['industry'] = p.get('industry', '')

        # EPS & Revenue analysis
        eps_list = []
        rev_list = []
        gm_list = []

        for q in income[:6]:
            eps = q.get('eps', 0) or 0
            rev = q.get('revenue', 0) or 0
            gross_profit = q.get('grossProfit', 0) or 0
            gm = (gross_profit / rev * 100) if rev > 0 else 0
            eps_list.append(eps)
            rev_list.append(rev)
            gm_list.append(gm)

        # EPS YoY growth (most recent Q vs same Q last year)
        eps_yoy = 0
        if len(eps_list) >= 5 and eps_list[4] != 0:
            eps_yoy = ((eps_list[0] - eps_list[4]) / abs(eps_list[4])) * 100

        # EPS acceleration
        eps_growth_rates = []
        for i in range(min(4, len(eps_list) - 1)):
            if eps_list[i + 1] != 0:
                g = ((eps_list[i] - eps_list[i + 1]) / abs(eps_list[i + 1])) * 100
                eps_growth_rates.append(g)

        eps_accel_2q = len(eps_growth_rates) >= 2 and eps_growth_rates[0] > eps_growth_rates[1]
        eps_accel_3q = len(eps_growth_rates) >= 3 and eps_growth_rates[0] > eps_growth_rates[1] > eps_growth_rates[2]

        # Revenue growth YoY
        rev_yoy = 0
        if len(rev_list) >= 5 and rev_list[4] > 0:
            rev_yoy = ((rev_list[0] - rev_list[4]) / rev_list[4]) * 100

        # Gross margin expansion
        gm_expanding = len(gm_list) >= 2 and gm_list[0] > gm_list[1]

        # Turnaround (loss to profit)
        turned_profitable = len(eps_list) >= 2 and eps_list[0] > 0 and eps_list[1] <= 0

        # EPS & Revenue beat
        eps_beat = False
        rev_beat = False
        if estimates and len(estimates) > 0:
            est = estimates[0]
            actual_eps = income[0].get('eps', 0) or 0 if income else 0
            actual_rev = income[0].get('revenue', 0) or 0 if income else 0
            est_eps = est.get('estimatedEpsAvg', 0) or 0
            est_rev = est.get('estimatedRevenueAvg', 0) or 0
            eps_beat = actual_eps > est_eps if est_eps != 0 else False
            rev_beat = actual_rev > est_rev if est_rev != 0 else False

        result.update({
            'eps_yoy': round(eps_yoy, 1),
            'rev_yoy': round(rev_yoy, 1),
            'eps_accel_2q': eps_accel_2q,
            'eps_accel_3q': eps_accel_3q,
            'gm_expanding': gm_expanding,
            'turned_profitable': turned_profitable,
            'eps_beat': eps_beat,
            'rev_beat': rev_beat,
        })

        return result
    except Exception as e:
        return {}

def score_stock(tech, fund):
    """Calculate 0-15 score"""
    score = 0
    breakdown = {}

    # 1. EPS YoY > 20%
    eps_yoy_pass = fund.get('eps_yoy', 0) > 20
    breakdown['eps_yoy'] = {'pass': eps_yoy_pass, 'points': 2, 'value': fund.get('eps_yoy', 0), 'label': 'EPS年增長 > 20%'}
    if eps_yoy_pass:
        score += 2

    # 2. EPS accel 2Q
    eps_accel_2q = fund.get('eps_accel_2q', False)
    breakdown['eps_accel_2q'] = {'pass': eps_accel_2q, 'points': 2, 'label': 'EPS連續2季加速'}
    if eps_accel_2q:
        score += 2

    # 3. EPS accel 3Q
    eps_accel_3q = fund.get('eps_accel_3q', False)
    breakdown['eps_accel_3q'] = {'pass': eps_accel_3q, 'points': 1, 'label': 'EPS連續3季加速'}
    if eps_accel_3q:
        score += 1

    # 4. Revenue growth > 15%
    rev_pass = fund.get('rev_yoy', 0) > 15
    breakdown['rev_growth'] = {'pass': rev_pass, 'points': 1, 'value': fund.get('rev_yoy', 0), 'label': '收入增長 > 15%'}
    if rev_pass:
        score += 1

    # 5. EPS beat
    eps_beat = fund.get('eps_beat', False)
    breakdown['eps_beat'] = {'pass': eps_beat, 'points': 2, 'label': 'EPS超越分析師預期'}
    if eps_beat:
        score += 2

    # 6. Revenue beat
    rev_beat = fund.get('rev_beat', False)
    breakdown['rev_beat'] = {'pass': rev_beat, 'points': 1, 'label': '收入超越分析師預期'}
    if rev_beat:
        score += 1

    # 7. Gross margin expanding
    gm_expanding = fund.get('gm_expanding', False)
    breakdown['gm_expanding'] = {'pass': gm_expanding, 'points': 1, 'label': '毛利率擴張'}
    if gm_expanding:
        score += 1

    # 8. Turned profitable
    turned = fund.get('turned_profitable', False)
    breakdown['turned_profitable'] = {'pass': turned, 'points': 1, 'label': '由虧轉盈'}
    if turned:
        score += 1

    # 9. RS Rating > 70
    rs_pass = tech.get('rs_rating', 0) > 70
    breakdown['rs_rating'] = {'pass': rs_pass, 'points': 2, 'value': tech.get('rs_rating', 0), 'label': 'RS評級 > 70'}
    if rs_pass:
        score += 2

    # 10. Stage 2 confirmed
    stage2 = tech.get('stage2', False)
    breakdown['stage2'] = {'pass': stage2, 'points': 2, 'label': 'Stage 2 確認'}
    if stage2:
        score += 2

    # Signal level
    if score >= 12:
        signal = 'strong_buy'
        signal_label = '強烈買入'
    elif score >= 9:
        signal = 'buy'
        signal_label = '買入'
    elif score >= 6:
        signal = 'neutral_positive'
        signal_label = '中性偏好'
    else:
        signal = 'watch'
        signal_label = '觀望'

    return score, signal, signal_label, breakdown

def run_full_scan(cache_file):
    global scan_progress
    scan_progress = {'progress': 0, 'current': '初始化...', 'total': len(SP500_TICKERS), 'done': 0}

    # Get SPY data for RS calculation
    try:
        spy_hist = yf.Ticker('SPY').history(period='1y')
    except:
        spy_hist = None

    results = []
    tickers = SP500_TICKERS
    total = len(tickers)

    # Batch download prices for efficiency
    scan_progress['current'] = '批量下載價格數據...'

    for i, symbol in enumerate(tickers):
        scan_progress['current'] = symbol
        scan_progress['done'] = i
        scan_progress['progress'] = int((i / total) * 100)

        try:
            # Technical analysis
            tech = get_technical_data(symbol, spy_hist)
            if not tech:
                continue  # Stage 2 filter failed

            # Pre-filter by market cap and beta (quick check)
            mkt_cap = None
            beta = None

            # Get fundamental data for top candidates
            fund = get_fundamental_data(symbol)

            mkt_cap = fund.get('market_cap', 0)
            beta_val = fund.get('beta', 1.0)

            # Filters: market cap > 2B, beta > 1, monthly dollar vol > 900M
            if mkt_cap and mkt_cap < 2_000_000_000:
                continue
            if beta_val and beta_val <= 1.0:
                continue
            if tech['monthly_dollar_vol'] < 900_000_000:
                continue

            score, signal, signal_label, breakdown = score_stock(tech, fund)

            results.append({
                'symbol': symbol,
                'company_name': fund.get('company_name', symbol),
                'sector': fund.get('sector', ''),
                'industry': fund.get('industry', ''),
                'score': score,
                'signal': signal,
                'signal_label': signal_label,
                'breakdown': breakdown,
                'price': tech['price'],
                'eps_yoy': fund.get('eps_yoy', 0),
                'rev_yoy': fund.get('rev_yoy', 0),
                'rs_rating': tech['rs_rating'],
                'beta': fund.get('beta', tech.get('beta', 1)),
                'market_cap': mkt_cap,
                'stop_loss': tech['stop_loss'],
                'target': tech['target'],
                'rr': tech['rr'],
                'chase_risk': tech['chase_risk'],
                'atr_pct': tech['atr_pct'],
                'rsi': tech['rsi'],
                'vol_mult': tech['vol_mult'],
            })

            time.sleep(0.05)  # Rate limiting

        except Exception as e:
            continue

    # Sort by score
    results.sort(key=lambda x: x['score'], reverse=True)

    # Add rankings
    for i, r in enumerate(results):
        r['rank'] = i + 1

    # Cache results
    cache_data = {
        'results': results,
        'scanned_at': datetime.now().isoformat(),
        'total_scanned': total,
        'passed': len(results)
    }

    with open(cache_file, 'w') as f:
        json.dump(cache_data, f)

    scan_progress['progress'] = 100
    scan_progress['current'] = f'完成！找到 {len(results)} 支股票'
    scan_progress['done'] = total

    return results

def get_cached_results(cache_file):
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        # Check if cache is within 24 hours
        scanned_at = datetime.fromisoformat(data['scanned_at'])
        if datetime.now() - scanned_at > timedelta(hours=24):
            return None
        return data
    except:
        return None

def score_single_stock(symbol):
    """Score a single stock for search page"""
    try:
        spy_hist = yf.Ticker('SPY').history(period='1y')
        tech = get_technical_data(symbol, spy_hist)

        fund = get_fundamental_data(symbol)

        if not tech:
            # Still return basic data even if not Stage 2
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period='6mo')
            if len(hist) < 10:
                return None

            close = hist['Close']
            current_price = float(close.iloc[-1])
            prev = float(close.iloc[-2])

            from market import compute_rsi, compute_atr
            rsi = float(compute_rsi(close).iloc[-1]) if len(close) >= 14 else 50

            profile = fmp_get(f'/profile/{symbol}')
            company_name = symbol
            sector = ''
            if profile and len(profile) > 0:
                company_name = profile[0].get('companyName', symbol)
                sector = profile[0].get('sector', '')

            return {
                'symbol': symbol,
                'company_name': company_name,
                'sector': sector,
                'price': round(current_price, 2),
                'score': 0,
                'signal': 'watch',
                'signal_label': '觀望',
                'breakdown': {},
                'stage2': False,
                'rsi': round(rsi, 1),
                'not_stage2': True,
                'eps_yoy': fund.get('eps_yoy', 0),
                'rev_yoy': fund.get('rev_yoy', 0),
            }

        score, signal, signal_label, breakdown = score_stock(tech, fund)

        return {
            'symbol': symbol,
            'company_name': fund.get('company_name', symbol),
            'sector': fund.get('sector', ''),
            'industry': fund.get('industry', ''),
            'score': score,
            'signal': signal,
            'signal_label': signal_label,
            'breakdown': breakdown,
            'price': tech['price'],
            'eps_yoy': fund.get('eps_yoy', 0),
            'rev_yoy': fund.get('rev_yoy', 0),
            'rs_rating': tech['rs_rating'],
            'beta': fund.get('beta', 1),
            'stop_loss': tech['stop_loss'],
            'target': tech['target'],
            'rr': tech['rr'],
            'chase_risk': tech['chase_risk'],
            'atr_pct': tech['atr_pct'],
            'rsi': tech['rsi'],
            'vol_mult': tech['vol_mult'],
            'stage2': tech['stage2'],
        }
    except Exception as e:
        return None
