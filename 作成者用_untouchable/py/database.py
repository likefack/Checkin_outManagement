import sqlite3
import pandas as pd
import glob
import os

# バッチファイルで .../py フォルダに移動してから実行されることを前提とする相対パス
DB_PATH = os.path.join('..', 'students.db') 
STUDENT_EXCEL_PATH_PATTERN = os.path.join('..', '..', '管理者用_touchable', '生徒情報_*.xlsx')
PHRASES_EXCEL_PATH = os.path.join('..', '..', '管理者用_touchable', 'motivational_phrases.xlsx')

def get_student_excel_path():
    files = glob.glob(STUDENT_EXCEL_PATH_PATTERN)
    return files[0] if files else None

def create_tables(conn):
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT, system_id INTEGER UNIQUE NOT NULL, enrollment_year INTEGER,
        grade INTEGER, class INTEGER, student_number INTEGER, name TEXT, guardian_email TEXT,
        is_present INTEGER DEFAULT 0, current_log_id INTEGER, title TEXT, last_phrase_id INTEGER DEFAULT 0
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS attendance_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        system_id INTEGER NOT NULL,
        entry_time TEXT,
        exit_time TEXT, 
        seat_number INTEGER, 
        FOREIGN KEY (system_id) REFERENCES students(system_id)
    )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_system_id ON attendance_logs(system_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_entry_time ON attendance_logs(entry_time)')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS phrases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, text TEXT NOT NULL, author TEXT, lifespan TEXT
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS achievements_tracker (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        system_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        achieved_at DATE NOT NULL, 
        context TEXT, 
        FOREIGN KEY (system_id) REFERENCES students(system_id)
    )
    ''')
    conn.commit()
    print("データベースのテーブルを定義しました。")

def import_students_from_excel(conn):
    student_excel_file = get_student_excel_path()
    if not student_excel_file:
        raise FileNotFoundError(f"生徒情報Excelファイルが見つかりません。検索パターン: {STUDENT_EXCEL_PATH_PATTERN}")

    df = pd.read_excel(student_excel_file, engine='openpyxl')
    df.columns = df.columns.str.strip()
    df.rename(columns={
        'システムID': 'system_id', '入学年度': 'enrollment_year', '学年': 'grade',
        '組': 'class', '番号': 'student_number', '生徒氏名': 'name', 'メールアドレス': 'guardian_email'
    }, inplace=True)
    required_cols = ['system_id', 'enrollment_year', 'grade', 'class', 'student_number', 'name', 'guardian_email']
    if not all(col in df.columns for col in required_cols):
        missing = [col for col in required_cols if col not in df.columns]
        raise ValueError(f"Excelに必要なカラムがありません: {missing}")

    df_students = df[required_cols].copy()
    for col in ['system_id', 'enrollment_year', 'grade', 'class', 'student_number']:
        df_students[col] = pd.to_numeric(df_students[col], errors='coerce')
    df_students.dropna(subset=['system_id'], inplace=True)
    for col in ['system_id', 'enrollment_year', 'grade', 'class', 'student_number']:
         df_students[col] = df_students[col].astype(int)

    df_students.to_sql('students', conn, if_exists='append', index=False)
    print(f"'{os.path.basename(student_excel_file)}' から生徒情報をインポートしました。")


def import_phrases_from_excel(conn):
    if not os.path.exists(PHRASES_EXCEL_PATH): return
    df = pd.read_excel(PHRASES_EXCEL_PATH, engine='openpyxl')
    df['lifespan'] = df.apply(lambda row: f"({row['生年']} ～ {row['没年']})" if pd.notna(row['生年']) else None, axis=1)
    df.rename(columns={'属性': 'category', 'phrase': 'text', '発信者': 'author'}, inplace=True)
    df_phrases = df[['category', 'text', 'author', 'lifespan']]
    df_phrases.sample(frac=1).reset_index(drop=True).to_sql('phrases', conn, if_exists='append', index=False)
    print(f"'{os.path.basename(PHRASES_EXCEL_PATH)}' からフレーズをインポートしました。")


def init_db():
    # ▼▼▼ 変更ここから ▼▼▼
    # 常に最初にDBファイルに接続する
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 'students'テーブルが存在するかどうかを、データベース自身に問い合わせて確認
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='students'")
    table_exists = cursor.fetchone()
    
    # もしテーブルが存在しなければ、初期化処理（テーブル作成とデータインポート）を実行
    if not table_exists:
        print("studentsテーブルが見つからないため、初期化を開始します...")
        try:
            create_tables(conn)
            import_students_from_excel(conn)
            import_phrases_from_excel(conn)
            print("データベースの初期化が完了しました。")
        except Exception as e:
            print(f"!!! データベース初期化中に致命的なエラーが発生しました: {e} !!!")
            # エラー発生時は接続を閉じ、中途半端なDBファイルを削除する
            conn.close()
            if os.path.exists(DB_PATH):
                os.remove(DB_PATH)
            raise e
            
    # 正常に処理が終わったら接続を閉じる
    conn.close()
    # ▲▲▲ 変更ここまで ▲▲▲
