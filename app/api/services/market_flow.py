"""기관·외국인 수급(net-buy) 오버레이 for the 'flow' simulated strategy.

The model behind `generate_daily_signal` only ever sees OHLCV (Alpha158), so
"who is actually buying this" is an information axis it is blind to. This
module adds that axis WITHOUT retraining: it re-ranks the top-30 signal rows
that are already persisted every evening, and the 'flow' strategy trades the
re-ranked top-K with the exact same execution model as 'close'.

Data source: KIS TR FHKST01010900 (주식현재가 투자자) via
``KISClient.get_investor_daily``. One call per code returns ~30 daily rows, so
the whole lookback window costs a single request. pykrx was the original plan
and is NOT usable — since 1.2.8 every 투자자별 매매동향 endpoint requires KRX
account credentials (verified 2026-08-03: returns an empty frame with
"KRX 로그인 실패").

Everything here is best-effort. If flow data is missing the overlay returns
the model order untouched and says so — a 'flow' day with no data is simply a
'close' day, never a blocked order.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..db import MarketFlow

log = logging.getLogger(__name__)


# Tunables live in one dict so a future scenario table can override them
# wholesale (see docs/02-design/features/scenario-matrix.design.md).
FLOW_CONFIG = {
    # Trading days of net buying to accumulate.
    "lookback_days": 5,
    # Weights on the two investor groups. 기관 is generally the cleaner
    # signal in KR equities — 외국인 net buys are diluted by program/ETF
    # flow — so it carries slightly more weight.
    "w_inst": 0.6,
    "w_frgn": 0.4,
    # 0 = pure model rank, 1 = pure flow rank.
    "blend": 0.5,
    # Drop candidates that were net SOLD by both groups over the window.
    "require_positive": True,
    # Below this many bars/flow rows a code is treated as "no data" rather
    # than "no buying" — an unknown must never look like a negative.
    "min_flow_days": 3,
}


# ─── Ingestion ──────────────────────────────────────────────────────


def ensure_flow_data(db: Session, codes: list[str], as_of: date,
                     *, client=None, force: bool = False) -> dict:
    """Fetch + upsert investor rows for `codes`, skipping codes already covered.

    A code is "covered" when it already has a MarketFlow row dated `as_of`
    (the most recent trading day whose data should exist). Idempotent, so the
    beat fallback and the on-demand call at order time can both run.

    Never raises: per-code failures are collected and reported.
    """
    from .kis_client import get_kis_client

    client = client or get_kis_client()
    if client.is_mock:
        return {"status": "skipped", "reason": "mock", "fetched": 0,
                "rows": 0, "failed": []}

    todo = list(dict.fromkeys(codes))
    if not force:
        covered = {
            c for (c,) in db.query(MarketFlow.code)
                            .filter(MarketFlow.trade_date == as_of,
                                    MarketFlow.code.in_(todo))
                            .all()
        }
        todo = [c for c in todo if c not in covered]
    if not todo:
        return {"status": "ok", "reason": "already_covered", "fetched": 0,
                "rows": 0, "failed": []}

    fetched = rows_written = 0
    failed: list[str] = []
    for code in todo:
        try:
            rows = client.get_investor_daily(code)
        except Exception as exc:  # noqa: BLE001 — one bad code must not stop the sweep
            log.warning("market_flow: get_investor_daily failed for %s: %s", code, exc)
            failed.append(code)
            continue
        if not rows:
            failed.append(code)
            continue
        fetched += 1
        rows_written += _upsert_rows(db, code, rows)
    db.commit()

    result = {"status": "ok", "as_of": as_of.isoformat(), "requested": len(todo),
              "fetched": fetched, "rows": rows_written, "failed": failed}
    log.info("market_flow.ensure_flow_data: %s", result)
    return result


def _upsert_rows(db: Session, code: str, rows: list[dict]) -> int:
    """Insert the rows we don't have yet for this code. Returns rows added."""
    days = [r["date"] for r in rows]
    existing = {
        d for (d,) in db.query(MarketFlow.trade_date)
                        .filter(MarketFlow.code == code,
                                MarketFlow.trade_date.in_(
                                    [_parse_day(d) for d in days]))
                        .all()
    }
    added = 0
    for r in rows:
        day = _parse_day(r["date"])
        if day is None or day in existing:
            continue
        db.add(MarketFlow(
            trade_date=day,
            code=code,
            frgn_net_qty=r.get("frgn_net_qty"),
            orgn_net_qty=r.get("orgn_net_qty"),
            prsn_net_qty=r.get("prsn_net_qty"),
            frgn_net_amt=r.get("frgn_net_amt"),
            orgn_net_amt=r.get("orgn_net_amt"),
            prsn_net_amt=r.get("prsn_net_amt"),
        ))
        existing.add(day)
        added += 1
    return added


def _parse_day(s: str) -> date | None:
    try:
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, TypeError, IndexError):
        return None


# ─── Scoring ────────────────────────────────────────────────────────


def flow_scores(db: Session, codes: list[str], as_of: date,
                cfg: dict | None = None) -> dict[str, dict]:
    """Per-code supply/demand metrics over the lookback window ending `as_of`.

    Normalising by the stock's own traded volume is the whole point: 100k
    shares bought in 삼성전자 and in a small-cap are not the same signal.
    Codes with too few flow rows are omitted entirely (unknown ≠ negative).
    """
    cfg = {**FLOW_CONFIG, **(cfg or {})}
    lookback = int(cfg["lookback_days"])
    codes = list(dict.fromkeys(codes))
    if not codes:
        return {}

    # Calendar-day window generous enough to hold `lookback` trading days.
    start = as_of - timedelta(days=lookback * 3 + 7)
    rows = (db.query(MarketFlow)
              .filter(MarketFlow.code.in_(codes),
                      MarketFlow.trade_date <= as_of,
                      MarketFlow.trade_date >= start)
              .order_by(MarketFlow.trade_date.desc())
              .all())
    by_code: dict[str, list[MarketFlow]] = {}
    for r in rows:
        by_code.setdefault(r.code, []).append(r)

    volumes = _window_volume(codes, as_of, lookback)

    out: dict[str, dict] = {}
    for code in codes:
        window = (by_code.get(code) or [])[:lookback]
        if len(window) < int(cfg["min_flow_days"]):
            continue
        vol = volumes.get(code)
        if not vol or vol <= 0:
            continue
        inst = sum(float(r.orgn_net_qty or 0.0) for r in window)
        frgn = sum(float(r.frgn_net_qty or 0.0) for r in window)
        inst_ratio = inst / vol
        frgn_ratio = frgn / vol
        out[code] = {
            "days": len(window),
            "inst_qty": inst,
            "frgn_qty": frgn,
            "inst_ratio": round(inst_ratio, 5),
            "frgn_ratio": round(frgn_ratio, 5),
            "score": round(cfg["w_inst"] * inst_ratio + cfg["w_frgn"] * frgn_ratio, 5),
        }
    return out


def _window_volume(codes: list[str], as_of: date, lookback: int) -> dict[str, float]:
    """Summed $volume over the last `lookback` trading days on/before `as_of`."""
    try:
        from qlib.data import D
        start = (as_of - timedelta(days=lookback * 3 + 7)).isoformat()
        df = D.features(list(codes), ["$volume"], start_time=start,
                        end_time=as_of.isoformat(), freq="day")
    except Exception as exc:  # noqa: BLE001
        log.warning("market_flow: $volume lookup failed: %s", exc)
        return {}
    if df is None or df.empty:
        return {}
    col = "$volume" if "$volume" in df.columns else df.columns[0]
    out: dict[str, float] = {}
    for code, sub in df[col].groupby(level="instrument"):
        s = sub.droplevel("instrument").dropna().tail(lookback)
        if not s.empty:
            out[str(code)] = float(s.sum())
    return out


# ─── Re-ranking ─────────────────────────────────────────────────────


def rerank(ranked_codes: list[str], scores: dict[str, dict],
           cfg: dict | None = None) -> tuple[list[str], dict]:
    """Blend the model's rank with the flow rank. Returns (codes, info).

    Both inputs are converted to percentile ranks (0 = best) so the blend
    weight means the same thing regardless of candidate-list length. Ties
    fall back to the model order, which keeps the result deterministic.

    With no usable scores this returns `ranked_codes` unchanged — the caller
    then trades exactly what 'close' would.
    """
    cfg = {**FLOW_CONFIG, **(cfg or {})}
    n = len(ranked_codes)
    if n == 0:
        return [], {"applied": False, "reason": "no_candidates"}
    scored = [c for c in ranked_codes if c in scores]
    if not scored:
        return list(ranked_codes), {"applied": False, "reason": "no_flow_data",
                                    "dropped": [], "scored": 0}

    dropped: list[str] = []
    keep = list(ranked_codes)
    if cfg["require_positive"]:
        # Only codes we actually have data for can be dropped; an unknown
        # keeps its model rank rather than being punished for missing data.
        keep = [c for c in ranked_codes
                if c not in scores or scores[c]["score"] > 0]
        dropped = [c for c in ranked_codes if c not in keep]
        if not keep:
            return list(ranked_codes), {"applied": False, "reason": "all_negative",
                                        "dropped": [], "scored": len(scored)}

    model_rank = {c: i for i, c in enumerate(ranked_codes)}
    # Codes without flow data sit at the median of the flow axis, so the
    # blend neither rewards nor punishes the gap.
    flow_order = sorted((c for c in keep if c in scores),
                        key=lambda c: -scores[c]["score"])
    flow_rank = {c: i for i, c in enumerate(flow_order)}
    m = max(len(flow_order) - 1, 1)
    blend = float(cfg["blend"])

    def _final(code: str) -> tuple[float, int]:
        mp = model_rank[code] / max(n - 1, 1)
        fp = flow_rank[code] / m if code in flow_rank else 0.5
        return ((1 - blend) * mp + blend * fp, model_rank[code])

    out = sorted(keep, key=_final)
    return out, {
        "applied": True,
        "scored": len(scored),
        "dropped": dropped,
        "blend": blend,
        "lookback_days": cfg["lookback_days"],
    }


# ─── Display ────────────────────────────────────────────────────────


def flow_summary(m: dict | None, lookback: int | None = None) -> str:
    """'기관 5일 +12.3만주(거래량 4.1%) · 외국인 -0.8만주' — dashboard one-liner."""
    if not m:
        return ""
    days = lookback or m.get("days") or FLOW_CONFIG["lookback_days"]

    def _part(label: str, qty: float, ratio: float) -> str:
        return f"{label} {days}일 {qty / 10_000:+.1f}만주(거래량 {ratio * 100:+.1f}%)"

    return " · ".join([
        _part("기관", m.get("inst_qty", 0.0), m.get("inst_ratio", 0.0)),
        _part("외국인", m.get("frgn_qty", 0.0), m.get("frgn_ratio", 0.0)),
    ])
