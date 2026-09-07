"""KRX open-day calendar, sourced from KIS 국내휴장일조회 (CTCA0903R).

Why this exists — 2026-08-17 (광복절 대체공휴일):

  * Celery beat fires on `day_of_week="mon-fri"`, which knows nothing about
    Korean market holidays.
  * `live_trader._next_trading_day` asks qlib for the calendar, but qlib's
    calendar is built from HISTORICAL bars — it has no future dates at all, so
    the lookup always falls through to "the next weekday". Friday's 16:20
    signal run therefore stamped `as_of=2026-08-17` on a closed Monday.
  * At 09:00 the open strategy submitted 4 real orders. KIS rejected every one
    with "모의투자 영업일이 아닙니다" — harmless, because a broker refused them.
  * The simulated strategies have no broker to refuse them. KIS quotes answer
    on a holiday with the previous session's price and `halted=False`
    (verified: 005930 → 274,500), so at 15:20 they would have written SIMULATED
    fills, a PositionSnapshot and a DailyPnL row for a day the market never
    opened — a phantom trading day in all eleven curves.

KIS's own docs ask for **at most one call per day** ("당사 원장서비스와 연관되어
있어 … 가급적 1일 1회 호출"), so results are cached in redis and a fetch covers
a wide forward window in a single request.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from ..config import settings

log = logging.getLogger(__name__)

# One entry per calendar month of fetched data. A single CTCA0903R call returns
# a forward run of days, so a month-keyed cache means the once-a-day budget is
# spent at most when we cross into an unseen month.
_KEY = "kis:opendays:{ym}"
_TTL_S = 40 * 24 * 3600      # well past a month, so a cached month never lapses mid-use

# CTCA0903R is a REAL-environment TR — on 모의투자 it answers
# "모의투자 TR 이 아닙니다" (verified 2026-08-17), so the paper setup gets no
# answer from it at all. The broker still tells us the market is shut, just
# later and in a different place: it rejects the 09:00 orders with
# "영업일이 아닙니다". Recording that verdict closes the gap for paper, and it
# lands well before the 15:20 simulated strategies — which are the ones that
# would actually write phantom data.
_CLOSED_KEY = "kis:marketclosed:{iso}"
_CLOSED_TTL_S = 3 * 24 * 3600
CLOSED_MARKERS = ("영업일이 아닙니다",)

# ─── Static KRX calendar ────────────────────────────────────────────
#
# The rejection marker above only teaches us about a holiday ON the day itself,
# which is too late for the one decision that matters most: which date the
# Friday-evening signal run stamps on tomorrow's picks. On 2026-08-14 nothing
# had yet been rejected, so the run labelled its output `as_of=2026-08-17` — a
# 광복절 대체공휴일. Monday's orders bounced, and Tuesday looked for signals
# dated 08-18 that no one had produced. A full session traded nothing while a
# perfectly good analysis sat in the table under the wrong date.
#
# On a real account CTCA0903R answers ahead of time and none of this is needed.
# This list is the 모의투자 stand-in, and it is worth maintaining only until the
# real account takes over.
#
# ⚠️ ONE ENTRY PER YEAR, and the year key is load-bearing: a date missing from a
# year we *do* list means "open", while a year we do not list means "unknown"
# (and callers fail open). Adding a year therefore asserts the list is complete
# for it — verify against KRX before doing so.
#
# Getting it wrong is asymmetric. A missed holiday costs nothing new: orders go
# out and the broker rejects them, exactly as before. A date wrongly marked
# closed **skips a real trading day**. When unsure, leave it out.
#
# 2026 — 공휴일 + 대체공휴일 + 연말 폐장. Cross-checked against KRX holiday
# listings; 08-17 independently confirmed by KIS rejecting live orders that day.
KRX_HOLIDAYS: dict[int, frozenset[str]] = {
    2026: frozenset({
        "2026-01-01",                                # 신정
        "2026-02-16", "2026-02-17", "2026-02-18",    # 설 연휴
        "2026-03-02",                                # 3·1절 대체 (3/1 일요일)
        "2026-05-01",                                # 근로자의 날
        "2026-05-05",                                # 어린이날
        "2026-05-25",                                # 부처님오신날
        "2026-06-03",                                # 제9회 전국동시지방선거
        "2026-08-17",                                # 광복절 대체 (8/15 토요일)
        "2026-09-24", "2026-09-25",                  # 추석 연휴
        "2026-10-05",                                # 개천절 대체 (10/3 토요일)
        "2026-10-09",                                # 한글날
        "2026-12-25",                                # 성탄절
        # 공휴일이 아니라 거래소 연말 폐장일 — 공휴일 목록만 보면 빠뜨린다.
        "2026-12-31",
    }),
}


def _static_verdict(day: date) -> bool | None:
    """Open/closed from the bundled calendar, or None if the year is unlisted."""
    holidays = KRX_HOLIDAYS.get(day.year)
    if holidays is None:
        return None
    if day.weekday() >= 5:
        return False
    return day.isoformat() not in holidays


_redis_client = None


def looks_closed(error: str | None) -> bool:
    """True if a KIS rejection means "the market is shut today"."""
    return bool(error) and any(m in error for m in CLOSED_MARKERS)


def mark_closed(day: date | None = None) -> None:
    """Remember that the broker refused business on `day` (best effort)."""
    day = day or date.today()
    r = _redis()
    if r is None:
        return
    try:
        r.set(_CLOSED_KEY.format(iso=day.isoformat()), "1", ex=_CLOSED_TTL_S)
        log.warning("trading_calendar: broker reports %s closed — later tasks will skip",
                    day.isoformat())
    except Exception as exc:  # noqa: BLE001
        log.warning("trading_calendar: closed-marker write failed: %s", exc)


def _marked_closed(day: date) -> bool:
    r = _redis()
    if r is None:
        return False
    try:
        return bool(r.get(_CLOSED_KEY.format(iso=day.isoformat())))
    except Exception:  # noqa: BLE001
        return False


def _redis():
    global _redis_client
    if _redis_client is None:
        try:
            import redis  # type: ignore[import-not-found]
            _redis_client = redis.Redis.from_url(
                settings.celery_broker_url, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2)
        except Exception as exc:  # noqa: BLE001
            log.warning("trading_calendar: redis unavailable: %s", exc)
            return None
    return _redis_client


def _cached_month(ym: str) -> dict[str, bool] | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(_KEY.format(ym=ym))
    except Exception as exc:  # noqa: BLE001
        log.warning("trading_calendar: cache read failed: %s", exc)
        return None
    return json.loads(raw) if raw else None


def _store(days: dict[str, bool]) -> None:
    """Split a fetched run into month buckets and cache each."""
    r = _redis()
    if r is None or not days:
        return
    months: dict[str, dict[str, bool]] = {}
    for iso, is_open in days.items():
        months.setdefault(iso[:7], {})[iso] = is_open
    for ym, bucket in months.items():
        try:
            existing = _cached_month(ym) or {}
            existing.update(bucket)
            r.set(_KEY.format(ym=ym), json.dumps(existing), ex=_TTL_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("trading_calendar: cache write failed for %s: %s", ym, exc)


def is_market_open(day: date | None = None) -> bool | None:
    """True/False if KRX trades on `day`; None when it cannot be determined.

    **None means unknown, and callers must fail OPEN** — treat it as a normal
    trading day. A calendar lookup that breaks (redis down, KIS unreachable,
    credentials missing) must never silently halt trading; the failure mode we
    are fixing is writing phantom data on a closed day, not missing a session.
    """
    day = day or date.today()
    iso = day.isoformat()

    # The broker's own refusal outranks everything — it is not a prediction.
    if _marked_closed(day):
        return False

    cached = _cached_month(iso[:7])
    if cached is not None and iso in cached:
        return cached[iso]

    fetched: dict[str, bool] = {}
    try:
        from .kis_client import get_kis_client
        client = get_kis_client()
        if not client.is_mock:      # no credentials — nothing to ask
            fetched = client.get_open_days(day)
    except Exception as exc:  # noqa: BLE001
        log.warning("trading_calendar: KIS holiday lookup failed for %s: %s", iso, exc)
    if fetched:
        _store(fetched)
        if iso in fetched:
            return fetched[iso]

    # Last resort, and the only source that answers at all on 모의투자 —
    # CTCA0903R is a real-environment TR. Still returns None for a year the
    # bundled list does not cover, so callers keep failing open.
    return _static_verdict(day)


def next_open_day(after: date, limit: int = 14) -> date | None:
    """First open day strictly after `after`, or None if undeterminable.

    Used to stamp signals with the day they are actually for. qlib's calendar
    cannot answer this — it only knows days that already have bars.
    """
    for i in range(1, limit + 1):
        d = after + timedelta(days=i)
        if d.weekday() >= 5:
            continue                       # weekend: free to skip, no API needed
        open_ = is_market_open(d)
        if open_ is None:
            return d                       # unknown → fail open on the weekday guess
        if open_:
            return d
    return None
