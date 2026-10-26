"""Read-only signal feed for per-user runner containers (`/api/v1/feed`).

The hub/runner split (2026-10-26): this repo keeps data collection, the model,
the screeners and the recommendation lists; order execution moves to
`qlib-runner` containers, one per KIS account. Runners have no qlib install,
so everything they need to decide an order — signals, candidates, the trading
calendar, daily bars, the strategy catalog and the hub's halt flag — comes
through here.

Contract rules a runner relies on:
  * Every list response names the session it is FOR (`as_of` / `session`).
    A runner refuses to BUY on a list whose session isn't today's.
  * No response joins `orders`. What the hub's own sims bought is not a
    signal; leaking it would couple runners back to the hub's ledger.
  * Auth is a static bearer token per runner (`QLIB_API_FEED_TOKENS`), not the
    admin cookie. An empty token list disables the feed entirely (503).

Not exposed publicly: nginx denies `/api/v1/feed/` (infra/nginx), runners call
the api container over app-network.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func

from ..config import settings
from ..db import CafeCandidate, SessionLocal, Signal, SurgePick

log = logging.getLogger(__name__)

# Shorter than this is a placeholder, not a secret — refused like a missing one.
MIN_TOKEN_LEN = 32
MAX_BAR_CODES = 50
MAX_BAR_DAYS = 400


def require_feed_token(authorization: str | None = Header(default=None)) -> None:
    tokens = [t for t in settings.feed_tokens if len(t) >= MIN_TOKEN_LEN]
    if not tokens:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "feed disabled")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    given = authorization[len("Bearer "):].encode()
    # Compare against every token without short-circuiting on a match.
    ok = False
    for t in tokens:
        ok |= hmac.compare_digest(given, t.encode())
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


router = APIRouter(prefix="/feed", tags=["feed"], dependencies=[Depends(require_feed_token)])


def _json(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def _latest(db, column):
    return db.query(func.max(column)).scalar()


@router.get("/signals")
def get_signals(as_of: date | None = Query(None)):
    """All stored ranks (30) of one session's model output — newest by default.

    The full 30 are served, not the tradable top-10: the flow re-rank needs
    the wide list and sell reasons quote ranks beyond the cut."""
    with SessionLocal() as db:
        as_of = as_of or _latest(db, Signal.as_of)
        if as_of is None:
            return {"as_of": None, "generated_at": None, "picks": []}
        rows = (db.query(Signal).filter(Signal.as_of == as_of)
                  .order_by(Signal.rank.asc()).all())
        generated = max((r.created_at for r in rows), default=None)
        return {
            "as_of": as_of.isoformat(),
            "generated_at": generated.isoformat() + "Z" if generated else None,
            "model_class": rows[0].model_class if rows else None,
            "strategy_class": rows[0].strategy_class if rows else None,
            "picks": [{"rank": r.rank, "code": r.code, "name": r.name,
                       "score": r.score, "reasons": _json(r.reasons_json)} for r in rows],
        }


@router.get("/cafe/candidates")
def get_cafe_candidates(session: date | None = Query(None)):
    """One screener session's candidates. `stop_px` is the structural stop the
    runner must copy onto its entry order — the exit engine reads it there."""
    with SessionLocal() as db:
        session = session or _latest(db, CafeCandidate.trade_date)
        if session is None:
            return {"session": None, "candidates": []}
        rows = (db.query(CafeCandidate).filter(CafeCandidate.trade_date == session)
                  .order_by(CafeCandidate.rank.asc()).all())
        return {
            "session": session.isoformat(),
            "candidates": [{"rank": r.rank, "code": r.code, "name": r.name,
                            "pattern": r.pattern, "close": r.close, "stop_px": r.stop_px,
                            "metrics": _json(r.metrics_json)} for r in rows],
        }


@router.get("/surge/picks")
def get_surge_picks(session: date | None = Query(None)):
    with SessionLocal() as db:
        session = session or _latest(db, SurgePick.trade_date)
        if session is None:
            return {"session": None, "picks": []}
        rows = (db.query(SurgePick).filter(SurgePick.trade_date == session)
                  .order_by(SurgePick.rank.asc()).all())
        return {
            "session": session.isoformat(),
            "picks": [{"rank": r.rank, "code": r.code, "name": r.name, "close": r.close,
                       "score": r.score, "metrics": _json(r.metrics_json)} for r in rows],
        }


@router.get("/flow")
def get_flow(as_of: date = Query(..., description="signal session (Signal.as_of)")):
    """The hub's flow re-rank of one session's 30 signals — the same call
    `live_trader._apply_flow_overlay` makes, so a runner's flow strategy
    orders exactly what the hub's would. Flow scores need $volume from qlib,
    which runners don't have; serving the ranking keeps the formula in one place.

    `applied: false` means "trade plain model order", same as the hub."""
    from ..services import market_flow
    from ..services.live_trader import _prev_trading_day
    with SessionLocal() as db:
        codes = [c for (c,) in db.query(Signal.code).filter(Signal.as_of == as_of)
                                  .order_by(Signal.rank.asc()).all()]
        if not codes:
            return {"as_of": as_of.isoformat(), "applied": False, "reason": "no_signal",
                    "ordered": [], "metrics": {}}
        try:
            as_of_flow = _prev_trading_day(as_of)
            market_flow.ensure_flow_data(db, codes, as_of_flow)
            metrics = market_flow.flow_scores(db, codes, as_of_flow)
            ordered, info = market_flow.rerank(codes, metrics)
        except Exception as exc:  # noqa: BLE001
            log.warning("feed/flow failed — runner falls back to model order: %s", exc)
            return {"as_of": as_of.isoformat(), "applied": False, "reason": f"error: {exc}",
                    "ordered": codes, "metrics": {}}
        return {"as_of": as_of.isoformat(), "flow_as_of": as_of_flow.isoformat(),
                "applied": bool(info.get("applied")), "info": info,
                "ordered": ordered, "metrics": metrics}


@router.get("/calendar")
def get_calendar(day: date | None = Query(None, description="기준일, 기본 오늘")):
    """The three calendar answers order code asks: is `day` open, the last
    trading day on/before it, and the next one after it.

    `next` comes from the KIS holiday calendar first — qlib's calendar is
    built from past bars and cannot know a future holiday (2026-08-17)."""
    from ..services.live_trader import _last_trading_day, _next_trading_day, _prev_trading_day
    from ..services.trading_calendar import is_market_open
    day = day or date.today()
    return {
        "day": day.isoformat(),
        "is_open": is_market_open(day),
        "last": _last_trading_day(day).isoformat(),
        "prev": _prev_trading_day(day).isoformat(),
        "next": _next_trading_day(day).isoformat(),
    }


@router.get("/bars")
def get_bars(codes: str = Query(..., description="comma-separated"),
             start: date = Query(...), end: date = Query(...)):
    """Daily OHLCV from the hub's kr_data store — the inputs of the bracket
    exits (structural stop low, trail peak, day high/low).

    Codes outside kr_data (cafe small caps) come back empty; the runner falls
    back to KIS daily bars with its own key, as `_day_ohlc_any` does."""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list or len(code_list) > MAX_BAR_CODES:
        raise HTTPException(400, f"codes: 1..{MAX_BAR_CODES}")
    if end < start or (end - start) > timedelta(days=MAX_BAR_DAYS):
        raise HTTPException(400, f"range: start<=end, at most {MAX_BAR_DAYS} days")
    from qlib.data import D
    fields = ["$open", "$high", "$low", "$close", "$volume"]
    out: dict[str, list[dict]] = {c: [] for c in code_list}
    try:
        df = D.features(code_list, fields, start_time=start.isoformat(),
                        end_time=end.isoformat(), freq="day")
    except Exception as exc:  # noqa: BLE001
        log.warning("feed/bars failed: %s", exc)
        raise HTTPException(502, "bars unavailable") from exc
    if df is not None and not df.empty:
        for (code, ts), row in df.iterrows():
            vals = [row[f] for f in fields]
            if any(v != v for v in vals):  # NaN — no real bar that day
                continue
            out.setdefault(str(code), []).append({
                "date": ts.date().isoformat(), "open": float(vals[0]),
                "high": float(vals[1]), "low": float(vals[2]),
                "close": float(vals[3]), "volume": float(vals[4])})
    return {"start": start.isoformat(), "end": end.isoformat(), "bars": out}


@router.get("/strategies")
def get_strategies():
    from ..services.feed_catalog import catalog
    return catalog()


@router.get("/status")
def get_status():
    """Hub health a runner checks before buying.

    `halt` is the operator's emergency stop for EVERY runner: a runner treats
    a reason string here exactly like its own kill switch (no new buys)."""
    from ..services.kis_client import trading_halted
    status_raw = None
    try:
        import redis  # type: ignore[import-not-found]

        from ..services.kr_data_refresh import REDIS_STATUS_KEY
        raw = redis.from_url(settings.celery_broker_url,
                             socket_connect_timeout=2, socket_timeout=2).get(REDIS_STATUS_KEY)
        status_raw = _json(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:  # noqa: BLE001
        pass
    with SessionLocal() as db:
        sig_as_of = _latest(db, Signal.as_of)
        sig_at = (db.query(func.max(Signal.created_at))
                    .filter(Signal.as_of == sig_as_of).scalar() if sig_as_of else None)
        cafe = _latest(db, CafeCandidate.trade_date)
        surge = _latest(db, SurgePick.trade_date)
    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "halt": trading_halted(),
        "signals": {"as_of": sig_as_of.isoformat() if sig_as_of else None,
                    "generated_at": sig_at.isoformat() + "Z" if sig_at else None},
        "cafe_session": cafe.isoformat() if cafe else None,
        "surge_session": surge.isoformat() if surge else None,
        "kr_data_refresh": status_raw,
    }
