from flask import Blueprint, render_template, request, redirect, url_for, session

# Blueprintの定義
# static_url_pathを '/qna/static' にして、メインアプリと衝突しないようにします
school_qna_bp = Blueprint(
    'school_qna', 
    __name__, 
    template_folder='templates',
    static_folder='static',
    static_url_path='/qna/static' 
)

# --- ダミーのルート定義 (画面表示用) ---

@school_qna_bp.route('/')
def index():
    # 【追加】表示確認用のダミーデータ（後でExcel読み込みに置き換えます）
    sub_categories_data = {
        '数学': ['数I', '数A', '数II', '数B', '数III', '数C'],
        '英語': ['文法', '長文読解', '英作文', '単語'],
        '物理': ['力学', '電磁気', '波動', '熱力学', '原子'],
        '化学': ['理論化学', '無機化学', '有機化学'],
        'その他': ['学習相談', '進路相談']
    }
    
    # base.html で使用するログイン状態も取得
    is_logged_in = session.get('logged_in', False)

    # 新規受付画面（データを渡してレンダリング）
    return render_template('qna_index.html', sub_categories_data=sub_categories_data, is_logged_in=is_logged_in)

@school_qna_bp.route('/login', methods=['GET', 'POST'])
def login():
    # ログイン画面
    return render_template('login.html')

@school_qna_bp.route('/logout')
def logout():
    # ログアウト処理（ダミー）
    session.pop('logged_in', None)
    return redirect(url_for('school_qna.index'))

@school_qna_bp.route('/list')
def list_view():
    # 質問一覧画面（ダミーデータ）
    dummy_questions = [
        {'id': 1, 'category': '数学', 'content': '微分の解き方がわかりません', 'status': 'pending', 'grade': 1, 'class_num': 1, 'student_num': 1, 'seat_num': 'A1', 'problem_num': 'p.12', 'student_name': 'テスト太郎', 'subject': '数学', 'sub_category': '微分', 'created_at': '2026-02-12 10:00', 'image_path': '[]'}
    ]
    # テンプレート側で使う定数などを渡す
    GRADE_DISPLAY_MAP = {1: '中1', 2: '中2', 3: '中3', 4: '高1', 5: '高2', 6: '高3'}
    return render_template('list.html', questions=dummy_questions, enumerate=enumerate, GRADE_DISPLAY_MAP=GRADE_DISPLAY_MAP)

@school_qna_bp.route('/change_password')
def change_password_prompt_current():
    # パスワード変更画面（ダミー）
    return render_template('change_password_current.html', title='パスワード変更')

@school_qna_bp.route('/view_images/<path:filename>')
def view_images(filename):
    return f"画像表示テスト: {filename}"

# base.htmlのエラー回避用（アイコンなど）
@school_qna_bp.route('/app_icon')
def app_icon():
    return "icon"