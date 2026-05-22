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
        'is_scanning': False,
        'progress': 0,
        'current': '未啟動',
        'total': 227,
        'done': 0
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

# ─── 🤖 AI MULTI-AGENT INTELLIGENCE ROUNDTABLE v2 (排好隊伍標準版) ───
@app.route('/api/scan/ai_debate', methods=['POST', 'GET'])
def ai_debate():
    import os
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if request.method == 'GET':
        return jsonify({'success': False, 'error': 'AI 圓桌會後端管線通電正常！'})
        
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
            print(f"[AI DEBATE BACKGROUND LOOKUP ERR] {symbol}: {err}")

    if not s or 'symbol' not in s:
        return jsonify({'success': False, 'error': f'無法取得「{symbol or "未知"}」的量化數據'}), 400
    if not api_key:
        return jsonify({'success': False, 'error': '未設定 OPENROUTER_API_KEY。'}), 400

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://stockscanner.com",
        "Content-Type": "application/json"
    }
    or_url = "https://openrouter.ai/api/v1/chat/completions"

    stock_context = f"""
【股票量化快照】
代號: {s.get('symbol')} | 公司: {s.get('company_name', s.get('symbol'))} | 股價: ${s.get('price')} | 板塊: {s.get('sector', '未知')}
系統評分: {s.get('score')}/15 | 訊號: {s.get('signal_label')} | EPS年增率: {s.get('eps_yoy') if s.get('eps_yoy') is not None else 'N/A'}% | 營收年增率: {s.get('rev_yoy') if s.get('rev_yoy') is not None else 'N/A'}%
RS評級: {s.get('rs_rating', 'N/A')} | Beta: {s.get('beta', 'N/A')} | RSI: {s.get('rsi', 'N/A')} | ATR%: {s.get('atr_pct', 'N/A')}% | 量能倍數: {s.get('vol_mult', 'N/A')}x
止損: ${s.get('stop_loss', 'N/A')} | 目標: ${s.get('target', 'N/A')}
【用戶問題】: {user_query}
"""

    def agent_news():
        prompt = f"""{stock_context}\n你是【新聞情報官】。請立即聯網報告 {s.get('symbol')} 最近 2 週的重大真實新聞、即將到來的財報催化劑與板塊趨勢。必須用繁體中文回答，字數 150 字內。"""
        r = requests.post(
            or_url,
            headers=headers,
            json={
                "model": "perplexity/llama-3.1-sonar-large-128k-online",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        ).json()
        if 'choices' not in r:
            return None, f"新聞情報官異常: {r.get('error', {}).get('message', str(r))}"
        return r['choices'][0]['message']['content'], None

    def agent_institution():
        prompt = f"""{stock_context}\n你是【機構動向官】。請立即聯網報告 {s.get('symbol')} 最近 30 天內華爾街分析師評級變化、目標價調整與大型機構造持倉變化。必須用繁體中文回答，字數 150 字內。"""
        r = requests.post(
            or_url,
            headers=headers,
            json={
                "model": "perplexity/llama-3.1-sonar-large-128k-online",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        ).json()
        if 'choices' not in r:
            return None, f"機構動向官異常: {r.get('error', {}).get('message', str(r))}"
        return r['choices'][0]['message']['content'], None

    def agent_fundamental():
        prompt = f"""{stock_context}\n你是【財報深度官】。請基於上方快照數據，深度解讀其增長率競爭力、RS評級強弱與波動風險是否可控。必須用繁體中文回答，字數 200 字內。給出明確的「財務支撐強/中/弱」結論。"""
        r = requests.post(
            or_url,
            headers=headers,
            json={
                "model": "deepseek/deepseek-chat",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        ).json()
        if 'choices' not in r:
            return None, f"財報深度官異常: {r.get('error', {}).get('message', str(r))}"
        return r['choices'][0]['message']['content'], None

    news_opinion = None
    institution_opinion = None
    fundamental_opinion = None
    errors = []
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(agent_news): 'news',
            executor.submit(agent_institution): 'institution',
            executor.submit(agent_fundamental): 'fundamental'
        }
        for future in as_completed(futures):
            agent_name = futures[future]
            try:
                result, err = future.result()
                if err:
                    errors.append(err)
                    result = f"（數據獲取失敗：{err}）"
                if agent_name == 'news':
                    news_opinion = result
                elif agent_name == 'institution':
                    institution_opinion = result
                elif agent_name == 'fundamental':
                    fundamental_opinion = result
            except Exception as e:
                errors.append(f"{agent_name}: {str(e)}")

    judge_prompt = f"""{stock_context}
【重要合規聲明：本報告僅作為資料科學學術模擬與歷史公開資訊整合練習，不包含任何投資招攬、操作推薦或前瞻性財務指導。】

你是【數據客觀整合官】，請審閱並摘要對齊下方三位分析師的客觀學術研究，針對用戶 query 「{user_query}」進行純粹的資料比對與大數據趨勢歸納：
📰 研究員甲: \"{news_opinion}\"
🏦 研究員乙: \"{institution_opinion}\"
📊 研究員丙: \"{fundamental_opinion}\"

請嚴格依據以下客觀學術格式進行中立排版：
**📊 資訊整合要點**（客觀列出多方資訊吻合處）
**🔍 數據潛在盲區**（列出財報或新聞中未明朗的風險因子）
**📝 模擬量化評級**（給出模型中立分類：類別A-動能強/類別B-基本穩/類別C-觀望中/類別D-保守看，並說明數據邏輯）
**⚙️ 數學參照區間**（僅依據公式計算之止損 ${s.get('stop_loss', 'N/A')} 與目標 ${s.get('target', 'N/A')} 進行公式對齊說明）
完全使用繁體中文，300字內。"""

    try:
        r4 = requests.post(
            or_url,
            headers=headers,
            json={
                "model": "google/gemini-2.0-flash-001",
                "messages": [{"role": "user", "content": judge_prompt}],
                "temperature": 0.3
            },
            timeout=45
        ).json()

        if 'choices' not in r4:
            return jsonify({
                'success': False, 
                'error': f"總裁判官通道限制: {r4.get('error', {}).get('message', str(r4))}"
            }), 500
            
        final_verdict = r4['choices'][0]['message']['content']

        return jsonify({
            'success': True,
            'agents': {
                'news': {
                    'label': '📰 新聞情報官',
                    'model': 'Perplexity (聯網)',
                    'content': news_opinion
                },
                'institution': {
                    'label': '🏦 機構動向官',
                    'model': 'Perplexity (聯網)',
                    'content': institution_opinion
                },
                'fundamental': {
                    'label': '📊 財報深度官',
                    'model': 'DeepSeek',
                    'content': fundamental_opinion
                },
                'judge': {
                    'label': '⚖️ 總裁判官',
                    'model': 'Gemini 2.0 Flash',
                    'content': final_verdict
                }
            },
            'p1_opinion': news_opinion,
            'p2_opinion': institution_opinion,
            'p3_opinion': fundamental_opinion,
            'final_consensus': final_verdict,
            'errors': errors if errors else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'總裁判官故障: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)