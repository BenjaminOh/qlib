"""SQLAlchemy engine + session factory for live-trading state.

Picks SQLite locally (`sqlite:///./app/api/db/live.sqlite`) and PostgreSQL
on production via `QLIB_API_LIVE_DB_URL`.
"""

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from ..config import settings

log = logging.getLogger(__name__)

_DB_URL = settings.live_db_url

# SQLite needs check_same_thread=False because Celery workers + FastAPI share connections.
_engine_kwargs = {}
if _DB_URL.startswith("sqlite"):
    Path(_DB_URL.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(_DB_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def init_db() -> None:
    """Create tables if they don't exist. Called once at API/worker startup.

    Set `QLIB_API_LIVE_DB_RESET=1` to drop and recreate all tables — used as a
    one-shot migration when schema changes (e.g. adding the per-strategy split
    that introduces composite uniques on snapshot_date / trade_date).
    """
    from . import models  # noqa: F401 — register on Base.metadata
    if os.environ.get("QLIB_API_LIVE_DB_RESET") == "1":
        log.warning("init_db: QLIB_API_LIVE_DB_RESET=1 → dropping all tables")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
