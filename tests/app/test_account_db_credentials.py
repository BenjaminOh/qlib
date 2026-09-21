"""계좌 자격증명을 **DB 에서** 읽는 경로 (2026-09-21).

계좌를 늘릴 때마다 서버 `.env` 를 고치고 재시작해야 하면 10계좌 운영이 안 된다.
그래서 `trading_accounts` 에 암호문으로 넣고 웹에서 등록하게 했다.

이 파일이 지키는 것 중 가장 중요한 하나:

    **복호화가 실패하면 env 로 물러나지 않는다.**

DB 에 자격증명이 있는데 키가 맞지 않아 조용히 env 폴백이 되면, 그 계좌 자리에
**다른 계좌의 키**가 들어가 주문이 엉뚱한 계좌로 나간다. 화면에는 정상으로 보인다.
DB 에 닿지 못하는 것(테이블 없음·연결 실패)만 진짜 폴백이다.
"""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("cryptography")
pytest.importorskip("requests")

from cryptography.fernet import Fernet  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.api.db as dbmod  # noqa: E402
from app.api.db import Base, TradingAccount  # noqa: E402
from app.api.services import kis_client as kc  # noqa: E402
from app.api.services import secrets as S  # noqa: E402

KEY = Fernet.generate_key().decode()
SLOT = "acct1"


class _NoClose:
    """`with SessionLocal() as db:` 를 테스트 세션으로 받아준다."""

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


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    kc._clients.clear()
    S._cached_key = None
    S._cached_fernet = None
    monkeypatch.setattr(S.settings, "secrets_key", KEY, raising=False)
    # env 슬롯은 비워 둔다 — 폴백이 일어나는지 여부를 분명히 보기 위해
    for f in ("kis_acct1_app_key", "kis_acct1_app_secret", "kis_acct1_account_no"):
        monkeypatch.setattr(kc.settings, f, "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_app_key", "MAIN-KEY", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cafe_app_key", "", raising=False)
    monkeypatch.setattr(kc.settings, "kis_cool_app_key", "", raising=False)
    yield
    kc._clients.clear()


def _row(session, **over):
    kw = dict(account_id=SLOT, label="지인 A",
              kis_env="paper", account_no="11112222-01", account_product="01",
              app_key_enc=S.encrypt("DB-KEY"), app_secret_enc=S.encrypt("DB-SECRET"),
              enabled=True)
    kw.update(over)
    session.add(TradingAccount(**kw))
    session.commit()


def test_db_credentials_are_used(session):
    """웹에서 등록하면 env 없이도 계좌가 산다 — 재시작 없이."""
    _row(session)

    c = kc._build_client(SLOT)

    assert c.app_key == "DB-KEY"
    assert c.cano == "11112222"
    assert c.acnt_prdt_cd == "01"
    assert c.env == "paper"


def test_db_wins_over_env(session, monkeypatch):
    """둘 다 있으면 DB 가 이긴다 — 웹에서 바꾼 값이 반영돼야 한다."""
    monkeypatch.setattr(kc.settings, "kis_acct1_app_key", "ENV-KEY", raising=False)
    monkeypatch.setattr(kc.settings, "kis_acct1_app_secret", "s", raising=False)
    monkeypatch.setattr(kc.settings, "kis_acct1_account_no", "99998888-01",
                        raising=False)
    _row(session)

    c = kc._build_client(SLOT)

    assert c.app_key == "DB-KEY", "env 가 DB 를 덮었다"
    assert c.cano == "11112222"


def test_decrypt_failure_refuses_instead_of_falling_back(session, monkeypatch):
    """**이 파일의 핵심.** 키가 어긋나면 멈춘다 — 남의 계좌로 주문이 나가면 안 된다."""
    monkeypatch.setattr(kc.settings, "kis_acct1_app_key", "ENV-KEY", raising=False)
    monkeypatch.setattr(kc.settings, "kis_acct1_app_secret", "s", raising=False)
    monkeypatch.setattr(kc.settings, "kis_acct1_account_no", "99998888-01",
                        raising=False)
    _row(session)

    # 키 교체 — 저장된 암호문을 더는 풀 수 없다
    S._cached_key = None
    monkeypatch.setattr(S.settings, "secrets_key", Fernet.generate_key().decode(),
                        raising=False)

    with pytest.raises(kc.AccountNotConfigured, match="복호화"):
        kc._build_client(SLOT)


def test_disabled_account_is_refused(session):
    """자격증명을 지우지 않고 잠시 끄는 스위치. 꺼진 계좌로 주문이 나가면 안 된다."""
    _row(session, enabled=False)

    with pytest.raises(kc.AccountNotConfigured, match="꺼져"):
        kc._build_client(SLOT)


def test_policy_only_row_falls_back_to_env(session, monkeypatch):
    """주문 정책만 있고 자격증명이 없는 행은 폴백 대상이다 — 기존 main·cafe 가 그렇다."""
    monkeypatch.setattr(kc.settings, "kis_acct1_app_key", "ENV-KEY", raising=False)
    monkeypatch.setattr(kc.settings, "kis_acct1_app_secret", "s", raising=False)
    monkeypatch.setattr(kc.settings, "kis_acct1_account_no", "99998888-01",
                        raising=False)
    _row(session, app_key_enc=None, app_secret_enc=None, account_no=None)

    c = kc._build_client(SLOT)

    assert c.app_key == "ENV-KEY"


def test_duplicate_appkey_across_db_and_env_is_refused(session, monkeypatch):
    """env 와 DB 를 **합쳐** 비교하지 않으면 같은 키가 두 계좌에 들어간다."""
    monkeypatch.setattr(kc.settings, "kis_app_key", "DB-KEY", raising=False)
    _row(session)

    with pytest.raises(kc.AccountNotConfigured, match="appkey"):
        kc._build_client(SLOT)


def test_all_accounts_includes_db_rows(session):
    """화면·검증이 쓰는 목록은 웹에서 방금 만든 계좌도 알아야 한다."""
    _row(session, account_id="friendB")

    accounts = kc.all_accounts()

    assert "friendB" in accounts
    assert kc.ACCOUNT_MAIN in accounts, "기존 계좌가 사라졌다"


def test_rev_bump_clears_the_client_cache(monkeypatch):
    """세 프로세스가 옛 클라이언트를 붙들지 않게 하는 장치."""
    store = {}

    class _FakeRedis:
        # 실제 redis 는 bytes 를 돌려준다. 값을 int 로 들고 있다가 읽을 때만
        # bytes 로 바꾼다 — 처음에 int(b"0") 로 썼다가 TypeError 가 나고,
        # bump 쪽 except 가 그걸 삼켜 리비전이 안 올라갔다.
        def incr(self, k):
            store[k] = store.get(k, 0) + 1

        def get(self, k):
            v = store.get(k)
            return None if v is None else str(v).encode()

    monkeypatch.setattr(kc, "_accounts_redis", lambda: _FakeRedis())
    kc._seen_rev = None
    kc._rev_checked_at = 0.0
    kc._clients["sentinel"] = object()

    kc._invalidate_if_stale()          # 최초 관측 — 비우지 않는다
    assert "sentinel" in kc._clients

    kc.bump_accounts_rev()
    kc._rev_checked_at = 0.0           # 쉼표를 건너뛴다
    kc._invalidate_if_stale()

    assert "sentinel" not in kc._clients, "리비전이 바뀌었는데 캐시가 남았다"
