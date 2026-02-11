import os
import sqlite3
import datetime
import json
import secrets
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, send_from_directory, flash, Response, current_app
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# 相対インポートでモジュールを読み込み
from . import excel_handler
from . import category_handler
from . import database
from .excel_handler import GRADE_DISPLAY_MAP

# Blueprintの定義
school_qna_bp = Blueprint(
    'school_qna', 
    __name__, 
    template_folder='templates',
    static_folder='static',
    static_url_path='/qna/static' 
)

# --- 設定・定数 ---
# ※ app.config への依存を避けるため、Blueprint内でパスを解決するか、current_app を利用するが、
# ここではモジュール変数として定義して利用する
SYSTEM_DIR = os.path.dirname(os.path.abspath(__file__))
# データベースパス
DATABASE_PATH = os.path.join(SYSTEM_DIR, 'questions.db')
# パスワードハッシュファイル
PASSWORD_HASH_FILE = os.path.join(SYSTEM_DIR, 'admin_pass.hash')
# アップロードフォルダ (../../data_可触部/uploads ではなく、管理者用_touchable配下に変更する場合も考慮するが、今回はdata_可触部がもしあればそこ、なければ管理者用へ)
# 今回の要件では「出力」を管理者用にとのことだが、アップロード画像の一時保存場所については
# 既存構造 (data_可触部/uploads) を維持するか、管理者用に変更するか。
# 指示には「質問履歴_for_import.csvなどの出力も...専用のフォルダを作って」とある。
# 画像もそこにまとめたほうが管理しやすいので、管理者用_touchable/質問画像 に設定する。
TOUCHABLE_DIR = os.path.join(SYSTEM_DIR, '..', '..', '管理者用_touchable')
UPLOAD_FOLDER = os.path.join(TOUCHABLE_DIR, '質問画像')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'heic', 'webp'}
MIN_PASSWORD_LENGTH = 8

# ディレクトリ作成
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- 初期化処理 ---
# アプリ起動時にDB初期化（テーブル作成）
database.init_db()

# パスワード初期化
if not os.path.exists(PASSWORD_HASH_FILE):
    hashed_password = generate_password_hash('koberyukoku')
    with open(PASSWORD_HASH_FILE, 'w') as f:
        f.write(hashed_password)

# --- ヘルパー関数 ---
def get_db():
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    return db

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_pending_count():
    db = get_db()
    try:
        count = db.execute("SELECT COUNT(id) FROM questions WHERE status = 'pending'").fetchone()[0]
    except (sqlite3.Error, TypeError):
        count = 0
    db.close()
    return count

# --- コンテキストプロセッサ ---
@school_qna_bp.context_processor
def inject_global_vars():
    return dict(
        is_logged_in=session.get('logged_in', False),
        GRADE_DISPLAY_MAP=GRADE_DISPLAY_MAP
    )

# --- ルーティング ---

@school_qna_bp.route('/', methods=['GET', 'POST'])
def index():
    # カテゴリデータの読み込み
    sub_categories_data = category_handler.load_sub_categories()
    error_message = None
    
    if request.method == 'POST':
        files = request.files.getlist('photo')
        
        # フォームデータの取得
        grade = request.form.get('grade')
        class_num = request.form.get('class_num')
        student_num = request.form.get('student_num')
        seat_num = request.form.get('seat_num')
        problem_num = request.form.get('problem_num')
        subject = request.form.get('subject')
        sub_category = request.form.get('sub_category')
        submit_button = request.form.get('submit_button')
        client_id = request.form.get('client_id')

        if not all([grade, class_num, student_num, seat_num, subject, sub_category]):
            error_message = "学年、組、番号、席番号、質問内容、小区分は必ず入力してください！"
            return render_template('qna_index.html', error=error_message, sub_categories_data=sub_categories_data)
        
        saved_filenames = []
        if files:
            for file in files:
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    now_str = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
                    unique_id = secrets.token_hex(4)
                    filename = f"{now_str}_{unique_id}_{filename}"
                    
                    filepath = os.path.join(UPLOAD_FOLDER, filename)
                    file.save(filepath)
                    saved_filenames.append(filename)

        image_path_json = json.dumps(saved_filenames) if saved_filenames else None

        if submit_button == '即時対応':
            status = 'done'
            submission_type = 'immediate'
        else:
            status = 'pending'
            submission_type = 'wait'
        
        db = get_db()
        cursor = db.cursor()
        
        cursor.execute("""
            INSERT INTO questions (grade, class_num, student_num, seat_num, problem_num, subject, sub_category, details, image_path, status, submission_type, client_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (grade, class_num, student_num, seat_num, problem_num, subject, sub_category, None, image_path_json, status, submission_type, client_id))
        
        last_id = cursor.lastrowid
        db.commit()
        db.close()
        
        if status == 'done':
            excel_handler.append_to_history(last_id)
            
        message_type = 'wait' if submission_type == 'wait' else 'immediate'
        return redirect(url_for('school_qna.thanks', question_id=last_id, message_type=message_type, submitted='true'))
        
    # テンプレート名は元の qna_index.html を使用
    return render_template('qna_index.html', error=error_message, sub_categories_data=sub_categories_data)

@school_qna_bp.route('/thanks')
def thanks():
    message_type = request.args.get('message_type', 'default')
    question_id = request.args.get('question_id', type=int)
    question_details = None
    student_name = "不明"
    if question_id:
        db = get_db()
        question_row = db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        if question_row:
            question_details = dict(question_row)
            student_name = excel_handler.get_student_name(
                question_details['grade'],
                question_details['class_num'],
                question_details['student_num']
            )
        db.close()
        
    if message_type == 'immediate':
        main_message = "以下の内容で質問を受け付けました！"
        sub_message = "質問ありがとうございます！"
    elif message_type == 'wait':
        main_message = "以下の内容で質問を受け付けました！"
        sub_message = "準備ができ次第コーチが呼びに行きます。しばらくお待ちください！"
    else:
        main_message = "受け付けました。"
        sub_message = "ありがとうございました。"
        
    return render_template('thanks.html',
                           title="受付完了",
                           main_message=main_message,
                           sub_message=sub_message,
                           question=question_details,
                           student_name=student_name)

@school_qna_bp.route('/list')
def list_view():
    if not session.get('logged_in'):
        flash('このページにアクセスするにはログインが必要です。', 'warning')
        return redirect(url_for('school_qna.login'))
        
    db = get_db()
    questions_data = db.execute("SELECT * FROM questions ORDER BY created_at DESC").fetchall()
    db.close()
    
    named_questions = excel_handler.add_names_to_questions(questions_data)
    return render_template('list.html', questions=named_questions)

@school_qna_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('school_qna.index'))
    
    if request.method == 'POST':
        password_attempt = request.form.get('password')
        try:
            with open(PASSWORD_HASH_FILE, 'r') as f:
                correct_password_hash = f.read().strip()
            if correct_password_hash and check_password_hash(correct_password_hash, password_attempt):
                session['logged_in'] = True
                flash('ログインしました。', 'success')
                return redirect(url_for('school_qna.index'))
            else:
                flash('パスワードが間違っています。', 'danger')
        except FileNotFoundError:
            flash('パスワードファイルエラー。管理者に連絡してください。', 'danger')
            
    return render_template('login.html', title="ログイン")

@school_qna_bp.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('ログアウトしました。', 'info')
    return redirect(url_for('school_qna.index'))

@school_qna_bp.route('/api/count')
def api_count():
    count = get_pending_count()
    return jsonify({'count': count})

@school_qna_bp.route('/api/mark_done/<int:question_id>', methods=['POST'])
def api_mark_done(question_id):
    db = get_db()
    db.execute("UPDATE questions SET status = 'done' WHERE id = ?", (question_id,))
    db.commit()
    db.close()
    excel_handler.append_to_history(question_id)
    new_count = get_pending_count()
    return jsonify({'success': True, 'new_count': new_count})

@school_qna_bp.route('/api/check_new_questions')
def api_check_new_questions():
    last_id = request.args.get('since_id', 0, type=int)
    client_id = request.args.get('client_id', '')
    
    db = get_db()
    new_count = db.execute(
        "SELECT COUNT(id) FROM questions WHERE id > ? AND (client_id IS NULL OR client_id != ?)", 
        (last_id, client_id)
    ).fetchone()[0]
    
    latest_question = db.execute("SELECT id FROM questions ORDER BY id DESC LIMIT 1").fetchone()
    latest_id = latest_question['id'] if latest_question else last_id
    db.close()
    
    return jsonify({
        'new_question_count': new_count,
        'latest_id': latest_id
    })

# 画像表示用 (Blueprintのstaticではなく、アップロードフォルダへのルート)
@school_qna_bp.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@school_qna_bp.route('/view_images/<int:question_id>')
def view_images(question_id):
    if not session.get('logged_in'):
        return redirect(url_for('school_qna.login'))
        
    db = get_db()
    question = db.execute("SELECT image_path FROM questions WHERE id = ?", (question_id,)).fetchone()
    db.close()

    image_files = []
    if question and question['image_path']:
        try:
            image_files = json.loads(question['image_path'])
        except (json.JSONDecodeError, TypeError):
            image_files = [question['image_path']]
            
    return render_template('view_images.html', images=image_files, question_id=question_id)

@school_qna_bp.route('/retract_question/<int:question_id>', methods=['POST'])
def retract_question(question_id):
    db = get_db()
    question = db.execute("SELECT status, image_path FROM questions WHERE id = ?", (question_id,)).fetchone()
    if question and question['status'] == 'pending':
        db.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        # 画像削除ロジックも必要だが省略
        db.commit()
        flash('質問を撤回しました。', 'success')
    db.close()
    return redirect(url_for('school_qna.index'))

@school_qna_bp.route('/delete_selected_questions', methods=['POST'])
def delete_selected_questions():
    if not session.get('logged_in'):
        return redirect(url_for('school_qna.login'))
        
    ids = request.form.getlist('selected_ids')
    if ids:
        db = get_db()
        for qid in ids:
            db.execute("DELETE FROM questions WHERE id = ?", (qid,))
        db.commit()
        db.close()
        flash(f'{len(ids)}件の質問を削除しました。', 'success')
    return redirect(url_for('school_qna.list_view'))

@school_qna_bp.route('/change_password')
def change_password_prompt_current():
    return render_template('change_password_current.html', title="現在のパスワード")

@school_qna_bp.route('/change_password/current', methods=['POST'])
def change_password_verify_current():
    current_password = request.form.get('current_password')
    try:
        with open(PASSWORD_HASH_FILE, 'r') as f:
            correct_hash = f.read().strip()
        if check_password_hash(correct_hash, current_password):
            session['can_set_new_password_flow'] = True
            return redirect(url_for('school_qna.change_password_set_new'))
        else:
            flash('パスワードが違います。', 'danger')
    except:
        pass
    return redirect(url_for('school_qna.change_password_prompt_current'))

@school_qna_bp.route('/change_password/new', methods=['GET', 'POST'])
def change_password_set_new():
    if not session.get('can_set_new_password_flow'):
        return redirect(url_for('school_qna.change_password_prompt_current'))
        
    if request.method == 'POST':
        p1 = request.form.get('new_password1')
        p2 = request.form.get('new_password2')
        if p1 == p2 and len(p1) >= MIN_PASSWORD_LENGTH:
            with open(PASSWORD_HASH_FILE, 'w') as f:
                f.write(generate_password_hash(p1))
            session.pop('can_set_new_password_flow', None)
            flash('変更しました。再ログインしてください。', 'success')
            return redirect(url_for('school_qna.logout'))
        else:
            flash('パスワードが一致しないか短すぎます。', 'danger')
            
    return render_template('change_password_new.html', title="新しいパスワード", min_length=MIN_PASSWORD_LENGTH)