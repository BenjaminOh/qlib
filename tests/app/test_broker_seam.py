"""Stage 0 — 브로커 이음매의 골든 기준선.

이 파일은 `app/` 을 **한 줄도 바꾸지 않는다.** 유일한 목적은, 앞으로 있을
브로커 추상화 리팩터(`ExecutionBroker` 프로토콜 · `get_broker()` 팩토리 ·
키움 어댑터)가 "동작은 하나도 안 바뀐다"고 주장할 때 **그 주장을 기계가
검사할 수 있게** 만드는 것이다.

왜 리팩터보다 **먼저** 쓰는가
---------------------------
골든 트레이스를 리팩터 *뒤에* 뜨면 그건 무변경 증명이 아니라 **새 동작을
사후 승인**하는 것이다. 2026-08-27 에 "사다리+트레일을 실계좌에 먼저 넣고
백테스트는 다음 날" 로 동결 원칙을 어긴 사고가 있었고, 거기서 나온 규칙이
**백테스트 → 모의 → 실계좌** 순서다. 같은 규칙을 리팩터에 적용하면
**기준선 → 리팩터** 가 된다. 그래서 이 파일이 Stage 0 이다.

무엇을 못 박는가
---------------
1. **호출 트레이스** — 09:00 실주문 경로와 컷오프 취소 경로가 브로커의 어떤
   메서드를 **어떤 순서로, 어떤 인자로** 부르는지. 리팩터가 호출 하나를
   더하거나 빼거나 순서를 바꾸면 여기서 걸린다.
2. **sleep 총량** — `KIS_THROTTLE_SECONDS` 지식이 오케스트레이션에서 브로커로
   옮겨갈 때, 실제 대기 시간이 그대로인지. 0.35초로 줄였다가 주문이 거부된
   실측이 2026-07-29 에 있다. 이 값은 성능이 아니라 **안전 장치**다.
3. **Redis 키 문자열** — 캐시 키가 바이트 단위로 같은지. 키가 바뀌면 배포
   직후 캐시가 비어 첫 조회가 브로커를 때린다.
4. **원장 기입** — `_persist_order` 가 쓰는 `ord_dvsn`/`status`/`kis_order_id`/
   `raw_response`. 화면·대사·회고가 전부 이 네 칸에서 파생된다.
5. **공개 표면** — 주문 경로가 실제로 쓰는 브로커 메서드 7개의 시그니처.
   이게 곧 `ExecutionBroker` 프로토콜이 만족시켜야 할 계약이다.

읽는 법
-------
`GOLDEN_*` 상수가 기준선이다. 리팩터 중 이 파일이 실패하면 **먼저 코드를
의심하라.** 골든 값을 고쳐서 초록으로 만드는 것은, 그 변화가 의도된 것임을
설명할 수 있을 때만 정당하다.
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Order, Signal, TradingAccount  # noqa: E402
from app.api.services import kis_client as kc  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402

DAY = date(2026, 8, 18)

# 주문 경로가 실제로 쓰는 브로커 메서드. 시세·리서치용 6개
# (get_daily_bars / get_orderbook / get_investor_daily / get_rank_fluctuation /
# get_rank_volume / get_open_days) 는 계좌 축과 무관하므로 여기 없다 —
# 그것들은 브로커가 아니라 시세 제공자의 표면이다.
EXECUTION_SURFACE = (
    "get_balance",
    "get_quote",
    "get_orderable_cash",
    "place_order",
    "cancel_order",
    "get_daily_fills",
)


# ─── 기록용 테스트 대역 ──────────────────────────────────────────────


class TracingBroker:
    """`KISClient` 를 상속하지 않는다 — 덕타이핑 주입이 이 저장소의 지배적
    테스트 패턴이고(`test_account_policy.py:44`), 그 사실 자체가 "KISClient 는
    이미 인터페이스"라는 증거다. 반환 타입만 실제 dataclass 를 쓴다.

    모든 호출을 `trace` 에 순서대로 적재한다. 인자는 읽을 수 있는 튜플로
    줄여서 담는다 — 골든 값이 사람이 눈으로 검토할 수 있어야 하기 때문이다.
    """

    is_mock = False

    def __init__(self, cash=10_000_000.0, holdings=(), quote=None, cancel_ok=True):
        self._cash = cash
        self._holdings = list(holdings)
        self._quote = quote or {"open": 10_000.0, "price": 10_500.0}
        self._cancel_ok = cancel_ok
        self.trace: list[tuple] = []
        self._orders = 0

    def get_balance(self):
        self.trace.append(("get_balance",))
        return kc.AccountSnapshot(cash=self._cash, total_eval=self._cash,
                                  holdings=list(self._holdings))

    def get_quote(self, code):
        self.trace.append(("get_quote", code))
        return dict(self._quote)

    def get_orderable_cash(self, code, price=None):
        self.trace.append(("get_orderable_cash", code, price))
        return {"cash": self._cash}

    def place_order(self, code, side, qty, price=None):
        self.trace.append(("place_order", code, side, qty, price))
        self._orders += 1
        return kc.OrderResult(
            ok=True, order_id=f"ODNO{self._orders}", code=code, side=side,
            qty=qty, price=price,
            raw={"output": {"KRX_FWDG_ORD_ORGNO": "91252",
                            "ODNO": f"ODNO{self._orders}"}},
            error=None)

    def cancel_order(self, *, code, side, qty, org_no, orgn_odno, ord_dvsn="00"):
        self.trace.append(("cancel_order", code, side, qty, org_no, orgn_odno, ord_dvsn))
        return kc.OrderResult(ok=self._cancel_ok, order_id=orgn_odno, code=code,
                              side=side, qty=qty, price=None, raw={},
                              error=None if self._cancel_ok else "거부")

    def get_daily_fills(self, start, end):
        self.trace.append(("get_daily_fills", start, end))
        return {}


class _NoCloseSession:
    """테스트 세션을 코드에 넘기되 닫지 않는다 (`test_account_policy.py:249`)."""

    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


# ─── 픽스처 ──────────────────────────────────────────────────────────


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def sleeps(monkeypatch):
    """실제로 잔 시간을 기록한다.

    다른 테스트 파일은 `lt.time.sleep` 을 no-op 으로 덮는다 — 빠르니까. 여기서는
    **얼마나 오래 자는지가 검사 대상**이라 기록만 하고 실제로는 자지 않는다.
    """
    recorded: list[float] = []
    monkeypatch.setattr(lt.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def orders_env(session, monkeypatch, sleeps):
    """09:00 주문 경로를 메모리 DB 위에서 결정론적으로 돌린다.

    `test_account_policy.py:485` 의 `orders_env` 와 같은 구성이다. 다르게 둔 것은
    `time.sleep` 하나뿐 — no-op 대신 `sleeps` 픽스처가 기록한다.
    """
    for rank, code in enumerate(["000001", "000002"], start=1):
        session.add(Signal(as_of=DAY, rank=rank, code=code, score=1.0 / rank,
                           model_class="LGBModel",
                           strategy_class="TopkDropoutStrategy"))
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
    return session


@pytest.fixture
def sweep_env(session, monkeypatch, sleeps):
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "SessionLocal", lambda: _NoCloseSession(session))
    return session


def _account(session, **kw):
    row = TradingAccount(account_id=kw.pop("account_id", "main"), **kw)
    session.add(row)
    session.commit()
    return row


def _resting_order(session, **kw):
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


# ─── 1. 골든 호출 트레이스 ───────────────────────────────────────────
#
# 리팩터가 브로커 호출을 하나라도 더하거나 빼거나 순서를 바꾸면 여기서 걸린다.


# `get_balance` 가 **두 번**인 것은 오타가 아니다. `submit_daily_orders` 는
# 매도 루프 뒤에 "Refresh cash after sells"(live_trader.py:652-656) 를 두는데,
# 이 재조회가 **매도 0건이어도 무조건** 실행된다. 그래서 매도가 없는 날에도
# 브로커 호출 1회 + 1.2초 대기가 더 붙는다.
#
# 관측이지 결함 신고가 아니다 — 잔고를 한 번 더 읽는 것은 틀린 답을 만들지
# 않는다. 전략 동결 중이므로 고치지 않고 기준선에 그대로 박는다. 리팩터가
# 이걸 "최적화" 하려 들면 여기서 걸리는데, 그게 이 파일의 목적이다.
GOLDEN_OPEN_MARKET = [
    ("get_balance",),                              # :550  최초 스냅샷
    ("get_balance",),                              # :655  매도 후 재조회 (무조건)
    ("get_orderable_cash", "000001", None),        # :716  시장가라 price=None
    ("place_order", "000001", "BUY", 100, None),
    ("place_order", "000002", "BUY", 100, None),
]


def test_golden_trace_open_market(orders_env, session):
    """09:00 실주문 — 시장가 계좌(오늘 살아 있는 그 계좌)."""
    _account(session, buy_ord_type="market", sell_ord_type="market")
    broker = TracingBroker()

    lt.submit_daily_orders(client=broker, strategy="open", simulated=False)

    assert broker.trace == GOLDEN_OPEN_MARKET


# 수량 103 이 핵심이다. 슬롯 예산 1,000,000 ÷ **지정가** 9,700 = 103주.
# 마지막 종가(10,000)로 나눴다면 100주였다 — 그 경우 수량·주문가능현금·체결가가
# 서로 다른 기준을 말하게 된다.
#
# `get_quote` 가 없는 것도 의미가 있다: base 가 `prev_close` 라 시세를 안 부른다
# (live_trader.py:695 은 base 가 open/quote 일 때만 브로커에 묻는다).
GOLDEN_OPEN_LIMIT = [
    ("get_balance",),
    ("get_balance",),                                   # 위와 같은 무조건 재조회
    ("get_orderable_cash", "000001", 9_700.0),          # 지정가 기준으로 물어야 한다
    ("place_order", "000001", "BUY", 103, 9_700.0),
    ("place_order", "000002", "BUY", 103, 9_700.0),
]


def test_golden_trace_open_limit(orders_env, session):
    """지정가 계좌 — 수량·주문가능현금 조회가 전부 지정가 기준이어야 한다.

    마지막 종가(10,000)로 계산하면 세 곳이 서로 다른 숫자를 말하게 된다.
    """
    _account(session, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, sell_ord_type="market")
    broker = TracingBroker()

    lt.submit_daily_orders(client=broker, strategy="open", simulated=False)

    assert broker.trace == GOLDEN_OPEN_LIMIT


GOLDEN_CANCEL_SWEEP = [
    ("cancel_order", "005930", "BUY", 10, "91252", "ODNO1", "00"),
]


def test_golden_trace_cancel_sweep(sweep_env):
    """컷오프 취소 — 실패하면 실주문이 장중에 방치되는 유일한 경로다.

    KIS 원문 응답에서 꺼낸 (원주문조직번호, 원주문번호) 가 그대로 넘어가야
    한다. 브로커마다 취소 식별자의 개수와 이름이 다르므로, 이 트레이스가
    `OrderHandle` 추상화의 기준선이 된다 (키움은 `ord_no` 하나뿐이다).
    """
    _account(sweep_env, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:20")
    _resting_order(sweep_env)
    broker = TracingBroker()

    lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 15, 30),
                              client=broker, account_id="main")

    assert broker.trace == GOLDEN_CANCEL_SWEEP


def test_simulated_curves_never_touch_the_broker(orders_env, session, monkeypatch):
    """시뮬 9곡선은 브로커를 **한 번도** 부르지 않는다.

    빈 트레이스가 기준선이라는 게 핵심이다. 리팩터가 시뮬 경로를 `SimBroker`
    같은 것으로 통일하려 들면 이 테스트가 즉시 걸린다 — 그건 "어느 주문이
    실주문인지"를 바꾸는 변경이고 동결 원칙 위반이다.
    """
    _account(session, buy_ord_type="limit", buy_base="prev_close", buy_offset_pct=0.03)
    monkeypatch.setattr(lt, "_sim_fill_price", lambda code: 10_000.0)
    monkeypatch.setattr(
        lt, "_simulated_balance",
        lambda db, strategy=None, **kw: kc.AccountSnapshot(
            cash=10_000_000.0, total_eval=10_000_000.0, holdings=[]))
    broker = TracingBroker()

    lt.submit_daily_orders(client=broker, strategy="close", simulated=True)

    assert broker.trace == []


# ─── 2. sleep 총량 ───────────────────────────────────────────────────


def test_throttle_constant_is_unchanged():
    """1.2초는 성능 튜닝 값이 아니라 안전 장치다.

    0.35초로 줄였다가 주문이 거부된 실측이 2026-07-29 에 있다. 레이트 지식이
    브로커로 옮겨갈 때 값이 따라 움직이지 않도록 여기서 못 박는다.
    """
    assert lt.KIS_THROTTLE_SECONDS == 1.2


def test_golden_sleep_budget_open_market(orders_env, session, sleeps):
    """주문 경로가 실제로 잔 시간. 리팩터가 sleep 을 빠뜨리면 한도를 넘긴다.

    매수 2건짜리 아침에 4회 = 4.8초. 내역:
      :656  매도 후 잔고 재조회 뒤   (매도 0건이어도 실행 — GOLDEN_OPEN_MARKET 주석)
      :718  주문가능현금 조회 뒤
      :790  매수 주문마다 (×2)

    호출당 1회라는 규칙이 보이는 것이 중요하다. 브로커가 `pace()` 를 갖게 되면
    이 총량이 그대로여야 한다.
    """
    _account(session, buy_ord_type="market", sell_ord_type="market")

    lt.submit_daily_orders(client=TracingBroker(), strategy="open", simulated=False)

    assert sleeps == [1.2, 1.2, 1.2, 1.2]
    assert sum(sleeps) == pytest.approx(4.8)


def test_golden_sleep_budget_cancel_sweep(sweep_env, sleeps):
    _account(sweep_env, buy_ord_type="limit", buy_base="prev_close",
             buy_offset_pct=0.03, buy_cancel_hhmm="15:20")
    _resting_order(sweep_env)

    lt.cancel_unfilled_orders(DAY, now=datetime(2026, 8, 18, 15, 30),
                              client=TracingBroker(), account_id="main")

    assert sleeps == [1.2]


# ─── 3. Redis 키 문자열 ──────────────────────────────────────────────


def test_golden_balance_cache_keys():
    """캐시 키가 바이트 단위로 같아야 한다.

    키가 바뀌면 배포 직후 캐시가 비어 첫 `/live/balance` 가 브로커를 때리거나
    `_from_db` 로 물러난다. 접두사 `kis:` 는 우연이 아니라 규약이다 — 키움
    어댑터는 `kiwoom:` 을 쓰게 되므로 **마이그레이션 없이** 공존한다.
    """
    from app.api.services import balance_cache as bc

    base, last, down = bc._keys("main")
    empty_cano = hashlib.sha256(b"").hexdigest()[:12]

    assert base == f"kis:balance:paper:{empty_cano}"
    assert (last, down) == (f"{base}:last", f"{base}:down")
    assert empty_cano == "e3b0c44298fc", "해시 방식이 바뀌면 캐시가 통째로 무효화된다"


# ─── 4. 원장 기입 ────────────────────────────────────────────────────


@pytest.mark.parametrize("price,ok,expected", [
    (None,   True,  {"ord_dvsn": "01", "status": "SUBMITTED", "kind": "market"}),
    (9700.0, True,  {"ord_dvsn": "00", "status": "SUBMITTED", "kind": "limit"}),
    (None,   False, {"ord_dvsn": "01", "status": "REJECTED",  "kind": "market"}),
])
def test_golden_order_ledger_row(session, monkeypatch, price, ok, expected):
    """`_persist_order` 가 원장에 쓰는 네 칸.

    화면의 주문 이력, 체결 대사, 회고가 전부 여기서 파생된다. `ord_dvsn` 은
    **KIS 코드값**("00"/"01")이 DB 스키마에 그대로 박힌 자리다 — 키움은 지정가가
    `trde_tp="0"` 이라 `Order.kind` 의 `== "00"` 비교가 조용히 market 으로
    오분류한다. 그 정규화를 언제 하든, 그 전까지의 기준선이 이것이다.
    """
    monkeypatch.setattr(lt, "_stock_name", lambda code: "삼성전자")
    res = kc.OrderResult(ok=ok, order_id="ODNO1" if ok else None, code="005930",
                         side="BUY", qty=10, price=price,
                         raw={"output": {"ODNO": "ODNO1"}},
                         error=None if ok else "잔고부족")

    o = lt._persist_order(session, DAY, "005930", "BUY", 10, price, res)

    assert o.ord_dvsn == expected["ord_dvsn"]
    assert o.status == expected["status"]
    assert o.kind == expected["kind"]
    assert o.kis_order_id == ("ODNO1" if ok else None)
    assert '"ODNO": "ODNO1"' in o.raw_response


# ─── 5. 공개 표면 (= ExecutionBroker 가 만족시켜야 할 계약) ──────────


def _param_shape(fn) -> list[tuple]:
    """(이름, 전달 방식, 기본값) — 호출 계약의 실체.

    애노테이션 **문자열**을 박지 않는 이유: `from __future__ import annotations`
    아래에서는 애노테이션이 문자열로 남아 표기 방식이 바뀌면 값이 흔들린다.
    포매팅이 바뀔 때마다 깨지는 검사는 안전이 아니라 마찰이다. 호출부가 실제로
    의존하는 것은 **이름·순서·키워드 전용 여부·기본값**이다.
    """
    return [(p.name, p.kind.name,
             "∅" if p.default is inspect.Parameter.empty else p.default)
            for p in inspect.signature(fn).parameters.values()]


def test_execution_surface_call_contract():
    """주문 경로가 쓰는 6개 메서드의 호출 계약.

    이게 곧 `ExecutionBroker` 프로토콜의 내용이 된다.

    `cancel_order` 만 키워드 전용이고 식별자를 **두 개**(`org_no`,`orgn_odno`)
    받는다는 점이 중요하다 — 이건 KIS 고유 사정이고, 키움은 `ord_no` 하나뿐이다.
    그래서 이 줄이 `OrderHandle` 추상화가 필요한 이유를 정확히 가리킨다.
    """
    shapes = {name: _param_shape(getattr(kc.KISClient, name))
              for name in EXECUTION_SURFACE}

    assert shapes == {
        "get_balance": [
            ("self", "POSITIONAL_OR_KEYWORD", "∅"),
        ],
        "get_quote": [
            ("self", "POSITIONAL_OR_KEYWORD", "∅"),
            ("code", "POSITIONAL_OR_KEYWORD", "∅"),
        ],
        "get_orderable_cash": [
            ("self", "POSITIONAL_OR_KEYWORD", "∅"),
            ("code", "POSITIONAL_OR_KEYWORD", "∅"),
            ("price", "POSITIONAL_OR_KEYWORD", None),
        ],
        "place_order": [
            ("self", "POSITIONAL_OR_KEYWORD", "∅"),
            ("code", "POSITIONAL_OR_KEYWORD", "∅"),
            ("side", "POSITIONAL_OR_KEYWORD", "∅"),
            ("qty", "POSITIONAL_OR_KEYWORD", "∅"),
            # price=None 이 곧 "시장가" 라는 규약. KIS ORD_DVSN="01" 에서 왔지만
            # "가격을 지정하지 않았다" 는 주문의 보편적 사실이라 그대로 둔다.
            ("price", "POSITIONAL_OR_KEYWORD", None),
        ],
        "cancel_order": [
            ("self", "POSITIONAL_OR_KEYWORD", "∅"),
            ("code", "KEYWORD_ONLY", "∅"),
            ("side", "KEYWORD_ONLY", "∅"),
            ("qty", "KEYWORD_ONLY", "∅"),
            ("org_no", "KEYWORD_ONLY", "∅"),      # KRX_FWDG_ORD_ORGNO — KIS 전용
            ("orgn_odno", "KEYWORD_ONLY", "∅"),   # ODNO
            ("ord_dvsn", "KEYWORD_ONLY", "00"),   # KIS 코드값이 인자로 새어 있다
        ],
        "get_daily_fills": [
            ("self", "POSITIONAL_OR_KEYWORD", "∅"),
            ("start", "POSITIONAL_OR_KEYWORD", "∅"),
            ("end", "POSITIONAL_OR_KEYWORD", "∅"),
        ],
    }


def test_tracing_broker_matches_the_real_call_contract():
    """대역의 시그니처가 실물과 같아야 골든 트레이스가 의미를 갖는다.

    대역이 더 느슨하면(예: `**kwargs`) 프로덕션이 인자를 바꿔도 트레이스가
    조용히 통과한다. 그 순간 이 파일 전체가 거짓 초록이 된다.
    """
    for name in EXECUTION_SURFACE:
        real = _param_shape(getattr(kc.KISClient, name))
        fake = _param_shape(getattr(TracingBroker, name))
        assert fake == real, f"{name} 대역 시그니처가 실물과 다르다"


def test_get_kis_client_default_account_is_main():
    """팩토리의 기본 인자. `get_broker` 별칭이 시그니처를 보존하는지 검사한다."""
    sig = inspect.signature(kc.get_kis_client)
    assert sig.parameters["account"].default == kc.ACCOUNT_MAIN


def test_tracing_broker_covers_the_whole_execution_surface():
    """대역이 표면 전체를 덮는지. 프로덕션이 메서드를 추가하면 골든 트레이스에
    구멍이 생기는데, 대역이 조용히 통과시키면 그 구멍이 안 보인다."""
    missing = [m for m in EXECUTION_SURFACE if not hasattr(TracingBroker, m)]
    assert not missing, f"TracingBroker 가 못 덮는 메서드: {missing}"
