"""장 마감 뒤 남은 지정가 매수를 **잔고로** 체결/소멸 판정한다. 계약을 고정한다.

2026-09-28·29 coolreal 매수 2건이 상한가에 걸려 체결되지 않았는데, 15:30 취소는
모의투자 "장종료"로 거부되고 모의 환경은 체결내역 TR 이 비어 있어 두 주문이
영구히 SUBMITTED 로 남았다. `_submit_cafe_like` 는 SUBMITTED 를 "이미 잡은
자리"로 치므로 **그 종목은 다시는 사지 않게 됐다.**

여기서 막는 사고:
  * 소멸한 주문이 SUBMITTED 로 남아 재매수를 영구히 막는 것
  * 진짜 체결을 소멸로 지워 실제 보유가 손절 밖에 놓이는 것 (더 나쁜 쪽)
  * 잔고 조회 실패를 "전부 소멸"로 읽는 것
  * 익일 09:06 재확인이 그날 09:01 에 막 낸 주문을 판정하는 것
"""

import datetime as dt

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.api.db import Base, Fill, Order, STRATEGY_COOLREAL, STRATEGY_OPEN
from app.api.services import balance_reconcile as br
from app.api.services import kis_client as kc

DAY = dt.date(2026, 9, 29)


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


class _NoCloseSession:
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _wire(session, monkeypatch):
    monkeypatch.setattr(br, "init_db", lambda: None)
    monkeypatch.setattr(br, "SessionLocal", lambda: _NoCloseSession(session))


class _Client:
    def __init__(self, holdings=None, fail=False):
        self._h = holdings or {}
        self._fail = fail

    def get_balance(self):
        if self._fail:
            raise RuntimeError("timeout")
        return kc.AccountSnapshot(
            cash=0.0, total_eval=0.0,
            holdings=[kc.Holding(code=c, qty=q, avg_price=p, eval_price=p,
                                 eval_value=q * p, pnl=0.0, pnl_pct=0.0)
                      for c, (q, p) in self._h.items()])


def _order(session, code="052260", qty=182, price=5490.0, day=DAY,
           side="BUY", status="SUBMITTED", strategy=STRATEGY_COOLREAL):
    o = Order(trade_date=day, strategy=strategy, account_id="cool", code=code,
              name=code, side=side, qty=qty, price=price, ord_dvsn="00",
              kis_order_id="0000035072", status=status)
    session.add(o)
    session.commit()
    return o


def _settle(client, day=DAY):
    return br.settle_resting_buys(day, strategy=STRATEGY_COOLREAL, client=client)


def test_not_held_means_expired(session):
    o = _order(session)

    res = _settle(_Client())

    assert o.status == "CANCELLED"
    assert o.error == br.EXPIRED_NOTE
    assert [e["id"] for e in res["expired"]] == [o.id]
    assert session.query(Fill).count() == 0


def test_held_means_filled_at_balance_avg(session):
    o = _order(session, qty=182, price=5490.0)

    res = _settle(_Client({"052260": (182, 5470.0)}))

    assert o.status == "FILLED"
    assert o.price == 5470.0
    f = session.query(Fill).one()
    assert (f.qty, f.price, f.account_id) == (182, 5470.0, "cool")
    assert len(res["filled"]) == 1


def test_partly_held_means_partial(session):
    o = _order(session, qty=182)

    _settle(_Client({"052260": (50, 5490.0)}))

    assert o.status == "PARTIAL"
    assert session.query(Fill).one().qty == 50


def test_existing_position_is_not_counted_as_a_new_fill(session):
    # 어제 산 100주가 원장에 확정돼 있다. 잔고 100주는 그걸로 설명되므로
    # 오늘 주문은 소멸이다 — 옛 보유를 새 체결로 둔갑시키면 안 된다.
    old = _order(session, qty=100, day=DAY - dt.timedelta(days=1), status="FILLED")
    session.add(Fill(order_id=old.id, qty=100, price=5000.0, strategy=STRATEGY_COOLREAL,
                     account_id="cool", filled_at=dt.datetime(2026, 9, 26)))
    new = _order(session, qty=182)

    _settle(_Client({"052260": (100, 5000.0)}))

    assert new.status == "CANCELLED"


def test_unconfirmed_sell_counts_as_gone(session):
    # 청산 시장가 매도가 원장에서 아직 SUBMITTED 여도 나간 것으로 센다 —
    # 그러지 않으면 재매수한 주식이 옛 보유로 설명돼 소멸로 읽힌다.
    old = _order(session, qty=100, day=DAY - dt.timedelta(days=2), status="FILLED")
    session.add(Fill(order_id=old.id, qty=100, price=5000.0, strategy=STRATEGY_COOLREAL,
                     account_id="cool", filled_at=dt.datetime(2026, 9, 25)))
    _order(session, qty=100, day=DAY - dt.timedelta(days=1), side="SELL")
    new = _order(session, qty=182)

    _settle(_Client({"052260": (182, 5490.0)}))

    assert new.status == "FILLED"


def test_balance_failure_writes_nothing(session):
    o = _order(session)

    res = _settle(_Client(fail=True))

    assert res["status"] == "balance_unavailable"
    assert o.status == "SUBMITTED"


def test_orders_after_day_are_left_alone(session):
    # 익일 09:06 재확인은 직전 거래일로 부른다. 그날 아침 막 낸 주문은 장중이다.
    today = _order(session, day=DAY + dt.timedelta(days=1))

    res = _settle(_Client())

    assert res["status"] == "nothing_resting"
    assert today.status == "SUBMITTED"


def test_older_stale_orders_are_swept_too(session):
    # 배포 전부터 남아 있던 9/28 주문도 첫 실행에서 정리돼야 재매수가 풀린다.
    stale = _order(session, code="256840", day=DAY - dt.timedelta(days=1))

    _settle(_Client())

    assert stale.status == "CANCELLED"


def test_idempotent(session):
    _order(session)
    _settle(_Client({"052260": (182, 5470.0)}))

    res = _settle(_Client({"052260": (182, 5470.0)}))

    assert res["status"] == "nothing_resting"
    assert session.query(Fill).count() == 1


def test_other_strategies_are_refused(session):
    res = br.settle_resting_buys(DAY, strategy=STRATEGY_OPEN, client=_Client())
    assert res["status"] == "skipped"
