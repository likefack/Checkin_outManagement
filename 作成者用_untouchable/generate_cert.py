from OpenSSL import crypto
import os
from dotenv import load_dotenv

# --- 設定 ---
# .envファイルからIPアドレスを読み込む
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '管理者用_touchable', '.env')
load_dotenv(dotenv_path)

#  .envファイルからIPアドレスを取得 
SERVER_IP = os.getenv("SERVER_IP")

CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
# 修正: スクリプトのあるディレクトリを基準に 'certs' フォルダのパスを決定する
CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")

def generate_self_signed_cert():
    """
    自己署名のSSL証明書と秘密鍵を生成する関数
    """
    if not SERVER_IP:
        print("="*60)
        print(" エラー: .envファイルに SERVER_IP が設定されていません。")
        print(" 例: SERVER_IP=\"192.168.1.225\"")
        print("="*60)
        exit()

    print(f"IPアドレス '{SERVER_IP}' の証明書を生成します...")

    # certsディレクトリがなければ作成
    if not os.path.exists(CERT_DIR):
        os.makedirs(CERT_DIR)

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
    
    # 修正: IP:127.0.0.1 を追加
    cert.add_extensions([
        crypto.X509Extension(
            b"subjectAltName", False, f"IP:{SERVER_IP}, IP:127.0.0.1".encode()
        )
    ])

    cert.sign(key, 'sha256')

    # ファイルに書き出し
    cert_path = os.path.join(CERT_DIR, CERT_FILE)
    key_path = os.path.join(CERT_DIR, KEY_FILE)

    with open(cert_path, "wt") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode("utf-8"))
    with open(key_path, "wt") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, key).decode("utf-8"))

    print(f"証明書 '{cert_path}' と秘密鍵 '{key_path}' を生成しました。")
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
        exit()
        
    generate_self_signed_cert()