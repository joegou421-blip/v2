from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import threading
import os
import json
import requests
import numpy as np
from flask.json.provider import DefaultJSONProvider
from database import init_db, get_scan_results, get_meta, set_meta
from market import get_market_overview
from scanner import run_full_scan, score_stock

class SafeJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        def default(o):
            if isinstance(o, np.integer):
                return int(o)
            if isinstance(o, np.floating):
                return float(o)
            if isinstance(o, np.bool_):
                return bool(o)
            if isinstance(o, np.ndarray):
                return o.tolist()
            return o
        kwargs['default'] = default
        return super().dumps(obj, **kwargs)

app = Flask(__name__, static_folder='../frontend', static_url_path='')
app.json = SafeJSONProvider(app)
CORS(app)

PROGRESS_FILE = 'scan_progress.json'
_scan_thread = None

try:
    db_file_path = os.environ.get('DB_PATH', '/tmp/stocks.db')
    if os.path.exists(db_file_path):
        os.remove(db_file_path)
        print(f"[REBOOT FORCE DISASTER RECOVERY] 已物理破除並清空 SQLite 實體：{db_file_path}", flush=True)
except Exception as e:
    print(f"[REBOOT FORCE DISASTER RECOVERY] 嘗試清空鎖死資料庫時失敗：{e}", flush=True)

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

@app.route('/api/scan/start', methods=['POST'])
def start_scan():
    global _scan_thread
    status = get_meta('scan_status') or {}
    
    if status.get('is_scanning'):
        return jsonify({
            'success': True,
            'already_running': True,
            'is_scanning': True,
            'progress': status.get('progress', 0),
            'current': status.get('current', '掃描中...'),
            'data': status
        })
        
    new_status = {
        'is_scanning': True,
        'progress': 0,
        'current': '準備啟動中...',
        'total': 227,
        'done': 0
    }
    set_meta('scan_status', new_status)
    
    def do_scan():
        try:
            run_full_scan(PROGRESS_FILE)
        except Exception as e:
            set_meta('scan_status', {
                'is_scanning': False,
                'progress': 0,
                'current': f'錯誤: {e}',
                'total': 227,
                'done': 0
            })
            
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
        
    set_meta('scan_status', {
        'is_scanning': True,
        'progress': 0,
        'current': '定時掃描啟動...',
        'total': 227,
        'done': 0
    })
    
    def do_cron():
        try:
            run_full_scan(PROGRESS_FILE)
        except Exception as e:
            set_meta('scan_status', {
                'is_scanning': False,
                'progress': 0,
                'current': f'Cron錯誤: {e}',
                'total': 227,
                'done': 0
            })
            
    threading.Thread(target=do_cron, daemon=True).start()
    return jsonify({'success': True, 'message': 'cron scan started'})

@app.route('/api/scan/progress')
@app.route('/api/scan/status')
def scan_progress():
    status = get_meta('scan_status') or {
        'is_scanning': False, 'progress': 0, 'current': '未啟動', 'total': 227, 'done': 0
    }
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
    cursor.execute('DELETE FROM fundamental_cache')
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
        spy_close = 400.0
        try:
            from scanner import get_spy
            spy_close = get_spy()['close']
        except:
            pass
            
        try:
            tech = get_technicals(symbol, spy_close)
        except Exception as e_tech:
            tech = None
            
        if not tech or tech.get('price') is None:
            return jsonify({'success': False, 'error': f'無法取得 {symbol} 即時技術面數據'}), 404
            
        close_s = tech.pop('_close', None)
        try:
            fund = get_fundamentals(symbol, close_s, spy_close)
        except:
            fund = {'_source': 'yfinance_fallback'}
            
        try:
            score, signal, signal_label, breakdown = score_stock(tech, fund)
        except:
            score = 5
            signal = 'watch'
            signal_label = '觀望 (財務數據異常)'
            breakdown = {}
            
        raw_eps = fund.get('eps_yoy')
        try:
            is_turn = bool(fund.get('turned_profitable') or (isinstance(raw_eps, (int, float)) and not np.isnan(raw_eps) and raw_eps < -100))
        except:
            is_turn = False
            
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
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── ⚖️ 2-COMPONENT ULTRA-LEAN QUANT SYSTEM (工業級終極合流複盤管線) ───
@app.route('/api/scan/ai_debate', methods=['POST', 'GET'])
def ai_debate():
    import os
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if request.method == 'GET':
        return jsonify({'success': False, 'error': '量化複盤後端管線就緒！'})
        
    req_json = request.get_json() or {}
    s = req_json.get('stock_data', {})
    symbol = req_json.get('symbol', '').upper().strip()
    user_query = req_json.get('query', '請評估這隻股票目前的進場時機與潛在風險')

    if (not s or 'symbol' not in s) and symbol:
        try:
            from scanner import get_technicals, get_fundamentals, score_stock, get_spy
            spy_close = 400.0
            try:
                spy_close = get_spy()['close']
            except:
                pass
            tech = get_technicals(symbol, spy_close)
            if tech and tech.get('price') is not None:
                close_s = tech.pop('_close', None)
                try:
                    fund = get_fundamentals(symbol, close_s, spy_close)
                except:
                    fund = {}
                score, signal, signal_label, breakdown = score_stock(tech, fund)
                s = {
                    'symbol': symbol,
                    'company_name': fund.get('company_name', symbol),
                    'price': tech.get('price'),
                    'score': score,
                    'signal_label': signal_label,
                    'rev_yoy': fund.get('rev_yoy'),
                    'eps_yoy': fund.get('eps_yoy'),
                    'beta': fund.get('beta'),
                    'rs_rating': tech.get('rs_rating'),
                    'stop_loss': tech.get('stop_loss'),
                    'target': tech.get('target'),
                    'rsi': tech.get('rsi'),
                    'atr_pct': tech.get('atr_pct'),
                    'vol_mult': tech.get('vol_mult'),
                    'sector': fund.get('sector', ''),
                    'market_cap': fund.get('market_cap', 0),
                }
        except Exception as err:
            print(f"[LOOKUP ERR] {symbol}: {err}")

    if not s or 'symbol' not in s:
        return jsonify({'success': False, 'error': f'無法取得「{symbol or "未知"}」的真實量化數據'}), 400
    if not api_key:
        return jsonify({'success': False, 'error': '未設定 OPENROUTER_API_KEY'}), 400

    # 🛠️ 數據預處理：防禦性清洗網，全物理物理超渡 None / NaN 字串漏洞
    def sanitize_val(val, prefix="$", default="無數據(N/A)"):
        if val is None or str(val).strip().lower() in ['none', '--', 'nan', 'null', '']:
            return default
        val_str = str(val).strip()
        if prefix == "$" and val_str.startswith("$"):
            return val_str
        return f"{prefix}{val_str}"

    sl_clean = sanitize_val(s.get('stop_loss'), prefix="$")
    tg_clean = sanitize_val(s.get('target'), prefix="$")
    eps_clean = sanitize_val(s.get('eps_yoy'), prefix="", default="無數據(N/A)")
    rev_clean = sanitize_val(s.get('rev_yoy'), prefix="", default="無數據(N/A)")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://stockscanner.com",
        "Content-Type": "application/json"
    }
    or_url = "https://openrouter.ai/api/v1/chat/completions"

    stock_context = f"""
【實時量化數據快照（真實數據源）】
代號: {s.get('symbol')} | 公司: {s.get('company_name')} | 當前股價: ${s.get('price')}
量化總分: {s.get('score')}/15 | 系統初始訊號: {s.get('signal_label')}
EPS年增率: {eps_clean}% | 營收年增率: {rev_clean}%
RS相對強度評級: {s.get('rs_rating', 'N/A')} | 風險 Beta 值: {s.get('beta', 'N/A')}
指標 RSI: {s.get('rsi', 'N/A')} | ATR% 日均波動: {s.get('atr_pct', 'N/A')}% | 成交量倍數: {s.get('vol_mult', 'N/A')}x
系統建議止損位: {sl_clean} | 預估目標價位: {tg_clean}
【核心提問】: {user_query}
"""

    # 📰 1. 新聞情報官：Claude 工業級極簡 Prompt + 我們的全域上下文防線
    def get_news_agent():
        prompt = f"""{stock_context}
任務：你是【新聞情報官】。請聯網搜索：
1. {s.get('symbol')} 最近 2 週重大新聞、公告、財報日期
2. 當前市場情緒與板塊趨勢

只報告可核實事實，不猜測。繁體中文，200 字內。"""
        try:
            r = requests.post(or_url, headers=headers, json={"model": "perplexity/sonar", "messages": [{"role": "user", "content": prompt}]}, timeout=30).json()
            return r['choices'][0]['message']['content'] if 'choices' in r else "（新聞情報獲取延遲）"
        except Exception as e: return f"（新聞情報網路異常: {str(e)}）"

    # 🏦 2. 機構動向官：Claude 硬核數字定向爆破 + 我們的全域上下文盲區防線
    def get_institution_agent():
        prompt = f"""{stock_context}
任務：請聯網搜索「{s.get('symbol')} analyst consensus rating buy hold sell target price 2026」。
來源優先：MarketBeat、TipRanks、Tickernerd。

回報：
- Buy/Hold/Sell 人數與比例
- 共識目標價
- 最近有哪家券商調整評級與具體數值

只報告數字與事實。繁體中文，150 字內。"""
        try:
            r = requests.post(or_url, headers=headers, json={"model": "perplexity/sonar", "messages": [{"role": "user", "content": prompt}]}, timeout=30).json()
            return r['choices'][0]['message']['content'] if 'choices' in r else "（機構籌碼獲取延遲）"
        except Exception as e: return f"（機構籌碼網路異常: {str(e)}）"

    p1_opinion = "（前線情報獲取失敗）"
    p2_opinion = "（前線情報獲取失敗）"

    # 🏎️ 雙通道高併發並行發射，等待時間維持在 4 秒內爆發
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(get_news_agent): 'news',
            executor.submit(get_institution_agent): 'institution'
        }
        for future in as_completed(futures):
            name = futures[future]
            if name == 'news': p1_opinion = future.result()
            elif name == 'institution': p2_opinion = future.result()

    # ⚖️ 3. 總裁判官：Claude 的中立三步法 + 我們的「首席量化分析師」心態錨定靈魂
    judge_prompt = f"""{stock_context}

你是冷靜、絕對中立、毫無立場的頂級量化分析師。

【新聞情報】: {p1_opinion}
【機構動向】: {p2_opinion}

不偏多不偏空，執行以下三步橫向交叉推演：
1. 審查矛盾：上述量化數據與情報之間有無明顯背離？
2. 診斷原因：背離的底層金融邏輯是什麼？（機構滯後？基本面惡化？技術假突破？）
3. 中立結論：純粹基於邏輯與數據的最終判斷。

⚠️ 嚴格按以下格式輸出，排版絕不允許亂來：
**🔍 多空背離審查**：
**⚙️ 核心成因診斷**：
**📋 最終量化結論**：（含止損 {sl_clean} / 目標 {tg_clean} 防守位說明）

繁體中文，300 字內。"""

    try:
        r_judge = requests.post(
            or_url,
            headers=headers,
            json={
                "model": "deepseek/deepseek-r1",
                "messages": [{"role": "user", "content": judge_prompt}],
                "temperature": 0.3
            },
            timeout=60
        ).json()

        if 'choices' not in r_judge:
            return jsonify({
                'success': False, 
                'error': f"最高裁判官通道限制: {r_judge.get('error', {}).get('message', str(r_judge))}"
            }), 500
            
        final_verdict = r_judge['choices'][0]['message']['content']

        return jsonify({
            'success': True,
            'agents': {
                'news': {'label': '📰 實時新聞情報官', 'model': 'Perplexity', 'content': p1_opinion},
                'institution': {'label': '🏦 機構動向情報官', 'model': 'Perplexity', 'content': p2_opinion},
                'judge': {'label': '⚖️ 最高風控裁決官', 'model': 'DeepSeek R1', 'content': final_verdict}
            },
            'p1_opinion': p1_opinion,
            'p2_opinion': p2_opinion,
            'p3_opinion': "（數據已直接對齊真實量化快照）",
            'final_consensus': final_verdict,
            'errors': None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'最高裁判官故障: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)