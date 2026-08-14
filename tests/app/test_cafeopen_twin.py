"""cafeopen twin — resting-limit entry, judged against the 09:00~10:00 range.

The twin exists to price cafe's one undefended assumption (that 250만원 fills
at a 상한가 close), so what these tests guard is the entry mechanics only:
the reservation written at 09:00 stays invisible until 10:00 resolves it, and
the three outcomes (gap through / touch / never touched) each land correctly.

DB-only — qlib and KIS are both stubbed.
"""

from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Order  # noqa: E402
from app.api.db.models import STRATEGY_CAFE, STRATEGY_CAFEOPEN  # noqa: E402
from app.api.services import live_trader  # noqa: E402

SOURCE_DAY = date(2026, 8, 13)   # the day cafe bought at the 상한가 close
TWIN_DAY = date(2026, 8, 14)     # the morning the twin rests its limit


@pytest.fixture
def session_factory(monkeypatch):
    """One in-memory DB shared by the module-level SessionLocal the code uses."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(live_trader, "SessionLocal", Session)
    monkeypatch.setattr(live_trader, "init_db", lambda: None)
    monkeypatch.setattr(live_trader, "_stock_name", lambda code: f"name-{code}")
    monkeypatch.setattr(live_trader, "_last_close", lambda code: 20000.0)
    monkeypatch.setattr(live_trader, "_prev_trading_day", lambda day: SOURCE_DAY)
    yield Session
    engine.dispose()


class _StubClient:
    """Minimal get_quote stub — only the fields the twin reads."""

    def __init__(self, quotes):
        self.quotes = quotes

    def get_quote(self, code):
        return self.quotes.get(code, {})


def _stub_kis(monkeypatch, quotes):
    monkeypatch.setattr(live_trader, "get_kis_client", lambda: _StubClient(quotes))


def _cafe_buy(session, code="126730", price=20000.0, qty=132, stop_px=17000.0):
    """The 15:28 cafe fill the twin mirrors."""
    import json
    o = Order(trade_date=SOURCE_DAY, strategy=STRATEGY_CAFE, code=code,
              name=f"name-{code}", side="BUY", qty=qty, price=price,
              ord_dvsn="SM", status="SIMULATED",
              reasons_json=json.dumps({"stop_px": stop_px,
                                       "metrics": {"ret20": 43.6}}))
    session.add(o)
    session.commit()
    return o


# ─── 09:00 reservation ──────────────────────────────────────────────


def test_rests_limit_three_pct_below_the_open(session_factory, monkeypatch):
    s = session_factory()
    _cafe_buy(s)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0, "price": 20800.0}})

    result = live_trader.submit_cafeopen_orders(TWIN_DAY)

    assert result["status"] == "ok"
    assert len(result["rested"]) == 1
    # 20800 × 0.97 = 20176 → floored onto the KRX tick grid, which is 50원 for
    # the 20,000~50,000 tier → 20150. An unrounded limit could never be placed.
    assert result["rested"][0]["limit_px"] == 20150
    row = s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).one()
    assert row.status == "PENDING"
    assert row.ord_dvsn == "00"


def test_pending_reservation_is_invisible_to_the_balance(session_factory, monkeypatch):
    """No Fill row until 10:00 — a resting order must not spend cash or show
    up as a holding, or the twin's equity curve leads its own entries."""
    s = session_factory()
    _cafe_buy(s)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0}})
    live_trader.submit_cafeopen_orders(TWIN_DAY)

    snap = live_trader._simulated_balance(s, strategy=STRATEGY_CAFEOPEN,
                                          seed_cash=10_000_000.0)
    assert snap.cash == 10_000_000.0
    assert snap.holdings == []


def test_skips_a_code_already_resting(session_factory, monkeypatch):
    """Idempotency: the 09:00 beat firing twice must not double the position."""
    s = session_factory()
    _cafe_buy(s)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0}})
    live_trader.submit_cafeopen_orders(TWIN_DAY)
    live_trader.submit_cafeopen_orders(TWIN_DAY)

    assert s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).count() == 1


def test_carries_the_cafe_structural_stop_across(session_factory, monkeypatch):
    """The exit engine reads stop_px off the ENTRY order — if the twin drops
    it the stops diverge and the A/B stops isolating the entry."""
    import json
    s = session_factory()
    _cafe_buy(s, stop_px=17000.0)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0}})
    live_trader.submit_cafeopen_orders(TWIN_DAY)

    row = s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).one()
    assert json.loads(row.reasons_json)["stop_px"] == 17000.0


def test_no_source_buys_is_a_no_op(session_factory, monkeypatch):
    _stub_kis(monkeypatch, {})
    result = live_trader.submit_cafeopen_orders(TWIN_DAY)
    assert result["status"] == "no_source_buys"


# ─── 10:00 resolution — the three outcomes ──────────────────────────


def _rest_then_resolve(session, monkeypatch, open_px, low_px):
    _cafe_buy(session)
    _stub_kis(monkeypatch, {"126730": {"open": open_px}})
    live_trader.submit_cafeopen_orders(TWIN_DAY)
    _stub_kis(monkeypatch, {"126730": {"open": open_px, "low": low_px}})
    return live_trader.resolve_cafeopen_orders(TWIN_DAY)


def test_gap_through_the_limit_fills_at_the_open(session_factory, monkeypatch):
    """Opening below the reservation fills at the OPEN — the better price the
    market actually printed, not the limit we happened to name."""
    s = session_factory()
    # limit off a 20800 open is 20170; a 19000 open gaps straight through it
    _cafe_buy(s)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0}})
    live_trader.submit_cafeopen_orders(TWIN_DAY)
    _stub_kis(monkeypatch, {"126730": {"open": 19000.0, "low": 18500.0}})

    result = live_trader.resolve_cafeopen_orders(TWIN_DAY)

    assert len(result["filled"]) == 1
    assert result["filled"][0]["kind"] == "gap"
    assert result["filled"][0]["fill_px"] == 19000.0


def test_range_touching_the_limit_fills_at_the_limit(session_factory, monkeypatch):
    s = session_factory()
    result = _rest_then_resolve(s, monkeypatch, open_px=20800.0, low_px=20000.0)

    assert len(result["filled"]) == 1
    assert result["filled"][0]["kind"] == "touch"
    assert result["filled"][0]["fill_px"] == 20150
    row = s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).one()
    assert row.status == "SIMULATED"
    assert len(row.fills) == 1


def test_untouched_limit_is_cancelled_but_the_row_survives(session_factory, monkeypatch):
    """A cancel is a RESULT — 'cafe's pick was never purchasable at a sane
    price' is exactly what this experiment measures, so the row must persist."""
    s = session_factory()
    result = _rest_then_resolve(s, monkeypatch, open_px=20800.0, low_px=20500.0)

    assert result["filled"] == []
    assert len(result["cancelled"]) == 1
    row = s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).one()
    assert row.status == "CANCELLED"
    assert row.fills == []


def test_filled_position_shows_up_in_the_balance(session_factory, monkeypatch):
    s = session_factory()
    _rest_then_resolve(s, monkeypatch, open_px=20800.0, low_px=20000.0)

    snap = live_trader._simulated_balance(s, strategy=STRATEGY_CAFEOPEN,
                                          seed_cash=10_000_000.0)
    assert len(snap.holdings) == 1
    assert snap.holdings[0].code == "126730"
    assert snap.cash < 10_000_000.0


def test_missing_quote_leaves_the_order_pending(session_factory, monkeypatch):
    """No data is not a cancel. Inventing one would fabricate a market event
    that never happened, so the row waits for a retry instead."""
    s = session_factory()
    _cafe_buy(s)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0}})
    live_trader.submit_cafeopen_orders(TWIN_DAY)
    _stub_kis(monkeypatch, {"126730": {}})

    result = live_trader.resolve_cafeopen_orders(TWIN_DAY)

    assert result["filled"] == [] and result["cancelled"] == []
    assert s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).one().status == "PENDING"


def test_resolution_is_idempotent(session_factory, monkeypatch):
    s = session_factory()
    _rest_then_resolve(s, monkeypatch, open_px=20800.0, low_px=20000.0)
    second = live_trader.resolve_cafeopen_orders(TWIN_DAY)

    assert second["status"] == "nothing_resting"
    row = s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).one()
    assert len(row.fills) == 1


# ─── the cancelled rows must not poison the exit lookup ─────────────


def test_stale_cancelled_row_is_not_mistaken_for_the_entry(session_factory, monkeypatch):
    """Keeping unfilled reservations is the whole point of this strategy, but
    they are BUY rows too. The bracket picks the EARLIEST buy for an episode,
    so an old cancel would otherwise win and date the position before it
    existed — skipping the entry-day exclusion and judging exits a day early.
    """
    s = session_factory()
    # Day 1: cafe bought it, the twin's limit never traded → CANCELLED.
    _cafe_buy(s)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0}})
    live_trader.submit_cafeopen_orders(TWIN_DAY)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0, "low": 20500.0}})
    live_trader.resolve_cafeopen_orders(TWIN_DAY)
    assert s.query(Order).filter(Order.strategy == STRATEGY_CAFEOPEN).one().status == "CANCELLED"

    # Day 2: cafe picks the same code again and this time the limit fills.
    later = date(2026, 8, 17)
    monkeypatch.setattr(live_trader, "_prev_trading_day", lambda day: SOURCE_DAY)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0}})
    live_trader.submit_cafeopen_orders(later)
    _stub_kis(monkeypatch, {"126730": {"open": 20800.0, "low": 20000.0}})
    live_trader.resolve_cafeopen_orders(later)

    filled = (s.query(Order)
               .filter(Order.strategy == STRATEGY_CAFEOPEN,
                       Order.status == "SIMULATED")
               .one())
    assert filled.trade_date == later

    # The lookup the bracket engine performs must land on the filled row.
    entry = (s.query(Order)
              .filter(Order.strategy == STRATEGY_CAFEOPEN,
                      Order.code == "126730",
                      Order.side == "BUY",
                      Order.status.notin_(("CANCELLED", "PENDING", "REJECTED")))
              .order_by(Order.trade_date.asc())
              .first())
    assert entry.id == filled.id
    assert entry.trade_date == later


# ─── registry wiring ────────────────────────────────────────────────


def test_twin_shares_cafe_exit_rules_exactly():
    """If these ever diverge the curves stop measuring the entry alone."""
    assert (live_trader.EXIT_RULES[STRATEGY_CAFEOPEN]
            == live_trader.EXIT_RULES[STRATEGY_CAFE])
    assert STRATEGY_CAFEOPEN in live_trader.BRACKET_STRATEGIES
