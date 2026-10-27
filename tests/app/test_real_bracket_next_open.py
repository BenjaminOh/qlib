"""실계좌 브래킷 청산은 다음 거래일 09:00 에 장중 주문으로 나간다 (2026-10-27).

2026-10-13~23 coolreal 의 손절·익절 6건이 15:49/16:25(장 마감 뒤)에 시장가로
나가 KIS 가 전부 거부했다("모의투자 장종료"). 그리고 거부된 매도가 "마지막 매도"로
잡혀 에피소드가 끝난 것으로 읽혀, 6종목은 손절선 아래에서 다시 판정되지 않았다.
장부에는 cool 계좌 주문이 main 으로 기록됐다.

여기서 고정하는 것:
- 실계좌 전략은 order_date 없이(=마감 뒤 체인) 부르면 주문을 내지 않는다.
- 아침 실행은 직전 거래일 봉으로 판정하고, 주문일·계좌를 바르게 기록한다.
- 거부된 매도는 에피소드를 끝내지 않는다.
- 같은 날 재실행은 두 번 팔지 않는다.
- 마감 뒤 체인은 실계좌를 건너뛰고, 09:00 beat 가 09:01 매수보다 앞선다.
"""

import json
from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("celery")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Order  # noqa: E402
from app.api.db.models import COOL_ACCOUNT_ID, STRATEGY_COOLREAL  # noqa: E402
from app.api.services import kis_client, live_trader as lt  # noqa: E402
from app.api.services.kis_client import AccountSnapshot, Holding, OrderResult  # noqa: E402

TODAY = date(2026, 10, 28)
PREV = date(2026, 10, 27)
ENTRY = date(2026, 10, 13)
CODE = "142280"
# 평단 10,000 · 스크리너 손절선 9,000. 직전 거래일 저가 8,800 → 손절.
BAR = {"open": 9_100.0, "high": 9_200.0, "low": 8_800.0, "close": 8_900.0}


class FakeClient:
    def __init__(self, ok=True):
        self.ok = ok
        self.orders: list[tuple] = []

    def get_balance(self):
        return AccountSnapshot(cash=0.0, total_eval=89_000.0, holdings=[
            Holding(code=CODE, qty=10, avg_price=10_000.0, eval_price=8_900.0,
                    eval_value=89_000.0, pnl=-11_000.0, pnl_pct=-0.11)])

    def place_order(self, code, side, qty, price=None):
        self.orders.append((code, side, qty, price))
        return OrderResult(ok=self.ok, order_id="K1" if self.ok else None, code=code,
                           side=side, qty=qty, price=price, raw={},
                           error=None if self.ok else "모의투자 장종료")


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    client = FakeClient()
    accounts = []

    def fake_get_kis_client(account=None):
        accounts.append(account)
        if account != COOL_ACCOUNT_ID:  # 운영처럼 cool 계좌만 설정돼 있다
            raise kis_client.AccountNotConfigured(account)
        return client

    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "SessionLocal", lambda: session)
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "_stock_name", lambda code: f"name-{code}")
    monkeypatch.setattr(lt, "_prev_trading_day", lambda d: PREV)
    monkeypatch.setattr(lt, "_last_trading_day", lambda: PREV)
    monkeypatch.setattr(lt, "_day_ohlc_any", lambda code, day: BAR if day == PREV else None)
    monkeypatch.setattr(lt, "KIS_THROTTLE_SECONDS", 0)
    monkeypatch.setattr(kis_client, "get_kis_client", fake_get_kis_client)
    session.add(Order(trade_date=ENTRY, strategy=STRATEGY_COOLREAL, account_id=COOL_ACCOUNT_ID,
                      code=CODE, name="n", side="BUY", qty=10, price=10_000.0, ord_dvsn="00",
                      status="FILLED", reasons_json=json.dumps({"stop_px": 9_000.0})))
    session.commit()
    yield session, client, accounts
    session.close()
    engine.dispose()


def _sells(session):
    return [o for o in session.query(Order).all() if o.side == "SELL"]


def test_real_strategy_without_order_date_places_nothing(env):
    session, client, accounts = env

    res = lt.evaluate_bracket_exits(strategy=STRATEGY_COOLREAL)

    assert res["status"] == "deferred_to_next_open"
    assert client.orders == [] and accounts == []  # KIS 에 붙지도 않는다
    assert _sells(session) == []


def test_morning_run_sells_on_prev_bar_with_right_date_and_account(env):
    session, client, accounts = env

    res = lt.evaluate_real_bracket_exits_morning(today=TODAY)[STRATEGY_COOLREAL]

    assert COOL_ACCOUNT_ID in accounts
    assert client.orders == [(CODE, "SELL", 10, None)]  # 시장가
    [sell] = _sells(session)
    assert sell.trade_date == TODAY            # 주문일 = 오늘(대사가 이 날짜로 조회)
    assert sell.account_id == COOL_ACCOUNT_ID  # 예전에는 main 으로 기록됐다
    assert sell.status == "SUBMITTED"
    assert res["trade_date"] == PREV.isoformat()
    assert [e["status"] for e in res["exits"]] == ["SUBMITTED"]


def test_rejected_sell_does_not_end_the_episode(env):
    session, client, _ = env
    session.add(Order(trade_date=date(2026, 10, 14), strategy=STRATEGY_COOLREAL,
                      account_id="main", code=CODE, name="n", side="SELL", qty=10,
                      price=None, ord_dvsn="01", status="REJECTED", error="모의투자 장종료"))
    session.commit()

    lt.evaluate_real_bracket_exits_morning(today=TODAY)

    assert client.orders == [(CODE, "SELL", 10, None)]


def test_same_day_rerun_does_not_sell_twice(env):
    session, client, _ = env

    lt.evaluate_real_bracket_exits_morning(today=TODAY)
    lt.evaluate_real_bracket_exits_morning(today=TODAY)  # kis_retry 재시도·수동 재실행

    assert len(client.orders) == 1
    assert len(_sells(session)) == 1


def test_rejected_morning_sell_is_retried_next_run(env):
    session, client, _ = env
    client.ok = False

    res = lt.evaluate_real_bracket_exits_morning(today=TODAY)[STRATEGY_COOLREAL]
    assert [e["status"] for e in res["exits"]] == ["REJECTED"]

    client.ok = True
    lt.evaluate_real_bracket_exits_morning(today=TODAY)
    assert len(client.orders) == 2


def test_after_close_chain_skips_real_strategies(monkeypatch):
    from app.api.services import notify, trading_calendar
    from app.api.workers import tasks

    called = []
    monkeypatch.setattr(trading_calendar, "is_market_open", lambda d=None: True)
    monkeypatch.setattr(lt, "evaluate_bracket_exits",
                        lambda strategy: called.append(strategy) or {"status": "ok", "exits": []})
    monkeypatch.setattr(lt, "evaluate_limit_entries", lambda: {"status": "ok"})
    monkeypatch.setattr(lt, "sync_account", lambda strategy: None)
    monkeypatch.setattr(notify, "notify_bracket_exits", lambda *a, **kw: None)
    monkeypatch.setattr(tasks.close_bracket_exits_task, "update_state", lambda **kw: None)

    tasks.close_bracket_exits_task.run()

    assert called and not set(called) & set(lt.REAL_BRACKET_STRATEGIES)


def test_morning_beat_runs_before_the_0901_buys():
    from app.api.workers.celery_app import celery_app

    beat = celery_app.conf.beat_schedule
    slot = beat["real-bracket-exits-morning"]
    assert slot["task"] == "real_bracket_exits_morning"
    sched = slot["schedule"]
    assert sched.hour == {9} and sched.minute == {0}
    assert beat["live-orders-coolreal-open"]["schedule"].minute == {1}
