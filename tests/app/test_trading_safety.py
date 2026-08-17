"""Safety rails that must hold before real money reaches the order path.

Every one of these guards a failure mode the current code has no answer for:

  * `KIS_ENV=real` with a missing key silently became MOCK mode, and
    `place_order` returned ok=True with a "MOCK-…" id — the dashboard and
    Telegram would report filled orders that never reached KIS.
  * `host`/`tr_set` compare against "real" EXACTLY, so "REAL" or "real "
    routed live credentials at the 모의투자 host and failed auth all day.
  * There was no way to stop trading without a deploy — minutes away, and
    itself risky mid-session.
  * Nothing bounded a single order's size, so a sizing bug or a bad quote
    could put the whole account into one name.
"""

import json

import pytest

pytest.importorskip("requests")

from app.api.services import kis_client as kc  # noqa: E402

# Captured before any test patches it, so the switch's own tests can call the
# real implementation while everything else runs with it stubbed out.
_REAL_TRADING_HALTED = kc.trading_halted


class FakeRedis:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False, px=None):
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)


def _client(monkeypatch, env="paper"):
    c = kc.KISClient(env=env, app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_hashkey", lambda body: "HASH")
    monkeypatch.setattr(c, "_ensure_token", lambda: "TOKEN")
    return c


@pytest.fixture(autouse=True)
def _no_halt(monkeypatch):
    """Default every test to 'switch not engaged' unless it says otherwise."""
    monkeypatch.setattr(kc, "trading_halted", lambda: None)


# ─── env validation ─────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["REAL", "live", "prod", "real ", "Paper"])
def test_typo_env_is_rejected_not_defaulted(bad, monkeypatch):
    """A misspelled env used to fall back to 모의투자 without a word."""
    monkeypatch.setattr(kc.settings, "kis_env", bad)
    with pytest.raises(ValueError, match="KIS_ENV"):
        kc.KISClient(app_key="k", app_secret="s", account_no="12345678-01")


def test_unset_env_still_defaults_to_paper(monkeypatch):
    """Empty is 'unset', not a typo — the safe default must survive."""
    monkeypatch.setattr(kc.settings, "kis_env", "")
    assert kc.KISClient(app_key="k", app_secret="s", account_no="12345678-01").env == "paper"


@pytest.mark.parametrize("good", ["real", "paper", "mock"])
def test_valid_envs_are_accepted(good):
    kc.KISClient(env=good, app_key="k", app_secret="s", account_no="12345678-01")


# ─── real mode must never degrade to mock ───────────────────────────


@pytest.mark.parametrize("key, secret, acct", [
    ("", "s", "12345678-01"),
    ("k", "", "12345678-01"),
    ("k", "s", ""),
])
def test_real_env_with_missing_creds_refuses_to_construct(key, secret, acct):
    """The trap: is_mock would be True and place_order would fake a fill."""
    with pytest.raises(ValueError, match="refusing to run"):
        kc.KISClient(env="real", app_key=key, app_secret=secret, account_no=acct)


def test_paper_env_still_allows_credential_less_mock():
    """Mock convenience must survive for paper/dev — only real is locked down."""
    c = kc.KISClient(env="paper", app_key="", app_secret="", account_no="")
    assert c.is_mock is True


# ─── kill switch ────────────────────────────────────────────────────


def test_halt_blocks_orders_before_any_network_call(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(kc, "trading_halted", lambda: "사용자 중지")

    def _must_not_send(*a, **kw):
        raise AssertionError("halted order must not reach KIS")

    monkeypatch.setattr(kc.requests, "post", _must_not_send)

    res = c.place_order("005930", "BUY", 10, price=60_000)

    assert res.ok is False
    assert "중지" in res.error
    assert res.raw.get("halted") is True


def test_halt_blocks_mock_orders_too(monkeypatch):
    """A halted paper run must not report a fake fill either."""
    c = kc.KISClient(env="mock", app_key="", app_secret="", account_no="")
    monkeypatch.setattr(kc, "trading_halted", lambda: "점검")

    res = c.place_order("005930", "BUY", 10, price=60_000)

    assert res.ok is False


def test_set_and_clear_halt_roundtrip(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(kc, "_halt_redis", lambda: r)

    assert _REAL_TRADING_HALTED() is None
    assert kc.set_trading_halt("장애 대응") is True
    assert _REAL_TRADING_HALTED() == "장애 대응"
    assert kc.set_trading_halt(None) is True
    assert _REAL_TRADING_HALTED() is None


def test_halt_survives_bytes_from_redis(monkeypatch):
    """redis-py returns bytes unless decode_responses is set."""
    monkeypatch.setattr(kc, "_halt_redis",
                        lambda: FakeRedis({kc._HALT_KEY: b"\xea\xb8\xb4\xea\xb8\x89"}))

    assert _REAL_TRADING_HALTED() == "긴급"


def test_halt_fails_open_when_redis_is_down(monkeypatch):
    """A broken switch must not silently stop a working system."""
    monkeypatch.setattr(kc, "_halt_redis", lambda: None)

    assert _REAL_TRADING_HALTED() is None


# ─── order value cap ────────────────────────────────────────────────


def test_order_over_cap_is_refused(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(kc.settings, "live_max_order_value", 1_000_000.0)

    def _must_not_send(*a, **kw):
        raise AssertionError("over-cap order must not reach KIS")

    monkeypatch.setattr(kc.requests, "post", _must_not_send)

    res = c.place_order("005930", "BUY", 100, price=60_000)  # 6,000,000

    assert res.ok is False
    assert "상한" in res.error
    assert res.raw["notional"] == 6_000_000


def test_order_within_cap_passes_through(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(kc.settings, "live_max_order_value", 10_000_000.0)
    sent = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"rt_cd": "0", "msg_cd": "OK", "output": {"ODNO": "123"}}

        text = "{}"

    def _capture(url, **kw):
        # place_order posts a pre-serialised body via data=, not json=.
        sent["body"] = json.loads(kw["data"])
        return Resp()

    monkeypatch.setattr(kc.requests, "post", _capture)

    res = c.place_order("005930", "BUY", 10, price=60_000)  # 600,000

    assert res.ok is True
    assert sent["body"]["ORD_DVSN"] == "00"   # 지정가
    assert sent["body"]["ORD_UNPR"] == "60000"


def test_cap_of_zero_disables_the_check(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(kc.settings, "live_max_order_value", 0.0)

    class Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {"rt_cd": "0", "output": {"ODNO": "1"}}

    monkeypatch.setattr(kc.requests, "post", lambda *a, **kw: Resp())

    assert c.place_order("005930", "BUY", 10_000, price=1_000_000).ok is True


# ─── trade cost ─────────────────────────────────────────────────────


def _lt():
    """live_trader imports qlib; skip the cost tests where it is unavailable."""
    return pytest.importorskip("app.api.services.live_trader")


def test_buy_pays_fee_only_no_transaction_tax():
    lt = _lt()
    # 1,000,000원 매수: 수수료 0.014% = 140원, 거래세 없음
    assert lt.trade_cost("BUY", 10, 100_000) == pytest.approx(140.0)


def test_sell_pays_fee_plus_transaction_tax():
    lt = _lt()
    # 1,000,000원 매도: 수수료 140원 + 거래세 0.18% = 1,800원
    assert lt.trade_cost("SELL", 10, 100_000) == pytest.approx(1_940.0)


def test_cost_is_zero_when_rates_disabled(monkeypatch):
    lt = _lt()
    monkeypatch.setattr(lt.settings, "live_fee_rate", 0.0)
    monkeypatch.setattr(lt.settings, "live_tax_rate", 0.0)
    assert lt.trade_cost("SELL", 10, 100_000) == 0.0


def test_cost_of_an_unpriced_or_empty_fill_is_zero():
    lt = _lt()
    assert lt.trade_cost("BUY", 10, None) == 0.0
    assert lt.trade_cost("BUY", 0, 100_000) == 0.0


def test_round_trip_cost_is_material_against_a_ten_percent_target():
    """Why this matters: the fee-free curve overstated every closed trade.

    A 1천만원 계좌의 100만원 슬롯이 +10%에 익절해도, 왕복 비용이 순이익의
    2%를 먹는다. 매일 회전하는 전략에서는 이게 누적된다.
    """
    lt = _lt()
    buy, sell = 100_000.0, 110_000.0
    qty = 10
    gross = (sell - buy) * qty                                  # 100,000
    net = gross - lt.trade_cost("BUY", qty, buy) - lt.trade_cost("SELL", qty, sell)
    assert gross - net == pytest.approx(140.0 + 154.0 + 1_980.0)
    assert net < gross


# ─── sellable qty / orderable cash ──────────────────────────────────


def test_holding_decodes_from_cache_written_before_sellable_qty(monkeypatch):
    """balance_cache._decode does Holding(**h) over JSON on disk in redis.

    Adding a dataclass field without a default would make every cached
    snapshot un-decodable the moment this deploys.
    """
    bc = pytest.importorskip("app.api.services.balance_cache")
    old_payload = json.dumps({
        "cash": 1.0, "total_eval": 2.0,
        "holdings": [{"code": "005930", "name": "삼성전자", "qty": 1,
                      "avg_price": 1.0, "eval_price": 1.0, "eval_value": 1.0,
                      "pnl": 0.0, "pnl_pct": 0.0}],   # no sellable_qty
        "as_of": "2026-08-14T00:00:00",
    })

    snap, _ = bc._decode(old_payload)

    assert snap.holdings[0].sellable_qty is None


def test_get_balance_parses_sellable_qty(monkeypatch):
    c = _client(monkeypatch)

    class Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {
                "output1": [{"pdno": "005930", "hldg_qty": "10",
                             "ord_psbl_qty": "4", "pchs_avg_pric": "60000",
                             "prpr": "61000", "evlu_amt": "610000",
                             "evlu_pfls_amt": "10000", "evlu_pfls_rt": "1.6"}],
                "output2": [{"dnca_tot_amt": "500000", "tot_evlu_amt": "1110000"}],
            }

    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: Resp())

    h = c.get_balance().holdings[0]

    assert h.qty == 10
    assert h.sellable_qty == 4, "매도가능수량이 보유수량보다 적은 상태를 잡아야 한다"


def test_orderable_cash_returns_empty_on_failure_not_zero(monkeypatch):
    """Callers treat {} as 'unknown' and keep their existing budget.

    Returning 0 would silently size every order to nothing.
    """
    c = _client(monkeypatch)

    def _boom(*a, **kw):
        raise kc.requests.RequestException("network down")

    monkeypatch.setattr(kc.requests, "get", _boom)

    assert c.get_orderable_cash("005930") == {}


def test_orderable_cash_parses_ord_psbl_cash(monkeypatch):
    c = _client(monkeypatch)

    class Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {"output": {"ord_psbl_cash": "3000000", "max_buy_qty": "49"}}

    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: Resp())

    assert c.get_orderable_cash("005930", price=61_000) == {"cash": 3_000_000.0, "max_qty": 49}
