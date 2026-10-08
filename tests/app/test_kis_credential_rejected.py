"""KIS 가 계좌 **키 자체**를 거부하면 기다리지 않고 그 계좌만 건너뛰고, 알린다.

2026-10-06 실사고. cafe 계좌 토큰 발급이 `403 EGW00105 유효하지 않은 AppSecret`
으로 거부됐다. `_ensure_token` 은 403 을 발급 1분 제한(EGW00133)으로 보고
5초+65초를 기다린 뒤 재발급했고, 대사 태스크는 autoretry 로 그걸 네 번 반복했다.
운영 워커는 2슬롯뿐이라 ~4.6분 동안 한 슬롯이 남의 계좌 태스크 자리를 잡고 있었다.
그리고 알림이 없어서 하루 넘게 아무도 몰랐다.
"""

import pytest

pytest.importorskip("requests")

from app.api.services import kis_client as kc  # noqa: E402

REJECT_TEXT = '{"error_description":"유효하지 않은 AppSecret입니다.","error_code":"EGW00105"}'
THROTTLE_TEXT = '{"error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)","error_code":"EGW00133"}'


class _Resp:
    def __init__(self, status_code, text="", body=None):
        self.status_code = status_code
        self.text = text
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code != 200:
            raise kc.requests.HTTPError(f"{self.status_code}")


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False, px=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def env(monkeypatch):
    r = FakeRedis()
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="50160169-01")
    c.account_label = "카페"
    monkeypatch.setattr(c, "_redis", lambda: r)
    sleeps, posts, alerts = [], [], []
    monkeypatch.setattr(kc.time, "sleep", lambda s: sleeps.append(s))

    from app.api.services import notify
    monkeypatch.setattr(notify, "notify_account_rejected",
                        lambda label, reason: alerts.append((label, reason)) or True)
    return c, r, sleeps, posts, alerts


def _post_returning(posts, resp):
    def _post(*a, **kw):
        posts.append(1)
        return resp
    return _post


def test_rejected_key_gives_up_at_once(env, monkeypatch):
    c, _r, sleeps, posts, _alerts = env
    monkeypatch.setattr(kc.requests, "post", _post_returning(posts, _Resp(403, REJECT_TEXT)))
    with pytest.raises(kc.AccountRejected) as e:
        c._ensure_token()
    assert sleeps == []          # 70초 대기 없음
    assert len(posts) == 1       # 재발급 시도 없음
    assert "카페" in str(e.value)
    from app.api.services import notify
    assert notify.KEY_REJECTED_MARK in str(e.value)   # 결과 알림 중복 억제가 이 표지를 본다


def test_rejection_is_skippable_like_missing_credentials(env, monkeypatch):
    # 주문·대사 호출부는 AccountNotConfigured 를 "오늘 건너뜀"으로 처리한다.
    c, _r, _s, posts, _a = env
    monkeypatch.setattr(kc.requests, "post", _post_returning(posts, _Resp(403, REJECT_TEXT)))
    with pytest.raises(kc.AccountNotConfigured):
        c._ensure_token()


def test_real_throttle_still_waits_it_out(env, monkeypatch):
    # 회귀 가드: 진짜 발급 제한(EGW00133)은 기존대로 5s/65s 를 기다려 재발급한다.
    c, _r, sleeps, posts, alerts = env
    seq = iter([_Resp(403, THROTTLE_TEXT),
                _Resp(200, body={"access_token": "T", "expires_in": 86400})])

    def _post(*a, **kw):
        posts.append(1)
        return next(seq)

    monkeypatch.setattr(kc.requests, "post", _post)
    assert c._ensure_token() == "T"
    assert sleeps == [5]
    assert alerts == []


def test_alert_once_per_key_per_day(env, monkeypatch):
    c, _r, _s, posts, alerts = env
    monkeypatch.setattr(kc.requests, "post", _post_returning(posts, _Resp(403, REJECT_TEXT)))
    for _ in range(3):
        with pytest.raises(kc.AccountRejected):
            c._ensure_token()
    assert len(alerts) == 1
    assert alerts[0][0] == "카페"
    assert "EGW00105" in alerts[0][1]


def test_reconcile_returns_no_account_instead_of_raising(monkeypatch):
    pytest.importorskip("sqlalchemy")
    from app.api.db import STRATEGY_CAFEREAL
    from app.api.services import live_trader as lt

    class _Rejecting:
        is_mock = False

        def get_daily_fills(self, *a, **kw):
            raise kc.AccountRejected("카페 계좌 KIS 키 거부 — EGW00105")

    monkeypatch.setattr(lt, "init_db", lambda: None)
    res = lt.reconcile_fills(strategy=STRATEGY_CAFEREAL, client=_Rejecting())
    assert res["status"] == "no_account"
    assert "EGW00105" in res["reason"]


def test_get_kis_client_labels_the_account(monkeypatch):
    monkeypatch.setattr(kc, "_build_client",
                        lambda account: kc.KISClient(env="mock", app_key="", app_secret="",
                                                     account_no="1-01"))
    monkeypatch.setattr(kc, "_invalidate_if_stale", lambda: None)
    monkeypatch.setattr(kc, "_clients", {})
    assert kc.get_kis_client(kc.ACCOUNT_CAFE).account_label == "카페"

