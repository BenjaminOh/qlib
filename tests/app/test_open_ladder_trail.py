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

    def __init__(self, holdings, *, ok=True, balance_raises=False):
        self._holdings = holdings
        self.ok = ok
        self._balance_raises = balance_raises
        self.calls: list[tuple] = []

    def get_balance(self):
        self.calls.append(("balance",))
        if self._balance_raises:
            raise RuntimeError("read timed out")
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


# ─── 커밋 3 — 장중 트레일링 폴링 ────────────────────────────────────


@pytest.fixture
def bars(monkeypatch):
    """금호에이치티 실측 봉. 08-25 종 8,550 / 08-26 종 7,310.

    _peak_close 는 **오늘 종가를 제외**하므로 08-26 의 최고 종가는 8,550 이고,
    트레일선은 8,550 × 0.93 = 7,951.5 — 평단 6,494 대비 +22.4%다. 그날 저가
    7,280 이 이를 깼다. 그게 사용자가 본 "40% 갔다가 14.7%로 되돌아온" 구간이다.
    """
    lt._TRAIL_LINE_CACHE.clear()
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "_peak_close", lambda c, e, d: 8550.0)
    monkeypatch.setattr(lt, "_prev_low", lambda c, e, w: 6100.0)
    return None


def _reserved(env, kis):
    lt.reserve_ladder_exits(DAY, client=kis)
    return _sells(env)[-1]


def test_trail_line_is_peak_close_minus_seven_percent(env, bars):
    line, kind = lt._trail_line(STRATEGY_OPEN, CODE, ENTRY_DAY, DAY, AVG, 0.07)
    assert (round(line, 1), kind) == (7951.5, "trail")


def test_trail_line_is_constant_through_the_session(env, bars, monkeypatch):
    """같은 날 두 번 물어도 같은 값이어야 한다.

    _peak_close 가 오늘 종가를 빼기 때문에 장중 상수다. 값이 틱마다 움직이면
    급등 중에 선이 따라 올라와 정상 눌림에서 털린다.
    """
    first = lt._trail_line(STRATEGY_OPEN, CODE, ENTRY_DAY, DAY, AVG, 0.07)
    monkeypatch.setattr(lt, "_peak_close", lambda c, e, d: 99999.0)  # 안 읽혀야 정상
    assert lt._trail_line(STRATEGY_OPEN, CODE, ENTRY_DAY, DAY, AVG, 0.07) == first


def test_structural_stop_wins_when_the_peak_is_below_entry(env, monkeypatch):
    """최고 종가가 평단 아래면 트레일선은 손절선 아래로 내려간다 → 손절이 이긴다.

    이게 없으면 물린 종목의 방어선이 계속 낮아진다.
    """
    lt._TRAIL_LINE_CACHE.clear()
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "_peak_close", lambda c, e, d: 6000.0)   # 평단 아래
    monkeypatch.setattr(lt, "_prev_low", lambda c, e, w: 6100.0)
    line, kind = lt._trail_line(STRATEGY_OPEN, CODE, ENTRY_DAY, DAY, AVG, 0.07)
    assert kind == "prev_low" and line > 6000.0 * 0.93


def test_breach_cancels_the_reservation_before_selling(env, bars):
    """순서가 전부다.

    예약이 매도가능수량을 붙잡고 있어서, 취소하지 않고 전량 매도를 내면
    수량 부족으로 거부된다 — 청산해야 할 바로 그 순간에.
    """
    _buy(env)
    kis = RecordingKIS([_holding()])
    _reserved(env, kis)
    kis.calls.clear()
    kis.quote = {"price": 7280.0}          # 08-26 저가. 트레일선 7,951.5 이탈

    out = lt.watch_trailing_exits(DAY, client=kis)

    kinds = [c[0] for c in kis.calls]
    assert kinds == ["balance", "quote", "cancel", "order"]
    assert kis.calls[-1] == ("order", CODE, "SELL", QTY, None)   # 전량 시장가
    assert out["exits"][0]["line_kind"] == "trail"
    cancelled = [o for o in _sells(env) if o.status == "CANCELLED"]
    assert len(cancelled) == 1


def test_above_the_line_does_nothing(env, bars):
    _buy(env)
    kis = RecordingKIS([_holding()])
    _reserved(env, kis)
    kis.calls.clear()
    kis.quote = {"price": 8000.0}          # 7,951.5 위

    out = lt.watch_trailing_exits(DAY, client=kis)
    assert out["exits"] == []
    assert [c[0] for c in kis.calls] == ["balance", "quote"]


def test_missing_quote_never_liquidates(env, bars):
    """값을 못 읽은 것을 '떨어졌다'로 읽으면 KIS 장애가 전량 청산이 된다."""
    _buy(env)
    kis = RecordingKIS([_holding()])
    kis.quote = {}

    out = lt.watch_trailing_exits(DAY, client=kis)
    assert out["exits"] == [] and out["watched"] == []
    assert not any(c[0] == "order" for c in kis.calls)


def test_entry_day_has_no_trail(env, bars):
    """진입 당일은 기준이 될 종가가 아직 없다."""
    _buy(env, day=DAY)
    kis = RecordingKIS([_holding()])
    kis.quote = {"price": 1.0}
    assert lt.watch_trailing_exits(DAY, client=kis)["exits"] == []


def test_both_tasks_skip_on_a_market_holiday(monkeypatch):
    """beat 는 mon-fri 만 알고 한국 공휴일을 모른다. 2026-08-17 에 그렇게
    닫힌 장으로 실주문 4건이 나갔다."""
    from app.api.workers import tasks as T

    monkeypatch.setattr("app.api.services.trading_calendar.is_market_open",
                        lambda d: False)
    called = []
    monkeypatch.setattr(lt, "reserve_ladder_exits",
                        lambda **kw: called.append("ladder"))
    monkeypatch.setattr(lt, "watch_trailing_exits",
                        lambda **kw: called.append("trail"))

    class _S:
        def update_state(self, **kw):
            pass

    assert T.ladder_reserve_task(_S())["status"] == "market_closed"
    assert T.trail_watch_task(_S())["status"] == "market_closed"
    assert called == []


def test_kumho_ht_real_bars_produce_the_designed_levels(env, bars):
    """이 설계를 고른 근거 자체를 박제한다 — 금호에이치티 08-25/26 실측.

        08-25  시 6,580  고 8,550  저 6,450  종 8,550   ← 상한가 마감
        08-26  시 9,100  고 10,000 저 7,280  종 7,310   ← 고가 +54% 후 되밀림

    두 값이 나와야 한다:
      · 사다리 = 6,494 × 1.10 → 호가단위 5원 그리드로 **7,140** (08-25 고가가 덮음)
      · 트레일 = 최고종가 8,550 × 0.93 = **7,951.5** (08-26 저가 7,280 이 이탈)

    두 값을 절반씩 채우면 블렌디드 **+16.2%** — 랭크 이탈만 쓰는 지금(+12.26%)
    보다 낫고, 사다리 10/15/20 전량(+13.77%)보다도 낫다. 트레일 단독은
    +22.44%지만 본전 회수가 없다.

    ⚠ 이건 **상한값**이다. 실제로는 5분 폴링이라 이탈을 7,951.5 에서 잡지
    못하고 그 아래 어딘가에서 시장가로 판다. 호가단위 내림도 0.03%p 를 깎는다.
    """
    ladder_px = lt.round_to_tick(AVG * 1.10)
    line, kind = lt._trail_line(STRATEGY_OPEN, CODE, ENTRY_DAY, DAY, AVG, 0.07)
    assert (ladder_px, round(line, 1), kind) == (7140, 7951.5, "trail")
    assert 8550.0 >= ladder_px          # 08-25 고가가 사다리를 덮는다
    assert 7280.0 <= line               # 08-26 저가가 트레일을 깬다

    half = QTY // 2
    gain = half * (ladder_px - AVG) + (QTY - half) * (line - AVG)
    assert round(gain / (QTY * AVG) * 100, 1) == 16.2


def test_trail_watch_stays_out_of_the_morning_order_window(monkeypatch):
    """09:00 주문 · 09:20 정산 · 09:25 예약과 같은 appkey 게이트를 다툰다.

    INSIGHTS 에 기록된 사고가 정확히 이것이다 — 대시보드 폴링과 경합해 매도가
    거부됐다. 주문을 늦추느니 30분 늦게 지켜보는 쪽이 낫다. 장 마감(15:30)
    뒤로는 시세가 움직이지 않아 폴링이 무의미하다.
    """
    from app.api.workers import tasks as T

    monkeypatch.setattr("app.api.services.trading_calendar.is_market_open",
                        lambda d: True)
    ran = []
    monkeypatch.setattr(lt, "watch_trailing_exits", lambda **kw: ran.append(1))
    # 바인딩된 celery 태스크를 직접 부르면 request id 가 없다. 창 판정과
    # 무관한 부분이므로 막아둔다.
    monkeypatch.setattr(T.trail_watch_task, "update_state", lambda **kw: None)

    def _freeze(h, m):
        monkeypatch.setattr(T, "datetime", type("D", (), {
            "now": staticmethod(lambda: dt.datetime(2026, 8, 26, h, m))}))

    for h, m in ((9, 0), (9, 25), (15, 30), (16, 25)):
        _freeze(h, m)
        assert T.trail_watch_task()["status"] == "outside_window", f"{h}:{m}"
    assert ran == []

    # 창 안이면 실제로 돈다.
    _freeze(9, 30)
    T.trail_watch_task()
    assert ran == [1]


# ─── KIS 장애 — 관측 실패는 판단 보류다 ─────────────────────────────


def test_ladder_holds_when_the_balance_cannot_be_read(env):
    """빈 잔고를 '보유 없음'으로 읽으면 그날 익절선이 통째로 사라진다.

    2026-08-26 14:10~ 모의투자 게이트웨이(openapivts:29443)가 통째로
    read-timeout 을 냈다. 실전 게이트웨이는 멀쩡했으니 우리 문제가 아니었다.
    """
    _buy(env)
    kis = RecordingKIS([_holding()], balance_raises=True)
    res = lt.reserve_ladder_exits(DAY, client=kis)

    assert res["status"] == "balance_unavailable"
    assert res["reserved"] == []
    assert not any(c[0] == "order" for c in kis.calls)


def test_trail_holds_when_the_balance_cannot_be_read(env, bars):
    """시세를 못 읽었을 때 청산하지 않는 것과 같은 이유다.

    5분마다 도는 태스크라 예외를 던지면 하루 78번의 트레이스백이 쌓이고,
    정작 "오늘 트레일 보호가 없었다"는 사실이 그 안에 묻힌다.
    """
    _buy(env)
    kis = RecordingKIS([_holding()], balance_raises=True)
    kis.quote = {"price": 1.0}          # 읽혔다면 즉시 청산됐을 값
    res = lt.watch_trailing_exits(DAY, client=kis)

    assert res["status"] == "balance_unavailable"
    assert res["exits"] == []
    assert not any(c[0] in ("order", "cancel") for c in kis.calls)


def test_all_three_tasks_degrade_the_same_way(env, bars):
    """세 태스크의 잔고 실패 처리가 같은 모양이어야 한다.

    하나만 예외를 던지면 그 태스크만 다르게 실패해, 장애 로그를 읽는 사람이
    '이건 왜 여기만 터지지'를 매번 다시 판단해야 한다.
    """
    from app.api.services import balance_reconcile as BR

    _buy(env)
    statuses = set()
    for fn in (lt.reserve_ladder_exits, lt.watch_trailing_exits,
               BR.reconcile_by_balance):
        kis = RecordingKIS([_holding()], balance_raises=True)
        kis.quote = {}
        statuses.add(fn(DAY, client=kis)["status"])
    assert statuses == {"balance_unavailable"}
