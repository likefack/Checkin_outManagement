# 必要な道具 (ライブラリ) を読み込む
import os
import sqlite3
import datetime
import subprocess
import json
from flask import Flask, Response, render_template, request, redirect, url_for, jsonify, send_from_directory, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
from . import excel_handler
from . import category_handler
from .excel_handler import GRADE_DISPLAY_MAP
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging

# --- パス定義 ---
SYSTEM_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_DIR = os.path.dirname(SYSTEM_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT_DIR, 'data_可触部')

# --- Flaskアプリケーションの基本設定 --
app = Flask(__name__, 
            static_folder=os.path.join(SYSTEM_DIR, 'static'), 
            template_folder=os.path.join(SYSTEM_DIR, 'templates'))
app.secret_key = secrets.token_hex(16)

@app.template_filter('fromjson')
def fromjson_filter(value):
    if value is None:
        return []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        # 古い形式の単一文字列の場合、リストに変換して返す
        if isinstance(value, str) and value.strip():
            return [value]
        return []

# --- 設定値の定義 ---
app.config['UPLOAD_FOLDER'] = os.path.join(DATA_DIR, 'uploads')
app.config['DATABASE'] = os.path.join(SYSTEM_DIR, 'questions.db')
PASSWORD_HASH_FILE = os.path.join(SYSTEM_DIR, 'admin_pass.hash')
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'heic', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# --- 起動時の初期化処理 ---
SUB_CATEGORIES = category_handler.load_sub_categories()

def initialize_password():
    if not os.path.exists(PASSWORD_HASH_FILE):
        print(f"'{PASSWORD_HASH_FILE}' が見つかりません。初期パスワード 'koberyukoku' で作成します。")
        hashed_password = generate_password_hash('koberyukoku')
        with open(PASSWORD_HASH_FILE, 'w') as f:
            f.write(hashed_password)

initialize_password()

# --- よく使う便利機能 (ヘルパー関数) ---
def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def get_pending_count():
    db = get_db()
    try:
        count = db.execute("SELECT COUNT(id) FROM questions WHERE status = 'pending'").fetchone()[0]
    except (sqlite3.Error, TypeError):
        count = 0
    db.close()
    return count

def clear_questions_table():
    print("実行中: 19:30 の質問リセット処理...")
    db_path = app.config['DATABASE']
    upload_path = app.config['UPLOAD_FOLDER']
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT image_path FROM questions WHERE image_path IS NOT NULL AND image_path != ''")
        image_paths_to_delete = cursor.fetchall()
        cursor.execute("DELETE FROM questions")
        conn.commit()
        
        deleted_image_count = 0
        for row in image_paths_to_delete:
            try:
                filenames = json.loads(row['image_path'])
                for filename in filenames:
                    if not filename: continue
                    filepath = os.path.join(upload_path, filename)
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        deleted_image_count += 1
            except (json.JSONDecodeError, TypeError):
                filename = row['image_path']
                if filename:
                    filepath = os.path.join(upload_path, filename)
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        deleted_image_count += 1
            except Exception as e:
                print(f"  - 🚨 画像削除エラー: {e}")
        print(f"完了: 質問リセット処理が完了しました。{deleted_image_count}件の画像を削除。")
    except sqlite3.Error as e:
        print(f"🚨 データベースエラー (リセット時): {e}")
    finally:
        if conn:
            conn.close()

@app.context_processor
def inject_global_vars():
    """全てのテンプレートで共通の変数が使えるようにする"""
    return dict(
        is_logged_in=session.get('logged_in', False),
        GRADE_DISPLAY_MAP=GRADE_DISPLAY_MAP
    )

# --- ルーティング ---
@app.route('/', methods=['GET', 'POST'])
def index():
    sub_categories_data = SUB_CATEGORIES
    error_message = None
    if request.method == 'POST':
        files = request.files.getlist('photo')

        grade = request.form.get('grade')
        class_num = request.form.get('class_num')
        student_num = request.form.get('student_num')
        seat_num = request.form.get('seat_num')
        problem_num = request.form.get('problem_num')
        subject = request.form.get('subject')
        sub_category = request.form.get('sub_category')
        submit_button = request.form.get('submit_button')
        client_id = request.form.get('client_id') # ★★★ 変更点1: client_id をフォームから取得 ★★★
        
        if not all([grade, class_num, student_num, seat_num, subject, sub_category]):
            error_message = "学年、組、番号、席番号、質問内容、小区分は必ず入力してください！"
            return render_template('index.html', error=error_message, sub_categories_data=sub_categories_data)
        
        saved_filenames = []
        if files:
            for file in files:
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    now_str = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
                    unique_id = secrets.token_hex(4)
                    filename = f"{now_str}_{unique_id}_{filename}"
                    
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
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
        
        # ★★★ 変更点2: INSERT文に client_id を追加 ★★★
        cursor.execute("""
            INSERT INTO questions (grade, class_num, student_num, seat_num, problem_num, subject, sub_category, details, image_path, status, submission_type, client_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (grade, class_num, student_num, seat_num, problem_num, subject, sub_category, None, image_path_json, status, submission_type, client_id))
        
        last_id = cursor.lastrowid
        db.commit()
        db.close()
        
        if status == 'done':
            excel_handler.append_to_history(last_id)
            
        if submission_type == 'wait':
            message_type = 'wait'
        else:
            message_type = 'immediate'
        return redirect(url_for('thanks', question_id=last_id, message_type=message_type, submitted='true'))
        
    return render_template('index.html', error=error_message, sub_categories_data=sub_categories_data)


@app.route('/thanks')
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
        page_title = "受付完了！"
        main_message = "以下の内容で質問を受け付けました！"
        sub_message = "質問ありがとうございます！"
    elif message_type == 'wait':
        page_title = "受付完了！"
        main_message = "以下の内容で質問を受け付けました！"
        sub_message = "準備ができ次第コーチが呼びに行きます。しばらくお待ちください！"
    else:
        page_title = "受付完了"
        main_message = "受け付けました。"
        sub_message = "ありがとうございました。"
    return render_template('thanks.html',
                           title=page_title,
                           main_message=main_message,
                           sub_message=sub_message,
                           question=question_details,
                           student_name=student_name)

@app.route('/list')
def list_view():
    if not session.get('logged_in'):
        flash('このページにアクセスするにはログインが必要です。', 'warning')
        return redirect(url_for('login'))
    db = get_db()
    questions_data = db.execute("SELECT * FROM questions ORDER BY created_at DESC").fetchall()
    db.close()
    named_questions = excel_handler.add_names_to_questions(questions_data)
    return render_template('list.html', questions=named_questions)

@app.route('/images/<int:question_id>')
def view_images(question_id):
    if not session.get('logged_in'):
        flash('このページにアクセスするにはログインが必要です。', 'warning')
        return redirect(url_for('login'))
        
    db = get_db()
    question = db.execute("SELECT image_path FROM questions WHERE id = ?", (question_id,)).fetchone()
    db.close()

    image_files = []
    if question and question['image_path']:
        try:
            image_files = json.loads(question['image_path'])
        except (json.JSONDecodeError, TypeError):
            image_files = [question['image_path']]
            
    if not image_files:
        return "写真が見つかりません。", 404
        
    return render_template('view_images.html', images=image_files, question_id=question_id)

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/count')
def api_count():
    count = get_pending_count()
    return jsonify({'count': count})

@app.route('/api/sub_categories/<subject>')
def api_sub_categories(subject):
    return jsonify(SUB_CATEGORIES.get(subject, []))

@app.route('/api/check_new_questions')
def api_check_new_questions():
    last_id = request.args.get('since_id', 0, type=int)
    client_id = request.args.get('client_id', '') # ★★★ 変更点3: client_id を受け取る ★★★
    
    db = get_db()
    # ★★★ 変更点4: 自分のclient_idが付いた質問は除外してカウント ★★★
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

@app.route('/api/mark_done/<int:question_id>', methods=['POST'])
def api_mark_done(question_id):
    db = get_db()
    db.execute("UPDATE questions SET status = 'done' WHERE id = ?", (question_id,))
    db.commit()
    db.close()
    excel_handler.append_to_history(question_id)
    new_count = get_pending_count()
    return jsonify({'success': True, 'new_count': new_count})

@app.route('/icon/R.svg')
def app_icon():
    svg_content = f"""
    <svg width="180" height="180" viewBox="0 0 180 180" xmlns="http://www.w3.org/2000/svg">
      <rect width="180" height="180" rx="30" fill="#007bff"/>
      <text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" 
            font-family="sans-serif" font-size="120" font-weight="bold" fill="white">
        R
      </text>
    </svg>
    """
    return Response(svg_content, mimetype='image/svg+xml')

@app.route('/delete_selected_questions', methods=['POST'])
def delete_selected_questions():
    ids_to_delete = request.form.getlist('selected_ids')
    if not ids_to_delete:
        return redirect(url_for('list_view'))
    db = None
    try:
        db = get_db()
        cursor = db.cursor()
        for question_id_str in ids_to_delete:
            question_id = int(question_id_str)
            cursor.execute("SELECT image_path FROM questions WHERE id = ?", (question_id,))
            question_data = cursor.fetchone()
            cursor.execute("DELETE FROM questions WHERE id = ?", (question_id,))
            if question_data and question_data['image_path']:
                try:
                    filenames = json.loads(question_data['image_path'])
                    for filename in filenames:
                        if not filename: continue
                        image_file_to_delete = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        if os.path.exists(image_file_to_delete):
                            os.remove(image_file_to_delete)
                except (json.JSONDecodeError, TypeError):
                    filename = question_data['image_path']
                    if filename:
                        image_file_to_delete = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        if os.path.exists(image_file_to_delete):
                            os.remove(image_file_to_delete)
        db.commit()
    except sqlite3.Error as e:
        print(f"🚨 データベースエラー (一括削除時): {e}")
    finally:
        if db:
            db.close()
    return redirect(url_for('list_view'))

@app.route('/retract_question/<int:question_id>', methods=['POST'])
def retract_question(question_id):
    db = None
    try:
        db = get_db()
        cursor = db.cursor()
        question_to_retract = cursor.execute("SELECT image_path, status FROM questions WHERE id = ?", (question_id,)).fetchone()
        if question_to_retract and question_to_retract['status'] == 'pending':
            cursor.execute("DELETE FROM questions WHERE id = ?", (question_id,))
            if question_to_retract['image_path']:
                try:
                    filenames = json.loads(question_to_retract['image_path'])
                    for filename in filenames:
                        if not filename: continue
                        image_file_to_delete = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        if os.path.exists(image_file_to_delete):
                            os.remove(image_file_to_delete)
                except (json.JSONDecodeError, TypeError):
                    filename = question_to_retract['image_path']
                    if filename:
                        image_file_to_delete = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        if os.path.exists(image_file_to_delete):
                            os.remove(image_file_to_delete)
            db.commit()
            flash(f'受付ID {question_id} の質問を撤回しました。', 'success')
        elif question_to_retract:
            flash(f'受付ID {question_id} の質問は既に処理されているため、撤回できません。', 'info')
        else:
            flash(f'受付ID {question_id} の質問が見つかりませんでした。', 'danger')
    except sqlite3.Error as e:
        print(f"🚨 データベースエラー (質問撤回時 ID: {question_id}): {e}")
        if db: db.rollback()
    finally:
        if db:
            db.close()
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        flash('既にログインしています。', 'info')
        return redirect(url_for('index'))
    
    next_url = request.args.get('next')

    if request.method == 'POST':
        password_attempt = request.form.get('password')
        try:
            with open(PASSWORD_HASH_FILE, 'r') as f:
                correct_password_hash = f.read().strip()
            if correct_password_hash and check_password_hash(correct_password_hash, password_attempt):
                session['logged_in'] = True
                flash('ログインしました。', 'success')
                
                if next_url:
                    return redirect(next_url)
                else:
                    return redirect(url_for('index'))
            else:
                flash('パスワードが間違っています。', 'danger')
        except FileNotFoundError:
            flash('パスワードファイルが見つかりません。管理者に連絡してください。', 'danger')
            initialize_password()
        except Exception as e:
            flash(f'ログイン処理中にエラーが発生しました: {e}', 'danger')

    return render_template('login.html', title="ログイン", next=next_url)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('ログアウトしました。', 'info')
    return redirect(url_for('index'))

def _set_file_attribute_windows(filepath, make_readonly=True):
    try:
        if not os.path.exists(filepath):
            return True
        action = "+R" if make_readonly else "-R"
        subprocess.run(
            ["attrib", action, filepath], 
            check=True, shell=True, capture_output=True, text=True, encoding="cp932"
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  - 🚨 ファイル属性変更エラー ({os.path.basename(filepath)}): {e}")
        return False
    except Exception as e:
        print(f"  - 🚨 ファイル属性変更中の予期せぬエラー ({os.path.basename(filepath)}): {e}")
        return False

MIN_PASSWORD_LENGTH = 8

def is_password_valid(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        flash(f'新しいパスワードは{MIN_PASSWORD_LENGTH}文字以上で入力してください。', 'danger')
        return False
    return True

def get_current_password_hash():
    try:
        with open(PASSWORD_HASH_FILE, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        initialize_password()
        with open(PASSWORD_HASH_FILE, 'r') as f:
            return f.read().strip()

def save_new_password_hash(new_password):
    made_writable = False
    try:
        if _set_file_attribute_windows(PASSWORD_HASH_FILE, make_readonly=False):
            made_writable = True
        else:
            print(f"致命的エラー: {PASSWORD_HASH_FILE} を書き込み可能にできませんでした。処理を中断します。")
            return False

        new_hash = generate_password_hash(new_password)
        with open(PASSWORD_HASH_FILE, 'w') as f:
            f.write(new_hash)
        print("パスワードハッシュを正常に保存しました。")
        return True
    except Exception as e:
        print(f"🚨 パスワードハッシュの保存に失敗: {e}")
        return False
    finally:
        if made_writable:
            _set_file_attribute_windows(PASSWORD_HASH_FILE, make_readonly=True)


@app.route('/change_password/current', methods=['GET', 'POST'])
def change_password_prompt_current():
    if not session.get('logged_in'):
        flash('パスワード変更の前にログインしてください。', 'warning')
        return redirect(url_for('login', next=url_for('change_password_prompt_current')))

    if request.method == 'POST':
        current_password_attempt = request.form.get('current_password')
        correct_password_hash = get_current_password_hash()

        if correct_password_hash and check_password_hash(correct_password_hash, current_password_attempt):
            session['can_set_new_password_flow'] = True 
            return redirect(url_for('change_password_set_new'))
        else:
            flash('現在のパスワードが間違っています。', 'danger')
    
    return render_template('change_password_current.html', title="現在のパスワードの確認")

@app.route('/change_password/new', methods=['GET', 'POST'])
def change_password_set_new():
    if not session.get('logged_in') or not session.get('can_set_new_password_flow'):
        flash('不正なアクセスです。再度ログインまたは現在のパスワード確認からやり直してください。', 'danger')
        return redirect(url_for('change_password_prompt_current')) 

    if request.method == 'POST':
        new_password1 = request.form.get('new_password1')
        new_password2 = request.form.get('new_password2')

        if new_password1 != new_password2:
            flash('新しいパスワードと確認用パスワードが一致しません。', 'danger')
        elif not is_password_valid(new_password1):
            pass 
        else:
            if save_new_password_hash(new_password1):
                session.pop('can_set_new_password_flow', None) 
                flash('パスワードが正常に変更されました。新しいパスワードで再度ログインしてください。', 'success')
                return redirect(url_for('logout')) 
            else:
                flash('パスワードの変更に失敗しました。管理者に連絡してください。', 'danger')

    return render_template('change_password_new.html', 
                           title="新しいパスワードの設定", 
                           min_length=MIN_PASSWORD_LENGTH)

# --- スケジューラーの設定 ---
def start_scheduler():
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=clear_questions_table,
        trigger="cron",
        hour=19,
        minute=30,
        timezone='Asia/Tokyo'
    )
    scheduler.start()
    print("⏰ スケジューラーを起動しました。毎日 19:30 にリセット処理を実行します。")
    atexit.register(lambda: scheduler.shutdown())

# アプリケーションの初期化とスケジューラーの起動
start_scheduler()