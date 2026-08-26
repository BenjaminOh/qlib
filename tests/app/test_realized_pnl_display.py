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
