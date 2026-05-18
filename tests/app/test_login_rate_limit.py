"""IP-based brute-force throttle for /auth/login.

Validates the Redis interaction layer with a hand-rolled stub — no extra
dependency. Behavioural invariants tested:
  - fail counter is per-IP and TTL'd
  - hitting fail_threshold flips to lockout with the right TTL
  - hitting burst_threshold uses the longer lockout
  - successful login clears the per-IP fail key
  - distinct IPs do not contaminate each other
  - Redis errors fail open (no exception, no lockout)
"""

from __future__ import annotations

import pytest

pytest.importorskip("redis")
pytest.importorskip("fastapi")

import redis as redis_module  # noqa: E402

from app.api.auth import rate_limit  # noqa: E402
from app.api.config import settings  # noqa: E402


class FakeRedis:
    """Minimal Redis stand-in supporting incr/expire/ttl/set/delete/get."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def incr(self, key):
        v = int(self.store.get(key, "0")) + 1
        self.store[key] = str(v)
        return v

    def expire(self, key, sec):
        if key in self.store:
            self.ttls[key] = int(sec)

    def ttl(self, key):
        if key not in self.store:
            return -2
        return self.ttls.get(key, -1)

    def set(self, key, value, ex=None):
        self.store[key] = str(value)
        if ex is not None:
            self.ttls[key] = int(ex)

    def get(self, key):
        return self.store.get(key)

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                self.store.pop(k, None)
                self.ttls.pop(k, None)
                n += 1
        return n


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", fake)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: fake)
    return fake


def test_first_failure_sets_counter_and_window_ttl(fake_redis):
    rate_limit.record_failure("1.2.3.4", username="admin")
    assert fake_redis.store["rl:login:fail:1.2.3.4"] == "1"
    assert fake_redis.ttls["rl:login:fail:1.2.3.4"] == settings.login_fail_window_sec
    # Forensic per-(ip,username) key written too
    assert fake_redis.store["rl:login:fail:1.2.3.4:admin"] == "1"


def test_threshold_flips_to_lockout(fake_redis):
    for _ in range(settings.login_fail_threshold):
        rate_limit.record_failure("1.2.3.4", username="admin")
    # Fail counter cleared, lockout key set with correct TTL
    assert "rl:login:fail:1.2.3.4" not in fake_redis.store
    assert fake_redis.store["rl:login:lock:1.2.3.4"] == "1"
    assert fake_redis.ttls["rl:login:lock:1.2.3.4"] == settings.login_lockout_sec
    assert rate_limit.check_locked("1.2.3.4") == settings.login_lockout_sec


def test_burst_threshold_uses_longer_lockout(fake_redis):
    for _ in range(settings.login_ip_burst_threshold):
        rate_limit.record_failure("1.2.3.4", username="admin")
    assert fake_redis.ttls["rl:login:lock:1.2.3.4"] == settings.login_ip_burst_lockout_sec


def test_success_clears_fail_counter(fake_redis):
    rate_limit.record_failure("1.2.3.4", username="admin")
    rate_limit.record_failure("1.2.3.4", username="admin")
    assert fake_redis.store["rl:login:fail:1.2.3.4"] == "2"
    rate_limit.record_success("1.2.3.4")
    assert "rl:login:fail:1.2.3.4" not in fake_redis.store


def test_check_locked_returns_none_when_no_key(fake_redis):
    assert rate_limit.check_locked("1.2.3.4") is None


def test_distinct_ips_are_isolated(fake_redis):
    for _ in range(settings.login_fail_threshold):
        rate_limit.record_failure("1.2.3.4", username="admin")
    # Different IP — still has zero history, not locked
    assert rate_limit.check_locked("5.6.7.8") is None


def test_redis_error_in_record_failure_fails_open(monkeypatch):
    class Boom:
        def incr(self, *a, **kw):
            raise redis_module.RedisError("down")
        def expire(self, *a, **kw):
            raise redis_module.RedisError("down")
        def set(self, *a, **kw):
            raise redis_module.RedisError("down")
        def delete(self, *a, **kw):
            raise redis_module.RedisError("down")
        def ttl(self, *a, **kw):
            raise redis_module.RedisError("down")

    monkeypatch.setattr(rate_limit, "_redis", Boom())
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: Boom())
    # Must not raise
    rate_limit.record_failure("1.2.3.4", username="admin")
    assert rate_limit.check_locked("1.2.3.4") is None


def test_client_ip_prefers_xff(monkeypatch):
    class Req:
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
        client = type("C", (), {"host": "10.0.0.1"})()
    assert rate_limit.client_ip(Req()) == "203.0.113.7"


def test_client_ip_falls_back_to_peer(monkeypatch):
    class Req:
        headers = {}
        client = type("C", (), {"host": "10.0.0.1"})()
    assert rate_limit.client_ip(Req()) == "10.0.0.1"
