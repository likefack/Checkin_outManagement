import pandas as pd
import sqlite3
import os
import datetime
import pytz

# --- 定数・ヘルパー関数 ---
JST = pytz.timezone('Asia/Tokyo')
UTC = pytz.utc
GRADE_MAP = {1: '中1', 2: '中2', 3: '中3', 4: '高1', 5: '高2', 6: '高3'}
DOW_MAP = {'Monday': '月', 'Tuesday': '火', 'Wednesday': '水', 'Thursday': '木', 'Friday': '金', 'Saturday': '土', 'Sunday': '日'}

def create_report(db_path, start_date_str, end_date_str):
    """
    利用者別サマリーに「初回利用日」「最多入室時間帯」を追加してレポートを生成する。
    """
    try:
        # --- 期間設定とファイルパスの準備 ---
        start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        
        report_dir_name = os.getenv('REPORT_OUTPUT_DIR', 'logs')
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '管理者用_touchable')
        report_dir = os.path.join(base_dir, report_dir_name)
        
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)

        file_name = f"集計レポート_{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}.xlsx"
        file_path = os.path.join(report_dir, file_name)

        # --- データベースからデータを取得 ---
        conn = sqlite3.connect(db_path)
        start_utc_iso = JST.localize(datetime.datetime.combine(start_date, datetime.time.min)).astimezone(UTC).isoformat()
        end_utc_iso = JST.localize(datetime.datetime.combine(end_date, datetime.time.max)).astimezone(UTC).isoformat()
        
        query = f"""
        SELECT al.system_id, s.grade, s.class, s.student_number, s.name, al.entry_time, al.exit_time
        FROM attendance_logs al JOIN students s ON al.system_id = s.system_id
        WHERE al.entry_time BETWEEN '{start_utc_iso}' AND '{end_utc_iso}' AND al.exit_time IS NOT NULL
        """
        df = pd.read_sql_query(query, conn)
        
        students_master = pd.read_sql_query("SELECT grade, class FROM students", conn)
        conn.close()

        if df.empty:
            return "No data", f"{start_date_str}から{end_date_str}の期間にデータはありませんでした。"

        # --- データ前処理 ---
        df['entry_time'] = pd.to_datetime(df['entry_time'], format='ISO8601').dt.tz_convert(JST)
        df['exit_time'] = pd.to_datetime(df['exit_time'], format='ISO8601').dt.tz_convert(JST)
        df['grade_jp'] = df['grade'].map(GRADE_MAP)
        df['ID'] = 'ID_' + df['system_id'].astype(str)
        df['stay_minutes'] = round((df['exit_time'] - df['entry_time']).dt.total_seconds() / 60, 1)
        df['stay_hours'] = round(df['stay_minutes'] / 60, 1)
        df['date'] = df['entry_time'].dt.date
        df['day_of_week_jp'] = df['entry_time'].dt.day_name().map(DOW_MAP)
        df['entry_hour'] = df['entry_time'].dt.hour
        df['entry_hour_jp'] = df['entry_hour'].astype(str) + '時台'

        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            
            # --- シート1: 学年組別サマリー ---
            all_grades_jp = sorted(students_master['grade'].map(GRADE_MAP).unique(), key=lambda x: list(GRADE_MAP.values()).index(x))
            all_classes = sorted(students_master['class'].unique())
            df_summary_class = pd.crosstab(df['grade_jp'], df['class'], values=df['system_id'], aggfunc='nunique').fillna(0).astype(int)
            df_summary_class = df_summary_class.reindex(index=all_grades_jp, columns=all_classes, fill_value=0)
            df_summary_class['合計'] = df_summary_class.sum(axis=1)
            df_summary_class.loc['合計'] = df_summary_class.sum()
            df_summary_class.index.name = "学年"
            df_summary_class.columns.name = "組"
            df_summary_class.to_excel(writer, sheet_name='学年組別サマリー')

            # --- シート2: 滞在記録(元データ) ---
            df_raw = df[['ID', 'grade_jp', 'class', 'student_number', 'name', 'entry_time', 'exit_time', 
                         'stay_minutes', 'stay_hours', 'entry_hour_jp', 'date', 'day_of_week_jp']].copy()
            df_raw.rename(columns={
                'ID': 'ID', 'grade_jp': '学年', 'class': '組', 'student_number': '番号', 'name': '氏名',
                'entry_time': '入室時刻', 'exit_time': '退室時刻', 'stay_minutes': '滞在時間(分)',
                'stay_hours': '滞在時間(時間)', 'entry_hour_jp': '入室時間帯', 'date': '日付', 'day_of_week_jp': '曜日'
            }, inplace=True)
            df_raw['入室時刻'] = df_raw['入室時刻'].dt.tz_localize(None).dt.round('s')
            df_raw['退室時刻'] = df_raw['退室時刻'].dt.tz_localize(None).dt.round('s')
            df_raw.to_excel(writer, sheet_name='滞在記録(元データ)', index=False)

            # --- シート3: 日別サマリー ---
            daily_agg = df.groupby('date').agg(
                day_of_week_jp=('day_of_week_jp', 'first'),
                total_checkins=('system_id', 'count'),
                unique_users=('system_id', 'nunique'),
                avg_stay_minutes=('stay_minutes', 'mean')
            ).round(1)
            daily_grade_pivot = pd.crosstab(df['date'], df['grade_jp'], values=df['system_id'], aggfunc='nunique').reindex(columns=all_grades_jp, fill_value=0)
            df_daily = pd.concat([daily_agg['day_of_week_jp'], daily_agg[['total_checkins', 'unique_users']], daily_grade_pivot, daily_agg['avg_stay_minutes']], axis=1)
            df_daily.rename(columns={'day_of_week_jp':'曜日', 'total_checkins':'総入室回数', 'unique_users':'ユニーク入室者数', 'avg_stay_minutes':'平均滞在時間(分)'}, inplace=True)
            df_daily.index.name = '日付'
            df_daily.to_excel(writer, sheet_name='日別サマリー')

            # --- シート4: 利用者別サマリー (★★★ 機能拡張 ★★★) ---
            total_days = (end_date - start_date).days + 1
            num_weeks = total_days / 7 if total_days >= 7 else 1

            df_user_summary = df.groupby(['grade_jp', 'class', 'student_number', 'name']).agg(
                total_checkins=('system_id', 'count'),
                total_stay_hours=('stay_hours', 'sum'),
                avg_stay_minutes=('stay_minutes', 'mean'),
                most_used_dow=('day_of_week_jp', lambda x: x.mode()[0]),
                first_use_date=('date', 'min'), # ★★★ 追加: 初回利用日 ★★★
                most_used_hour=('entry_hour_jp', lambda x: x.mode()[0]) # ★★★ 追加: 最多入室時間帯 ★★★
            ).reset_index()
            
            df_user_summary['weekly_avg_checkins'] = round(df_user_summary['total_checkins'] / num_weeks, 1)

            df_user_summary.rename(columns={
                'grade_jp': '学年', 'class': '組', 'student_number': '番号', 'name': '氏名',
                'total_checkins': '総利用回数', 'total_stay_hours': '総滞在時間(時間)',
                'avg_stay_minutes': '平均滞在時間(分)', 'most_used_dow': '最多利用曜日',
                'weekly_avg_checkins': '週平均利用回数',
                'first_use_date': '初回利用日', 'most_used_hour': '最多入室時間帯'
            }, inplace=True)
            
            # カラムの順序を最終調整
            df_user_summary = df_user_summary[[
                '学年', '組', '番号', '氏名', '総利用回数', '週平均利用回数', '総滞在時間(時間)', 
                '平均滞在時間(分)', '最多利用曜日', '最多入室時間帯', '初回利用日'
            ]]
            df_user_summary.to_excel(writer, sheet_name='利用者別サマリー', index=False)
            
            # --- シート5: 時間帯別サマリー ---
            hourly_pivot = pd.crosstab(df['entry_hour_jp'], df['grade_jp']).reindex(columns=all_grades_jp, fill_value=0)
            all_hours_jp = [f"{h}時台" for h in range(24)]
            hourly_pivot = hourly_pivot.reindex(index=all_hours_jp, fill_value=0)
            hourly_pivot['合計'] = hourly_pivot.sum(axis=1)
            hourly_pivot.loc['合計'] = hourly_pivot.sum()
            hourly_pivot.index.name = '時間帯'
            hourly_pivot.to_excel(writer, sheet_name='時間帯別サマリー')

        return file_path, f"レポートが正常に作成されました: {os.path.basename(file_path)}"
        
    except Exception as e:
        error_message = f"レポート作成中にエラーが発生しました: {e}"
        print(error_message)
        return None, error_message