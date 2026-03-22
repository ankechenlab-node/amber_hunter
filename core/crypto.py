"""
core/crypto.py — AES-256-GCM 加密/解密 + PBKDF2 密钥派生
所有加密操作集中在此模块
"""

import os, hashlib, base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 100_000  # PBKDF2 迭代次数


def derive_key(master_password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 → 32 字节 AES-256 密钥"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    return kdf.derive(master_password.encode("utf-8"))


def encrypt_content(plaintext: bytes, key: bytes) -> tuple[bytes, bytes]:
    """
    AES-256-GCM 加密。
    返回 (ciphertext, nonce)
    - ciphertext: 密文（含 auth tag）
    - nonce: 12 字节随机数
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return ciphertext, nonce


def decrypt_content(ciphertext: bytes, key: bytes, nonce: bytes) -> bytes | None:
    """
    AES-256-GCM 解密。
    成功返回明文，失败返回 None（密钥错误或密文被篡改）
    """
    try:
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception:
        return None


def generate_salt() -> bytes:
    """生成 16 字节随机盐"""
    return os.urandom(16)


def check_password(plaintext: bytes, ciphertext: bytes, nonce: bytes, salt: bytes, password: str) -> bool:
    """用给定密码验证密文能否正确解密"""
    key = derive_key(password, salt)
    result = decrypt_content(ciphertext, key, nonce)
    return result == plaintext
