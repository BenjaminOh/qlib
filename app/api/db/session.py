"""SQLAlchemy engine + session factory for live-trading state.

Picks SQLite locally (`sqlite:///./app/api/db/live.sqlite`) and PostgreSQL
on production via `QLIB_API_LIVE_DB_URL`.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
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


if _DB_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover — driver hook
        """WAL + a busy timeout so concurrent writers queue instead of erroring.

        Production runs SQLite with `--concurrency=2` and several beat slots
        now fire together (15:20 close+flow orders, 15:40 three syncs). Under
        the default rollback journal a second writer fails instantly with
        "database is locked"; WAL lets readers run during a write and
        busy_timeout makes a competing writer wait 5s instead of dying.
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def _drop_authorized() -> bool:
    # Two gates so a stray env var can't nuke prod:
    #   1. QLIB_API_LIVE_DB_RESET must equal the SQLite basename (e.g. "live.sqlite").
    #   2. A same-day token file `.allow_reset_<YYYYMMDD>` (UTC) must exist in the DB dir.
    requested = os.environ.get("QLIB_API_LIVE_DB_RESET", "").strip()
    if not requested:
        return False
    if not _DB_URL.startswith("sqlite"):
        log.warning("init_db: QLIB_API_LIVE_DB_RESET set but DB is not SQLite — ignoring")
        return False
    db_path = Path(_DB_URL.replace("sqlite:///", ""))
    if requested != db_path.name:
        log.warning(
            "init_db: QLIB_API_LIVE_DB_RESET=%r does not match DB basename %r — ignoring",
            requested, db_path.name,
        )
        return False
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    token = db_path.parent / f".allow_reset_{today}"
    if not token.exists():
        log.warning("init_db: reset requested but token %s missing — ignoring", token)
        return False
    return True


def init_db() -> None:
    """Create tables if missing. Drop is intentionally hard to trigger.

    To actually drop all tables (destructive — last resort during schema migration):
      1. Set ``QLIB_API_LIVE_DB_RESET`` to the exact SQLite basename (e.g. ``live.sqlite``).
      2. Create a same-day token file ``.allow_reset_<YYYYMMDD>`` (UTC date) in the DB dir.
    Both gates must be present, otherwise the env var is ignored and create_all runs as usual.
    """
    from . import models  # noqa: F401 — register on Base.metadata
    if _drop_authorized():
        log.warning("init_db: reset authorized → dropping all tables")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
