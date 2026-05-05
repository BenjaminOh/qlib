"""Password hashing (bcrypt) + JWT signing/decoding (HS256).

Uses the bcrypt library directly (not passlib) because passlib 1.7.4 is
incompatible with bcrypt 4.x+ — it misreads the version-detection API
and raises spurious "password cannot be longer than 72 bytes" errors,
even on short inputs. SHA-256 pre-hash sidesteps the real 72-byte limit
that bcrypt enforces (long UTF-8 passwords with multi-byte glyphs would
otherwise be rejected outright).
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import bcrypt
from jose import jwt

from ..config import settings

_ALGO = "HS256"


def _normalize(plain: str) -> bytes:
    """SHA-256 pre-hash → 64 ASCII hex bytes, always under bcrypt's 72-byte cap."""
    return sha256(plain.encode("utf-8")).hexdigest().encode("ascii")


def bcrypt_hash(plain: str) -> str:
    return bcrypt.hashpw(_normalize(plain), bcrypt.gensalt()).decode("ascii")


def bcrypt_verify(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_normalize(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed stored hash, encoding mismatch, etc. — treat as miss.
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
