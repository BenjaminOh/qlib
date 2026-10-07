"""늦게 나간 open 주문은 시가 지정가로 · 서지 스크린은 커밋 뒤 결과를 읽지 않는다."""

from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pandas")

from app.api.services import live_trader as lt  # noqa: E402
from app.api.services.account_policy import MARKET_BUY, MARKET_SELL, OrderPolicy  # noqa: E402


def _at(h, m):
    return datetime(2026, 10, 8, h, m)


# ─── open 늦은 진입 ────────────────────────────────────────────────


def test_on_time_open_keeps_the_account_policy():
    b, s = lt.late_open_policies(MARKET_BUY, MARKET_SELL, _at(9, 0))
    assert (b, s) == (MARKET_BUY, MARKET_SELL)
    b, s = lt.late_open_policies(MARKET_BUY, MARKET_SELL, _at(9, 4))
    assert not b.is_limit


def test_late_open_becomes_an_open_price_limit():
    # KIS 가 09:40 에야 살아났다 — 시장가는 시가와 무관한 가격에 체결된다.
    b, s = lt.late_open_policies(MARKET_BUY, MARKET_SELL, _at(9, 40))
    assert (b.ord_type, b.base, b.offset_pct, b.side) == ("limit", "open", 0.0, "BUY")
    assert (s.ord_type, s.base, s.offset_pct, s.side) == ("limit", "open", 0.0, "SELL")


def test_late_open_leaves_an_existing_limit_policy_alone():
    mine = OrderPolicy(side="BUY", ord_type="limit", base="prev_close", offset_pct=0.03)
    b, s = lt.late_open_policies(mine, MARKET_SELL, _at(9, 30))
    assert b is mine
    assert s.ord_type == "limit"


# ─── 서지 스크린 DetachedInstanceError ─────────────────────────────


def test_surge_screen_returns_picks_after_commit(monkeypatch):
    """세션을 **진짜로 닫는** DB 로 돌린다. 닫지 않는 대역으로는 이 버그가 재현되지 않는다."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.api.db import Base, MarketPoolSnapshot, SurgePick
    from app.api.services import market_screener as ms

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)         # expire_on_commit=True — 운영과 같다
    monkeypatch.setattr(ms, "SessionLocal", Session)
    monkeypatch.setattr(ms, "init_db", lambda: None)

    day = date(2026, 10, 6)
    cols = {c.name for c in MarketPoolSnapshot.__table__.columns}
    with Session() as db:
        for i, code in enumerate(("200350", "047920")):
            row = dict(trade_date=day, code=code, name=f"N{i}", close=1000.0 + i,
                       ret20=0.3, pos_vs_5d_high=-6.5, vol_x=2.0, ma20_gap=0.05)
            db.add(MarketPoolSnapshot(**{k: v for k, v in row.items() if k in cols}))
        db.commit()

    monkeypatch.setattr(ms, "surge_score", lambda s: 50.0 + s.close)
    res = ms.run_surge_screen(day)                 # 수정 전: DetachedInstanceError
    assert res["status"] == "ok"
    assert [p["code"] for p in res["picks"]] == ["047920", "200350"]

    res2 = ms.run_surge_screen(day)                # 이미 저장된 날 다시 돌아도 같다
    assert [p["code"] for p in res2["picks"]] == ["047920", "200350"]
    with Session() as db:
        assert db.query(SurgePick).filter(SurgePick.trade_date == day).count() == 2
