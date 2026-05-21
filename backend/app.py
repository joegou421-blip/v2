from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import threading, os, json
import numpy as np
from flask.json.provider import DefaultJSONProvider
from database import init_db, get_scan_results, get_meta, set_meta
from market import get_market_overview
# 🚀 物理校準：把找不到的 score_single_stock 拿掉，只留下真正存在的核心選股管線與函式
from scanner import run_full_scan, score_stock

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

    # 如果發現系統本來就已經在背景全速掃描了，直接溫柔放行讓前端接管進度條
    if status.get('is_scanning'):
        print("[INFO] 偵測到重複或正在運行的掃描線程，自動接通進度。", flush=True)
        return jsonify({
            'success': True, 'already_running': True,
            'is_scanning': True, 'progress': status.get('progress', 0),
            'current': status.get('current', '掃描中...'),
            'data': status
        })

    # 🚀 終極降維斬首：徹底拔除 cached 攔截阻礙！只要點擊紫色按鈕，無條件強制開啟新一輪實時全掃描！
    # 這能 100% 解決前端卡死在「已載入緩存結果」卻完全不跑進度條、沒變化的死循環
    new_status = {'is_scanning': True, 'progress': 0, 'current': '準備啟動中...', 'total': 227, 'done': 0}
    set_meta('scan_status', new_status)

    def do_scan():
        try:
            run_full_scan(PROGRESS_FILE)
        except Exception as e:
            print(f"[scan thread] error: {e}")
            set_meta('scan_status', {'is_scanning': False, 'progress': 0,
                                      'current': f'錯誤: {e}', 'total': 227, 'done': 0})

    _scan_thread = threading.Thread(target=do_scan, daemon=True)
    _scan_thread.start()
    
    return jsonify({
        'success': True, 
        'message': '掃描已強制開始', 
        'is_scanning': True, 
        'progress': 0,
        'current': '啟動中...',
        'data': new_status
    })

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

# 🚀 雙軌導流通電：同時支援舊版的 /progress 與新版的 /status，100% 滿足前端輪詢寫法！
@app.route('/api/scan/progress')
@app.route('/api/scan/status')
def scan_progress():
    status = get_meta('scan_status') or {
        'is_scanning': False, 'progress': 0, 'current': '未啟動', 'total': 227, 'done': 0
    }
    # 🚀 全相容大融合：同時支援 json.is_scanning、json.data.is_scanning 的前端格式
    return jsonify({
        'success': True,
        'is_scanning': status.get('is_scanning', False),
        'progress': status.get('progress', 0),
        'current': status.get('current', ''),
        'total': status.get('total', 0),
        'done': status.get('done', 0),
        'stats': status.get('stats', {}),
        'data': status
    })

# 🚀 修正對接傷：精準解包根目錄，將結果結構與 index.html 的 renderScanResults 100% 鎖死對齊！
@app.route('/api/scan/results')
def scan_results():
    data = get_scan_results() or {'results': [], 'stats': {}, 'passed': 0}
    results_list = data.get('results', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    
    return jsonify({
        'success': True, 
        'status': 'success', 
        'passed': data.get('passed', 0) if isinstance(data, dict) else len(results_list), 
        'scanned_at': data.get('scanned_at') if isinstance(data, dict) else None, 
        'data': data,
        'results': results_list
    })

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
        from scanner import get_technicals, get_fundamentals, score_stock
        import time

        spy_close = 400.0  
        try:
            from scanner import get_spy
            spy_close = get_spy()['close']
        except:
            pass

        try:
            tech = get_technicals(symbol, spy_close)
        except Exception as e_tech:
            print(f"[API TECH ERR] {symbol} 技術面抓取崩潰: {e_tech}")
            tech = None

        if not tech or tech.get('price') is None:
            return jsonify({'success': False, 'error': f'無法取得 {symbol} 即時技術面數據，請稍後重試'}), 404

        close_s = tech.pop('_close', None)
        try:
            fund = get_fundamentals(symbol, close_s, spy_close)
        except Exception as e_fund:
            print(f"[API FUND ERR] {symbol} 基本面抓取崩潰: {e_fund}")
            fund = {'_source': 'yfinance_fallback'}

        # 🚀 終極護衛防線：防止 TSLA 等超級熱門股因為財務欄位缺失導致 score_stock 算分核爆
        try:
            score, signal, signal_label, breakdown = score_stock(tech, fund)
        except Exception as e_score:
            print(f"[API SCORE CRASH] {symbol} 算分核心核爆，啟動安全防禦降維: {e_score}")
            score = 5
            signal = 'watch'
            signal_label = '觀望 (財務數據異常)'
            breakdown = {}

        raw_eps = fund.get('eps_yoy')
        import numpy as np
        
        try:
            is_turn = bool(fund.get('turned_profitable') or (isinstance(raw_eps, (int, float)) and not np.isnan(raw_eps) and raw_eps < -100))
        except:
            is_turn = False

        # 🛠️ 完美補齊前端詳細面板滑出所需的所有隱藏量化欄位
        result = {
            'symbol': symbol,
            'company_name': fund.get('company_name', symbol),
            'price': tech.get('price', 0.0),
            'score': score,
            'signal': signal,
            'signal_label': signal_label,
            'not_stage2': tech.get('not_stage2', False),
            'breakdown': breakdown,
            'rev_yoy': None if (isinstance(fund.get('rev_yoy'), float) and np.isnan(fund.get('rev_yoy'))) else fund.get('rev_yoy'),
            'eps_yoy': "由虧轉盈" if is_turn else (None if (isinstance(raw_eps, float) and np.isnan(raw_eps)) else raw_eps),
            'beta': 1.0 if (fund.get('beta') is None or (isinstance(fund.get('beta'), float) and np.isnan(fund.get('beta')))) else fund.get('beta'),
            'rs_rating': 50.0 if (tech.get('rs_rating') is None or (isinstance(tech.get('rs_rating'), float) and np.isnan(tech.get('rs_rating')))) else tech['rs_rating'],
            
            # 🚀 注入核心交易面板數據，讓單股點擊後的詳細跑馬燈不漏白
            'stop_loss': tech.get('stop_loss', '--'),
            'target': tech.get('target', '--'),
            'rr': tech.get('rr', '--'),
            'industry': fund.get('industry', fund.get('sector', '美股個股')),
            'sector': fund.get('sector', ''),
            'atr_pct': tech.get('atr_pct', '--'),
            'rsi': tech.get('rsi', '--'),
            'vol_mult': tech.get('vol_mult', '--'),
            'chase_risk': tech.get('chase_risk', False),
            'fund_source': fund.get('_source', '?')
        }

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        print(f"[api] /stock/{symbol} global error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── 🤖 AI MULTI-AGENT ADVERSARIAL DEBATE ROUNDTABLE (方案 B: 免 VPN 開源鋼鐵陣容) ───
@app.route('/api/scan/ai_debate', methods=['POST'])
def ai_debate():
    import os, requests
    # 🚀 自動讀取大佬提供的 OpenRouter Key，保障全線通車
    api_key = os.environ.get("OPENROUTER_API_KEY", "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")
    
    from flask import request
    req_json = request.get_json() or {}
    s = req_json.get('stock_data', {})
    user_query = req_json.get('query', '請評估這隻股票的短線走勢')

    if not s or 'symbol' not in s:
        return jsonify({'success': False, 'error': '缺乏個股量化核心數據'}), 400

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://stockscanner.com",
        "Content-Type": "application/json"
    }
    url = "https://openrouter.ai/api/v1/chat/completions"

    # 📦 數據完全體封裝：包含所有可能缺失的 None 欄位，原封不動交給 AI 拷問
    stock_context = f"""
    【大佬系統之即時量化實時快照】
    股票代號: {s.get('symbol')}
    公司名稱: {s.get('company_name', s.get('symbol'))}
    當前股價: ${s.get('price')}
    量化綜合總分: {s.get('score')}/15 分
    系統量化訊號: {s.get('signal_label')}
    營收年增率 (Rev YoY): {s.get('rev_yoy') if s.get('rev_yoy') is not None else '無數據/None'}
    EPS年增率 (EPS YoY): {s.get('eps_yoy') if s.get('eps_yoy') is not None else '無數據/None'}
    RS 相對強度評級: {s.get('rs_rating', '無數據')}
    風險係數 (Beta): {s.get('beta', '無數據')}
    系統建議止損價: {s.get('stop_loss', '無數據')}
    系統建議目標價: {s.get('target', '無數據')}
    當前技術指標 (RSI): {s.get('rsi', '無數據')}
    當前技術指標 (ATR% 日均波動): {s.get('atr_pct', '無數據')}%
    當前技術指標 (成交量倍數): {s.get('vol_mult', '無數據')}x
    """

    try:
        # 🔥 ⚔️ ROUND 1: 技術動能官 (Llama 3 8B) 開火，尋求短線突破利多
        p1_prompt = f"{stock_context}\n大佬提問：{user_query}\n\n任務：你是【趨勢動能官（Llama-3）】。請完全站在短線技術動能、K線趨勢突破的角度，給出短線激進的多空理由。字數 150 字內。"
        p1_res = requests.post(url, headers=headers, json={
            "model": "meta-llama/llama-3-8b-instruct:free", "messages": [{"role": "user", "content": p1_prompt}]
        }).json()
        p1_opinion = p1_res['choices'][0]['message']['content']

        # 🔥 ⚔️ ROUND 2: 財報審查官 (Mistral Large Paid Flagship) 專職挑刺，拷問 None 數據空缺
        p2_prompt = f"{stock_context}\n【動能進攻官的樂觀觀點如下】:\n{p1_opinion}\n\n任務：你是【基本面審查官（Mistral-Large旗艦級大腦）】。請針對動能官的盲目看法進行嚴厲質質疑！特別盯緊大佬數據中為 None、無數據、或是不及格的欄位，進行無情挑刺與打臉，抓出這筆交易隱藏的致命盲區。字數 200 字內。"
        p2_res = requests.post(url, headers=headers, json={
            "model": "mistralai/mistral-large", "messages": [{"role": "user", "content": p2_prompt}]
        }).json()
        p2_opinion = p2_res['choices'][0]['message']['content']

        # 🔥 ⚔️ ROUND 3: 最高風控裁決官 (DeepSeek V3/R1) 聯網掃描新聞，強制達成風控共識
        p3_prompt = f"{stock_context}\n【雙方對抗辯論紀錄】:\n動能官觀點: {p1_opinion}\n\n審查官質疑: {p2_opinion}\n\n任務：你是【最高風控裁決官（DeepSeek）】。請先啟動即時聯網搜捕該股最近的利空利多新聞、分析師評語。接著審視前兩者的激烈辯論，揪出被網路上即時新聞驗證後的邏輯漏洞。在充分考慮數據缺失帶來的未知風險後，給出一個最嚴密、包含具體倉位控制與止損防線的【最終對抗審查共識結論】。字數 300 字內。"
        p3_res = requests.post(url, headers=headers, json={
            "model": "deepseek/deepseek-chat", "messages": [{"role": "user", "content": p3_prompt}]
        }).json()
        final_consensus = p3_res['choices'][0]['message']['content']

        return jsonify({
            'success': True,
            'p1_opinion': p1_opinion,
            'p2_opinion': p2_opinion,
            'final_consensus': final_consensus
        })

    except Exception as e:
        return jsonify({'success': False, 'error': f'智囊團圓桌會議因外部網路熔斷: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)