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
    return HealthResponse(
        status="ok",
        qlib_initialized=_qlib_initialized,
        provider_uri=settings.provider_uri,
    )
