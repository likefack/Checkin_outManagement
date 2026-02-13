import openpyxl
import sqlite3
import os
import datetime
import csv
import subprocess
import glob
import pandas as pd

# --- パス定義 ---
SYSTEM_DIR = os.path.dirname(os.path.abspath(__file__))
# 管理者用_touchable フォルダへのパス (../../管理者用_touchable)
TOUCHABLE_DIR = os.path.join(SYSTEM_DIR, '..', '..', '管理者用_touchable')
HISTORY_DIR = os.path.join(TOUCHABLE_DIR, '質問履歴')

# 出力先ディレクトリが存在しない場合は作成
if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR, exist_ok=True)

# ファイルパターン・パス
STUDENT_INFO_PATTERN = os.path.join(TOUCHABLE_DIR, '生徒情報_*.xlsx')
HISTORY_FILE_XLSX = os.path.join(HISTORY_DIR, '質問履歴.xlsx')
HISTORY_FILE_CSV = os.path.join(HISTORY_DIR, '質問履歴_for_import.csv')
DATABASE = os.path.join(SYSTEM_DIR, 'questions.db')

# --- グローバル定数 ---
GRADE_DISPLAY_MAP = {
    1: "中1", 2: "中2", 3: "中3",
    4: "高1", 5: "高2", 6: "高3"
}

_roster_cache = None # 名簿データのキャッシュ

# --- ヘルパー関数: ファイル属性操作 ---
def _set_file_attribute_windows(filepath, make_readonly=True):
    try:
        if not os.path.exists(filepath):
            return True 
        action = "+R" if make_readonly else "-R"
        subprocess.run(["attrib", action, filepath], check=True, shell=True, capture_output=True, text=True, encoding="cp932")
        return True
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.strip() if e.stderr else "詳細不明のエラー (CalledProcessError)"
        print(f"  - 🚨 ファイル属性変更エラー ({os.path.basename(filepath)}): {error_output}")
        return False
    except Exception as e:
        print(f"  - 🚨 ファイル属性変更中の予期せぬエラー ({os.path.basename(filepath)}): {e}")
        return False

# --- 名簿読み込み関連 ---
def load_roster():
    """
    管理者用_touchable/生徒情報_*.xlsx から名簿を読み込む関数。
    """
    global _roster_cache
    if _roster_cache is not None:
        return _roster_cache
    
    roster = {}
    
    # ファイル検索
    files = glob.glob(STUDENT_INFO_PATTERN)
    if not files:
        print(f"🚨 警告: 名簿ファイルが見つかりません。パターン: {STUDENT_INFO_PATTERN}")
        _roster_cache = {}
        return _roster_cache
        
    target_file = files[0] # 最初に見つかったファイルを使用
    print(f"名簿ファイル読み込み: {target_file}")

    try:
        # pandasを使って読み込み (入退室管理アプリと合わせる)
        df = pd.read_excel(target_file, engine='openpyxl')
        
        # カラム名の空白除去
        df.columns = df.columns.str.strip()
        
        # 必要なカラムの存在確認 (入退室アプリの形式: '学年', '組', '番号', '生徒氏名')
        required_cols = ['学年', '組', '番号', '生徒氏名']
        if not all(col in df.columns for col in required_cols):
             # カラムがない場合、'氏名'なども試す
             if '氏名' in df.columns:
                 df.rename(columns={'氏名': '生徒氏名'}, inplace=True)
             else:
                 print(f"🚨 名簿ファイルに必要なカラムがありません: {required_cols}")
                 return {}

        for _, row in df.iterrows():
            try:
                grade = int(row['学年']) if pd.notna(row['学年']) else None
                class_num = int(row['組']) if pd.notna(row['組']) else None
                student_num = int(row['番号']) if pd.notna(row['番号']) else None
                name = str(row['生徒氏名']).strip() if pd.notna(row['生徒氏名']) else ""

                if grade and class_num and student_num and name: 
                    key = f"{grade}-{class_num}-{student_num}"
                    roster[key] = name
            except (ValueError, TypeError):
                continue
                
        _roster_cache = roster
        print(f"名簿を読み込みました: {len(roster)} 人分")
        
    except Exception as e:
        print(f" 名簿読み込みエラー: {e}")
        _roster_cache = {}
            
    return _roster_cache

def get_student_name(grade, class_num, student_num):
    roster = load_roster()
    key = f"{grade}-{class_num}-{student_num}"
    return roster.get(key, "氏名不明")

# --- 履歴書き込み ---
def append_to_history(question_id):
    print(f"ID {question_id} の記録処理を開始します...")
    
    DESIRED_CSV_HEADER = [
        "対応日時", "学年", "組", "番号", "氏名", 
        "質問内容", "小区分", "問題番号",
        "即時対応の可否"
    ]
    
    #  変更点1: Excelのヘッダー定義を修正 
    HEADER_EXCEL = ["ID", "受付日時", "学年", "組", "番号", "席番号", "問題番号", "氏名", "質問内容", "小区分", "画像ファイル名", "送信方法", "処理日時"]

    conn_db = None
    try:
        conn_db = sqlite3.connect(DATABASE)
        conn_db.row_factory = sqlite3.Row
        cur = conn_db.cursor()
        cur.execute("SELECT * FROM questions WHERE id = ?", (question_id,))
        question_db_row = cur.fetchone()

        if not question_db_row:
            print(f" エラー: ID {question_id} の質問がデータベースで見つかりません。")
            return

        question = dict(question_db_row)

        name = get_student_name(question['grade'], question['class_num'], question['student_num'])
        display_grade = GRADE_DISPLAY_MAP.get(question['grade'], str(question['grade']))
        processing_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # --- Excel (.xlsx) への書き込み ---
        xlsx_made_writable = False
        try:
            if os.path.exists(HISTORY_FILE_XLSX):
                if _set_file_attribute_windows(HISTORY_FILE_XLSX, make_readonly=False):
                    xlsx_made_writable = True
            
            try:
                workbook = openpyxl.load_workbook(HISTORY_FILE_XLSX)
                sheet_xlsx = workbook.active
            except FileNotFoundError:
                workbook = openpyxl.Workbook()
                sheet_xlsx = workbook.active
                sheet_xlsx.append(HEADER_EXCEL)
                print(f"{os.path.basename(HISTORY_FILE_XLSX)} を新規作成し、ヘッダーを書き込みました。")
            
            #  変更点2: Excelの行データから「補足」を削除 
            row_data_excel = [
                question['id'], 
                question['created_at'], 
                display_grade, 
                question['class_num'],
                question['student_num'], 
                question.get('seat_num', ''), 
                question.get('problem_num', ''),
                name, 
                question['subject'], # 「質問内容」
                question['sub_category'], # 「小科目」
                question.get('image_path', '') if question.get('image_path', '') else '',
                question['submission_type'], 
                processing_time
            ]
            sheet_xlsx.append(row_data_excel)
            workbook.save(HISTORY_FILE_XLSX)
            print(f"  - ID {question_id} を Excel ({os.path.basename(HISTORY_FILE_XLSX)}) に追記しました。")
        except Exception as e:
            print(f" Excel (.xlsx) 履歴書き込みエラー: {e}")
        finally:
            if xlsx_made_writable:
                _set_file_attribute_windows(HISTORY_FILE_XLSX, make_readonly=True)

        # --- CSV (.csv) への書き込み ---
        csv_made_writable = False
        try:
            submission_type_internal = question['submission_type']
            submission_type_for_csv = "可" if submission_type_internal == 'immediate' else "不可" if submission_type_internal == 'wait' else submission_type_internal

            csv_data_map = {
                "対応日時": processing_time,
                "学年": display_grade, 
                "組": question['class_num'],
                "番号": question['student_num'], 
                "氏名": name, 
                "質問内容": question['subject'],
                "小区分": question['sub_category'], 
                "問題番号": question.get('problem_num', ''),
                "即時対応の可否": submission_type_for_csv
            }
            current_csv_row_values = [csv_data_map.get(h, "") for h in DESIRED_CSV_HEADER]

            write_header_to_csv = False
            if not os.path.isfile(HISTORY_FILE_CSV):
                write_header_to_csv = True
            elif os.path.getsize(HISTORY_FILE_CSV) == 0:
                write_header_to_csv = True
            
            if os.path.exists(HISTORY_FILE_CSV):
                if _set_file_attribute_windows(HISTORY_FILE_CSV, make_readonly=False):
                    csv_made_writable = True
            
            with open(HISTORY_FILE_CSV, 'a', newline='', encoding='utf-8-sig') as f_csv:
                writer = csv.writer(f_csv)
                if write_header_to_csv: 
                    writer.writerow(DESIRED_CSV_HEADER)
                    print(f"{os.path.basename(HISTORY_FILE_CSV)} にヘッダーを書き込みました。")
                writer.writerow(current_csv_row_values)
            print(f"  - ID {question_id} を CSV ({os.path.basename(HISTORY_FILE_CSV)}) に追記しました。")

        except Exception as e:
            print(f"🚨 CSV 履歴書き込みエラー: {e}")
        finally:
            if os.path.exists(HISTORY_FILE_CSV):
                if csv_made_writable or write_header_to_csv :
                    _set_file_attribute_windows(HISTORY_FILE_CSV, make_readonly=True)
            
    finally:
        if conn_db:
            conn_db.close()
            
    print(f"ID {question_id} の記録処理が完了しました。 ✅")

def add_names_to_questions(questions_data):
    processed_list = []
    for q_row in questions_data:
        q = dict(q_row)
        name = get_student_name(q['grade'], q['class_num'], q['student_num'])
        q['student_name'] = name
        processed_list.append(q)
    return processed_list