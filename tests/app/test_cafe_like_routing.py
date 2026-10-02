"""카페 계열 매수 공통부가 **전략에 맞는 계좌로** 나가는지 고정한다.

2026-09-15 이전의 `_submit_cafe_like` 는 실주문 경로에서 계좌를 `cafe` 로
하드코딩하고 있었다. 계좌가 둘일 때는 cafereal 하나뿐이라 드러나지 않았지만,
세 번째 계좌(coolreal)가 붙는 순간 두 가지가 동시에 깨진다:

  * 주문이 **다른 계좌의 자격증명**으로 나간다 (한 계좌의 보유가 다른 계좌로 팔린다)
  * `_persist_order` 가 `account_id` 를 받지 못해 실주문 Order 행이 전부 'main'
    으로 남는다 — Fill 은 계좌 축을 알기 때문에 짝이 어긋난다

셋 다 조용히 잘못되는 종류라 테스트로 고정한다.
"""

from datetime import date

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.db import (Base, CafeCandidate, Order, STRATEGY_CAFEREAL,
                        STRATEGY_COOLREAL)
from app.api.services import account_policy as ap
from app.api.services import kis_client as kc
from app.api.services import live_trader as lt
from app.api.services import market_screener as ms

DAY = date(2026, 9, 15)


class _NoCloseSession:
    """세션을 코드에 넘기되 닫지 않는다 (test_broker_seam.py 와 같은 패턴)."""

    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


class _FakeClient:
    """주문 경로만 흉내 낸다. 어느 계좌로 만들어졌는지 기억한다."""

    def __init__(self, account):
        self.account = account

    def get_balance(self):
        return kc.AccountSnapshot(cash=10_000_000.0, total_eval=10_000_000.0,
                                  holdings=[])

    def get_quote(self, code):
        return {"price": 10_000.0}

    def place_order(self, code, side, qty, price=None):
        return kc.OrderResult(ok=True, order_id="ODNO1", code=code, side=side,
                              qty=qty, price=price, raw={}, error=None)


class _Pol:
    is_limit = True


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def env(session, monkeypatch):
    """실주문 경로를 메모리 DB 위에서 결정론적으로 돌린다.

    반환값은 "어느 계좌로 물어봤나"를 담는 dict — 검사 대상이 그것이다.
    """
    asked: dict[str, list] = {"client": [], "policy": []}

    def fake_get_kis_client(account="main"):
        asked["client"].append(account)
        return _FakeClient(account)

    def fake_get_policies(db, account_id):
        asked["policy"].append(account_id)
        return (_Pol(), _Pol())

    monkeypatch.setattr(kc, "get_kis_client", fake_get_kis_client)
    monkeypatch.setattr(ap, "get_policies", fake_get_policies)
    monkeypatch.setattr(ap, "order_price", lambda *a, **kw: 9_700.0)
    monkeypatch.setattr(ms, "init_db", lambda: None)
    monkeypatch.setattr(ms, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(lt, "_stock_name", lambda code: f"name-{code}")
    # KIS_THROTTLE_SECONDS 만큼 실제로 자면 테스트가 느려진다. 자는 것 자체는
    # 이 테스트의 검사 대상이 아니다(그건 test_broker_seam.py 가 본다).
    monkeypatch.setattr("time.sleep", lambda s: None)
    return asked


def _candidate(session, code, ret20, rank=1):
    session.add(CafeCandidate(
        trade_date=DAY, code=code, name=f"종목{code}", pattern="A", rank=rank,
        close=10_000.0, stop_px=9_000.0,
        metrics_json=f'{{"ret20": {ret20}}}'))
    session.commit()


def test_coolreal_uses_its_own_account(session, env):
    _candidate(session, "000001", ret20=10.0)

    ms._submit_cafe_like(DAY, strategy=STRATEGY_COOLREAL, ret20_max=50.0,
                         real=True)

    # 클라이언트도 주문 정책도 cool 계좌에서 와야 한다. 하나라도 cafe 면
    # 남의 계좌 자격증명·남의 주문 방식으로 주문이 나간다.
    assert env["client"] == ["cool"]
    assert env["policy"] == ["cool"]


def test_order_row_records_the_account(session, env):
    _candidate(session, "000001", ret20=10.0)

    ms._submit_cafe_like(DAY, strategy=STRATEGY_COOLREAL, ret20_max=50.0,
                         real=True)

    o = session.query(Order).filter(Order.strategy == STRATEGY_COOLREAL).one()
    # 'main' 으로 남으면 계좌 축이 반쯤만 동작해 곡선이 빈 채로 그려진다.
    assert o.account_id == "cool"


def test_ceiling_applies_on_the_real_path(session, env):
    # 지금까지 상한은 시뮬 경로에서만 실측됐다. 실주문 경로에서도 걸러야 한다.
    _candidate(session, "000001", ret20=60.0, rank=1)   # 과열 — 제외
    _candidate(session, "000002", ret20=10.0, rank=2)   # 통과

    res = ms._submit_cafe_like(DAY, strategy=STRATEGY_COOLREAL, ret20_max=50.0,
                               real=True)

    assert [b["code"] for b in res["buys"]] == ["000002"]


def test_unreadable_ret20_is_skipped_on_the_real_path(session, env):
    # 과열 여부를 모르는 종목을 실계좌로 사는 것이 가장 나쁜 실패다.
    session.add(CafeCandidate(trade_date=DAY, code="000003", name="깨진행",
                              pattern="A", rank=1, close=10_000.0,
                              stop_px=9_000.0, metrics_json="not-json"))
    session.commit()

    res = ms._submit_cafe_like(DAY, strategy=STRATEGY_COOLREAL, ret20_max=50.0,
                               real=True)

    assert res["buys"] == []


def test_cafereal_still_routes_to_the_cafe_account(session, env):
    # 회귀 가드 — 매개변수화하면서 기존 전략의 계좌가 바뀌면 사고다.
    _candidate(session, "000001", ret20=10.0)

    ms._submit_cafe_like(DAY, strategy=STRATEGY_CAFEREAL, ret20_max=None,
                         real=True)

    assert env["client"] == ["cafe"]
    assert env["policy"] == ["cafe"]
    o = session.query(Order).filter(Order.strategy == STRATEGY_CAFEREAL).one()
    assert o.account_id == "cafe"


# ─── 익일 시가 진입(2026-09-30) — 후보 날짜와 주문 날짜가 갈린다 ───────────

def test_source_date_reads_previous_days_candidates(session, env):
    # 전날(DAY) 후보를 오늘(NEXT) 산다. 주문 행은 **주문일**로 남아야
    # 대사·취소 스윕(둘 다 trade_date == 오늘)이 그 주문을 찾는다.
    from datetime import timedelta
    nxt = DAY + timedelta(days=1)
    _candidate(session, "000001", ret20=10.0)

    res = ms._submit_cafe_like(nxt, strategy=STRATEGY_COOLREAL, ret20_max=50.0,
                               real=True, source_date=DAY)

    assert [b["code"] for b in res["buys"]] == ["000001"]
    o = session.query(Order).filter(Order.strategy == STRATEGY_COOLREAL).one()
    assert o.trade_date == nxt


def test_without_source_date_todays_candidates_only(session, env):
    # 시뮬(cafe·cafecool)은 source_date 를 넘기지 않는다 — 동결 곡선의 회귀 가드.
    from datetime import timedelta
    _candidate(session, "000001", ret20=10.0)

    res = ms._submit_cafe_like(DAY + timedelta(days=1),
                               strategy=STRATEGY_COOLREAL, ret20_max=50.0,
                               real=True)

    assert res["status"] == "no_candidates"


@pytest.mark.parametrize("name,strategy", [
    ("submit_cafereal_orders", STRATEGY_CAFEREAL),
    ("submit_coolreal_orders", STRATEGY_COOLREAL),
])
def test_real_wrappers_buy_previous_sessions_picks(monkeypatch, name, strategy):
    seen = {}

    def fake(trade_date, **kw):
        seen.update(kw, trade_date=trade_date)
        return {}

    monkeypatch.setattr(ms, "_submit_cafe_like", fake)
    monkeypatch.setattr(ms, "_prev_session", lambda d: date(2026, 9, 29))

    getattr(ms, name)(date(2026, 9, 30))

    assert seen["strategy"] == strategy
    assert seen["real"] is True
    assert seen["trade_date"] == date(2026, 9, 30)
    assert seen["source_date"] == date(2026, 9, 29)


def test_real_entries_fire_in_the_morning_not_at_close():
    pytest.importorskip("celery")
    from app.api.workers.celery_app import celery_app
    beat = celery_app.conf.beat_schedule
    by_task = {}
    for key, entry in beat.items():
        by_task.setdefault(entry["task"], []).append(entry["schedule"])
    for task in ("live_orders_cafereal", "live_orders_coolreal"):
        (sched,) = by_task[task]  # 정확히 한 슬롯 — 15:28 이 남아 있으면 이중 매수
        assert sched.hour == {9} and sched.minute == {1}, task
