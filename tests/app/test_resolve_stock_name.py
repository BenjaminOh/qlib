"""고정 이름표에 없는 소형주도 이름을 찾는다.

2026-11-12: coolreal 청산 알림이 `051630 (051630)` 으로 나갔고 매도 주문 행의
name 에도 코드가 저장됐다. 고정 파일(kr_stock_names.json)은 코스피200·코스닥150·
ETF 뿐인데, 카페·냉각이 사는 종목은 대부분 그 밖의 코스닥 소형주다.
"""

from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Order  # noqa: E402
from app.api.db.models import CafeCandidate  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402
from app.api.services.kis_client import OrderResult  # noqa: E402

D = date(2026, 11, 12)


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    S = sessionmaker(bind=engine)
    monkeypatch.setattr(lt, "SessionLocal", S)
    # 고정 이름표에는 대형주 하나만 있다고 둔다.
    monkeypatch.setattr(lt, "_stock_name",
                        lambda code: {"005930": "삼성전자"}.get(code, code))
    yield S
    engine.dispose()


def _buy(s, code, name):
    s.add(Order(trade_date=D, strategy="coolreal", account_id="cool", code=code,
                name=name, side="BUY", qty=1, price=1000.0, ord_dvsn="00",
                status="FILLED"))
    s.commit()


def test_hint_from_kis_wins(Session):
    assert lt._resolve_name("051630", "진양화학") == "진양화학"


def test_hint_equal_to_code_is_ignored(Session):
    assert lt._resolve_name("005930", "005930") == "삼성전자"


def test_small_cap_name_comes_from_the_buy_row(Session):
    with Session() as s:
        _buy(s, "051630", "진양화학")
        assert lt._resolve_name("051630", db=s) == "진양화학"
    assert lt._resolve_name("051630") == "진양화학", "세션 없이도 찾는다"


def test_rows_that_stored_the_code_as_name_are_skipped(Session):
    with Session() as s:
        _buy(s, "051630", "진양화학")
        _buy(s, "051630", "051630")              # 예전 버그로 저장된 매도 행 모양
        assert lt._resolve_name("051630", db=s) == "진양화학"


def test_falls_back_to_cafe_candidates(Session):
    with Session() as s:
        s.add(CafeCandidate(trade_date=D, code="060310", name="3S", pattern="B",
                            rank=1, stop_px=1000.0))
        s.commit()
        assert lt._resolve_name("060310", db=s) == "3S"


def test_unknown_code_returns_the_code(Session):
    assert lt._resolve_name("999999") == "999999"


def test_persisted_sell_row_gets_the_name(Session):
    with Session() as s:
        _buy(s, "411080", "샌즈랩")
        o = lt._persist_order(s, D, "411080", "SELL", 25, None,
                              OrderResult(ok=True, order_id="1", code="411080",
                                          side="SELL", qty=25, price=None, raw={}),
                              strategy="coolreal", account_id="cool")
        assert o.name == "샌즈랩"
