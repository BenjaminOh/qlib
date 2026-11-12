"""JWT 를 python-jose → PyJWT 로 옮긴 뒤에도 검증이 그대로 엄격한지(2026-11-12).

python-jose 3.5.0 에 수정 버전 없는 취약점이 있어 옮겼다. 라이브러리를 바꿀 때 가장 흔한
사고는 '검증이 느슨해지는 것'(서명 미검증·만료 무시·alg=none 허용)이라 그것만 본다.
"""

import base64
import json
from datetime import timedelta

import pytest

pytest.importorskip("jwt")
pytest.importorskip("bcrypt")

from app.api.auth import security  # noqa: E402
from app.api.auth.dependencies import JWTError  # noqa: E402


def test_roundtrip_keeps_subject():
    tok = security.create_access_token("benadmin")
    assert isinstance(tok, str)
    assert security.decode_access_token(tok)["sub"] == "benadmin"


def test_expired_token_is_rejected():
    tok = security.create_access_token("benadmin", expires_delta=timedelta(seconds=-5))
    with pytest.raises(JWTError):
        security.decode_access_token(tok)


def test_tampered_payload_is_rejected():
    head, body, sig = security.create_access_token("benadmin").split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["sub"] = "attacker"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(JWTError):
        security.decode_access_token(f"{head}.{forged}.{sig}")


def test_alg_none_is_rejected():
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'benadmin', 'exp': 9999999999})}."
    with pytest.raises(JWTError):
        security.decode_access_token(unsigned)


def test_wrong_secret_is_rejected(monkeypatch):
    tok = security.create_access_token("benadmin")
    monkeypatch.setattr(security.settings, "jwt_secret", "다른-비밀값-" * 4)
    with pytest.raises(JWTError):
        security.decode_access_token(tok)
