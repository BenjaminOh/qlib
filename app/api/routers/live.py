"""Live trading endpoints for the /live UI page (Phase B).

Read-only views over the SQLAlchemy models populated by the Celery beat tasks.
KIS balance is *also* fetched on demand for the dashboard's "right now" panel,
but everything historical comes from the DB.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
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
    strategy: str = "open"
    # SELL rows only: realized pnl vs the running average price at sale.
    # Market orders estimate the fill from the day open ('realized_est').
    realized_pnl: float | None = None
    realized_est: bool = False
    # True order kind — 'market' | 'limit' | 'sim'. NOT inferable from `price`:
    # sync_fills pins the reconciled average fill onto market orders.
    order_kind: str = "market"
    # reasons_json["basis"] — the decision basis captured at order time.
    basis: str | None = None
    # limit-strategy BUY rows only: previous trading day's close and the ACTUAL
    # fill discount vs it (negative = bought below prev close). A gap-down open
    # fills deeper than the configured discount, and that shows up here.
    prev_close: float | None = None
    discount_pct: float | None = None


class LiveOrdersResponse(BaseModel):
    orders: list[LiveOrderRow]
    # The configured 지정가 discount (0.03 = −3%), so the UI need not hardcode it.
    limit_discount: float = 0.03


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
    order_id: int | None = None
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
    # Sells only: the episode average price BEFORE this sell. `avg_price`
    # above is the post-event value, which is None once the position closes.
    avg_price_before: float | None = None
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


def _position_timeline(code: str, strategy: str,
                       kis_avg: float | None = None,
                       kis_now: float | None = None) -> list[StockTradeRow]:
    """Chronological order timeline with running position reconstruction.

    Shared by /stock/{code}/trades, /exits and the realized-pnl roll-up so
    every surface computes avg price / realized pnl identically."""
    prices = _price_series(code)
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
                order_id=r.id,
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
                        item.avg_price_before = avg_before or None
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


def _close_safe(code: str) -> float | None:
    try:
        from ..services.live_trader import _last_close
        return _last_close(code)
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=512)
def _prev_close_cached(code: str, day: date) -> float | None:
    """Close of the last trading day strictly before `day` — memoised.

    Reuses live_trader._prev_close_before so the 지정가 discount shown in the
    orders table is computed with exactly the same semantics the entry rule
    used. Cached because one page can repeat the same (code, day) pair.
    """
    try:
        from ..services.live_trader import _prev_close_before
        return _prev_close_before(code, day)
    except Exception:  # noqa: BLE001
        return None


def _kis_holding_prices(code: str) -> tuple[float | None, float | None]:
    """(avg_price, live quote) from the KIS balance — intraday fallback for
    dates whose bar hasn't landed in kr_data yet (arrives at 15:45)."""
    try:
        snap = get_kis_client().get_balance()
        h = next((h for h in snap.holdings if h.code == code), None)
        if h is not None:
            return h.avg_price or None, h.eval_price or None
    except Exception:  # noqa: BLE001
        pass
    return None, None


@router.get("/stock/{code}/trades", response_model=list[StockTradeRow])
def get_stock_trades(code: str, strategy: str = Query("open")):
    """Per-stock trade timeline (see _position_timeline)."""
    init_db()
    kis_avg, kis_now = _kis_holding_prices(code) if strategy == "open" else (None, None)
    return _position_timeline(code, strategy, kis_avg=kis_avg, kis_now=kis_now)


# ─── Per-stock return curves (holding-period lines for the chart) ────

_EXECUTED_STATUSES = ("SUBMITTED", "FILLED", "PARTIAL", "SIMULATED")


class StockCurvePoint(BaseModel):
    date: date
    ret_pct: float  # close / running avg_price − 1 on that day


class StockCurve(BaseModel):
    code: str
    name: str | None = None
    status: str  # "held" | "exited"
    episode: int  # re-entries get separate curves: 1, 2, ...
    start: date
    end: date | None = None  # None while still held
    avg_price: float | None = None  # avg carried at episode end/now
    points: list[StockCurvePoint]


class StockCurvesResponse(BaseModel):
    strategy: str
    curves: list[StockCurve]


@router.get("/stocks/curves", response_model=StockCurvesResponse)
def get_stock_curves(strategy: str = Query("open"),
                     days: int = Query(120, ge=7, le=365)):
    """Daily return-% series per stock, spanning ONLY its holding period(s).

    One curve per holding episode (buy → position reaches zero); a re-entered
    code yields episode 2, 3, ... Return basis is the running average buy
    price in effect on each day, so adding to a position bends the line at
    the new avg. Sell-day points use that day's CLOSE — the exits card owns
    the exact realized figure (actual fill price)."""
    init_db()
    cutoff = date.today() - timedelta(days=days)
    with SessionLocal() as db:
        rows = (db.query(Order)
                  .filter(Order.strategy == strategy,
                          Order.status.in_(_EXECUTED_STATUSES))
                  .order_by(Order.code.asc(), Order.trade_date.asc(), Order.id.asc())
                  .all())
        by_code: dict[str, list] = {}
        for r in rows:
            by_code.setdefault(r.code, []).append(
                (r.trade_date, r.side, r.qty, r.price))

    curves: list[StockCurve] = []
    for code, events in by_code.items():
        prices = _price_series(code)
        if not prices:
            continue
        all_dates = sorted(prices.keys())

        # Walk orders, carving episodes and per-date avg checkpoints.
        pos_qty, pos_cost = 0, 0.0
        episode = 0
        ep_start: date | None = None
        checkpoints: list[tuple[date, float]] = []  # (effective_date, avg)
        ep_list: list[dict] = []
        for (d, side, qty, px) in events:
            if px is None:  # market order pre-reconciliation → day open estimate
                day_open, day_close = prices.get(d.isoformat(), (None, None))
                px = day_open or day_close
            if px is None:
                continue
            if side == "BUY":
                if pos_qty == 0:
                    episode += 1
                    ep_start = d
                    checkpoints = []
                pos_qty += qty
                pos_cost += qty * px
                checkpoints.append((d, pos_cost / pos_qty))
            else:  # SELL
                avg_before = (pos_cost / pos_qty) if pos_qty else 0.0
                pos_cost -= avg_before * min(qty, pos_qty)
                pos_qty = max(pos_qty - qty, 0)
                if pos_qty == 0 and ep_start is not None:
                    ep_list.append({"episode": episode, "start": ep_start,
                                    "end": d, "checkpoints": list(checkpoints),
                                    "avg": avg_before})
                    ep_start = None
        if pos_qty > 0 and ep_start is not None:  # still held
            ep_list.append({"episode": episode, "start": ep_start, "end": None,
                            "checkpoints": list(checkpoints),
                            "avg": (pos_cost / pos_qty)})

        for ep in ep_list:
            if ep["end"] is not None and ep["end"] < cutoff:
                continue
            points: list[StockCurvePoint] = []
            for d_iso in all_dates:
                d_obj = date.fromisoformat(d_iso)
                if d_obj < ep["start"] or d_obj < cutoff:
                    continue
                if ep["end"] is not None and d_obj > ep["end"]:
                    break
                avg = None
                for (cd, cavg) in ep["checkpoints"]:
                    if cd <= d_obj:
                        avg = cavg
                _open, close = prices[d_iso]
                if not avg or not close:
                    continue
                points.append(StockCurvePoint(
                    date=d_obj, ret_pct=round(close / avg - 1, 6)))
            if not points:
                continue
            curves.append(StockCurve(
                code=code, name=_stock_name_safe(code),
                status="exited" if ep["end"] is not None else "held",
                episode=ep["episode"], start=ep["start"], end=ep["end"],
                avg_price=ep["avg"], points=points))

    curves.sort(key=lambda c: (c.status != "held", c.start), reverse=False)
    return StockCurvesResponse(strategy=strategy, curves=curves)


class ExitRow(BaseModel):
    code: str
    name: str | None = None
    strategy: str
    last_sell_date: date
    sold_qty: int
    avg_buy_price: float | None = None
    est_sell_price: float | None = None
    price_est: bool = True
    realized_pnl: float | None = None
    reasons: dict | None = None


@router.get("/exits", response_model=list[ExitRow])
def get_recent_exits(days: int = Query(30, ge=1, le=180), strategy: str = Query("open")):
    """Positions fully exited recently — the stocks that 'disappeared' from
    the holdings card, with why they were sold and the (estimated) realized
    pnl. Market sells have no recorded fill price until fill reconciliation
    exists, so exec/realized values use the day-open estimate."""
    init_db()
    cutoff = date.today() - timedelta(days=days)
    try:
        held = {h.code for h in get_kis_client().get_balance().holdings} if strategy == "open" else set()
    except Exception:  # noqa: BLE001
        held = set()
    with SessionLocal() as db:
        sold_codes = [r[0] for r in (db.query(Order.code)
                                       .filter(Order.strategy == strategy,
                                               Order.side == "SELL",
                                               Order.status.in_(("SUBMITTED", "FILLED", "PARTIAL", "SIMULATED")),
                                               Order.trade_date >= cutoff)
                                       .distinct())]
    out: list[ExitRow] = []
    for code in sold_codes:
        if code in held:
            continue  # partially rotated but still held — not an exit
        # Exited stocks have no KIS holding to fall back on intraday; use the
        # last stored close as the estimate until today's bar lands at 15:45.
        lc = _close_safe(code)
        timeline = _position_timeline(code, strategy, kis_now=lc)
        sells = [t for t in timeline
                 if t.side == "SELL" and t.status in ("SUBMITTED", "FILLED", "PARTIAL", "SIMULATED")
                 and t.trade_date >= cutoff]
        if not sells:
            continue
        last = sells[-1]
        # avg buy price the position carried INTO the final sell: reconstruct
        # from realized = (sell - avg) * qty when both are known.
        avg_buy = None
        if last.realized_pnl is not None and last.exec_price is not None and last.qty:
            avg_buy = last.exec_price - (last.realized_pnl / last.qty)
        with SessionLocal() as db:
            name_row = (db.query(Order.name)
                          .filter(Order.code == code, Order.name.isnot(None))
                          .order_by(desc(Order.id)).first())
        out.append(ExitRow(
            code=code, name=name_row[0] if name_row else None, strategy=strategy,
            last_sell_date=last.trade_date,
            sold_qty=sum(s.qty for s in sells),
            avg_buy_price=avg_buy,
            est_sell_price=last.exec_price,
            price_est=last.price_est,
            realized_pnl=sum(s.realized_pnl for s in sells if s.realized_pnl is not None) or None,
            reasons=last.reasons,
        ))
    out.sort(key=lambda r: r.last_sell_date, reverse=True)
    return out


@router.get("/realized/today")
def get_today_realized(strategy: str = Query("open")):
    """Sum of (estimated) realized pnl over today's sells — feeds the
    '당일 실현 손익' card, which previously ignored open-strategy sells."""
    init_db()
    today = date.today()
    with SessionLocal() as db:
        codes = [r[0] for r in (db.query(Order.code)
                                  .filter(Order.strategy == strategy,
                                          Order.side == "SELL",
                                          Order.status.in_(("SUBMITTED", "FILLED", "PARTIAL", "SIMULATED")),
                                          Order.trade_date == today)
                                  .distinct())]
    total = 0.0
    any_est = False
    n = 0
    for code in codes:
        for t in _position_timeline(code, strategy, kis_now=_close_safe(code)):
            if t.side == "SELL" and t.trade_date == today and t.realized_pnl is not None:
                total += t.realized_pnl
                any_est = any_est or t.price_est
                n += 1
    return {"date": today.isoformat(), "strategy": strategy,
            "realized_pnl": round(total, 0), "sell_count": n, "estimated": any_est}


@router.get("/signals", response_model=LiveSignalsResponse)
def get_latest_signals():
    """Most recent stored top-K signal."""
    init_db()
    with SessionLocal() as db:
        latest = db.query(Signal.as_of).order_by(desc(Signal.as_of)).first()
        if not latest:
            return LiveSignalsResponse(as_of=None, picks=[])
        as_of = latest[0]
        # Signals persist 30 ranks (sell-reason context); the recommendation
        # card shows only the tradable top-10.
        rows = (db.query(Signal)
                  .filter(Signal.as_of == as_of, Signal.rank <= 10)
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
def get_orders(limit: int = Query(100, ge=1, le=500),
               include_sim: bool = Query(False),
               strategy: str | None = Query(None),
               view: str = Query("real", pattern="^(all|real|sim)$")):
    """Recent orders (newest first) — SELL rows carry (estimated) realized pnl.

    `view` selects the ledger: 'real' = KIS test-account orders only (default),
    'sim' = SIMULATED strategy fills only, 'all' = both. `strategy` narrows to
    one strategy and composes with view. `include_sim=true` is the legacy
    spelling of view=all."""
    init_db()
    effective_view = "all" if (include_sim and view == "real") else view

    def _filtered(q):
        if strategy:
            q = q.filter(Order.strategy == strategy)
        if effective_view == "sim":
            return q.filter(Order.status == "SIMULATED")
        if effective_view == "real":
            return q.filter(Order.status != "SIMULATED")
        return q

    with SessionLocal() as db:
        rows = (_filtered(db.query(Order))
                .order_by(desc(Order.submitted_at))
                .limit(limit)
                .all())
    # Realized pnl per SELL order via the shared position timeline.
    _executed = ("SUBMITTED", "FILLED", "PARTIAL", "SIMULATED")
    realized_by_id: dict[int, tuple[float, bool]] = {}
    for code, strat in {(o.code, o.strategy) for o in rows
                        if o.side == "SELL" and o.status in _executed}:
        try:
            for t in _position_timeline(code, strat, kis_now=_close_safe(code)):
                if t.order_id is not None and t.realized_pnl is not None:
                    realized_by_id[t.order_id] = (t.realized_pnl, t.price_est)
        except Exception:  # noqa: BLE001
            continue
    with SessionLocal() as db:
        rows = (_filtered(db.query(Order))
                .order_by(desc(Order.submitted_at))
                .limit(limit)
                .all())
        # Exit SELLs of out-of-universe codes were stored with the code in the
        # name column — borrow the real name from any other row of the same code.
        nameless = {o.code for o in rows if not o.name or o.name == o.code}
        names: dict[str, str] = {}
        if nameless:
            for code, nm in (db.query(Order.code, Order.name)
                             .filter(Order.code.in_(nameless),
                                     Order.name.isnot(None),
                                     Order.name != Order.code)
                             .order_by(desc(Order.submitted_at))
                             .all()):
                names.setdefault(code, nm)
        # 지정가 entries: how far below the previous close did we ACTUALLY fill?
        # Recomputed from the price store rather than parsed out of the display
        # string in reasons_json, so it also covers rows written before the
        # basis wording existed. Limited to limit-BUYs (≤2/day) so the qlib
        # lookups stay cheap; failures degrade to no discount line.
        prev_closes: dict[tuple[str, date], float] = {}
        for key in {(o.code, o.trade_date) for o in rows
                    if o.strategy == "limit" and o.side == "BUY" and o.price}:
            pc = _prev_close_cached(*key)
            if pc:
                prev_closes[key] = pc

        def _basis(o: Order) -> str | None:
            if not o.reasons_json:
                return None
            try:
                return (json.loads(o.reasons_json) or {}).get("basis")
            except Exception:  # noqa: BLE001
                return None

        def _discount(o: Order) -> float | None:
            pc = prev_closes.get((o.code, o.trade_date))
            if not pc or not o.price:
                return None
            return round((o.price / pc - 1.0) * 100, 2)

        rows_out = [LiveOrderRow(
            id=o.id,
            submitted_at=o.submitted_at,
            trade_date=o.trade_date,
            code=o.code,
            name=(o.name if o.name and o.name != o.code
                  else names.get(o.code, o.name)),
            side=o.side,
            qty=o.qty,
            price=o.price,
            status=o.status,
            error=o.error,
            realized_pnl=(realized_by_id.get(o.id) or (None, False))[0],
            realized_est=(realized_by_id.get(o.id) or (None, False))[1],
            kis_order_id=o.kis_order_id,
            strategy=o.strategy or "open",
            order_kind=o.kind,
            basis=_basis(o),
            prev_close=prev_closes.get((o.code, o.trade_date)),
            discount_pct=_discount(o),
        ) for o in rows]
        return LiveOrdersResponse(orders=rows_out,
                                  limit_discount=settings.live_limit_discount)


# ─── Order story (row-click detail: why this buy/sell happened) ──────


class StoryBar(BaseModel):
    open: float
    high: float
    low: float
    close: float
    source: str  # "recorded" | "qlib" | "kis"


class StoryRuleLine(BaseModel):
    kind: str  # "tp" | "sl" | "trail" | "ladder_rung"
    label: str
    px: float | None = None
    # Whether the day's bar crossed this line (tp/rung: high>=px, stops: low<=px).
    hit: bool | None = None


class StoryRules(BaseModel):
    strategy: str
    exit_model: str  # "bracket" | "ladder" | "trail" | "rank_dropout" | "none"
    lines: list[StoryRuleLine] = []
    sl_kind: str | None = None  # prev_low | cap | entry_stop | trail | ladder_floor
    # True = rebuilt from CURRENT rules/config, not the values recorded at
    # judgment time — pre-2026-08-13 orders have no structured record.
    reconstructed: bool = False


class StoryEntry(BaseModel):
    order_id: int | None = None
    trade_date: date | None = None
    exec_price: float | None = None
    qty: int | None = None
    # SELL stories: the episode average at the moment of sale.
    avg_at_sale: float | None = None
    basis: str | None = None
    reasons: dict | None = None
    rank: int | None = None  # signal rank on the entry day, if any


class StoryJudgment(BaseModel):
    mode: str  # "sim_daily_bar" | "real_order"
    text: str
    recorded_at: datetime | None = None


class RankPoint(BaseModel):
    as_of: date
    rank: int | None = None  # None = outside the stored top-30


class PricePoint(BaseModel):
    trade_date: date
    close: float


class LimitEntryStory(BaseModel):
    limit_px: float | None = None
    prev_close: float | None = None
    fill_px: float | None = None
    discount_pct: float | None = None
    gap_down_fill: bool = False


class OrderStory(BaseModel):
    order: LiveOrderRow
    reasons: dict | None = None
    entry: StoryEntry | None = None
    rules: StoryRules | None = None
    bar: StoryBar | None = None
    judgment: StoryJudgment
    position_before: int | None = None
    position_after: int | None = None
    stage: int | None = None  # ladder: this was the n-th sell of the episode
    rank_history: list[RankPoint] = []
    topk: int = 10
    rank_store_n: int = 30
    post_closes: list[PricePoint] = []
    give_back_pct: float | None = None  # last post-close vs the exit price
    limit_entry: LimitEntryStory | None = None
    notes: list[str] = []


_SL_LABELS = {"prev_low": "손절선 (전 저점 기준)", "cap": "손절선 (리스크 캡)",
              "entry_stop": "손절선 (스크리너 구조적)", "trail": "트레일링 청산선",
              "ladder_floor": "사다리 플로어"}


def _trade_consts() -> tuple[int, int]:
    try:
        from ..services.live_trader import LIVE_CONFIG, SIGNAL_STORE_TOP_N
        return int(LIVE_CONFIG["strategy_kwargs"]["topk"]), int(SIGNAL_STORE_TOP_N)
    except Exception:  # noqa: BLE001
        return 10, 30


def _exit_rule(strategy: str) -> dict:
    try:
        from ..services.live_trader import EXIT_RULES
        return EXIT_RULES.get(strategy) or {}
    except Exception:  # noqa: BLE001
        return {}


def _day_ohlc_safe(code: str, day: date) -> dict | None:
    try:
        from ..services.live_trader import _day_ohlc
        return _day_ohlc(code, day)
    except Exception:  # noqa: BLE001
        return None


def _prev_low_safe(code: str, entry_date: date, window: int) -> float | None:
    try:
        from ..services.live_trader import _prev_low
        return _prev_low(code, entry_date, window)
    except Exception:  # noqa: BLE001
        return None


def _peak_close_safe(code: str, entry_date: date, day: date) -> float | None:
    try:
        from ..services.live_trader import _peak_close
        return _peak_close(code, entry_date, day)
    except Exception:  # noqa: BLE001
        return None


def _round_to_tick_safe(px: float) -> float:
    try:
        from ..services.kis_client import round_to_tick
        return float(round_to_tick(px))
    except Exception:  # noqa: BLE001
        return round(px)


@lru_cache(maxsize=128)
def _kis_bars_cached(code: str, cache_day: str) -> tuple:
    """KIS daily bars memoised per (code, calendar day).

    Completed daily bars are immutable, so the day-keyed cache is safe and
    keeps the story endpoint at ≤1 gated KIS call per code per day. Only
    reached when the qlib store has no bar (out-of-universe codes)."""
    try:
        return tuple(get_kis_client().get_daily_bars(code) or ())
    except Exception:  # noqa: BLE001
        return ()


def _build_order_story(o: Order) -> OrderStory:  # noqa: C901 — linear narrative
    notes: list[str] = []
    today = date.today()
    strategy = o.strategy or "open"
    topk, store_n = _trade_consts()

    reasons: dict | None = None
    if o.reasons_json:
        try:
            reasons = json.loads(o.reasons_json) or None
        except Exception:  # noqa: BLE001
            notes.append("사유 기록 파싱 실패 — 원본이 손상되었습니다")
    if reasons is None and o.reasons_json is None:
        notes.append("이 주문에는 사유 기록이 없습니다 (기능 도입 전 주문)")
    exit_ns = reasons.get("exit") if isinstance(reasons, dict) and isinstance(reasons.get("exit"), dict) else None
    entry_ns = reasons.get("entry") if isinstance(reasons, dict) and isinstance(reasons.get("entry"), dict) else None

    # ── Episode walk over the shared timeline (same math as realized pnl).
    timeline: list[StockTradeRow] = []
    try:
        timeline = _position_timeline(o.code, strategy)
    except Exception:  # noqa: BLE001
        notes.append("포지션 타임라인 재구성 실패")
    target = next((t for t in timeline if t.order_id == o.id), None)

    entry_row: StockTradeRow | None = None
    stage: int | None = None
    position_before: int | None = None
    position_after: int | None = None
    avg_at_sale: float | None = None
    if target is not None:
        prev_cum = 0
        sells_before = 0
        for t in timeline:
            if t.order_id == o.id:
                break
            if t.cum_qty is None:  # not executed — no position effect
                continue
            if t.side == "BUY" and prev_cum == 0:
                entry_row, sells_before = t, 0
            elif t.side == "SELL":
                sells_before += 1
                if t.cum_qty == 0:  # episode fully closed before our order
                    entry_row, sells_before = None, 0
            prev_cum = t.cum_qty
        if o.side == "SELL":
            avg_at_sale = target.avg_price_before
            if target.cum_qty is not None:
                position_after = target.cum_qty
                position_before = target.cum_qty + o.qty
            if _exit_rule(strategy).get("ladder"):
                stage = sells_before + 1
        else:
            entry_row = target
    elif o.side == "SELL":
        notes.append("타임라인에서 주문을 찾지 못해 진입 매칭을 생략했습니다")

    entry: StoryEntry | None = None
    if entry_row is not None:
        entry_rank = None
        try:
            with SessionLocal() as db:
                sig = (db.query(Signal)
                         .filter(Signal.code == o.code,
                                 Signal.as_of == entry_row.trade_date)
                         .first())
                entry_rank = sig.rank if sig else None
        except Exception:  # noqa: BLE001
            pass
        entry = StoryEntry(order_id=entry_row.order_id,
                           trade_date=entry_row.trade_date,
                           exec_price=entry_row.exec_price, qty=entry_row.qty,
                           avg_at_sale=avg_at_sale,
                           basis=(entry_row.reasons or {}).get("basis"),
                           reasons=entry_row.reasons, rank=entry_rank)
    elif o.side == "SELL" and target is not None:
        notes.append("선행 매수 기록이 없어 진입 맥락을 표시할 수 없습니다")

    # ── The day's bar: recorded → qlib → KIS (completed days only).
    bar: StoryBar | None = None
    if exit_ns and isinstance(exit_ns.get("bar"), dict):
        try:
            b = exit_ns["bar"]
            bar = StoryBar(open=b["open"], high=b["high"], low=b["low"],
                           close=b["close"], source="recorded")
        except Exception:  # noqa: BLE001
            pass
    if bar is None:
        b = _day_ohlc_safe(o.code, o.trade_date)
        if b:
            bar = StoryBar(**b, source="qlib")
    if bar is None:
        if o.trade_date >= today:
            notes.append("당일 봉은 15:45 데이터 갱신 후 표시됩니다")
        else:
            for kb in _kis_bars_cached(o.code, today.isoformat()):
                if kb.get("date") == o.trade_date.isoformat():
                    try:
                        if all(float(kb[k]) > 0 for k in ("open", "high", "low", "close")):
                            bar = StoryBar(open=kb["open"], high=kb["high"],
                                           low=kb["low"], close=kb["close"],
                                           source="kis")
                    except Exception:  # noqa: BLE001
                        pass
                    break
            if bar is None:
                notes.append("당일 봉 데이터를 찾지 못했습니다")

    # ── Rule lines (recorded values first; else rebuilt from CURRENT rules).
    rules: StoryRules | None = None
    rule = _exit_rule(strategy)
    if strategy == "open":
        rules = StoryRules(strategy=strategy, exit_model="rank_dropout")
    elif rule:
        lines: list[StoryRuleLine] = []
        sl_kind: str | None = None
        exit_model = ("ladder" if rule.get("ladder")
                      else "trail" if rule.get("trail") and not rule.get("tp")
                      else "bracket")
        entry_avg: float | None = None
        if exit_ns and exit_ns.get("entry_avg"):
            entry_avg = float(exit_ns["entry_avg"])
        elif avg_at_sale:
            entry_avg = avg_at_sale
        elif entry is not None and entry.exec_price:
            entry_avg = entry.exec_price

        def _line(kind: str, label: str, px: float | None) -> None:
            # hit only makes sense on the sell day — the entry day's range
            # predates the modeled close-entry, so BUY previews stay neutral.
            hit = None
            if px is not None and bar is not None and o.side == "SELL":
                hit = bar.high >= px if kind in ("tp", "ladder_rung") else bar.low <= px
            lines.append(StoryRuleLine(kind=kind, label=label,
                                       px=round(px, 2) if px else None, hit=hit))

        if exit_ns is not None:
            sl_kind = exit_ns.get("sl_kind")
            if exit_ns.get("tp_px"):
                tp_pct = rule.get("tp")
                _line("tp", f"익절선 +{tp_pct * 100:.0f}%" if tp_pct else "익절선",
                      float(exit_ns["tp_px"]))
            if entry_avg:
                for i, r_pct in enumerate(exit_ns.get("ladder") or []):
                    _line("ladder_rung", f"사다리 {i + 1}차 +{r_pct * 100:.0f}%",
                          entry_avg * (1 + r_pct))
            if exit_ns.get("sl_px"):
                _line("trail" if sl_kind == "trail" else "sl",
                      _SL_LABELS.get(sl_kind or "", "손절선"), float(exit_ns["sl_px"]))
            rules = StoryRules(strategy=strategy, exit_model=exit_model,
                               lines=lines, sl_kind=sl_kind, reconstructed=False)
        elif entry_avg:
            tp_pct = rule.get("tp")
            if tp_pct and not rule.get("ladder"):
                _line("tp", f"익절선 +{tp_pct * 100:.0f}%", entry_avg * (1 + tp_pct))
            for i, r_pct in enumerate(rule.get("ladder") or []):
                _line("ladder_rung", f"사다리 {i + 1}차 +{r_pct * 100:.0f}%",
                      entry_avg * (1 + r_pct))
            entry_reasons = (entry.reasons if entry else None) or {}
            if rule.get("stop_source") == "entry":
                cap = entry_avg * (1 - settings.live_cafe_stop_cap)
                stop_px = entry_reasons.get("stop_px")
                if stop_px and cap < float(stop_px) < entry_avg:
                    sl_px, sl_kind = float(stop_px), "entry_stop"
                else:
                    sl_px, sl_kind = cap, "cap"
            else:
                cap = entry_avg * (1 - settings.live_close_bracket_sl)
                prev_low = (_prev_low_safe(o.code, entry.trade_date,
                                           settings.live_close_bracket_low_window)
                            if entry is not None and entry.trade_date else None)
                low_stop = (prev_low * (1 - settings.live_close_bracket_low_buffer)
                            if prev_low else None)
                if low_stop and cap < low_stop < entry_avg:
                    sl_px, sl_kind = low_stop, "prev_low"
                else:
                    sl_px, sl_kind = cap, "cap"
            trail_pct = rule.get("trail")
            if trail_pct and entry is not None and entry.trade_date:
                peak = _peak_close_safe(o.code, entry.trade_date, o.trade_date)
                if peak and peak * (1 - trail_pct) > sl_px:
                    sl_px, sl_kind = peak * (1 - trail_pct), "trail"
            _line("trail" if sl_kind == "trail" else "sl",
                  _SL_LABELS.get(sl_kind or "", "손절선"), sl_px)
            rules = StoryRules(strategy=strategy, exit_model=exit_model,
                               lines=lines, sl_kind=sl_kind, reconstructed=True)
        else:
            rules = StoryRules(strategy=strategy, exit_model=exit_model)
            notes.append("평단을 특정할 수 없어 규칙 선을 생략했습니다")

    # ── Signal rank history around the order (rank None = outside top-30).
    rank_history: list[RankPoint] = []
    try:
        with SessionLocal() as db:
            as_ofs = [r[0] for r in (db.query(Signal.as_of).distinct()
                                       .filter(Signal.as_of <= o.trade_date)
                                       .order_by(desc(Signal.as_of))
                                       .limit(10).all())]
            ranks = dict(db.query(Signal.as_of, Signal.rank)
                           .filter(Signal.code == o.code,
                                   Signal.as_of.in_(as_ofs)).all())
        rank_history = [RankPoint(as_of=d, rank=ranks.get(d))
                        for d in sorted(as_ofs)]
        if not ranks:
            rank_history = []  # never ranked (cafe/surge out-of-signal codes)
    except Exception:  # noqa: BLE001
        pass

    # ── Post-trade closes (qlib store; reuse already-cached KIS bars only).
    post_closes: list[PricePoint] = []
    try:
        series = _price_series(o.code)
        pts = [(d, c) for d, (_, c) in sorted(series.items())
               if c is not None and d > o.trade_date.isoformat()]
        if not pts and bar is not None and bar.source == "kis":
            pts = [(kb["date"], float(kb["close"]))
                   for kb in _kis_bars_cached(o.code, today.isoformat())
                   if kb.get("date") and kb["date"] > o.trade_date.isoformat()
                   and kb.get("close")]
        post_closes = [PricePoint(trade_date=date.fromisoformat(d), close=c)
                       for d, c in pts[:5]]
    except Exception:  # noqa: BLE001
        pass
    give_back_pct = None
    exit_px = o.price or (target.exec_price if target else None)
    if o.side == "SELL" and post_closes and exit_px:
        give_back_pct = round((post_closes[-1].close / exit_px - 1) * 100, 2)

    # ── How the fill came to exist.
    if o.status == "REJECTED":
        judgment = StoryJudgment(mode="real_order", recorded_at=o.submitted_at,
                                 text=f"주문 거부 — {o.error or '사유 미기록'}")
    elif o.status == "SIMULATED":
        judgment = StoryJudgment(
            mode="sim_daily_bar", recorded_at=o.submitted_at,
            text="시뮬 전략 — KIS 실주문 없이, 장 마감 후(15:45 데이터 갱신 뒤) "
                 "당일 봉으로 규칙 통과 여부를 판정해 장부에 기록한 체결입니다.")
    else:
        kind_txt = {"market": "시장가", "limit": "지정가"}.get(o.kind, o.kind)
        judgment = StoryJudgment(
            mode="real_order", recorded_at=o.submitted_at,
            text=f"KIS 실주문({kind_txt}) — 시장가 실체결가는 09:20 대사에서 확정됩니다.")

    # ── Header row (same construction rules as /orders).
    prev_close_v = disc = None
    if strategy == "limit" and o.side == "BUY" and o.price:
        pc = _prev_close_cached(o.code, o.trade_date)
        if pc:
            prev_close_v = pc
            disc = round((o.price / pc - 1.0) * 100, 2)
    order_row = LiveOrderRow(
        id=o.id, submitted_at=o.submitted_at, trade_date=o.trade_date,
        code=o.code,
        name=(o.name if o.name and o.name != o.code
              else _stock_name_safe(o.code) or o.name),
        side=o.side, qty=o.qty, price=o.price, status=o.status, error=o.error,
        realized_pnl=target.realized_pnl if target else None,
        realized_est=target.price_est if target else False,
        kis_order_id=o.kis_order_id, strategy=strategy, order_kind=o.kind,
        basis=(reasons or {}).get("basis"),
        prev_close=prev_close_v, discount_pct=disc)

    limit_entry: LimitEntryStory | None = None
    if strategy == "limit" and o.side == "BUY":
        if entry_ns:
            lp = entry_ns.get("limit_px")
            pcv = entry_ns.get("prev_close")
            fp = entry_ns.get("fill_px") or o.price
        else:
            pcv = prev_close_v
            lp = _round_to_tick_safe(pcv * (1 - settings.live_limit_discount)) if pcv else None
            fp = o.price
        if lp or pcv:
            dpct = round((fp / pcv - 1) * 100, 2) if fp and pcv else None
            limit_entry = LimitEntryStory(
                limit_px=lp, prev_close=pcv, fill_px=fp, discount_pct=dpct,
                gap_down_fill=bool(fp and lp and fp < lp - 1e-9))

    return OrderStory(order=order_row, reasons=reasons, entry=entry,
                      rules=rules, bar=bar, judgment=judgment,
                      position_before=position_before,
                      position_after=position_after, stage=stage,
                      rank_history=rank_history, topk=topk,
                      rank_store_n=store_n, post_closes=post_closes,
                      give_back_pct=give_back_pct, limit_entry=limit_entry,
                      notes=notes)


@router.get("/orders/{order_id}/story", response_model=OrderStory)
def get_order_story(order_id: int):
    """Row-click detail: the full narrative behind one order — entry context,
    rule lines, the day's bar, how the judgment was made, and what happened
    after. Sections degrade independently; the core row always returns."""
    init_db()
    with SessionLocal() as db:
        o = db.query(Order).filter(Order.id == order_id).first()
        if o is None:
            raise HTTPException(status_code=404, detail="order not found")
        db.expunge(o)
    return _build_order_story(o)


class CafeCandidateRow(BaseModel):
    trade_date: date
    code: str
    name: str | None = None
    pattern: str
    pattern_label: str
    rank: int
    close: float | None = None
    stop_px: float | None = None
    bought: bool = False


class OrderbookRow(BaseModel):
    """One (day, slot, code) book capture, plus the fill verdict it supports."""
    trade_date: date
    slot: str
    code: str
    name: str | None = None
    price: float | None = None
    upper_limit_px: float | None = None
    at_upper_limit: bool = False
    total_ask_qty: float | None = None
    total_bid_qty: float | None = None
    ask_qty_1: float | None = None
    antc_price: float | None = None
    antc_qty: float | None = None
    # What the cafe sim actually claimed to buy that day, so the depth on the
    # left can be read against the size on the right without a second query.
    order_qty: int | None = None
    # order_qty vs the depth available in this slot. None when the slot has no
    # comparable depth figure (e.g. a 동시호가 capture with no 예상체결수량).
    depth_ratio: float | None = None


class OrderbookResponse(BaseModel):
    rows: list[OrderbookRow]


class RetroEpisode(BaseModel):
    code: str
    name: str | None = None
    strategy: str
    entry_date: str | None = None
    exit_date: str | None = None
    avg: float
    exit_px: float | None = None
    qty: int
    ret_pct: float | None = None
    unreal_pct: float | None = None
    max_unreal_pct: float | None = None
    give_back_pp: float | None = None
    post5_drift_pct: float | None = None
    hold_days: int | None = None
    entry_metrics: dict = {}
    entry_basis: str = ""


class RetroHypothesis(BaseModel):
    key: str
    label: str
    evidence: str
    support: int
    refute: int
    threshold: str


class RetroIC(BaseModel):
    as_of: str
    n: int
    rank_ic: float


class SurgeSelectionRow(BaseModel):
    trade_date: str
    rank: int
    code: str
    name: str | None = None
    close: float | None = None
    score: float
    next_ret_pct: float | None = None
    hit: bool | None = None


class RetroResponse(BaseModel):
    episodes: list[RetroEpisode]
    scoreboard: list[RetroHypothesis]
    daily_ic: list[RetroIC]
    surge_selection: list[SurgeSelectionRow] | None = None


@router.get("/retro", response_model=RetroResponse)
def get_retro(strategy: str = Query("open")):
    """Trade retrospective — closed episodes joined with entry evidence and
    outcomes, hypothesis scoreboard, and the signal's daily rank-IC."""
    from ..services.retrospective import build_retro
    return RetroResponse(**build_retro(strategy))


class SurgePickRow(BaseModel):
    trade_date: date
    rank: int
    code: str
    name: str | None = None
    close: float | None = None
    score: float
    bought: bool = False


class SurgePicksResponse(BaseModel):
    picks: list[SurgePickRow]


@router.get("/surge/picks", response_model=SurgePicksResponse)
def get_surge_picks(days: int = Query(7, ge=1, le=60)):
    """Recent surge-eve TOP10 picks + whether the 15:29 sim bought them."""
    from ..db import SurgePick
    init_db()
    cutoff = date.today() - timedelta(days=days)
    with SessionLocal() as db:
        rows = (db.query(SurgePick)
                  .filter(SurgePick.trade_date >= cutoff)
                  .order_by(desc(SurgePick.trade_date), SurgePick.rank.asc())
                  .all())
        bought = {(o.trade_date, o.code) for o in
                  db.query(Order).filter(Order.strategy == "surge",
                                         Order.side == "BUY",
                                         Order.trade_date >= cutoff).all()}
        return SurgePicksResponse(picks=[SurgePickRow(
            trade_date=r.trade_date, rank=r.rank, code=r.code, name=r.name,
            close=r.close, score=r.score,
            bought=(r.trade_date, r.code) in bought,
        ) for r in rows])


class CafeCandidatesResponse(BaseModel):
    candidates: list[CafeCandidateRow]


@router.get("/cafe/candidates", response_model=CafeCandidatesResponse)
def get_cafe_candidates(days: int = Query(7, ge=1, le=60)):
    """Recent cafe-screener picks (15:05 scan) + whether the 15:28 sim bought them."""
    from ..db import CafeCandidate
    from ..services.market_screener import _PATTERN_LABELS
    init_db()
    cutoff = date.today() - timedelta(days=days)
    with SessionLocal() as db:
        rows = (db.query(CafeCandidate)
                  .filter(CafeCandidate.trade_date >= cutoff)
                  .order_by(desc(CafeCandidate.trade_date), CafeCandidate.rank.asc())
                  .all())
        bought = {(o.trade_date, o.code) for o in
                  db.query(Order).filter(Order.strategy == "cafe",
                                         Order.side == "BUY",
                                         Order.trade_date >= cutoff).all()}
        return CafeCandidatesResponse(candidates=[CafeCandidateRow(
            trade_date=r.trade_date, code=r.code, name=r.name,
            pattern=r.pattern,
            pattern_label=_PATTERN_LABELS.get(r.pattern, r.pattern),
            rank=r.rank, close=r.close, stop_px=r.stop_px,
            bought=(r.trade_date, r.code) in bought,
        ) for r in rows])


@router.get("/cafe/orderbook", response_model=OrderbookResponse)
def get_cafe_orderbook(days: int = Query(14, ge=1, le=90)):
    """Book depth captured at 15:07 / 15:27 for each cafe candidate.

    `depth_ratio` is the whole point: order_qty ÷ available depth. Above 1.0
    the 15:28 sim claimed more shares than the book was showing, which is the
    fill nobody can defend. The 1505 slot compares against 총매도호가잔량
    (정규장 depth), the 1528 slot against 예상체결수량 (what would match in
    the closing auction)."""
    from ..db import OrderbookSnapshot
    init_db()
    cutoff = date.today() - timedelta(days=days)
    with SessionLocal() as db:
        rows = (db.query(OrderbookSnapshot)
                  .filter(OrderbookSnapshot.trade_date >= cutoff)
                  .order_by(desc(OrderbookSnapshot.trade_date),
                            OrderbookSnapshot.slot.asc(),
                            OrderbookSnapshot.code.asc())
                  .all())
        qty_by_key = {(o.trade_date, o.code): o.qty for o in
                      db.query(Order).filter(Order.strategy == "cafe",
                                             Order.side == "BUY",
                                             Order.trade_date >= cutoff).all()}
        out = []
        for r in rows:
            order_qty = qty_by_key.get((r.trade_date, r.code))
            depth = r.antc_qty if r.slot == "1528" else r.total_ask_qty
            out.append(OrderbookRow(
                trade_date=r.trade_date, slot=r.slot, code=r.code, name=r.name,
                price=r.price, upper_limit_px=r.upper_limit_px,
                at_upper_limit=bool(r.at_upper_limit),
                total_ask_qty=r.total_ask_qty, total_bid_qty=r.total_bid_qty,
                ask_qty_1=r.ask_qty_1, antc_price=r.antc_price,
                antc_qty=r.antc_qty, order_qty=order_qty,
                depth_ratio=(order_qty / depth) if (order_qty and depth) else None,
            ))
        return OrderbookResponse(rows=out)


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
                "flow": settings.live_seed_cash_flow,
                "trail": settings.live_seed_cash_trail,
                "scale": settings.live_seed_cash_scale,
                "limit": settings.live_seed_cash_limit,
                "cafe": settings.live_seed_cash_cafe,
                "surge": settings.live_seed_cash_surge,
                "cafeopen": settings.live_seed_cash_cafeopen,
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
