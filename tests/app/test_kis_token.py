"""KIS token invalidation handling.

Regression tests for 2026-08-11, when the 09:00 order task lost both buys to
`EGW00123 기간이 만료된 token`. KIS invalidates the previous token the moment a
new one is issued for the same appkey, so a token can die mid-task while its
nominal expiry is still hours away — the two sells at 09:00:03/09:00:05 went
through and the two buys at 09:00:19/09:00:23 did not.

Two independent defects made that unrecoverable:
  1. `_ensure_token` returned the *local* token without consulting redis, so a
     process kept using a token another process had already invalidated.
  2. `place_order` retried throttle rejections only, so a token rejection was
     returned to the caller as a plain order failure.
"""

import json
import time

import pytest

pytest.importorskip("requests")

from app.api.services import kis_client as kc  # noqa: E402


class FakeRedis:
    """Just enough redis for the token cache path."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.deleted = []

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False, px=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def delete(self, key):
        self.deleted.append(key)
        self.data.pop(key, None)


def _client(monkeypatch, redis_obj=None):
    """A non-mock client with the call gate and hashkey neutralised."""
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_redis", lambda: redis_obj)
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_hashkey", lambda body: "HASH")
    return c


def _token_blob(token, ttl=86400):
    return json.dumps({"access_token": token, "expires_at": time.time() + ttl})


# ─── _ensure_token: redis is the authority ──────────────────────────


def test_ensure_token_prefers_redis_over_live_local_token(monkeypatch):
    """The core defect: a local token that has not expired is NOT trustworthy.

    Another process may have issued a new token, which invalidates this one
    server-side while its local expiry still looks fine.
    """
    r = FakeRedis()
    c = _client(monkeypatch, r)
    r.data[c._redis_token_key] = _token_blob("NEW_FROM_OTHER_PROC")
    c._token = "STALE_LOCAL"
    c._token_expires_at = time.time() + 80000  # nowhere near expiry

    def _no_issue(*a, **kw):
        raise AssertionError("must not re-issue when redis has a valid token")

    monkeypatch.setattr(kc.requests, "post", _no_issue)

    assert c._ensure_token() == "NEW_FROM_OTHER_PROC"


def test_ensure_token_falls_back_to_local_when_redis_down(monkeypatch):
    """Redis being unreachable must not force a re-issue on every call."""
    c = _client(monkeypatch, None)  # _redis() -> None
    c._token = "LOCAL_ONLY"
    c._token_expires_at = time.time() + 80000

    def _no_issue(*a, **kw):
        raise AssertionError("must not re-issue while the local token is valid")

    monkeypatch.setattr(kc.requests, "post", _no_issue)

    assert c._ensure_token() == "LOCAL_ONLY"


def test_ensure_token_issues_when_redis_token_is_near_expiry(monkeypatch):
    r = FakeRedis()
    c = _client(monkeypatch, r)
    r.data[c._redis_token_key] = _token_blob("ALMOST_DEAD", ttl=60)

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "FRESH", "expires_in": 86400}

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(kc.requests, "post", lambda *a, **kw: Resp())

    assert c._ensure_token() == "FRESH"
    assert json.loads(r.data[c._redis_token_key])["access_token"] == "FRESH"


# ─── place_order: recover from a token rejection ────────────────────


class _OrderResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


_TOKEN_REJECT = {"rt_cd": "1", "msg1": "기간이 만료된 token 입니다.", "msg_cd": "EGW00123"}
_OK = {
    "rt_cd": "0",
    "msg_cd": "40590000",
    "msg1": "모의투자 매수주문이 완료 되었습니다.",
    "output": {"KRX_FWDG_ORD_ORGNO": "00950", "ODNO": "0000001099"},
}


def test_place_order_reauths_and_retries_on_expired_token(monkeypatch):
    """A token rejection means the order was never accepted — retry is safe."""
    c = _client(monkeypatch, FakeRedis())
    monkeypatch.setattr(c, "_ensure_token", lambda: "T")
    monkeypatch.setattr(time, "sleep", lambda s: None)

    dropped = []
    monkeypatch.setattr(c, "_drop_token", lambda: dropped.append(True))

    responses = [_OrderResp(_TOKEN_REJECT), _OrderResp(_OK)]
    monkeypatch.setattr(kc.requests, "post", lambda *a, **kw: responses.pop(0))

    res = c.place_order("036420", "BUY", 633)

    assert res.ok is True
    assert res.order_id == "0000001099"
    assert dropped, "the dead token must be dropped before retrying"


def test_place_order_gives_up_after_three_token_rejections(monkeypatch):
    c = _client(monkeypatch, FakeRedis())
    monkeypatch.setattr(c, "_ensure_token", lambda: "T")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(c, "_drop_token", lambda: None)

    calls = []

    def _always_reject(*a, **kw):
        calls.append(1)
        return _OrderResp(_TOKEN_REJECT)

    monkeypatch.setattr(kc.requests, "post", _always_reject)

    res = c.place_order("036420", "BUY", 633)

    assert res.ok is False
    assert len(calls) == 3
    assert "EGW00123" in res.raw.get("msg_cd", "")


def test_place_order_still_does_not_retry_business_rejections(monkeypatch):
    """Guard: only throttle/token rejections retry. A 잔고부족 must not repeat."""
    c = _client(monkeypatch, FakeRedis())
    monkeypatch.setattr(c, "_ensure_token", lambda: "T")
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(c, "_drop_token", lambda: None)

    calls = []

    def _reject(*a, **kw):
        calls.append(1)
        return _OrderResp({"rt_cd": "1", "msg1": "주문가능금액이 부족합니다.", "msg_cd": "40240000"})

    monkeypatch.setattr(kc.requests, "post", _reject)

    res = c.place_order("036420", "BUY", 633)

    assert res.ok is False
    assert len(calls) == 1, "business rejections must not be retried"
