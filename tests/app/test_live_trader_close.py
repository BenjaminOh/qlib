"""Close-strategy (simulated) accounting in `live_trader`.

These tests bypass qlib data and KIS — they only validate the DB-only paper
portfolio reconstruction and the simulated fill bookkeeping.
"""

from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Fill, Order, Signal  # noqa: E402
from app.api.db.models import STRATEGY_CLOSE  # noqa: E402
from app.api.services import live_trader  # noqa: E402


@pytest.fixture
def session(monkeypatch):
    """Fresh in-memory sqlite for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _stub_last_close(monkeypatch):
    """Pin `_last_close` so eval prices are deterministic."""
    monkeypatch.setattr(live_trader, "_last_close", lambda code: 1000.0)
    monkeypatch.setattr(live_trader, "_stock_name", lambda code: f"name-{code}")


def _add_simulated_fill(session, code, side, qty, price, strategy=STRATEGY_CLOSE,
                        trade_date=date(2026, 5, 20), pnl=None):
    o = Order(trade_date=trade_date, strategy=strategy, code=code,
              name=f"name-{code}", side=side, qty=qty, price=price,
              ord_dvsn="01", status="SIMULATED")
    session.add(o)
    session.flush()
    session.add(Fill(order_id=o.id, strategy=strategy, qty=qty, price=price,
                     fee=0.0, pnl=pnl, filled_at=datetime(2026, 5, 20, 15, 20)))
    session.commit()
    return o


def test_simulated_balance_empty_returns_seed_cash(session):
    snap = live_trader._simulated_balance(session, strategy=STRATEGY_CLOSE,
                                          seed_cash=10_000_000.0)
    assert snap.cash == 10_000_000.0
    assert snap.total_eval == 10_000_000.0
    assert snap.holdings == []


def test_simulated_balance_after_buy_deducts_cash_and_lists_holding(session):
    _add_simulated_fill(session, "005930", "BUY", 100, 70_000.0)
    snap = live_trader._simulated_balance(session, strategy=STRATEGY_CLOSE,
                                          seed_cash=10_000_000.0)
    assert snap.cash == 10_000_000.0 - 100 * 70_000.0  # 3M left
    assert len(snap.holdings) == 1
    h = snap.holdings[0]
    assert h.code == "005930"
    assert h.qty == 100
    assert h.avg_price == 70_000.0
    assert h.eval_price == 1000.0  # stubbed
    assert h.eval_value == 100 * 1000.0


def test_simulated_balance_buy_then_sell_clears_position(session):
    _add_simulated_fill(session, "005930", "BUY", 100, 70_000.0)
    _add_simulated_fill(session, "005930", "SELL", 100, 80_000.0)
    snap = live_trader._simulated_balance(session, strategy=STRATEGY_CLOSE,
                                          seed_cash=10_000_000.0)
    # Cash = 10M - 100*70k + 100*80k = 11M
    assert snap.cash == 10_000_000.0 - 7_000_000.0 + 8_000_000.0
    assert snap.holdings == []


def test_simulated_balance_partial_sell_keeps_remainder(session):
    _add_simulated_fill(session, "005930", "BUY", 100, 70_000.0)
    _add_simulated_fill(session, "005930", "SELL", 40, 80_000.0)
    snap = live_trader._simulated_balance(session, strategy=STRATEGY_CLOSE,
                                          seed_cash=10_000_000.0)
    assert len(snap.holdings) == 1
    h = snap.holdings[0]
    assert h.qty == 60
    # Avg cost preserved per share (= 70k); proportional cost reduction on sell
    assert abs(h.avg_price - 70_000.0) < 1e-6


def test_simulated_balance_isolates_strategies(session):
    _add_simulated_fill(session, "005930", "BUY", 100, 70_000.0, strategy="open")
    snap_close = live_trader._simulated_balance(session, strategy=STRATEGY_CLOSE,
                                                seed_cash=10_000_000.0)
    # Open strategy fill must not contaminate close snapshot
    assert snap_close.holdings == []
    assert snap_close.cash == 10_000_000.0


def test_submit_daily_orders_simulated_no_signal_short_circuits(session, monkeypatch):
    """When the signals table is empty, no fills are written."""
    monkeypatch.setattr(live_trader, "init_db", lambda: None)
    monkeypatch.setattr(live_trader, "SessionLocal", lambda: session)
    result = live_trader.submit_daily_orders(today=date(2026, 5, 20),
                                              strategy=STRATEGY_CLOSE,
                                              simulated=True)
    assert result["status"] == "no_signal"
    assert result["strategy"] == STRATEGY_CLOSE
    assert result["simulated"] is True
    assert session.query(Fill).count() == 0


def test_submit_daily_orders_simulated_buys_top_picks(session, monkeypatch):
    """A pristine close portfolio buys the top picks from today's Signal."""
    today = date(2026, 5, 20)
    session.add_all([
        Signal(as_of=today, rank=1, code="005930", name="n", score=0.5,
               model_class="LGBModel", strategy_class="TopkDropoutStrategy"),
        Signal(as_of=today, rank=2, code="000660", name="n", score=0.4,
               model_class="LGBModel", strategy_class="TopkDropoutStrategy"),
    ])
    session.commit()
    monkeypatch.setattr(live_trader, "init_db", lambda: None)
    monkeypatch.setattr(live_trader, "SessionLocal", lambda: session)
    result = live_trader.submit_daily_orders(today=today,
                                              strategy=STRATEGY_CLOSE,
                                              simulated=True)
    assert result["status"] == "ok"
    # n_drop=2 by default, so up to 2 buys on a fresh portfolio
    assert result["buys"] == 2
    assert result["submitted"] == 2
    assert session.query(Fill).count() == 2
    # All fills tagged close
    for f in session.query(Fill).all():
        assert f.strategy == STRATEGY_CLOSE
