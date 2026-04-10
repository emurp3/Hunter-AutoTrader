from __future__ import annotations

import base64
import hashlib
import hmac
import os

from app.auth.models import UserInDB

# ---------------------------------------------------------------------------
# Password hashing — pure Python stdlib (PBKDF2-SHA256 + random salt).
# Replaced passlib[bcrypt] which is broken on Python 3.14 due to an internal
# detect_wrap_bug() call that hashes a 73-byte test password, triggering a
# ValueError in the underlying _bcrypt C library regardless of any truncation.
# ---------------------------------------------------------------------------
_ITERS = 260_000  # OWASP 2024 minimum for PBKDF2-SHA256


def hash_password(plain: str) -> str:
    salt = os.urandom(32)
    key  = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _ITERS)
    return base64.b64encode(salt + key).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        raw  = base64.b64decode(hashed)
        salt = raw[:32]
        key  = raw[32:]
        test = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _ITERS)
        return hmac.compare_digest(key, test)
    except Exception:
        return False


def _build_db() -> dict[str, UserInDB]:
    admin_pw = os.getenv("HUNTER_ADMIN_PASSWORD", "hunter-admin-2024")
    owner_pw = os.getenv("HUNTER_OWNER_PASSWORD", "Em252525!!")
    return {
        "admin": UserInDB(
            username="admin",
            hashed_password=hash_password(admin_pw),
            role="admin",
        ),
        "emurp3@gmail.com": UserInDB(
            username="emurp3@gmail.com",
            hashed_password=hash_password(owner_pw),
            role="admin",
        ),
        "guest": UserInDB(
            username="guest",
            hashed_password=hash_password("guest-demo"),
            role="guest",
        ),
    }


_USERS: dict[str, UserInDB] = _build_db()


def get_user(username: str) -> UserInDB | None:
    return _USERS.get(username)


def authenticate_user(username: str, password: str) -> UserInDB | None:
    user = get_user(username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user
