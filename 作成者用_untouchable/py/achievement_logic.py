import sqlite3
from datetime import datetime, timedelta
import pytz

# --- タイムゾーン定義 ---
JST = pytz.timezone('Asia/Tokyo')
UTC = pytz.utc

# (アチーブメントメッセージの定義は変更なし)
ACHIEVEMENT_MESSAGES = {
    'monthly_rank_1': {'student': "あなたは先月の首席利用者に選ばれました！！！", 'guardian': "{name}さんが、先月の自習室利用時間において全校生徒の中で1位となられましたことをお知らせします。", 'title': "首席利用者"},
    'monthly_rank_2': {'student': "あなたは先月の次席利用者に選ばれました！！", 'guardian': "{name}さんが、先月の自習室利用時間において全校生徒の中で2位となられましたことをお知らせします。", 'title': "次席利用者"},
    'monthly_rank_3': {'student': "あなたは先月、全校生徒の中で3番目に多く自習室を利用しました！", 'guardian': "{name}さんが、先月の自習室利用時間において全校生徒の中で3位となられましたことをお知らせします。", 'title': "三席利用者"},
    'consecutive_days': {'student': "{days}日連続利用です！その調子！", 'guardian': "{name}さんは{days}日連続で自習室を利用されています。"},
    'monthly_hours': {'student': "今月の利用時間が{hours}時間を突破！", 'guardian': "{name}さんの今月の自習室利用時間が{hours}時間を突破されました。"},
    'monthly_visits_10': {'student': "今月10回目の利用！目標に向かって着実に進んでいますね！", 'guardian': "{name}さんの今月の自習室利用回数が10回に到達しました。"},
    'monthly_visits_20': {'student': "すごい！今月20回目の利用です！努力の積み重ねが自信になりますね。", 'guardian': "{name}さんの今月の自習室利用回数が20回に到達しました。"},
    'monthly_visits_30': {'student': "今月30回目の利用! 君は努力の天才だ!!", 'guardian': "{name}さんの今月の自習室利用回数が30回に到達しました。"},
    'first_arrival': {'student': "一番乗り！今日も一日頑張りましょう！", 'guardian': "{name}さんは本日、一番乗りで自習室を利用されました。"},
    'weekend_warrior': {'student': "週末にも頑張っててすごい！", 'guardian': "{name}さんは週末の貴重な時間にも、自習室で学習されています。"},
    'late_finisher': {'student': "遅くまでお疲れ様！よく頑張りましたね！", 'guardian': "{name}さんは遅くまで学習に取り組んでおられました。"}
}

# --- ヘルパー関数 (ID名をsystem_idに変更) ---
def _has_achieved(conn, system_id, code, context=None):
    today = datetime.now(JST).date()
    if 'monthly' in code:
        start_of_month = today.replace(day=1)
        query = "SELECT 1 FROM achievements_tracker WHERE system_id = ? AND code = ? AND achieved_at >= ?"
        params = (system_id, code, start_of_month)
        if context:
            query += " AND context = ?"
            params += (context,)
    else:
        query = "SELECT 1 FROM achievements_tracker WHERE system_id = ? AND code = ? AND achieved_at = ?"
        params = (system_id, code, today)
        if context:
             query += " AND context = ?"
             params += (context,)
    return conn.execute(query, params).fetchone() is not None

def _record_achievement(conn, system_id, code, context=None):
    today = datetime.now(JST).date()
    conn.execute(
        "INSERT INTO achievements_tracker (system_id, code, achieved_at, context) VALUES (?, ?, ?, ?)",
        (system_id, code, today, context)
    )

def _update_student_title(conn, system_id, new_title):
    titles = {"首席利用者": 3, "次席利用者": 2, "三席利用者": 1}
    current_title_row = conn.execute("SELECT title FROM students WHERE system_id = ?", (system_id,)).fetchone()
    current_title = current_title_row['title'] if current_title_row else None
    current_rank = titles.get(current_title, 0)
    new_rank = titles.get(new_title, 0)
    if new_rank > current_rank:
        conn.execute("UPDATE students SET title = ? WHERE system_id = ?", (new_title, system_id))

# --- アチーブメント判定関数 (ロジック修正) ---

def _check_monthly_ranking(conn, system_id):
    now_jst = datetime.now(JST)
    monthly_check_context = f"rank_check_{now_jst.year}_{now_jst.month}"
    if _has_achieved(conn, system_id, 'monthly_rank_check', context=monthly_check_context):
        return None
    _record_achievement(conn, system_id, 'monthly_rank_check', context=monthly_check_context)
    
    today_jst = now_jst.date()
    last_month_end = today_jst.replace(day=1) - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    start_utc = JST.localize(datetime.combine(last_month_start, datetime.min.time())).astimezone(UTC)
    end_utc = JST.localize(datetime.combine(last_month_end, datetime.max.time())).astimezone(UTC)
    
    query = """
        SELECT system_id, SUM(strftime('%s', exit_time) - strftime('%s', entry_time)) as total_seconds
        FROM attendance_logs WHERE entry_time BETWEEN ? AND ? AND exit_time IS NOT NULL
        GROUP BY system_id ORDER BY total_seconds DESC LIMIT 3
    """
    ranking = conn.execute(query, (start_utc.isoformat(), end_utc.isoformat())).fetchall()
    
    for i, row in enumerate(ranking):
        if row['system_id'] == system_id:
            rank = i + 1
            code = f'monthly_rank_{rank}'
            achieved_context = f"rank_{last_month_start.year}_{last_month_start.month}"
            if not _has_achieved(conn, system_id, code, context=achieved_context):
                _record_achievement(conn, system_id, code, context=achieved_context)
                title = ACHIEVEMENT_MESSAGES[code]['title']
                _update_student_title(conn, system_id, title)
                return {'code': code, 'params': {}}
    return None

def _check_consecutive_days(conn, system_id):
    open_days_rows = conn.execute("SELECT DISTINCT strftime('%Y-%m-%d', entry_time, 'localtime') as open_day FROM attendance_logs ORDER BY open_day DESC").fetchall()
    open_days = [datetime.strptime(row['open_day'], '%Y-%m-%d').date() for row in open_days_rows]
    if not open_days: return None

    my_days_rows = conn.execute("SELECT DISTINCT strftime('%Y-%m-%d', entry_time, 'localtime') as my_day FROM attendance_logs WHERE system_id = ? ORDER BY my_day DESC", (system_id,)).fetchall()
    my_days = {datetime.strptime(row['my_day'], '%Y-%m-%d').date() for row in my_days_rows}

    today_jst = datetime.now(JST).date()
    if today_jst not in my_days:
        return None 

    consecutive_count = 0
    for day in open_days:
        if day in my_days:
            consecutive_count += 1
        else:
            break
            
    if consecutive_count >= 2:
        context = f"days_{consecutive_count}"
        if not _has_achieved(conn, system_id, 'consecutive_days', context=context):
            _record_achievement(conn, system_id, 'consecutive_days', context=context)
            return {'code': 'consecutive_days', 'params': {'days': consecutive_count}}
    return None

def _check_monthly_hours(conn, system_id, current_log_id):
    now_jst = datetime.now(JST)
    start_of_month_jst = now_jst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_month_utc = start_of_month_jst.astimezone(UTC)

    prev_logs_sum = conn.execute("""
        SELECT SUM(strftime('%s', exit_time) - strftime('%s', entry_time)) FROM attendance_logs
        WHERE system_id = ? AND entry_time >= ? AND exit_time IS NOT NULL AND id != ?
    """, (system_id, start_of_month_utc.isoformat(), current_log_id)).fetchone()[0] or 0
    
    current_log = conn.execute("SELECT entry_time, exit_time FROM attendance_logs WHERE id = ?", (current_log_id,)).fetchone()
    current_duration = (datetime.fromisoformat(current_log['exit_time']) - datetime.fromisoformat(current_log['entry_time'])).total_seconds()

    prev_hours = prev_logs_sum / 3600
    total_hours = (prev_logs_sum + current_duration) / 3600

    for hours_milestone in range(10, 101, 10):
        if prev_hours < hours_milestone <= total_hours:
            if not _has_achieved(conn, system_id, 'monthly_hours', context=str(hours_milestone)):
                _record_achievement(conn, system_id, 'monthly_hours', context=str(hours_milestone))
                return {'code': 'monthly_hours', 'params': {'hours': hours_milestone}}
    return None

def _check_monthly_visits(conn, system_id):
    now_jst = datetime.now(JST)
    start_of_month_jst = now_jst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_month_utc = start_of_month_jst.astimezone(UTC)
    
    count = conn.execute("SELECT COUNT(DISTINCT strftime('%Y-%m-%d', entry_time, 'localtime')) FROM attendance_logs WHERE system_id = ? AND entry_time >= ?", (system_id, start_of_month_utc.isoformat())).fetchone()[0]
    
    code_map = {10: 'monthly_visits_10', 20: 'monthly_visits_20', 30: 'monthly_visits_30'}
    if count in code_map:
        code = code_map[count]
        if not _has_achieved(conn, system_id, code, context=str(count)):
            _record_achievement(conn, system_id, code, context=str(count))
            return {'code': code, 'params': {'count': count}}
    return None

def _check_first_arrival(conn):
    start_of_day_jst = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day_jst.astimezone(UTC)
    count = conn.execute("SELECT COUNT(*) FROM attendance_logs WHERE entry_time >= ?", (start_of_day_utc.isoformat(),)).fetchone()[0]
    if count == 1:
        return {'code': 'first_arrival', 'params': {}}
    return None

def _check_weekend_warrior(conn, system_id):
    now_jst = datetime.now(JST)
    if now_jst.weekday() >= 5:
        if not _has_achieved(conn, system_id, 'weekend_warrior', context=str(now_jst.date())):
             _record_achievement(conn, system_id, 'weekend_warrior', context=str(now_jst.date()))
             return {'code': 'weekend_warrior', 'params': {}}
    return None

def _check_late_finisher(conn, system_id):
    if datetime.now(JST).hour >= 18:
        if not _has_achieved(conn, system_id, 'late_finisher', context=str(datetime.now(JST).date())):
             _record_achievement(conn, system_id, 'late_finisher', context=str(datetime.now(JST).date()))
             return {'code': 'late_finisher', 'params': {}}
    return None

def check_achievements(conn, system_id, event_type, current_log_id=None):
    student_info = conn.execute("SELECT name, title FROM students WHERE system_id = ?", (system_id,)).fetchone()
    student_name = student_info['name'] if student_info else ""
    achieved = None
    if event_type == 'check_in':
        achieved = _check_monthly_ranking(conn, system_id)
        if not achieved: achieved = _check_consecutive_days(conn, system_id)
        if not achieved: achieved = _check_monthly_visits(conn, system_id)
        if not achieved: achieved = _check_first_arrival(conn)
        if not achieved: achieved = _check_weekend_warrior(conn, system_id)
    elif event_type == 'check_out' and current_log_id:
        achieved = _check_monthly_hours(conn, system_id, current_log_id)
        if not achieved: achieved = _check_late_finisher(conn, system_id)
        
    if achieved:
        code, params = achieved['code'], achieved['params']
        messages = ACHIEVEMENT_MESSAGES[code]
        student_message = messages['student'].format(**params)
        guardian_message = messages['guardian'].format(name=student_name, **params) if 'guardian' in messages else None
        # デフォルトでは、現在の称号を返すように設定
        rank_to_return = student_info['title']
        
        # もし達成した実績が月間ランキングの場合、
        # DB更新後の最新の称号（メッセージ定義に含まれる'title'）を返すように上書きする
        if code.startswith('monthly_rank_'):
            rank_to_return = messages.get('title')
            
        return {'student_message': student_message, 'guardian_message': guardian_message, 'rank': rank_to_return}
    
    last_phrase_id_row = conn.execute("SELECT last_phrase_id FROM students WHERE system_id = ?", (system_id,)).fetchone()
    last_phrase_id = last_phrase_id_row['last_phrase_id'] if last_phrase_id_row and last_phrase_id_row['last_phrase_id'] is not None else 0
    phrase_count_row = conn.execute("SELECT COUNT(id) FROM phrases").fetchone()
    phrase_count = phrase_count_row[0] if phrase_count_row else 0
    if phrase_count > 0:
        next_phrase_id = (last_phrase_id % phrase_count) + 1
        phrase = conn.execute("SELECT * FROM phrases WHERE id = ?", (next_phrase_id,)).fetchone()
        if phrase:
            conn.execute("UPDATE students SET last_phrase_id = ? WHERE system_id = ?", (next_phrase_id, system_id))
            if phrase['category'] == '警句' and phrase['author']:
                student_message = f"「{phrase['text']}」 - {phrase['author']}"
                if phrase['lifespan']: student_message += f" {phrase['lifespan']}"
            else:
                student_message = phrase['text']
            return {'student_message': student_message, 'guardian_message': None, 'rank': student_info['title']}
    return {'student_message': None, 'guardian_message': None, 'rank': None}