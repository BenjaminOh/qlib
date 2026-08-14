"""Read-path cache for the KIS account balance.

The dashboard polls 총 평가금액/보유종목 every 30s and three separate endpoints
(/live/balance, /live/exits, /live/stock/{code}/trades) each fetched the balance
independently — every one of them a real KIS call, serialized by the
account-wide 1.2s gate. Worse, when KIS itself degrades (2026-08-14: the paper
gateway answered `EGW00300` and token issuance read-timed-out at 10s) each poll
blocked for 10s and then 500'd, and the retry storm kept feeding KIS's 1/min
token-issuance limit.

This module gives the READ path a short-lived shared snapshot plus a stale
fallback, so the cards render instantly and survive a KIS outage.

Deliberately NOT wired into ``KISClient.get_balance`` — the order path
(``live_trader``) must always see a live balance, never a cached one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime

from ..config import settings
from .kis_client import (
    AccountSnapshot, Holding, fail_fast_tokens, get_kis_client,
)

log = logging.getLogger(__name__)

# Fresh window. Long enough that the dashboard's three balance consumers share
# one KIS call, short enough that a 30s poll still shows a live number.
_TTL_S = 5
# Last-known-good, kept well past a trading session so an outage that starts
# mid-morning still renders that morning's numbers instead of an empty card.
_STALE_TTL_S = 12 * 3600
# After a failed fetch, stop calling KIS at all for this long and serve the
# stale snapshot outright. Without it every 30s poll would still pay one failed
# round trip — up to the 15s HTTP timeout — before falling back.
_DOWN_TTL_S = 30

_redis_client = None


def _redis():
    """Module-singleton redis client — None if unavailable (never raises).

    Mirrors ``auth.rate_limit._get_redis``: one pool for the process (the old
    per-call ``redis.from_url`` leaked a pool on every KIS call) and explicit
    socket timeouts so an unreachable redis degrades instead of hanging.
    """
    global _redis_client
    if _redis_client is None:
        try:
            import redis  # type: ignore[import-not-found]
            _redis_client = redis.Redis.from_url(
                settings.celery_broker_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("balance cache: redis unavailable: %s", exc)
            return None
    return _redis_client


def _keys() -> tuple[str, str, str]:
    client = get_kis_client()
    base = f"kis:balance:{client.env}:{client.cano}"
    return base, f"{base}:last", f"{base}:down"


def _encode(snap: AccountSnapshot, as_of: datetime) -> str:
    return json.dumps({
        "cash": snap.cash,
        "total_eval": snap.total_eval,
        "holdings": [asdict(h) for h in snap.holdings],
        "as_of": as_of.isoformat(),
    }, ensure_ascii=False)


def _decode(raw: str) -> tuple[AccountSnapshot, datetime]:
    d = json.loads(raw)
    snap = AccountSnapshot(
        cash=float(d["cash"]),
        total_eval=float(d["total_eval"]),
        holdings=[Holding(**h) for h in d.get("holdings", [])],
    )
    return snap, datetime.fromisoformat(d["as_of"])


def _from_db() -> tuple[AccountSnapshot, datetime] | None:
    """Last stored PositionSnapshot for the open strategy.

    ``live_sync`` writes one at 09:30 and 15:40, so this is the final backstop
    when both redis tiers are empty (fresh container during a KIS outage).
    """
    try:
        from ..db import PositionSnapshot, SessionLocal, STRATEGY_OPEN

        with SessionLocal() as db:
            row = (db.query(PositionSnapshot)
                     .filter(PositionSnapshot.strategy == STRATEGY_OPEN)
                     .order_by(PositionSnapshot.snapshot_date.desc())
                     .first())
            if row is None:
                return None
            holdings = []
            for h in json.loads(row.holdings_json or "[]"):
                holdings.append(Holding(
                    code=str(h.get("code")),
                    name=h.get("name"),
                    qty=int(h.get("qty") or 0),
                    # sync_account writes the average price as "avg".
                    avg_price=float(h.get("avg") or 0),
                    eval_price=float(h.get("eval_price") or 0),
                    eval_value=float(h.get("eval_value") or 0),
                    pnl=float(h.get("pnl") or 0),
                    pnl_pct=float(h.get("pnl_pct") or 0),
                ))
            snap = AccountSnapshot(cash=row.cash, total_eval=row.total_eval,
                                   holdings=holdings)
            as_of = row.created_at or datetime.combine(row.snapshot_date,
                                                       datetime.min.time())
            return snap, as_of
    except Exception as exc:  # noqa: BLE001
        log.warning("balance cache: DB fallback failed: %s", exc)
        return None


def get_balance_for_read() -> tuple[AccountSnapshot, str, datetime]:
    """(snapshot, source, as_of) for display surfaces. Never raises.

    source: "cache" (fresh redis) | "live" (just fetched) | "stale"
            (last-known-good after a KIS failure) | "db" (PositionSnapshot)
            | "empty" (nothing available at all)
    """
    fresh_key, last_key, down_key = _keys()
    r = _redis()
    kis_down = False

    if r is not None:
        try:
            cached = r.get(fresh_key)
            if cached:
                snap, as_of = _decode(cached)
                return snap, "cache", as_of
            kis_down = bool(r.get(down_key))
        except Exception as exc:  # noqa: BLE001
            log.warning("balance cache: redis read failed: %s", exc)

    if not kis_down:
        try:
            # fail_fast: a browser is waiting, and we hold a fallback. Never sit
            # through the token path's 70s rate-limit recovery here.
            with fail_fast_tokens():
                snap = get_kis_client().get_balance()
            as_of = datetime.utcnow()
            if r is not None:
                try:
                    payload = _encode(snap, as_of)
                    r.set(fresh_key, payload, ex=_TTL_S)
                    r.set(last_key, payload, ex=_STALE_TTL_S)
                    r.delete(down_key)
                except Exception as exc:  # noqa: BLE001
                    log.warning("balance cache: redis write failed: %s", exc)
            return snap, "live", as_of
        except Exception as exc:  # noqa: BLE001
            log.warning("balance cache: KIS fetch failed, falling back: %s", exc)
            if r is not None:
                try:
                    r.set(down_key, "1", ex=_DOWN_TTL_S)
                except Exception:  # noqa: BLE001
                    pass

    if r is not None:
        try:
            last = r.get(last_key)
            if last:
                snap, as_of = _decode(last)
                return snap, "stale", as_of
        except Exception as exc:  # noqa: BLE001
            log.warning("balance cache: stale read failed: %s", exc)

    from_db = _from_db()
    if from_db is not None:
        snap, as_of = from_db
        return snap, "db", as_of

    return AccountSnapshot(cash=0.0, total_eval=0.0, holdings=[]), "empty", datetime.utcnow()


def invalidate() -> None:
    """Drop the fresh window so the next read re-fetches.

    Called after an order round-trip, where the 5s window would otherwise show
    pre-trade holdings.
    """
    r = _redis()
    if r is None:
        return
    try:
        r.delete(_keys()[0])
    except Exception:  # noqa: BLE001
        pass
