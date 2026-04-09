from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.auth.jwt import create_access_token, get_current_user
from app.auth.models import Token, UserInDB
from app.auth.users import authenticate_user

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE  = "access_token"
_SECURE  = True  # set HUNTER_COOKIE_SECURE=false in local .env to disable


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login", response_model=Token)
def login(body: LoginRequest, response: Response) -> Token:
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_access_token(user.username, user.role)
    import os
    secure = os.getenv("HUNTER_COOKIE_SECURE", "true").lower() != "false"
    response.set_cookie(
        key=_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=8 * 3600,
    )
    return Token(access_token=token, token_type="bearer", role=user.role)


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(key=_COOKIE, samesite="lax")
    return {"message": "Logged out"}


@router.get("/me")
def me(current_user: UserInDB = Depends(get_current_user)) -> dict:
    return {"username": current_user.username, "role": current_user.role}
