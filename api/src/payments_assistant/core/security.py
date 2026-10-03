"""Token and password primitives shared by invites, owner sessions, and API keys.

Tokens are high-entropy random strings; only their SHA-256 is stored, so a DB leak can't be
replayed. SHA-256 (not a slow hash) is fine precisely because the tokens are already 192+ bits.
Passwords are low-entropy, so they get argon2id.
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
