import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.environ.get('DB_PATH', 'stocks.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Scan results table
    c.execute('''
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            data TEXT NOT NULL
        )
    ''')

    # Scan metadata
    c.execute('''
        CREATE TABLE IF NOT EXISTS scan_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # Fundamental cache (updates weekly)
    c.execute('''
        CREATE TABLE IF NOT EXISTS fundamental_cache (
            symbol TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()

def save_scan_results(results, total_scanned):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().isoformat()

    # Clear old results
    c.execute('DELETE FROM scan_results')

    # Insert new results
    for r in results:
        c.execute(
            'INSERT INTO scan_results (symbol, scanned_at, data) VALUES (?,?,?)',
            (r['symbol'], now, json.dumps(r))
        )

    # Update meta
    c.execute('''
        INSERT OR REPLACE INTO scan_meta (key, value, updated_at)
        VALUES (?, ?, ?)
    ''', ('last_scan', json.dumps({
        'scanned_at': now,
        'total_scanned': total_scanned,
        'passed': len(results)
    }), now))

    conn.commit()
    conn.close()

def get_scan_results():
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

def get_fundamental_cache(symbol):
    conn = get_conn()
    c = conn.cursor()
    row = c.execute('SELECT data, cached_at FROM fundamental_cache WHERE symbol=?', (symbol,)).fetchone()
    conn.close()
    if not row:
        return None
    # Cache fundamentals for 7 days
    from datetime import timedelta
    cached_at = datetime.fromisoformat(row['cached_at'])
    if datetime.now() - cached_at > timedelta(days=7):
        return None
    return json.loads(row['data'])

def save_fundamental_cache(symbol, data):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO fundamental_cache (symbol, data, cached_at)
        VALUES (?, ?, ?)
    ''', (symbol, json.dumps(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def set_meta(key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO scan_meta (key, value, updated_at) VALUES (?,?,?)
    ''', (key, json.dumps(value), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_meta(key):
    conn = get_conn()
    c = conn.cursor()
    row = c.execute('SELECT value FROM scan_meta WHERE key=?', (key,)).fetchone()
    conn.close()
    return json.loads(row['value']) if row else None
