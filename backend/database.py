import sqlite3, json, os
from datetime import datetime, timedelta

DB_PATH = os.environ.get('DB_PATH', '/tmp/stocks.db')
FUND_CACHE_VERSION = '2'  # 戳破舊緩存的核彈密碼

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS scan_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        scanned_at TEXT NOT NULL,
        data TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS scan_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS fundamental_cache (
        symbol TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        cached_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()

def save_scan_results(results, total_scanned):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('DELETE FROM scan_results')
    for r in results:
        c.execute('INSERT INTO scan_results (symbol, scanned_at, data) VALUES (?,?,?)',
                  (r['symbol'], now, json.dumps(r)))
    c.execute('INSERT OR REPLACE INTO scan_meta (key, value, updated_at) VALUES (?,?,?)',
              ('last_scan', json.dumps({
                  'scanned_at': now,
                  'total_scanned': total_scanned,
                  'passed': len(results)
              }), now))
    conn.commit()
    conn.close()

def get_scan_results():
    try:
        conn = get_conn()
        c = conn.cursor()
        meta = c.execute("SELECT value FROM scan_meta WHERE key='last_scan'").fetchone()
        if not meta:
            conn.close()
            return None
        meta_data = json.loads(meta['value'])
        rows = c.execute('SELECT data FROM scan_results ORDER BY rowid').fetchall()
        results = [json.loads(r['data']) for r in rows]
        conn.close()
        return {**meta_data, 'results': results}
    except:
        return None

def get_fundamental_cache(symbol):
    try:
        conn = get_conn()
        c = conn.cursor()
        row = c.execute('SELECT data, cached_at FROM fundamental_cache WHERE symbol=?',
                        (symbol,)).fetchone()
        conn.close()
        if not row:
            return None
            
        # 7 天快取過期判定
        if datetime.now() - datetime.fromisoformat(row['cached_at']) > timedelta(days=7):
            return None
            
        data = json.loads(row['data'])
        # 🚀 修正版本熔斷：如果快取版本不對，立刻宣判失效，強迫走最新公式重算！
        if data.get('_cache_version') != FUND_CACHE_VERSION:
            return None
        return data
    except:
        return None

def save_fundamental_cache(symbol, data):
    try:
        data['_cache_version'] = FUND_CACHE_VERSION
        conn = get_conn()
        conn.execute('INSERT OR REPLACE INTO fundamental_cache (symbol, data, cached_at) VALUES (?,?,?)',
                     (symbol, json.dumps(data), datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[db] save_fundamental_cache {symbol}: {e}")

def set_meta(key, value):
    try:
        conn = get_conn()
        conn.execute('INSERT OR REPLACE INTO scan_meta (key, value, updated_at) VALUES (?,?,?)',
                     (key, json.dumps(value), datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[db] set_meta {key}: {e}")

def get_meta(key):
    try:
        conn = get_conn()
        c = conn.cursor()
        row = c.execute('SELECT value FROM scan_meta WHERE key=?', (key,)).fetchone()
        conn.close()
        return json.loads(row['value']) if row else None
    except:
        return None