"""FastAPI application wrapping qlib for web-based backtesting."""

from contextlib import asynccontextmanager

import qlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import auth_router
from .config import settings
from .routers import backtest, data, live
from .schemas.common import HealthResponse

_qlib_initialized = False

# Placeholder that ships in config.py so a dev checkout runs without setup. It
# must never survive into a real deployment: a known jwt_secret lets anyone mint
# a valid session cookie, and a known admin_password is a working login. Both
# defaults sit in a public repo.
INSECURE_PLACEHOLDER = "CHANGE_ME_IN_PROD"


def _assert_secrets_configured() -> None:
    """Refuse to start with the shipped placeholder secrets.

    docker-compose passes these only through `env_file: .env` — they are not
    listed under `environment:` — so a single missing line in .env used to boot
    silently on the public default rather than failing. Checking at startup is
    the difference between "the deploy failed" and "the deploy succeeded and
    anyone can forge a session".

    Enforced whenever real money is reachable (`kis_env == "real"`) and, for
    jwt_secret, always — a forgeable session on the paper account still exposes
    the dashboard and the kill switch.
    """
    if settings.jwt_secret == INSECURE_PLACEHOLDER:
        raise RuntimeError(
            "QLIB_API_JWT_SECRET is still the placeholder. Anyone could forge a "
            "session cookie. Set it in .env before starting."
        )
    if settings.kis_env == "real" and settings.admin_password == INSECURE_PLACEHOLDER:
        raise RuntimeError(
            "QLIB_API_ADMIN_PASSWORD is still the placeholder while KIS_ENV=real. "
            "Set it in .env before starting."
        )


def _seed_admin_user() -> None:
    """Create the admin account on first startup. Idempotent."""
    from .auth.security import bcrypt_hash
    from .db import SessionLocal, User

    with SessionLocal() as db:
        existing = db.query(User).filter_by(username=settings.admin_username).first()
        if existing:
            return
        db.add(User(
            username=settings.admin_username,
            password_hash=bcrypt_hash(settings.admin_password),
        ))
        db.commit()
        print(f"[api] seeded admin user: {settings.admin_username}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _qlib_initialized
    # Before anything else — a misconfigured secret must stop the deploy,
    # not be discovered later.
    _assert_secrets_configured()
    qlib.init(provider_uri=settings.provider_uri, region=settings.region)
    _qlib_initialized = True
    # Bootstrap live-trading tables (idempotent)
    try:
        from .db import init_db
        init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"[api] live DB init failed: {exc}")
    # Seed admin user (idempotent — only runs first time)
    try:
        _seed_admin_user()
    except Exception as exc:  # noqa: BLE001
        print(f"[api] admin seed failed: {exc}")
    yield


app = FastAPI(
    title="Qlib Web API",
    description="Web API for qlib backtesting and simulation (Korean market focus)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")  # /login, /logout, /me — no auth required for /login
app.include_router(data.router, prefix="/api/v1")
app.include_router(backtest.router, prefix="/api/v1")
app.include_router(live.router, prefix="/api/v1")


@app.get("/api/v1/health", response_model=HealthResponse)
def health_check():
    # provider_uri deliberately omitted: this endpoint is unauthenticated, and
    # the value is a container-internal absolute path (/root/...) that also
    # advertises the process runs as root. A health check needs liveness, not
    # filesystem layout.
    return HealthResponse(
        status="ok",
        qlib_initialized=_qlib_initialized,
    )
