import smtplib
from email.mime.text import MIMEText
from email.header import Header
import os
import threading
import logging

logger = logging.getLogger(__name__)

def send_email(recipient_email, subject, body):
    """
    指定された宛先にメールを送信する関数（バックグラウンドで実行される）。
    """
    # .envファイルから設定を読み込む
    gmail_user = os.getenv('GMAIL_USER')
    gmail_pass = os.getenv('GMAIL_PASS')
    sender_name = os.getenv('SENDER_NAME', '入退室管理システム')

    if not gmail_user or not gmail_pass:
        logger.error("メール送信エラー: Gmailのユーザー名またはパスワードが.envファイルに設定されていません。")
        return

    try:
        # メッセージの組み立て
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = Header(subject, 'utf-8')
        msg['From'] = f'"{Header(sender_name, "utf-8")}" <{gmail_user}>'
        msg['To'] = recipient_email

        # GmailのSMTPサーバーに接続して送信
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)
        server.quit()
        logger.info(f"[メール送信] 成功 - 宛先: {recipient_email}")

    except Exception as e:
        logger.error(f"[メール送信] 失敗 - 宛先: {recipient_email}, エラー: {e}", exc_info=True)

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