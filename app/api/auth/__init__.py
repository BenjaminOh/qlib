"""Admin authentication: JWT in httpOnly cookie + bcrypt password hashing.

Single admin account seeded from QLIB_API_ADMIN_USERNAME / QLIB_API_ADMIN_PASSWORD
on first startup. Tokens issued at /api/v1/auth/login expire after 24h.
"""

from .dependencies import get_current_user
from .routes import router as auth_router

__all__ = ["auth_router", "get_current_user"]
