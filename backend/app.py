from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import threading, os, json
import numpy as np
from flask.json.provider import DefaultJSONProvider
from database import init_db, get_scan_results, get_meta, set_meta
from market import get_market_overview
from scanner import run_full_scan, score_single_stock

class SafeJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        def default(o):
            if isinstance(o, np.integer):  return int(o)
            if isinstance(o, np.floating): return float(o)
            if isinstance(o, np.bool_):    return bool(o)
            if isinstance(o, np.ndarray):  return o.tolist()
            return o
        kwargs['default'] = default
        return super().dumps(obj, **kwargs)

app = Flask(__name__, static_folder='../frontend', static_url_path='')
app.json = SafeJSONProvider(app)
CORS(app)

PROGRESS_FILE = 'scan_progress.json'
_scan_thread  = None

# 🚀 終極破鎖熔斷機制：搶在所有人前面，把卡死的 SQLite 殘留直接格式化蒸發！
try:
    import os
    db_file_path = os.environ.get('DB_PATH', '/tmp/stocks.db')
    if os.path.exists(db_file_path):
        os.remove(db_file_path)
        print(f"[REBOOT FORCE DISASTER RECOVERY] 已物理破除並清空卡死的 SQLite 實體：{db_file_path}", flush=True)
except Exception as e:
    print(f"[REBOOT FORCE DISASTER RECOVERY] 嘗試清空鎖死資料庫時失敗（可能已被釋放）：{e}", flush=True)

init_db()

@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('../frontend', path)

@app.route('/api/market')
def market():
    try:
        return jsonify({'success': True, 'data': get_market_overview()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ── Scan: start ───────────────────────────────────────────────────────────────
@app.route('/api/scan/start', methods=['POST'])
def start_scan():
    global _scan_thread
    status = get_meta('scan_status') or {}

    # 🚀 終極放行防禦：如果發現掃描已經在背景跑了，絕對不噴 409！
    # 直接溫柔地回傳成功，並告訴前端「繼續讀取進度就對了」，徹底封死前端錯亂 Bug
    if status.get('is_scanning'):
        print("[INFO] 偵測到重複觸發掃描，系統已自動接管並維持原有進度線程。", flush=True)
        return jsonify({'success': True, 'already_running': True, 'cached': True})

    # If DB already has results, return them
    cached = get_scan_results()
    if cached and cached.get('passed', 0) > 0:
        return jsonify({'success': True, 'cached': True})

    set_meta('scan_status', {'is_scanning': True, 'progress': 0,
                              'current': '啟動中...', 'total': 227, 'done': 0})

    def do_scan():
        try:
            run_full_scan(PROGRESS_FILE)
        except Exception as e:
            print(f"[scan thread] error: {e}")
            set_meta('scan_status', {'is_scanning': False, 'progress': 0,
                                      'current': f'錯誤: {e}', 'total': 227, 'done': 0})

    _scan_thread = threading.Thread(target=do_scan, daemon=True)
    _scan_thread.start()
    return jsonify({'success': True, 'message': '掃描已開始'})

@app.route('/api/scan/cron', methods=['POST', 'GET'])
def cron_scan():
    global _scan_thread
    status = get_meta('scan_status') or {}
    if status.get('is_scanning'):
        return jsonify({'success': False, 'error': 'already running'})

    set_meta('scan_status', {'is_scanning': True, 'progress': 0,
                              'current': '定時掃描啟動...', 'total': 227, 'done': 0})

    def do_cron():
        try:
            run_full_scan(PROGRESS_FILE)
        except Exception as e:
            print(f"[cron] error: {e}")
            set_meta('scan_status', {'is_scanning': False, 'progress': 0,
                                      'current': f'Cron錯誤: {e}', 'total': 227, 'done': 0})

    threading.Thread(target=do_cron, daemon=True).start()
    return jsonify({'success': True, 'message': 'cron scan started'})

@app.route('/api/scan/progress')
def scan_progress():
    status = get_meta('scan_status') or {
        'is_scanning': False, 'progress': 0, 'current': '', 'total': 227, 'done': 0
    }
    return jsonify(status)

# 🚀 修正對接傷：精準解包根目錄，將結果結構與 index.html 的 renderScanResults 100% 鎖死對齊！
@app.route('/api/scan/results')
def scan_results():
    data = get_scan_results()
    if data and data.get('passed', 0) > 0:
        return jsonify({
            'success': True, 
            'status': 'success', 
            'passed': data.get('passed', 0), 
            'scanned_at': data.get('scanned_at'), 
            'results': data.get('results', [])
        })
    return jsonify({'success': True, 'status': 'not_scanned', 'results': [], 'passed': 0}), 200

@app.route('/api/scan/clear', methods=['POST'])
def clear_scan():
    from database import get_conn
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM scan_results')
    cursor.execute('DELETE FROM fundamental_cache')  # 一鍵清空實體髒快取
    cursor.execute("DELETE FROM scan_meta WHERE key='last_scan'")
    cursor.execute("DELETE FROM scan_meta WHERE key='scan_status'")
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/stock/<ticker>')
def single_stock(ticker):
    symbol = ticker.upper().strip()
    import re
    if not re.match(r'^[A-Z.\-]{1,10}$', symbol):
        return jsonify({'success': False, 'error': '代號格式錯誤'}), 400
    try:
        result = score_single_stock(symbol)
        if result is not None:
            return jsonify({'success': True, 'data': result})
        return jsonify({'success': False, 'error': f'無法取得 {symbol} 數據'}), 404
    except Exception as e:
        print(f"[api] /stock/{symbol} error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)