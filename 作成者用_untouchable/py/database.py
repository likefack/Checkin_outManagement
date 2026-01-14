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

def sync_students_from_excel(conn):
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

    print(f"'{os.path.basename(student_excel_file)}' との同期を開始します...")
    cursor = conn.cursor()
    updated_count = 0
    inserted_count = 0
    
    # 既存データと比較して UPDATE または INSERT を実行
    for _, row in df_students.iterrows():
        s_id = row['system_id']
        cursor.execute("SELECT system_id FROM students WHERE system_id = ?", (s_id,))
        exists = cursor.fetchone()
        
        if exists:
            # 既存IDがある場合は情報を更新 (入退室ステータス等は変更しない)
            cursor.execute('''
                UPDATE students 
                SET enrollment_year=?, grade=?, class=?, student_number=?, name=?, guardian_email=?
                WHERE system_id=?
            ''', (row['enrollment_year'], row['grade'], row['class'], row['student_number'], row['name'], row['guardian_email'], s_id))
            updated_count += 1
        else:
            # 新規IDの場合は追加
            cursor.execute('''
                INSERT INTO students (system_id, enrollment_year, grade, class, student_number, name, guardian_email)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (s_id, row['enrollment_year'], row['grade'], row['class'], row['student_number'], row['name'], row['guardian_email']))
            inserted_count += 1
            
    conn.commit()
    print(f"生徒情報の同期完了: 更新 {updated_count} 件, 新規 {inserted_count} 件")


def import_phrases_from_excel(conn):
    if not os.path.exists(PHRASES_EXCEL_PATH): return
    df = pd.read_excel(PHRASES_EXCEL_PATH, engine='openpyxl')

    # '生年'と'没年'を数値に変換し、変換できない値（'没年不明'など）はNaNにする
    df['birth_year_num'] = pd.to_numeric(df['生年'], errors='coerce')
    df['death_year_num'] = pd.to_numeric(df['没年'], errors='coerce')

    def format_lifespan(row):
        # 生年が有効な数値かチェック
        if pd.notna(row['birth_year_num']):
            birth_year = int(row['birth_year_num'])
            # 没年が有効な数値かチェック
            if pd.notna(row['death_year_num']):
                death_year = int(row['death_year_num'])
                return f"({birth_year} ～ {death_year})"
            else:
                # 没年が空欄や文字列の場合は、空にする
                return f"({birth_year} ～ )"
        return None # 生年が無効なら何も返さない

    df['lifespan'] = df.apply(format_lifespan, axis=1)

    df.rename(columns={'属性': 'category', 'phrase': 'text', '発信者': 'author'}, inplace=True)
    df_phrases = df[['category', 'text', 'author', 'lifespan']]
    df_phrases.sample(frac=1).reset_index(drop=True).to_sql('phrases', conn, if_exists='append', index=False)
    print(f"'{os.path.basename(PHRASES_EXCEL_PATH)}' からフレーズをインポートしました。")


def init_db():
    # 常に最初にDBファイルに接続する
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # テーブル作成（IF NOT EXISTS なので毎回実行しても安全）
        create_tables(conn)
        
        # 生徒情報の同期（毎回起動時にExcelと同期を行う）
        # 新しい関数 sync_students_from_excel を使用
        sync_students_from_excel(conn)
        
        # フレーズテーブルの確認（データが空の場合のみインポート）
        cursor.execute("SELECT COUNT(*) FROM phrases")
        if cursor.fetchone()[0] == 0:
            import_phrases_from_excel(conn)
            
        print("データベースの初期化・更新処理が完了しました。")

    except Exception as e:
        print(f"!!! データベース処理中にエラーが発生しました: {e} !!!")
        # 既存DBへの影響を考慮し、ここではDB削除を行わずエラーを通知するのみとする
        conn.close()
        raise e
            
    # 正常に処理が終わったら接続を閉じる
    conn.close()

