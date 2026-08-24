"""Per-account order policy: what actually reaches KIS.

The real-money path had `place_order(..., price=None)` hard-coded on both
sides, which `place_order` turns into ORD_DVSN="01" — a market order. The
written policy said the opposite ("매수는 시장가 금지 — 기준가 −3% 지정가"),
and the −3% logic that did exist lived only inside simulated curves that never
call KIS. These tests pin the two halves that matter:

  1. An account with no policy, or a policy of `market`, submits EXACTLY what
     it submitted before. Adding this feature must not move the live account.
  2. An account set to `limit` sends a tick-aligned limit price, and the same
     price is what sizing, affordability and 주문가능현금 are computed from —
     the three places that previously used the signal's last close and would
     otherwise disagree with the order.
"""

from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Order, TradingAccount  # noqa: E402
from app.api.services import account_policy as ap  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402

DAY = date(2026, 8, 18)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


class FakeClient:
    """Just enough KISClient for the policy layer."""

    is_mock = False

    def __init__(self, quote=None):
        self._quote = quote or {"open": 10_000.0, "price": 10_500.0}
        self.quote_calls = 0

    def get_quote(self, code):
        self.quote_calls += 1
        return dict(self._quote)


def _account(session, **kw):
    row = TradingAccount(account_id=kw.pop("account_id", "main"), **kw)
    session.add(row)
    session.commit()
    return row


# ─── policy resolution ──────────────────────────────────────────────


def test_missing_account_row_is_market_on_both_sides(session):
    buy, sell = ap.get_policies(session, "nobody")

    assert (buy.is_limit, sell.is_limit) == (False, False)
    assert ap.order_price(FakeClient(), "005930", buy, DAY) is None
    assert ap.order_price(FakeClient(), "005930", sell, DAY) is None


def test_seeded_main_account_keeps_the_market_path(session):
    _account(session, buy_ord_type="market", sell_ord_type="market")

    buy, sell = ap.get_policies(session, "main")

    assert ap.order_price(FakeClient(), "005930", buy, DAY) is None
    assert ap.order_price(FakeClient(), "005930", sell, DAY) is None


# ─── limit pricing ──────────────────────────────────────────────────


def test_prev_close_base_discounts_and_snaps_to_the_tick(session, monkeypatch):
    _account(session, buy_ord_type="limit", buy_base="prev_close", buy_offset_pct=0.03)
    monkeypatch.setattr(lt, "_prev_close_before", lambda code, day: 30_800.0)

    buy, _ = ap.get_policies(session, "main")
    px = ap.order_price(FakeClient(), "005930", buy, DAY)

    # 30,800 × 0.97 = 29,876 → 20,000~50,000 구간은 50원 호가 → 29,850
    assert px == 29_850.0


def test_open_base_uses_todays_opening_print(session):
    _account(session, buy_ord_type="limit", buy_base="open", buy_offset_pct=0.03)
    client = FakeClient({"open": 10_000.0, "price": 99_999.0})

    buy, _ = ap.get_policies(session, "main")

    assert ap.order_price(client, "005930", buy, DAY) == 9_700.0


def test_quote_base_uses_the_current_price(session):
    _account(session, buy_ord_type="limit", buy_base="quote", buy_offset_pct=0.03)
    client = FakeClient({"open": 99_999.0, "price": 10_000.0})

    buy, _ = ap.get_policies(session, "main")

    assert ap.order_price(client, "005930", buy, DAY) == 9_700.0


def test_a_supplied_quote_is_not_refetched(session):
    _account(session, buy_ord_type="limit", buy_base="open", buy_offset_pct=0.03)
    client = FakeClient()
    buy, _ = ap.get_policies(session, "main")

    ap.order_price(client, "005930", buy, DAY, quote={"open": 20_000.0})

    assert client.quote_calls == 0, "이미 받아 둔 호가를 다시 조회하면 안 된다"


def test_sell_premium_goes_the_other_way(session, monkeypatch):
    _account(session,
             buy_ord_type="limit", buy_base="prev_close", buy_offset_pct=0.03,
             sell_ord_type="limit", sell_base="prev_close", sell_offset_pct=0.03)
    monkeypatch.setattr(lt, "_prev_close_before", lambda code, day: 10_000.0)

    buy, sell = ap.get_policies(session, "main")

    assert ap.order_price(FakeClient(), "005930", buy, DAY) == 9_700.0
    assert ap.order_price(FakeClient(), "005930", sell, DAY) == 10_300.0


def test_missing_base_price_raises_rather_than_falling_back_to_market(session, monkeypatch):
    _account(session, buy_ord_type="limit", buy_base="prev_close", buy_offset_pct=0.03)
    monkeypatch.setattr(lt, "_prev_close_before", lambda code, day: None)

    buy, _ = ap.get_policies(session, "main")

    with pytest.raises(ap.BasePriceUnavailable):
        ap.order_price(FakeClient(), "005930", buy, DAY)


# ─── the buy path agrees with itself ────────────────────────────────


def test_affordability_and_sizing_use_the_limit_price_not_the_last_close():
    """A 1,030,000원 stock is unaffordable at a 1,000,000원 slot — but its
    −3% limit (999,000) is not. The filter must judge the price we will
    actually order at, or affordability, qty and the order disagree."""
    selected, skipped = lt._select_affordable_buys(
        ["EXPENSIVE"], slot_budget=1_000_000.0, n_drop=2,
        price_fn=lambda code: 999_000.0,     # the limit price
        risk_fn=lambda code: None,
    )
    assert selected == [("EXPENSIVE", 999_000.0)]
    assert skipped == []

    selected, skipped = lt._select_affordable_buys(
        ["EXPENSIVE"], slot_budget=1_000_000.0, n_drop=2,
        price_fn=lambda code: 1_030_000.0,   # the last close
        risk_fn=lambda code: None,
    )
    assert selected == []
    assert skipped == ["EXPENSIVE"]


def test_unpriceable_candidate_is_skipped_and_the_next_rank_takes_the_slot():
    def _price(code):
        return None if code == "BROKEN" else 10_000.0

    selected, _ = lt._select_affordable_buys(
        ["BROKEN", "FINE"], slot_budget=1_000_000.0, n_drop=1,
        price_fn=_price, risk_fn=lambda code: None,
    )

    assert selected == [("FINE", 10_000.0)]


def test_risk_check_reuses_a_quote_instead_of_paying_for_a_second(monkeypatch):
    """_is_risky costs a gated quote. Under a limit policy the entry price
    already fetched one; charging for it twice doubles the order run."""
    client = FakeClient({"halted": False, "status_code": ""})
    slept = []
    monkeypatch.setattr(lt.time, "sleep", lambda s: slept.append(s))

    assert lt._is_risky("005930", client, quote={"halted": False, "status_code": ""}) is None
    assert client.quote_calls == 0
    assert slept == [], "재사용한 호가에 스로틀 대기를 걸 이유가 없다"

    assert lt._is_risky("005930", client) is None
    assert client.quote_calls == 1


def test_risk_flags_still_block_from_a_reused_quote():
    client = FakeClient()

    assert lt._is_risky("005930", client, quote={"halted": True}) == "거래정지"
    assert lt._is_risky("005930", client,
                        quote={"halted": False, "status_code": "51"}) == "관리종목"


# ─── unfilled-limit cancel sweep ────────────────────────────────────


def _order(session, **kw):
    fields = dict(
        trade_date=DAY, strategy="open", code="005930", side="BUY",
        qty=10, price=9_700.0, ord_dvsn="00", status="SUBMITTED",
        kis_order_id="ODNO1",
        raw_response='{"output": {"KRX_FWDG_ORD_ORGNO": "91252", "ODNO": "ODNO1"}}',
    )
    fields.update(kw)
    o = Order(**fields)
    session.add(o)
    session.commit()
    return o


class CancelClient(FakeClient):
    def __init__(self, ok=True, error=None):
        super().__init__()
        self.calls = []
        self._ok = ok
        self._error = error

    def cancel_order(self, **kw):
        self.calls.append(kw)
        from app.api.services.kis_client import OrderResult
        return OrderResult(ok=self._ok, order_id=kw.get("orgn_odno"), code=kw["code"],
                           side=kw["side"], qty=kw["qty"], price=None, raw={},
                           error=self._error)


@pytest.fixture
def sweep_env(session, monkeypatch):
    """Point the sweep at the in-memory session and skip schema/throttle work."""
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(lt.time, "sleep", lambda s: None)
    return session


class _NoCloseSession:
    """Hands the test's session to the code under test without closing it."""

    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


def test_no_cutoff_means_the_sweep_does_nothing(sweep_env):
    _account(sweep_env, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm=None)
    _order(sweep_env)
    client = CancelClient()

    res = lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 15, 0),
                                    client=client, account_id="main")

    assert res["status"] == "no_cutoff"
    assert client.calls == []


def test_before_the_cutoff_nothing_is_cancelled(sweep_env):
    _account(sweep_env, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:20")
    o = _order(sweep_env)
    client = CancelClient()

    res = lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 11, 0),
                                    client=client, account_id="main")

    assert res["cancelled"] == 0
    assert client.calls == []
    assert o.status == "SUBMITTED"


def test_past_the_cutoff_the_order_is_cancelled(sweep_env):
    _account(sweep_env, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:20")
    o = _order(sweep_env)
    client = CancelClient()

    res = lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 15, 20),
                                    client=client, account_id="main")

    assert res["cancelled"] == 1
    assert o.status == "CANCELLED"
    # The original order's identifiers must survive into the cancel request.
    assert client.calls[0]["org_no"] == "91252"
    assert client.calls[0]["orgn_odno"] == "ODNO1"


def test_market_orders_are_never_swept(sweep_env):
    _account(sweep_env, buy_ord_type="market", buy_cancel_hhmm="15:20")
    o = _order(sweep_env, ord_dvsn="01")
    client = CancelClient()

    lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 15, 30),
                              client=client, account_id="main")

    assert client.calls == []
    assert o.status == "SUBMITTED"


def test_partially_filled_orders_are_left_for_a_human(sweep_env):
    """Flipping a PARTIAL row to CANCELLED would drop its real fills out of
    the pnl queries, which select on status in (FILLED, PARTIAL)."""
    _account(sweep_env, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:20")
    o = _order(sweep_env, status="PARTIAL")
    client = CancelClient()

    res = lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 15, 30),
                                    client=client, account_id="main")

    assert client.calls == []
    assert o.status == "PARTIAL"
    assert res["cancelled"] == 0


def test_a_failed_cancel_leaves_the_row_open_and_records_why(sweep_env):
    _account(sweep_env, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:20")
    o = _order(sweep_env)
    client = CancelClient(ok=False, error="1 이미 체결된 주문입니다")

    res = lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 15, 30),
                                    client=client, account_id="main")

    assert res["cancelled"] == 0 and res["failed"] == 1
    assert o.status == "SUBMITTED", "취소 실패를 취소 성공으로 기록하면 안 된다"
    assert "취소 실패" in (o.error or "")


def test_sell_side_uses_its_own_cutoff(sweep_env):
    _account(sweep_env,
             buy_ord_type="limit", buy_base="prev_close", buy_offset_pct=0.03,
             buy_cancel_hhmm="15:20",
             sell_ord_type="limit", sell_base="prev_close", sell_offset_pct=0.03,
             sell_cancel_hhmm="10:00")
    buy_order = _order(sweep_env)
    sell_order = _order(sweep_env, side="SELL")
    client = CancelClient()

    lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 10, 30),
                              client=client, account_id="main")

    assert sell_order.status == "CANCELLED"
    assert buy_order.status == "SUBMITTED", "매수 컷오프(15:20)는 아직 지나지 않았다"


@pytest.mark.parametrize("value,expected", [
    ("15:20", (15, 20)),
    ("09:00", (9, 0)),
    (None, None),
    ("", None),
    ("nope", None),
    ("25:00", None),
    ("12:75", None),
])
def test_cutoff_parsing(value, expected):
    assert lt._parse_hhmm(value) == expected


# ─── PUT /live/accounts validation ──────────────────────────────────
#
# Called directly rather than through TestClient, matching the rest of
# tests/app. What matters here is that a bad policy is refused BEFORE it
# reaches the row the 09:00 run reads.


@pytest.fixture
def api(monkeypatch, session):
    from app.api.routers import live as live_router
    monkeypatch.setattr(live_router, "SessionLocal", lambda: _NoCloseSession(session))
    return live_router


def _side(api, **kw):
    return api.AccountSidePolicy(**kw)


def test_rejects_an_unknown_order_type(api):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        api._validate_side(_side(api, ord_type="지정가"), "매수")
    assert e.value.status_code == 422


def test_limit_without_a_base_is_rejected(api):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        api._validate_side(_side(api, ord_type="limit", base=None, offset_pct=0.03), "매수")
    assert "기준가" in e.value.detail


def test_an_absurd_offset_is_rejected(api):
    """0.3 typed as 30 would order 30x below the market — far likelier a typo
    than an intent, and it spends real money."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        api._validate_side(
            _side(api, ord_type="limit", base="prev_close", offset_pct=30.0), "매수")


def test_a_malformed_cutoff_is_rejected(api):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        api._validate_side(
            _side(api, ord_type="limit", base="prev_close", offset_pct=0.03,
                  cancel_hhmm="3시20분"), "매수")
    assert "HH:MM" in e.value.detail


def test_market_side_skips_the_limit_only_checks(api):
    # No base, no offset, no cutoff — all fine while the side is market.
    api._validate_side(_side(api, ord_type="market"), "매도")


def test_update_persists_the_policy_the_order_path_will_read(api, session):
    _account(session, buy_ord_type="market", sell_ord_type="market")

    out = api.update_account("main", api.AccountPolicyUpdate(
        buy=_side(api, ord_type="limit", base="prev_close", offset_pct=0.03,
                  cancel_hhmm="15:20"),
        sell=_side(api, ord_type="market"),
    ))

    assert out.buy.ord_type == "limit" and out.buy.offset_pct == 0.03
    buy, sell = ap.get_policies(session, "main")
    assert buy.is_limit and buy.base == "prev_close" and buy.cancel_hhmm == "15:20"
    assert not sell.is_limit


def test_update_of_an_unknown_account_is_404(api):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        api.update_account("ghost", api.AccountPolicyUpdate(
            buy=_side(api, ord_type="market"), sell=_side(api, ord_type="market")))
    assert e.value.status_code == 404


# ─── submit_daily_orders end to end ─────────────────────────────────
#
# The pieces are covered above; this pins the whole 09:00 path, because the
# regression being guarded against is precisely that the parts were each fine
# while the call site still passed price=None.


class RecordingClient(FakeClient):
    def __init__(self, cash=10_000_000.0, holdings=(), quote=None):
        super().__init__(quote)
        self._cash = cash
        self._holdings = list(holdings)
        self.orders = []
        self.psbl_calls = []

    def get_balance(self):
        from app.api.services.kis_client import AccountSnapshot
        return AccountSnapshot(cash=self._cash, total_eval=self._cash,
                               holdings=list(self._holdings))

    def get_orderable_cash(self, code, price=None):
        self.psbl_calls.append(price)
        return {"cash": self._cash}

    def place_order(self, code, side, qty, price=None):
        from app.api.services.kis_client import OrderResult
        self.orders.append({"code": code, "side": side, "qty": qty, "price": price})
        return OrderResult(ok=True, order_id=f"ODNO{len(self.orders)}", code=code,
                           side=side, qty=qty, price=price, raw={"output": {"ODNO": "1"}},
                           error=None)


@pytest.fixture
def orders_env(session, monkeypatch):
    from app.api.db import Signal
    for rank, code in enumerate(["000001", "000002"], start=1):
        session.add(Signal(as_of=DAY, rank=rank, code=code, score=1.0 / rank,
                           model_class="LGBModel", strategy_class="TopkDropoutStrategy"))
    session.commit()

    monkeypatch.setattr(lt, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "_last_trading_day", lambda today=None: DAY)
    monkeypatch.setattr(lt, "_next_trading_day", lambda d: DAY)
    monkeypatch.setattr(lt, "_stock_name", lambda code: f"name-{code}")
    monkeypatch.setattr(lt, "_last_close", lambda code: 10_000.0)
    monkeypatch.setattr(lt, "_prev_close_before", lambda code, d: 10_000.0)
    monkeypatch.setattr(lt, "_buy_reasons", lambda *a, **kw: None)
    monkeypatch.setattr(lt, "_sell_reasons", lambda *a, **kw: None)
    monkeypatch.setattr(lt, "_is_risky", lambda code, client=None, quote=None: None)
    monkeypatch.setattr(lt.time, "sleep", lambda s: None)
    return session


def test_market_account_still_submits_price_none(orders_env, session):
    """The no-change guarantee for the account that is live today."""
    _account(session, buy_ord_type="market", sell_ord_type="market")
    client = RecordingClient()

    lt.submit_daily_orders(client=client, strategy="open", simulated=False)

    assert client.orders, "주문이 하나도 나가지 않았다"
    assert all(o["price"] is None for o in client.orders)
    assert client.psbl_calls == [None]
    saved = session.query(Order).all()
    assert all(o.ord_dvsn == "01" for o in saved)


def test_limit_account_submits_the_discounted_price(orders_env, session):
    _account(session, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, sell_ord_type="market")
    client = RecordingClient()

    lt.submit_daily_orders(client=client, strategy="open", simulated=False)

    buys = [o for o in client.orders if o["side"] == "BUY"]
    assert buys, "매수 주문이 나가지 않았다"
    # 10,000 × 0.97 = 9,700 (10,000원 미만 구간 10원 호가라 그대로)
    assert all(o["price"] == 9_700.0 for o in buys)
    # 주문가능현금도 같은 지정가 기준으로 물어야 한다.
    assert client.psbl_calls == [9_700.0]
    # 수량은 마지막 종가(10,000)가 아니라 지정가(9,700) 기준으로 나눠야 한다.
    # 슬롯 예산 1,000,000 ÷ 9,700 = 103주 (종가 기준이면 100주였다).
    slot = 10_000_000.0 / lt.LIVE_CONFIG["strategy_kwargs"]["topk"]
    assert all(o["qty"] == int(slot // 9_700) for o in buys)
    assert buys[0]["qty"] != int(slot // 10_000), "종가 기준 수량이 남아 있다"
    saved = session.query(Order).filter(Order.side == "BUY").all()
    assert all(o.ord_dvsn == "00" and o.price == 9_700.0 for o in saved)
    assert all(o.kind == "limit" for o in saved)


def test_limit_sell_prices_above_the_base(orders_env, session):
    from app.api.services.kis_client import Holding
    _account(session, buy_ord_type="market",
             sell_ord_type="limit", sell_base="prev_close", sell_offset_pct=0.03)
    # A holding that is not in today's top-K, so it gets sold.
    held = Holding(code="999999", name="old", qty=5, avg_price=9_000.0,
                   eval_price=10_000.0, eval_value=50_000.0, pnl=0.0, pnl_pct=0.0,
                   sellable_qty=5)
    client = RecordingClient(holdings=[held])

    lt.submit_daily_orders(client=client, strategy="open", simulated=False)

    sells = [o for o in client.orders if o["side"] == "SELL"]
    assert sells, "매도 주문이 나가지 않았다"
    assert sells[0]["price"] == 10_300.0, "매도는 기준가 위로 붙어야 한다"


def test_a_candidate_whose_base_price_is_missing_is_skipped_not_market_bought(
        orders_env, session, monkeypatch):
    """The failure mode worth being loud about: a limit policy must never
    quietly become a market order."""
    _account(session, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, sell_ord_type="market")
    monkeypatch.setattr(lt, "_prev_close_before",
                        lambda code, d: None if code == "000001" else 10_000.0)
    client = RecordingClient()

    lt.submit_daily_orders(client=client, strategy="open", simulated=False)

    codes = [o["code"] for o in client.orders if o["side"] == "BUY"]
    assert "000001" not in codes
    assert codes == ["000002"]
    assert all(o["price"] is not None for o in client.orders if o["side"] == "BUY")


def test_simulated_curves_ignore_the_account_policy(orders_env, session, monkeypatch):
    """The eight comparison curves must keep filling the way they always did —
    the policy governs the real broker path only."""
    _account(session, buy_ord_type="limit", buy_base="prev_close", buy_offset_pct=0.03)
    monkeypatch.setattr(lt, "_sim_fill_price", lambda code: 10_000.0)
    monkeypatch.setattr(lt, "_simulated_balance",
                        lambda db, strategy=None, **kw: RecordingClient().get_balance())
    client = RecordingClient()

    lt.submit_daily_orders(client=client, strategy="close", simulated=True)

    assert client.orders == [], "시뮬 곡선이 KIS로 주문을 보내면 안 된다"
    sim = session.query(Order).filter(Order.strategy == "close").all()
    assert sim and all(o.price == 10_000.0 for o in sim), "시뮬은 실시간가로 체결"


# ─── KISClient.cancel_order ─────────────────────────────────────────


def _kis(monkeypatch, env="paper"):
    from app.api.services import kis_client as kc
    c = kc.KISClient(env=env, app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_hashkey", lambda body: "HASH")
    monkeypatch.setattr(c, "_ensure_token", lambda: "TOKEN")
    return c


class _Resp:
    status_code = 200
    text = "{}"

    @staticmethod
    def json():
        return {"rt_cd": "0", "output": {"ODNO": "ODNO1"}}


def test_cancel_sends_a_full_remaining_quantity_cancel(monkeypatch):
    import json as _json
    from app.api.services import kis_client as kc

    c = _kis(monkeypatch)
    sent = {}
    monkeypatch.setattr(kc.requests, "post",
                        lambda url, **kw: (sent.update(body=_json.loads(kw["data"]),
                                                       url=url), _Resp())[1])

    res = c.cancel_order(code="005930", side="BUY", qty=10,
                         org_no="91252", orgn_odno="ODNO1")

    assert res.ok is True
    assert sent["url"].endswith("/uapi/domestic-stock/v1/trading/order-rvsecncl")
    assert sent["body"]["RVSE_CNCL_DVSN_CD"] == "02"    # 취소 (정정 아님)
    assert sent["body"]["QTY_ALL_ORD_YN"] == "Y"        # 잔량 전부
    assert sent["body"]["ORGN_ODNO"] == "ODNO1"
    assert sent["body"]["KRX_FWDG_ORD_ORGNO"] == "91252"


def test_cancel_without_original_ids_never_reaches_kis(monkeypatch):
    from app.api.services import kis_client as kc

    c = _kis(monkeypatch)
    monkeypatch.setattr(kc.requests, "post", lambda *a, **kw: pytest.fail("must not POST"))

    res = c.cancel_order(code="005930", side="BUY", qty=10, org_no="", orgn_odno="")

    assert res.ok is False and "원주문" in res.error


def test_kill_switch_does_not_block_a_cancel(monkeypatch):
    """The switch stops NEW exposure. Blocking cancels would strand resting
    orders in the book at the exact moment someone decided to stop."""
    import json as _json
    from app.api.services import kis_client as kc

    c = _kis(monkeypatch)
    monkeypatch.setattr(kc, "trading_halted", lambda: "수동 중지")
    sent = {}
    monkeypatch.setattr(kc.requests, "post",
                        lambda url, **kw: (sent.update(body=_json.loads(kw["data"])), _Resp())[1])

    res = c.cancel_order(code="005930", side="BUY", qty=10,
                         org_no="91252", orgn_odno="ODNO1")

    assert res.ok is True and sent["body"]["ORGN_ODNO"] == "ODNO1"


def test_paper_and_real_use_different_cancel_tr_ids(monkeypatch):
    assert _kis(monkeypatch, "paper").tr_set["cancel"].startswith("V")


# ─── 예산 계측 (2026-08-24) ─────────────────────────────────────────
#
# "완전투자 상태에서 매도대금을 당일 매수에 못 쓴다"는 의심을 재기 위한 계측.
# 여기 테스트는 **동작을 고정하는 게 아니라 관측 가능성을 고정한다** — 굶주림
# 자체는 아직 확정된 버그가 아니고(KIS 필드 의미론 미확정), 고치는 것은 별도
# 승인 대상이다. 다만 "조용히 일어나는 것"만은 지금 막는다.


class StarvingClient(RecordingClient):
    """예수금은 바닥인데 주문가능현금에는 매도대금이 잡혀 있는 계좌.

    한국 증권사 관행상 매도대금은 당일 매수에 쓸 수 있지만 예수금(dnca_tot_amt)
    은 D+2 결제라 아직 반영되지 않는다 — docs/05-daily/INSIGHTS.md:29 에 이
    계좌에서 직접 관측된 기록이 있다. 그 상태를 그대로 만든다.
    """

    def __init__(self, dnca, psbl, holdings=()):
        super().__init__(cash=dnca, holdings=holdings)
        self._psbl = psbl

    def get_balance(self):
        from app.api.services.kis_client import AccountSnapshot
        # 총평가는 보유분까지 포함 — 완전투자 계좌의 모습
        return AccountSnapshot(cash=self._cash, total_eval=10_000_000.0,
                               holdings=list(self._holdings))

    def get_orderable_cash(self, code, price=None):
        self.psbl_calls.append(price)
        return {"cash": self._psbl, "nrcvb": self._psbl}


def test_starved_slots_are_reported_not_silent(orders_env, session):
    """예산이 모자라 0주가 된 슬롯이 결과에 남아야 한다.

    예전에는 `if qty <= 0: continue` 가 로그도 카운터도 없이 지나갔다. 게다가
    result["buys"] 는 "사려고 한 수"라, 한 주도 못 산 날에도 dict 은 "buys: 2"
    라고 말했다. 매수가 조용히 안 나간 날과 애초에 후보가 없던 날이 사람 눈에
    똑같이 보였다.
    """
    _account(session, buy_ord_type="market", sell_ord_type="market")
    # 예수금 5만 · 단가 1만 · 후보 2건 → 종목당 2.5만 → 2주씩은 살 수 있다.
    # 예수금을 1천원으로 낮추면 0주가 된다.
    client = StarvingClient(dnca=1_000.0, psbl=1_000.0)

    res = lt.submit_daily_orders(client=client, strategy="open", simulated=False)

    assert res["starved"], "0주 스킵이 결과에 안 실렸다 — 다시 침묵한다"
    assert {s["code"] for s in res["starved"]} <= {"000001", "000002"}
    assert res["starved"][0]["px"] == 10_000
    # 거부가 아니다 — 기존 카운터의 의미를 훼손하면 안 된다.
    assert res["rejected"] == 0
    assert not any(o["side"] == "BUY" for o in client.orders)


def test_budget_observation_carries_both_cash_figures(orders_env, session):
    """예수금과 주문가능현금을 나란히 남겨야 판정이 가능하다.

    판정 규칙: 매도가 체결된 날 `주문가능 − 예수금 ≈ 당일 매도 체결금액` 이면
    min() 이 매도대금을 버리고 있다는 직접 증거다.
    """
    _account(session, buy_ord_type="market", sell_ord_type="market")
    client = StarvingClient(dnca=50_000.0, psbl=2_700_000.0)

    res = lt.submit_daily_orders(client=client, strategy="open", simulated=False)

    b = res["budget"]
    assert b["dnca"] == 50_000
    assert b["psbl"] == 2_700_000
    assert b["nrcvb"] == 2_700_000      # 미수 가정 판정용
    # gap = 총평가 − Σ보유평가 − 예수금. 보유가 없으면 총평가 그대로 남는다.
    assert b["gap"] == 10_000_000 - 0 - 50_000
    assert b["slot_budget"] == 1_000_000


def test_min_keeps_the_smaller_figure_unchanged(orders_env, session):
    """계측이 동작을 바꾸지 않았음을 고정한다.

    주문가능현금이 예수금보다 크면 min() 이 예수금을 고른다 — 그 차액이 곧
    버려지는 매도대금이다. 이 동작을 **고치는 것은 별도 승인 대상**이고, 지금은
    그대로 두되 보이게만 만든다.
    """
    _account(session, buy_ord_type="market", sell_ord_type="market")
    client = StarvingClient(dnca=30_000.0, psbl=5_000_000.0)

    res = lt.submit_daily_orders(client=client, strategy="open", simulated=False)

    # 예수금 3만 / 후보 2건 → 종목당 1.5만 → 단가 1만이면 1주씩.
    # 주문가능 500만을 썼다면 종목당 100만(slot 상한) → 100주씩이었을 것이다.
    buys = [o for o in client.orders if o["side"] == "BUY"]
    assert buys, "매수가 아예 안 나갔다 — 이 테스트의 전제가 깨졌다"
    assert all(o["qty"] == 1 for o in buys), f"min() 동작이 바뀌었다: {buys}"
