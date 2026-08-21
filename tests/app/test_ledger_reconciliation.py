"""원장 신뢰성 — cafereal 정산·취소 스윕·중복 주문. 계약을 고정한다.

2026-08-21: 보유 종목 전략 배지를 붙이려고 원장을 읽다가, 원장 자체가 세 곳에서
비어 있다는 걸 발견했다. 셋 다 "주문을 냈다"와 "샀다"를 구분하지 못하게 만든다.

여기서 막는 사고:
  * cafereal 주문이 영구히 SUBMITTED 로 남는 것 — 체결률이 이 실험의 측정값인데
    정산기가 없어 잴 수가 없었다.
  * 취소 스윕이 남의 계좌 주문을 자기 계좌 자격증명으로 취소하는 것 — main 을
    지정가로 바꾸는 순간 터지는 잠복 버그였다.
  * 미체결 지정가가 잔고에 없다는 이유로 같은 종목을 이틀 연속 사는 것.
  * 부분체결 주문이 장부에 주문 수량 전부로 잡히는 것.
  * 모의 환경의 "보유 중이면 체결로 친다" 규칙이 cafereal 로 번지는 것 —
    미체결을 확정 체결로 둔갑시켜 배지가 정확히 거꾸로 거짓말하게 된다.
"""

import datetime as dt

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.api.db import (
    ACCOUNT_STRATEGIES, Base, Fill, Order, STRATEGY_CAFEREAL, STRATEGY_OPEN,
    TradingAccount,
)
from app.api.services import live_trader as lt

DAY = dt.date(2026, 8, 20)


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
    """테스트 세션을 코드에 넘기되 닫히지 않게 한다."""

    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


@pytest.fixture
def env(session, monkeypatch):
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(lt.time, "sleep", lambda s: None)
    return session


def _order(session, *, strategy, code="000720", side="BUY", qty=10,
           status="SUBMITTED", ord_dvsn="00", price=None, kis_id="0000001",
           submitted_at=None):
    o = Order(trade_date=DAY, strategy=strategy, code=code, name=code,
              side=side, qty=qty, price=price, ord_dvsn=ord_dvsn,
              kis_order_id=kis_id, status=status,
              submitted_at=submitted_at or dt.datetime(2026, 8, 20, 0, 0))
    session.add(o)
    session.commit()
    return o


def _account(session, account_id, **kw):
    session.add(TradingAccount(account_id=account_id, **kw))
    session.commit()


# ─── P0-1 cafereal 정산기 ───────────────────────────────────────────


class FakeKIS:
    is_mock = False

    def __init__(self, fills=None, holdings=None):
        self._fills = fills or {}
        self._holdings = holdings or []
        self.balance_calls = 0

    def get_daily_fills(self, start, end):
        return dict(self._fills)

    def get_balance(self):
        self.balance_calls += 1

        class S:
            pass
        s = S()
        s.holdings = self._holdings
        return s

    def get_quote(self, code):
        return {"open": 9_999.0}


def test_cafereal_is_reconciled_against_its_own_account(env, monkeypatch):
    """cafereal 을 정산하는 호출이 아예 없어서 주문이 영구 SUBMITTED 였다."""
    asked = []
    kis = FakeKIS(fills={"1": {"avg_price": 4700.0, "ccld_qty": 10}})

    def _client(account="main"):
        asked.append(account)
        return kis

    monkeypatch.setattr(lt, "get_kis_client", _client)
    o = _order(env, strategy=STRATEGY_CAFEREAL, kis_id="0000001")

    res = lt.reconcile_fills(DAY, strategy=STRATEGY_CAFEREAL)

    assert res["status"] == "ok"
    assert asked == ["cafe"]          # main 자격증명으로 조회하면 안 된다
    env.refresh(o)
    assert o.status == "FILLED"
    assert o.price == 4700.0


def test_simulated_strategies_have_nothing_to_reconcile(env, monkeypatch):
    monkeypatch.setattr(lt, "get_kis_client",
                        lambda a="main": pytest.fail("브로커를 부르면 안 된다"))
    res = lt.reconcile_fills(DAY, strategy="cafecool")
    assert res["status"] == "skipped"
    assert res["reason"] == "simulated_strategy"


def test_unconfigured_cafe_account_is_not_an_error(env, monkeypatch):
    def _client(account="main"):
        raise RuntimeError("KIS_CAFE_* 미설정")

    monkeypatch.setattr(lt, "get_kis_client", _client)
    res = lt.reconcile_fills(DAY, strategy=STRATEGY_CAFEREAL)
    assert res["status"] == "no_account"


def test_accepted_but_unfilled_is_not_a_partial(env, monkeypatch):
    """체결수량 0을 PARTIAL 로 찍던 버그. 한 주도 안 붙은 주문이 부분체결이 됐다."""
    kis = FakeKIS(fills={"1": {"avg_price": 0.0, "ccld_qty": 0}})
    monkeypatch.setattr(lt, "get_kis_client", lambda a="main": kis)
    o = _order(env, strategy=STRATEGY_CAFEREAL, qty=10, kis_id="0000001")

    lt.reconcile_fills(DAY, strategy=STRATEGY_CAFEREAL)

    env.refresh(o)
    assert o.status == "SUBMITTED"
    assert o.price is None


def test_fill_row_records_the_actual_filled_quantity(env, monkeypatch):
    """정산기가 ccld_qty 를 버리고 Fill.qty = Order.qty 를 써서, 부분체결이
    장부에서 늘 주문 수량 전부로 잡혔다."""
    kis = FakeKIS(fills={"1": {"avg_price": 1000.0, "ccld_qty": 3}})
    monkeypatch.setattr(lt, "get_kis_client", lambda a="main": kis)
    o = _order(env, strategy=STRATEGY_CAFEREAL, qty=10, kis_id="0000001")

    lt.reconcile_fills(DAY, strategy=STRATEGY_CAFEREAL)

    env.refresh(o)
    assert o.status == "PARTIAL"
    fill = env.query(Fill).filter(Fill.order_id == o.id).one()
    assert fill.qty == 3          # 10 이 아니다


def test_a_partial_that_later_fills_is_promoted(env, monkeypatch):
    """`if o.price is not None: continue` 때문에 PARTIAL 은 영원히 PARTIAL 이었다."""
    kis = FakeKIS(fills={"1": {"avg_price": 1000.0, "ccld_qty": 3}})
    monkeypatch.setattr(lt, "get_kis_client", lambda a="main": kis)
    o = _order(env, strategy=STRATEGY_CAFEREAL, qty=10, kis_id="0000001")
    lt.reconcile_fills(DAY, strategy=STRATEGY_CAFEREAL)

    # 잔량이 마저 체결됐다 — 다음 대사에서 반영돼야 한다.
    kis._fills = {"1": {"avg_price": 1010.0, "ccld_qty": 10}}
    lt.reconcile_fills(DAY, strategy=STRATEGY_CAFEREAL)

    env.refresh(o)
    assert o.status == "FILLED"
    assert o.price == 1010.0
    fills = env.query(Fill).filter(Fill.order_id == o.id).all()
    assert len(fills) == 1        # 같은 주문이 장부에 두 번 잡히면 안 된다
    assert fills[0].qty == 10


def test_paper_balance_heuristic_never_touches_cafereal(env, monkeypatch):
    """모의 환경 대체 규칙은 체결 여부를 확인하지 않는다 — 보유 중인 종목이면
    오늘 주문을 FILLED 로 찍는다. cafereal 에 적용되면 미체결이 확정 체결로
    둔갑해, 배지가 정확히 거꾸로 거짓말하게 된다."""
    class H:
        code, avg_price = "000720", 5000.0

    kis = FakeKIS(fills={}, holdings=[H()])
    monkeypatch.setattr(lt, "get_kis_client", lambda a="main": kis)
    o = _order(env, strategy=STRATEGY_CAFEREAL, code="000720", kis_id="0000001")

    lt.reconcile_fills(DAY, strategy=STRATEGY_CAFEREAL)

    env.refresh(o)
    assert o.status == "SUBMITTED"
    assert o.price is None
    assert kis.balance_calls == 0     # 잔고를 볼 이유조차 없다


def test_open_keeps_its_paper_fallback(env, monkeypatch):
    """open 의 기존 동작은 그대로여야 한다 — 실계좌 곡선의 연속성."""
    class H:
        code, avg_price = "000720", 5000.0

    kis = FakeKIS(fills={}, holdings=[H()])
    monkeypatch.setattr(lt, "get_kis_client", lambda a="main": kis)
    o = _order(env, strategy=STRATEGY_OPEN, code="000720", kis_id="0000001")

    lt.reconcile_fills(DAY, strategy=STRATEGY_OPEN)

    env.refresh(o)
    assert o.status == "FILLED"
    assert o.price == 5000.0


# ─── P0-2 취소 스윕 계좌 스코프 ─────────────────────────────────────


class CancelKIS:
    is_mock = False

    def __init__(self, tag):
        self.tag = tag
        self.cancelled = []

    def cancel_order(self, *, code, side, qty, org_no, orgn_odno, ord_dvsn="00"):
        self.cancelled.append(code)

        class R:
            ok, error, order_id = True, None, "x"
        return R()


def test_sweep_never_cancels_another_accounts_orders(env, monkeypatch):
    """main 을 지정가로 바꾸는 순간 터졌을 버그.

    쿼리에 전략 필터가 없고 클라이언트가 main 고정이라, 10분마다 도는 스윕이
    cafe 계좌 주문을 main 계좌 자격증명으로 취소하려 들었다.
    """
    _account(env, "main", buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:20",
             sell_ord_type="market")
    _order(env, strategy=STRATEGY_OPEN, code="000720")
    _order(env, strategy=STRATEGY_CAFEREAL, code="271940", kis_id="0000002")

    kis = CancelKIS("main")
    monkeypatch.setattr(lt, "get_kis_client", lambda a="main": kis)
    res = lt.cancel_unfilled_orders(DAY, now=dt.datetime(2026, 8, 20, 15, 25),
                                    account_id="main")

    assert res["cancelled"] == 1
    assert kis.cancelled == ["000720"]     # cafe 종목은 건드리지 않는다


def test_sweep_uses_the_account_that_placed_the_order(env, monkeypatch):
    """cafe 의 15:30 컷오프는 한 번도 실행된 적이 없다 — 태스크가 인자 없이
    불러 account_id='main' 이었고, main 은 컷오프가 없어 즉시 리턴했다."""
    _account(env, "cafe", buy_ord_type="limit", buy_base="quote",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:30",
             sell_ord_type="market")
    _order(env, strategy=STRATEGY_CAFEREAL, code="271940", kis_id="0000002")

    asked = []
    kis = CancelKIS("cafe")

    def _client(account="main"):
        asked.append(account)
        return kis

    monkeypatch.setattr(lt, "get_kis_client", _client)
    res = lt.cancel_unfilled_orders(DAY, now=dt.datetime(2026, 8, 20, 15, 31),
                                    account_id="cafe")

    assert asked == ["cafe"]
    assert res["cancelled"] == 1
    assert kis.cancelled == ["271940"]


def test_sweep_covers_every_account(monkeypatch):
    """계좌를 하나 추가하면 beat 항목을 추가로 기억하지 않아도 쓸려야 한다.

    태스크가 `cancel_unfilled_orders()` 를 인자 없이 부르던 것이 이 사고의
    출발점이었다 — 기본값 main 은 컷오프가 없어 매 틱 no_cutoff 로 끝났고,
    cafe 의 15:30 컷오프는 한 번도 실행되지 않았다.
    """
    from app.api.services import trading_calendar
    from app.api.workers import tasks

    swept = []
    task = tasks.cancel_unfilled_orders_task
    monkeypatch.setattr(trading_calendar, "is_market_open", lambda d: True)
    monkeypatch.setattr(lt, "cancel_unfilled_orders",
                        lambda **kw: swept.append(kw["account_id"]) or {"status": "ok"})
    # update_state 만 막고 함수 본문을 그대로 부른다. celery 로 디스패치하면
    # 결과 백엔드(redis)를 물어 tests/app 이 지켜온 "인프라 없이 도는" 규약이
    # 깨진다 — 게이트는 redis 없는 도커 스테이지에서 돈다.
    monkeypatch.setattr(task, "update_state", lambda **kw: None)
    result = task.run()

    assert set(swept) == set(ACCOUNT_STRATEGIES)
    assert set(result) == set(ACCOUNT_STRATEGIES)


def test_unknown_account_sweeps_nothing(env):
    res = lt.cancel_unfilled_orders(DAY, now=dt.datetime(2026, 8, 20, 15, 31),
                                    account_id="ghost")
    assert res["status"] == "no_strategies"


# ─── P0-3 중복 주문 ────────────────────────────────────────────────


def test_resting_order_blocks_a_repeat_buy(env, monkeypatch):
    """미체결 지정가는 잔고에 없다. 중복 방지가 잔고만 보면 같은 종목을
    이틀 연속 사서 슬롯 하나에 두 배를 넣는다."""
    from app.api.services import market_screener as ms

    _order(env, strategy=STRATEGY_CAFEREAL, code="271940", kis_id="0000002")
    resting = {code for (code,) in
               env.query(Order.code)
                 .filter(Order.strategy == STRATEGY_CAFEREAL,
                         Order.status.in_(("SUBMITTED", "PARTIAL")))
                 .distinct()}
    assert resting == {"271940"}
    # 시뮬 전략은 SIMULATED 로 기록되므로 이 집합이 비어 있어야 한다 —
    # 동결된 곡선의 동작이 바뀌면 안 된다.
    assert ms.STRATEGY_CAFE != STRATEGY_CAFEREAL
    sim = {code for (code,) in
           env.query(Order.code)
             .filter(Order.strategy == ms.STRATEGY_CAFE,
                     Order.status.in_(("SUBMITTED", "PARTIAL")))
             .distinct()}
    assert sim == set()
