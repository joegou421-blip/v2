"""
fundamentals.py — 三層備援基本面數據抓取
Layer 1: yfinance .info + .quarterly_income_stmt
Layer 2: SEC EDGAR API (免費，無需 API key)
Layer 3: OpenBB SEC provider

每層獨立 try-catch，任何一層成功就返回
數據緩存 7 天到 SQLite
"""
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import json
import time
from datetime import datetime
from database import get_fundamental_cache, save_fundamental_cache

HEADERS_SEC = {'User-Agent': 'StockScanner contact@stockscanner.app'}

# ── CIK lookup cache ──────────────────────────────────────────────────────────
_cik_cache = {}

def get_cik(ticker):
    """Get SEC CIK number for a ticker symbol"""
    if ticker in _cik_cache:
        return _cik_cache[ticker]
    try:
        r = requests.get(
            'https://efts.sec.gov/LATEST/search-index?q=%22' + ticker + '%22&dateRange=custom&startdt=2020-01-01&forms=10-K',
            headers=HEADERS_SEC, timeout=10
        )
        # Use the company tickers JSON (best source)
        r2 = requests.get(
            'https://www.sec.gov/files/company_tickers.json',
            headers=HEADERS_SEC, timeout=15
        )
        if r2.status_code == 200:
            data = r2.json()
            for _, v in data.items():
                if v.get('ticker', '').upper() == ticker.upper():
                    cik = str(v['cik_str']).zfill(10)
                    _cik_cache[ticker] = cik
                    return cik
    except Exception as e:
        print(f"[cik] {ticker}: {e}")
    return None

# ── Layer 1: yfinance ─────────────────────────────────────────────────────────
def _from_yfinance(symbol, hist_close=None, spy_close=None):
    """
    Fetch fundamentals from yfinance.
    Returns dict or None if failed.
    """
    result = {}
    ticker = yf.Ticker(symbol)

    # --- Profile: name, sector, beta, market_cap ---
    try:
        info = ticker.info
        if info and isinstance(info, dict) and len(info) > 5:
            result['company_name'] = info.get('shortName') or info.get('longName') or symbol
            result['sector']       = info.get('sector', '')
            result['industry']     = info.get('industry', '')
            result['beta']         = info.get('beta')
            result['market_cap']   = info.get('marketCap', 0) or 0
            print(f"[fund L1] {symbol} info OK: {result['company_name']}, beta={result['beta']}")
    except Exception as e:
        print(f"[fund L1] {symbol} info error: {e}")

    # --- Market cap fallback from fast_info ---
    if not result.get('market_cap'):
        try:
            fi = ticker.fast_info
            result['market_cap'] = getattr(fi, 'market_cap', 0) or 0
        except: pass

    # --- Beta fallback: calculate from price data ---
    if result.get('beta') is None and hist_close is not None and spy_close is not None:
        try:
            sr = hist_close.pct_change().dropna()
            mr = spy_close.pct_change().dropna()
            aligned = pd.concat([sr, mr], axis=1).dropna()
            aligned.columns = ['s', 'm']
            if len(aligned) >= 60:
                beta = aligned['s'].cov(aligned['m']) / aligned['m'].var()
                result['beta'] = round(float(beta), 2)
                print(f"[fund L1] {symbol} beta calculated: {result['beta']}")
        except Exception as e:
            print(f"[fund L1] {symbol} beta calc: {e}")

    # --- Income statement: EPS + Revenue ---
    eps_yoy = rev_yoy = None
    eps_accel_2q = eps_accel_3q = gm_expanding = turned_profitable = False

    try:
        q = ticker.quarterly_income_stmt
        if q is not None and not q.empty and q.shape[1] >= 4:
            # Find rows
            ni_row = gp_row = rev_row = None
            for idx in q.index:
                il = str(idx).lower()
                if 'net income' in il and ni_row is None:   ni_row  = q.loc[idx].values.astype(float)
                if 'gross profit' in il and gp_row is None: gp_row  = q.loc[idx].values.astype(float)
                if 'total revenue' in il and rev_row is None: rev_row = q.loc[idx].values.astype(float)

            if ni_row is not None and len(ni_row) >= 5:
                ni = ni_row
                if ni[4] != 0:
                    eps_yoy = round((ni[0] - ni[4]) / abs(ni[4]) * 100, 1)
                gr = []
                for i in range(min(4, len(ni)-1)):
                    if ni[i+1] != 0:
                        gr.append((ni[i] - ni[i+1]) / abs(ni[i+1]) * 100)
                eps_accel_2q    = len(gr) >= 2 and gr[0] > gr[1]
                eps_accel_3q    = len(gr) >= 3 and gr[0] > gr[1] > gr[2]
                turned_profitable = len(ni) >= 2 and ni[0] > 0 and ni[1] <= 0

            if rev_row is not None and len(rev_row) >= 5:
                rev = rev_row
                if rev[4] > 0:
                    rev_yoy = round((rev[0] - rev[4]) / rev[4] * 100, 1)

            if gp_row is not None and rev_row is not None and len(gp_row) >= 2:
                gm0 = gp_row[0]/rev_row[0]*100 if rev_row[0] else 0
                gm1 = gp_row[1]/rev_row[1]*100 if rev_row[1] else 0
                gm_expanding = gm0 > gm1

            print(f"[fund L1] {symbol} income OK: eps_yoy={eps_yoy}, rev_yoy={rev_yoy}")
    except Exception as e:
        print(f"[fund L1] {symbol} income_stmt: {e}")

    result.update({
        'eps_yoy':           eps_yoy,
        'rev_yoy':           rev_yoy,
        'eps_accel_2q':      eps_accel_2q,
        'eps_accel_3q':      eps_accel_3q,
        'gm_expanding':      gm_expanding,
        'turned_profitable': turned_profitable,
        'eps_beat':          False,
        'rev_beat':          False,
        '_source':           'yfinance',
    })

    # Consider valid if we got at least price or company name
    has_data = bool(result.get('company_name') and result['company_name'] != symbol) or \
               result.get('market_cap', 0) > 0 or \
               result.get('beta') is not None or \
               eps_yoy is not None
    return result if has_data else None

# ── Layer 2: SEC EDGAR ────────────────────────────────────────────────────────
def _from_sec_edgar(symbol):
    """
    Fetch fundamentals from SEC EDGAR (free, no API key).
    Uses XBRL company facts API for EPS and Revenue.
    """
    result = {'company_name': symbol, 'sector': '', 'industry': '',
              'market_cap': 0, 'beta': None, '_source': 'sec_edgar'}

    cik = get_cik(symbol)
    if not cik:
        print(f"[fund L2] {symbol}: CIK not found")
        return None

    try:
        url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
        r = requests.get(url, headers=HEADERS_SEC, timeout=20)
        if r.status_code != 200:
            print(f"[fund L2] {symbol}: SEC XBRL HTTP {r.status_code}")
            return None

        facts = r.json()
        us_gaap = facts.get('facts', {}).get('us-gaap', {})

        # Company name
        result['company_name'] = facts.get('entityName', symbol)

        # Net Income (quarterly)
        eps_yoy = rev_yoy = None
        eps_accel_2q = eps_accel_3q = gm_expanding = turned_profitable = False

        # Net Income
        ni_data = (us_gaap.get('NetIncomeLoss') or
                   us_gaap.get('ProfitLoss') or {})
        ni_units = ni_data.get('units', {}).get('USD', [])
        ni_qtrs = sorted(
            [x for x in ni_units if x.get('form') in ('10-Q','10-K') and x.get('fp','').startswith('Q')],
            key=lambda x: x.get('end',''), reverse=True
        )[:8]

        if len(ni_qtrs) >= 5:
            ni_vals = [q['val'] for q in ni_qtrs]
            if ni_vals[4] != 0:
                eps_yoy = round((ni_vals[0] - ni_vals[4]) / abs(ni_vals[4]) * 100, 1)
            gr = []
            for i in range(min(4, len(ni_vals)-1)):
                if ni_vals[i+1] != 0:
                    gr.append((ni_vals[i] - ni_vals[i+1]) / abs(ni_vals[i+1]) * 100)
            eps_accel_2q      = len(gr) >= 2 and gr[0] > gr[1]
            eps_accel_3q      = len(gr) >= 3 and gr[0] > gr[1] > gr[2]
            turned_profitable = len(ni_vals) >= 2 and ni_vals[0] > 0 and ni_vals[1] <= 0

        # Revenue
        rev_data = (us_gaap.get('RevenueFromContractWithCustomerExcludingAssessedTax') or
                    us_gaap.get('Revenues') or
                    us_gaap.get('SalesRevenueNet') or {})
        rev_units = rev_data.get('units', {}).get('USD', [])
        rev_qtrs = sorted(
            [x for x in rev_units if x.get('form') in ('10-Q','10-K') and x.get('fp','').startswith('Q')],
            key=lambda x: x.get('end',''), reverse=True
        )[:8]

        if len(rev_qtrs) >= 5:
            rev_vals = [q['val'] for q in rev_qtrs]
            if rev_vals[4] > 0:
                rev_yoy = round((rev_vals[0] - rev_vals[4]) / rev_vals[4] * 100, 1)

        # Gross Profit for margin
        gp_data = us_gaap.get('GrossProfit', {})
        gp_units = gp_data.get('units', {}).get('USD', [])
        gp_qtrs = sorted(
            [x for x in gp_units if x.get('form') in ('10-Q','10-K') and x.get('fp','').startswith('Q')],
            key=lambda x: x.get('end',''), reverse=True
        )[:3]
        if len(gp_qtrs) >= 2 and len(rev_qtrs) >= 2:
            gm0 = gp_qtrs[0]['val'] / rev_qtrs[0]['val'] if rev_qtrs[0]['val'] else 0
            gm1 = gp_qtrs[1]['val'] / rev_qtrs[1]['val'] if rev_qtrs[1]['val'] else 0
            gm_expanding = gm0 > gm1

        result.update({
            'eps_yoy':           eps_yoy,
            'rev_yoy':           rev_yoy,
            'eps_accel_2q':      eps_accel_2q,
            'eps_accel_3q':      eps_accel_3q,
            'gm_expanding':      gm_expanding,
            'turned_profitable': turned_profitable,
            'eps_beat':          False,
            'rev_beat':          False,
        })
        print(f"[fund L2] {symbol} SEC OK: eps_yoy={eps_yoy}, rev_yoy={rev_yoy}")
        return result

    except Exception as e:
        print(f"[fund L2] {symbol} SEC error: {e}")
        return None

# ── Layer 3: OpenBB SEC ───────────────────────────────────────────────────────
def _from_openbb(symbol):
    """OpenBB SEC provider as final fallback"""
    try:
        from openbb import obb
        import warnings
        warnings.filterwarnings('ignore')

        result = {'company_name': symbol, 'sector': '', 'industry': '',
                  'market_cap': 0, 'beta': None, '_source': 'openbb_sec'}

        # Income statement via OpenBB
        inc = obb.equity.fundamental.income(symbol, provider='sec', period='quarter', limit=5)
        df  = inc.to_df()
        if df is None or df.empty:
            return None

        # Map column names (OpenBB normalizes them)
        ni_col  = next((c for c in df.columns if 'net_income' in c.lower()), None)
        rev_col = next((c for c in df.columns if 'revenue' in c.lower()), None)
        gp_col  = next((c for c in df.columns if 'gross_profit' in c.lower()), None)

        eps_yoy = rev_yoy = None
        if ni_col and len(df) >= 5:
            ni = df[ni_col].values.astype(float)
            if ni[4] != 0:
                eps_yoy = round((ni[0]-ni[4])/abs(ni[4])*100, 1)
        if rev_col and len(df) >= 5:
            rev = df[rev_col].values.astype(float)
            if rev[4] > 0:
                rev_yoy = round((rev[0]-rev[4])/rev[4]*100, 1)

        result.update({
            'eps_yoy':           eps_yoy,
            'rev_yoy':           rev_yoy,
            'eps_accel_2q':      False,
            'eps_accel_3q':      False,
            'gm_expanding':      False,
            'turned_profitable': False,
            'eps_beat':          False,
            'rev_beat':          False,
        })
        print(f"[fund L3] {symbol} OpenBB OK: eps_yoy={eps_yoy}, rev_yoy={rev_yoy}")
        return result

    except Exception as e:
        print(f"[fund L3] {symbol} OpenBB: {e}")
        return None

# ── Public interface ──────────────────────────────────────────────────────────
def get_fundamentals(symbol, hist_close=None, spy_close=None):
    """
    Three-layer fundamental data fetching with 7-day SQLite cache.
    Always returns a dict (never None).
    """
    # Check cache first
    cached = get_fundamental_cache(symbol)
    if cached:
        return cached

    empty = {
        'company_name': symbol, 'sector': '', 'industry': '',
        'market_cap': 0, 'beta': None,
        'eps_yoy': None, 'rev_yoy': None,
        'eps_accel_2q': False, 'eps_accel_3q': False,
        'gm_expanding': False, 'turned_profitable': False,
        'eps_beat': False, 'rev_beat': False,
        '_source': 'none',
    }

    # Layer 1: yfinance
    print(f"[fund] {symbol}: trying Layer 1 (yfinance)")
    result = _from_yfinance(symbol, hist_close, spy_close)

    # Layer 2: SEC EDGAR (if Layer 1 missing income data)
    if result is None or (result.get('eps_yoy') is None and result.get('rev_yoy') is None):
        print(f"[fund] {symbol}: trying Layer 2 (SEC EDGAR)")
        sec_result = _from_sec_edgar(symbol)
        if sec_result:
            # Merge: keep yfinance profile data (beta, market_cap, sector)
            # but take SEC income data
            if result:
                sec_result['beta']       = result.get('beta') or sec_result.get('beta')
                sec_result['market_cap'] = result.get('market_cap') or sec_result.get('market_cap', 0)
                sec_result['sector']     = result.get('sector') or sec_result.get('sector', '')
                sec_result['industry']   = result.get('industry') or sec_result.get('industry', '')
            result = sec_result

    # Layer 3: OpenBB (last resort)
    if result is None or (result.get('eps_yoy') is None and result.get('rev_yoy') is None):
        print(f"[fund] {symbol}: trying Layer 3 (OpenBB)")
        obb_result = _from_openbb(symbol)
        if obb_result:
            if result:
                obb_result['beta']       = result.get('beta') or obb_result.get('beta')
                obb_result['market_cap'] = result.get('market_cap') or obb_result.get('market_cap', 0)
                obb_result['sector']     = result.get('sector') or obb_result.get('sector', '')
            result = obb_result

    final = result if result else empty

    # Ensure all required keys exist
    for key, default in empty.items():
        if key not in final:
            final[key] = default

    # Cache the result
    save_fundamental_cache(symbol, final)
    source = final.get('_source', 'none')
    print(f"[fund] {symbol}: final source={source}, eps_yoy={final.get('eps_yoy')}, beta={final.get('beta')}")
    return final
