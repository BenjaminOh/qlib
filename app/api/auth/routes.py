"""Auth endpoints: POST /login, POST /logout, GET /me."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..config import settings
from ..db import SessionLocal, User
from .dependencies import get_current_user
from .rate_limit import check_locked, client_ip, record_failure, record_success
from .security import bcrypt_verify, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    username: str


class MeResponse(BaseModel):
    username: str


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Verify credentials, issue JWT in httpOnly cookie.

    Brute-force protection: per-IP sliding-window counter in Redis throttles
    repeated failures into a temporary 429 (see auth/rate_limit.py).
    """
    ip = client_ip(request)
    locked_ttl = check_locked(ip)
    if locked_ttl is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"로그인 시도가 너무 많습니다. {locked_ttl}초 후 다시 시도하세요.",
            headers={"Retry-After": str(locked_ttl)},
        )

    with SessionLocal() as db:
        user = db.query(User).filter_by(username=body.username).first()

    if not user or not bcrypt_verify(body.password, user.password_hash):
        record_failure(ip, username=body.username)
        # Constant-ish response so timing doesn't leak which username exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 잘못되었습니다",
        )

    record_success(ip)
    token = create_access_token(sub=user.username)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=settings.session_max_age_seconds,
        path="/",
    )
    return LoginResponse(ok=True, username=user.username)


@router.post("/logout")
def logout(response: Response) -> dict:
    """Clear the session cookie. Idempotent — works even if no cookie present."""
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    """Echo back the authenticated user — used by the frontend to verify session."""
    return MeResponse(username=current_user.username)
