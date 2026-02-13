import sqlite3
import os

# このファイル(database.py)が存在するディレクトリを基準にする
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# データベースファイルをこのディレクトリ内に作成する
DATABASE = os.path.join(BASE_DIR, 'questions.db')

def init_db():
    """データベースとテーブルを作る関数"""
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        #  変更点1: CREATE TABLE文に client_id を追加 
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grade INTEGER NOT NULL,
                class_num INTEGER NOT NULL,
                student_num INTEGER NOT NULL,
                seat_num INTEGER,
                problem_num TEXT,
                subject TEXT NOT NULL,
                sub_category TEXT NOT NULL,
                details TEXT,
                image_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                submission_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT (STRFTIME('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
                client_id TEXT
            );
        """)
        
        # 既存のテーブルにカラムがなければ追加する処理
        cursor.execute("PRAGMA table_info(questions)")
        columns = [column[1] for column in cursor.fetchall()]

        if 'seat_num' not in columns:
            cursor.execute("ALTER TABLE questions ADD COLUMN seat_num INTEGER") 
        if 'problem_num' not in columns:
            cursor.execute("ALTER TABLE questions ADD COLUMN problem_num TEXT")
        
        #  変更点2: client_id カラムの存在チェックと追加 
        if 'client_id' not in columns:
            cursor.execute("ALTER TABLE questions ADD COLUMN client_id TEXT")
            print("カラム 'client_id' を questions テーブルに追加しました。")

        conn.commit()
        
        print(f"データベースの準備が完了しました (場所: {DATABASE})")
    except sqlite3.Error as e:
        print(f"データベースで問題発生: {e} ")
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    # このスクリプトを直接実行したときにデータベースを初期化する
    init_db()