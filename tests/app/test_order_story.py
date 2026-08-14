"""GET /live/orders/{id}/story — the row-click "why did this trade happen" view.

The builder composes: episode entry (position-timeline walk), rule lines
(recorded `reasons.exit` first, else rebuilt from CURRENT rules with
reconstructed=True), the day's bar (recorded → qlib → KIS), rank history,
post-trade closes, and a judgment blurb. Every section degrades independently.
"""

from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Order, Signal  # noqa: E402


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
        trade_date=date(2026, 8, 12),
        strategy="cafe",
        code="226340",
        name="본느",
        side="SELL",
        qty=329,
        price=8_866.0,
        ord_dvsn=Order.ORD_DVSN_SIM,
        status="SIMULATED",
        submitted_at=datetime(2026, 8, 12, 6, 49, 0),
    )
    base.update(kw)
    return Order(**base)


def _signal(**kw) -> Signal:
    base = dict(as_of=date(2026, 8, 12), rank=1, code="005930",
                model_class="LGBModel", strategy_class="TopkDropoutStrategy")
    base.update(kw)
    return Signal(**base)


BONNE_BAR = {"open": 8_750.0, "high": 9_680.0, "low": 7_560.0, "close": 7_560.0}


def _story(monkeypatch, session, order_id, *, exit_rule=None, day_ohlc=None,
           prices=None, prev_closes=None, kis_bars=()):
    """Call the real endpoint against an in-memory DB with qlib/KIS stubbed."""
    from app.api.routers import live as live_router

    class _Ctx:
        def __enter__(self_inner):
            return session

        def __exit__(self_inner, *a):
            return False

    monkeypatch.setattr(live_router, "SessionLocal", lambda: _Ctx())
    monkeypatch.setattr(live_router, "_stock_name_safe", lambda code: None)
    monkeypatch.setattr(live_router, "_exit_rule", lambda s: (exit_rule or {}).get(s, {}))
    monkeypatch.setattr(live_router, "_trade_consts", lambda: (10, 30))
    monkeypatch.setattr(live_router, "_price_series", lambda code: prices or {})
    monkeypatch.setattr(live_router, "_day_ohlc_safe",
                        lambda code, day: (day_ohlc or {}).get(day.isoformat()))
    monkeypatch.setattr(live_router, "_prev_low_safe", lambda *a: None)
    monkeypatch.setattr(live_router, "_peak_close_safe", lambda *a: None)
    monkeypatch.setattr(live_router, "_prev_close_cached",
                        lambda code, day: (prev_closes or {}).get(code))
    monkeypatch.setattr(live_router, "_kis_bars_cached", lambda code, day: kis_bars)
    return live_router.get_order_story(order_id)


def _add_bonne_episode(session, *, sell_reasons=None):
    """8/11 cafe BUY 329 @8,060 (pattern A, stop 6,900) → 8/12 SELL @8,866."""
    session.add(_order(
        trade_date=date(2026, 8, 11), side="BUY", price=8_060.0,
        submitted_at=datetime(2026, 8, 11, 6, 28, 0),
        reasons_json='{"action":"buy","basis":"카페 모사 — 패턴 A(신고가 돌파), 손절 6,900원",'
                     '"stop_px":6900.0}'))
    session.add(_order(reasons_json=sell_reasons))
    session.commit()
    return [o.id for o in session.query(Order).order_by(Order.id).all()]


# ─── cafe SELL: rules rebuilt from current config when nothing was recorded ──


def test_cafe_sell_reconstructs_rules_and_marks_them_estimated(monkeypatch, session):
    _, sell_id = _add_bonne_episode(
        session,
        sell_reasons='{"action":"sell","basis":"브래킷 익절 +10.0% 도달 — 평단 8,060 → 체결 8,866"}')

    s = _story(monkeypatch, session, sell_id,
               exit_rule={"cafe": {"tp": 0.10, "stop_source": "entry"}},
               day_ohlc={"2026-08-12": BONNE_BAR},
               prices={"2026-08-13": (7_200.0, 7_810.0)})

    assert s.entry.trade_date == date(2026, 8, 11)
    assert s.entry.exec_price == 8_060.0
    assert s.entry.avg_at_sale == pytest.approx(8_060.0)
    assert s.position_before == 329 and s.position_after == 0

    assert s.rules.reconstructed is True
    by_kind = {l.kind: l for l in s.rules.lines}
    assert by_kind["tp"].px == pytest.approx(8_866.0)
    assert by_kind["tp"].hit is True          # day high 9,680 crossed the TP
    assert s.rules.sl_kind == "entry_stop"    # screener stop 6,900 > cap 6,851
    assert by_kind["sl"].px == pytest.approx(6_900.0)
    assert by_kind["sl"].hit is False         # day low 7,560 stayed above

    assert s.bar.source == "qlib" and s.bar.high == 9_680.0
    assert s.judgment.mode == "sim_daily_bar"
    # Post-exit drift: closed 7,810 the next day → selling at 8,866 kept +11.9%.
    assert s.post_closes[0].close == 7_810.0
    assert s.give_back_pct == pytest.approx((7_810.0 / 8_866.0 - 1) * 100, abs=0.01)


def test_recorded_exit_snapshot_wins_over_reconstruction(monkeypatch, session):
    _, sell_id = _add_bonne_episode(
        session,
        sell_reasons='{"action":"sell","basis":"브래킷 익절 +10.0% 도달",'
                     '"exit":{"judged":"sim_daily_bar",'
                     '"bar":{"open":8750.0,"high":9680.0,"low":7560.0,"close":7560.0},'
                     '"tp_px":8866.0,"sl_px":6900.0,"sl_kind":"entry_stop",'
                     '"entry_avg":8060.0,"entry_date":"2026-08-11","entry_order_id":1}}')

    s = _story(monkeypatch, session, sell_id,
               exit_rule={"cafe": {"tp": 0.10, "stop_source": "entry"}})

    assert s.rules.reconstructed is False
    assert s.bar.source == "recorded" and s.bar.high == 9_680.0
    by_kind = {l.kind: l for l in s.rules.lines}
    assert by_kind["tp"].px == 8_866.0 and by_kind["tp"].hit is True
    assert by_kind["sl"].px == 6_900.0 and s.rules.sl_kind == "entry_stop"


# ─── scale: two partial sells the same day keep distinct stages ──────


def test_scale_same_day_partial_sells_get_stage_and_quantities(monkeypatch, session):
    session.add(_order(strategy="scale", code="000150", trade_date=date(2026, 8, 10),
                       side="BUY", qty=100, price=10_000.0,
                       submitted_at=datetime(2026, 8, 10, 6, 28)))
    session.add(_order(strategy="scale", code="000150", qty=50, price=11_000.0,
                       submitted_at=datetime(2026, 8, 12, 6, 48)))
    session.add(_order(strategy="scale", code="000150", qty=50, price=11_500.0,
                       submitted_at=datetime(2026, 8, 12, 6, 48, 30)))
    session.commit()
    ids = [o.id for o in session.query(Order).order_by(Order.id).all()]
    rule = {"scale": {"ladder": [0.10, 0.15, 0.20], "floor_gap": 0.05}}

    s1 = _story(monkeypatch, session, ids[1], exit_rule=rule)
    s2 = _story(monkeypatch, session, ids[2], exit_rule=rule)

    assert s1.stage == 1 and s2.stage == 2
    assert (s1.position_before, s1.position_after) == (100, 50)
    assert (s2.position_before, s2.position_after) == (50, 0)
    # Both sells belong to the same 8/10 entry at avg 10,000.
    assert s1.entry.trade_date == s2.entry.trade_date == date(2026, 8, 10)
    assert s1.entry.avg_at_sale == pytest.approx(10_000.0)
    assert s2.entry.avg_at_sale == pytest.approx(10_000.0)


# ─── open: rank-dropout story leans on the signal history ────────────


def test_open_sell_rank_history_includes_unranked_days(monkeypatch, session):
    session.add(_order(strategy="open", code="008930", name="한미사이언스",
                       trade_date=date(2026, 8, 11), side="BUY", qty=10,
                       price=None, ord_dvsn="01", status="SUBMITTED",
                       submitted_at=datetime(2026, 8, 11, 0, 0)))
    session.add(_order(strategy="open", code="008930", name="한미사이언스",
                       qty=10, price=None, ord_dvsn="01", status="SUBMITTED",
                       submitted_at=datetime(2026, 8, 12, 0, 0),
                       reasons_json='{"action":"sell","basis":"당일 신호 top-10 이탈 — 전일 1위 → 금일 30위권 밖"}'))
    session.add(_signal(as_of=date(2026, 8, 11), code="008930", rank=1))
    # 8/12 signals exist but 008930 fell out of the stored top-30 entirely.
    session.add(_signal(as_of=date(2026, 8, 12), code="005930", rank=1))
    session.commit()
    sell_id = session.query(Order).filter(Order.side == "SELL").one().id

    s = _story(monkeypatch, session, sell_id,
               prices={"2026-08-11": (98_000.0, 99_000.0),
                       "2026-08-12": (97_000.0, 95_000.0)})

    assert s.rules.exit_model == "rank_dropout"
    pts = {p.as_of: p.rank for p in s.rank_history}
    assert pts[date(2026, 8, 11)] == 1
    assert pts[date(2026, 8, 12)] is None  # 권외 — the story's whole point
    assert s.entry.rank == 1
    assert s.judgment.mode == "real_order"


# ─── limit BUY: resting-limit narrative, gap-down fill flagged ───────


def test_limit_buy_reconstructs_resting_price_and_gap_fill(monkeypatch, session):
    session.add(_order(strategy="limit", code="078930", name="GS", side="BUY",
                       qty=10, price=9_400.0, reasons_json=None))
    session.commit()
    oid = session.query(Order).one().id

    s = _story(monkeypatch, session, oid,
               exit_rule={"limit": {"tp": 0.10}},
               prev_closes={"078930": 10_000.0})

    assert s.limit_entry is not None
    assert s.limit_entry.prev_close == 10_000.0
    assert s.limit_entry.limit_px == pytest.approx(9_700.0)
    assert s.limit_entry.fill_px == 9_400.0
    assert s.limit_entry.discount_pct == pytest.approx(-6.0)
    assert s.limit_entry.gap_down_fill is True


def test_limit_buy_prefers_recorded_entry_snapshot(monkeypatch, session):
    session.add(_order(
        strategy="limit", code="078930", side="BUY", qty=10, price=9_700.0,
        reasons_json='{"action":"buy","basis":"지정가 −3.0% 체결",'
                     '"entry":{"limit_px":9700,"prev_close":10000.0,'
                     '"fill_px":9700.0,"rank":3,"discount":0.03}}'))
    session.commit()
    oid = session.query(Order).one().id

    s = _story(monkeypatch, session, oid, exit_rule={"limit": {"tp": 0.10}})

    assert s.limit_entry.limit_px == 9_700
    assert s.limit_entry.gap_down_fill is False
    assert s.limit_entry.discount_pct == pytest.approx(-3.0)


# ─── degradation: the core never dies ────────────────────────────────


def test_orphan_sell_without_reasons_degrades_with_notes(monkeypatch, session):
    session.add(_order(reasons_json=None))  # SELL, no prior BUY, no reasons
    session.commit()
    oid = session.query(Order).one().id

    s = _story(monkeypatch, session, oid,
               exit_rule={"cafe": {"tp": 0.10, "stop_source": "entry"}})

    assert s.order.id == oid
    assert s.entry is None
    assert s.rules.lines == []          # no avg to anchor the lines to
    assert s.bar is None                # no qlib bar, KIS stub empty
    assert s.judgment.mode == "sim_daily_bar"
    joined = " ".join(s.notes)
    assert "사유 기록이 없습니다" in joined
    assert "봉 데이터를 찾지 못했습니다" in joined


def test_missing_order_is_404(monkeypatch, session):
    with pytest.raises(HTTPException) as exc:
        _story(monkeypatch, session, 999)
    assert exc.value.status_code == 404
