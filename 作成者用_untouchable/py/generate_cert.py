from OpenSSL import crypto
import os
from dotenv import load_dotenv

# --- 設定 ---
# .envファイルからIPアドレスを読み込む
# 相対パス指定に戻し、正しい階層を指定 ('..', '..')
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '管理者用_touchable', '.env')

# デバッグ用 print 文
print(f"DEBUG: .envファイルのパス: {dotenv_path}")
print(f"DEBUG: .envファイルが存在するか?: {os.path.exists(dotenv_path)}")

# .envファイルを読み込む (override=True, verbose=True)
found = load_dotenv(dotenv_path, verbose=True, override=True)
print(f"DEBUG: load_dotenv の結果 (読み込めたか?): {found}")

# ★★★ .envファイルからIPアドレスを取得 ★★★
SERVER_IP = os.getenv("SERVER_IP")
print(f"DEBUG: os.getenv(\"SERVER_IP\") の結果: {SERVER_IP}")


CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
CERT_DIR = "certs" # 相対的なフォルダ名

def generate_self_signed_cert():
    """
    自己署名のSSL証明書と秘密鍵を生成する関数
    """
    if not SERVER_IP:
        print("="*60)
        print(" エラー: .envファイルに SERVER_IP が設定されていません。")
        print(" 例: SERVER_IP=\"192.168.1.225\"")
        print("="*60)
        return

    print(f"IPアドレス '{SERVER_IP}' の証明書を生成します...")

    # ▼▼▼ certs ディレクトリの絶対パスを計算 ▼▼▼
    # スクリプトファイル(__file__)があるディレクトリを取得
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # その1つ上の階層 (作成者用_untouchable) にある certs フォルダを指定
    certs_abs_dir = os.path.join(script_dir, '..', CERT_DIR)
    print(f"DEBUG: 証明書フォルダの絶対パス: {certs_abs_dir}") # デバッグ用に追加

    # certsディレクトリがなければ絶対パスで作成
    if not os.path.exists(certs_abs_dir):
        try:
            print(f"DEBUG: {certs_abs_dir} を作成します。") # デバッグ用に追加
            os.makedirs(certs_abs_dir)
        except Exception as e:
            print("="*60)
            print(f" エラー: certs フォルダの作成中に問題が発生しました。")
            print(f" 場所: {certs_abs_dir}")
            print(f" 詳細: {e}")
            print(" フォルダへの書き込み権限があるか確認してください。")
            print("="*60)
            return


    # 秘密鍵を生成
    key = crypto.PKey()
    key.generate_key(crypto.TYPE_RSA, 2048)

    # 証明書リクエストを生成
    cert = crypto.X509()
    cert.get_subject().C = "JP"
    cert.get_subject().ST = "Tokyo"
    cert.get_subject().L = "Chiyoda-ku"
    cert.get_subject().O = "My Private CA"
    cert.get_subject().CN = SERVER_IP
    cert.set_serial_number(1000)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(10*365*24*60*60) # 10年間有効
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(key)

    cert.add_extensions([
        crypto.X509Extension(
            b"subjectAltName", False, f"IP:{SERVER_IP}".encode()
        )
    ])

    cert.sign(key, 'sha256')

    # ファイルに書き出し (絶対パスを使用)
    cert_path = os.path.join(certs_abs_dir, CERT_FILE)
    key_path = os.path.join(certs_abs_dir, KEY_FILE)
    print(f"DEBUG: 証明書ファイルパス: {cert_path}") # デバッグ用に追加
    print(f"DEBUG: 秘密鍵ファイルパス: {key_path}") # デバッグ用に追加

    try: # ファイル書き込みエラーを捕捉
        with open(cert_path, "wt", encoding='utf-8') as f: # encoding='utf-8' を追加
            f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode("utf-8"))
        with open(key_path, "wt", encoding='utf-8') as f: # encoding='utf-8' を追加
            f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, key).decode("utf-8"))

        print(f"証明書 '{cert_path}' と秘密鍵 '{key_path}' を生成しました。")

    except Exception as e: # 書き込みエラー時のメッセージを追加
        print("="*60)
        print(f" エラー: ファイルの書き込み中に問題が発生しました。")
        print(f" 場所: {certs_abs_dir}")
        print(f" 詳細: {e}")
        print(" フォルダへの書き込み権限があるか確認してください。")
        print("="*60)
        return # エラーがあったらここで終了

    print("サーバーを再起動してください。")

if __name__ == '__main__':
    try:
        from OpenSSL import crypto
        from dotenv import load_dotenv
    except ImportError:
        print("="*60)
        print(" エラー: 必要なライブラリが見つかりません。")
        print(" 以下のコマンドを実行してインストールしてください:")
        print(" pip install pyOpenSSL python-dotenv")
        print("="*60)
    else:
        # スクリプト実行時に generate_self_signed_cert を呼び出す
        generate_self_signed_cert()