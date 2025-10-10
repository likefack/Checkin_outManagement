import pandas as pd
import sqlite3
import os
import numpy as np
from datetime import datetime

def unify_timezone_to_jst(series):
    s_dt = pd.to_datetime(series, errors='coerce')
    return s_dt.apply(lambda x: x.tz_localize('Asia/Tokyo') if pd.notnull(x) and x.tzinfo is None else x.tz_convert('Asia/Tokyo'))

def create_report(db_path, start_date_str, end_date_str):
    """
    指定された期間で、要件に合わせて詳細な分析レポートを生成する。
    """
    start_date = f"{start_date_str} 00:00:00"
    end_date = f"{end_date_str} 23:59:59"
    
    try:
        conn = sqlite3.connect(db_path)
        df_students_master = pd.read_sql_query("SELECT system_id AS ID, grade AS 学年, class AS 組, student_number AS 番号, name AS 氏名 FROM students", conn)
        query_all_entries = """
            SELECT al.id as log_id, s.system_id AS ID, s.grade AS 学年, s.class AS 組, s.student_number AS 番号, s.name AS 氏名, al.entry_time, al.exit_time
            FROM attendance_logs al JOIN students s ON al.student_id = s.system_id
            WHERE al.entry_time BETWEEN ? AND ?
        """
        df_all_entries = pd.read_sql_query(query_all_entries, conn, params=(start_date, end_date))
        conn.close()
    except Exception as e:
        return None, f"データベース読み込みエラー: {e}"

    if df_all_entries.empty:
        return "No data", "指定期間内に入室記録がありませんでした。"

    # --- データ前処理 ---
    grade_map = {1: '中1', 2: '中2', 3: '中3', 4: '高1', 5: '高2', 6: '高3'}
    weekday_map = {'Monday': '月', 'Tuesday': '火', 'Wednesday': '水', 'Thursday': '木', 'Friday': '金', 'Saturday': '土', 'Sunday': '日'}

    df_all_entries['入室時刻_jst'] = unify_timezone_to_jst(df_all_entries['entry_time'])
    df_all_entries['退室時刻_jst'] = unify_timezone_to_jst(df_all_entries['exit_time'])
    df_all_entries.dropna(subset=['入室時刻_jst'], inplace=True)
    
    df_all_entries['滞在時間(分)'] = (df_all_entries['退室時刻_jst'] - df_all_entries['入室時刻_jst']).dt.total_seconds() / 60
    df_all_entries['滞在時間(時間)'] = df_all_entries['滞在時間(分)'] / 60
    
    df_all_entries['日付'] = df_all_entries['入室時刻_jst'].dt.date
    df_all_entries['曜日'] = df_all_entries['入室時刻_jst'].dt.day_name().map(weekday_map)
    df_all_entries['入室時間帯'] = df_all_entries['入室時刻_jst'].dt.hour.astype(str) + "時台"
    df_all_entries['学年'] = df_all_entries['学年'].map(grade_map)
    
    # --- 1. 学年組別サマリー ---
    all_grades_classes = df_students_master.groupby(['学年', '組']).size().reset_index()[['学年', '組']]
    all_grades_classes['学年'] = all_grades_classes['学年'].map(grade_map)
    all_grades_classes = all_grades_classes.drop_duplicates().set_index(['学年', '組'])
    summary_grade_class_raw = df_all_entries.groupby(['学年', '組'])['ID'].nunique()
    df_summary_grade_class = all_grades_classes.join(summary_grade_class_raw).fillna(0).astype(int).unstack(level='組', fill_value=0)
    df_summary_grade_class.columns = df_summary_grade_class.columns.droplevel(0)
    df_summary_grade_class['合計'] = df_summary_grade_class.sum(axis=1)
    df_summary_grade_class.loc['合計'] = df_summary_grade_class.sum()

    # --- 2. 滞在記録(元データ) ---
    df_raw_logs = df_all_entries.copy()
    grade_char_map_rev = {1:'A', 2:'B', 3:'C', 4:'D', 5:'E', 6:'F'}
    df_raw_logs['ID_str'] = df_raw_logs['ID'].astype(str)
    df_raw_logs['grade_char'] = df_raw_logs['ID_str'].str[2].astype(int).map(grade_char_map_rev)
    df_raw_logs['ID'] = "ID_" + (df_raw_logs['ID_str'].str[0:2] + df_raw_logs['grade_char'].fillna('') + df_raw_logs['ID_str'].str[3:])
    
    df_raw_logs = df_raw_logs[['ID', '学年', '組', '番号', '氏名', '入室時刻_jst', '退室時刻_jst', '滞在時間(分)', '滞在時間(時間)', '入室時間帯', '日付', '曜日']]
    df_raw_logs.rename(columns={'入室時刻_jst': '入室時刻', '退室時刻_jst': '退室時刻'}, inplace=True)
    df_raw_logs['入室時刻'] = df_raw_logs['入室時刻'].dt.strftime('%Y-%m-%d %H:%M:%S')
    df_raw_logs['退室時刻'] = df_raw_logs['退室時刻'].apply(lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(x) else '')
    df_raw_logs['滞在時間(分)'] = df_raw_logs['滞在時間(分)'].round(1)
    df_raw_logs['滞在時間(時間)'] = df_raw_logs['滞在時間(時間)'].round(2)


    # --- 3. 日別サマリー ---
    daily_summary_base = df_all_entries.groupby('日付').agg(総入室回数=('ID', 'count'), ユニーク入室者数=('ID', 'nunique'))
    daily_summary_base['曜日'] = df_all_entries.groupby('日付')['曜日'].first()
    daily_summary_base = daily_summary_base[['曜日', '総入室回数', 'ユニーク入室者数']]
    daily_by_grade = df_all_entries.groupby(['日付', '学年'])['ID'].nunique().unstack(fill_value=0)
    all_grade_columns = [grade_map[i] for i in range(1, 7)]
    daily_by_grade = daily_by_grade.reindex(columns=all_grade_columns, fill_value=0)
    
    daily_stay_time = df_all_entries.groupby('日付')['滞在時間(分)'].mean().round(1)
    df_daily_summary = pd.concat([daily_summary_base, daily_by_grade, daily_stay_time], axis=1).fillna(0)
    df_daily_summary.rename(columns={'滞在時間(分)': '平均滞在時間(分)'}, inplace=True)

    # --- 4. 利用者別サマリー ---
    user_summary_entry = df_all_entries.groupby('ID').agg(総利用回数=('ID', 'count'), 初回利用日=('日付', 'min'), 最多利用曜日=('曜日', lambda x: x.mode()[0]), 最多入室時間帯=('入室時間帯', lambda x: x.mode()[0]))
    
    # ★★★ 修正箇所: min_count=1を使い、データがない場合は合計を0でなくNaN(空欄)にする ★★★
    user_summary_stay = df_all_entries.groupby('ID').agg(
        総滞在時間_分=('滞在時間(分)', lambda x: x.sum(min_count=1)),
        平均滞在時間_分=('滞在時間(分)', 'mean')
    )
    
    df_user_summary = df_students_master.set_index('ID').join(user_summary_entry, how='inner')
    df_user_summary = df_user_summary.join(user_summary_stay)
    
    df_user_summary = df_user_summary.sort_values(by=['学年', '組', '番号'])
    df_user_summary['学年'] = df_user_summary['学年'].map(grade_map)
    df_user_summary.rename(columns={'総滞在時間_分': '総滞在時間(分)', '平均滞在時間_分': '平均滞在時間(分)'}, inplace=True)
    
    # 総利用回数がない場合は0を埋める
    df_user_summary['総利用回数'] = df_user_summary['総利用回数'].fillna(0)
    
    df_user_summary = df_user_summary.reset_index()[['学年', '組', '番号', '氏名', '総利用回数', '総滞在時間(分)', '平均滞在時間(分)', '最多利用曜日', '初回利用日', '最多入室時間帯']]

    # --- 5. 時間帯別サマリー ---
    hourly_summary_raw = pd.crosstab(
        index=df_all_entries['入室時間帯'],
        columns=df_all_entries['学年'],
        values=df_all_entries['ID'],
        aggfunc=pd.Series.nunique
    ).fillna(0).astype(int)

    all_hours = [f"{h}時台" for h in range(24)]
    all_grade_cols = [grade_map[i] for i in range(1, 7)]
    hourly_summary = hourly_summary_raw.reindex(index=all_hours, columns=all_grade_cols, fill_value=0)
    hourly_summary['合計'] = hourly_summary.sum(axis=1)
    hourly_summary.rename_axis('時間帯(時)', inplace=True)
    hourly_summary.reset_index(inplace=True)

    # --- Excelファイルへの書き込み ---
    try:
        log_folder = os.getenv('LOG_FOLDER_PATH')
        if not log_folder or log_folder.strip() == "":
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_folder = os.path.join(base_dir, '..', '管理者用_touchable', 'logs')
        os.makedirs(log_folder, exist_ok=True) 
        file_name = f"集計レポート_{start_date_str.replace('-', '')}-{end_date_str.replace('-', '')}.xlsx"
        file_path = os.path.join(log_folder, file_name)
        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            df_summary_grade_class.to_excel(writer, sheet_name='学年組別サマリー')
            df_raw_logs.to_excel(writer, sheet_name='滞在記録(元データ)', index=False)
            df_daily_summary.to_excel(writer, sheet_name='日別サマリー')
            df_user_summary.to_excel(writer, sheet_name='利用者別サマリー', index=False)
            hourly_summary.to_excel(writer, sheet_name='時間帯別サマリー', index=False)
        return file_path, f"レポートを作成しました: {file_name}"
    except Exception as e:
        return None, f"Excelファイル書き込み中にエラーが発生しました: {e}"