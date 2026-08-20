"""cafereal — cafe 를 실계좌로 돌리는 전략. 계약을 고정한다.

2026-08-20: 카페 픽을 실제로 사보기로 했다. cafe(시뮬) 곡선은 그대로 두고 별도
전략·별도 계좌로 돌려, **두 곡선의 격차가 곧 체결 가정이 부풀린 크기**가 되게 한다.
호가 스냅샷 8건이 전부 상한가·매도잔량 0이었던 질문에 답할 유일한 방법이다.

여기서 막는 사고:
  * 규칙이 cafe 와 갈리면 격차의 원인을 말할 수 없다 → 청산·시드 동일성 고정
  * 같은 appkey 를 쓰면 09:00 실계좌 주문과 토큰·한도를 서로 깎는다 → 거부 확인
  * 자격증명이 없는데 조용히 모의로 도는 것 → AccountNotConfigured 확인
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from app.api.db import STRATEGY_CAFE, STRATEGY_CAFEREAL
from app.api.services import kis_client as kc
from app.api.services import live_trader as lt


def test_strategy_code_fits_the_column():
    assert len(STRATEGY_CAFEREAL) <= 8       # Order.strategy 는 String(8)
    assert STRATEGY_CAFEREAL != STRATEGY_CAFE


def test_registered_as_bracket_and_real():
    assert STRATEGY_CAFEREAL in lt.BRACKET_STRATEGIES
    # 실계좌 목록에 있어야 잔고를 KIS 에서 읽고 청산이 실주문이 된다.
    assert STRATEGY_CAFEREAL in lt.REAL_BRACKET_STRATEGIES


def test_exit_rules_identical_to_cafe():
    # 청산이 갈리면 두 곡선의 차이가 체결 때문인지 규칙 때문인지 알 수 없다.
    assert lt.EXIT_RULES[STRATEGY_CAFEREAL] == lt.EXIT_RULES[STRATEGY_CAFE]


def test_simulated_curves_are_not_marked_real():
    # cafe·cafecool·cafeopen 이 실주문 경로를 타면 사고다.
    for s in ("cafe", "cafecool", "cafeopen", "close", "flow", "trail",
              "scale", "limit", "surge"):
        assert s not in lt.REAL_BRACKET_STRATEGIES


def test_missing_credentials_raise_not_configured(monkeypatch):
    kc._clients.pop(kc.ACCOUNT_CAFE, None)
    monkeypatch.setattr(kc.settings, "kis_cafe_app_key", "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_app_secret", "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_account_no", "", raising=False)
    with pytest.raises(kc.AccountNotConfigured):
        kc._build_client(kc.ACCOUNT_CAFE)
    assert kc.cafe_account_configured() is False


def test_shared_appkey_is_refused(monkeypatch):
    # KIS 한도는 appkey 단위다. 같은 키를 두 계좌가 쓰면 서로의 토큰을 죽인다.
    kc._clients.pop(kc.ACCOUNT_CAFE, None)
    monkeypatch.setattr(kc.settings, "kis_app_key", "SAME", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_app_key", "SAME", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_app_secret", "s", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_account_no", "12345678-01", raising=False)
    with pytest.raises(kc.AccountNotConfigured, match="appkey"):
        kc._build_client(kc.ACCOUNT_CAFE)


def test_default_account_client_is_unchanged():
    # 기존 호출부는 인자 없이 부른다 — 시그니처가 바뀌어도 main 이 나와야 한다.
    import inspect
    sig = inspect.signature(kc.get_kis_client)
    assert sig.parameters["account"].default == kc.ACCOUNT_MAIN
