"""자격증명 암호화 — **조용히 실패하지 않는다**는 것이 요점이다.

계좌 자격증명을 DB 에 두기로 하면서(2026-09-21) 가장 위험한 실패는 예외가 아니라
침묵이다: 키가 없는데 빈 문자열을 돌려주면 평문이 흘러들거나, 복호화가 깨졌는데
빈 값을 주면 "자격증명 없는 계좌"로 읽혀 그날 그 계좌의 거래가 통째로 건너뛰어진다.
둘 다 화면에는 아무 일도 없어 보인다.
"""

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("pydantic_settings")

from cryptography.fernet import Fernet, InvalidToken

from app.api.services import secrets as S

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset():
    """모듈 캐시를 비운다 — 테스트가 키를 갈아끼우므로."""
    S._cached_key = None
    S._cached_fernet = None
    yield
    S._cached_key = None
    S._cached_fernet = None


def test_roundtrip(monkeypatch):
    monkeypatch.setattr(S.settings, "secrets_key", KEY, raising=False)
    plain = "PSYuRT17NUgfuprLAyQKSZ0vXW7VKL8nnn8V"

    token = S.encrypt(plain)

    assert token != plain, "암호문이 평문과 같다"
    assert plain not in token, "암호문에 평문이 그대로 들어 있다"
    assert S.decrypt(token) == plain


def test_missing_key_refuses_to_encrypt(monkeypatch):
    """키가 없으면 **저장을 거부**한다 — 평문으로 흘러드는 것이 최악이다."""
    monkeypatch.setattr(S.settings, "secrets_key", "", raising=False)

    with pytest.raises(S.SecretsKeyMissing):
        S.encrypt("secret")
    assert S.available() is False


def test_wrong_key_raises_instead_of_returning_empty(monkeypatch):
    """복호화 실패를 빈 값으로 삼키면 '계좌 미설정'으로 오인된다."""
    monkeypatch.setattr(S.settings, "secrets_key", KEY, raising=False)
    token = S.encrypt("secret")

    S._cached_key = None          # 키 교체를 반영시킨다
    monkeypatch.setattr(S.settings, "secrets_key", OTHER_KEY, raising=False)

    with pytest.raises(InvalidToken):
        S.decrypt(token)


def test_empty_values_pass_through(monkeypatch):
    """빈 칸은 암호화 대상이 아니다 — 굳이 키를 요구하지 않는다."""
    monkeypatch.setattr(S.settings, "secrets_key", "", raising=False)
    assert S.encrypt("") == ""
    assert S.decrypt("") == ""


def test_mask_hides_the_middle():
    """화면·로그에 쓰는 표시용. 어느 키인지 알아보되 복원은 안 돼야 한다."""
    plain = "PSYuRT17NUgfuprLAyQKSZ0vXW7VKL8nnn8V"

    m = S.mask(plain)

    assert m.startswith("PSYu") and m.endswith("n8V")
    assert "…" in m
    assert len(m) < len(plain)
    # 가운데가 남으면 마스킹이 아니다
    assert "uprLAyQKSZ" not in m


def test_key_change_is_picked_up(monkeypatch):
    """키를 바꾸면 새 키로 동작해야 한다 — 캐시가 옛 키를 붙들면 안 된다."""
    monkeypatch.setattr(S.settings, "secrets_key", KEY, raising=False)
    first = S.encrypt("x")

    monkeypatch.setattr(S.settings, "secrets_key", OTHER_KEY, raising=False)
    second = S.encrypt("x")

    assert first != second, "키를 바꿨는데 같은 암호문이 나왔다 — 캐시가 안 갈렸다"
