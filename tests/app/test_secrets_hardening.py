"""Defences added before real-account operation (2026-08-18 security review).

Each test pins a hole that was reachable from outside the process:

  * The shipped placeholder secrets had no guard. docker-compose passes
    jwt_secret/admin_password only through `env_file: .env` — never under
    `environment:` — so one missing line booted silently on a value published
    in this repo, and anyone could mint a valid session cookie.
  * `client_ip` read the FIRST X-Forwarded-For hop, which the client writes.
    Rotating that header gave an attacker a fresh identity per request and
    reset their own lockout every time.
  * The bot token rides in the Telegram URL path, and connection errors quote
    the URL, so a single network blip left it in the container log.
  * The balance cache keyed redis on the raw 8-digit account number, while the
    token keys next to it were deliberately hashed for exactly that reason.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("requests")


# ─── startup secret guard ───────────────────────────────────────────


def test_placeholder_jwt_secret_refuses_to_start(monkeypatch):
    from app.api import main

    monkeypatch.setattr(main.settings, "jwt_secret", main.INSECURE_PLACEHOLDER)

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        main._assert_secrets_configured()


def test_placeholder_admin_password_blocks_real_env(monkeypatch):
    from app.api import main

    monkeypatch.setattr(main.settings, "jwt_secret", "a-real-64-char-secret")
    monkeypatch.setattr(main.settings, "admin_password", main.INSECURE_PLACEHOLDER)
    monkeypatch.setattr(main.settings, "kis_env", "real")

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        main._assert_secrets_configured()


def test_placeholder_admin_password_allowed_on_paper(monkeypatch):
    """A dev checkout must still run — the guard is about real money."""
    from app.api import main

    monkeypatch.setattr(main.settings, "jwt_secret", "a-real-64-char-secret")
    monkeypatch.setattr(main.settings, "admin_password", main.INSECURE_PLACEHOLDER)
    monkeypatch.setattr(main.settings, "kis_env", "paper")

    main._assert_secrets_configured()   # must not raise


def test_configured_secrets_pass(monkeypatch):
    from app.api import main

    monkeypatch.setattr(main.settings, "jwt_secret", "x" * 64)
    monkeypatch.setattr(main.settings, "admin_password", "a-real-password")
    monkeypatch.setattr(main.settings, "kis_env", "real")

    main._assert_secrets_configured()


# ─── telegram token must not reach the log ──────────────────────────


def test_connection_error_text_is_scrubbed(monkeypatch):
    from app.api.services import notify

    token = "123456:AAE-supersecret-bot-token"
    monkeypatch.setattr(notify.settings, "telegram_bot_token", token)

    exc = ConnectionError(
        f"HTTPSConnectionPool(host='api.telegram.org'): "
        f"Max retries exceeded with url: /bot{token}/sendMessage")

    scrubbed = notify._scrub(exc)

    assert token not in scrubbed, "봇 토큰이 로그에 평문으로 남으면 안 된다"
    assert "<token>" in scrubbed
    assert "api.telegram.org" in scrubbed, "진단에 필요한 문맥은 남아야 한다"


def test_scrub_is_safe_when_no_token_configured(monkeypatch):
    from app.api.services import notify

    monkeypatch.setattr(notify.settings, "telegram_bot_token", "")
    assert notify._scrub(RuntimeError("boom")) == "boom"


# ─── account number must not appear in redis key names ──────────────


def test_balance_keys_do_not_contain_the_account_number(monkeypatch):
    from app.api.services import balance_cache as bc

    cano = "50199531"

    class _Client:
        env = "paper"

    _Client.cano = cano
    monkeypatch.setattr(bc, "get_kis_client", lambda account="main": _Client())

    for key in bc._keys():
        assert cano not in key, f"계좌번호가 redis 키에 노출됨: {key}"


def test_different_accounts_get_different_balance_keys(monkeypatch):
    """Hashing must not collapse two accounts onto one cache entry."""
    from app.api.services import balance_cache as bc

    def _keys_for(cano):
        class _C:
            env = "paper"
        _C.cano = cano
        monkeypatch.setattr(bc, "get_kis_client", lambda account="main": _C())
        return bc._keys()

    assert _keys_for("11111111")[0] != _keys_for("22222222")[0]


# ─── health must not advertise internal paths ───────────────────────


def test_health_does_not_leak_provider_uri():
    """Unauthenticated endpoint — it should report liveness, not filesystem."""
    from app.api.main import health_check

    body = health_check()

    assert body.status == "ok"
    assert body.provider_uri is None, "무인증 엔드포인트가 내부 경로를 노출하면 안 된다"
