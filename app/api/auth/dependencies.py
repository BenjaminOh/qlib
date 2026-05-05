"""FastAPI dependency: extract session cookie → decode JWT → load User."""

from fastapi import Cookie, HTTPException, status
from jose import JWTError

from ..config import settings
from ..db import SessionLocal, User
from .security import decode_access_token


def get_current_user(qlib_session: str | None = Cookie(default=None)) -> User:
    """Returns the authenticated User or raises 401.

    The cookie name (`qlib_session`) is fixed; FastAPI maps the parameter name
    `qlib_session` to that cookie. Parameter alias would let us stay flexible
    if `settings.session_cookie_name` ever differs, but the simpler form is
    fine for a single deployed instance.
    """
    if not qlib_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_access_token(qlib_session)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")

    with SessionLocal() as db:
        user = db.query(User).filter_by(username=username).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")
        return user
