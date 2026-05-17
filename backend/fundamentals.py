"""
fundamentals.py — 三層備援基本面數據
L1: yfinance .info + .quarterly_income_stmt
L2: SEC EDGAR XBRL API (直接用 ticker 查 CIK)
L3: OpenBB SEC provider
"""
import yfinance as yf
import pandas as pd
import numpy as np
import requests, json, time, re
from datetime import datetime, timedelta
from database import get_fundamental_cache, save_fundamental_cache

HEADERS_SEC = {'User-Agent': 'StockScanner/1.0 contact@example.com Accept-Encoding: gzip, deflate'}

# ── CIK lookup (fast: use direct ticker endpoint) ────────────────────────────
_cik_cache = {}

def get_cik(ticker):
    """Get SEC CIK for a ticker. Uses fast EDGAR ticker endpoint."""
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    try:
        # Method 1: Direct ticker lookup (fastest, ~1KB response)
        url = f'https://data.sec.gov/submissions/CIK.json'  # placeholder
        # Use the company_tickers_exchange.json (smaller, ~2MB vs 40MB)
        r = requests.get(
            'https://www.sec.gov/files/company_tickers_exchange.json',
            headers=HEADERS_SEC, timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            fields = data.get('fields', [])  # ['cik', 'name', 'ticker', 'exchange']
            rows   = data.get('data', [])
            cik_i    = fields.index('cik')    if 'cik'    in fields else 0
            ticker_i = fields.index('ticker') if 'ticker' in fields else 2
            for row in rows:
                if str(row[ticker_i]).upper() == ticker.upper():
                    cik = str(row[cik_i]).zfill(10)
                    _cik_cache[ticker] = cik
                    return cik
    except Exception as e:
        print(f"[cik] {ticker}: {e}")

    # Method 2: EDGAR full-text search
    try:
        r = requests.get(
            f'https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=10-K&hits.hits.total.value=1',
            headers=HEADERS_SEC, timeout=10
        )
        if r.status_code == 200:
            hits = r.json().get('hits', {}).get('hits', [])
            if hits:
                entity_id = hits[0].get('_source', {}).get('entity_id', '')
                if entity_id:
                    cik = str(entity_id).zfill(10)
                    _cik_cache[ticker] = cik
                    return cik
    except Exception as e:
        print(f"[cik] {ticker} method2: {e}")

    return None

# ── Beta from price data (always reliable) ───────────────────────────────────
def calc_beta_from_prices(hist_close, spy_close, period=252):
    try:
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
        print(f"[beta] {e}")
        return None

# ── IBD-style RS Rating ───────────────────────────────────────────────────────
def calc_rs_rating(close_series, spy_close):
    """
    IBD weighted: 40%×3m + 20%×6m + 20%×9m + 20%×12m vs SPY
    Returns 1-99
    """
    try:
        def pct(s, d):
            s = s.dropna()
            if len(s) < d+2: return 0.0
            return (float(s.iloc[-1]) / float(s.iloc[-d]) - 1) * 100

        sw = 0.4*pct(close_series,63) + 0.2*pct(close_series,126) + \
             0.2*pct(close_series,189) + 0.2*pct(close_series,252)
        mw = 0.4*pct(spy_close,63)    + 0.2*pct(spy_close,126)    + \
             0.2*pct(spy_close,189)   + 0.2*pct(spy_close,252)

        outperf = sw - mw
        rs = 50 + outperf * 0.6
        return round(max(1, min(99, rs)), 1)
    except Exception as e:
        print(f"[rs] {e}")
        return None

# ── Layer 1: yfinance ─────────────────────────────────────────────────────────
def _from_yfinance(symbol, hist_close=None, spy_close=None):
    result = {'_source': 'yfinance'}
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
            print(f"[L1] {symbol} info OK: {result.get('company_name')}")
    except Exception as e:
        print(f"[L1] {symbol} info err: {e}")

    # fast_info market_cap fallback
    if not result.get('market_cap'):
        try:
            result['market_cap'] = getattr(ticker.fast_info, 'market_cap', 0) or 0
        except: pass

    # Beta from prices (most reliable)
    if result.get('beta') is None and hist_close is not None and spy_close is not None:
        result['beta'] = calc_beta_from_prices(hist_close, spy_close)

    # Income statement
    eps_yoy = rev_yoy = None
    accel2 = accel3 = gm_exp = turned = False
    try:
        q = ticker.quarterly_income_stmt
        if q is not None and not q.empty and q.shape[1] >= 4:
            ni_row = gp_row = rev_row = None
            for idx in q.index:
                il = str(idx).lower()
                if 'net income' in il and ni_row is None:   ni_row  = q.loc[idx].values.astype(float)
                if 'gross profit' in il and gp_row is None: gp_row  = q.loc[idx].values.astype(float)
                if 'total revenue' in il and rev_row is None: rev_row = q.loc[idx].values.astype(float)

            if ni_row is not None and len(ni_row) >= 5:
                ni = ni_row
                if ni[4] != 0: eps_yoy = round((ni[0]-ni[4])/abs(ni[4])*100, 1)
                gr = [(ni[i]-ni[i+1])/abs(ni[i+1])*100 for i in range(min(4,len(ni)-1)) if ni[i+1]!=0]
                accel2  = len(gr)>=2 and gr[0]>gr[1]
                accel3  = len(gr)>=3 and gr[0]>gr[1]>gr[2]
                turned  = len(ni)>=2 and ni[0]>0 and ni[1]<=0

            if rev_row is not None and len(rev_row) >= 5:
                rev = rev_row
                if rev[4] > 0: rev_yoy = round((rev[0]-rev[4])/rev[4]*100, 1)

            if gp_row is not None and rev_row is not None and len(gp_row) >= 2:
                gm_exp = (gp_row[0]/rev_row[0] if rev_row[0] else 0) > (gp_row[1]/rev_row[1] if rev_row[1] else 0)

            print(f"[L1] {symbol} income OK: eps={eps_yoy}, rev={rev_yoy}")
        else:
            print(f"[L1] {symbol} income_stmt empty: shape={getattr(q,'shape','N/A')}")
    except Exception as e:
        print(f"[L1] {symbol} income err: {e}")

    result.update({'eps_yoy':eps_yoy,'rev_yoy':rev_yoy,'eps_accel_2q':accel2,
                   'eps_accel_3q':accel3,'gm_expanding':gm_exp,'turned_profitable':turned,
                   'eps_beat':False,'rev_beat':False})

    has_data = (result.get('company_name','')!=symbol or result.get('market_cap',0)>0
                or result.get('beta') is not None or eps_yoy is not None)
    return result if has_data else None

# ── Layer 2: SEC EDGAR XBRL ───────────────────────────────────────────────────
def _from_sec_edgar(symbol):
    result = {'company_name':symbol,'sector':'','industry':'',
              'market_cap':0,'beta':None,'_source':'sec_edgar'}
    cik = get_cik(symbol)
    if not cik:
        print(f"[L2] {symbol}: CIK not found")
        return None
    try:
        r = requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
                         headers=HEADERS_SEC, timeout=25)
        if r.status_code != 200:
            print(f"[L2] {symbol} CIK={cik}: HTTP {r.status_code}")
            return None

        facts   = r.json()
        us_gaap = facts.get('facts', {}).get('us-gaap', {})
        result['company_name'] = facts.get('entityName', symbol)

        def get_qtrs(names):
            for n in names:
                units = us_gaap.get(n, {}).get('units', {}).get('USD', [])
                qtrs = sorted(
                    [x for x in units if x.get('form') in ('10-Q','10-K')
                     and x.get('fp','Q').startswith('Q')],
                    key=lambda x: x.get('end',''), reverse=True
                )[:8]
                if len(qtrs) >= 4: return qtrs
            return []

        ni_q  = get_qtrs(['NetIncomeLoss','ProfitLoss','NetIncome'])
        rev_q = get_qtrs(['RevenueFromContractWithCustomerExcludingAssessedTax',
                           'Revenues','SalesRevenueNet','SalesRevenueGoodsNet'])
        gp_q  = get_qtrs(['GrossProfit'])

        eps_yoy = rev_yoy = None
        accel2 = accel3 = gm_exp = turned = False

        if len(ni_q) >= 5:
            ni = [q['val'] for q in ni_q]
            if ni[4] != 0: eps_yoy = round((ni[0]-ni[4])/abs(ni[4])*100, 1)
            gr = [(ni[i]-ni[i+1])/abs(ni[i+1])*100 for i in range(min(4,len(ni)-1)) if ni[i+1]!=0]
            accel2 = len(gr)>=2 and gr[0]>gr[1]
            accel3 = len(gr)>=3 and gr[0]>gr[1]>gr[2]
            turned = len(ni)>=2 and ni[0]>0 and ni[1]<=0

        if len(rev_q) >= 5:
            rev = [q['val'] for q in rev_q]
            if rev[4] > 0: rev_yoy = round((rev[0]-rev[4])/rev[4]*100, 1)

        if gp_q and rev_q and len(gp_q)>=2 and len(rev_q)>=2:
            gm_exp = (gp_q[0]['val']/rev_q[0]['val'] if rev_q[0]['val'] else 0) > \
                     (gp_q[1]['val']/rev_q[1]['val'] if rev_q[1]['val'] else 0)

        result.update({'eps_yoy':eps_yoy,'rev_yoy':rev_yoy,'eps_accel_2q':accel2,
                       'eps_accel_3q':accel3,'gm_expanding':gm_exp,'turned_profitable':turned,
                       'eps_beat':False,'rev_beat':False})
        print(f"[L2] {symbol} CIK={cik} OK: eps={eps_yoy}, rev={rev_yoy}")
        return result
    except Exception as e:
        print(f"[L2] {symbol}: {e}")
        return None

# ── Layer 3: OpenBB ───────────────────────────────────────────────────────────
def _from_openbb(symbol):
    try:
        from openbb import obb
        import warnings; warnings.filterwarnings('ignore')
        inc = obb.equity.fundamental.income(symbol, provider='yfinance', period='quarter', limit=5)
        df  = inc.to_df()
        if df is None or df.empty: return None

        result = {'company_name':symbol,'sector':'','industry':'',
                  'market_cap':0,'beta':None,'_source':'openbb'}

        ni_col  = next((c for c in df.columns if 'net_income' in c.lower()), None)
        rev_col = next((c for c in df.columns if 'revenue'    in c.lower()), None)
        gp_col  = next((c for c in df.columns if 'gross_profit' in c.lower()), None)

        eps_yoy = rev_yoy = None
        if ni_col and len(df)>=5:
            ni = df[ni_col].values.astype(float)
            if ni[4]!=0: eps_yoy = round((ni[0]-ni[4])/abs(ni[4])*100,1)
        if rev_col and len(df)>=5:
            rev = df[rev_col].values.astype(float)
            if rev[4]>0: rev_yoy = round((rev[0]-rev[4])/rev[4]*100,1)

        result.update({'eps_yoy':eps_yoy,'rev_yoy':rev_yoy,'eps_accel_2q':False,
                       'eps_accel_3q':False,'gm_expanding':False,'turned_profitable':False,
                       'eps_beat':False,'rev_beat':False})
        print(f"[L3] {symbol} OpenBB OK: eps={eps_yoy}, rev={rev_yoy}")
        return result
    except Exception as e:
        print(f"[L3] {symbol}: {e}")
        return None

# ── Public interface ──────────────────────────────────────────────────────────
def get_fundamentals(symbol, hist_close=None, spy_close=None):
    """Three-layer fetch with 7-day cache. Always returns dict."""
    cached = get_fundamental_cache(symbol)
    if cached:
        # Re-calc beta if missing
        if cached.get('beta') is None and hist_close is not None and spy_close is not None:
            beta = calc_beta_from_prices(hist_close, spy_close)
            if beta:
                cached['beta'] = beta
                save_fundamental_cache(symbol, cached)
        return cached

    empty = {'company_name':symbol,'sector':'','industry':'','market_cap':0,'beta':None,
             'eps_yoy':None,'rev_yoy':None,'eps_accel_2q':False,'eps_accel_3q':False,
             'gm_expanding':False,'turned_profitable':False,'eps_beat':False,'rev_beat':False,
             '_source':'none'}

    # L1: yfinance
    result = _from_yfinance(symbol, hist_close, spy_close)

    # L2: SEC EDGAR (if income data missing)
    if not result or (result.get('eps_yoy') is None and result.get('rev_yoy') is None):
        sec = _from_sec_edgar(symbol)
        if sec:
            if result:
                sec['beta']       = result.get('beta') or sec.get('beta')
                sec['market_cap'] = result.get('market_cap') or sec.get('market_cap',0)
                sec['sector']     = result.get('sector','')
                sec['industry']   = result.get('industry','')
                sec['company_name'] = result.get('company_name', symbol)
            result = sec

    # L3: OpenBB (last resort)
    if not result or (result.get('eps_yoy') is None and result.get('rev_yoy') is None):
        obb = _from_openbb(symbol)
        if obb:
            if result:
                obb.update({'beta':result.get('beta') or obb.get('beta'),
                            'market_cap':result.get('market_cap') or obb.get('market_cap',0),
                            'sector':result.get('sector',''),
                            'industry':result.get('industry',''),
                            'company_name':result.get('company_name',symbol)})
            result = obb

    final = result or empty
    for k, v in empty.items():
        if k not in final: final[k] = v

    # Final beta attempt
    if final.get('beta') is None and hist_close is not None and spy_close is not None:
        final['beta'] = calc_beta_from_prices(hist_close, spy_close)

    save_fundamental_cache(symbol, final)
    print(f"[fund] {symbol}: src={final.get('_source')}, eps={final.get('eps_yoy')}, "
          f"rev={final.get('rev_yoy')}, beta={final.get('beta')}, mktcap={final.get('market_cap',0)//1e9:.1f}B")
    return final
