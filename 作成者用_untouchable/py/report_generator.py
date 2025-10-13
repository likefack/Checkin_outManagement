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
    「日別サマリー」シート作成時のKeyErrorを修正。
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
        WHERE al.entry_time BETWEEN '{start_utc_iso}' AND '{end_utc_iso}'
        """
        df = pd.read_sql_query(query, conn)
        
        students_master = pd.read_sql_query("SELECT grade, class FROM students", conn)
        conn.close()

        if df.empty:
            return "No data", f"{start_date_str}から{end_date_str}の期間にデータはありませんでした。"

        # --- データ前処理 ---
        df['entry_time'] = pd.to_datetime(df['entry_time'], format='ISO8601').dt.tz_convert(JST)
        df['exit_time'] = pd.to_datetime(df['exit_time'], format='ISO8601', errors='coerce').dt.tz_convert(JST)

        completed_logs = df.dropna(subset=['exit_time'])
        avg_stay_minutes_map = completed_logs.groupby('system_id').apply(
            lambda x: (x['exit_time'] - x['entry_time']).dt.total_seconds().mean() / 60
        ).round(1)

        forgot_exit_mask = df['exit_time'].isnull()
        df['stay_minutes_imputed'] = df['system_id'].map(avg_stay_minutes_map).fillna(120)
        
        df['stay_minutes'] = round((df['exit_time'] - df['entry_time']).dt.total_seconds() / 60, 1)
        df.loc[forgot_exit_mask, 'stay_minutes'] = df.loc[forgot_exit_mask, 'stay_minutes_imputed']
        df.loc[forgot_exit_mask, 'exit_time'] = df.loc[forgot_exit_mask, 'entry_time'] + pd.to_timedelta(df.loc[forgot_exit_mask, 'stay_minutes'], unit='m')
        
        df['grade_jp'] = df['grade'].map(GRADE_MAP)
        df['ID'] = 'ID_' + df['system_id'].astype(str)
        df['stay_hours'] = round(df['stay_minutes'] / 60, 1)
        df['date'] = df['entry_time'].dt.date # 'date'列をここで作成
        df['day_of_week_jp'] = df['entry_time'].dt.day_name().map(DOW_MAP)
        df['entry_hour'] = df['entry_time'].dt.hour
        df['entry_hour_jp'] = df['entry_hour'].astype(str) + '時台'

        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            
            # --- シート1: 日別ユニーク学年組別サマリー ---
            # 存在するすべての学年・組のリストをマスターから取得
            all_grades_jp = sorted(students_master['grade'].map(GRADE_MAP).unique(), key=lambda x: list(GRADE_MAP.values()).index(x))
            all_classes = sorted(students_master['class'].unique())

            # 1. 日付と生徒IDで重複を削除し、「日ごとのユニーク利用者」データを作成
            df_daily_unique_users = df.drop_duplicates(subset=['date', 'system_id'])

            # 2. 上記の「日ごとユニーク」データを使ってクロス集計（=延べ人数をカウント）
            df_summary_class = pd.crosstab(df_daily_unique_users['grade_jp'], df_daily_unique_users['class'])
            
            # 3. 欠損している学年・組を0埋めして合計を計算（ここは変更なし）
            df_summary_class = df_summary_class.reindex(index=all_grades_jp, columns=all_classes, fill_value=0)
            df_summary_class['合計'] = df_summary_class.sum(axis=1)
            df_summary_class.loc['合計'] = df_summary_class.sum()
            df_summary_class.index.name = "学年"
            df_summary_class.columns.name = "組"
            
            # 4. シート名を分かりやすいように変更してExcelに出力
            df_summary_class.to_excel(writer, sheet_name='日別ユニーク学年組別サマリー')
            # --- シート2: 滞在記録(元データ) ---
            df_raw = df[['ID', 'grade_jp', 'class', 'student_number', 'name', 'entry_time', 'exit_time', 
                         'stay_minutes', 'day_of_week_jp', 'entry_hour_jp']].copy()
            df_raw['入室日'] = df_raw['entry_time'].dt.strftime('%Y-%m-%d')
            df_raw['入室時刻'] = df_raw['entry_time'].dt.strftime('%H:%M:%S')
            df_raw['退室日'] = df_raw['exit_time'].dt.strftime('%Y-%m-%d')
            df_raw['退室時刻'] = df_raw['exit_time'].dt.strftime('%H:%M:%S')
            df_raw.loc[forgot_exit_mask, '退室時刻'] += '（推定）'
            df_raw.rename(columns={
                'ID': 'ID', 'grade_jp': '学年', 'class': '組', 'student_number': '番号', 'name': '氏名',
                'stay_minutes': '滞在時間(分)', 'day_of_week_jp': '曜日', 'entry_hour_jp': '入室時間帯'
            }, inplace=True)
            final_columns = ['ID', '学年', '組', '番号', '氏名', '入室日', '曜日', '入室時刻', '退室日', '退室時刻', '滞在時間(分)', '入室時間帯']
            df_raw[final_columns].to_excel(writer, sheet_name='滞在記録(元データ)', index=False)
            
            # --- シート3: 日別サマリー ---
            # 1. これまで通り、日付ごとの集計と、学年ごとのクロス集計をそれぞれ作成
            daily_agg = df.groupby('date').agg(
                total_checkins=('system_id', 'count'),
                unique_users=('system_id', 'nunique'),
                avg_stay_minutes=('stay_minutes', 'mean')
            ).round(1)

            daily_grade_pivot = pd.crosstab(df['date'], df['grade_jp'], values=df['system_id'], aggfunc='nunique')
            
            # 2. 2つの集計結果を一度結合し、列にすべての学年が含まれるように保証する
            df_daily = pd.concat([daily_agg, daily_grade_pivot], axis=1)
            # (all_grades_jpはシート1作成時に定義済み)
            df_daily = df_daily.reindex(columns=daily_agg.columns.tolist() + all_grades_jp)

            all_dates_index = pd.to_datetime(pd.date_range(start=start_date, end=end_date)).date
            df_daily = df_daily.reindex(all_dates_index)

            # 4. 記録がなくてNaN(空欄)になったセルを0で埋める
            df_daily.fillna(0, inplace=True)
            
            # 5. 空だった行の曜日を、日付インデックスから再生成して埋める
            df_daily['day_of_week_jp'] = pd.to_datetime(df_daily.index).to_series().dt.day_name().map(DOW_MAP)

            # 6. カラムのデータ型（整数）と最終的な順序を整える
            count_cols = ['total_checkins', 'unique_users'] + all_grades_jp
            df_daily[count_cols] = df_daily[count_cols].astype(int)
            final_columns_daily = ['day_of_week_jp'] + count_cols + ['avg_stay_minutes']
            df_daily = df_daily[final_columns_daily]

            # 7. カラム名を日本語にリネームしてExcelに出力
            df_daily.rename(columns={
                'day_of_week_jp':'曜日', 'total_checkins':'総入室回数', 
                'unique_users':'ユニーク入室者数', 'avg_stay_minutes':'平均滞在時間(分)'
            }, inplace=True)
            df_daily.index.name = '日付'
            df_daily.to_excel(writer, sheet_name='日別サマリー')

            # --- シート4: 利用者別サマリー ---
            # 1. ログデータからユニークな日付の数を数え、「開室日数」を算出する
            total_open_days = df['date'].nunique()
            # 2. 「開室日数」を基に週数を計算する（7日未満は1週間とみなす）
            num_weeks = total_open_days / 7 if total_open_days >= 7 else 1
            df_user_summary = df.groupby(['grade_jp', 'class', 'student_number', 'name']).agg(
                total_checkins=('system_id', 'count'),
                total_stay_hours=('stay_hours', 'sum'),
                avg_stay_minutes=('stay_minutes', 'mean'),
                most_used_dow=('day_of_week_jp', lambda x: x.mode()[0]),
                first_use_date=('date', 'min'),
                most_used_hour=('entry_hour_jp', lambda x: x.mode()[0])
            ).reset_index()
            df_user_summary['weekly_avg_checkins'] = round(df_user_summary['total_checkins'] / num_weeks, 1)
            df_user_summary.rename(columns={
                'grade_jp': '学年', 'class': '組', 'student_number': '番号', 'name': '氏名',
                'total_checkins': '総利用回数', 'total_stay_hours': '総滞在時間(時間)',
                'avg_stay_minutes': '平均滞在時間(分)', 'most_used_dow': '最多利用曜日',
                'weekly_avg_checkins': '週平均利用回数', 'first_use_date': '初回利用日', 'most_used_hour': '最多入室時間帯'
            }, inplace=True)
            df_user_summary = df_user_summary[['学年', '組', '番号', '氏名', '総利用回数', '週平均利用回数', '総滞在時間(時間)', '平均滞在時間(分)', '最多利用曜日', '最多入室時間帯', '初回利用日']]
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