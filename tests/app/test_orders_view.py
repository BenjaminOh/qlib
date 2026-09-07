"""Order-kind derivation and 지정가 discount surfaced by GET /live/orders.

Background (2026-08-12): `_persist_order` set `ord_dvsn = "01" if price is None
else "00"`, so every *simulated* fill — no KIS round-trip at all — was recorded
as a 지정가 order. Worse, `price` cannot stand in for the order type either:
`reconcile_fills` pins the reconciled average fill price onto real market orders, so
a filled 시장가 order also carries a price. Both are covered below.
"""

from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Order  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
    engine.dispose()


def _order(**kw) -> Order:
    base = dict(
        trade_date=date(2026, 8, 11),
        strategy="open",
        code="005930",
        name="삼성전자",
        side="BUY",
        qty=10,
        price=None,
        ord_dvsn="01",
        status="SUBMITTED",
        submitted_at=datetime(2026, 8, 11, 0, 0, 0),
    )
    base.update(kw)
    return Order(**base)


# ─── Order.kind ─────────────────────────────────────────────────────


def test_simulated_fill_is_sim_not_limit():
    """The headline bug: a sim fill carries ord_dvsn='00' but sent no order."""
    o = _order(strategy="surge", price=3935.0, ord_dvsn="00", status="SIMULATED")
    assert o.kind == "sim"


def test_simulated_fill_with_new_marker_is_sim():
    o = _order(strategy="surge", price=3935.0, ord_dvsn=Order.ORD_DVSN_SIM, status="SIMULATED")
    assert o.kind == "sim"


def test_real_market_order_before_reconciliation():
    o = _order(price=None, ord_dvsn="01", status="SUBMITTED")
    assert o.kind == "market"


def test_reconciled_market_order_is_still_market():
    """Regression guard: reconcile_fills pins an average fill price onto a 시장가
    order (live_trader.py:608). Keying off `price` would call this 지정가."""
    o = _order(price=71_200.0, ord_dvsn="01", status="FILLED")
    assert o.kind == "market"


def test_real_limit_order_is_limit():
    o = _order(price=95_800.0, ord_dvsn="00", status="SUBMITTED")
    assert o.kind == "limit"


def test_rejected_order_keeps_its_kind():
    o = _order(price=None, ord_dvsn="01", status="REJECTED", error="1 기간이 만료된 token 입니다.")
    assert o.kind == "market"


# ─── persistence: simulated fills stop claiming 지정가 ───────────────


def test_persist_simulated_fill_marks_ord_dvsn(session, monkeypatch):
    # live_trader pulls in the whole qlib stack (pandas → ruamel → compiled
    # extensions); skip where that isn't installed rather than drag it in.
    pytest.importorskip("qlib")
    from app.api.services import live_trader

    monkeypatch.setattr(live_trader, "_stock_name", lambda code: f"name-{code}")
    live_trader._persist_simulated_fill(
        session,
        date(2026, 8, 11),
        "036420",
        "BUY",
        633,
        3935.0,
        strategy="surge",
        reasons={"action": "buy", "basis": "급등 전야 1위"},
    )
    session.flush()

    o = session.query(Order).one()
    assert o.status == "SIMULATED"
    assert o.ord_dvsn == Order.ORD_DVSN_SIM
    assert o.kind == "sim"


# ─── discount surfaced by the router ────────────────────────────────


def _get_orders(monkeypatch, session, prev_closes: dict, **params):
    """Call the real endpoint function against an in-memory DB."""
    from app.api.routers import live as live_router

    class _Ctx:
        def __enter__(self_inner):
            return session

        def __exit__(self_inner, *a):
            # 운영과 같은 수명: with 종료 시 detach (2026-08-28 /orders 500 회귀 방지)
            session.expunge_all()
            return False

    monkeypatch.setattr(live_router, "SessionLocal", lambda: _Ctx())
    monkeypatch.setattr(live_router, "_position_timeline", lambda *a, **kw: [])
    monkeypatch.setattr(live_router, "_close_safe", lambda code: None)
    monkeypatch.setattr(live_router, "_prev_close_cached", lambda code, day: prev_closes.get(code))
    # Called directly, so FastAPI's Query(...) defaults are never resolved —
    # every parameter has to be passed explicitly.
    call = {"limit": 100, "include_sim": False, "strategy": None, "view": "real"}
    call.update(params)
    return live_router.get_orders(**call)


def test_limit_fill_at_the_resting_price_reports_minus_three(monkeypatch, session):
    session.add(
        _order(strategy="limit", code="078930", name="GS", qty=10, price=9_700.0, ord_dvsn="00", status="SIMULATED")
    )
    session.commit()

    res = _get_orders(monkeypatch, session, {"078930": 10_000.0}, view="sim")
    row = res.orders[0]

    assert row.order_kind == "sim"
    assert row.prev_close == 10_000.0
    assert row.discount_pct == -3.0


def test_gap_down_open_fills_deeper_than_the_limit(monkeypatch, session):
    """The whole point of the feature: a deeper number is the 저점 매수 evidence."""
    session.add(
        _order(strategy="limit", code="078930", name="GS", qty=10, price=9_400.0, ord_dvsn="00", status="SIMULATED")
    )
    session.commit()

    row = _get_orders(monkeypatch, session, {"078930": 10_000.0}, view="sim").orders[0]

    assert row.discount_pct == -6.0


def test_non_limit_rows_get_no_discount(monkeypatch, session):
    session.add(_order(strategy="surge", code="036420", price=3_935.0, ord_dvsn=Order.ORD_DVSN_SIM, status="SIMULATED"))
    session.commit()

    row = _get_orders(monkeypatch, session, {"036420": 4_000.0}, view="sim").orders[0]

    assert row.discount_pct is None
    assert row.prev_close is None


def test_missing_prev_close_degrades_quietly(monkeypatch, session):
    session.add(_order(strategy="limit", code="078930", price=9_700.0, ord_dvsn="00", status="SIMULATED"))
    session.commit()

    row = _get_orders(monkeypatch, session, {}, view="sim").orders[0]

    assert row.discount_pct is None


def test_basis_is_extracted_and_survives_bad_json(monkeypatch, session):
    session.add(
        _order(
            strategy="limit",
            code="078930",
            price=9_700.0,
            ord_dvsn="00",
            status="SIMULATED",
            reasons_json='{"action":"buy","basis":"지정가 체결"}',
        )
    )
    session.add(
        _order(
            strategy="limit", code="000660", price=9_700.0, ord_dvsn="00", status="SIMULATED", reasons_json="{not json"
        )
    )
    session.commit()

    rows = {r.code: r for r in _get_orders(monkeypatch, session, {}, view="sim").orders}

    assert rows["078930"].basis == "지정가 체결"
    assert rows["000660"].basis is None


def test_response_carries_the_configured_discount(monkeypatch, session):
    session.add(_order(status="SIMULATED", ord_dvsn=Order.ORD_DVSN_SIM))
    session.commit()

    res = _get_orders(monkeypatch, session, {}, view="sim")

    assert res.limit_discount == pytest.approx(0.03)
