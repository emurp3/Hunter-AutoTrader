from __future__ import annotations

import os

from passlib.context import CryptContext

from app.auth.models import UserInDB

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _b(plain: str) -> bytes:
    """Bcrypt hard limit is 72 bytes. Pre-truncate so the C library never sees more."""
    return plain.encode("utf-8")[:72]


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(_b(plain), hashed)


def hash_password(plain: str) -> str:
    return _pwd.hash(_b(plain))


def _build_db() -> dict[str, UserInDB]:
    admin_pw  = os.getenv("HUNTER_ADMIN_PASSWORD", "hunter-admin-2024")
    owner_pw  = os.getenv("HUNTER_OWNER_PASSWORD", "Em252525!!")
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


# Built once at process startup; admin password is read from env at that time.
_USERS: dict[str, UserInDB] = _build_db()


def get_user(username: str) -> UserInDB | None:
    return _USERS.get(username)


def authenticate_user(username: str, password: str) -> UserInDB | None:
    user = get_user(username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user
