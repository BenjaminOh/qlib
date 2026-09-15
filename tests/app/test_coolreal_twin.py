"""coolreal — cafecool 을 **실계좌**로 돌리는 전략. 계약을 고정한다.

2026-09-15 오너 지시: 세 번째 모의계좌를 열고, 카페 모사 픽 중 ret20 상한(과열
제외)을 통과한 것만 실제로 사보기로 했다. cafecool(시뮬) 곡선은 동결된 채 그대로
두고 별도 전략·별도 계좌로 돌려, **두 곡선의 격차가 곧 시뮬 체결 가정의 크기**가
되게 한다. cafereal(상한 없음)과 나란히 놓으면 "과열 제외가 실제 체결에서도
값어치가 있는가"를 같은 계좌 조건에서 잰다.

여기서 막는 사고:
  * 청산·시드가 카페 계열과 갈리면 격차의 원인을 말할 수 없다
  * 같은 appkey 를 쓰면 다른 계좌의 토큰·초당 한도를 깎아 15:28 주문이 조용히 죽는다
  * 자격증명이 없는데 조용히 다른 계좌로 도는 것
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from app.api.config import settings
from app.api.db import (COOL_ACCOUNT_ID, STRATEGY_CAFE, STRATEGY_CAFECOOL,
                        STRATEGY_CAFEREAL, STRATEGY_COOLREAL)
from app.api.services import kis_client as kc
from app.api.services import live_trader as lt


def test_strategy_code_fits_the_column():
    # Order.strategy 는 String(8). 넘치면 조용히 잘려 다른 전략과 섞인다.
    assert len(STRATEGY_COOLREAL) <= 8
    assert STRATEGY_COOLREAL not in (STRATEGY_CAFE, STRATEGY_CAFECOOL,
                                     STRATEGY_CAFEREAL)


def test_registered_as_bracket_and_real():
    assert STRATEGY_COOLREAL in lt.BRACKET_STRATEGIES
    # 실계좌 목록 둘 다에 있어야 잔고를 KIS 에서 읽고 청산이 실주문이 된다.
    assert STRATEGY_COOLREAL in lt.REAL_BRACKET_STRATEGIES
    assert STRATEGY_COOLREAL in lt.REAL_BALANCE_STRATEGIES


def test_exit_rules_identical_to_cafe_family():
    # 진입 조건만 다른 쌍둥이 — 청산이 갈리면 무엇을 재는지 알 수 없다.
    assert lt.EXIT_RULES[STRATEGY_COOLREAL] == lt.EXIT_RULES[STRATEGY_CAFE]
    assert lt.EXIT_RULES[STRATEGY_COOLREAL] == lt.EXIT_RULES[STRATEGY_CAFECOOL]


def test_routed_to_its_own_account():
    # 계좌가 겹치면 한 계좌의 보유가 다른 계좌로 팔린다.
    assert lt._account_for(STRATEGY_COOLREAL) == COOL_ACCOUNT_ID
    assert lt._account_for(STRATEGY_CAFEREAL) != COOL_ACCOUNT_ID


def test_ret20_ceiling_is_configured_separately():
    # 상한이 없으면 cafereal 과 똑같은 전략이 하나 더 생길 뿐이다.
    assert settings.live_coolreal_ret20_max > 30.0  # A패턴 하한 +30% 위여야 후보가 남는다
    # cafecool 과 **별도 키**여야 실계좌 쪽만 조정해도 동결 곡선이 안 흔들린다.
    assert hasattr(settings, "live_cafecool_ret20_max")


def test_entry_path_applies_the_ceiling_and_is_real():
    import inspect
    from app.api.services.market_screener import submit_coolreal_orders
    src = inspect.getsource(submit_coolreal_orders)
    assert "live_coolreal_ret20_max" in src
    assert "real=True" in src


def test_missing_credentials_raise_not_configured(monkeypatch):
    kc._clients.pop(kc.ACCOUNT_COOL, None)
    monkeypatch.setattr(kc.settings, "kis_cool_app_key", "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cool_app_secret", "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cool_account_no", "", raising=False)
    with pytest.raises(kc.AccountNotConfigured):
        kc._build_client(kc.ACCOUNT_COOL)


@pytest.mark.parametrize("field", ["kis_app_key", "kis_cafe_app_key"])
def test_appkey_shared_with_another_account_is_refused(monkeypatch, field):
    # KIS 한도는 appkey 단위다. 계좌가 셋이 되면 "다른 하나와만 다른" 키를
    # 발급받는 실수가 쉬워진다 — 둘 다와 비교해야 한다.
    kc._clients.pop(kc.ACCOUNT_COOL, None)
    monkeypatch.setattr(kc.settings, "kis_app_key", "OTHER", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_app_key", "OTHER2", raising=False)
    monkeypatch.setattr(kc.settings, field, "SAME", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cool_app_key", "SAME", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cool_app_secret", "s", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cool_account_no", "12345678-01",
                        raising=False)
    with pytest.raises(kc.AccountNotConfigured, match="appkey"):
        kc._build_client(kc.ACCOUNT_COOL)


def test_frozen_simulated_twins_stay_simulated():
    # cafecool 이 실주문 경로로 넘어가면 동결된 곡선이 깨진다.
    for s in ("cafe", "cafecool", "cafeopen", "close", "flow", "trail",
              "scale", "limit", "surge"):
        assert s not in lt.REAL_BRACKET_STRATEGIES
        assert s not in lt.REAL_BALANCE_STRATEGIES
