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
from ..db import (CafeCandidate, CafeScout, MarketPoolSnapshot, Order,
                  OrderbookSnapshot, SessionLocal, STRATEGY_CAFE, STRATEGY_CAFECOOL, STRATEGY_CAFEREAL,
                  STRATEGY_SURGE, SurgePick, init_db)

log = logging.getLogger(__name__)

# Pool hygiene: instruments the recommender never touches.
_EXCLUDE_TOKENS = ("스팩", "ETF", "ETN", "리츠", "인버스", "레버리지", "채권",
                   "TIGER", "KODEX", "KBSTAR", "ACE ", "SOL ", "PLUS ")

MAX_POOL_BARS = 25       # codes we spend daily-bar calls on (~30s at the gate)
MAX_CANDIDATES = 2       # picks stored per day (recommender bets 1-2 names)
PRIORITY = ("B", "A", "R", "C", "D")


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
    # Stop = the broken closing-high level itself ("돌파 기점 붕괴 시 무효" —
    # TXR/JW신약 표본). The old today.low*0.95 hit −30% on wide surge days.
    prev30_high = max(b["high"] for b in bars[-31:-1])
    if today["close"] >= max(closes[:-1]) and ret20 >= 0.30 and vol20 and today["volume"] >= 2 * vol20:
        return {"pattern": "A", "stop_px": max(closes[:-1]) * 0.97, "metrics": m}

    # R — 저항대 돌파 (JW신약형): closes above the last-5-day resistance while
    # still BELOW the 30d high, with a live 20d surge and volume expansion.
    # The recommender's "돌파" fires here, one step earlier than a fresh high
    # (validated 2026-08-06: caught JW신약, stop within 1% of his 2,020).
    prev5_high = max(b["high"] for b in bars[-6:-1])
    if today["close"] > prev5_high and today["high"] < prev30_high \
            and ret20 >= 0.15 and vol20 and today["volume"] >= 2 * vol20:
        return {"pattern": "R", "stop_px": prev5_high * 0.97,
                "metrics": {**m, "broke_level": prev5_high}}

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


def _eve_features(bars: list[dict]) -> dict:
    """Close-time features for the surge-eve research (Track 2) — mirrors
    scripts/mine_surge_eve.py so the forward small-cap data joins the
    universe backtest table."""
    today = bars[-1]
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    vol20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 0
    prev5_high = max(b["high"] for b in bars[-6:-1]) if len(bars) >= 6 else None
    prev30_high = max(b["high"] for b in bars[-31:-1]) if len(bars) >= 6 else None
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    return {
        "close": today["close"],
        "ret5": round(today["close"] / closes[-6] - 1, 4) if len(closes) >= 6 else None,
        "ret20": round(today["close"] / closes[-21] - 1, 4) if len(closes) >= 21 else None,
        "vol_x": round(today["volume"] / vol20, 2) if vol20 else None,
        "pos_vs_5d_high": round(today["close"] / prev5_high - 1, 4) if prev5_high else None,
        "off_30d_high": round(today["close"] / prev30_high - 1, 4) if prev30_high else None,
        "green": int(today["close"] > today["open"]),
        "ma20_gap": round(today["close"] / ma20 - 1, 4) if ma20 else None,
    }


def _effective(hit: dict, close: float) -> dict | None:
    """Floor the structural stop at the −cap the exit engine enforces, so what
    is stored/alerted equals what actually triggers. A stop at/above the price
    means the setup is already invalid (broke back below its level) — drop it."""
    stop = max(hit["stop_px"], close * (1 - settings.live_cafe_stop_cap))
    if stop >= close:
        return None
    return {**hit, "stop_px": stop}


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
    pool_snaps: list[dict] = []
    for code, row in list(pool.items())[:MAX_POOL_BARS]:
        scanned += 1
        bars = client.get_daily_bars(code)
        if not bars:
            continue
        hit = _classify(bars)
        if hit:
            hit = _effective(hit, bars[-1]["close"])
        if hit:
            matched.append({**hit, "code": code, "name": row["name"],
                            "close": bars[-1]["close"]})
        if len(bars) >= 6:
            pool_snaps.append({"code": code, "name": row["name"],
                               "pattern": hit["pattern"] if hit else None,
                               **_eve_features(bars)})

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
        # Track 2 (surge-eve research): snapshot the WHOLE scanned pool —
        # next-day labels are joined at analysis time, nothing trades on this.
        for s in pool_snaps:
            exists = (db.query(MarketPoolSnapshot)
                        .filter(MarketPoolSnapshot.trade_date == day,
                                MarketPoolSnapshot.code == s["code"])
                        .first())
            if exists:
                continue
            db.add(MarketPoolSnapshot(
                trade_date=day, code=s["code"], name=s["name"],
                close=s["close"], ret5=s["ret5"], ret20=s["ret20"],
                vol_x=s["vol_x"], pos_vs_5d_high=s["pos_vs_5d_high"],
                off_30d_high=s["off_30d_high"], green=s["green"],
                ma20_gap=s.get("ma20_gap"),
                matched_pattern=s["pattern"]))
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "pool": len(pool),
            "scanned": scanned, "matched": len(matched),
            "picks": [{"code": c["code"], "name": c["name"],
                       "pattern": c["pattern"], "close": c["close"],
                       "stop_px": round(c["stop_px"], 2)} for c in picks]}


_PATTERN_LABELS = {
    "B": "급등 타이트 눌림", "A": "신고가 돌파", "R": "저항대 돌파",
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
            hit = _effective(hit, bars[-1]["close"])
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


def surge_score(s) -> float | None:
    """Transparent scoring of the mined surge-eve profile (2026-08-07 study):
    strongest odds tomorrow = already trending (+ret20), pulled −3~−15% off
    the 5d high (sweet spot ≈ −6.5%), above MA20, volume already expanded.
    Returns None when the row fails the profile's hard gates."""
    if s.ret20 is None or s.pos_vs_5d_high is None:
        return None
    pull = s.pos_vs_5d_high * 100
    if s.ret20 < 0.05 or pull > -1 or pull < -15:
        return None
    score = s.ret20 * 100
    score += 10 - abs(pull + 6.5)
    score += min(s.vol_x or 0, 5) * 2
    score += 5 if (s.ma20_gap or 0) > 0 else -5
    return round(score, 2)


def run_surge_screen(trade_date: date | None = None) -> dict:
    """15:12 — score today's pool snapshots with the surge-eve profile and
    store the TOP10. Reads market_pool_snapshots only (zero extra KIS calls)."""
    init_db()
    day = trade_date or date.today()
    with SessionLocal() as db:
        snaps = (db.query(MarketPoolSnapshot)
                   .filter(MarketPoolSnapshot.trade_date == day)
                   .all())
        scored = []
        for s in snaps:
            sc = surge_score(s)
            if sc is not None:
                scored.append((sc, s))
        scored.sort(key=lambda x: -x[0])
        picks = scored[:10]
        for i, (sc, s) in enumerate(picks, start=1):
            exists = (db.query(SurgePick)
                        .filter(SurgePick.trade_date == day, SurgePick.code == s.code)
                        .first())
            if exists:
                continue
            db.add(SurgePick(
                trade_date=day, rank=i, code=s.code, name=s.name,
                close=s.close, score=sc,
                metrics_json=json.dumps({
                    "ret20": s.ret20, "pos_vs_5d_high": s.pos_vs_5d_high,
                    "vol_x": s.vol_x, "ma20_gap": s.ma20_gap}, ensure_ascii=False)))
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(),
            "pool": len(snaps), "matched": len(scored),
            "picks": [{"rank": i + 1, "code": s.code, "name": s.name,
                       "close": s.close, "score": sc}
                      for i, (sc, s) in enumerate(picks)]}


def submit_surge_orders(trade_date: date | None = None) -> dict:
    """15:29 — sim-buy today's top surge picks at the live KIS quote."""
    from .kis_client import get_kis_client
    from .live_trader import _persist_simulated_fill, _simulated_balance
    init_db()
    day = trade_date or date.today()
    client = get_kis_client()
    seed_slots = max(settings.live_surge_slots, 1)
    bought: list[dict] = []
    with SessionLocal() as db:
        cands = (db.query(SurgePick)
                   .filter(SurgePick.trade_date == day)
                   .order_by(SurgePick.rank.asc())
                   .all())
        if not cands:
            return {"status": "no_candidates", "trade_date": day.isoformat()}
        snapshot = _simulated_balance(db, strategy=STRATEGY_SURGE)
        held = {h.code for h in snapshot.holdings}
        slot_budget = max(snapshot.total_eval, snapshot.cash) / seed_slots
        cash = snapshot.cash
        for c in cands:
            if c.code in held or len(bought) >= settings.live_surge_max_buys:
                continue
            q = client.get_quote(c.code)
            px = q.get("price") or c.close
            if not px or px <= 0 or q.get("halted"):
                continue
            budget = min(cash, slot_budget)
            qty = int(budget // px)
            if qty <= 0:
                continue
            reasons = {"action": "buy",
                       "basis": (f"급등 전야 {c.rank}위 — 추세+눌림 프로파일 "
                                 f"(점수 {c.score})"),
                       "summary": "", "metrics": json.loads(c.metrics_json or "{}"),
                       "top_features": [],
                       "fill_source": "quote" if q.get("price") else "fallback_scan_price"}
            _persist_simulated_fill(db, day, c.code, "BUY", qty, px,
                                    strategy=STRATEGY_SURGE, reasons=reasons)
            db.query(Order).filter(Order.trade_date == day,
                                   Order.code == c.code,
                                   Order.strategy == STRATEGY_SURGE,
                                   Order.name.in_((None, c.code))).update(
                {"name": c.name}, synchronize_session=False)
            cash -= qty * px
            bought.append({"code": c.code, "name": c.name, "rank": c.rank,
                           "qty": qty, "price": px})
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(),
            "strategy": strategy, "buys": bought,
            "skipped_overheated": skipped_hot}


def capture_orderbook(slot: str, trade_date: date | None = None) -> dict:
    """15:07 / 15:27 — snapshot the book for today's cafe candidates.

    Observation only. The 15:28 sim buy calls get_quote(), which reports a
    price and nothing about whether that price had size behind it, then books
    a full fill. This records what get_quote() throws away so the fill can be
    audited after the fact rather than argued about.

    Slot semantics differ by design (see OrderbookSnapshot): "1505" lands in
    정규장 and carries a real ask ladder; "1527" lands inside 동시호가 where
    the ladder is meaningless and 예상체결수량 is the number that matters.

    Never raises into the caller and never touches a trading path — a failed
    capture costs one row of research data, and that is all it may cost.
    """
    from .kis_client import get_kis_client
    init_db()
    day = trade_date or date.today()
    client = get_kis_client()
    captured: list[dict] = []
    with SessionLocal() as db:
        cands = (db.query(CafeCandidate)
                   .filter(CafeCandidate.trade_date == day)
                   .order_by(CafeCandidate.rank.asc())
                   .all())
        if not cands:
            return {"status": "no_candidates", "trade_date": day.isoformat(),
                    "slot": slot}
        for c in cands:
            existing = (db.query(OrderbookSnapshot)
                          .filter(OrderbookSnapshot.trade_date == day,
                                  OrderbookSnapshot.slot == slot,
                                  OrderbookSnapshot.code == c.code)
                          .first())
            if existing:
                continue  # idempotent — a retry must not double-write
            try:
                q = client.get_quote(c.code)
                book = client.get_orderbook(c.code)
            except Exception as exc:  # noqa: BLE001
                log.warning("capture_orderbook %s/%s failed: %s", slot, c.code, exc)
                continue
            if not book and not q:
                continue
            price = q.get("price")
            upper = q.get("upper_limit")
            asks = book.get("asks") or []
            db.add(OrderbookSnapshot(
                trade_date=day, slot=slot, code=c.code, name=c.name,
                price=price, upper_limit_px=upper,
                at_upper_limit=int(bool(price and upper and price >= upper)),
                total_ask_qty=book.get("total_ask_qty"),
                total_bid_qty=book.get("total_bid_qty"),
                ask_qty_1=(asks[0]["qty"] if asks else None),
                antc_price=book.get("antc_price"),
                antc_qty=book.get("antc_qty"),
                book_json=json.dumps({"asks": asks, "bids": book.get("bids") or []},
                                     ensure_ascii=False),
            ))
            captured.append({"code": c.code, "name": c.name, "price": price,
                             "at_upper_limit": bool(price and upper and price >= upper),
                             "total_ask_qty": book.get("total_ask_qty"),
                             "antc_qty": book.get("antc_qty")})
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "slot": slot,
            "captured": captured}


def submit_cafe_orders(trade_date: date | None = None) -> dict:
    """15:28 — sim-buy today's top candidates at the current KIS quote."""
    return _submit_cafe_like(trade_date, strategy=STRATEGY_CAFE, ret20_max=None)


def submit_cafereal_orders(trade_date: date | None = None) -> dict:
    """15:28 — cafe 와 같은 픽을 **실계좌**로 산다.

    규칙(픽·슬롯·손절·익절)이 cafe 와 완전히 같고 다른 것은 주문이 실제라는 것뿐이다.
    그래서 cafe(시뮬) 곡선과의 격차가 곧 **체결 가정이 부풀린 크기**다 — 호가 8건이
    전부 상한가·매도잔량 0이었던 그 질문에 유일하게 답할 수 있는 방법이다.

    계좌 미설정이면 조용히 건너뛴다. 카페 계좌는 선택 사항이고, 없다고 해서
    나머지 전략이 멈춰서는 안 된다.
    """
    return _submit_cafe_like(trade_date, strategy=STRATEGY_CAFEREAL,
                             ret20_max=None, real=True)


def submit_cafecool_orders(trade_date: date | None = None) -> dict:
    """15:28 — cafe 와 같은 후보에서 ret20 상한을 넘는 종목만 뺀 쌍둥이.

    후보 스캔을 다시 돌리지 않고 `cafe_candidates` 를 그대로 읽는다. 같은 행을
    공유하므로 두 곡선의 차이는 **오직 ret20 상한**이며, 스캔 비용도 KIS 호출도
    늘지 않는다(진입 시세 조회만 각자 한다).
    """
    return _submit_cafe_like(trade_date, strategy=STRATEGY_CAFECOOL,
                             ret20_max=settings.live_cafecool_ret20_max)


def _submit_cafe_like(trade_date: date | None, *, strategy: str,
                      ret20_max: float | None, real: bool = False) -> dict:
    """cafe 계열 15:28 매수 공통부.

    `ret20_max` 가 주어지면 진입 시점 ret20(%)이 그 값 이상인 후보를 건너뛴다.
    cafe 는 None 이라 종전과 완전히 동일하게 동작한다.

    `real=True` 면 별도 계좌로 **실주문**을 낸다 — 잔고도 장부가 아니라 KIS 가
    진실이고, 주문 방식은 `trading_accounts` 의 cafe 행을 따른다.
    """
    from .account_policy import BasePriceUnavailable, get_policies, order_price
    from .kis_client import (ACCOUNT_CAFE, AccountNotConfigured, get_kis_client)
    from .live_trader import (CAFE_ACCOUNT_ID, KIS_THROTTLE_SECONDS,
                              _persist_order, _persist_simulated_fill,
                              _simulated_balance)
    import time as _time
    init_db()
    day = trade_date or date.today()
    if real:
        try:
            client = get_kis_client(ACCOUNT_CAFE)
        except (AccountNotConfigured, ValueError) as exc:
            log.info("cafereal 건너뜀 — %s", exc)
            return {"status": "no_account", "strategy": strategy,
                    "trade_date": day.isoformat(), "reason": str(exc)}
    else:
        client = get_kis_client()
    seed_slots = max(settings.live_cafe_slots, 1)
    bought: list[dict] = []
    skipped_hot: list[dict] = []
    with SessionLocal() as db:
        cands = (db.query(CafeCandidate)
                   .filter(CafeCandidate.trade_date == day)
                   .order_by(CafeCandidate.rank.asc())
                   .all())
        if not cands:
            return {"status": "no_candidates", "trade_date": day.isoformat(),
                    "strategy": strategy}
        # 실계좌는 KIS 잔고가 진실이다. 장부로 재구성하면 수동 매매나 미체결이
        # 반영되지 않아 실제보다 많이 사려 든다.
        snapshot = client.get_balance() if real else _simulated_balance(db, strategy=strategy)
        held = {h.code for h in snapshot.holdings}
        slot_budget = max(snapshot.total_eval, snapshot.cash) / seed_slots
        cash = snapshot.cash
        buy_pol = get_policies(db, CAFE_ACCOUNT_ID)[0] if real else None
        for c in cands:
            if c.code in held or len(bought) >= settings.live_cafe_max_buys:
                continue
            if ret20_max is not None:
                try:
                    ret20 = json.loads(c.metrics_json or "{}").get("ret20")
                except Exception:  # noqa: BLE001
                    ret20 = None
                # ret20 를 못 읽으면 보수적으로 건너뛴다 — 과열 여부를 모르는
                # 종목을 사는 것이 이 쌍둥이의 취지에 어긋난다.
                if ret20 is None or ret20 >= ret20_max:
                    skipped_hot.append({"code": c.code, "name": c.name,
                                        "ret20": ret20})
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
                       "top_features": [], "stop_px": c.stop_px,
                       "fill_source": "quote" if q.get("price") else "fallback_scan_price"}
            if real:
                try:
                    entry_px = (order_price(client, c.code, buy_pol, day, quote=q)
                                if buy_pol.is_limit else None)
                except BasePriceUnavailable as exc:
                    log.warning("cafereal BUY %s 건너뜀 — 기준가 없음: %s", c.code, exc)
                    continue
                if entry_px:
                    qty = int(min(cash, slot_budget) // entry_px)
                    if qty <= 0:
                        continue
                res = client.place_order(c.code, "BUY", qty, price=entry_px)
                _persist_order(db, day, c.code, "BUY", qty, entry_px, res,
                               strategy=strategy, reasons=reasons)
                if not res.ok:
                    log.warning("cafereal BUY REJECTED code=%s qty=%d error=%s",
                                c.code, qty, res.error)
                _time.sleep(KIS_THROTTLE_SECONDS)
                px = entry_px or px
            else:
                _persist_simulated_fill(db, day, c.code, "BUY", qty, px,
                                        strategy=strategy, reasons=reasons)
            # Out-of-universe KOSDAQ codes miss the kr_data name lookup —
            # the ranking TR already gave us the real name, so pin it.
            db.query(Order).filter(Order.trade_date == day,
                                   Order.code == c.code,
                                   Order.strategy == strategy,
                                   Order.name.in_((None, c.code))).update(
                {"name": c.name}, synchronize_session=False)
            cash -= qty * px
            bought.append({"code": c.code, "name": c.name, "pattern": c.pattern,
                           "qty": qty, "price": px, "stop_px": c.stop_px})
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "buys": bought}
