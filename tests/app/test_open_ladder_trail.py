"""open 전략의 청산 규칙 — 사다리 절반 + 잔여 트레일링.

2026-08-27: 금호에이치티(214330)가 08-25 상한가, 08-26 장중 +54% 까지 갔는데
`open` 은 **랭크 이탈로만 팔기 때문에** 그 상승을 하나도 확정하지 못했다. 그리고
`open` 에는 익절·손절·트레일링이 **하나도 없었다** — 커밋 b31728ce 가 "노출을
100%로 키우면서 방어가 0" 이라고 1순위 과제로 지목해 둔 상태였다.

규칙: +10% 에 절반(예약 지정가) → 잔여는 최고 종가 대비 −7% 트레일링(장중 폴링).
구조적 손절(전저점 −1%, 캡 −10%)이 공통 분기에서 자동으로 딸려온다.

여기서 막는 사고:
  * **main 계좌 포지션을 카페 계좌로 팔려 드는 것** — 실계좌 브래킷이 cafereal
    하나뿐이던 시절의 하드코딩이 남아 있었다.
  * 예약 지정가가 sellable_qty 를 잡은 채로 전량 매도를 내 거부되는 것
  * 같은 종목에 사다리 예약이 매일 중복으로 쌓이는 것
  * 취소 스윕이 사다리 예약을 쓸어가는 것
  * 시뮬 5종의 규칙이 함께 바뀌어 A/B 가 무의미해지는 것
"""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.api.db import (  # noqa: E402
    ACCOUNT_STRATEGIES, CAFE_ACCOUNT_ID, DEFAULT_ACCOUNT_ID,
    STRATEGY_CAFEREAL, STRATEGY_OPEN, STRATEGY_SCALE,
)
from app.api.services import live_trader as lt  # noqa: E402


# ─── 커밋 1 — 실계좌 브래킷의 계좌 축 ───────────────────────────────


def test_bracket_account_follows_the_strategy():
    """open 은 main, cafereal 은 cafe. 예전엔 둘 다 cafe 로 갔다."""
    assert lt._account_for(STRATEGY_OPEN) == DEFAULT_ACCOUNT_ID
    assert lt._account_for(STRATEGY_CAFEREAL) == CAFE_ACCOUNT_ID


def test_simulated_strategy_falls_back_to_default_account():
    """시뮬 전략은 어느 계좌에도 속하지 않는다 — 실주문을 내지 않으므로
    계좌 해석이 호출될 일이 없지만, 물어보면 기본 계좌로 답한다."""
    assert lt._account_for(STRATEGY_SCALE) == DEFAULT_ACCOUNT_ID


def test_account_map_covers_every_real_bracket_strategy():
    """실주문 브래킷 전략은 반드시 어느 계좌에 속해야 한다.

    안 그러면 _account_for 가 기본 계좌로 물러나 **다른 계좌의 포지션을
    엉뚱한 계좌로 팔려 든다.**
    """
    owned = {s for strategies in ACCOUNT_STRATEGIES.values() for s in strategies}
    for s in lt.REAL_BRACKET_STRATEGIES:
        assert s in owned, f"{s} 가 ACCOUNT_STRATEGIES 에 없다"


def test_no_hardcoded_cafe_account_in_bracket_paths():
    """계좌 하드코딩 회귀 가드.

    _sell_bracket 과 evaluate_bracket_exits 가 CAFE_ACCOUNT_ID / ACCOUNT_CAFE 를
    직접 쓰면 안 된다 — _account_for 를 거쳐야 한다.
    """
    import inspect
    for fn in (lt._sell_bracket, lt.evaluate_bracket_exits):
        src = inspect.getsource(fn)
        assert "CAFE_ACCOUNT_ID" not in src, f"{fn.__name__} 에 계좌 하드코딩"
        assert "ACCOUNT_CAFE" not in src, f"{fn.__name__} 에 계좌 하드코딩"


# ─── 시뮬 5종은 건드리지 않는다 (A/B 보존) ──────────────────────────


def test_sim_exit_rules_unchanged():
    """scale 은 여전히 10/15/20 전량이어야 한다.

    open 이 [0.10]+트레일로 가는 것과 규칙이 달라야 두 곡선의 A/B 가 유효하다.
    같아지면 무엇을 비교하는지 알 수 없게 된다.
    """
    assert lt.EXIT_RULES[STRATEGY_SCALE]["ladder"] == [0.10, 0.15, 0.20]
    assert lt.EXIT_RULES[STRATEGY_SCALE].get("floor_gap") == 0.05
    assert "trail_rest" not in lt.EXIT_RULES[STRATEGY_SCALE]
    assert lt.EXIT_RULES["trail"] == {"trail": 0.07}
    assert lt.EXIT_RULES["close"] == {"tp": 0.10}


def test_open_stays_out_of_the_1625_bracket_sweep():
    """16:25 스윕(`close_bracket_exits`)은 BRACKET_STRATEGIES 를 돈다.

    open 을 거기 넣으면 **장 마감 후에 실주문**이 나가 다음 장까지 걸려 있게 된다.
    open 의 사다리는 09:25 예약, 트레일은 장중 폴링이 담당한다.
    """
    assert STRATEGY_OPEN not in lt.BRACKET_STRATEGIES
    assert STRATEGY_CAFEREAL in lt.BRACKET_STRATEGIES


def test_sync_account_reads_the_strategys_own_account():
    """스냅샷도 계좌 축을 따라야 한다.

    open 을 REAL_BRACKET_STRATEGIES 에 넣는 순간, sync_account 의 하드코딩된
    `get_kis_client(ACCOUNT_CAFE)` 가 **main 잔고 자리에 카페 잔고를 적는다.**
    """
    import inspect
    src = inspect.getsource(lt.sync_account)
    assert "ACCOUNT_CAFE" not in src
    assert "_account_for(strategy)" in src


# ─── 커밋 2 — 09:25 사다리 예약 ─────────────────────────────────────

import datetime as dt  # noqa: E402

from app.api.db import (  # noqa: E402
    Base, EXIT_KIND_LADDER, Fill, Order, TradingAccount,
)
from app.api.services.kis_client import AccountSnapshot, Holding, OrderResult  # noqa: E402

DAY = dt.date(2026, 8, 26)
ENTRY_DAY = dt.date(2026, 8, 25)
# 금호에이치티 214330 — 08-25 BUY 134주 @6,494. 사용자가 문제를 제기한 실제 건.
CODE = "214330"
AVG = 6494.0
QTY = 134


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


class RecordingKIS:
    """주문을 받아 적기만 하는 KIS. 호출 순서를 통째로 보존한다."""

    is_mock = False

    def __init__(self, holdings, *, ok=True):
        self._holdings = holdings
        self.ok = ok
        self.calls: list[tuple] = []

    def get_balance(self):
        self.calls.append(("balance",))
        return AccountSnapshot(cash=0.0, total_eval=0.0, holdings=self._holdings)

    def place_order(self, code, side, qty, price=None):
        self.calls.append(("order", code, side, qty, price))
        return OrderResult(ok=self.ok, order_id="ODNO-1", code=code, side=side,
                           qty=qty, price=price,
                           raw={"output": {"KRX_FWDG_ORD_ORGNO": "91252",
                                           "ODNO": "0000123"}},
                           error=None if self.ok else "거부")

    def cancel_order(self, *, code, side, qty, org_no, orgn_odno, ord_dvsn="00"):
        self.calls.append(("cancel", code, orgn_odno))
        return OrderResult(ok=True, order_id=orgn_odno, code=code, side=side,
                           qty=qty, price=None, raw={}, error=None)

    def get_quote(self, code):
        self.calls.append(("quote", code))
        return self.quote


@pytest.fixture
def env(session, monkeypatch):
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(lt, "_stock_name", lambda c: "금호에이치티")
    session.add(TradingAccount(account_id=DEFAULT_ACCOUNT_ID))
    session.commit()
    return session


def _holding(qty=QTY, avg=AVG, sellable=None):
    return Holding(code=CODE, qty=qty, avg_price=avg, eval_price=7290.0,
                   eval_value=7290.0 * qty, pnl=0.0, pnl_pct=0.0,
                   name="금호에이치티", sellable_qty=sellable)


def _buy(session, *, day=ENTRY_DAY, status="FILLED", qty=QTY):
    o = Order(trade_date=day, strategy=STRATEGY_OPEN, code=CODE,
              name="금호에이치티", side="BUY", qty=qty, price=AVG,
              ord_dvsn="01", status=status, kis_order_id="B1")
    session.add(o)
    session.commit()
    return o


def _sells(session):
    return (session.query(Order)
                   .filter(Order.side == "SELL")
                   .order_by(Order.id.asc()).all())


def test_reserve_places_half_at_plus_ten_percent(env):
    """평단 6,494 × 1.10 = 7,143.4 → 호가단위 5원 그리드로 7,140. 134/2 = 67주."""
    _buy(env)
    kis = RecordingKIS([_holding()])
    res = lt.reserve_ladder_exits(DAY, client=kis)

    assert res["status"] == "ok"
    assert kis.calls == [("balance",), ("order", CODE, "SELL", 67, 7140.0)]
    (o,) = _sells(env)
    assert (o.qty, o.price, o.ord_dvsn, o.status) == (67, 7140.0, "00", "SUBMITTED")
    assert lt._is_ladder_reservation(o.reasons_json)


def test_reserve_is_idempotent_within_the_day(env):
    """재시도가 예약을 두 번 걸면 매도 수량이 보유를 넘어간다."""
    _buy(env)
    kis = RecordingKIS([_holding()])
    lt.reserve_ladder_exits(DAY, client=kis)
    again = lt.reserve_ladder_exits(DAY, client=kis)

    assert len(_sells(env)) == 1
    assert again["skipped"] == [{"code": CODE, "why": "already_reserved"}]


def test_yesterdays_unfilled_reservation_does_not_block_today(env):
    """한국 주식 주문은 당일 유효 — 어제 미체결 예약은 이미 소멸했다.

    이걸 "이미 예약함"으로 읽으면 그 종목은 두 번 다시 익절선을 갖지 못한다.
    """
    _buy(env)
    kis = RecordingKIS([_holding()])
    lt.reserve_ladder_exits(ENTRY_DAY, client=kis)     # 어제치, 미체결로 남음
    lt.reserve_ladder_exits(DAY, client=kis)           # 오늘 다시

    assert [o.trade_date for o in _sells(env)] == [ENTRY_DAY, DAY]


def test_filled_rung_is_not_reserved_again(env):
    """이번 에피소드에서 이미 체결된 매도가 있으면 사다리는 끝났다."""
    _buy(env)
    sold = Order(trade_date=DAY, strategy=STRATEGY_OPEN, code=CODE, side="SELL",
                 qty=67, price=7140.0, ord_dvsn="00", status="FILLED")
    env.add(sold)
    env.commit()

    kis = RecordingKIS([_holding(qty=67)])
    res = lt.reserve_ladder_exits(DAY, client=kis)
    assert res["skipped"] == [{"code": CODE, "why": "rung_taken"}]
    assert len(_sells(env)) == 1


def test_holding_without_a_ledger_entry_is_left_alone(env):
    """원장이 설명하지 못하는 보유 — 수동 매매·대체입고. 남의 것을 팔지 않는다."""
    kis = RecordingKIS([_holding()])
    res = lt.reserve_ladder_exits(DAY, client=kis)
    assert res["skipped"] == [{"code": CODE, "why": "no_entry"}]
    assert _sells(env) == []


def test_reservation_is_clamped_to_sellable_qty(env):
    """미결제분까지 걸면 주문이 통째로 거부되고, 거부된 예약 = 익절선 없음."""
    _buy(env)
    kis = RecordingKIS([_holding(sellable=40)])
    lt.reserve_ladder_exits(DAY, client=kis)
    assert kis.calls[-1] == ("order", CODE, "SELL", 40, 7140.0)


def test_reservation_survives_the_cancel_sweep(env, monkeypatch):
    """컷오프 스윕이 예약을 쓸어가면 그날의 익절선이 조용히 사라진다.

    스윕의 취지는 미체결 지정가가 **주문가능현금**을 붙잡는 것을 푸는 건데,
    매도 예약은 현금을 잡지 않는다. main 계좌에 sell_cancel_hhmm 을 설정하는
    순간 터지는 잠복 버그다.
    """
    _buy(env)
    kis = RecordingKIS([_holding()])
    lt.reserve_ladder_exits(DAY, client=kis)
    # 사다리가 아닌 평범한 미체결 지정가 매도도 하나 둔다 — 대조군.
    plain = Order(trade_date=DAY, strategy=STRATEGY_OPEN, code="000720",
                  side="SELL", qty=5, price=1000.0, ord_dvsn="00",
                  status="SUBMITTED", kis_order_id="P1",
                  raw_response='{"output": {"KRX_FWDG_ORD_ORGNO": "9", "ODNO": "8"}}')
    env.add(plain)
    acct = env.query(TradingAccount).filter_by(account_id=DEFAULT_ACCOUNT_ID).one()
    acct.sell_cancel_hhmm = "14:00"
    acct.sell_order_type = "LIMIT"
    env.commit()

    out = lt.cancel_unfilled_orders(DAY, now=dt.datetime(2026, 8, 26, 14, 30),
                                    client=kis, account_id=DEFAULT_ACCOUNT_ID)
    assert out["cancelled"] == 1                      # 대조군만 취소됐다
    ladder = [o for o in _sells(env) if lt._is_ladder_reservation(o.reasons_json)]
    assert [o.status for o in ladder] == ["SUBMITTED"]


def test_pending_reservation_does_not_flip_the_badge_to_manual(env):
    """미체결 매도 예약은 보유를 줄이지 않는다.

    귀속기는 SUBMITTED 매도를 "미확정 매도"로 세어 확정 매수분에서 깎는다.
    사다리 예약에 그 규칙을 그대로 적용하면 **보유 종목 배지의 절반이
    '수동/미상'으로 뒤집힌다** — 아무것도 팔리지 않았는데도.
    """
    from app.api.services.holding_attribution import attribute_holdings

    _buy(env)
    lt.reserve_ladder_exits(DAY, client=RecordingKIS([_holding()]))

    attr = attribute_holdings({CODE: QTY}, DEFAULT_ACCOUNT_ID, db=env)[CODE]
    assert {c.strategy: c.qty for c in attr.claims} == {STRATEGY_OPEN: QTY}


def test_a_filled_reservation_does_reduce_the_badge(env):
    """체결되면 이야기가 다르다 — 그때는 정말 절반을 판 것이다."""
    from app.api.services.holding_attribution import attribute_holdings

    _buy(env)
    lt.reserve_ladder_exits(DAY, client=RecordingKIS([_holding()]))
    (o,) = _sells(env)
    o.status = "FILLED"
    env.add(Fill(order_id=o.id, strategy=STRATEGY_OPEN, qty=67, price=7140.0))
    env.commit()

    attr = attribute_holdings({CODE: QTY - 67}, DEFAULT_ACCOUNT_ID, db=env)[CODE]
    assert {c.strategy: c.qty for c in attr.claims} == {STRATEGY_OPEN: QTY - 67}
