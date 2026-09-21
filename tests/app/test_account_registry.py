"""계좌를 **코드 수정 없이** 늘릴 수 있다는 사실을 고정한다.

2026-09-21 이전에는 계좌 하나를 추가하려면 `_build_client` 에 25줄짜리 분기를
복제하고, appkey 중복 비교쌍을 손으로 나열해야 했다. 비교쌍은 계좌 수의 제곱으로
불어나는데 그걸 사람이 관리하면 반드시 하나를 빠뜨린다 — 그리고 빠뜨린 결과는
조용하다: 같은 appkey 를 쓰는 두 계좌가 서로의 토큰을 무효화해 15:28 주문이 죽는다.

그래서 레지스트리로 바꿨고, 이 파일은 그 레지스트리가 **실제로 동작하는지**를 본다.
"통과하니까 됐다"가 아니라 새 슬롯으로 클라이언트를 만들어 보고, 중복 검사가
새 계좌에도 걸리는지 확인한다.
"""

import pytest

pytest.importorskip("pydantic_settings")
pytest.importorskip("requests")

from app.api.services import kis_client as kc

SLOT = "acct1"          # 추가 슬롯 중 첫 번째
PREFIX = "kis_acct1"


def _clear(monkeypatch, **fields):
    """슬롯 자격증명을 주입한다. 캐시된 클라이언트는 먼저 버린다."""
    kc._clients.pop(SLOT, None)
    for k, v in fields.items():
        monkeypatch.setattr(kc.settings, f"{PREFIX}_{k}", v, raising=False)


def test_registry_and_prefix_map_agree():
    """둘이 어긋나면 `_build_client` 가 KeyError 로 죽는다 — 중복 검사 루프에서."""
    assert set(kc.ALL_ACCOUNTS) == set(kc._ACCOUNT_PREFIX)
    assert len(kc.ALL_ACCOUNTS) == len(set(kc.ALL_ACCOUNTS)), "계좌 id 가 중복됐다"


def test_extra_slot_builds_a_client_from_settings_alone(monkeypatch):
    """코드 변경 0줄 — env 다섯 줄이면 계좌가 산다."""
    _clear(monkeypatch, env="paper", app_key="SLOT-KEY", app_secret="s",
           account_no="12345678-01", account_product="")
    # 다른 계좌와 키가 겹치지 않아야 통과한다
    monkeypatch.setattr(kc.settings, "kis_app_key", "MAIN-KEY", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_app_key", "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cool_app_key", "", raising=False)

    c = kc._build_client(SLOT)

    assert c.app_key == "SLOT-KEY"
    assert c.cano == "12345678"
    assert c.acnt_prdt_cd == "01"
    assert c.env == "paper"


def test_missing_credentials_name_the_env_vars(monkeypatch):
    """화면에 뜨는 사유 문구다 — 사람이 보고 어느 env 를 채울지 알아야 한다."""
    _clear(monkeypatch, app_key="", app_secret="", account_no="")

    with pytest.raises(kc.AccountNotConfigured) as e:
        kc._build_client(SLOT)

    msg = str(e.value)
    assert "KIS_ACCT1_APP_KEY" in msg
    assert "KIS_ACCT1_APP_SECRET" in msg
    assert "KIS_ACCT1_ACCOUNT_NO" in msg


@pytest.mark.parametrize("other_field", ["kis_app_key", "kis_cafe_app_key",
                                         "kis_cool_app_key"])
def test_duplicate_appkey_is_caught_against_every_account(monkeypatch, other_field):
    """새 계좌도 **전 계좌**와 비교된다.

    예전 구조에서는 비교 대상을 손으로 적었기 때문에, 계좌를 추가하면 그 계좌와의
    비교가 자동으로 생기지 않았다. 이 테스트는 파라미터를 늘리지 않아도
    레지스트리가 새 계좌를 포함하는지를 본다.
    """
    _clear(monkeypatch, app_key="SAME", app_secret="s", account_no="12345678-01")
    for f in ("kis_app_key", "kis_cafe_app_key", "kis_cool_app_key"):
        monkeypatch.setattr(kc.settings, f, "OTHER-" + f, raising=False)
    monkeypatch.setattr(kc.settings, other_field, "SAME", raising=False)

    with pytest.raises(kc.AccountNotConfigured, match="appkey"):
        kc._build_client(SLOT)


def test_unknown_account_still_fails_loudly():
    """오타는 조용히 main 으로 흘러가면 안 된다 — 남의 계좌에 주문이 나간다."""
    with pytest.raises(ValueError, match="unknown account"):
        kc._build_client("nope")


def test_main_stays_credential_free(monkeypatch):
    """main 은 자격증명이 없어도 mock 모드로 떠야 한다.

    일반화하면서 main 까지 `AccountNotConfigured` 로 만들면 개발 환경이 통째로
    죽는다 — 그래서 main 분기는 일부러 남겼다.
    """
    monkeypatch.setattr(kc.settings, "kis_app_key", "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_app_secret", "", raising=False)
    kc._clients.pop(kc.ACCOUNT_MAIN, None)

    c = kc._build_client(kc.ACCOUNT_MAIN)

    assert c.is_mock is True
