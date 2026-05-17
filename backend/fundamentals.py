"""
fundamentals.py — 三層備援基本面數據
Layer 1: yfinance .info + .quarterly_income_stmt
Layer 2: SEC EDGAR XBRL API
Layer 3: OpenBB SEC provider
"""
import yfinance as yf
import pandas as pd
import numpy as np
import requests, json, time
from datetime import datetime
from database import get_fundamental_cache, save_fundamental_cache

HEADERS_SEC = {'User-Agent': 'StockScanner contact@stockscanner.app'}
_cik_cache  = {}

# ── CIK lookup ────────────────────────────────────────────────────────────────
def get_cik(ticker):
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    try:
        r = requests.get('https://www.sec.gov/files/company_tickers.json',
                         headers=HEADERS_SEC, timeout=15)
        if r.status_code == 200:
            for _, v in r.json().items():
                if v.get('ticker', '').upper() == ticker.upper():
                    cik = str(v['cik_str']).zfill(10)
                    _cik_cache[ticker] = cik
                    return cik
    except Exception as e:
        print(f"[cik] {ticker}: {e}")
    return None

# ── Beta from price data ──────────────────────────────────────────────────────
def calc_beta_from_prices(hist_close, spy_close, period=252):
    """Calculate beta using covariance method. period=252 for 1yr, 63 for 3m"""
    try:
        # Use last N days
        s = hist_close.tail(period)
        m = spy_close.tail(period)
        sr = s.pct_change().dropna()
        mr = m.pct_change().dropna()
        aligned = pd.concat([sr, mr], axis=1).dropna()
        aligned.columns = ['s', 'm']
        if len(aligned) < 30:
            return None
        cov = aligned['s'].cov(aligned['m'])
        var = aligned['m'].var()
        return round(float(cov / var), 2) if var else None
    except Exception as e:
        print(f"[beta] calc error: {e}")
        return None

# ── IBD-style RS Rating ───────────────────────────────────────────────────────
def calc_rs_rating(close_series, spy_close):
    """
    IBD-style RS Rating (1-99).
    Weighted return: 40%*3m + 20%*6m + 20%*9m + 20%*12m
    Compared against SPY as benchmark, mapped to 1-99 scale.
    """
    try:
        def ret(series, days):
            if len(series) < days + 5:
                return 0.0
            return (float(series.iloc[-1]) / float(series.iloc[-days]) - 1) * 100

        s_3m  = ret(close_series, 63)
        s_6m  = ret(close_series, 126)
        s_9m  = ret(close_series, 189)
        s_12m = ret(close_series, 252)

        m_3m  = ret(spy_close, 63)
        m_6m  = ret(spy_close, 126)
        m_9m  = ret(spy_close, 189)
        m_12m = ret(spy_close, 252)

        stock_w = 0.4*s_3m + 0.2*s_6m + 0.2*s_9m + 0.2*s_12m
        spy_w   = 0.4*m_3m + 0.2*m_6m + 0.2*m_9m + 0.2*m_12m

        outperf = stock_w - spy_w
        # Map: SPY=50, every 1% outperf ≈ 0.6 points
        rs = 50 + outperf * 0.6
        return round(max(1, min(99, rs)), 1)
    except Exception as e:
        print(f"[rs] calc error: {e}")
        return None

# ── Layer 1: yfinance ─────────────────────────────────────────────────────────
def _from_yfinance(symbol, hist_close=None, spy_close=None):
    result = {}
    ticker = yf.Ticker(symbol)

    # Profile
    try:
        info = ticker.info
        if info and isinstance(info, dict) and len(info) > 3:
            result['company_name'] = info.get('shortName') or info.get('longName') or symbol
            result['sector']       = info.get('sector', '')
            result['industry']     = info.get('industry', '')
            result['beta']         = info.get('beta')
            result['market_cap']   = info.get('marketCap', 0) or 0
    except Exception as e:
        print(f"[L1] {symbol} info: {e}")

    # Market cap fallback
    if not result.get('market_cap'):
        try:
            fi = ticker.fast_info
            result['market_cap'] = getattr(fi, 'market_cap', 0) or 0
        except: pass

    # Beta: calculate from prices if API failed or returned None
    if (result.get('beta') is None) and hist_close is not None and spy_close is not None:
        result['beta'] = calc_beta_from_prices(hist_close, spy_close)
        if result['beta']:
            print(f"[L1] {symbol} beta calculated: {result['beta']}")

    # Income statement
    eps_yoy = rev_yoy = None
    eps_accel_2q = eps_accel_3q = gm_expanding = turned_profitable = False
    try:
        q = ticker.quarterly_income_stmt
        if q is not None and not q.empty and q.shape[1] >= 4:
            ni_row = gp_row = rev_row = None
            for idx in q.index:
                il = str(idx).lower()
                if 'net income' in il and ni_row is None:
                    ni_row = q.loc[idx].values.astype(float)
                if 'gross profit' in il and gp_row is None:
                    gp_row = q.loc[idx].values.astype(float)
                if 'total revenue' in il and rev_row is None:
                    rev_row = q.loc[idx].values.astype(float)

            if ni_row is not None and len(ni_row) >= 5:
                ni = ni_row
                if ni[4] != 0:
                    eps_yoy = round((ni[0]-ni[4])/abs(ni[4])*100, 1)
                gr = []
                for i in range(min(4, len(ni)-1)):
                    if ni[i+1] != 0:
                        gr.append((ni[i]-ni[i+1])/abs(ni[i+1])*100)
                eps_accel_2q      = len(gr)>=2 and gr[0]>gr[1]
                eps_accel_3q      = len(gr)>=3 and gr[0]>gr[1]>gr[2]
                turned_profitable = len(ni)>=2 and ni[0]>0 and ni[1]<=0

            if rev_row is not None and len(rev_row) >= 5:
                rev = rev_row
                if rev[4] > 0:
                    rev_yoy = round((rev[0]-rev[4])/rev[4]*100, 1)

            if gp_row is not None and rev_row is not None and len(gp_row) >= 2:
                gm0 = gp_row[0]/rev_row[0]*100 if rev_row[0] else 0
                gm1 = gp_row[1]/rev_row[1]*100 if rev_row[1] else 0
                gm_expanding = gm0 > gm1

            print(f"[L1] {symbol} income OK: eps={eps_yoy}, rev={rev_yoy}")
    except Exception as e:
        print(f"[L1] {symbol} income_stmt: {e}")

    result.update({
        'eps_yoy': eps_yoy, 'rev_yoy': rev_yoy,
        'eps_accel_2q': eps_accel_2q, 'eps_accel_3q': eps_accel_3q,
        'gm_expanding': gm_expanding, 'turned_profitable': turned_profitable,
        'eps_beat': False, 'rev_beat': False, '_source': 'yfinance',
    })

    has_data = (result.get('company_name','') != symbol or
                result.get('market_cap',0) > 0 or
                result.get('beta') is not None or
                eps_yoy is not None)
    return result if has_data else None

# ── Layer 2: SEC EDGAR ────────────────────────────────────────────────────────
def _from_sec_edgar(symbol):
    result = {'company_name': symbol, 'sector': '', 'industry': '',
              'market_cap': 0, 'beta': None, '_source': 'sec_edgar'}

    cik = get_cik(symbol)
    if not cik:
        return None

    try:
        r = requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
                         headers=HEADERS_SEC, timeout=20)
        if r.status_code != 200:
            return None

        facts   = r.json()
        us_gaap = facts.get('facts', {}).get('us-gaap', {})
        result['company_name'] = facts.get('entityName', symbol)

        def get_qtrs(concept_names):
            for name in concept_names:
                data = us_gaap.get(name, {})
                units = data.get('units', {}).get('USD', [])
                qtrs = sorted(
                    [x for x in units if x.get('form') in ('10-Q','10-K')
                     and x.get('fp','').startswith('Q')],
                    key=lambda x: x.get('end',''), reverse=True
                )[:8]
                if qtrs: return qtrs
            return []

        ni_qtrs  = get_qtrs(['NetIncomeLoss','ProfitLoss','NetIncome'])
        rev_qtrs = get_qtrs(['RevenueFromContractWithCustomerExcludingAssessedTax',
                             'Revenues','SalesRevenueNet','RevenueFromContractWithCustomer'])
        gp_qtrs  = get_qtrs(['GrossProfit'])

        eps_yoy = rev_yoy = None
        eps_accel_2q = eps_accel_3q = gm_expanding = turned_profitable = False

        if len(ni_qtrs) >= 5:
            ni = [q['val'] for q in ni_qtrs]
            if ni[4] != 0:
                eps_yoy = round((ni[0]-ni[4])/abs(ni[4])*100, 1)
            gr = []
            for i in range(min(4, len(ni)-1)):
                if ni[i+1] != 0: gr.append((ni[i]-ni[i+1])/abs(ni[i+1])*100)
            eps_accel_2q      = len(gr)>=2 and gr[0]>gr[1]
            eps_accel_3q      = len(gr)>=3 and gr[0]>gr[1]>gr[2]
            turned_profitable = len(ni)>=2 and ni[0]>0 and ni[1]<=0

        if len(rev_qtrs) >= 5:
            rev = [q['val'] for q in rev_qtrs]
            if rev[4] > 0:
                rev_yoy = round((rev[0]-rev[4])/rev[4]*100, 1)

        if gp_qtrs and rev_qtrs and len(gp_qtrs) >= 2:
            r0 = rev_qtrs[0]['val']; r1 = rev_qtrs[1]['val']
            gm0 = gp_qtrs[0]['val']/r0*100 if r0 else 0
            gm1 = gp_qtrs[1]['val']/r1*100 if r1 else 0
            gm_expanding = gm0 > gm1

        result.update({
            'eps_yoy': eps_yoy, 'rev_yoy': rev_yoy,
            'eps_accel_2q': eps_accel_2q, 'eps_accel_3q': eps_accel_3q,
            'gm_expanding': gm_expanding, 'turned_profitable': turned_profitable,
            'eps_beat': False, 'rev_beat': False,
        })
        print(f"[L2] {symbol} SEC OK: eps={eps_yoy}, rev={rev_yoy}")
        return result
    except Exception as e:
        print(f"[L2] {symbol} SEC: {e}")
        return None

# ── Layer 3: OpenBB ───────────────────────────────────────────────────────────
def _from_openbb(symbol):
    try:
        from openbb import obb
        import warnings; warnings.filterwarnings('ignore')
        result = {'company_name': symbol, 'sector': '', 'industry': '',
                  'market_cap': 0, 'beta': None, '_source': 'openbb'}

        inc = obb.equity.fundamental.income(symbol, provider='sec', period='quarter', limit=5)
        df  = inc.to_df()
        if df is None or df.empty: return None

        ni_col  = next((c for c in df.columns if 'net_income' in c.lower()), None)
        rev_col = next((c for c in df.columns if 'revenue'    in c.lower()), None)

        eps_yoy = rev_yoy = None
        if ni_col and len(df) >= 5:
            ni = df[ni_col].values.astype(float)
            if ni[4] != 0: eps_yoy = round((ni[0]-ni[4])/abs(ni[4])*100, 1)
        if rev_col and len(df) >= 5:
            rev = df[rev_col].values.astype(float)
            if rev[4] > 0: rev_yoy = round((rev[0]-rev[4])/rev[4]*100, 1)

        result.update({'eps_yoy': eps_yoy, 'rev_yoy': rev_yoy,
                       'eps_accel_2q': False, 'eps_accel_3q': False,
                       'gm_expanding': False, 'turned_profitable': False,
                       'eps_beat': False, 'rev_beat': False})
        print(f"[L3] {symbol} OpenBB OK: eps={eps_yoy}, rev={rev_yoy}")
        return result
    except Exception as e:
        print(f"[L3] {symbol} OpenBB: {e}")
        return None

# ── Public interface ──────────────────────────────────────────────────────────
def get_fundamentals(symbol, hist_close=None, spy_close=None):
    """Three-layer fetch with 7-day SQLite cache. Always returns dict."""
    cached = get_fundamental_cache(symbol)
    if cached:
        # Recalc beta from prices if cached beta is None
        if cached.get('beta') is None and hist_close is not None and spy_close is not None:
            beta = calc_beta_from_prices(hist_close, spy_close)
            if beta:
                cached['beta'] = beta
                save_fundamental_cache(symbol, cached)
        return cached

    empty = {
        'company_name': symbol, 'sector': '', 'industry': '',
        'market_cap': 0, 'beta': None,
        'eps_yoy': None, 'rev_yoy': None,
        'eps_accel_2q': False, 'eps_accel_3q': False,
        'gm_expanding': False, 'turned_profitable': False,
        'eps_beat': False, 'rev_beat': False, '_source': 'none',
    }

    # Layer 1
    result = _from_yfinance(symbol, hist_close, spy_close)

    # Layer 2 if income data missing
    if result is None or (result.get('eps_yoy') is None and result.get('rev_yoy') is None):
        sec = _from_sec_edgar(symbol)
        if sec:
            if result:
                sec['beta']       = result.get('beta') or sec.get('beta')
                sec['market_cap'] = result.get('market_cap') or sec.get('market_cap',0)
                sec['sector']     = result.get('sector') or ''
                sec['industry']   = result.get('industry') or ''
                sec['company_name'] = result.get('company_name', symbol)
            result = sec

    # Layer 3 last resort
    if result is None or (result.get('eps_yoy') is None and result.get('rev_yoy') is None):
        obb = _from_openbb(symbol)
        if obb:
            if result:
                obb['beta']       = result.get('beta') or obb.get('beta')
                obb['market_cap'] = result.get('market_cap') or obb.get('market_cap',0)
                obb['sector']     = result.get('sector') or ''
                obb['company_name'] = result.get('company_name', symbol)
            result = obb

    final = result or empty
    for k, v in empty.items():
        if k not in final: final[k] = v

    # Final beta calculation attempt
    if final.get('beta') is None and hist_close is not None and spy_close is not None:
        final['beta'] = calc_beta_from_prices(hist_close, spy_close)

    save_fundamental_cache(symbol, final)
    print(f"[fund] {symbol}: src={final.get('_source')}, eps={final.get('eps_yoy')}, beta={final.get('beta')}")
    return final
