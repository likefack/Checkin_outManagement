import os
import sqlite3
import datetime
import pytz 
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
        try:
            dt_naive = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S.%f')
            return JST.localize(dt_naive)
        except (ValueError, TypeError):
            try:
                dt_naive = datetime.datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
                return JST.localize(dt_naive)
            except ValueError:
                return None

def _handle_notifications(conn, system_id, event_type, log_id):
    student = conn.execute('SELECT name, guardian_email FROM students WHERE system_id = ?', (system_id,)).fetchone()
    if not student: return None
    ach_result = check_achievements(conn, system_id, event_type, log_id)
    app_name = os.getenv('APP_NAME')
    org_name = os.getenv('ORGANIZATION_NAME')
    sender_name = os.getenv('SENDER_NAME')
    log_entry = conn.execute('SELECT entry_time, exit_time FROM attendance_logs WHERE id = ?', (log_id,)).fetchone()
    guardian_message = ach_result.get('guardian_message', "") if ach_result else ""
    if event_type == 'check_in':
        entry_time_jst = parse_db_time_to_jst(log_entry['entry_time'])
        subject = f"【{app_name}】{student['name']}さんの入室通知"
        body = f"保護者様\n\nお世話になっております、{org_name}です。\n\n{student['name']}さんが{entry_time_jst.strftime('%H時%M分')}に入室されました。\n\n{guardian_message}\n\n今後ともよろしくお願いいたします。\n{sender_name}"
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
        body = f"保護者様\n\nお世話になっております、{org_name}です。\n\n{student['name']}さんが{exit_time_jst.strftime('%H時%M分')}に退室されました。\n{stay_text}\n\n{guardian_message}\n\n今後ともよろしくお願いいたします。\n{sender_name}"
        send_email_async(student['guardian_email'], subject, body)
    return ach_result

def auto_checkout_forgotten_students():
    print("退室忘れの記録をチェックしています...")
    conn = get_db_connection()
    try:
        start_of_today_jst = datetime.datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_today_utc = start_of_today_jst.astimezone(UTC)
        forgotten_logs = conn.execute("SELECT id, system_id, entry_time FROM attendance_logs WHERE exit_time IS NULL AND entry_time < ?", (start_of_today_utc.isoformat(),)).fetchall()
        if not forgotten_logs:
            print("退室忘れの記録はありませんでした。")
            return
        for log in forgotten_logs:
            entry_time_jst = parse_db_time_to_jst(log['entry_time'])
            estimated_exit_time_jst = entry_time_jst.replace(hour=22, minute=0, second=0)
            estimated_exit_time_utc = estimated_exit_time_jst.astimezone(UTC)
            conn.execute("UPDATE attendance_logs SET exit_time = ? WHERE id = ?", (estimated_exit_time_utc.isoformat(), log['id']))
            conn.execute("UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?", (log['system_id'],))
            print(f"ID:{log['system_id']} の退室忘れを自動処理しました。")
        conn.commit()
    except Exception as e:
        print(f"退室忘れの自動処理中にエラーが発生しました: {e}")
        conn.rollback()
    finally:
        conn.close()

# --- 起動時処理 ---
with app.app_context():
    database.init_db()
    auto_checkout_forgotten_students()

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
    start_of_day_jst = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day_jst.astimezone(UTC)
    conn = get_db_connection()
    students_cursor = conn.execute('SELECT system_id, name, grade, class, student_number, is_present, current_log_id FROM students ORDER BY grade, class, student_number')
    all_students = {s['system_id']: dict(s) for s in students_cursor.fetchall()}
    students_data_nested = {}
    for sid, student in all_students.items():
        grade, class_num, number = student['grade'], student['class'], student['student_number']
        student['is_present'] = student['is_present'] == 1
        if grade not in students_data_nested: students_data_nested[grade] = {}
        if class_num not in students_data_nested[grade]: students_data_nested[grade][class_num] = {}
        students_data_nested[grade][class_num][number] = student
    attendees_cursor = conn.execute('SELECT al.id AS log_id, s.system_id, al.seat_number, al.entry_time, al.exit_time, s.name, s.grade, s.class, s.student_number FROM attendance_logs al JOIN students s ON al.system_id = s.system_id WHERE al.entry_time >= ? ORDER BY al.entry_time ASC', (start_of_day_utc.isoformat(),))
    current_attendees = [dict(row) for row in attendees_cursor.fetchall()]
    conn.close()
    return jsonify({'students': students_data_nested, 'attendees': current_attendees})

@app.route('/api/check_in', methods=['POST'])
def check_in():
    data = request.json
    system_id, seat_number = data.get('system_id'), data.get('seat_number')
    if not system_id or not seat_number: return jsonify({'status': 'error', 'message': 'IDまたは座席番号がありません。'}), 400
    conn = get_db_connection()
    try:
        student = conn.execute('SELECT is_present, name FROM students WHERE system_id = ?', (system_id,)).fetchone()
        if student and student['is_present']: return jsonify({'status': 'error', 'message': f'{student["name"]}さんは既に入室済みです。'}), 409
        entry_time_utc = datetime.datetime.now(UTC)
        cursor = conn.execute('INSERT INTO attendance_logs (system_id, seat_number, entry_time) VALUES (?, ?, ?)', (system_id, seat_number, entry_time_utc.isoformat()))
        new_log_id = cursor.lastrowid
        conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
        ach_result = _handle_notifications(conn, system_id, 'check_in', new_log_id)
        conn.commit()
        return jsonify({'status': 'success', 'message': f'{student["name"]}さんが入室しました。', 'achievement': ach_result})
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
        student = conn.execute('SELECT name FROM students WHERE system_id = ?', (system_id,)).fetchone()
        log_id_to_update = log_id if log_id else conn.execute('SELECT current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()['current_log_id']
        if not log_id_to_update: return jsonify({'status': 'error', 'message': '有効な退室記録が見つかりません。'}), 409
        log_to_exit = conn.execute('SELECT exit_time FROM attendance_logs WHERE id = ?', (log_id_to_update,)).fetchone()
        if not log_to_exit: return jsonify({'status': 'error', 'message': f'内部エラー: ログID {log_id_to_update} が見つかりません。'}), 500
        if log_to_exit['exit_time']: return jsonify({'status': 'error', 'message': '既に退室処理済みです。'}), 409
        exit_time_utc = datetime.datetime.fromisoformat(exit_time_str).astimezone(UTC) if exit_time_str else datetime.datetime.now(UTC)
        conn.execute('UPDATE attendance_logs SET exit_time = ? WHERE id = ?', (exit_time_utc.isoformat(), log_id_to_update))
        conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
        ach_result = _handle_notifications(conn, system_id, 'check_out', log_id_to_update)
        conn.commit()
        return jsonify({'status': 'success', 'message': f'{student["name"]}さんが退室しました。', 'achievement': ach_result})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/qr_process', methods=['POST'])
def qr_process():
    data = request.json
    system_id = int(data.get('system_id'))
    if not system_id: return jsonify({'status': 'error', 'message': 'IDがありません。'}), 400
    conn = get_db_connection()
    try:
        student = conn.execute('SELECT is_present, name, current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()
        if not student: return jsonify({'status': 'error', 'message': '該当する生徒が見つかりません。'}), 404
        
        if student['is_present']:
            log_entry = conn.execute('SELECT entry_time FROM attendance_logs WHERE id = ?', (student['current_log_id'],)).fetchone()
            if log_entry and parse_db_time_to_jst(log_entry['entry_time']).date() < datetime.datetime.now(JST).date():
                forgotten_exit_time = parse_db_time_to_jst(log_entry['entry_time']).replace(hour=22, minute=0, second=0).astimezone(UTC)
                conn.execute('UPDATE attendance_logs SET exit_time = ? WHERE id = ?', (forgotten_exit_time.isoformat(), student['current_log_id']))
                conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
                print(f"ID:{system_id} の退室忘れを検出し、自動退室させました。")
                student = conn.execute('SELECT is_present, name, current_log_id FROM students WHERE system_id = ?', (system_id,)).fetchone()
        
        message, ach_result = "", None
        if student['is_present']:
            exit_time_utc = datetime.datetime.now(UTC)
            log_id_to_update = student['current_log_id']
            if not log_id_to_update: return jsonify({'status': 'error', 'message': '有効な退室記録が見つかりません。'}), 409
            conn.execute('UPDATE attendance_logs SET exit_time = ? WHERE id = ?', (exit_time_utc.isoformat(), log_id_to_update))
            conn.execute('UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id = ?', (system_id,))
            message = f'{student["name"]}さんが退室しました。'
            ach_result = _handle_notifications(conn, system_id, 'check_out', log_id_to_update)
        else:
            entry_time_utc = datetime.datetime.now(UTC)
            cursor = conn.execute('INSERT INTO attendance_logs (system_id, entry_time) VALUES (?, ?)', (system_id, entry_time_utc.isoformat()))
            new_log_id = cursor.lastrowid
            conn.execute('UPDATE students SET is_present = 1, current_log_id = ? WHERE system_id = ?', (new_log_id, system_id))
            message = f'{student["name"]}さんが入室しました。'
            ach_result = _handle_notifications(conn, system_id, 'check_in', new_log_id)
        conn.commit()
        return jsonify({'status': 'success', 'message': message, 'achievement': ach_result})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/exit_all', methods=['POST'])
def exit_all():
    conn = get_db_connection()
    try:
        present_students = conn.execute('SELECT current_log_id, system_id FROM students WHERE is_present = 1').fetchall()
        if not present_students: return jsonify({'status': 'success', 'message': '退室させる生徒がいません。'})
        exit_time_utc = datetime.datetime.now(UTC)
        log_ids = [s['current_log_id'] for s in present_students if s['current_log_id']]
        if not log_ids: return jsonify({'status': 'info', 'message': '退室記録対象の生徒がいません。'})
        system_ids = [s['system_id'] for s in present_students]
        conn.execute(f'UPDATE attendance_logs SET exit_time = ? WHERE id IN ({",".join("?"*len(log_ids))})', [exit_time_utc.isoformat()] + log_ids)
        conn.execute(f'UPDATE students SET is_present = 0, current_log_id = NULL WHERE system_id IN ({",".join("?"*len(system_ids))})', system_ids)
        for student_info in present_students:
             if student_info['current_log_id']:
                _handle_notifications(conn, student_info['system_id'], 'check_out', student_info['current_log_id'])
        conn.commit()
        return jsonify({'status': 'success', 'message': f'{len(present_students)}名の生徒を全員退室させました。'})
    except Exception as e:
        conn.rollback(); return jsonify({'status': 'error', 'message': f'データベースエラー: {e}'}), 500
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

# --- サーバーの起動 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)

