"""보유 종목 → 매수 전략 귀속. 계약을 고정한다.

2026-08-21: "현재 보유 종목" 표에 qlib(open)이 산 것인지 카페(cafereal)가 산
것인지 구분자가 없었다. KIS 잔고에는 그 정보가 없고 orders.strategy 에는 있는데,
둘이 한 번도 조인된 적이 없었다.

여기서 막는 사고:
  * **한 번도 산 적 없는 종목을 "보유"라고 주장하는 것** — cafereal 주문은
    정산기가 없어 영구히 SUBMITTED 다. 이걸 체결로 세면 유령 보유가 생긴다.
  * 배지 수량의 합이 표의 수량 컬럼과 어긋나는 것 — 같은 행이 두 숫자를 말한다.
  * 시뮬 전략(cafe/close/…)이 실계좌 보유를 주장하는 것 — 계좌가 섞인다.
  * 원장이 잔고를 초과했는데 조용히 깎여 사라지는 것 — 그 불일치가 곧 신호다.
  * 귀속 코드가 qlib 을 끌어들여 30초 폴링 경로를 무겁게 만드는 것.
"""

import sys

import pytest

pytest.importorskip("sqlalchemy")

from app.api.db import ACCOUNT_STRATEGIES, STRATEGY_CAFEREAL, STRATEGY_OPEN
from app.api.services import holding_attribution as ha
from app.api.services.holding_attribution import (
    MANUAL, STATUS_CONFIRMED, STATUS_INFERRED, STATUS_MISMATCH,
    LedgerEvent, allocate, primary_strategy, replay_net_qty,
)


def _net(**kw):
    """전략 하나짜리 nets 맵을 만든다."""
    return {s: replay_net_qty(evs) for s, evs in kw.items()}


def _buy(strategy, qty, status="FILLED", fill_qty=None):
    return LedgerEvent(strategy=strategy, side="BUY", qty=qty, status=status,
                       fill_qty=fill_qty)


def _sell(strategy, qty, status="FILLED", fill_qty=None):
    return LedgerEvent(strategy=strategy, side="SELL", qty=qty, status=status,
                       fill_qty=fill_qty)


def _by_strategy(claims):
    return {c.strategy: c.qty for c in claims}


# ─── 배분 로직 ──────────────────────────────────────────────────────


def test_single_strategy_exact_match():
    claims, status = allocate(10, _net(open=[_buy("open", 10)]))
    assert _by_strategy(claims) == {"open": 10}
    assert status == STATUS_CONFIRMED
    assert claims[0].confirmed is True


def test_ledger_silent_means_manual():
    # 원장에 아무것도 없는데 잔고에 있다 = 수동 매매거나 대체입고.
    claims, status = allocate(7, {})
    assert _by_strategy(claims) == {MANUAL: 7}
    assert status == STATUS_INFERRED


def test_partial_ledger_leaves_manual_residual():
    claims, status = allocate(8, _net(open=[_buy("open", 5)]))
    assert _by_strategy(claims) == {"open": 5, MANUAL: 3}
    assert status == STATUS_INFERRED
    # manual 은 언제나 마지막 — 화면에서 실제 전략 뒤에 와야 읽힌다.
    assert claims[-1].strategy == MANUAL


def test_unconfirmed_is_shrunk_before_confirmed():
    """오차는 균등하지 않고 미확정 행에 몰려 있다 — 거기부터 깎아야 한다."""
    nets = _net(open=[_buy("open", 5)],
                cafereal=[_buy("cafereal", 10, status="SUBMITTED")])
    claims, status = allocate(6, nets)
    assert _by_strategy(claims) == {"open": 5, "cafereal": 1}
    # 확정분은 온전히 남았으므로 mismatch 가 아니라 추정이다.
    assert status == STATUS_INFERRED


def test_confirmed_exceeding_balance_is_a_mismatch():
    claims, status = allocate(6, _net(open=[_buy("open", 10)]))
    assert _by_strategy(claims) == {"open": 6}
    assert status == STATUS_MISMATCH
    # 원장이 뭐라고 주장했는지는 진단을 위해 남는다.
    assert claims[0].ledger_qty == 10


@pytest.mark.parametrize("kis_qty", [1, 3, 7, 10, 33, 97, 222])
@pytest.mark.parametrize("a,b", [(1, 1), (3, 7), (10, 1), (5, 5), (2, 9)])
def test_claims_always_sum_to_the_balance(kis_qty, a, b):
    """배지 수량의 합 == 표의 수량. 어긋나면 한 행이 두 숫자를 말하게 된다."""
    nets = _net(open=[_buy("open", a)],
                cafereal=[_buy("cafereal", b, status="SUBMITTED")])
    claims, _ = allocate(kis_qty, nets)
    assert sum(c.qty for c in claims) == kis_qty
    assert all(c.qty > 0 for c in claims)      # 0주짜리 배지는 소음이다


def test_sell_exceeding_buys_is_flagged_not_hidden():
    # open 전략의 매도는 KIS 잔고 전량이라 원장 매수분을 넘길 수 있다.
    nets = _net(open=[_buy("open", 10), _sell("open", 15)])
    claims, status = allocate(3, nets)
    assert status == STATUS_MISMATCH
    assert _by_strategy(claims) == {MANUAL: 3}


def test_zero_balance_yields_no_claims():
    """유령 원장이 있어도 보유 0이면 아무것도 주장하지 않는다."""
    nets = _net(cafereal=[_buy("cafereal", 100, status="SUBMITTED")])
    claims, status = allocate(0, nets)
    assert claims == []
    assert status == STATUS_MISMATCH


# ─── 원장 재생 ──────────────────────────────────────────────────────


def test_submitted_is_unconfirmed_not_owned():
    """cafereal 회귀.

    cafereal 주문은 −3% 지정가라 대개 체결되지 않고, reconcile_fills 가
    cafereal 을 정산하지 않아 영구히 SUBMITTED 다. SUBMITTED 를 체결로 세면
    한 번도 산 적 없는 종목이 "카페실계좌 100주"로 화면에 뜬다.
    """
    net = replay_net_qty([_buy("cafereal", 100, status="SUBMITTED")])
    assert net.confirmed == 0
    assert net.unconfirmed == 100
    # 잔고가 0이면 청구가 하나도 나오면 안 된다.
    assert allocate(0, {"cafereal": net})[0] == []


def test_rejected_cancelled_pending_never_count():
    for status in ("REJECTED", "CANCELLED", "PENDING"):
        net = replay_net_qty([_buy("open", 50, status=status)])
        assert net.total == 0, status


def test_fill_quantity_wins_over_order_quantity():
    """정산기가 실제 체결수량을 적어두면 그쪽이 진실이다.

    지금은 Fill.qty 가 Order.qty 의 복사본이지만, 그게 고쳐지는 날
    귀속이 저절로 정확해져야 한다.
    """
    net = replay_net_qty([_buy("open", 100, status="PARTIAL", fill_qty=30)])
    assert net.unconfirmed == 30


def test_unconfirmed_sell_eats_into_confirmed_buys():
    # 확정 매수 10주를 미확정 매도 4주로 팔았다 → 남은 건 확정 6주.
    net = replay_net_qty([_buy("open", 10),
                          _sell("open", 4, status="SUBMITTED")])
    assert net.confirmed == 6
    assert net.unconfirmed == 0
    assert net.went_negative is False


def test_attribution_does_not_import_qlib():
    """/balance 는 30초 폴링 경로다. 귀속이 qlib 을 끌어들이면 안 된다."""
    before = "qlib" in sys.modules
    replay_net_qty([_buy("open", 1)])
    allocate(1, _net(open=[_buy("open", 1)]))
    assert ("qlib" in sys.modules) == before


# ─── 계좌 축 ────────────────────────────────────────────────────────


def test_primary_strategy_matches_account_map():
    assert primary_strategy("main") == STRATEGY_OPEN
    assert primary_strategy("cafe") == STRATEGY_CAFEREAL
    # 미등록 계좌는 터지지 않고 기본 계좌로 물러난다.
    assert primary_strategy("nope") == STRATEGY_OPEN


def test_balance_cache_fallback_derives_from_the_same_map():
    """계좌↔전략 매핑의 사본이 두 벌이 되면 반드시 갈라진다."""
    from app.api.services import balance_cache as bc

    assert not hasattr(bc, "_FALLBACK_STRATEGY")
    for account, strategies in ACCOUNT_STRATEGIES.items():
        assert primary_strategy(account) == strategies[0]


def test_cafe_account_never_sees_the_open_ledger():
    assert STRATEGY_OPEN not in ha.account_strategies("cafe")
    assert STRATEGY_CAFEREAL not in ha.account_strategies("main")


def test_simulated_strategies_belong_to_no_account():
    """시뮬 포지션은 브로커가 아니라 Fill 장부에만 있다."""
    owned = {s for strategies in ACCOUNT_STRATEGIES.values() for s in strategies}
    for sim in ("close", "flow", "trail", "scale", "limit", "cafe", "surge",
                "cafeopen", "cafecool"):
        assert sim not in owned


def test_unknown_account_attributes_everything_to_manual():
    out = ha.attribute_holdings({"005930": 12}, "nosuch")
    assert _by_strategy(out["005930"].claims) == {MANUAL: 12}
    assert out["005930"].status == STATUS_INFERRED


def test_empty_holdings_never_touch_the_database(monkeypatch):
    def _boom(*a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError("보유 종목이 없으면 DB 를 열면 안 된다")

    monkeypatch.setattr(ha, "SessionLocal", _boom)
    assert ha.attribute_holdings({}, "main") == {}


def test_ledger_failure_degrades_to_no_badges(monkeypatch):
    """원장 조회가 실패해도 잔고 화면은 살아 있어야 한다."""
    def _boom(*a, **kw):  # noqa: ANN002, ANN003
        raise RuntimeError("db down")

    monkeypatch.setattr(ha, "_events_by_code", _boom)
    assert ha.attribute_holdings({"005930": 5}, "main") == {}


# ─── 원장 조회 (DB) ─────────────────────────────────────────────────


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def _order(session, *, code, strategy, side="BUY", qty=10, status="FILLED",
           fill_qty=None, day=None):
    import datetime as _dt

    from app.api.db import Fill, Order

    o = Order(trade_date=day or _dt.date(2026, 8, 20), strategy=strategy,
              code=code, name=code, side=side, qty=qty, price=1000.0,
              ord_dvsn="00", status=status)
    session.add(o)
    session.flush()
    if fill_qty is not None:
        session.add(Fill(order_id=o.id, strategy=strategy, qty=fill_qty,
                         price=1000.0))
    session.commit()
    return o


def test_cafe_account_ignores_the_open_ledger_for_the_same_code(session):
    """같은 종목을 두 계좌가 들고 있어도 장부가 섞이면 안 된다."""
    _order(session, code="000720", strategy=STRATEGY_OPEN, qty=9)
    _order(session, code="000720", strategy=STRATEGY_CAFEREAL, qty=4,
           status="SUBMITTED")

    main = ha.attribute_holdings({"000720": 9}, "main", db=session)
    cafe = ha.attribute_holdings({"000720": 4}, "cafe", db=session)

    assert _by_strategy(main["000720"].claims) == {STRATEGY_OPEN: 9}
    assert main["000720"].status == STATUS_CONFIRMED
    assert _by_strategy(cafe["000720"].claims) == {STRATEGY_CAFEREAL: 4}
    # cafereal 은 정산기가 없어 SUBMITTED 로 남는다 → 확정이 아니라 추정이다.
    assert cafe["000720"].status == STATUS_INFERRED
    assert cafe["000720"].claims[0].confirmed is False


def test_fill_rows_refine_the_quantity(session):
    _order(session, code="097230", strategy=STRATEGY_OPEN, qty=100,
           status="PARTIAL", fill_qty=59)
    out = ha.attribute_holdings({"097230": 59}, "main", db=session)
    assert _by_strategy(out["097230"].claims) == {STRATEGY_OPEN: 59}


def test_sells_reduce_the_attributed_position(session):
    _order(session, code="210980", strategy=STRATEGY_OPEN, qty=300)
    _order(session, code="210980", strategy=STRATEGY_OPEN, side="SELL", qty=78)
    out = ha.attribute_holdings({"210980": 222}, "main", db=session)
    assert _by_strategy(out["210980"].claims) == {STRATEGY_OPEN: 222}
    assert out["210980"].status == STATUS_CONFIRMED


def test_long_history_is_not_truncated(session):
    """누적 재생은 잘라내면 틀린다 — _position_timeline 의 limit(200) 회귀."""
    for _ in range(150):
        _order(session, code="001450", strategy=STRATEGY_OPEN, qty=2)
        _order(session, code="001450", strategy=STRATEGY_OPEN, side="SELL", qty=1)
    out = ha.attribute_holdings({"001450": 150}, "main", db=session)
    assert _by_strategy(out["001450"].claims) == {STRATEGY_OPEN: 150}


# ─── /live 라우터 계약 ──────────────────────────────────────────────


@pytest.fixture
def live_api(monkeypatch):
    pytest.importorskip("fastapi")
    from app.api.routers import live as live_router
    return live_router


class _Snap:
    def __init__(self, holdings):
        self.cash = 1_000_000.0
        self.total_eval = 5_240_000.0
        self.holdings = holdings


class _H:
    def __init__(self, code, qty):
        self.code, self.qty = code, qty
        self.name = code
        self.avg_price = self.eval_price = self.eval_value = 1000.0
        self.pnl = self.pnl_pct = 0.0


def test_balance_keeps_every_pre_existing_field(live_api, monkeypatch):
    import datetime as _dt

    monkeypatch.setattr(live_api, "get_balance_for_read",
                        lambda a="main": (_Snap([_H("000720", 9)]), "live",
                                          _dt.datetime(2026, 8, 21, 9, 0)))
    monkeypatch.setattr(live_api, "attribute_holdings", lambda *a, **k: {})
    res = live_api.get_balance(account="main")
    h = res.holdings[0]
    for field in ("code", "name", "qty", "avg_price", "eval_price",
                  "eval_value", "pnl", "pnl_pct"):
        assert hasattr(h, field)
    assert res.cash == 1_000_000.0 and res.total_eval == 5_240_000.0


def test_balance_survives_a_broken_ledger(live_api, monkeypatch):
    """귀속이 터져도 잔고 카드는 떠야 한다 — 이 라우트는 500 을 내면 안 된다."""
    import datetime as _dt

    def _boom(*a, **k):
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr(live_api, "get_balance_for_read",
                        lambda a="main": (_Snap([_H("000720", 9)]), "live",
                                          _dt.datetime(2026, 8, 21, 9, 0)))
    monkeypatch.setattr(live_api, "attribute_holdings", _boom)
    res = live_api.get_balance(account="main")
    assert res.holdings[0].strategies == []
    assert res.holdings[0].attribution == "unknown"


def test_unconfigured_account_never_queries_the_ledger(live_api, monkeypatch):
    import datetime as _dt

    called = []
    monkeypatch.setattr(live_api, "get_balance_for_read",
                        lambda a="cafe": (_Snap([]), "no_account",
                                          _dt.datetime(2026, 8, 21, 9, 0)))
    monkeypatch.setattr(live_api, "attribute_holdings",
                        lambda *a, **k: called.append(a) or {})
    res = live_api.get_balance(account="cafe")
    assert called == []
    assert res.holdings == []


def _spy_timeline(live_api, monkeypatch) -> dict:
    """_position_timeline / _kis_holding_prices 가 어떤 원장·계좌로 불렸는지 기록."""
    seen: dict = {}

    def timeline(code, strategy, **kw):
        seen["strategy"] = strategy
        return []

    def prices(code, account=None):
        seen["account"] = account
        seen.setdefault("price_calls", []).append(code)
        return None, None

    monkeypatch.setattr(live_api, "_position_timeline", timeline)
    monkeypatch.setattr(live_api, "_kis_holding_prices", prices)
    return seen


def test_trade_history_follows_the_account(live_api, monkeypatch):
    """카페 계좌 종목을 펼치면 cafereal 원장을 봐야 한다.

    이 인자가 없던 시절에는 서버 기본값 "open" 을 보고 늘 빈 목록이 떴다.
    """
    seen = _spy_timeline(live_api, monkeypatch)
    live_api.get_stock_trades("000720", strategy=None, account="cafe")
    assert seen["strategy"] == STRATEGY_CAFEREAL
    assert seen["account"] == "cafe"


def test_trade_history_default_is_unchanged(live_api, monkeypatch):
    seen = _spy_timeline(live_api, monkeypatch)
    live_api.get_stock_trades("000720", strategy=None, account=None)
    assert seen["strategy"] == STRATEGY_OPEN
    assert seen["account"] == "main"      # open 은 main 계좌 소속


def test_explicit_strategy_wins_over_account(live_api, monkeypatch):
    seen = _spy_timeline(live_api, monkeypatch)
    live_api.get_stock_trades("000720", strategy="cafe", account="main")
    assert seen["strategy"] == "cafe"


def test_simulated_ledger_never_asks_a_broker_for_prices(live_api, monkeypatch):
    """시뮬 포지션은 어떤 브로커에도 없다 — 잔고를 부르면 엉뚱한 계좌를 읽는다."""
    seen = _spy_timeline(live_api, monkeypatch)
    live_api.get_stock_trades("000720", strategy="cafecool", account=None)
    assert "price_calls" not in seen
