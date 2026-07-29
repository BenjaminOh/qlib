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
from ..config import settings
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
    # Latest close in the qlib store — the price the next morning's buy
    # quantity is computed from (slot budget // last_close).
    last_close: float | None = None
    # Why this pick: {"summary", "metrics", "top_features"} — see signal_reasons.
    reasons: dict | None = None


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
    strategy: str
    starting_equity: float
    ending_equity: float
    realised_pnl: float
    unrealised_pnl: float
    fees: float


class DailyPnLResponse(BaseModel):
    rows: list[DailyPnLRow]
    # Per-strategy seed cash. open and close run on different starting
    # balances (real KIS paper vs DB-only simulated), so the chart must
    # normalise each line against its own seed.
    seed_cash: dict[str, float]


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
            name=h.name or _stock_name_safe(h.code),
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


def _stock_name_safe(code: str) -> str | None:
    try:
        from ..services.live_trader import _stock_name
        return _stock_name(code)
    except Exception:  # noqa: BLE001
        return None


class StockTradeRow(BaseModel):
    trade_date: date
    strategy: str
    side: str
    qty: int
    status: str
    error: str | None = None
    # Execution price. Real market orders have no recorded fill price yet
    # (no fill reconciliation), so this falls back to that day's OPEN from
    # kr_data with price_est=True.
    exec_price: float | None = None
    price_est: bool = False
    # Position state AFTER this event (running reconstruction):
    cum_qty: int | None = None
    avg_price: float | None = None
    # Valuation as of that day's close:
    day_close: float | None = None
    ret_pct: float | None = None    # (close - avg) / avg
    pnl_amt: float | None = None    # (close - avg) * cum_qty
    realized_pnl: float | None = None  # sells only: (sell - avg) * qty
    # Decision basis captured at order time: {"action","basis","summary",
    # "metrics","top_features"} — buys carry that day's signal reasons,
    # sells carry the top-K-exit snapshot.
    reasons: dict | None = None


def _price_series(code: str) -> dict[str, tuple[float | None, float | None]]:
    """{iso_date: (open, close)} from the qlib store — best effort."""
    try:
        from ..core.qlib_manager import ensure_qlib_initialized
        ensure_qlib_initialized()
        from qlib.data import D
        df = D.features([code], ["$open", "$close"], freq="day")
        if df is None or df.empty:
            return {}
        df = df.droplevel("instrument")
        return {
            ts.strftime("%Y-%m-%d"): (
                float(row["$open"]) if row["$open"] == row["$open"] else None,
                float(row["$close"]) if row["$close"] == row["$close"] else None,
            )
            for ts, row in df.iterrows()
        }
    except Exception:  # noqa: BLE001
        return {}


@router.get("/stock/{code}/trades", response_model=list[StockTradeRow])
def get_stock_trades(code: str, strategy: str = Query("open")):
    """Chronological per-stock trade history with running position state,
    point-in-time valuation and the decision basis of each order."""
    init_db()
    prices = _price_series(code)
    # Intraday fallback: today's bars only land in kr_data at the 15:45
    # refresh, so same-day rows would show no exec price / valuation all
    # trading day. For the open strategy use the KIS holding instead —
    # avg_price is the REAL average fill, eval_price the live quote.
    kis_avg: float | None = None
    kis_now: float | None = None
    if strategy == "open":
        try:
            snap = get_kis_client().get_balance()
            h = next((h for h in snap.holdings if h.code == code), None)
            if h is not None:
                kis_avg = h.avg_price or None
                kis_now = h.eval_price or None
        except Exception:  # noqa: BLE001
            pass
    with SessionLocal() as db:
        rows = (db.query(Order)
                  .filter(Order.code == code, Order.strategy == strategy)
                  .order_by(Order.trade_date.asc(), Order.id.asc())
                  .limit(200)
                  .all())
        out: list[StockTradeRow] = []
        pos_qty = 0
        pos_cost = 0.0
        for r in rows:
            reasons = None
            if r.reasons_json:
                try:
                    reasons = json.loads(r.reasons_json)
                except Exception:  # noqa: BLE001
                    pass
            d_iso = r.trade_date.isoformat()
            day_open, day_close = prices.get(d_iso, (None, None))
            if day_open is None and day_close is None:
                # Store has no bar for this date yet (intraday) — KIS fallback.
                day_open, day_close = kis_avg, kis_now

            item = StockTradeRow(
                trade_date=r.trade_date, strategy=r.strategy, side=r.side,
                qty=r.qty, status=r.status, error=r.error, reasons=reasons,
            )
            executed = r.status in ("SUBMITTED", "FILLED", "PARTIAL", "SIMULATED")
            if executed:
                px = r.price
                item.price_est = px is None
                if px is None:
                    px = day_open or day_close
                item.exec_price = px
                if px is not None:
                    if r.side == "BUY":
                        pos_qty += r.qty
                        pos_cost += r.qty * px
                    else:  # SELL
                        avg_before = (pos_cost / pos_qty) if pos_qty else 0.0
                        item.realized_pnl = (px - avg_before) * min(r.qty, pos_qty)
                        pos_cost -= avg_before * min(r.qty, pos_qty)
                        pos_qty = max(pos_qty - r.qty, 0)
                item.cum_qty = pos_qty
                item.avg_price = (pos_cost / pos_qty) if pos_qty else None
                item.day_close = day_close
                if day_close is not None and item.avg_price:
                    item.ret_pct = day_close / item.avg_price - 1
                    item.pnl_amt = (day_close - item.avg_price) * pos_qty
            out.append(item)
        return out


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
        def _reasons(r):
            if not r.reasons_json:
                return None
            try:
                return json.loads(r.reasons_json)
            except Exception:  # noqa: BLE001
                return None

        def _close(code: str) -> float | None:
            try:
                from ..services.live_trader import _last_close
                return _last_close(code)
            except Exception:  # noqa: BLE001
                return None

        return LiveSignalsResponse(
            as_of=as_of,
            picks=[LiveSignalRow(rank=r.rank, code=r.code, name=r.name,
                                  score=r.score, as_of=r.as_of,
                                  last_close=_close(r.code),
                                  reasons=_reasons(r)) for r in rows],
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
        return DailyPnLResponse(
            seed_cash={
                "open": settings.live_seed_cash_open,
                "close": settings.live_seed_cash_close,
            },
            rows=[DailyPnLRow(
                trade_date=r.trade_date,
                strategy=r.strategy,
                starting_equity=r.starting_equity,
                ending_equity=r.ending_equity,
                realised_pnl=r.realised_pnl,
                unrealised_pnl=r.unrealised_pnl,
                fees=r.fees,
            ) for r in rows],
        )


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
