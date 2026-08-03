"""기관·외국인 수급 오버레이 (`market_flow`) + the 'flow' simulated strategy.

No qlib data and no KIS: the $volume lookup and the KIS client are stubbed so
these tests only exercise the scoring, re-ranking and fallback logic.

The single most important property under test is the FALLBACK: when supply /
demand data is missing or unusable, 'flow' must trade exactly what 'close'
would rather than skipping or mis-ordering picks.
"""

from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Fill, MarketFlow, Order, Signal  # noqa: E402
from app.api.db.models import STRATEGY_CLOSE, STRATEGY_FLOW  # noqa: E402
from app.api.services import live_trader, market_flow  # noqa: E402


TODAY = date(2026, 5, 20)
# Five trading days ending the day before TODAY — flow always looks at data
# that exists at order time (KIS publishes a day's row only after the close).
FLOW_DAYS = [date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15),
             date(2026, 5, 18), date(2026, 5, 19)]


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def _add_flow(session, code, inst_per_day, frgn_per_day, days=None):
    for d in (days or FLOW_DAYS):
        session.add(MarketFlow(trade_date=d, code=code,
                               orgn_net_qty=inst_per_day,
                               frgn_net_qty=frgn_per_day,
                               prsn_net_qty=-(inst_per_day + frgn_per_day)))
    session.commit()


# ─── flow_scores ────────────────────────────────────────────────────


def test_flow_scores_normalises_by_traded_volume(session, monkeypatch):
    """Equal share counts in differently-liquid stocks are NOT equal signals."""
    _add_flow(session, "005930", inst_per_day=1_000, frgn_per_day=0)
    _add_flow(session, "000660", inst_per_day=1_000, frgn_per_day=0)
    monkeypatch.setattr(market_flow, "_window_volume",
                        lambda codes, as_of, lookback: {"005930": 1_000_000.0,
                                                        "000660": 100_000.0})

    scores = market_flow.flow_scores(session, ["005930", "000660"], date(2026, 5, 19))

    # 5,000 shares over the window: 0.5% of one stock's volume, 5% of the other
    assert scores["005930"]["inst_ratio"] == pytest.approx(0.005)
    assert scores["000660"]["inst_ratio"] == pytest.approx(0.05)
    assert scores["000660"]["score"] > scores["005930"]["score"]


def test_flow_scores_omits_codes_with_too_few_days(session, monkeypatch):
    """Two days of data is an UNKNOWN, not a weak signal — it must be absent."""
    _add_flow(session, "005930", 1_000, 0, days=FLOW_DAYS[:2])
    monkeypatch.setattr(market_flow, "_window_volume",
                        lambda codes, as_of, lookback: {"005930": 1_000_000.0})

    assert market_flow.flow_scores(session, ["005930"], date(2026, 5, 19)) == {}


def test_flow_scores_omits_codes_without_volume(session, monkeypatch):
    _add_flow(session, "005930", 1_000, 0)
    monkeypatch.setattr(market_flow, "_window_volume",
                        lambda codes, as_of, lookback: {})

    assert market_flow.flow_scores(session, ["005930"], date(2026, 5, 19)) == {}


def test_flow_scores_ignores_rows_after_as_of(session, monkeypatch):
    """No look-ahead: a row dated after `as_of` must not enter the window."""
    _add_flow(session, "005930", 1_000, 0, days=FLOW_DAYS)
    session.add(MarketFlow(trade_date=date(2026, 5, 20), code="005930",
                           orgn_net_qty=9_999_999, frgn_net_qty=0))
    session.commit()
    monkeypatch.setattr(market_flow, "_window_volume",
                        lambda codes, as_of, lookback: {"005930": 1_000_000.0})

    scores = market_flow.flow_scores(session, ["005930"], date(2026, 5, 19))
    assert scores["005930"]["inst_qty"] == pytest.approx(5_000.0)


# ─── rerank ─────────────────────────────────────────────────────────


def test_rerank_without_any_scores_returns_model_order(session):
    codes = ["A", "B", "C"]
    out, info = market_flow.rerank(codes, {})
    assert out == codes
    assert info["applied"] is False
    assert info["reason"] == "no_flow_data"


def test_rerank_promotes_strong_flow_over_model_rank():
    codes = ["A", "B", "C", "D"]
    scores = {c: {"score": s} for c, s in
              [("A", 0.001), ("B", 0.002), ("C", 0.05), ("D", 0.04)]}
    out, info = market_flow.rerank(codes, scores, {"blend": 0.5})
    assert info["applied"] is True
    # C is last-but-one on the model axis and best on flow → climbs to the top
    assert out[0] == "C"
    assert out.index("C") < out.index("A")


def test_rerank_drops_net_sold_codes_but_keeps_unknown_ones():
    codes = ["A", "B", "C"]
    scores = {"A": {"score": -0.03}, "B": {"score": 0.02}}  # C has no data
    out, info = market_flow.rerank(codes, scores)
    assert "A" not in out           # net sold by both groups → dropped
    assert set(out) == {"B", "C"}   # unknown keeps its slot
    assert info["dropped"] == ["A"]


def test_rerank_all_negative_falls_back_to_model_order():
    """An empty basket is worse than the model's own picks."""
    codes = ["A", "B"]
    scores = {"A": {"score": -0.01}, "B": {"score": -0.02}}
    out, info = market_flow.rerank(codes, scores)
    assert out == codes
    assert info["applied"] is False
    assert info["reason"] == "all_negative"


def test_rerank_blend_zero_is_pure_model_order():
    codes = ["A", "B", "C"]
    scores = {c: {"score": s} for c, s in [("A", 0.001), ("B", 0.002), ("C", 0.05)]}
    out, _ = market_flow.rerank(codes, scores, {"blend": 0.0})
    assert out == codes


def test_rerank_blend_one_is_pure_flow_order():
    codes = ["A", "B", "C"]
    scores = {c: {"score": s} for c, s in [("A", 0.001), ("B", 0.002), ("C", 0.05)]}
    out, _ = market_flow.rerank(codes, scores, {"blend": 1.0})
    assert out == ["C", "B", "A"]


# ─── ensure_flow_data ───────────────────────────────────────────────


class _FakeClient:
    is_mock = False

    def __init__(self, rows_by_code=None, fail=()):
        self.rows_by_code = rows_by_code or {}
        self.fail = set(fail)
        self.calls = []

    def get_investor_daily(self, code):
        self.calls.append(code)
        if code in self.fail:
            raise RuntimeError("boom")
        return self.rows_by_code.get(code, [])


def _kis_row(day, orgn, frgn):
    return {"date": day, "orgn_net_qty": orgn, "frgn_net_qty": frgn,
            "prsn_net_qty": 0, "orgn_net_amt": 0, "frgn_net_amt": 0,
            "prsn_net_amt": 0}


def test_ensure_flow_data_writes_rows_and_is_idempotent(session):
    client = _FakeClient({"005930": [_kis_row("20260519", 100, 50),
                                     _kis_row("20260518", 200, -10)]})
    first = market_flow.ensure_flow_data(session, ["005930"], date(2026, 5, 19),
                                         client=client)
    assert first["rows"] == 2
    assert session.query(MarketFlow).count() == 2

    second = market_flow.ensure_flow_data(session, ["005930"], date(2026, 5, 19),
                                          client=client)
    assert second["reason"] == "already_covered"
    assert client.calls == ["005930"]  # no second round-trip
    assert session.query(MarketFlow).count() == 2


def test_ensure_flow_data_survives_per_code_failure(session):
    client = _FakeClient({"000660": [_kis_row("20260519", 10, 10)]},
                         fail=["005930"])
    result = market_flow.ensure_flow_data(session, ["005930", "000660"],
                                          date(2026, 5, 19), client=client)
    assert result["failed"] == ["005930"]
    assert result["fetched"] == 1
    assert session.query(MarketFlow).count() == 1


def test_ensure_flow_data_skips_mock_client(session):
    class _Mock(_FakeClient):
        is_mock = True

    result = market_flow.ensure_flow_data(session, ["005930"], date(2026, 5, 19),
                                          client=_Mock())
    assert result["status"] == "skipped"
    assert session.query(MarketFlow).count() == 0


# ─── flow_summary ───────────────────────────────────────────────────


def test_flow_summary_reads_as_korean_one_liner():
    text = market_flow.flow_summary(
        {"days": 5, "inst_qty": 123_000.0, "frgn_qty": -8_000.0,
         "inst_ratio": 0.041, "frgn_ratio": -0.003})
    assert "기관 5일 +12.3만주(거래량 +4.1%)" in text
    assert "외국인 5일 -0.8만주(거래량 -0.3%)" in text


def test_flow_summary_of_nothing_is_empty():
    assert market_flow.flow_summary(None) == ""


# ─── submit_daily_orders(strategy='flow') ───────────────────────────


@pytest.fixture
def _sim_env(session, monkeypatch):
    monkeypatch.setattr(live_trader, "init_db", lambda: None)
    monkeypatch.setattr(live_trader, "SessionLocal", lambda: session)
    monkeypatch.setattr(live_trader, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(live_trader, "_last_close", lambda code: 10_000.0)
    monkeypatch.setattr(live_trader, "_stock_name", lambda code: f"name-{code}")
    monkeypatch.setattr(live_trader, "_prev_trading_day", lambda d: date(2026, 5, 19))
    return session


def _seed_signals(session, codes):
    for i, code in enumerate(codes, 1):
        session.add(Signal(as_of=TODAY, rank=i, code=code, name=f"n-{code}",
                           score=1.0 / i, model_class="LGBModel",
                           strategy_class="TopkDropoutStrategy"))
    session.commit()


def test_flow_strategy_buys_flow_ranked_picks_not_model_top(_sim_env, monkeypatch):
    """The whole point: a low-ranked stock with strong 기관/외국인 buying takes
    a buy slot the model alone would have given to a higher-ranked name.

    Every candidate is net BOUGHT here, so nothing is filtered out — the
    re-ranking itself has to do the work.
    """
    session = _sim_env
    codes = [f"00000{i}" for i in range(1, 6)]
    _seed_signals(session, codes)
    # Model rank #5 is the one being accumulated hardest.
    for i, code in enumerate(codes):
        _add_flow(session, code, inst_per_day=(5_000 if i == 4 else 100),
                  frgn_per_day=0)
    monkeypatch.setattr(market_flow, "_window_volume",
                        lambda c, a, lb: {code: 1_000_000.0 for code in c})
    monkeypatch.setattr(market_flow, "ensure_flow_data",
                        lambda *a, **k: {"status": "ok"})
    monkeypatch.setitem(market_flow.FLOW_CONFIG, "blend", 0.8)

    result = live_trader.submit_daily_orders(today=TODAY, strategy=STRATEGY_FLOW,
                                             simulated=True)

    assert result["status"] == "ok"
    assert result["flow"]["applied"] is True
    assert result["flow"]["dropped"] == []      # promotion, not filtering
    bought = {o.code for o in session.query(Order).all()}
    assert bought == {codes[0], codes[4]}       # #5 displaced the model's #2
    assert result["flow"]["model_top"][:2] == codes[:2]
    for f in session.query(Fill).all():
        assert f.strategy == STRATEGY_FLOW


def test_flow_strategy_without_data_matches_close_picks(_sim_env, monkeypatch):
    """No flow rows at all → identical behaviour to the close strategy."""
    session = _sim_env
    codes = [f"00000{i}" for i in range(1, 6)]
    _seed_signals(session, codes)
    monkeypatch.setattr(market_flow, "ensure_flow_data",
                        lambda *a, **k: {"status": "skipped"})
    monkeypatch.setattr(market_flow, "_window_volume", lambda c, a, lb: {})

    result = live_trader.submit_daily_orders(today=TODAY, strategy=STRATEGY_FLOW,
                                             simulated=True)

    assert result["status"] == "ok"
    assert result["flow"]["applied"] is False
    bought = {o.code for o in session.query(Order).all()}
    assert bought == set(codes[:2])  # n_drop=2 → the model's own top two


def test_flow_strategy_survives_overlay_exception(_sim_env, monkeypatch):
    """A crash inside the overlay must not cost the day's orders."""
    session = _sim_env
    codes = [f"00000{i}" for i in range(1, 6)]
    _seed_signals(session, codes)

    def _boom(*a, **k):
        raise RuntimeError("KIS is down")

    monkeypatch.setattr(market_flow, "ensure_flow_data", _boom)

    result = live_trader.submit_daily_orders(today=TODAY, strategy=STRATEGY_FLOW,
                                             simulated=True)

    assert result["status"] == "ok"
    assert result["flow"]["applied"] is False
    assert session.query(Fill).count() == 2


def test_flow_strategy_does_not_sell_on_rank_dropout(_sim_env, monkeypatch):
    """flow shares close's bracket-exit contract — held names are never sold
    just for leaving the top-K."""
    session = _sim_env
    _seed_signals(session, ["000001", "000002"])
    o = Order(trade_date=date(2026, 5, 19), strategy=STRATEGY_FLOW, code="009999",
              name="held", side="BUY", qty=10, price=10_000.0, ord_dvsn="01",
              status="SIMULATED")
    session.add(o)
    session.flush()
    session.add(Fill(order_id=o.id, strategy=STRATEGY_FLOW, qty=10, price=10_000.0))
    session.commit()
    monkeypatch.setattr(market_flow, "ensure_flow_data", lambda *a, **k: {})
    monkeypatch.setattr(market_flow, "_window_volume", lambda c, a, lb: {})

    result = live_trader.submit_daily_orders(today=TODAY, strategy=STRATEGY_FLOW,
                                             simulated=True)

    assert result["sells"] == 0
    assert not [o for o in session.query(Order).all() if o.side == "SELL"]


def test_bracket_exits_are_strategy_scoped(_sim_env, monkeypatch):
    """A close position must not be exited by a flow bracket run (and vice versa)."""
    session = _sim_env
    for strategy in (STRATEGY_CLOSE, STRATEGY_FLOW):
        o = Order(trade_date=date(2026, 5, 18), strategy=strategy, code="000001",
                  name="n", side="BUY", qty=10, price=10_000.0, ord_dvsn="01",
                  status="SIMULATED")
        session.add(o)
        session.flush()
        session.add(Fill(order_id=o.id, strategy=strategy, qty=10, price=10_000.0))
    session.commit()
    monkeypatch.setattr(live_trader, "_last_trading_day", lambda: TODAY)
    # Day range gaps straight through the +5% take-profit.
    monkeypatch.setattr(live_trader, "_day_ohlc", lambda code, day: {
        "open": 11_000.0, "high": 11_500.0, "low": 10_900.0, "close": 11_200.0})

    result = live_trader.evaluate_bracket_exits(strategy=STRATEGY_FLOW)

    assert result["strategy"] == STRATEGY_FLOW
    assert len(result["exits"]) == 1
    sells = [o for o in session.query(Order).all() if o.side == "SELL"]
    assert [o.strategy for o in sells] == [STRATEGY_FLOW]
