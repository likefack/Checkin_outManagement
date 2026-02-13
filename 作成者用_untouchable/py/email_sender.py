import smtplib
from email.mime.text import MIMEText
from email.header import Header
import os
import threading
import logging
import sqlite3
import database # DBパスを利用するためにインポート

logger = logging.getLogger(__name__)

def _send_smtp_raw(recipient_email, subject, body):
    """
    実際にSMTPサーバーに接続してメールを送信する内部関数。
    成功すればTrue, 失敗すれば例外をraiseする。
    """
    gmail_user = os.getenv('GMAIL_USER')
    gmail_pass = os.getenv('GMAIL_PASS')
    sender_name = os.getenv('SENDER_NAME', '入退室管理システム')

    if not gmail_user or not gmail_pass:
        raise ValueError("Gmailのユーザー名またはパスワードが設定されていません。")

    # メッセージの組み立て
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = f'"{Header(sender_name, "utf-8")}" <{gmail_user}>'
    msg['To'] = recipient_email

    # GmailのSMTPサーバーに接続して送信
    server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) # タイムアウトを設定
    server.login(gmail_user, gmail_pass)
    server.send_message(msg)
    server.quit()
    return True

def _queue_email(recipient_email, subject, body):
    """送信に失敗したメールをDBのキューに保存する"""
    try:
        conn = sqlite3.connect(database.DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO email_queue (recipient, subject, body) VALUES (?, ?, ?)",
            (recipient_email, subject, body)
        )
        conn.commit()
        conn.close()
        logger.info(f"[メール保留] 送信失敗のためキューに保存しました - 宛先: {recipient_email}")
    except Exception as e:
        logger.error(f"[メール保留] キュー保存にも失敗しました: {e}")

def send_email(recipient_email, subject, body):
    """
    指定された宛先にメールを送信する関数（バックグラウンドで実行される）。
    """
    # .envファイルから設定を読み込む
    gmail_user = os.getenv('GMAIL_USER')
    gmail_pass = os.getenv('GMAIL_PASS')
    sender_name = os.getenv('SENDER_NAME', '入退室管理システム')

    try:
        _send_smtp_raw(recipient_email, subject, body)
        logger.info(f"[メール送信] 成功 - 宛先: {recipient_email}")
    except Exception as e:
        logger.warning(f"[メール送信] 一時的な失敗（オフラインの可能性） - 宛先: {recipient_email}, エラー: {e}")
        # 失敗したらキューに保存
        _queue_email(recipient_email, subject, body)

def retry_queued_emails():
    """
    保留中のメールを再送する関数。定期実行されることを想定。
    """
    try:
        conn = sqlite3.connect(database.DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 保留中のメールを取得
        emails = cursor.execute("SELECT id, recipient, subject, body FROM email_queue ORDER BY id ASC LIMIT 5").fetchall()
        
        if not emails:
            conn.close()
            return

        logger.info(f"[メール再送] {len(emails)} 件の保留メールの再送を試みます...")
        
        ids_to_delete = []
        for email in emails:
            try:
                # 送信試行
                _send_smtp_raw(email['recipient'], email['subject'], email['body'])
                logger.info(f"[メール再送] 成功 - ID: {email['id']}, 宛先: {email['recipient']}")
                ids_to_delete.append(email['id'])
            except Exception as e:
                logger.warning(f"[メール再送] 失敗 - ID: {email['id']}, エラー: {e}")
                # 接続エラーなどの場合は、ループを抜けて次回の実行を待つ（無駄な試行を防ぐ）
                break
        
        # 成功したものを削除
        if ids_to_delete:
            placeholders = ','.join('?' * len(ids_to_delete))
            conn.execute(f"DELETE FROM email_queue WHERE id IN ({placeholders})", ids_to_delete)
            conn.commit()
            
        conn.close()
    except Exception as e:
        logger.error(f"[メール再送処理] エラー: {e}")

def send_email_async(recipient_email, subject, body):
    """
    メール送信を非同期（別スレッド）で実行するためのラッパー関数。
    """
    # recipient_emailが空かNoneの場合は何もしない
    if not recipient_email:
        logger.info("メール宛先が空のため、送信をスキップしました。")
        return
        
    # スレッドを作成して、send_email関数をバックグラウンドで実行
    email_thread = threading.Thread(target=send_email, args=(recipient_email, subject, body))
    email_thread.start()