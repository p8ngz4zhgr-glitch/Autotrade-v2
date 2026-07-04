# ═══════════════════════════════════════════════════════════
# SECURITY — AES API Encryption and Decryption
# ═══════════════════════════════════════════════════════════
import os
import base64
from cryptography.fernet import Fernet

KEY = os.getenv("ENCRYPTION_KEY", "").encode()

if not KEY:
    # Fallback key chỉ dùng khi dev hoặc chạy thử nghiệm, khuyên dùng thực tế set ENCRYPTION_KEY
    KEY = base64.b64encode(b"SignalBotV6_AES_SecKey_32bytes_Long!")

cipher_suite = Fernet(KEY)

def encrypt_api_secret(plain_secret: str) -> str:
    """Mã hóa API Secret trước khi lưu vào Database"""
    return cipher_suite.encrypt(plain_secret.encode()).decode()

def decrypt_api_secret(encrypted_secret: str) -> str:
    """Giải mã API Secret khi Worker cần dùng để đặt lệnh"""
    return cipher_suite.decrypt(encrypted_secret.encode()).decode()
