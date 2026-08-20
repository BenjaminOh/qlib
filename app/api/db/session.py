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
    _seed_default_account()


def _seed_default_account() -> None:
    """Insert the 'main' account row if absent, with MARKET on both sides.

    Idempotent, and it never touches an existing row: the seed must not be a
    back door that silently rewrites a policy an operator chose. A fresh table
    therefore reproduces exactly the pre-existing behaviour (market buy, market
    sell) rather than switching the live account to limit orders on deploy.
    """
    from .models import (BASE_QUOTE, DEFAULT_ACCOUNT_ID, ORD_TYPE_LIMIT,
                         ORD_TYPE_MARKET, TradingAccount)

    with SessionLocal() as db:
        added = False
        if db.get(TradingAccount, DEFAULT_ACCOUNT_ID) is None:
            db.add(TradingAccount(
                account_id=DEFAULT_ACCOUNT_ID,
                label="기본 계좌",
                buy_ord_type=ORD_TYPE_MARKET,
                sell_ord_type=ORD_TYPE_MARKET,
            ))
            added = True
        # cafe 계좌 — cafereal 전략이 쓰는 두 번째 실계좌의 주문 방식.
        # 기본값을 확정 정책(매수 시장가 금지 · 현재가 −3% 지정가)으로 둔다.
        # ⚠ 카페 픽은 대개 상한가라 −3% 지정가로는 거의 체결되지 않는다. 그것
        # 자체가 측정값이지만(체결률), 체결을 보고 싶으면 화면에서 시장가로 바꿀 것.
        # 안전한 쪽을 기본으로 두는 이유: 잘못 켜져도 돈이 나가지 않는다.
        if db.get(TradingAccount, "cafe") is None:
            db.add(TradingAccount(
                account_id="cafe",
                label="카페 실매매 계좌",
                buy_ord_type=ORD_TYPE_LIMIT,
                buy_base=BASE_QUOTE,
                buy_offset_pct=0.03,   # 분수. 매수는 base×(1−offset) = −3%
                buy_cancel_hhmm="15:30",
                sell_ord_type=ORD_TYPE_MARKET,
            ))
            added = True
        if not added:
            return
        try:
            db.commit()
        except Exception:  # noqa: BLE001 — another worker seeded it first
            db.rollback()
            log.info("init_db: account rows already present")
