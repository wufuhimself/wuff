"""At-rest encryption for platform credentials (ESPN cookies today).

Key comes from WUFF_ENCRYPTION_KEY (a Fernet key — generate with
`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
Without it, a key is derived from SECRET_KEY: fine for local dev, NOT for
production — set both explicitly at deploy, and note that rotating the
derived secret orphans previously stored credentials.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get('WUFF_ENCRYPTION_KEY')
    if key:
        return Fernet(key.encode())
    secret = os.environ.get('SECRET_KEY', 'dev-only-not-a-secret')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
