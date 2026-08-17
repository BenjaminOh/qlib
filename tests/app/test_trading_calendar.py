"""KRX open-day calendar and the beat guard built on it.

2026-08-17 (광복절 대체공휴일) was a Monday. Beat fires `day_of_week="mon-fri"`,
so every scheduled task ran:

  * 09:00 submitted 4 real orders — KIS rejected all four with
    "모의투자 영업일이 아닙니다". No harm, but only because a broker said no.
  * The simulated strategies at 15:20 have no broker. KIS quotes answer on a
    holiday with the previous session's price and `halted=False` (verified on
    the day: 005930 → 274,500), so they would have written fills, a position
    snapshot and a PnL row for a session that never happened.

The root cause is that `_next_trading_day` consulted qlib's calendar, which is
derived from bars that already exist and therefore cannot know a future
holiday — it always fell through to "the next weekday".
"""

import json
from datetime import date

import pytest

pytest.importorskip("requests")

from app.api.services import kis_client as kc  # noqa: E402
from app.api.services import trading_calendar as tc  # noqa: E402


class FakeRedis:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False, px=None):
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(tc, "_redis", lambda: r)
    return r


def _holiday_payload():
    """08-14 Fri open, 08-15~17 closed (광복절 + 주말 + 대체휴일), 08-18 open."""
    return {
        "2026-08-14": True,
        "2026-08-15": False,
        "2026-08-16": False,
        "2026-08-17": False,
        "2026-08-18": True,
        "2026-08-19": True,
    }


# ─── is_market_open ─────────────────────────────────────────────────


def test_holiday_is_reported_closed(redis, monkeypatch):
    class _Client:
        is_mock = False

        def get_open_days(self, d):
            return _holiday_payload()

    monkeypatch.setattr(kc, "get_kis_client", lambda: _Client())

    assert tc.is_market_open(date(2026, 8, 17)) is False
    assert tc.is_market_open(date(2026, 8, 18)) is True


def test_second_lookup_is_served_from_cache(redis, monkeypatch):
    calls = []

    class _Client:
        is_mock = False

        def get_open_days(self, d):
            calls.append(d)
            return _holiday_payload()

    monkeypatch.setattr(kc, "get_kis_client", lambda: _Client())

    tc.is_market_open(date(2026, 8, 17))
    tc.is_market_open(date(2026, 8, 18))
    tc.is_market_open(date(2026, 8, 19))

    assert len(calls) == 1, "KIS는 이 TR을 하루 1회만 호출하라고 요구한다"


def test_unknown_when_kis_fails(redis, monkeypatch):
    class _Client:
        is_mock = False

        def get_open_days(self, d):
            raise RuntimeError("KIS down")

    monkeypatch.setattr(kc, "get_kis_client", lambda: _Client())

    assert tc.is_market_open(date(2026, 8, 17)) is None


def test_unknown_on_mock_client(redis, monkeypatch):
    class _Client:
        is_mock = True

    monkeypatch.setattr(kc, "get_kis_client", lambda: _Client())

    assert tc.is_market_open(date(2026, 8, 17)) is None


def test_unknown_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(tc, "_redis", lambda: None)

    class _Client:
        is_mock = False

        def get_open_days(self, d):
            return _holiday_payload()

    monkeypatch.setattr(kc, "get_kis_client", lambda: _Client())

    # No cache, but the fetch still answers.
    assert tc.is_market_open(date(2026, 8, 17)) is False


# ─── next_open_day ──────────────────────────────────────────────────


def test_next_open_day_skips_the_substitute_holiday(redis, monkeypatch):
    monkeypatch.setattr(tc, "is_market_open",
                        lambda d=None: _holiday_payload().get(d.isoformat()))

    # Friday → the naive "next weekday" answer is Monday 08-17, which is closed.
    assert tc.next_open_day(date(2026, 8, 14)) == date(2026, 8, 18)


def test_next_open_day_does_not_spend_lookups_on_weekends(redis, monkeypatch):
    asked = []

    def _open(d=None):
        asked.append(d)
        return _holiday_payload().get(d.isoformat())

    monkeypatch.setattr(tc, "is_market_open", _open)
    tc.next_open_day(date(2026, 8, 14))

    assert all(d.weekday() < 5 for d in asked), "주말은 API 없이 건너뛰어야 한다"


def test_next_open_day_falls_open_when_calendar_unknown(redis, monkeypatch):
    monkeypatch.setattr(tc, "is_market_open", lambda d=None: None)

    # Unknown must not stall the pipeline — take the weekday guess.
    assert tc.next_open_day(date(2026, 8, 14)) == date(2026, 8, 17)


# ─── beat guard ─────────────────────────────────────────────────────


def test_guard_skips_the_task_on_a_closed_day(monkeypatch):
    tasks = pytest.importorskip("app.api.workers.tasks")

    monkeypatch.setattr(tc, "is_market_open", lambda d=None: False)

    ran = []

    @tasks.market_day_only
    def _task(self):
        ran.append(True)
        return {"status": "ok"}

    res = _task(None)

    assert ran == [], "휴장일에는 태스크 본체가 실행되면 안 된다"
    assert res["status"] == "market_closed"


def test_guard_runs_normally_on_an_open_day(monkeypatch):
    tasks = pytest.importorskip("app.api.workers.tasks")

    monkeypatch.setattr(tc, "is_market_open", lambda d=None: True)

    @tasks.market_day_only
    def _task(self):
        return {"status": "ok"}

    assert _task(None)["status"] == "ok"


def test_guard_fails_open_when_the_calendar_is_unknown(monkeypatch):
    """A broken calendar must not cost a trading session."""
    tasks = pytest.importorskip("app.api.workers.tasks")

    monkeypatch.setattr(tc, "is_market_open", lambda d=None: None)

    @tasks.market_day_only
    def _task(self):
        return {"status": "ok"}

    assert _task(None)["status"] == "ok"


# ─── KIS response parsing ───────────────────────────────────────────


def test_get_open_days_parses_opnd_yn(monkeypatch):
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_ensure_token", lambda: "TOKEN")

    class Resp:
        status_code = 200
        headers = {"tr_cont": "D"}
        text = "{}"

        @staticmethod
        def json():
            return {"output": [
                # 영업일이면서 개장일이 아닌 날이 존재하므로 opnd_yn만 본다.
                {"bass_dt": "20260814", "bzdy_yn": "Y", "opnd_yn": "Y"},
                {"bass_dt": "20260817", "bzdy_yn": "Y", "opnd_yn": "N"},
                {"bass_dt": "20260818", "bzdy_yn": "Y", "opnd_yn": "Y"},
            ]}

    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: Resp())

    days = c.get_open_days(date(2026, 8, 14))

    assert days == {"2026-08-14": True, "2026-08-17": False, "2026-08-18": True}


def test_get_open_days_returns_empty_on_failure(monkeypatch):
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_ensure_token", lambda: "TOKEN")

    def _boom(*a, **kw):
        raise kc.requests.RequestException("network down")

    monkeypatch.setattr(kc.requests, "get", _boom)

    assert c.get_open_days(date(2026, 8, 14)) == {}


def test_cache_is_bucketed_by_month(redis, monkeypatch):
    tc._store({"2026-08-17": False, "2026-09-01": True})

    assert json.loads(redis.data["kis:opendays:2026-08"]) == {"2026-08-17": False}
    assert json.loads(redis.data["kis:opendays:2026-09"]) == {"2026-09-01": True}


# ─── broker-rejection fallback (paper has no holiday TR) ────────────


def test_rejection_text_is_recognised_as_market_closed():
    assert tc.looks_closed("1 모의투자 영업일이 아닙니다.") is True
    assert tc.looks_closed("1 초당 거래건수를 초과하였습니다.") is False
    assert tc.looks_closed(None) is False


def test_marked_closed_beats_a_cached_open_verdict(redis, monkeypatch):
    """The broker refusing business is fact, not forecast — it wins."""
    tc._store({"2026-08-17": True})          # calendar wrongly says open
    tc.mark_closed(date(2026, 8, 17))

    assert tc.is_market_open(date(2026, 8, 17)) is False


def test_order_rejection_records_the_closed_marker(monkeypatch):
    """CTCA0903R is unavailable on 모의투자, so this is the paper path."""
    marked = []
    monkeypatch.setattr(tc, "mark_closed", lambda day=None: marked.append(day))

    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_hashkey", lambda body: "HASH")
    monkeypatch.setattr(c, "_ensure_token", lambda: "TOKEN")
    monkeypatch.setattr(kc, "trading_halted", lambda: None)

    attempts = []

    class Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            attempts.append(1)
            return {"rt_cd": "1", "msg_cd": "40570000",
                    "msg1": "모의투자 영업일이 아닙니다."}

    monkeypatch.setattr(kc.requests, "post", lambda *a, **kw: Resp())

    res = c.place_order("005930", "BUY", 10, price=60_000)

    assert res.ok is False
    assert len(marked) == 1, "휴장 사실이 기록되어야 15:20 시뮬이 건너뛴다"
    assert len(attempts) == 1, "휴장은 재시도해도 소용없다"
