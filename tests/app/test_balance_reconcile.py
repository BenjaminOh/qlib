"""잔고 차이로 원장을 맞추는 정산기 — 계약을 고정한다.

2026-08-26: 사용자가 "MTS 에서 직접 매도하면 최근 주문건에 어떻게 반영되나"를
물었고, 답은 **어디에도 반영되지 않는다** 였다. 같은 조사에서 09:25 사다리
예약의 체결도 정산하는 곳이 없다는 걸 발견했다 — `reconcile_fills` 는 09:20 에
돌고 예약은 09:25 에 걸린다.

모의계좌는 체결내역 TR 이 빈 값을 돌려주므로(운영 실측 `kis_fills: 0`)
**체결을 알 수 있는 유일한 길이 잔고 차이**다. 그래서 둘이 같은 문제다.

여기서 막는 사고:
  * **우리가 낸 사다리 체결을 "사용자가 팔았다"로 오인해 가짜 행을 쓰는 것** —
    배분 순서가 뒤집히면 바로 이렇게 된다.
  * 잔고 조회가 실패했는데 빈 잔고를 "전량 매도"로 읽어 **원장 전체에 가짜
    청산을 찍는 것.**
  * 수동 매수를 합성 BUY 로 기록해 **사다리·트레일이 사용자가 직접 산 종목을
    팔러 가는 것.**
  * 재실행이 같은 청산을 두 번 쓰는 것.
  * 어제자 미체결 예약이 SUBMITTED 로 영원히 쌓이는 것.
"""

import datetime as dt

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.api.db import (  # noqa: E402
    Base, DEFAULT_ACCOUNT_ID, EXIT_KIND_LADDER, EXIT_KIND_MANUAL,
    Fill, Order, STRATEGY_OPEN, TradingAccount,
)
from app.api.services import balance_reconcile as BR  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402
from app.api.services.kis_client import AccountSnapshot, Holding  # noqa: E402

DAY = dt.date(2026, 8, 27)
ENTRY_DAY = dt.date(2026, 8, 25)
CODE = "214330"          # 금호에이치티 — 실제로 문제가 된 종목
AVG = 6494.0
QTY = 134
LADDER_PX = 7140.0       # 평단 × 1.10 → 호가단위 5원 그리드
CLOSE = 7310.0           # 08-26 종가


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


class _NoCloseSession:
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


class FakeKIS:
    """잔고와 체결내역만 답하는 KIS. 주문은 내지 않는다(정산기는 안 낸다)."""

    is_mock = False

    def __init__(self, holdings, *, fills=None, balance_raises=False):
        self._holdings = holdings
        self._fills = fills or {}
        self._raises = balance_raises

    def get_balance(self):
        if self._raises:
            raise RuntimeError("KIS 잔고 조회 실패")
        return AccountSnapshot(cash=0.0, total_eval=0.0, holdings=self._holdings)

    def get_daily_fills(self, start, end):
        return self._fills


@pytest.fixture
def env(session, monkeypatch):
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(BR, "init_db", lambda: None)
    monkeypatch.setattr(BR, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(lt, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "_stock_name", lambda c: "금호에이치티")
    monkeypatch.setattr(lt, "_last_trading_day", lambda: DAY)
    monkeypatch.setattr(lt, "_day_ohlc_any", lambda c, d: {
        "open": 9100.0, "high": 10000.0, "low": 7280.0, "close": CLOSE})
    session.add(TradingAccount(account_id=DEFAULT_ACCOUNT_ID))
    session.commit()
    return session


def _holding(qty, avg=AVG):
    return Holding(code=CODE, qty=qty, avg_price=avg, eval_price=CLOSE,
                   eval_value=CLOSE * qty, pnl=0.0, pnl_pct=0.0,
                   name="금호에이치티", sellable_qty=qty)


def _buy(session, *, day=ENTRY_DAY, qty=QTY, status="FILLED", price=AVG):
    o = Order(trade_date=day, strategy=STRATEGY_OPEN, code=CODE,
              name="금호에이치티", side="BUY", qty=qty, price=price,
              ord_dvsn="01", status=status, kis_order_id="B1",
              submitted_at=dt.datetime(2026, 8, 25, 0, 1))
    session.add(o)
    session.commit()
    if status in ("FILLED", "PARTIAL"):
        session.add(Fill(order_id=o.id, strategy=STRATEGY_OPEN, qty=qty, price=price))
        session.commit()
    return o


def _ladder(session, *, day=DAY, qty=67, price=LADDER_PX, status="SUBMITTED"):
    import json
    o = Order(trade_date=day, strategy=STRATEGY_OPEN, code=CODE,
              name="금호에이치티", side="SELL", qty=qty, price=price,
              ord_dvsn="00", status=status, kis_order_id="S1",
              submitted_at=dt.datetime(2026, 8, 27, 0, 25),
              reasons_json=json.dumps({"exit": {"kind": EXIT_KIND_LADDER}}))
    session.add(o)
    session.commit()
    return o


def _sells(session):
    return (session.query(Order).filter(Order.side == "SELL")
                   .order_by(Order.id.asc()).all())


def _synthetic(session):
    return [o for o in _sells(session)
            if o.kis_order_id is None and o.status == "FILLED"]


# ─── ① 우리 미체결 주문 배분이 먼저다 ────────────────────────────────


def test_ladder_fill_is_matched_not_invented(env):
    """사다리 예약 체결을 **수동 매도로 오인하지 않는다.**

    배분 순서가 이 모듈의 전부다. 먼저 배분하지 않으면 우리가 낸 주문의 체결을
    "사용자가 팔았다"로 읽어 원장에 가짜 행을 쓴다.
    """
    _buy(env)
    o = _ladder(env)
    kis = FakeKIS([_holding(QTY - 67)])          # 절반이 체결돼 잔고가 줄었다

    res = BR.reconcile_by_balance(DAY, client=kis)

    assert res["manual"] == []                   # 합성 행 없음
    assert len(res["matched"]) == 1
    assert (o.status, o.price) == ("FILLED", LADDER_PX)
    fill = env.query(Fill).filter(Fill.order_id == o.id).one()
    assert (fill.qty, fill.price) == (67, LADDER_PX)
    assert fill.pnl is not None and fill.pnl > 0   # +10% 익절이니 이익이다


def test_partial_ladder_fill_becomes_partial(env):
    """67주 예약 중 30주만 붙었다."""
    _buy(env)
    o = _ladder(env)
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([_holding(QTY - 30)]))

    assert o.status == "PARTIAL"
    assert res["manual"] == []
    assert env.query(Fill).filter(Fill.order_id == o.id).one().qty == 30


# ─── ② 남은 것은 수동 매도 ──────────────────────────────────────────


def test_full_manual_sell_is_repaired(env):
    """전량 수동 매도 — 지금까지 전 화면에서 흔적 없이 증발하던 경우다."""
    _buy(env)
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([]))   # 잔고에서 사라짐

    assert [m["qty"] for m in res["manual"]] == [QTY]
    assert res["manual"][0]["price_source"] == "close"
    (o,) = _synthetic(env)
    assert (o.side, o.qty, o.price) == ("SELL", QTY, CLOSE)
    assert o.kis_order_id is None
    import json
    assert json.loads(o.reasons_json)["exit"]["kind"] == EXIT_KIND_MANUAL


def test_partial_manual_sell_leaves_the_rest_held(env):
    _buy(env)
    BR.reconcile_by_balance(DAY, client=FakeKIS([_holding(67)]))

    (o,) = _synthetic(env)
    assert o.qty == 67
    # 다시 돌려도 잔여 67주는 건드리지 않는다.
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([_holding(67)]))
    assert res["manual"] == [] and len(_synthetic(env)) == 1


def test_ladder_fill_and_manual_sell_together(env):
    """예약 67주가 체결되고 사용자가 나머지 67주도 팔았다.

    ①이 예약분을 **먼저** 먹고, ②는 남은 것만 합성해야 한다. 순서가 뒤집히면
    합성 행이 134주가 되고 예약은 영원히 SUBMITTED 로 남는다.
    """
    _buy(env)
    o = _ladder(env)
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([]))   # 전량 사라짐

    assert o.status == "FILLED"
    assert [m["qty"] for m in res["matched"]] == [67]
    assert [m["qty"] for m in res["manual"]] == [67]
    (syn,) = _synthetic(env)
    assert syn.qty == 67


def test_real_account_fill_price_beats_the_close_estimate(env):
    """실계좌로 가면 체결내역이 살아나 추정이 사라진다."""
    _buy(env)
    kis = FakeKIS([], fills={"9999": {"code": CODE, "side": "SELL",
                                      "ccld_qty": QTY, "avg_price": 7850.0}})
    res = BR.reconcile_by_balance(DAY, client=kis)

    assert (res["manual"][0]["price"], res["manual"][0]["price_source"]) == (7850.0, "ccld")


# ─── 안전장치 ───────────────────────────────────────────────────────


def test_manual_buy_is_never_written(env):
    """수동 매수에 합성 BUY 를 쓰면 **사다리·트레일이 남의 포지션을 팔러 간다.**"""
    kis = FakeKIS([_holding(50)])              # 원장 0, 잔고 50 — 사용자가 직접 샀다
    res = BR.reconcile_by_balance(DAY, client=kis)

    assert res["manual"] == [] and res["matched"] == []
    assert env.query(Order).count() == 0


def test_balance_failure_writes_nothing(env):
    """빈 잔고를 '전량 매도'로 읽으면 원장 전체에 가짜 청산이 찍힌다."""
    _buy(env)
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([], balance_raises=True))

    assert res["status"] == "balance_unavailable"
    assert _sells(env) == []


def test_unfilled_buy_does_not_flip_the_gap(env):
    """미체결 매수를 보유로 세면 gap 이 뒤집혀 가짜 매도가 나온다."""
    _buy(env, status="SUBMITTED", price=None)   # 09:00 시장가, 아직 미체결
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([]))
    # 체결된 매수가 없으므로 원장 순수량 0 → 팔 것이 없다.
    assert res["manual"] == [] and _synthetic(env) == []


def test_idempotent(env):
    """한 번 쓰면 차이가 0이 되므로 재실행이 저절로 안전하다."""
    _buy(env)
    BR.reconcile_by_balance(DAY, client=FakeKIS([]))
    BR.reconcile_by_balance(DAY, client=FakeKIS([]))
    assert len(_synthetic(env)) == 1
    assert env.query(Fill).filter(Fill.qty == QTY, Fill.price == CLOSE).count() == 1


def test_over_cap_writes_nothing_but_reports(env, monkeypatch):
    """불일치가 상한을 넘으면 사람이 볼 몫이다 — 원장이 폭주하면 안 된다."""
    monkeypatch.setattr(BR.settings, "live_manual_exit_max_per_day", 0)
    _buy(env)
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([]))

    assert res["over_cap"] is True and res["written"] is False
    assert len(res["manual"]) == 1          # 알림에는 나온다
    assert _synthetic(env) == []            # 원장에는 안 쓴다


def test_dry_run_writes_nothing(env):
    _buy(env)
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([]), write=False)
    assert len(res["manual"]) == 1 and _synthetic(env) == []


# ─── ③ 당일 유효가 끝난 예약 정리 ───────────────────────────────────


def test_stale_reservation_is_expired(env):
    """어제 걸어둔 예약은 장 마감에 이미 소멸했다. DB 만 그걸 모른다."""
    _buy(env)
    old = _ladder(env, day=dt.date(2026, 8, 26))
    today = _ladder(env, day=DAY)

    BR.reconcile_by_balance(DAY, client=FakeKIS([_holding(QTY)]))

    assert (old.status, old.error) == ("CANCELLED", "장 마감 — 당일 유효 주문 소멸")
    assert today.status == "SUBMITTED"      # 오늘 것은 살려둔다


def test_non_ladder_resting_sell_is_not_expired(env):
    """사다리가 아닌 미체결 지정가는 취소 스윕의 몫이다. 여기서 손대지 않는다."""
    _buy(env)
    plain = Order(trade_date=dt.date(2026, 8, 26), strategy=STRATEGY_OPEN,
                  code=CODE, side="SELL", qty=10, price=9000.0, ord_dvsn="00",
                  status="SUBMITTED", kis_order_id="P1",
                  submitted_at=dt.datetime(2026, 8, 26, 5, 0))
    env.add(plain)
    env.commit()

    BR.reconcile_by_balance(DAY, client=FakeKIS([_holding(QTY)]))
    assert plain.status == "SUBMITTED"


# ─── 아무 일도 없는 평상시 ──────────────────────────────────────────


def test_quiet_day_changes_nothing(env):
    """평시에는 조용히 끝나야 한다 — 이게 대부분의 날이다."""
    _buy(env)
    res = BR.reconcile_by_balance(DAY, client=FakeKIS([_holding(QTY)]))
    assert res["matched"] == [] and res["manual"] == []
    assert _sells(env) == []


def test_gap_view_is_a_pure_read(env):
    """diff_by_code 는 아무것도 쓰지 않는다 — 진단으로 안전하게 부를 수 있다."""
    _buy(env)
    gaps = BR.diff_by_code(env, {CODE: 67}, strategy=STRATEGY_OPEN)
    (g,) = gaps
    assert (g.ledger_qty, g.kis_qty, g.gap) == (QTY, 67, 67)
    assert env.query(Fill).count() == 1     # 매수 Fill 하나 그대로
