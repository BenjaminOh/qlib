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


# ─── 현금은 D+2 정산금액이다 (2026-08-26) ───────────────────────────
#
# 화면이 "현금 23.5%"를 보여주는데 실제 쓸 수 있는 돈은 8.8% 였다. 163만원을
# 있지도 않은 현금으로 세고 있었다. 원인은 결제 시점이 다른 두 필드를 한 화면에서
# 섞어 쓴 것 — 총평가(tot_evlu_amt)는 D+2 인데 현금만 D+0(dnca_tot_amt)이었다.


def _balance_resp(output2: dict):
    class Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {
                "output1": [{"pdno": "005930", "hldg_qty": "10",
                             "pchs_avg_pric": "60000", "prpr": "61000",
                             "evlu_amt": "610000", "evlu_pfls_amt": "10000",
                             "evlu_pfls_rt": "1.6"}],
                "output2": [output2],
            }
    return Resp


def test_cash_uses_d2_settlement_not_d0_deposit(monkeypatch):
    """D+0 예수금은 미결제 매수대금을 아직 포함한다 — 그걸 현금이라 부르면 안 된다.

    실측(2026-08-26): d0 2,602,518 vs d2 971,429 → 163만원 과대.
    """
    c = _client(monkeypatch)
    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: _balance_resp({
        "dnca_tot_amt": "2602518",        # D+0 — 매수대금이 아직 안 빠짐
        "nxdy_excc_amt": "1160264",       # D+1
        "prvs_rcdl_excc_amt": "971429",   # D+2 — 실제로 쓸 수 있는 돈
        "scts_evlu_amt": "610000",
        "tot_evlu_amt": "1581429",
    })())

    assert c.get_balance().cash == 971_429.0


def test_cash_falls_back_to_d0_when_d2_missing(monkeypatch):
    """모의계좌가 D+2 필드를 안 줄 수 있다. 그때는 예전 동작으로 물러난다."""
    c = _client(monkeypatch)
    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: _balance_resp({
        "dnca_tot_amt": "500000", "tot_evlu_amt": "1110000",
    })())

    assert c.get_balance().cash == 500_000.0


def test_balance_identity_total_minus_holdings_equals_cash(monkeypatch):
    """화면의 "현금 = 총평가 − 평가금액" 이 성립해야 한다.

    KIS 의 총평가금액은 D+2 로 만들어진다 —
      prvs_rcdl_excc_amt + scts_evlu_amt == tot_evlu_amt  (실측 일치)
    D+0 을 쓰면 이 항등식이 깨지고, 깨진 크기가 곧 화면이 뻥튀기한 금액이다.
    운영 스냅샷에서 이 gap 이 −375만까지 벌어졌었다.
    """
    c = _client(monkeypatch)
    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: _balance_resp({
        "dnca_tot_amt": "2602518",
        "prvs_rcdl_excc_amt": "971429",
        "scts_evlu_amt": "610000",
        "tot_evlu_amt": "1581429",        # = 971,429 + 610,000
    })())

    snap = c.get_balance()
    holdings_eval = sum(h.eval_value for h in snap.holdings)
    assert snap.total_eval - holdings_eval == snap.cash


def test_cash_never_grows_toward_margin(monkeypatch):
    """수정이 미수 방향으로 현금을 키우는 일은 없어야 한다.

    D+2 가 D+0 보다 큰 경우(순매도 국면)에도 예산이 부풀지 않는지 고정한다 —
    이 코드는 두 값 중 하나를 고를 뿐 더하지 않는다.
    """
    c = _client(monkeypatch)
    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: _balance_resp({
        "dnca_tot_amt": "100000", "prvs_rcdl_excc_amt": "900000",
        "scts_evlu_amt": "610000", "tot_evlu_amt": "1510000",
    })())

    snap = c.get_balance()
    assert snap.cash == 900_000.0          # D+2 를 고른다
    assert snap.cash <= snap.total_eval    # 총평가를 넘지 않는다


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
            return {"output": {"ord_psbl_cash": "3000000", "max_buy_qty": "49",
                               "nrcvb_buy_amt": "2500000"}}

    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: Resp())

    assert c.get_orderable_cash("005930", price=61_000) == {
        "cash": 3_000_000.0, "max_qty": 49, "nrcvb": 2_500_000.0}


def test_orderable_cash_carries_nrcvb_for_observation():
    """미수없는매수금액을 관측용으로 실어 나른다.

    get_orderable_cash 의 docstring 은 ord_psbl_cash 를 "미수 없이 쓸 수 있는
    금액"이라 단정하지만 검증된 적이 없다. KIS 에는 nrcvb_buy_amt 라는 전용
    필드가 따로 있고, ord_psbl_cash 가 증거금 레버리지 금액이라면 호출부의
    min() 을 푸는 순간 미수 방어가 사라진다. 판정 전까지 아무도 이 값을 쓰지
    않지만, 실려 오지 않으면 판정 자체를 할 수 없다.
    """
    import inspect
    src = inspect.getsource(kc.KISClient.get_orderable_cash)
    assert "nrcvb_buy_amt" in src


def test_orderable_cash_zero_is_indistinguishable_from_failure(monkeypatch):
    """⚠ 알려진 결함을 고정한다 — 고치는 게 아니라 기록하는 테스트다.

    `float(...) or None` 때문에 ord_psbl_cash == 0 이면 None 이 되고, 호출부의
    `if psbl.get("cash"):` 가 falsy 로 빠져 **가장 위험한 값에서만 미수 가드가
    통째로 꺼진다.** 조회 실패({})와 0원이 같은 값으로 뭉개져 있다.

    이 동작을 바꾸는 것은 매매 동작 변경이라 별도 승인 대상이다. 지금은 계측만
    한다(live_trader 가 두 경우를 서로 다른 로그로 구분한다).
    """
    c = _client(monkeypatch)

    class Resp:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {"output": {"ord_psbl_cash": "0", "max_buy_qty": "0"}}

    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: Resp())
    out = c.get_orderable_cash("005930", price=61_000)
    assert out["cash"] is None      # 0.0 이 아니라 None — 여기가 트랩


# ─── appkey-scoped redis keys ───────────────────────────────────────
#
# KIS enforces token invalidation and the request budget per APPKEY. Keying
# redis by account number was harmless while one appkey served one account;
# with several accounts behind one appkey (the shape a 10-account setup takes,
# since KIS issues one application per ≤2 accounts) each cano-keyed cache would
# issue its own token and every issue would kill the others — the 2026-07-29 /
# 08-11 lost-order mechanism, but daily.


def _c(app_key, cano):
    return kc.KISClient(env="paper", app_key=app_key, app_secret="s", account_no=cano)


def test_accounts_sharing_an_appkey_share_token_and_gate():
    a = _c("SAME_KEY", "11111111-01")
    b = _c("SAME_KEY", "22222222-01")

    assert a._redis_token_key == b._redis_token_key
    assert a._redis_cooldown_key == b._redis_cooldown_key
    assert a._redis_slot_key == b._redis_slot_key, "하나의 appkey 예산을 함께 써야 한다"


def test_different_appkeys_stay_isolated():
    a = _c("KEY_A", "11111111-01")
    b = _c("KEY_B", "11111111-01")   # same account number, different appkey

    assert a._redis_token_key != b._redis_token_key
    assert a._redis_slot_key != b._redis_slot_key


def test_balance_cache_stays_per_account(monkeypatch):
    """Balance is account data — it must NOT collapse onto the appkey."""
    bc = pytest.importorskip("app.api.services.balance_cache")

    monkeypatch.setattr(bc, "get_kis_client", lambda account="main": _c("SAME_KEY", "11111111-01"))
    keys_a = bc._keys()
    monkeypatch.setattr(bc, "get_kis_client", lambda account="main": _c("SAME_KEY", "22222222-01"))
    keys_b = bc._keys()

    assert keys_a[0] != keys_b[0], "계좌가 다르면 잔고 캐시는 분리돼야 한다"


def test_appkey_is_never_exposed_in_a_redis_key():
    """Keys land in logs and `redis-cli --scan` output; the appkey is a secret."""
    secret = "PSxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    c = _c(secret, "11111111-01")

    for key in (c._redis_token_key, c._redis_cooldown_key, c._redis_slot_key):
        assert secret not in key
        assert len(c._appkey_scope) == 12


def test_missing_appkey_does_not_collide_with_a_real_one():
    assert _c("", "11111111-01")._appkey_scope == "nokey"
    assert _c("REAL_KEY", "11111111-01")._appkey_scope != "nokey"
