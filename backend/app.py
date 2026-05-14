from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import json, os, threading
from market import get_market_overview
from scanner import run_full_scan, get_cached_results, score_single_stock

app = Flask(__name__, static_folder='../frontend', static_url_path='')
CORS(app)

CACHE_FILE = 'scan_cache.json'
PROGRESS_FILE = 'scan_progress.json'

def write_progress(data):
    try:
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(data, f)
    except:
        pass

def read_progress():
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {'is_scanning': False, 'progress': 0, 'current': '', 'total': 500, 'done': 0}

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
    prog = read_progress()
    if prog.get('is_scanning'):
        return jsonify({'success': False, 'error': '掃描進行中'}), 409

    # Return cache if valid AND has results
    cached = get_cached_results(CACHE_FILE)
    if cached and cached.get('passed', 0) > 0:
        return jsonify({'success': True, 'cached': True, 'message': '使用緩存結果'})

    write_progress({'is_scanning': True, 'progress': 0, 'current': '初始化...', 'total': 500, 'done': 0})

    def do_scan():
        try:
            run_full_scan(CACHE_FILE, PROGRESS_FILE)
        except Exception as e:
            write_progress({'is_scanning': False, 'progress': 0, 'current': f'錯誤: {str(e)}', 'total': 500, 'done': 0})
        finally:
            p = read_progress()
            p['is_scanning'] = False
            write_progress(p)

    t = threading.Thread(target=do_scan, daemon=True)
    t.start()
    return jsonify({'success': True, 'message': '掃描已開始'})

@app.route('/api/scan/progress')
def scan_progress_api():
    return jsonify(read_progress())

@app.route('/api/scan/results')
def scan_results():
    cached = get_cached_results(CACHE_FILE)
    if cached:
        return jsonify({'success': True, 'data': cached})
    return jsonify({'success': False, 'error': '尚無掃描結果，請先執行掃描'})

@app.route('/api/scan/clear', methods=['POST'])
def clear_cache():
    for f in [CACHE_FILE, PROGRESS_FILE]:
        if os.path.exists(f):
            os.remove(f)
    return jsonify({'success': True})

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
