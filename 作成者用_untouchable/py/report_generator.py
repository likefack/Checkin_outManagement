import pandas as pd
import sqlite3
import os
import datetime
import pytz
import logging

logger = logging.getLogger(__name__)

# --- 定数・ヘルパー関数 ---
JST = pytz.timezone('Asia/Tokyo')
UTC = pytz.utc
GRADE_MAP = {1: '中1', 2: '中2', 3: '中3', 4: '高1', 5: '高2', 6: '高3'}
GRADE_ALPHABET_MAP = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F'}
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
        # format='ISO8601' を追加して、様々な形式のISO8601文字列に正しく対応する
        df['entry_time'] = pd.to_datetime(df['entry_time'], format='ISO8601', utc=True).dt.tz_convert(JST)
        df['exit_time'] = pd.to_datetime(df['exit_time'], errors='coerce', format='ISO8601', utc=True).dt.tz_convert(JST)

        completed_logs = df.dropna(subset=['exit_time']).copy()

         # 先に滞在時間（分）を計算する
        # この時点で exit_time と entry_time は日時型なので、安全に計算できる
        completed_logs.loc[:, 'stay_minutes'] = (completed_logs['exit_time'] - completed_logs['entry_time']).dt.total_seconds() / 60

        # system_id ごとに平均滞在時間を計算する
        avg_stay_minutes_map = completed_logs.groupby('system_id')['stay_minutes'].mean().round(1)

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
        
        df = df.sort_values(by='entry_time') # 入室時間が早い順に並び替え（シート0で最初の記録を取得するために必要）

        # 【追加】ファイルがロックされている（開かれている）場合に別名を生成する処理
        base_name, ext = os.path.splitext(file_name)
        counter = 1
        while True:
            try:
                # ファイルが存在する場合のみロックチェックを行う
                if os.path.exists(file_path):
                    # 追記モードで開いてみることでロック状態を確認
                    # WindowsではExcelで開いているファイルに対して PermissionError が発生する
                    with open(file_path, 'a'):
                        pass
                # エラーが出なければ書き込み可能（またはファイルが存在しない）なのでループを抜ける
                break
            except PermissionError:
                # ロックされている場合は連番を付与して再試行
                logger.warning(f"ファイル {os.path.basename(file_path)} はロックされています。別名での保存を試みます。")
                new_name = f"{base_name}_{counter}{ext}"
                file_path = os.path.join(report_dir, new_name)
                counter += 1

        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            
            # --- シート0: 日報人数カウント用 ---
            # (仕様変更) 日付と生徒IDで重複を削除し、その日最初の入室記録のみを対象とする
            df_for_sheet0 = df.drop_duplicates(subset=['date', 'system_id'], keep='first')
            df_copy_paste = df_for_sheet0.copy() # フィルター後のデータを使用

            # 1. (仕様変更) IDを 'ID_23C0115' 形式に生成
            # system_idを文字列に変換（7桁ゼロ埋め）
            df_copy_paste['system_id_str'] = df_copy_paste['system_id'].astype(str).str.zfill(7)
            # 学年(数値)をアルファベット(A-F)に変換
            df_copy_paste['grade_alphabet'] = df_copy_paste['grade'].map(GRADE_ALPHABET_MAP)
            # 結合してIDを生成: 'ID_' + '23' + 'C' + '0115'
            # system_idの3文字目(学年)を、マッピングしたアルファベットで置き換える
            df_copy_paste['ID_formatted'] = 'ID_' + \
                                            df_copy_paste['system_id_str'].str[0:2] + \
                                            df_copy_paste['grade_alphabet'] + \
                                            df_copy_paste['system_id_str'].str[3:]

            # 1-2. 中高を除いた数字のみの学年を作成
            df_copy_paste['学年_数値'] = df_copy_paste['grade_jp'].str.extract(r'(\d+)').astype(int)

            # 2. 入室時間と退室時間を HH:MM 形式にフォーマット
            df_copy_paste['入室時間_HM'] = df_copy_paste['entry_time'].dt.strftime('%H:%M')
            # 退室時間が空欄でない場合のみフォーマット、空欄の場合は空文字列
            df_copy_paste['退室時間_HM'] = df_copy_paste['exit_time'].apply(lambda x: x.strftime('%H:%M') if pd.notna(x) else '')

            # 3. その日の何回目の入室かを計算 (ユニーク抽出したため、すべて1になる)
            df_copy_paste['入室回数'] = 1 # groupby処理を削除し、固定で 1 を設定

            # 4. 中高の区分を作成 ( 削除)
            # df_copy_paste['中高'] = df_copy_paste['grade_jp'].str[0] # この行を削除

            # 5. 必要な列を順番通りに選択 ( ID -> ID_formatted に変更、'中高'を削除)
            df_final_copy_paste = df_copy_paste[[
                'ID_formatted', # 変更
                '学年_数値',
                'class',
                'student_number',
                'name',
                '入室時間_HM',
                '退室時間_HM',
                '入室回数',
                # '中高' # 削除
            ]]

            # 6. Excelに出力する際の列名（ヘッダー）を変更 
            df_final_copy_paste.columns = [
                'ID',
                '学年',
                '組',
                '番',
                '名前',
                '入室時間',
                '退室時間',
                '回数',
                # '中高' # 削除
            ]

            # 7. Excelファイルの一番目のシートとして書き出す
            # (仕様変更) シート名を変更し、A1に期間、A2からデータを出力
            sheet_name = '日報人数カウント用'
            
            # データをA2から書き出す (startrow=1) （仕様①）
            df_final_copy_paste.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1)
            
            # openpyxlのワークシートオブジェクトを取得
            worksheet = writer.sheets[sheet_name]
            
            # (仕様変更) A1セルに期間を書き込む （仕様②）
            if start_date == end_date:
                # 単一日の場合
                title_str = start_date.strftime('%Y/%m/%d')
            else:
                # 複数日の場合
                title_str = f"{start_date.strftime('%Y/%m/%d')}～{end_date.strftime('%Y/%m/%d')}"
            worksheet.cell(row=1, column=1, value=title_str)
            
            # (仕様変更) シート見出しの色を黄色に設定 （仕様③）
            worksheet.sheet_properties.tabColor = "FFFFFF00" # ARGB for Yellow

            # --- シート1: 日別ユニーク学年組別サマリー ---
            # 存在するすべての学年・組のリストをマスターから取得
            all_grades_jp = sorted(students_master['grade'].map(GRADE_MAP).unique(), key=lambda x: list(GRADE_MAP.values()).index(x))
            all_classes = sorted(students_master['class'].unique())

            # 1. 日付と生徒IDで重複を削除し、「日ごとのユニーク利用者」データを作成
            df_daily_unique_users = df.drop_duplicates(subset=['date', 'system_id'])

            # 2. 上記の「日ごとユニーク」データを使ってクロス集計（=延べ人数をカウント）
            df_summary_class = pd.crosstab(df_daily_unique_users['grade_jp'], df_daily_unique_users['class'])
            
            # 3. 欠損している学年・組を0埋めして合計を計算
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
            df_daily_unique_users = df.drop_duplicates(subset=['date', 'system_id'])
            df_user_summary = df.groupby(['grade_jp', 'class', 'student_number', 'name', 'system_id']).agg(
                total_checkins=('system_id', 'count'),
                unique_days_attended=('date', 'nunique'),
                total_stay_minutes=('stay_minutes', 'sum'),
                avg_stay_minutes=('stay_minutes', 'mean'),
                most_used_dow=('day_of_week_jp', lambda x: x.mode()[0]),
                first_use_date=('date', 'min'),
                most_used_hour=('entry_hour_jp', lambda x: x.mode()[0])
            ).reset_index()
             # 曜日ごとの利用日数を集計
            dow_counts = pd.crosstab(df_daily_unique_users['system_id'], df_daily_unique_users['day_of_week_jp'])
            
            # 月〜日のカラム順に並び替え
            dow_order = ['月', '火', '水', '木', '金', '土', '日']
            dow_counts = dow_counts.reindex(columns=dow_order, fill_value=0)

            # 曜日ごとの集計結果をメインのサマリーに結合
            df_user_summary = pd.merge(df_user_summary, dow_counts, on='system_id', how='left')
            
            #結合によって発生したNaN(欠損値)を0で埋め、データ型を整数に変換する
            df_user_summary[dow_order] = df_user_summary[dow_order].fillna(0).astype(int)
            # 結合後に不要になったsystem_id列を削除
            df_user_summary.drop(columns=['system_id'], inplace=True)
            df_user_summary['weekly_avg_checkins'] = round(df_user_summary['unique_days_attended'] / num_weeks, 1)
            df_user_summary.rename(columns={
                'grade_jp': '学年', 'class': '組', 'student_number': '番号', 'name': '氏名',
                'total_checkins': '総利用回数', 'unique_days_attended': '利用日数', # '利用日数' を追加
                'total_stay_minutes': '総滞在時間(分)', # '総滞在時間(分)' に変更
                'avg_stay_minutes': '平均滞在時間(分)', 'most_used_dow': '最多利用曜日',
                'weekly_avg_checkins': '週平均利用回数', 'first_use_date': '初回利用日', 'most_used_hour': '最多入室時間帯'
            }, inplace=True)
            
            # 最終的に出力する列のリストを定義し直す
            final_user_summary_columns = [
                '学年', '組', '番号', '氏名', 
                '総利用回数', '利用日数', '週平均利用回数', 
                '総滞在時間(分)', '平均滞在時間(分)', 
                '最多利用曜日', '月', '火', '水', '木', '金', '土', '日',
                '最多入室時間帯', '初回利用日'
            ]
            df_user_summary = df_user_summary[final_user_summary_columns]
            
            df_user_summary.to_excel(writer, sheet_name='利用者別サマリー', index=False)
            
            # --- シート5: 時間帯別総入室回数サマリー ---
            hourly_pivot = pd.crosstab(df['entry_hour_jp'], df['grade_jp']).reindex(columns=all_grades_jp, fill_value=0)
            all_hours_jp = [f"{h}時台" for h in range(24)]
            hourly_pivot = hourly_pivot.reindex(index=all_hours_jp, fill_value=0)
            hourly_pivot['合計'] = hourly_pivot.sum(axis=1)
            hourly_pivot.loc['合計'] = hourly_pivot.sum()
            hourly_pivot.index.name = '時間帯'
            hourly_pivot.to_excel(writer, sheet_name='時間帯別総入室回数サマリー')

            # --- シート6: 時間帯別在室人数サマリー ---
            # 各滞在がカバーする時間帯（日時）のリストを生成
            def get_hour_timestamps(row):
                start = row['entry_time'].floor('h')
                # 退室時間がジャスト(例: 10:00:00)の場合は、その時間帯(10時台)には在室していないとみなすため1秒引く
                end = (row['exit_time'] - pd.Timedelta(seconds=1)).floor('h')
                # startからendまでの1時間ごとの「日時そのもの」を取得（日付情報を保持するため）
                return pd.date_range(start, end, freq='h').tolist()

            # データフレームに各利用者の在室時間帯（日時）リストを追加
            df_occupancy = df[['grade_jp', 'system_id']].copy()
            df_occupancy['timestamps'] = df.apply(get_hour_timestamps, axis=1)
            
            # リストを行に展開（1人の滞在を複数の時間帯行に分割）
            df_exploded = df_occupancy.explode('timestamps')
            df_exploded = df_exploded.dropna(subset=['timestamps'])

            # 日付と時間を抽出
            df_exploded['date_val'] = df_exploded['timestamps'].dt.date
            df_exploded['hour_val'] = df_exploded['timestamps'].dt.hour
            df_exploded['hour_str'] = df_exploded['hour_val'].astype(str) + '時台'

            #「同じ日」かつ「同じ時間帯」かつ「同じ人」の場合のみ重複として削除する
            # これにより、「別の日」の「同じ時間帯」の利用は維持される（例: 月曜10時と火曜10時は別カウント）
            df_exploded = df_exploded.drop_duplicates(subset=['date_val', 'hour_str', 'system_id'])
            
            # 時間帯(0-23時) × 学年 でクロス集計（人数カウント）
            # ここで集計されるのは「期間中の全日程における、その時間帯の在室人数の延べ合計」となる
            occupancy_pivot = pd.crosstab(df_exploded['hour_str'], df_exploded['grade_jp'])
            
            # 全時間帯・全学年の枠を確保して埋める
            occupancy_pivot = occupancy_pivot.reindex(index=all_hours_jp, columns=all_grades_jp, fill_value=0)
            
            # 合計列・行の計算
            # 横方向（その時間帯の全学年合計）は意味があるため残す
            occupancy_pivot['合計'] = occupancy_pivot.sum(axis=1)
            
            # 縦方向（列の合計）は、時間をまたぐ同一人物が重複加算され、
            # 「延べ積算人数」のような直感的でない値になるため計算しない
            # occupancy_pivot.loc['合計'] = occupancy_pivot.sum()
            
            occupancy_pivot.index.name = '時間帯'
            occupancy_pivot.to_excel(writer, sheet_name='時間帯別在室人数サマリー')

        return file_path, f"レポートが正常に作成されました: {os.path.basename(file_path)}"
        
    except Exception as e:
        error_message = f"レポート作成中にエラーが発生しました。該当期間のExcelファイルが開かれていないかを確認してください: {e}"
        logger.error(error_message, exc_info=True)
        return None, error_message