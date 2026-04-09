from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException, status
from jose import JWTError, jwt

from app.auth.models import UserInDB
from app.auth.users import get_user

_SECRET = os.getenv("HUNTER_JWT_SECRET", "hunter-dev-secret-change-in-production")
_ALGO   = "HS256"
_ADMIN_TTL_H = int(os.getenv("HUNTER_ADMIN_TOKEN_TTL_HOURS", "8"))
_GUEST_TTL_H = int(os.getenv("HUNTER_GUEST_TOKEN_TTL_HOURS", "2"))


def create_access_token(username: str, role: str) -> str:
    ttl  = _ADMIN_TTL_H if role == "admin" else _GUEST_TTL_H
    exp  = datetime.now(timezone.utc) + timedelta(hours=ttl)
    data = {"sub": username, "role": role, "exp": exp}
    return jwt.encode(data, _SECRET, algorithm=_ALGO)


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGO])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> UserInDB:
    """Accepts HTTP-only cookie (browser) or Authorization: Bearer (API / curl)."""
    token: str | None = None
    if access_token:
        token = access_token
    elif authorization and authorization.startswith("Bearer "):
        candidate = authorization[7:]
        # Don't treat the static worker token as a JWT user token
        worker_token = os.getenv("HUNTER_WORKER_TOKEN", "")
        if not worker_token or candidate != worker_token:
            token = candidate
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    payload  = _decode(token)
    username = payload.get("sub")
    role     = payload.get("role")
    if not username or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    user = get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def require_admin(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def require_worker(authorization: str | None = Header(default=None)) -> dict:
    """Service-to-service auth for the HVA worker — static bearer token."""
    worker_token = os.getenv("HUNTER_WORKER_TOKEN", "")
    if not worker_token:
        # Fail open in dev (no token configured), fail closed in production
        if os.getenv("ENVIRONMENT", "development").lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Worker token not configured",
            )
        return {"role": "worker"}
    if not authorization or authorization != f"Bearer {worker_token}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid worker token required",
        )
    return {"role": "worker"}
