"""Live trading endpoints for the /live UI page (Phase B).

Read-only views over the SQLAlchemy models populated by the Celery beat tasks.
KIS balance is *also* fetched on demand for the dashboard's "right now" panel,
but everything historical comes from the DB.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc

from ..auth import get_current_user
from ..db import (
    DailyPnL, Fill, Order, PositionSnapshot, SessionLocal, Signal, init_db,
)
from ..services.kis_client import get_kis_client

router = APIRouter(prefix="/live", tags=["live"], dependencies=[Depends(get_current_user)])


# ─── Schemas ────────────────────────────────────────────────────────


class LiveHolding(BaseModel):
    code: str
    name: str | None = None
    qty: int
    avg_price: float
    eval_price: float
    eval_value: float
    pnl: float
    pnl_pct: float


class LiveBalanceResponse(BaseModel):
    cash: float
    total_eval: float
    holdings: list[LiveHolding]
    fetched_at: datetime
    mode: str  # "real" | "paper" | "mock"


class LiveSignalRow(BaseModel):
    rank: int
    code: str
    name: str | None = None
    score: float | None = None
    as_of: date


class LiveSignalsResponse(BaseModel):
    as_of: date | None = None
    picks: list[LiveSignalRow]


class LiveOrderRow(BaseModel):
    id: int
    submitted_at: datetime
    trade_date: date
    code: str
    name: str | None = None
    side: str
    qty: int
    price: float | None = None
    status: str
    error: str | None = None
    kis_order_id: str | None = None


class LiveOrdersResponse(BaseModel):
    orders: list[LiveOrderRow]


class DailyPnLRow(BaseModel):
    trade_date: date
    starting_equity: float
    ending_equity: float
    realised_pnl: float
    unrealised_pnl: float
    fees: float


class DailyPnLResponse(BaseModel):
    rows: list[DailyPnLRow]


# ─── Endpoints ──────────────────────────────────────────────────────


@router.get("/balance", response_model=LiveBalanceResponse)
def get_balance():
    """Current KIS balance + holdings (live fetch)."""
    init_db()
    client = get_kis_client()
    snap = client.get_balance()
    return LiveBalanceResponse(
        cash=snap.cash,
        total_eval=snap.total_eval,
        holdings=[LiveHolding(
            code=h.code,
            name=None,  # filled by frontend from kr_stock_names mapping if needed
            qty=h.qty,
            avg_price=h.avg_price,
            eval_price=h.eval_price,
            eval_value=h.eval_value,
            pnl=h.pnl,
            pnl_pct=h.pnl_pct,
        ) for h in snap.holdings],
        fetched_at=datetime.utcnow(),
        mode=("mock" if client.is_mock else client.env),
    )


@router.get("/signals", response_model=LiveSignalsResponse)
def get_latest_signals():
    """Most recent stored top-K signal."""
    init_db()
    with SessionLocal() as db:
        latest = db.query(Signal.as_of).order_by(desc(Signal.as_of)).first()
        if not latest:
            return LiveSignalsResponse(as_of=None, picks=[])
        as_of = latest[0]
        rows = (db.query(Signal)
                  .filter(Signal.as_of == as_of)
                  .order_by(Signal.rank.asc())
                  .all())
        return LiveSignalsResponse(
            as_of=as_of,
            picks=[LiveSignalRow(rank=r.rank, code=r.code, name=r.name,
                                  score=r.score, as_of=r.as_of) for r in rows],
        )


@router.get("/orders", response_model=LiveOrdersResponse)
def get_orders(limit: int = Query(100, ge=1, le=500)):
    """Recent orders (newest first)."""
    init_db()
    with SessionLocal() as db:
        rows = (db.query(Order)
                  .order_by(desc(Order.submitted_at))
                  .limit(limit)
                  .all())
        return LiveOrdersResponse(orders=[LiveOrderRow(
            id=o.id,
            submitted_at=o.submitted_at,
            trade_date=o.trade_date,
            code=o.code,
            name=o.name,
            side=o.side,
            qty=o.qty,
            price=o.price,
            status=o.status,
            error=o.error,
            kis_order_id=o.kis_order_id,
        ) for o in rows])


@router.get("/pnl/daily", response_model=DailyPnLResponse)
def get_daily_pnl(days: int = Query(180, ge=1, le=730)):
    """Per-trading-day PnL roll-up for the equity chart."""
    init_db()
    cutoff = date.today() - timedelta(days=days)
    with SessionLocal() as db:
        rows = (db.query(DailyPnL)
                  .filter(DailyPnL.trade_date >= cutoff)
                  .order_by(DailyPnL.trade_date.asc())
                  .all())
        return DailyPnLResponse(rows=[DailyPnLRow(
            trade_date=r.trade_date,
            starting_equity=r.starting_equity,
            ending_equity=r.ending_equity,
            realised_pnl=r.realised_pnl,
            unrealised_pnl=r.unrealised_pnl,
            fees=r.fees,
        ) for r in rows])


@router.get("/positions/history")
def get_position_history(limit: int = Query(60, ge=1, le=365)):
    """Recent end-of-day position snapshots (raw JSON holdings)."""
    init_db()
    with SessionLocal() as db:
        snaps = (db.query(PositionSnapshot)
                   .order_by(desc(PositionSnapshot.snapshot_date))
                   .limit(limit)
                   .all())
        return {
            "snapshots": [
                {
                    "snapshot_date": s.snapshot_date.isoformat(),
                    "cash": s.cash,
                    "total_eval": s.total_eval,
                    "holdings": json.loads(s.holdings_json or "[]"),
                }
                for s in snaps
            ]
        }
