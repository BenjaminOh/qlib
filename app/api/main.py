"""FastAPI application wrapping qlib for web-based backtesting."""

from contextlib import asynccontextmanager

import qlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import backtest, data, live
from .schemas.common import HealthResponse

_qlib_initialized = False


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
