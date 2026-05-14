from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import json, os, time, threading
from market import get_market_overview
from scanner import run_full_scan, get_cached_results, score_single_stock, scan_progress

app = Flask(__name__, static_folder='../frontend', static_url_path='')
CORS(app)

CACHE_FILE = 'scan_cache.json'
scan_lock = threading.Lock()
is_scanning = False

# ─── Serve Frontend ───────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('../frontend', path)

# ─── Market Overview ──────────────────────────────────────────────────────────
@app.route('/api/market')
def market():
    try:
        data = get_market_overview()
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── Scanner ──────────────────────────────────────────────────────────────────
@app.route('/api/scan/start', methods=['POST'])
def start_scan():
    global is_scanning
    with scan_lock:
        if is_scanning:
            return jsonify({'success': False, 'error': '掃描進行中'}), 409
        is_scanning = True

    def do_scan():
        global is_scanning
        try:
            run_full_scan(CACHE_FILE)
        finally:
            is_scanning = False

    t = threading.Thread(target=do_scan, daemon=True)
    t.start()
    return jsonify({'success': True, 'message': '掃描已開始'})

@app.route('/api/scan/progress')
def scan_progress_api():
    return jsonify({
        'is_scanning': is_scanning,
        'progress': scan_progress.get('progress', 0),
        'current': scan_progress.get('current', ''),
        'total': scan_progress.get('total', 500),
        'done': scan_progress.get('done', 0)
    })

@app.route('/api/scan/results')
def scan_results():
    cached = get_cached_results(CACHE_FILE)
    if cached:
        return jsonify({'success': True, 'data': cached})
    return jsonify({'success': False, 'error': '尚無掃描結果，請先執行掃描'})

# ─── Single Stock Search ──────────────────────────────────────────────────────
@app.route('/api/stock/<ticker>')
def single_stock(ticker):
    try:
        result = score_single_stock(ticker.upper())
        if result:
            return jsonify({'success': True, 'data': result})
        return jsonify({'success': False, 'error': f'無法取得 {ticker} 數據'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
