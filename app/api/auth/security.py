"""Password hashing (bcrypt) + JWT signing/decoding (HS256)."""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from ..config import settings

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_ALGO = "HS256"


def bcrypt_hash(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def bcrypt_verify(plain: str, hashed: str) -> bool:
    try:
        return _pwd_ctx.verify(plain, hashed)
    except Exception:  # noqa: BLE001 — passlib raises on malformed hash
        return False


def create_access_token(sub: str, expires_delta: timedelta | None = None) -> str:
    """Issue a signed JWT carrying the username in `sub`."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(seconds=settings.session_max_age_seconds)
    )
    payload: dict[str, Any] = {"sub": sub, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify signature + expiry. Raises JWTError on any failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
