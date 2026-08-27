"""실현손익 표시 — 화면이 원장과 같은 숫자를 말하게 한다.

2026-08-26: 사용자가 청산 화면과 주문 화면의 손익이 다르다고 지적했다. 운영 DB를
떠 보니 **버그가 둘**이었다.

  버그 1  /live/exits 가 30일치 매도를 전부 합산하면서 평단·매도가·날짜는 마지막
          한 건만 썼다. 100840 은 에피소드가 둘(37주 8/07~8/12, 33주 8/25~8/26)인데
          화면은 "70주 +116,700" 한 줄로 말했다. 093370 은 마지막 청산이 손실인데
          이전 이익과 합쳐져 **부호가 뒤집혔다**.
  버그 2  화면이 (매도가 − 평단) × 수량 을 다시 계산해 gross 를 보여줬다. 원장
          (Fill.pnl)·곡선(DailyPnL)·텔레그램(notify.py)은 전부 net 이라 **화면만
          다른 말을 하고 있었다.** 097230: 화면 −16,520 vs 원장 −18,613.46.

여기서 막는 사고:
  * 서로 다른 매매를 한 줄이 말하는 것
  * 화면 숫자와 계좌 숫자가 다른 것
  * 한 주문에 Fill 행이 여러 개일 때 하나만 읽어 과소 계상하는 것
  * 미정산 주문의 추정치를 확정치인 척 보여주는 것

이 파일이 생기기 전까지 **/exits 를 검사하는 테스트는 하나도 없었다.** 두 버그가
그 공백에서 자랐다.
"""

from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Fill, Order  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def api(monkeypatch, session):
    """실제 _position_timeline 을 인메모리 DB 위에서 돌린다 (qlib 만 차단)."""
    from app.api.routers import live as live_router

    class _Ctx:
        def __enter__(self):
            return session

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(live_router, "SessionLocal", lambda: _Ctx())
    monkeypatch.setattr(live_router, "_price_series", lambda code: {})
    monkeypatch.setattr(live_router, "_close_safe", lambda code: None)
    monkeypatch.setattr(live_router, "_stock_name_safe", lambda code: None)
    return live_router


def _trade(session, *, code, day, side, qty, price, pnl=None, fee=0.0,
           strategy="open", status="FILLED"):
    """주문 + (pnl 이 주어지면) Fill 한 쌍."""
    o = Order(trade_date=day, strategy=strategy, code=code, name=code,
              side=side, qty=qty, price=price, ord_dvsn="01", status=status,
              submitted_at=datetime.combine(day, datetime.min.time()))
    session.add(o)
    session.flush()
    if pnl is not None or side == "BUY":
        session.add(Fill(order_id=o.id, strategy=strategy, qty=qty,
                         price=price, fee=fee, pnl=pnl))
    session.commit()
    return o


# ─── 버그 2 — 손익은 net 이다 ────────────────────────────────────────


def test_timeline_reports_net_not_gross(api, session):
    """097230 실측. 화면 −16,520(gross) 이 아니라 원장 −18,613.46(net)이어야 한다."""
    _trade(session, code="097230", day=date(2026, 8, 21), side="BUY",
           qty=59, price=17_320.0, fee=143.06)
    _trade(session, code="097230", day=date(2026, 8, 26), side="SELL",
           qty=59, price=17_040.0, pnl=-18_613.46, fee=1_950.40)

    sells = [t for t in api._position_timeline("097230", "open") if t.side == "SELL"]
    t = sells[0]

    assert t.realized_pnl == pytest.approx(-18_613.46, abs=0.01)
    assert t.pnl_basis == "net"
    # gross 는 사라지지 않는다 — 툴팁·검산용으로 남는다.
    assert t.realized_gross == pytest.approx(-16_520.0, abs=0.01)
    assert t.realized_cost == pytest.approx(2_093.46, abs=0.01)


def test_unreconciled_sell_falls_back_to_gross_and_says_so(api, session):
    """Fill 이 없으면(09:20 대사 전) gross 로 물러나되 추정임을 밝힌다.

    모델링한 비용을 임의로 빼서 net 인 척하면 나중 확정치와 어긋난다.
    """
    _trade(session, code="005930", day=date(2026, 8, 25), side="BUY",
           qty=10, price=60_000.0)
    _trade(session, code="005930", day=date(2026, 8, 26), side="SELL",
           qty=10, price=61_000.0, status="SUBMITTED")   # Fill 없음

    t = [x for x in api._position_timeline("005930", "open") if x.side == "SELL"][0]

    assert t.pnl_basis == "gross"
    assert t.realized_pnl == pytest.approx(10_000.0)
    assert t.realized_cost is None


def test_multiple_fills_on_one_order_are_summed(api, session):
    """한 주문에 Fill 행이 여러 개일 수 있다 — .first() 로 읽으면 과소 계상된다.

    reconcile_fills 의 "행을 새로 만들면 같은 주문이 장부에 두 번 잡힌다" 주석이
    그 전력을 남겼다. 운영 DB에 그 시절 행이 남아 있을 수 있다.
    """
    _trade(session, code="005930", day=date(2026, 8, 25), side="BUY",
           qty=10, price=60_000.0)
    o = _trade(session, code="005930", day=date(2026, 8, 26), side="SELL",
               qty=10, price=61_000.0, pnl=4_000.0)
    session.add(Fill(order_id=o.id, strategy="open", qty=0, price=61_000.0,
                     fee=0.0, pnl=5_000.0))       # 두 번째 Fill 행
    session.commit()

    t = [x for x in api._position_timeline("005930", "open") if x.side == "SELL"][0]

    assert t.realized_pnl == pytest.approx(9_000.0), "Fill 을 하나만 읽었다"


def test_buy_rows_carry_no_realized_pnl(api, session):
    _trade(session, code="005930", day=date(2026, 8, 25), side="BUY",
           qty=10, price=60_000.0)

    t = api._position_timeline("005930", "open")[0]
    assert t.realized_pnl is None
    assert t.pnl_basis is None


def test_sell_without_prior_buy_does_not_crash(api, session):
    """선행 매수가 없는 매도 — 원장이 불완전해도 화면이 죽으면 안 된다."""
    _trade(session, code="005930", day=date(2026, 8, 26), side="SELL",
           qty=10, price=61_000.0, pnl=None, status="SUBMITTED")

    rows = api._position_timeline("005930", "open")
    assert rows[0].side == "SELL"
    assert rows[0].cum_qty == 0


# ─── 수익률 정의는 서버에 하나만 있다 ───────────────────────────────


def test_orders_view_ships_avg_and_pct_so_frontend_never_back_solves(api, session,
                                                                     monkeypatch):
    """프론트가 손익에서 평단을 역산하던 패턴을 없앤다.

    OrdersTable 은 `entry = price − pnl/qty` 로 평단을 되풀었다. net 은 그 공식이
    아니라 역산이 틀어진다. 서버가 avg_buy_price·ret_pct 를 직접 내려준다.
    """
    _trade(session, code="100840", day=date(2026, 8, 25), side="BUY",
           qty=33, price=26_887.878, fee=124.22)
    _trade(session, code="100840", day=date(2026, 8, 26), side="SELL",
           qty=33, price=29_550.0, pnl=85_834.02, fee=1_891.79)

    res = api.get_orders(limit=100, include_sim=False, strategy=None, view="all")
    sell = [r for r in res.orders if r.side == "SELL"][0]

    assert sell.realized_pnl == pytest.approx(85_834.02, abs=0.01)
    assert sell.avg_buy_price == pytest.approx(26_887.878, abs=0.001)
    # net 손익 ÷ 매입금액 = 85,834.02 / (26,887.878 × 33) = +9.67%
    assert sell.ret_pct == pytest.approx(0.0967, abs=0.0005)
    # 가격 등락(gross) 9.90% 와 다르다 — 그게 정상이다.
    assert sell.realized_gross == pytest.approx(87_850.03, abs=0.1)


# ─── 버그 1 — 에피소드 1건 = 1행 ────────────────────────────────────


def _exits(api, monkeypatch, *, days=30, held=()):
    monkeypatch.setattr(api, "get_balance_for_read",
                        lambda a="main": (type("S", (), {"holdings": [
                            type("H", (), {"code": c})() for c in held]})(), "live", None))
    return api.get_recent_exits(days=days, strategy="open")


def test_two_episodes_become_two_rows(api, session, monkeypatch):
    """100840 실측. 예전에는 "70주 +116,700" 한 줄이었다.

    08-07 BUY 37 → 08-12 SELL 37   Fill.pnl +28,850.01
    08-25 BUY 33 → 08-26 SELL 33   Fill.pnl +85,834.02
    """
    _trade(session, code="100840", day=date(2026, 8, 7), side="BUY",
           qty=37, price=28_120.27)
    _trade(session, code="100840", day=date(2026, 8, 12), side="SELL",
           qty=37, price=28_900.0, pnl=28_850.01)
    _trade(session, code="100840", day=date(2026, 8, 25), side="BUY",
           qty=33, price=26_887.878)
    _trade(session, code="100840", day=date(2026, 8, 26), side="SELL",
           qty=33, price=29_550.0, pnl=85_834.02)

    rows = _exits(api, monkeypatch, days=3650)

    assert len(rows) == 2, "에피소드가 둘인데 한 줄로 합쳐졌다"
    # 네거티브 단언 — 합산 버그의 지문
    assert all(r.sold_qty != 70 for r in rows), "37+33=70 으로 합산됐다"
    assert all(abs((r.realized_pnl or 0) - 116_700) > 1 for r in rows)

    latest = max(rows, key=lambda r: r.last_sell_date)
    assert latest.sold_qty == 33
    assert latest.realized_pnl == pytest.approx(85_834.02, abs=0.01)
    assert latest.avg_buy_price == pytest.approx(26_887.878, abs=0.001)
    assert latest.ret_pct == pytest.approx(0.0967, abs=0.0005)
    assert latest.entry_date == date(2026, 8, 25)

    older = min(rows, key=lambda r: r.last_sell_date)
    assert older.sold_qty == 37
    assert older.realized_pnl == pytest.approx(28_850.01, abs=0.01)


def test_loss_episode_is_not_masked_by_an_earlier_gain(api, session, monkeypatch):
    """093370 실측 — 합산 버그의 최악 사례. **부호가 뒤집혔다.**

    08-13 SELL +55,370 · 08-21 SELL −16,621 → 합치면 +38,749 로 이익처럼 보인다.
    """
    _trade(session, code="093370", day=date(2026, 8, 12), side="BUY",
           qty=97, price=10_689.0)
    _trade(session, code="093370", day=date(2026, 8, 13), side="SELL",
           qty=97, price=11_260.0, pnl=55_370.0)
    _trade(session, code="093370", day=date(2026, 8, 19), side="BUY",
           qty=81, price=12_280.0)
    _trade(session, code="093370", day=date(2026, 8, 21), side="SELL",
           qty=81, price=12_100.0, pnl=-16_621.0)

    rows = _exits(api, monkeypatch, days=3650)
    latest = max(rows, key=lambda r: r.last_sell_date)

    assert latest.realized_pnl < 0, "마지막 청산은 손실인데 이익으로 표시됐다"
    assert latest.realized_pnl == pytest.approx(-16_621.0, abs=0.01)
    assert latest.ret_pct is not None and latest.ret_pct < 0


def test_ladder_exit_stays_one_row(api, session, monkeypatch):
    """한 에피소드를 나눠 팔면(사다리) 여전히 한 줄이다 — 수량가중 평균 매도가."""
    _trade(session, code="005930", day=date(2026, 8, 20), side="BUY",
           qty=100, price=60_000.0)
    _trade(session, code="005930", day=date(2026, 8, 24), side="SELL",
           qty=40, price=63_000.0, pnl=110_000.0)
    _trade(session, code="005930", day=date(2026, 8, 26), side="SELL",
           qty=60, price=66_000.0, pnl=340_000.0)

    rows = _exits(api, monkeypatch, days=3650)

    assert len(rows) == 1
    r = rows[0]
    assert r.sold_qty == 100 and r.sell_count == 2
    assert r.realized_pnl == pytest.approx(450_000.0)
    # (40×63,000 + 60×66,000) / 100 = 64,800
    assert r.est_sell_price == pytest.approx(64_800.0)


def test_cutoff_uses_episode_close_not_each_sell(api, session, monkeypatch):
    """사다리가 컷오프에 걸쳐도 앞쪽 매도가 누락되지 않는다."""
    _trade(session, code="005930", day=date(2026, 1, 2), side="BUY",
           qty=100, price=60_000.0)
    _trade(session, code="005930", day=date(2026, 1, 3), side="SELL",
           qty=40, price=63_000.0, pnl=110_000.0)     # 컷오프 밖
    _trade(session, code="005930", day=date.today(), side="SELL",
           qty=60, price=66_000.0, pnl=340_000.0)     # 컷오프 안

    rows = _exits(api, monkeypatch, days=30)

    assert len(rows) == 1
    assert rows[0].sold_qty == 100, "컷오프 밖 매도가 빠졌다"
    assert rows[0].realized_pnl == pytest.approx(450_000.0)


def test_closed_exit_shows_even_while_holding_again(api, session, monkeypatch):
    """같은 종목을 다시 사서 보유 중이어도 **이전에 닫힌** 청산은 계속 보인다.

    예전 코드는 KIS 잔고를 읽어 "보유 중이면 청산이 아니다" 로 통째로 걸렀다.
    그러면 08-12 에 실제로 청산된 37주가 화면에서 사라지고, 이 카드의 합계가
    원장과 또 어긋난다. 이제 종료를 구조적으로(보유 0) 판정하므로 잔고를 안 본다.
    """
    _trade(session, code="100840", day=date(2026, 8, 7), side="BUY",
           qty=37, price=28_120.27)
    _trade(session, code="100840", day=date(2026, 8, 12), side="SELL",
           qty=37, price=28_900.0, pnl=28_850.01)
    _trade(session, code="100840", day=date(2026, 8, 25), side="BUY",
           qty=33, price=26_887.878)

    rows = _exits(api, monkeypatch, days=3650, held=("100840",))

    assert len(rows) == 1, "닫힌 08-12 청산까지 사라졌다"
    assert rows[0].sold_qty == 37


def test_zero_pnl_exit_is_not_erased(api, session, monkeypatch):
    """손익이 정확히 0인 청산이 "—" 로 지워지던 `or None` 버그."""
    _trade(session, code="005930", day=date(2026, 8, 25), side="BUY",
           qty=10, price=60_000.0)
    _trade(session, code="005930", day=date(2026, 8, 26), side="SELL",
           qty=10, price=60_000.0, pnl=0.0)

    rows = _exits(api, monkeypatch, days=3650)
    assert rows[0].realized_pnl == 0.0, "0원 손익이 None 으로 지워졌다"


def test_exits_sum_equals_ledger_sum(api, session, monkeypatch):
    """창 단위 불변식 — 버그1이 깨뜨렸던 바로 그 항등식.

    Σ(/exits 행 손익) == Σ(그 창에서 닫힌 에피소드의 Fill.pnl)
    """
    for code, buys, sells in [
        ("100840", [(date(2026, 8, 7), 37, 28_120.27), (date(2026, 8, 25), 33, 26_887.878)],
                   [(date(2026, 8, 12), 37, 28_900.0, 28_850.01),
                    (date(2026, 8, 26), 33, 29_550.0, 85_834.02)]),
        ("097230", [(date(2026, 8, 21), 59, 17_320.0)],
                   [(date(2026, 8, 26), 59, 17_040.0, -18_613.46)]),
    ]:
        for d, q, px in buys:
            _trade(session, code=code, day=d, side="BUY", qty=q, price=px)
        for d, q, px, pnl in sells:
            _trade(session, code=code, day=d, side="SELL", qty=q, price=px, pnl=pnl)

    rows = _exits(api, monkeypatch, days=3650)
    ledger = sum(f.pnl for f in session.query(Fill).all() if f.pnl is not None)

    assert sum(r.realized_pnl for r in rows) == pytest.approx(ledger, abs=0.01)


# ─── 시장가 청산의 체결가 추정 — 결정 시점 시세를 쓴다 ────────────────
#
# 2026-08-27: 214330 트레일 청산(134주 시장가)이 화면에 **+101,270 (+11.6%*)**
# 로 떴다. 실제 체결은 6,800 이라 약 41,000 이다. 시장가라 `Order.price` 가
# 없고, 추정이 그날 봉으로 물러나는데 **장중에는 오늘 봉이 없어 어제 종가**
# (7,250)가 잡혔다. 2.5배 부푼 숫자를 사용자가 보고 판단했다.
#
# `watch_trailing_exits` 는 청산할 때 본 현재가를 `reasons.exit.quote` 에
# 남긴다. 그게 가장 가까운 추정치다.


def test_market_sell_uses_the_decision_quote(api, session):
    """미체결 시장가 매도는 **결정 시점 시세**로 추정한다."""
    import datetime as dt
    import json

    _trade(session, code="214330", day=dt.date(2026, 8, 25), side="BUY",
           qty=134, price=6494.0)
    o = Order(trade_date=dt.date(2026, 8, 27), strategy="open", code="214330",
              name="214330", side="SELL", qty=134, price=None, ord_dvsn="01",
              status="SUBMITTED", kis_order_id=None,
              reasons_json=json.dumps({"exit": {"kind": "trail_exit",
                                                "quote": 6800.0}}))
    session.add(o)
    session.commit()

    rows = api._position_timeline("214330", "open", kis_now=7250.0)
    sell = [r for r in rows if r.side == "SELL"][0]
    assert sell.exec_price == 6800.0, "어제 종가가 아니라 결정 시점 시세를 써야 한다"
    assert sell.price_est is True                  # 여전히 추정이다 — 별표 유지
    # (6800 − 6494) × 134 = 41,004 에서 양방향 비용을 뺀 값
    assert 35_000 < sell.realized_pnl < 41_100


def test_falls_back_to_bars_without_a_quote(api, session):
    """`reasons.exit.quote` 가 없으면 기존 폴백(봉)이 그대로 동작한다."""
    import datetime as dt

    _trade(session, code="214330", day=dt.date(2026, 8, 25), side="BUY",
           qty=10, price=1000.0)
    session.add(Order(trade_date=dt.date(2026, 8, 27), strategy="open",
                      code="214330", name="214330", side="SELL", qty=10,
                      price=None, ord_dvsn="01", status="SUBMITTED",
                      kis_order_id=None))
    session.commit()

    rows = api._position_timeline("214330", "open", kis_now=1200.0)
    sell = [r for r in rows if r.side == "SELL"][0]
    assert sell.exec_price == 1200.0
