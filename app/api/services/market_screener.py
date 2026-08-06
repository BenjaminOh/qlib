"""Cafe-mimic market screener — the recommender's system as pure rules.

Reverse-engineered from 4 samples (docs/06-research/): the recommender buys
theme leaders at the close on a technical-event day and places a STRUCTURAL
stop. Four patterns, in his observed priority order:

  B  급등 타이트 눌림  (TXR형)   : big up day, then a tight ≤6% pullback day
  A  신고가 돌파       (위닉스형) : 30d new high on ≥2× volume
  C  급등 눌림 재진입  (씨피형)   : surge, −10~25% pullback, green reversal
  D  낙폭과대 반등     (뉴엔AI형) : −40% off the high, +5% rebound over MA20

Candidates come from KIS ranking TRs (whole market — no local data needed),
bars from the KIS daily-price TR, so out-of-universe KOSDAQ small caps work.
Everything is sim-only: the 15:28 buy writes SIMULATED fills for the 'cafe'
strategy; exits run in the shared bracket loop with the stored structural stop.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from ..config import settings
from ..db import CafeCandidate, CafeScout, Order, SessionLocal, STRATEGY_CAFE, init_db

log = logging.getLogger(__name__)

# Pool hygiene: instruments the recommender never touches.
_EXCLUDE_TOKENS = ("스팩", "ETF", "ETN", "리츠", "인버스", "레버리지", "채권",
                   "TIGER", "KODEX", "KBSTAR", "ACE ", "SOL ", "PLUS ")

MAX_POOL_BARS = 25       # codes we spend daily-bar calls on (~30s at the gate)
MAX_CANDIDATES = 2       # picks stored per day (recommender bets 1-2 names)
PRIORITY = ("B", "A", "C", "D")


def _excluded(name: str) -> bool:
    if not name:
        return False
    if any(tok in name for tok in _EXCLUDE_TOKENS):
        return True
    return name.endswith("우") or name.endswith("우B")  # 우선주


def _classify(bars: list[dict]) -> dict | None:
    """Return {pattern, stop_px, metrics} if the bar history matches a
    pattern, else None. `bars` oldest→newest; the LAST row is today's
    (possibly intraday) bar — the screener runs at 15:05, near the close."""
    if len(bars) < 21:
        return None
    today = bars[-1]
    prev = bars[-2]
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    vol20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 0
    ret20 = today["close"] / closes[-21] - 1 if closes[-21] > 0 else 0
    prev_ret = prev["close"] / bars[-3]["close"] - 1 if bars[-3]["close"] > 0 else 0
    m = {"ret20": round(ret20 * 100, 1),
         "prev_ret": round(prev_ret * 100, 1),
         "vol_x": round(today["volume"] / vol20, 2) if vol20 else None}

    # B — 급등 타이트 눌림: yesterday ≥+15%, today's range ≤6% of prev close,
    # holding above yesterday's low. Stop = the pre-surge swing high.
    if prev_ret >= 0.15 and prev["close"] > 0:
        day_range = (today["high"] - today["low"]) / prev["close"]
        if day_range <= 0.06 and today["close"] > prev["low"]:
            pre_surge = bars[-12:-2]
            stop = max(b["high"] for b in pre_surge) if pre_surge else today["low"] * 0.95
            if stop < today["close"]:
                return {"pattern": "B", "stop_px": stop,
                        "metrics": {**m, "range_pct": round(day_range * 100, 1)}}

    # A — 신고가 돌파: today is the 30d closing high, ret20 ≥ +30%, ≥2× volume.
    if today["close"] >= max(closes[:-1]) and ret20 >= 0.30 and vol20 and today["volume"] >= 2 * vol20:
        return {"pattern": "A", "stop_px": today["low"] * 0.95, "metrics": m}

    # C — 급등 눌림 재진입: a ≥+25% surge within ~10 days, now −8~−25% off that
    # peak, today a green reversal bar. Stop = recent pullback low − 4%.
    peak = max(closes[-10:])
    base = min(closes[-20:-8]) if len(closes) >= 20 else min(closes[:-8] or closes)
    off_peak = today["close"] / peak - 1 if peak > 0 else 0
    if base > 0 and peak / base >= 1.25 and -0.25 <= off_peak <= -0.08 \
            and today["close"] > today["open"]:
        pull_low = min(b["low"] for b in bars[-5:])
        return {"pattern": "C", "stop_px": pull_low * 0.96,
                "metrics": {**m, "off_peak": round(off_peak * 100, 1)}}

    # D — 낙폭과대 반등: ≥40% below the 30d high, today ≥+5% and closing above
    # MA20 from below. Stop = today's low − 3%.
    high30 = max(closes)
    ma20 = sum(closes[-20:]) / 20
    today_ret = today["close"] / prev["close"] - 1 if prev["close"] > 0 else 0
    if high30 > 0 and today["close"] / high30 <= 0.60 and today_ret >= 0.05 \
            and today["close"] > ma20 >= prev["close"]:
        return {"pattern": "D", "stop_px": today["low"] * 0.97,
                "metrics": {**m, "off_high30": round((today['close'] / high30 - 1) * 100, 1)}}

    return None


def run_screener(trade_date: date | None = None) -> dict:
    """15:05 — scan KIS ranking pools, classify, store today's candidates."""
    from .kis_client import get_kis_client
    init_db()
    day = trade_date or date.today()
    client = get_kis_client()
    pool: dict[str, dict] = {}
    for row in client.get_rank_fluctuation() + client.get_rank_volume():
        if row["code"] not in pool and not _excluded(row["name"]):
            pool[row["code"]] = row
    if not pool:
        return {"status": "empty_pool", "trade_date": day.isoformat()}

    # Rate-gated daily-bar fetches — cap the spend.
    matched: list[dict] = []
    scanned = 0
    for code, row in list(pool.items())[:MAX_POOL_BARS]:
        scanned += 1
        bars = client.get_daily_bars(code)
        hit = _classify(bars) if bars else None
        if hit:
            matched.append({**hit, "code": code, "name": row["name"],
                            "close": bars[-1]["close"]})

    matched.sort(key=lambda c: PRIORITY.index(c["pattern"]))
    picks = matched[:MAX_CANDIDATES]

    with SessionLocal() as db:
        for i, c in enumerate(picks, start=1):
            exists = (db.query(CafeCandidate)
                        .filter(CafeCandidate.trade_date == day,
                                CafeCandidate.code == c["code"])
                        .first())
            if exists:
                continue
            db.add(CafeCandidate(
                trade_date=day, code=c["code"], name=c["name"],
                pattern=c["pattern"], rank=i, close=c["close"],
                stop_px=round(c["stop_px"], 2),
                metrics_json=json.dumps(c["metrics"], ensure_ascii=False)))
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "pool": len(pool),
            "scanned": scanned, "matched": len(matched),
            "picks": [{"code": c["code"], "name": c["name"],
                       "pattern": c["pattern"], "close": c["close"],
                       "stop_px": round(c["stop_px"], 2)} for c in picks]}


_PATTERN_LABELS = {
    "B": "급등 타이트 눌림", "A": "신고가 돌파",
    "C": "급등 눌림 재진입", "D": "낙폭과대 반등",
}


def run_scout_scan(slot: str, trade_date: date | None = None) -> dict:
    """Observation-only scan (14:30/15:00) — same pool + classifier as 15:05,
    stores the top-5 with their at-scan price, trades NOTHING.

    Purpose (user hypothesis 2026-08-06): the recommender buys intraday and
    posts later, so entering earlier than the 15:28 slot may be possible.
    These rows measure (a) pick overlap vs the 15:05 scan and (b) price drift
    scan → close; the accumulated distribution decides whether cafe entries
    move earlier. Idempotent per (day, slot, code)."""
    from .kis_client import get_kis_client
    init_db()
    day = trade_date or date.today()
    client = get_kis_client()
    pool: dict[str, dict] = {}
    for row in client.get_rank_fluctuation() + client.get_rank_volume():
        if row["code"] not in pool and not _excluded(row["name"]):
            pool[row["code"]] = row

    matched: list[dict] = []
    scanned = 0
    for code, row in list(pool.items())[:MAX_POOL_BARS]:
        scanned += 1
        bars = client.get_daily_bars(code)
        hit = _classify(bars) if bars else None
        if hit:
            matched.append({**hit, "code": code, "name": row["name"],
                            "price": bars[-1]["close"]})
    matched.sort(key=lambda c: PRIORITY.index(c["pattern"]))
    picks = matched[:5]

    with SessionLocal() as db:
        for i, c in enumerate(picks, start=1):
            exists = (db.query(CafeScout)
                        .filter(CafeScout.trade_date == day,
                                CafeScout.slot == slot,
                                CafeScout.code == c["code"])
                        .first())
            if exists:
                continue
            db.add(CafeScout(trade_date=day, slot=slot, code=c["code"],
                             name=c["name"], pattern=c["pattern"], rank=i,
                             price=c["price"]))
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "slot": slot,
            "pool": len(pool), "scanned": scanned, "matched": len(matched),
            "picks": [{"code": c["code"], "name": c["name"],
                       "pattern": c["pattern"], "price": c["price"]} for c in picks]}


def submit_cafe_orders(trade_date: date | None = None) -> dict:
    """15:28 — sim-buy today's top candidates at the current KIS quote."""
    from .kis_client import get_kis_client
    from .live_trader import _persist_simulated_fill, _simulated_balance
    init_db()
    day = trade_date or date.today()
    client = get_kis_client()
    seed_slots = max(settings.live_cafe_slots, 1)
    bought: list[dict] = []
    with SessionLocal() as db:
        cands = (db.query(CafeCandidate)
                   .filter(CafeCandidate.trade_date == day)
                   .order_by(CafeCandidate.rank.asc())
                   .all())
        if not cands:
            return {"status": "no_candidates", "trade_date": day.isoformat()}
        snapshot = _simulated_balance(db, strategy=STRATEGY_CAFE)
        held = {h.code for h in snapshot.holdings}
        slot_budget = max(snapshot.total_eval, snapshot.cash) / seed_slots
        cash = snapshot.cash
        for c in cands:
            if c.code in held or len(bought) >= settings.live_cafe_max_buys:
                continue
            q = client.get_quote(c.code)
            px = q.get("price") or c.close
            if not px or px <= 0 or q.get("halted"):
                continue
            budget = min(cash, slot_budget)
            qty = int(budget // px)
            if qty <= 0:
                continue
            label = _PATTERN_LABELS.get(c.pattern, c.pattern)
            reasons = {"action": "buy",
                       "basis": (f"카페 모사 — 패턴 {c.pattern}({label}), "
                                 f"손절 {round(c.stop_px):,}원"),
                       "summary": "", "metrics": json.loads(c.metrics_json or "{}"),
                       "top_features": [], "stop_px": c.stop_px}
            _persist_simulated_fill(db, day, c.code, "BUY", qty, px,
                                    strategy=STRATEGY_CAFE, reasons=reasons)
            # Out-of-universe KOSDAQ codes miss the kr_data name lookup —
            # the ranking TR already gave us the real name, so pin it.
            db.query(Order).filter(Order.trade_date == day,
                                   Order.code == c.code,
                                   Order.strategy == STRATEGY_CAFE,
                                   Order.name.in_((None, c.code))).update(
                {"name": c.name}, synchronize_session=False)
            cash -= qty * px
            bought.append({"code": c.code, "name": c.name, "pattern": c.pattern,
                           "qty": qty, "price": px, "stop_px": c.stop_px})
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "buys": bought}
