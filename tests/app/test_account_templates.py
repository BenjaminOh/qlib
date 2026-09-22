"""계좌가 고른 **매매 방식(template)** 이 실제 진입으로 이어지는가 (2026-09-21).

전략마다 beat 슬롯과 래퍼를 복제하던 구조를 "시각 슬롯 1개 → 계좌 순회"로 바꿨다.
그 순회가 **누구를 돌리고 누구를 건너뛰는지**가 이 파일의 전부다.

특히 조용한 실패 두 가지를 막는다:
  - 템플릿을 안 고른 계좌가 돌아 **예상 못 한 주문**이 나가는 것
  - `strategy_params` 가 깨졌을 때 기본값으로 돌아 **ret20 상한이 사라지는** 것
    (상한 없이 도는 것은 다른 전략이 되는 것이지 "기본 동작"이 아니다)
"""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("requests")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.api.db as dbmod  # noqa: E402
from app.api.db import Base, TradingAccount  # noqa: E402
from app.api.services import account_templates as AT  # noqa: E402


class _NoClose:
    def __init__(self, s):
        self._s = s

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _NoClose(s))
    yield s
    s.close()
    engine.dispose()


def _acct(session, account_id, **over):
    kw = dict(account_id=account_id, label=account_id, template="cafe",
              strategy_params=None, strategy_enabled=True, enabled=True)
    kw.update(over)
    session.add(TradingAccount(**kw))
    session.commit()


def test_only_accounts_with_that_template_run(session):
    _acct(session, "acct1", template="cafe")
    _acct(session, "acct2", template="close")
    _acct(session, "acct3", template=None)          # 방식 미정 — 주문 없음

    ids = [p["account_id"] for p in AT.account_plans("cafe")]

    assert ids == ["acct1"], f"cafe 템플릿에 {ids} 가 잡혔다"


def test_plan_strategy_routes_back_to_its_own_account(session):
    """계획의 전략이 **자기 계좌**를 가리켜야 한다 (2026-09-22 버그의 역검증).

    예전 코드는 `strategy = row.account_id` 로 단정했다. acct1~4 는 우연히 맞지만
    main·cafe·cool 은 전략이 open·cafereal·coolreal 이라 틀리고, 그 순간
    `_account_for` 가 **기본 계좌로 폴백**해 남의 계좌로 주문이 나간다.
    그래서 "같은 문자열인가"가 아니라 "돌아오는가"를 본다.
    """
    from app.api.services.live_trader import _account_for

    _acct(session, "acct2", template="cafe")

    plan = AT.account_plans("cafe")[0]

    assert _account_for(plan["strategy"]) == plan["account_id"] == "acct2"


@pytest.mark.parametrize("account_id", ["main", "cafe", "cool"])
def test_accounts_with_dedicated_slots_are_skipped(session, account_id):
    """전용 beat 슬롯이 있는 계좌는 디스패처가 잡지 않는다.

    두 가지를 동시에 막는다:
      - **이중 주문** — cafereal·coolreal 은 이미 15:28 전용 슬롯이 있다.
      - **계좌 유출** — 전략을 계좌 id 로 지어내면 `_account_for` 폴백으로
        카페 픽이 기본 계좌(실주문)로 나간다.
    """
    _acct(session, account_id, template="cafe")

    assert AT.account_plans("cafe") == []


def test_unknown_account_is_skipped(session):
    """카탈로그에 없는 계좌는 전략을 지어내지 않고 건너뛴다."""
    _acct(session, "ghost", template="cafe")

    assert AT.account_plans("cafe") == []


def test_template_strategy_never_falls_back_to_default():
    """`primary_strategy` 를 쓰면 안 되는 이유를 못으로 박아 둔다.

    그쪽은 미등록 계좌에 `open`(기본 계좌 전략)을 돌려주는 읽기용 기본값이다.
    주문 경로에서 그 값을 쓰면 고치려던 버그가 그대로 재현된다.
    """
    from app.api.services.holding_attribution import primary_strategy

    assert primary_strategy("ghost") == "open"      # 읽기 경로의 기본값
    assert AT.template_strategy("ghost") is None    # 주문 경로는 거부한다
    assert AT.template_strategy("cool") is None     # 전용 슬롯이 진실
    assert AT.template_strategy("acct3") == "acct3"


def test_disabled_strategy_is_skipped(session):
    """방식을 지우지 않고 잠시 멈추는 스위치."""
    _acct(session, "acct1", template="cafe", strategy_enabled=False)

    assert AT.account_plans("cafe") == []


def test_disabled_account_is_skipped(session):
    """계좌 자체가 꺼져 있으면 주문도 없다."""
    _acct(session, "acct1", template="cafe", enabled=False)

    assert AT.account_plans("cafe") == []


def test_broken_params_skip_instead_of_defaulting(session):
    """**기본값으로 돌리지 않는다.**

    `{"ret20_max": 50}` 이 깨졌을 때 상한 없이 돌면 그건 다른 전략이다.
    조용히 다른 것을 사는 것보다 오늘 안 사는 쪽이 낫다.
    """
    _acct(session, "acct1", template="cafe", strategy_params="{망가진 json")

    assert AT.account_plans("cafe") == []


def test_params_are_parsed(session):
    _acct(session, "acct1", template="cafe",
          strategy_params='{"ret20_max": 50}')

    assert AT.account_plans("cafe")[0]["params"] == {"ret20_max": 50}


def test_no_accounts_is_not_an_error(session):
    """아무도 안 골랐으면 조용히 끝난다 — 매일 도는 슬롯이라 시끄러우면 안 된다."""
    assert AT.run_template("cafe") == {"status": "no_accounts", "template": "cafe"}


def test_unknown_template_is_reported(session):
    r = AT.run_template("nope")

    assert r["status"] == "unknown_template"


def test_one_account_failing_does_not_stop_the_rest(session, monkeypatch):
    """예전에는 전략마다 태스크가 따로라 격리가 저절로 됐다 — 순회로 바꾸면서 잃기 쉽다."""
    _acct(session, "acct1", template="cafe")
    _acct(session, "acct2", template="cafe")

    calls = []

    def _boom(plan):
        calls.append(plan["account_id"])
        if plan["account_id"] == "acct1":
            raise RuntimeError("KIS 터짐")
        return {"status": "ok"}

    monkeypatch.setitem(AT._RUNNERS, "cafe", _boom)

    r = AT.run_template("cafe")

    assert calls == ["acct1", "acct2"], "앞 계좌가 터지자 뒤 계좌를 건너뛰었다"
    assert r["accounts"]["acct1"]["status"] == "error"
    assert r["accounts"]["acct2"]["status"] == "ok"


def test_api_refuses_template_on_a_fixed_strategy_account(session, monkeypatch):
    """저장 단계에서 막는다 — 디스패처 방어만 두면 "설정했는데 왜 안 도나"가 된다."""
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    import app.api.routers.live as live

    monkeypatch.setattr(live, "SessionLocal", lambda: _NoClose(session))
    _acct(session, "cool", template=None)

    with pytest.raises(HTTPException) as e:
        live.put_account_strategy(
            "cool", live.AccountStrategyUpdate(template="cafe"))

    assert e.value.status_code == 422
    assert "coolreal" in str(e.value.detail)


def test_api_allows_template_on_a_slot_account(session, monkeypatch):
    """역검증 — 위 거부가 슬롯 계좌까지 막아버리면 기능이 죽는다."""
    pytest.importorskip("fastapi")
    import app.api.routers.live as live
    import app.api.services.kis_client as kc

    monkeypatch.setattr(live, "SessionLocal", lambda: _NoClose(session))
    # 라우터가 함수 안에서 import 하므로 원본 모듈을 갈아끼운다(redis 접속 회피).
    monkeypatch.setattr(kc, "bump_accounts_rev", lambda: None)
    _acct(session, "acct1", template=None)

    out = live.put_account_strategy(
        "acct1", live.AccountStrategyUpdate(template="cafe", ret20_max=50))

    assert out["template"] == "cafe"


def test_cafe_runner_passes_ret20_and_real(session, monkeypatch):
    """실주문 플래그와 상한이 그대로 내려가는지 — 여기가 어긋나면 시뮬로 돌거나
    상한 없이 산다."""
    seen = {}

    def _fake(trade_date, *, strategy, ret20_max, real=False):
        seen.update(strategy=strategy, ret20_max=ret20_max, real=real)
        return {"status": "ok"}

    import app.api.services.market_screener as ms
    monkeypatch.setattr(ms, "_submit_cafe_like", _fake)

    AT._run_cafe({"strategy": "acct1", "params": {"ret20_max": 50}})

    assert seen == {"strategy": "acct1", "ret20_max": 50.0, "real": True}
