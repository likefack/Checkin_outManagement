import os
import sqlite3
import datetime
import pytz 
import traceback
import logging # 追加
import queue
import json
import secrets # 追加
import atexit # 追加
from apscheduler.schedulers.background import BackgroundScheduler # 追加
# from logging.handlers import RotatingFileHandler # 削除またはコメントアウト
from concurrent_log_handler import ConcurrentRotatingFileHandler # 追加
import os # osがインポートされているか確認（なければ追加）
from flask import Flask, render_template, request, jsonify, Response
from dotenv import load_dotenv
import database
from report_generator import create_report
from achievement_logic import check_achievements
from email_sender import send_email_async
# 【追加】質問管理アプリのBlueprintをインポート
# 注意: pyフォルダから見た相対パスでインポートできるようパスを通すか、
# school_qnaフォルダをパッケージとして認識させる必要があります。
# 簡易的に、sys.pathを追加する方法をとります。
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..')) 
from school_qna import school_qna_bp

# 【追加】ポーリング系のログを除外するフィルタークラス
class PollingLogFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        # 成功(200)したポーリングリクエストはログに出さない
        if 'GET /qna/api/count' in msg and '" 200 ' in msg:
            return False
        if 'GET /qna/api/check_new_questions' in msg and '" 200 ' in msg:
            return False
        if 'GET /api/stream' in msg and '" 200 ' in msg: # メインアプリのSSE接続ログも抑制したい場合
            return False
        return True

# --- アプリケーションの初期設定 ---
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '管理者用_touchable', '.env')
load_dotenv(dotenv_path)

# SSE用: 接続中のクライアントキューを保持するリスト
sse_clients = []

def announce_update():
    """全接続クライアントに更新通知を送る"""
    msg = json.dumps({"type": "update"})
    # リストのコピーを作成して反復処理（スレッドセーフ対策）
    for q in sse_clients[:]:
        try:
            q.put(msg)
        except Exception:
            pass # エラー時は無視

app = Flask(__name__, 
            template_folder=os.path.join(os.path.dirname(__file__), '..', 'templates'),
            static_folder=os.path.join(os.path.dirname(__file__), '..', 'static'))

# 【修正】セッションを利用するためにSecret Keyを設定 (これがないとログイン機能などで落ちる)
app.secret_key = secrets.token_hex(16)

# 【追加】Blueprintを登録 (URLのプレフィックスを /qna に設定)
app.register_blueprint(school_qna_bp, url_prefix='/qna')

# --- ログ設定の追加 ---    
def configure_logging(app):
    # ログ保存先ディレクトリ: ../../管理者用_touchable/server_logs
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, '..', '..', '管理者用_touchable', 'server_logs')

    # ディレクトリが存在しない場合は作成
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # ログファイルのパス (起動日を含める: server_2023-01-01.log)
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    log_file_path = os.path.join(log_dir, f'server_{today_str}.log')

    # ローテーション設定: 10MBごとに新しいファイルにし、最大10世代残す
    # Windows/マルチプロセス環境でも安全に動作する ConcurrentRotatingFileHandler を使用
    file_handler = ConcurrentRotatingFileHandler(log_file_path, maxBytes=10*1024*1024, backupCount=10, encoding='utf-8')
    
    # ログのフォーマット設定: 日時 レベル モジュール メッセージ
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(module)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    # ログレベル設定 (INFO以上を記録)
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(PollingLogFilter()) # 【追加】ファイルログにもフィルタ適用

    # --- 追加: コンソール出力用のハンドラ ---
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)
    stream_handler.addFilter(PollingLogFilter()) # 【追加】コンソールログにもフィルタ適用
    # ------------------------------------

    # ルートロガー設定（ここだけにハンドラを集約する）
    root_logger = logging.getLogger()
    
    # 既存のハンドラがあればクリア（重複防止）
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)
    root_logger.setLevel(logging.INFO)

    # アプリやWerkzeugのロガーは、独自のハンドラを持たせずルートへ伝播させる
    # これにより「アプリで出力」→「ルートでも出力」という重複を防ぐ
    app.logger.handlers = []
    logging.getLogger('werkzeug').handlers = []

# ログ設定を適用
configure_logging(app)

# --- タイムゾーン定義 ---
JST = pytz.timezone('Asia/Tokyo')
UTC = pytz.utc

# --- テーマカラーの注入 ---
@app.context_processor
def inject_theme_color():
    return dict(theme_color=os.getenv('THEME_COLOR', '#4a90e2'))

# --- データベース接続 ---
def get_db_connection():
    # タイムアウトを10秒に設定（デフォルトは5秒）。
    # これにより、複数端末から一斉にアクセスがあっても、ロックが解除されるのを長く待機でき、エラーになりにくくなる。
    conn = sqlite3.connect(database.DB_PATH, timeout=10, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

# --- ヘルパー関数 ---
# 【追加】ログIDから表示用の詳細データを取得する関数
def _get_log_details(conn, log_id):
    query = """
        SELECT al.id AS log_id, s.system_id, al.seat_number, al.entry_time, al.exit_time, 
               s.name, s.grade, s.class, s.student_number 
        FROM attendance_logs al 
        JOIN students s ON al.system_id = s.system_id 
        WHERE al.id = ?
    """
    row = conn.execute(query, (log_id,)).fetchone()
    return dict(row) if row else None

def parse_db_time_to_jst(dt_str):
    if not dt_str: return None
    try:
        dt = datetime.datetime.fromisoformat(dt_str)
        return dt.astimezone(JST) if dt.tzinfo else JST.localize(dt)
    except (ValueError, TypeError):
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
            try:
                dt_naive = datetime.datetime.strptime(dt_str, fmt)
                return JST.localize(dt_naive)
            except (ValueError, TypeError):
                continue
    return None

def _reset_forgotten_checkin_status(conn, system_id):
    student = conn.execute('SELECT is_present, current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()
    if student and student['is_present']:
        log_entry = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (student['current_log_id'],)).fetchone()
        if log_entry and parse_db_time_to_jst(log_entry['entry_time']).date() < datetime.datetime.now(JST).date():
            app.logger.info(f"ID:{system_id} の前日以前の入室記録を検出。入退ステータスをリセットします。")
            conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
            conn.commit()
            app.logger.info(f"ID:{system_id} の入退ステータスをリセットしました。")
            return True
    return False

def _handle_notifications(conn, system_id, event_type, log_id):
    student = conn.execute('SELECT name, guardian_email FROM students WHERE system_id = ?', (system_id,)).fetchone()
    if not student: return None

    ach_result = check_achievements(conn, system_id, event_type, log_id)
    app_name = os.getenv('APP_NAME')
    org_name = os.getenv('ORGANIZATION_NAME')
    sender_name = os.getenv('SENDER_NAME')
    log_entry = conn.execute('SELECT entry_time, exit_time FROM attendance_logs WHERE id = ?', (log_id,)).fetchone()
    
    guardian_message = ""
    if ach_result:
        msg = ach_result.get('guardian_message')
        if msg:
            guardian_message = msg

    # 自動配信のフッターテキスト
    footer_text = "\n\n※このメールはシステムより自動配信されています。"
    if event_type == 'check_in':
        entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
        subject = f"【{app_name}】{student['name']}さんの入室通知"
        
        # メッセージがある場合のみ改行を含めて設定
        extra_msg = f"\n{guardian_message}" if guardian_message else ""

        body = f"{student['name']}さんの保護者様\n\nお世話になっております、{org_name}の{sender_name}です。\n\n{student['name']}さんが{entry_time_jst.strftime('%H時%M分')}に入室されたことをお知らせします。{extra_msg}\n\n今後ともよろしくお願いいたします。\n{sender_name}{footer_text}"
        send_email_async(student['guardian_email'], subject, body)
    elif event_type == 'check_out':
        entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
        exit_time_jst = parse_db_time_to_jst(log_entry['exit_time'])
        stay_text = ""
        if entry_time_jst and exit_time_jst:
            stay_duration = exit_time_jst - entry_time_jst
            stay_hours, remainder = divmod(stay_duration.total_seconds(), 3600)
            stay_minutes = remainder // 60
            stay_text = f"滞在時間: {int(stay_hours)}時間{int(stay_minutes)}分"
        subject = f"【{app_name}】{student['name']}さんの退室通知"

        # 滞在時間とメッセージをリスト化し、存在するものだけを結合
        infos = []
        if stay_text: infos.append(stay_text)
        if guardian_message: infos.append(guardian_message)
        extra_info = "\n".join(infos)
        if extra_info: extra_info = f"\n{extra_info}"

        body = f"{student['name']}の保護者様\n\nお世話になっております、{org_name}の{sender_name}です。\n\n{student['name']}さんが{exit_time_jst.strftime('%H時%M分')}に退室されたことをお知らせします。{extra_info}\n\n今後ともよろしくお願いいたします。\n{sender_name}{footer_text}"
        send_email_async(student['guardian_email'], subject, body)
    return ach_result

# --- 起動時処理 ---
with app.app_context():
    database.init_db()

# --- ルーティング ---
@app.route('/')
def index():
    mode = request.args.get('mode', 'students')
    app_name = os.getenv('APP_NAME', '入退室管理システム')
    org_name_eng = os.getenv('ORGANIZATION_NAME_ENG', '2026 Codeswitcher Co.,Ltd.')
    if mode == 'edit':
        return_mode = request.args.get('return_mode', 'admin')
        return render_template('edit.html', app_name=app_name, org_name_eng=org_name_eng, return_mode=return_mode)
    else:
        max_seat_number = int(os.getenv('MAX_SEAT_NUMBER', 72))
        return render_template('index.html', mode=mode, app_name=app_name, max_seat_number=max_seat_number, org_name_eng=org_name_eng)

# --- API ---
@app.route('/api/initial_data')
def get_initial_data():
    now_jst = datetime.datetime.now(JST)
    today_date = now_jst.date() # 今日の日付を取得
    start_of_day_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day_jst.astimezone(UTC)
    conn = get_db_connection()

    try: # データベース操作全体をtry...finallyで囲む
        students_cursor = conn.execute('SELECT system_id, name, grade, class, student_number, is_present, current_log_id FROM students ORDER BY grade, class, student_number')
        all_students_list = students_cursor.fetchall() # 一度リストとして取得

        students_data_nested = {}
        #is_present の状態を日付でチェックして上書き 
        ids_to_reset = [] # DBリセット対象のsystem_idリスト
        for student_row in all_students_list:
            student = dict(student_row) # Rowオブジェクトを辞書に変換
            is_present_db = student['is_present'] == 1
            current_log_id = student['current_log_id']
            is_present_today_for_frontend = False # フロントエンドに返す値（デフォルトFalse）

            if is_present_db and current_log_id:
                # 入室中の場合、ログの日付を確認
                log_entry = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (current_log_id,)).fetchone()
                if log_entry:
                    entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
                    if entry_time_jst and entry_time_jst.date() == today_date:
                        # 今日の記録ならフロントエンドにも True を返す
                        is_present_today_for_frontend = True
                    else:
                        # 前日以前の記録ならリセット対象に追加
                        ids_to_reset.append(student['system_id'])
                        app.logger.info(f"ID:{student['system_id']} の前日以前の入室記録を検出(initial_data)。リセット対象に追加。")
            # else: is_present が 0 または log_id がない場合は is_present_today_for_frontend は False のまま

            # フロントエンドに返す is_present を設定
            student['is_present'] = is_present_today_for_frontend

            # ネスト構造に格納 
            grade, class_num, number = student['grade'], student['class'], student['student_number']
            if grade not in students_data_nested: students_data_nested[grade] = {}
            if class_num not in students_data_nested[grade]: students_data_nested[grade][class_num] = {}
            students_data_nested[grade][class_num][number] = student

        #リセット対象の生徒のDBステータスを更新
        if ids_to_reset:
            # プレースホルダーを使って安全にUPDATE文を実行
            placeholders = ','.join('?' * len(ids_to_reset))
            conn.execute(f'UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id IN ({placeholders})', ids_to_reset)
            conn.commit()
            app.logger.info(f"リセット対象 {len(ids_to_reset)} 件のステータスをDBでリセットしました。")

        # 今日の入退室記録を取得
        attendees_cursor = conn.execute('SELECT al.id AS log_id, s.system_id, al.seat_number, al.entry_time, al.exit_time, s.name, s.grade, s.class, s.student_number FROM attendance_logs al JOIN students s ON al.system_id = s.system_id WHERE al.entry_time >= ? ORDER BY al.entry_time ASC', (start_of_day_utc.isoformat(),))
        current_attendees = [dict(row) for row in attendees_cursor.fetchall()]

        return jsonify({'students': students_data_nested, 'attendees': current_attendees})

    except Exception as e:
        app.logger.error(f"Error in get_initial_data: {e}", exc_info=True)
        # エラーが発生した場合もコネクションを閉じる
        if conn:
            conn.close()
        return jsonify({'status': 'error', 'message': f'初期データの取得中にエラーが発生しました: {e}'}), 500
    finally:
        # 正常終了時もコネクションを閉じる
        if conn:
            conn.close()

@app.route('/api/check_in', methods=['POST'])
def check_in():
    data = request.json
    system_id, seat_number = data.get('system_id'), data.get('seat_number')
    if not system_id or not seat_number:
        return jsonify({'status': 'error', 'message': 'IDまたは座席番号がありません。'}), 400

    conn = get_db_connection()
    try:
        #まず現在の生徒情報を取得 
        student = conn.execute('SELECT is_present, name, title, current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()
        if not student:
             return jsonify({'status': 'error', 'message': '該当する生徒が見つかりません。'}), 404 # 生徒が見つからない場合のエラーを追加

        is_present_today = False
        #is_present が True の場合、それが今日の記録か確認 
        if student['is_present'] and student['current_log_id']:
            log_entry = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (student['current_log_id'],)).fetchone()
            if log_entry:
                entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
                if entry_time_jst and entry_time_jst.date() == datetime.datetime.now(JST).date():
                    is_present_today = True
                else:
                    # 前日以前の記録ならリセット
                    app.logger.info(f"ID:{system_id} の前日以前の入室記録を検出(check_in)。ステータスをリセットします。")
                    conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
                    app.logger.info(f"ID:{system_id} のステータスをリセットしました。")
                    # student 変数はここでは再取得不要（入室処理に進むため）

        #今日の記録があるかで分岐 
        if is_present_today:
            # 既に今日入室済みの場合、時刻を確認して「より早い時刻」なら更新する（挙動の合理化）
            current_log_id = student['current_log_id']
            # クライアントからの指定時刻
            entry_time_str = data.get('entry_time')
            
            updated = False
            if entry_time_str:
                new_entry_time_utc = datetime.datetime.fromisoformat(entry_time_str).astimezone(UTC)
                # DB上の既存時刻
                current_log = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (current_log_id,)).fetchone()
                current_entry_time_utc = datetime.datetime.fromisoformat(current_log['entry_time']).astimezone(UTC)

                # 新しいリクエストの方が過去（古い）なら、開始時刻を修正する
                if new_entry_time_utc < current_entry_time_utc:
                    conn.execute('UPDATE attendance_logs SET entry_time = ? WHERE id = ?', (new_entry_time_utc.isoformat(), current_log_id))
                    app.logger.info(f"ID:{system_id} の入室時刻をより早い時刻に修正しました ({current_entry_time_utc} -> {new_entry_time_utc})")
                    updated = True

            # エラー(409)ではなく成功(200)を返し、クライアント側のキューを消化させる
            # ログデータ取得のためにIDをセット
            new_log_id = current_log_id
            ach_result = None # 重複時は通知しない
            msg = f'{student["name"]}さんの入室時刻を修正しました。' if updated else f'{student["name"]}さんは既に入室済みです。'
            
        else:
            # --- 入室処理 ---
            # クライアントから指定時刻があればそれを使用（オフライン同期用）、なければ現在時刻
            entry_time_str = data.get('entry_time')
            if entry_time_str:
                entry_time_utc = datetime.datetime.fromisoformat(entry_time_str).astimezone(UTC)
            else:
                entry_time_utc = datetime.datetime.now(UTC)

            cursor = conn.execute('INSERT INTO attendance_logs (system_id, seat_number, entry_time) VALUES (?, ?, ?)', (system_id, seat_number, entry_time_utc.isoformat()))
            new_log_id = cursor.lastrowid
            
            # 【追加】日付チェック：現在の日付（JST）とリクエストの日付（JST）が一致する場合のみ在室フラグを立てる
            # JSTタイムゾーンを定義（UTC+9）
            JST = datetime.timezone(datetime.timedelta(hours=9))
            entry_date_jst = entry_time_utc.astimezone(JST).date()
            current_date_jst = datetime.datetime.now(JST).date()

            if entry_date_jst == current_date_jst:
                conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
            else:
                app.logger.info(f"日付不一致のため在室フラグ更新をスキップ: ID={system_id}, EntryDate={entry_date_jst}, Today={current_date_jst}")

            # 実績判定処理を呼び出す
            ach_result = _handle_notifications(conn, system_id, 'check_in', new_log_id)
            msg = f'{student["name"]}さんが入室しました。'
        
        # 最終的に通知に使うランクを決定する。
        # もし実績（ach_result）の中に新しいランク情報があればそれを優先し、
        # なければ最初にDBから読み込んだランク情報を使う。
        final_rank = ach_result.get('rank') if ach_result and ach_result.get('rank') else student['title']
        
        # [操作ログ] 手動入室の詳細
        log_suffix = " (オフライン同期)" if data.get('entry_time') else ""
        app.logger.info(f"[操作ログ] 入室処理(手入力){log_suffix} - 生徒ID: {system_id}, 座席: {seat_number}, 実行者IP: {request.remote_addr}")

        conn.commit()
        
        # 他の端末へ更新を通知
        announce_update()

        # `rank`キーに、上で決定した最新のランク情報(final_rank)を渡す
        # 【修正】リスト更新用のログデータを返却に追加
        log_data = _get_log_details(conn, new_log_id)
        # msg変数はif/elseブロック内で定義済み
        return jsonify({'status': 'success', 'message': msg, 'rank': final_rank, 'achievement': ach_result, 'log_data': log_data})

    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/check_out', methods=['POST'])
def check_out():
    data = request.json
    system_id, log_id, exit_time_str = data.get('system_id'), data.get('log_id'), data.get('exit_time')
    if not system_id and not log_id: return jsonify({'status': 'error', 'message': 'IDがありません。'}), 400
    conn = get_db_connection()
    try:
        if log_id and not system_id:
            id_row = conn.execute('SELECT system_id FROM attendance_logs WHERE id = ?', (log_id,)).fetchone()
            if not id_row: return jsonify({'status': 'error', 'message': '該当の記録が見つかりません。'}), 404
            system_id = id_row['system_id']

        student = conn.execute('SELECT name, title FROM students WHERE system_id = ?', (system_id,)).fetchone()
        log_id_to_update = log_id if log_id else conn.execute('SELECT current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()['current_log_id']
        if not log_id_to_update: return jsonify({'status': 'error', 'message': '有効な退室記録が見つかりません。'}), 409
        log_to_exit = conn.execute('SELECT exit_time FROM attendance_logs WHERE id = ?', (log_id_to_update,)).fetchone()
        if not log_to_exit: return jsonify({'status': 'error', 'message': f'内部エラー: ログID {log_id_to_update} が見つかりません。'}), 500
        
        # 既に退室済みの場合
        if log_to_exit['exit_time']:
            # エラー(409)ではなく成功(200)を返し、キューを消化させる
            msg = '既に退室処理済みです。'
            ach_result = None
            final_rank = student['title']
            # ログデータ取得
            log_data = _get_log_details(conn, log_id_to_update)
            return jsonify({'status': 'success', 'message': msg, 'rank': final_rank, 'achievement': ach_result, 'log_data': log_data})
            
        exit_time_utc = datetime.datetime.fromisoformat(exit_time_str).astimezone(UTC) if exit_time_str else datetime.datetime.now(UTC)
        conn.execute('UPDATE attendance_logs SET exit_time = ? WHERE id = ?', (exit_time_utc.isoformat(), log_id_to_update))
        conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
        
        # 実績判定処理を呼び出す
        ach_result = _handle_notifications(conn, system_id, 'check_out', log_id_to_update)
        
        # 最終的に通知に使うランクを決定する
        final_rank = ach_result.get('rank') if ach_result and ach_result.get('rank') else student['title']
        
        # [操作ログ] 手動退室の詳細
        log_suffix = " (オフライン同期)" if data.get('exit_time') else ""
        app.logger.info(f"[操作ログ] 退室処理(手入力){log_suffix} - 生徒ID: {system_id}, 実行者IP: {request.remote_addr}")

        conn.commit()
        
        # 他の端末へ更新を通知
        announce_update()

        # `rank`キーに、最新のランク情報(final_rank)を渡す
        # 【修正】リスト更新用のログデータを返却に追加
        log_data = _get_log_details(conn, log_id_to_update)
        return jsonify({'status': 'success', 'message': f'{student["name"]}さんが退室しました。', 'rank': final_rank, 'achievement': ach_result, 'log_data': log_data})

    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/qr_process', methods=['POST'])
def qr_process():
    data = request.json
    # [デバッグログ] QRコード読み取り時の生データ（受信ペイロード）
    app.logger.info(f"[デバッグログ] QR受信データ: {data}, 実行者IP: {request.remote_addr}")

    try: # system_id が数値でない場合のエラーを捕捉
        system_id = int(data.get('system_id'))
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': '無効なID形式です。'}), 400

    if not system_id: return jsonify({'status': 'error', 'message': 'IDがありません。'}), 400
    conn = get_db_connection()
    try:
        #まず生徒情報を取得
        student = conn.execute('SELECT is_present, name, title, current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()
        if not student: return jsonify({'status': 'error', 'message': '該当する生徒が見つかりません。'}), 404

        is_present_today = False
        #is_present が True の場合、それが今日の記録か確認
        if student['is_present'] and student['current_log_id']:
            log_entry = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (student['current_log_id'],)).fetchone()
            if log_entry:
                entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
                # entry_timeが取得でき、かつそれが今日の日付であれば True
                if entry_time_jst and entry_time_jst.date() == datetime.datetime.now(JST).date():
                    is_present_today = True
                else:
                    #  前日以前の記録なら、ここで強制的にリセット 
                    app.logger.info(f"ID:{system_id} の前日以前の入室記録を検出(qr_process)。ステータスをリセットします。")
                    conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
                    app.logger.info(f"ID:{system_id} のステータスをリセットしました。")
                    # is_present_today は False のまま（=入室処理へ）

        message, ach_result = "", None
        #「今日」入室しているかどうかで分岐
        if is_present_today:
            # --- 退室処理 ---
            # クライアントから指定時刻があればそれを使用（オフライン同期用）
            timestamp_str = data.get('timestamp')
            if timestamp_str:
                exit_time_utc = datetime.datetime.fromisoformat(timestamp_str).astimezone(UTC)
            else:
                exit_time_utc = datetime.datetime.now(UTC)

            log_id_to_update = student['current_log_id']
            # log_idがない場合はエラー（通常は起こらないはず）
            if not log_id_to_update: return jsonify({'status': 'error', 'message': '有効な退室記録が見つかりません。'}), 409

            # 念のため、対象ログが本当に未退室か確認
            log_to_exit = conn.execute('SELECT exit_time FROM attendance_logs WHERE id = ?', (log_id_to_update,)).fetchone()
            if not log_to_exit: return jsonify({'status': 'error', 'message': f'内部エラー: ログID {log_id_to_update} が見つかりません。'}), 500
            if log_to_exit['exit_time']: return jsonify({'status': 'info', 'message': '既に退室処理済みです。'}), 200 # 重複退室はエラーにしない

            conn.execute('UPDATE attendance_logs SET exit_time = ? WHERE id = ?', (exit_time_utc.isoformat(), log_id_to_update))
            conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
            message = f'{student["name"]}さんが自習室から退室しました。'
            ach_result = _handle_notifications(conn, system_id, 'check_out', log_id_to_update)
            
            # [操作ログ] QR退室の詳細
            log_suffix = " (オフライン同期)" if data.get('timestamp') else ""
            app.logger.info(f"[操作ログ] 退室処理(QR){log_suffix} - 生徒ID: {system_id}")
        else:
            # --- 入室処理 ---
            # クライアントから指定時刻があればそれを使用（オフライン同期用）
            timestamp_str = data.get('timestamp')
            if timestamp_str:
                entry_time_utc = datetime.datetime.fromisoformat(timestamp_str).astimezone(UTC)
            else:
                entry_time_utc = datetime.datetime.now(UTC)

            cursor = conn.execute('INSERT INTO attendance_logs (system_id, entry_time) VALUES (?, ?)', (system_id, entry_time_utc.isoformat()))
            new_log_id = cursor.lastrowid
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
            message = f'{student["name"]}さんが自習室に入室しました。'
            ach_result = _handle_notifications(conn, system_id, 'check_in', new_log_id)

            # [操作ログ] QR入室の詳細 (QR入室時は座席指定なしのためNULL/None扱いです)
            log_suffix = " (オフライン同期)" if data.get('timestamp') else ""
            app.logger.info(f"[操作ログ] 入室処理(QR){log_suffix} - 生徒ID: {system_id}, 座席: 指定なし")

        # 最新の称号情報を決定
        # student変数はリセット時に再取得しないため、必要ならここで再取得
        current_student_state = conn.execute('SELECT title FROM students WHERE system_id = ?', (system_id,)).fetchone()
        current_title = current_student_state['title'] if current_student_state else None
        final_rank = ach_result.get('rank') if ach_result and ach_result.get('rank') else current_title

        # 【修正】リスト更新用のログデータを取得 (入室時はnew_log_id, 退室時はlog_id_to_updateを使用)
        target_log_id = new_log_id if not is_present_today else log_id_to_update
        log_data = _get_log_details(conn, target_log_id)

        conn.commit()
        
        # 他の端末へ更新を通知
        announce_update()

        return jsonify({'status': 'success', 'message': message, 'rank': final_rank, 'achievement': ach_result, 'log_data': log_data})

    except Exception as e:
        conn.rollback()
        app.logger.error(f"Error in qr_process: {e}", exc_info=True) # エラーログを追加
        return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        # finallyブロックで確実にコネクションを閉じる
        if conn:
            conn.close()

#`exit_all`関数を新しい仕様に合わせて修正 
@app.route('/api/exit_all', methods=['POST'])
def exit_all():
    conn = get_db_connection()
    try:
        # 今日の始まりをUTCで定義
        start_of_today_jst = datetime.datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_today_utc = start_of_today_jst.astimezone(UTC)
        
        # 「本日入室」かつ「在室中」かつ「有効なログを持つ」生徒のみを厳選
        present_students = conn.execute('''
            SELECT s.system_id, s.current_log_id
            FROM students s
            JOIN attendance_logs al ON s.current_log_id = al.id
            WHERE s.is_present = 1 
              AND al.exit_time IS NULL
              AND al.entry_time >= ?
        ''', (start_of_today_utc.isoformat(),)).fetchall()

        if not present_students:
            return jsonify({'status': 'success', 'message': '本日退室させる生徒がいません。'})

        exit_time_utc = datetime.datetime.now(UTC)
        log_ids = [s['current_log_id'] for s in present_students]
        system_ids = [s['system_id'] for s in present_students]

        if not log_ids: #念のためチェック
            return jsonify({'status': 'info', 'message': '退室記録対象の生徒がいません。'})

        conn.execute(f'UPDATE attendance_logs SET exit_time = ? WHERE id IN ({",".join("?"*len(log_ids))})',
                     [exit_time_utc.isoformat()] + log_ids)
        conn.execute(f'UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id IN ({",".join("?"*len(system_ids))})',
                     system_ids)

        for student in present_students:
            _handle_notifications(conn, student['system_id'], 'check_out', student['current_log_id'])

        conn.commit()
        
        # 他の端末へ更新を通知
        announce_update()
        
        return jsonify({'status': 'success', 'message': f'{len(present_students)}名の生徒を全員退室させました。'})
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Error in exit_all: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()
        
@app.route('/api/create_report', methods=['POST'])
def handle_create_report():
    data = request.json
    start_date, end_date = data.get('start_date'), data.get('end_date')
    if not start_date or not end_date: return jsonify({'status': 'error', 'message': '期間が指定されていません。'}), 400

    # [操作ログ] レポート作成開始
    app.logger.info(f"[操作ログ] 集計レポート作成開始 - 期間: {start_date} ～ {end_date}, 実行者IP: {request.remote_addr}")

    file_path, message = create_report(database.DB_PATH, start_date, end_date)
    if file_path:
        if file_path == "No data": 
            app.logger.info(f"[操作ログ] 集計レポート作成完了(データなし) - 期間: {start_date} ～ {end_date}")
            return jsonify({'status': 'info', 'message': message})
        
        app.logger.info(f"[操作ログ] 集計レポート作成完了(成功) - 期間: {start_date} ～ {end_date}, 出力ファイル: {os.path.basename(file_path)}")
        return jsonify({'status': 'success', 'message': message})
    else:
        app.logger.error(f"[操作ログ] 集計レポート作成失敗 - 期間: {start_date} ～ {end_date}, エラー: {message}")
        return jsonify({'status': 'error', 'message': message}), 500

# --- 設定管理用API ---
@app.route('/api/settings', methods=['GET', 'POST'])
def manage_settings():
    # 編集を許可するキーのリスト
    ALLOWED_KEYS = ['APP_NAME', 'ORGANIZATION_NAME', 'ORGANIZATION_NAME_ENG', 'MAX_SEAT_NUMBER', 'THEME_COLOR']
    
    if request.method == 'GET':
        settings = {key: os.getenv(key, '') for key in ALLOWED_KEYS}
        return jsonify(settings)
    
    elif request.method == 'POST':
        new_settings = request.json
        if not new_settings:
            return jsonify({'status': 'error', 'message': 'データがありません'}), 400
            
        try:
            # .envファイルを読み込んで行ごとに処理
            lines = []
            if os.path.exists(dotenv_path):
                with open(dotenv_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            
            updated_keys = set()
            new_lines = []
            
            for line in lines:
                key_part = line.split('=')[0].strip()
                # 更新対象のキーであれば、新しい値に書き換える
                if key_part in ALLOWED_KEYS and key_part in new_settings:
                    new_val = str(new_settings[key_part]).replace('\n', '') # 改行除去
                    new_lines.append(f"{key_part}=\"{new_val}\"\n")
                    updated_keys.add(key_part)
                else:
                    new_lines.append(line)
            
            # ファイルになかったキーは追記する
            for key in ALLOWED_KEYS:
                if key in new_settings and key not in updated_keys:
                    new_val = str(new_settings[key]).replace('\n', '')
                    new_lines.append(f"\n{key}=\"{new_val}\"\n")
            
            # 書き込み
            with open(dotenv_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
                
            # 環境変数を再ロードして即時反映させることは難しい（再起動推奨）が、
            # os.environ は更新しておく
            for key in ALLOWED_KEYS:
                if key in new_settings:
                    os.environ[key] = str(new_settings[key])

            return jsonify({'status': 'success', 'message': '設定を保存しました。反映にはサーバーの再起動が必要な場合があります。'})
            
        except Exception as e:
            app.logger.error(f"設定保存エラー: {e}", exc_info=True)
            return jsonify({'status': 'error', 'message': f'保存に失敗しました: {e}'}), 500

# --- 記録編集ページ用API ---
def convert_to_utc(time_str):
    if not time_str:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            dt_naive = datetime.datetime.strptime(time_str, fmt)
            dt_jst = JST.localize(dt_naive)
            return dt_jst.astimezone(UTC).isoformat()
        except ValueError:
            continue
    return None

@app.route('/api/logs', methods=['GET'])
def get_logs():
    # print(f"[デバッグ] /api/logs が呼び出されました。")
    # print(f"[デバッグ] 受け取った全パラメータ: {request.args}")
    log_id = request.args.get('id')
    if log_id:
        conn = get_db_connection()
        log = conn.execute('SELECT al.*, s.grade, s.class, s.student_number, s.name FROM attendance_logs al LEFT JOIN students s ON al.system_id = s.system_id WHERE al.id = ?', (log_id,)).fetchone()
        conn.close()
        return jsonify({'logs': [dict(log)] if log else []})

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 100))
    sort_by = request.args.get('sort', 'id')
    sort_dir = request.args.get('dir', 'desc')
    
    filters = {
        'start': request.args.get('start'), 'end': request.args.get('end'),
        'name': request.args.get('name'), 'grade': request.args.get('grade'),
        'class': request.args.get('class'), 'number': request.args.get('number'),
    }
    # print(f"[デバッグ] 抽出したフィルター値: {filters}") 
    conn = get_db_connection()
    all_students_cursor = conn.execute('SELECT system_id, name, grade, class, student_number FROM students')
    all_students = [dict(row) for row in all_students_cursor.fetchall()]

    query = "SELECT al.id, al.system_id, al.entry_time, al.exit_time, s.name, s.grade, s.class, s.student_number FROM attendance_logs al LEFT JOIN students s ON al.system_id = s.system_id"
    count_query = "SELECT COUNT(al.id) FROM attendance_logs al LEFT JOIN students s ON al.system_id = s.system_id"
    conditions, params = [], []

    if filters['start']:
        start_raw = filters['start'] # 元の値を保持
        # print(f"[デバッグ] 受け取った開始日: {start_raw}") # デバッグ出力追加
        try:
            start_dt_naive = datetime.datetime.strptime(start_raw, '%Y-%m-%d')
            start_dt_jst = JST.localize(start_dt_naive.replace(hour=0, minute=0, second=0))
            start_utc_iso = start_dt_jst.astimezone(UTC).isoformat()
            conditions.append("al.entry_time >= ?")
            params.append(start_utc_iso)
            # print(f"[デバッグ] 変換後の開始日(UTC ISO): {start_utc_iso}") # デバッグ出力追加
        except ValueError as e:
            # エラー発生時の詳細ログ出力
            app.logger.error(f"【エラー】開始日の変換に失敗しました。入力値: '{start_raw}', エラー: {e}\n{traceback.format_exc()}")
        except Exception as e:
            # 予期せぬエラー発生時のログ出力
            app.logger.error(f"【予期せぬエラー】開始日の処理中に問題が発生しました。入力値: '{start_raw}', エラー: {e}\n{traceback.format_exc()}")

    if filters['end']:
        end_raw = filters['end'] # 元の値を保持
        # print(f"[デバッグ] 受け取った終了日: {end_raw}") # デバッグ出力追加
        try:
            end_dt_naive = datetime.datetime.strptime(end_raw, '%Y-%m-%d')
            end_dt_jst = JST.localize(end_dt_naive.replace(hour=23, minute=59, second=59, microsecond=999999))
            end_utc_iso = end_dt_jst.astimezone(UTC).isoformat()
            conditions.append("al.entry_time <= ?")
            params.append(end_utc_iso)
            # print(f"[デバッグ] 変換後の終了日(UTC ISO): {end_utc_iso}") # デバッグ出力追加
        except ValueError as e:
            # エラー発生時の詳細ログ出力
            app.logger.error(f"【エラー】終了日の変換に失敗しました。入力値: '{end_raw}', エラー: {e}\n{traceback.format_exc()}")
        except Exception as e:
            # 予期せぬエラー発生時のログ出力
            app.logger.error(f"【予期せぬエラー】終了日の処理中に問題が発生しました。入力値: '{end_raw}', エラー: {e}\n{traceback.format_exc()}")
    if filters['name']:
        conditions.append("s.name LIKE ?"); params.append(f"%{filters['name']}%")
    if filters['grade']:
        conditions.append("s.grade = ?"); params.append(filters['grade'])
    if filters['class']:
        conditions.append("s.class = ?"); params.append(filters['class'])
    if filters['number']:
        conditions.append("s.student_number = ?"); params.append(filters['number'])

    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)
        query += where_clause
        count_query += where_clause
        # print(f"[デバッグ] SQL条件: {where_clause}") # デバッグ出力追加
        # print(f"[デバッグ] SQLパラメータ: {params}") # デバッグ出力追加

    total = conn.execute(count_query, params).fetchone()[0]
    
    valid_sort_columns = ['id', 'entry_time', 'grade', 'class', 'student_number', 'name', 'exit_time', 'seat_number']
    if sort_by in valid_sort_columns and sort_dir in ['asc', 'desc']:
        sort_column = f"s.{sort_by}" if sort_by in ['grade', 'class', 'student_number', 'name'] else f"al.{sort_by}"
        query += f" ORDER BY {sort_column} {sort_dir.upper()}"

    offset = (page - 1) * per_page
    query += f" LIMIT {per_page} OFFSET {offset}"

    # クエリに al.seat_number を追加
    query = query.replace("al.entry_time, al.exit_time", "al.entry_time, al.exit_time, al.seat_number")
    logs = [dict(row) for row in conn.execute(query, params).fetchall()]
    conn.close()
    
    return jsonify({'logs': logs, 'total': total, 'students': all_students})

@app.route('/api/logs', methods=['POST'])
def add_log():
    data = request.json
    system_id, entry_time, exit_time, seat_number = data.get('system_id'), data.get('entry_time'), data.get('exit_time'), data.get('seat_number')
    if not system_id or not entry_time: return jsonify({'status': 'error', 'message': '生徒IDと入室時刻は必須です。'}), 400
    entry_time_utc, exit_time_utc = convert_to_utc(entry_time), convert_to_utc(exit_time)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO attendance_logs (system_id, entry_time, exit_time, seat_number) VALUES (?, ?, ?, ?)', (system_id, entry_time_utc, exit_time_utc, seat_number))
        new_log_id = cursor.lastrowid # 作成されたログのIDを取得

        # [監査ログ] 記録の追加
        app.logger.info(f"[監査ログ] 記録追加 - 実行者IP: {request.remote_addr}, 新規ID: {new_log_id}, 対象生徒ID: {system_id}, 入室: {entry_time_utc}, 退室: {exit_time_utc}, 座席: {seat_number}")

        is_today = datetime.datetime.fromisoformat(entry_time_utc).astimezone(JST).date() == datetime.datetime.now(JST).date()
        if exit_time_utc is None and is_today:
            # 該当生徒のステータスを「在室中」に更新する
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
        
        conn.commit()
        
        # 他の端末へ更新を通知
        announce_update()

        return jsonify({'status': 'success', 'message': '記録が正常に追加されました。'})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/logs/<int:log_id>', methods=['PUT'])
def update_log(log_id):
    data = request.json
    system_id, entry_time, exit_time, seat_number = data.get('system_id'), data.get('entry_time'), data.get('exit_time'), data.get('seat_number')
    if not system_id or not entry_time: return jsonify({'status': 'error', 'message': '生徒IDと入室時刻は必須です。'}), 400
    entry_time_utc, exit_time_utc = convert_to_utc(entry_time), convert_to_utc(exit_time)
    conn = get_db_connection()
    try:
        # --- 1. 既存のステータスを安全にリセット ---
        # このログIDが、いずれかの生徒の「現在の入室記録」として設定されている場合、
        # その生徒のステータスを一旦「退室済み」にリセットする。
        # これにより、ログの担当生徒が変更された場合でも、元の生徒のステータスが「入室中」のまま残るのを防ぐ。
        conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE current_log_id = ?', (log_id,))

        # --- 2. ログ記録を更新 ---
        conn.execute('UPDATE attendance_logs SET system_id = ?, entry_time = ?, exit_time = ?, seat_number = ? WHERE id = ?', (system_id, entry_time_utc, exit_time_utc, seat_number, log_id))

        # [監査ログ] 記録の編集
        app.logger.info(f"[監査ログ] 記録編集 - 実行者IP: {request.remote_addr}, 対象ログID: {log_id}, 変更内容: [生徒ID: {system_id}, 入室: {entry_time_utc}, 退室: {exit_time_utc}, 座席: {seat_number}]")

        # --- 3. 新しいステータスを条件付きで設定 ---
        # 更新後の入室日が今日であるかを確認
        is_today = datetime.datetime.fromisoformat(entry_time_utc).astimezone(JST).date() == datetime.datetime.now(JST).date()
        
        # もし更新後の記録が「未退室」かつ「今日の日付」ならば、
        if exit_time_utc is None and is_today:
            # 新しい担当生徒のステータスを「在室中」に更新する
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (log_id, system_id))
        
        conn.commit()
        
        # 他の端末へ更新を通知
        announce_update()

        return jsonify({'status': 'success', 'message': f'ID: {log_id} の記録が正常に更新されました。'})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/logs/<int:log_id>', methods=['DELETE'])
def delete_log(log_id):
    conn = get_db_connection()
    try:
        # [監査ログ] 記録の削除（削除実行前に記録）
        app.logger.info(f"[監査ログ] 記録削除 - 実行者IP: {request.remote_addr}, 対象ログID: {log_id}")

        conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE current_log_id = ?', (log_id,))
        conn.execute('DELETE FROM attendance_logs WHERE id = ?', (log_id,))
        conn.commit()
        
        # 他の端末へ更新を通知
        announce_update()

        return jsonify({'status': 'success', 'message': f'ID: {log_id} の記録が正常に削除されました。'})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

# --- SSE用エンドポイント ---
@app.route('/api/stream')
def stream():
    def event_stream():
        q = queue.Queue()
        sse_clients.append(q)
        try:
            while True:
                # キューからメッセージを取得（タイムアウト付きでブロッキング）
                # タイムアウトを設定することで、切断検知や定期的なKeep-Aliveが可能
                try:
                    msg = q.get(timeout=20)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    # タイムアウト時はコメント行を送って接続維持
                    yield ": keep-alive\n\n"
        except GeneratorExit:
            sse_clients.remove(q)
        except Exception:
            if q in sse_clients:
                sse_clients.remove(q)

    return Response(event_stream(), mimetype='text/event-stream')

# --- サーバーの起動 ---
if __name__ == '__main__':
    # 証明書ファイルのパスを定義
    # 【修正】読み込む証明書名を cert.crt に変更
    cert_path = os.path.join(os.path.dirname(__file__), '..', 'certs', 'cert.crt')
    key_path = os.path.join(os.path.dirname(__file__), '..', 'certs', 'key.pem')

    # 証明書と秘密鍵の両方が存在するかチェック
    if os.path.exists(cert_path) and os.path.exists(key_path):
        print("SSL証明書を検出しました。HTTPSでサーバーを起動します。")
        # HTTPSで起動
        app.run(host='0.0.0.0', port=8080, debug=True, ssl_context=(cert_path, key_path))
    else:
        print("SSL証明書が見つかりません。HTTPでサーバーを起動します。")
        # 通常のHTTPで起動
        app.run(host='0.0.0.0', port=8080, debug=True)