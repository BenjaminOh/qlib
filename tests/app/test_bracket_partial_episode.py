"""사다리 부분 매도 뒤 남은 수량도 다음 날 다시 판정된다 (2026-10-27).

예전 `evaluate_bracket_exits` 는 진입을 "마지막 매도 다음의 첫 매수"로 찾았다. 사다리는 절반만
팔기 때문에 그 뒤에는 매수가 없어 진입을 못 찾고 그 종목을 건너뛰었다 — scale 시뮬에서
남은 수량 23종목이 8월부터 한 번도 판정되지 않았다. 이제 수량으로 에피소드를 가른다.
"""

from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Fill, Order  # noqa: E402
from app.api.db.models import STRATEGY_SCALE  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402

BUY_DAY, RUNG_DAY, TODAY = date(2026, 9, 30), date(2026, 10, 2), date(2026, 10, 27)


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "SessionLocal", lambda: s)
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "_stock_name", lambda code: code)
    monkeypatch.setattr(lt, "_last_close", lambda code: 10_000.0)
    monkeypatch.setattr(lt, "_prev_low", lambda code, d, w: None)
    monkeypatch.setattr(lt, "_peak_close", lambda code, a, b: None)
    yield s
    s.close()
    engine.dispose()


def _sim(s, day, side, qty, px):
    o = Order(trade_date=day, strategy=STRATEGY_SCALE, code="011790", name="n", side=side,
              qty=qty, price=px, ord_dvsn="01", status="SIMULATED")
    s.add(o)
    s.flush()
    s.add(Fill(order_id=o.id, strategy=STRATEGY_SCALE, qty=qty, price=px))
    s.commit()


def test_remainder_after_a_ladder_rung_is_judged_again(session, monkeypatch):
    _sim(session, BUY_DAY, "BUY", 2, 10_000.0)
    _sim(session, RUNG_DAY, "SELL", 1, 11_000.0)          # +10% 1단 절반 매도
    # 오늘 봉이 1단 아래 바닥선(+5%, 10,500)을 깬다 → 남은 1주를 판다.
    monkeypatch.setattr(lt, "_day_ohlc_any", lambda code, day: {
        "open": 10_400.0, "high": 10_450.0, "low": 9_800.0, "close": 9_900.0})

    res = lt.evaluate_bracket_exits(trade_date=TODAY, strategy=STRATEGY_SCALE)

    assert [(e["code"], e["qty"], e["kind"]) for e in res["exits"]] == [("011790", 1, "플로어 청산")]
    assert res["exits"][0]["price"] == 10_400.0            # 시가가 이미 바닥선 아래 → 시가 체결


def test_fully_closed_episode_is_not_judged(session, monkeypatch):
    _sim(session, BUY_DAY, "BUY", 2, 10_000.0)
    _sim(session, RUNG_DAY, "SELL", 2, 11_000.0)
    monkeypatch.setattr(lt, "_day_ohlc_any", lambda code, day: {
        "open": 9_000.0, "high": 9_000.0, "low": 8_000.0, "close": 8_000.0})
    assert lt.evaluate_bracket_exits(trade_date=TODAY, strategy=STRATEGY_SCALE)["exits"] == []
