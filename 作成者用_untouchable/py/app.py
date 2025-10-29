import os
import sqlite3
import datetime
import pytz 
import traceback
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import database 
from report_generator import create_report
from achievement_logic import check_achievements
from email_sender import send_email_async

# --- アプリケーションの初期設定 ---
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '管理者用_touchable', '.env')
load_dotenv(dotenv_path)

app = Flask(__name__, 
            template_folder=os.path.join(os.path.dirname(__file__), '..', 'templates'),
            static_folder=os.path.join(os.path.dirname(__file__), '..', 'static'))

# --- タイムゾーン定義 ---
JST = pytz.timezone('Asia/Tokyo')
UTC = pytz.utc

# --- データベース接続 ---
def get_db_connection():
    conn = sqlite3.connect(database.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

# --- ヘルパー関数 ---
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
            print(f"ID:{system_id} の前日以前の入室記録を検出。入退ステータスをリセットします。")
            conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
            conn.commit()
            print(f"ID:{system_id} の入退ステータスをリセットしました。")
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

    if event_type == 'check_in':
        entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
        subject = f"【{app_name}】{student['name']}さんの入室通知"
        body = f"{student['name']}の保護者様\n\nお世話になっております、{org_name}の{sender_name}です。\n\n{student['name']}さんが{entry_time_jst.strftime('%H時%M分')}に入室されたことをお知らせします。\n{guardian_message}\n\n今後ともよろしくお願いいたします。\n{sender_name}"
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
        body = f"{student['name']}の保護者様\n\nお世話になっております、{org_name}の{sender_name}です。\n\n{student['name']}さんが{exit_time_jst.strftime('%H時%M分')}に退室されたことをお知らせします。\n{stay_text}\n{guardian_message}\n\n今後ともよろしくお願いいたします。\n{sender_name}"
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
    if mode == 'edit':
        return render_template('edit.html', app_name=app_name)
    else:
        max_seat_number = int(os.getenv('MAX_SEAT_NUMBER', 72))
        return render_template('index.html', mode=mode, app_name=app_name, max_seat_number=max_seat_number)

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
        # ▼▼▼ 修正点: is_present の状態を日付でチェックして上書き ▼▼▼
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
                        print(f"ID:{student['system_id']} の前日以前の入室記録を検出(initial_data)。リセット対象に追加。")
            # else: is_present が 0 または log_id がない場合は is_present_today_for_frontend は False のまま

            # フロントエンドに返す is_present を設定
            student['is_present'] = is_present_today_for_frontend

            # ネスト構造に格納 (ここは変更なし)
            grade, class_num, number = student['grade'], student['class'], student['student_number']
            if grade not in students_data_nested: students_data_nested[grade] = {}
            if class_num not in students_data_nested[grade]: students_data_nested[grade][class_num] = {}
            students_data_nested[grade][class_num][number] = student

        # ▼▼▼ 修正点: リセット対象の生徒のDBステータスを更新 ▼▼▼
        if ids_to_reset:
            # プレースホルダーを使って安全にUPDATE文を実行
            placeholders = ','.join('?' * len(ids_to_reset))
            conn.execute(f'UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id IN ({placeholders})', ids_to_reset)
            conn.commit()
            print(f"リセット対象 {len(ids_to_reset)} 件のステータスをDBでリセットしました。")

        # 今日の入退室記録を取得 (ここは変更なし)
        attendees_cursor = conn.execute('SELECT al.id AS log_id, s.system_id, al.seat_number, al.entry_time, al.exit_time, s.name, s.grade, s.class, s.student_number FROM attendance_logs al JOIN students s ON al.system_id = s.system_id WHERE al.entry_time >= ? ORDER BY al.entry_time ASC', (start_of_day_utc.isoformat(),))
        current_attendees = [dict(row) for row in attendees_cursor.fetchall()]

        return jsonify({'students': students_data_nested, 'attendees': current_attendees})

    except Exception as e:
        print(f"Error in get_initial_data: {e}")
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
        # ▼▼▼ 修正点1: まず現在の生徒情報を取得 ▼▼▼
        student = conn.execute('SELECT is_present, name, title, current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()
        if not student:
             return jsonify({'status': 'error', 'message': '該当する生徒が見つかりません。'}), 404 # 生徒が見つからない場合のエラーを追加

        is_present_today = False
        # ▼▼▼ 修正点2: is_present が True の場合、それが今日の記録か確認 ▼▼▼
        if student['is_present'] and student['current_log_id']:
            log_entry = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (student['current_log_id'],)).fetchone()
            if log_entry:
                entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
                if entry_time_jst and entry_time_jst.date() == datetime.datetime.now(JST).date():
                    is_present_today = True
                else:
                    # 前日以前の記録ならリセット
                    print(f"ID:{system_id} の前日以前の入室記録を検出(check_in)。ステータスをリセットします。")
                    conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
                    print(f"ID:{system_id} のステータスをリセットしました。")
                    # student 変数はここでは再取得不要（入室処理に進むため）

        # ▼▼▼ 修正点3: 今日の記録があるかで分岐 ▼▼▼
        if is_present_today:
            # 既に今日入室済みの場合 (手動入室ではエラーとする)
            conn.close() # エラーを返す前にコネクションを閉じる
            return jsonify({'status': 'error', 'message': f'{student["name"]}さんは本日既に入室済みです。'}), 409
        else:
            # --- 入室処理 ---
            entry_time_utc = datetime.datetime.now(UTC)
            cursor = conn.execute('INSERT INTO attendance_logs (system_id, seat_number, entry_time) VALUES (?, ?, ?)', (system_id, seat_number, entry_time_utc.isoformat()))
            new_log_id = cursor.lastrowid
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
        
        # 実績判定処理を呼び出す
        ach_result = _handle_notifications(conn, system_id, 'check_in', new_log_id)
        
        # ▼▼▼ 変更ここから ▼▼▼
        # 最終的に通知に使うランクを決定する。
        # もし実績（ach_result）の中に新しいランク情報があればそれを優先し、
        # なければ最初にDBから読み込んだランク情報を使う。
        final_rank = ach_result.get('rank') if ach_result and ach_result.get('rank') else student['title']
        
        conn.commit()

        # `rank`キーに、上で決定した最新のランク情報(final_rank)を渡す
        return jsonify({'status': 'success', 'message': f'{student["name"]}さんが入室しました。', 'rank': final_rank, 'achievement': ach_result})
        # ▲▲▲ 変更ここまで ▲▲▲

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
        if log_to_exit['exit_time']: return jsonify({'status': 'error', 'message': '既に退室処理済みです。'}), 409
        exit_time_utc = datetime.datetime.fromisoformat(exit_time_str).astimezone(UTC) if exit_time_str else datetime.datetime.now(UTC)
        conn.execute('UPDATE attendance_logs SET exit_time = ? WHERE id = ?', (exit_time_utc.isoformat(), log_id_to_update))
        conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
        
        # 実績判定処理を呼び出す
        ach_result = _handle_notifications(conn, system_id, 'check_out', log_id_to_update)
        
        # ▼▼▼ 変更ここから ▼▼▼
        # 最終的に通知に使うランクを決定する
        final_rank = ach_result.get('rank') if ach_result and ach_result.get('rank') else student['title']
        
        conn.commit()

        # `rank`キーに、最新のランク情報(final_rank)を渡す
        return jsonify({'status': 'success', 'message': f'{student["name"]}さんが退室しました。', 'rank': final_rank, 'achievement': ach_result})
        # ▲▲▲ 変更ここまで ▲▲▲

    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/qr_process', methods=['POST'])
def qr_process():
    data = request.json
    try: # system_id が数値でない場合のエラーを捕捉
        system_id = int(data.get('system_id'))
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': '無効なID形式です。'}), 400

    if not system_id: return jsonify({'status': 'error', 'message': 'IDがありません。'}), 400
    conn = get_db_connection()
    try:
        # ▼▼▼ 修正点1: まず生徒情報を取得 ▼▼▼
        student = conn.execute('SELECT is_present, name, title, current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()
        if not student: return jsonify({'status': 'error', 'message': '該当する生徒が見つかりません。'}), 404

        is_present_today = False
        # ▼▼▼ 修正点2: is_present が True の場合、それが今日の記録か確認 ▼▼▼
        if student['is_present'] and student['current_log_id']:
            log_entry = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (student['current_log_id'],)).fetchone()
            if log_entry:
                entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
                # entry_timeが取得でき、かつそれが今日の日付であれば True
                if entry_time_jst and entry_time_jst.date() == datetime.datetime.now(JST).date():
                    is_present_today = True
                else:
                    # ★★★ 前日以前の記録なら、ここで強制的にリセット ★★★
                    print(f"ID:{system_id} の前日以前の入室記録を検出(qr_process)。ステータスをリセットします。")
                    conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
                    print(f"ID:{system_id} のステータスをリセットしました。")
                    # is_present_today は False のまま（=入室処理へ）

        message, ach_result = "", None
        # ▼▼▼ 修正点3: 「今日」入室しているかどうかで分岐 ▼▼▼
        if is_present_today:
            # --- 退室処理 ---
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
        else:
            # --- 入室処理 ---
            entry_time_utc = datetime.datetime.now(UTC)
            cursor = conn.execute('INSERT INTO attendance_logs (system_id, entry_time) VALUES (?, ?)', (system_id, entry_time_utc.isoformat()))
            new_log_id = cursor.lastrowid
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
            message = f'{student["name"]}さんが自習室に入室しました。'
            ach_result = _handle_notifications(conn, system_id, 'check_in', new_log_id)

        # 最新の称号情報を決定（変更なし）
        # student変数はリセット時に再取得しないため、必要ならここで再取得
        current_student_state = conn.execute('SELECT title FROM students WHERE system_id = ?', (system_id,)).fetchone()
        current_title = current_student_state['title'] if current_student_state else None
        final_rank = ach_result.get('rank') if ach_result and ach_result.get('rank') else current_title

        conn.commit()

        return jsonify({'status': 'success', 'message': message, 'rank': final_rank, 'achievement': ach_result})

    except Exception as e:
        conn.rollback()
        print(f"Error in qr_process: {e}") # エラーログを追加
        return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        # finallyブロックで確実にコネクションを閉じる
        if conn:
            conn.close()

# ★★★ 修正: `exit_all`関数を新しい仕様に合わせて修正 ★★★
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
        return jsonify({'status': 'success', 'message': f'{len(present_students)}名の生徒を全員退室させました。'})
    except Exception as e:
        conn.rollback()
        print(f"Error in exit_all: {e}")
        return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()
        
@app.route('/api/create_report', methods=['POST'])
def handle_create_report():
    data = request.json
    start_date, end_date = data.get('start_date'), data.get('end_date')
    if not start_date or not end_date: return jsonify({'status': 'error', 'message': '期間が指定されていません。'}), 400
    file_path, message = create_report(database.DB_PATH, start_date, end_date)
    if file_path:
        if file_path == "No data": return jsonify({'status': 'info', 'message': message})
        return jsonify({'status': 'success', 'message': message})
    else:
        return jsonify({'status': 'error', 'message': message}), 500

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
        # print(f"[デバッグ] 受け取った開始日: {start_raw}") # ★デバッグ出力追加
        try:
            start_dt_naive = datetime.datetime.strptime(start_raw, '%Y-%m-%d')
            start_dt_jst = JST.localize(start_dt_naive.replace(hour=0, minute=0, second=0))
            start_utc_iso = start_dt_jst.astimezone(UTC).isoformat()
            conditions.append("al.entry_time >= ?")
            params.append(start_utc_iso)
            # print(f"[デバッグ] 変換後の開始日(UTC ISO): {start_utc_iso}") # ★デバッグ出力追加
        except ValueError as e:
            # ★エラー発生時の詳細ログ出力
            print(f"【エラー】開始日の変換に失敗しました。入力値: '{start_raw}', エラー: {e}")
            print(traceback.format_exc()) # ★エラーの詳細な発生箇所を出力
        except Exception as e:
            # ★予期せぬエラー発生時のログ出力
            print(f"【予期せぬエラー】開始日の処理中に問題が発生しました。入力値: '{start_raw}', エラー: {e}")
            print(traceback.format_exc()) # ★エラーの詳細な発生箇所を出力

    if filters['end']:
        end_raw = filters['end'] # 元の値を保持
        # print(f"[デバッグ] 受け取った終了日: {end_raw}") # ★デバッグ出力追加
        try:
            end_dt_naive = datetime.datetime.strptime(end_raw, '%Y-%m-%d')
            end_dt_jst = JST.localize(end_dt_naive.replace(hour=23, minute=59, second=59, microsecond=999999))
            end_utc_iso = end_dt_jst.astimezone(UTC).isoformat()
            conditions.append("al.entry_time <= ?")
            params.append(end_utc_iso)
            # print(f"[デバッグ] 変換後の終了日(UTC ISO): {end_utc_iso}") # ★デバッグ出力追加
        except ValueError as e:
            # ★エラー発生時の詳細ログ出力
            print(f"【エラー】終了日の変換に失敗しました。入力値: '{end_raw}', エラー: {e}")
            print(traceback.format_exc()) # ★エラーの詳細な発生箇所を出力
        except Exception as e:
            # ★予期せぬエラー発生時のログ出力
            print(f"【予期せぬエラー】終了日の処理中に問題が発生しました。入力値: '{end_raw}', エラー: {e}")
            print(traceback.format_exc()) # ★エラーの詳細な発生箇所を出力
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
        # print(f"[デバッグ] SQL条件: {where_clause}") # ★デバッグ出力追加
        # print(f"[デバッグ] SQLパラメータ: {params}") # ★デバッグ出力追加

    total = conn.execute(count_query, params).fetchone()[0]
    
    valid_sort_columns = ['id', 'entry_time', 'grade', 'class', 'student_number', 'name', 'exit_time']
    if sort_by in valid_sort_columns and sort_dir in ['asc', 'desc']:
        sort_column = f"s.{sort_by}" if sort_by in ['grade', 'class', 'student_number', 'name'] else f"al.{sort_by}"
        query += f" ORDER BY {sort_column} {sort_dir.upper()}"

    offset = (page - 1) * per_page
    query += f" LIMIT {per_page} OFFSET {offset}"

    logs = [dict(row) for row in conn.execute(query, params).fetchall()]
    conn.close()
    
    return jsonify({'logs': logs, 'total': total, 'students': all_students})

@app.route('/api/logs', methods=['POST'])
def add_log():
    data = request.json
    system_id, entry_time, exit_time = data.get('system_id'), data.get('entry_time'), data.get('exit_time')
    if not system_id or not entry_time: return jsonify({'status': 'error', 'message': '生徒IDと入室時刻は必須です。'}), 400
    entry_time_utc, exit_time_utc = convert_to_utc(entry_time), convert_to_utc(exit_time)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO attendance_logs (system_id, entry_time, exit_time) VALUES (?, ?, ?)', (system_id, entry_time_utc, exit_time_utc))
        new_log_id = cursor.lastrowid # 作成されたログのIDを取得

        is_today = datetime.datetime.fromisoformat(entry_time_utc).astimezone(JST).date() == datetime.datetime.now(JST).date()
        if exit_time_utc is None and is_today:
            # 該当生徒のステータスを「在室中」に更新する
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
        
        conn.commit()
        return jsonify({'status': 'success', 'message': '記録が正常に追加されました。'})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/logs/<int:log_id>', methods=['PUT'])
def update_log(log_id):
    data = request.json
    system_id, entry_time, exit_time = data.get('system_id'), data.get('entry_time'), data.get('exit_time')
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
        conn.execute('UPDATE attendance_logs SET system_id = ?, entry_time = ?, exit_time = ? WHERE id = ?', (system_id, entry_time_utc, exit_time_utc, log_id))

        # --- 3. 新しいステータスを条件付きで設定 ---
        # 更新後の入室日が今日であるかを確認
        is_today = datetime.datetime.fromisoformat(entry_time_utc).astimezone(JST).date() == datetime.datetime.now(JST).date()
        
        # もし更新後の記録が「未退室」かつ「今日の日付」ならば、
        if exit_time_utc is None and is_today:
            # 新しい担当生徒のステータスを「在室中」に更新する
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (log_id, system_id))
        
        conn.commit()
        return jsonify({'status': 'success', 'message': f'ID: {log_id} の記録が正常に更新されました。'})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/logs/<int:log_id>', methods=['DELETE'])
def delete_log(log_id):
    conn = get_db_connection()
    try:
        conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE current_log_id = ?', (log_id,))
        conn.execute('DELETE FROM attendance_logs WHERE id = ?', (log_id,))
        conn.commit()
        return jsonify({'status': 'success', 'message': f'ID: {log_id} の記録が正常に削除されました。'})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

# --- サーバーの起動 ---
if __name__ == '__main__':
    # 証明書ファイルのパスを定義
    cert_path = os.path.join(os.path.dirname(__file__), '..', 'certs', 'cert.pem')
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