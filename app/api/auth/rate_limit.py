"""Login brute-force protection — IP-based sliding-window counter in Redis.

Fail-open on Redis errors: if Redis is unreachable, callers proceed without
rate limiting rather than lock everyone out. The login endpoint stays
available; only the throttling layer degrades.
"""

from __future__ import annotations

import logging

import redis
from fastapi import Request

from ..config import settings

log = logging.getLogger(__name__)

_FAIL_KEY = "rl:login:fail:{ip}"
_LOCK_KEY = "rl:login:lock:{ip}"
# Per-(ip, username) counter is forensic-only — never consulted for blocking.
# Useful for spotting which accounts an attacker targets.
_FAIL_USER_KEY = "rl:login:fail:{ip}:{username}"

_redis: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(
            settings.celery_broker_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis


def client_ip(request: Request) -> str:
    """Best-effort client IP. Trusts the first X-Forwarded-For hop (nginx-proxy
    + nginx-waf both rewrite this header), falling back to the direct peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_locked(ip: str) -> int | None:
    """Returns lock TTL (seconds) if `ip` is currently locked out, else None."""
    try:
        ttl = _get_redis().ttl(_LOCK_KEY.format(ip=ip))
    except redis.RedisError:
        log.warning("rate_limit.check_locked: redis unavailable; fail-open", exc_info=True)
        return None
    return ttl if isinstance(ttl, int) and ttl > 0 else None


def record_failure(ip: str, username: str | None = None) -> None:
    """Increment per-IP fail counter; promote to lockout when threshold crossed."""
    try:
        r = _get_redis()
        key = _FAIL_KEY.format(ip=ip)
        count = r.incr(key)
        if count == 1:
            r.expire(key, settings.login_fail_window_sec)
        if username:
            user_key = _FAIL_USER_KEY.format(ip=ip, username=username)
            r.incr(user_key)
            r.expire(user_key, settings.login_fail_window_sec)

        if count >= settings.login_ip_burst_threshold:
            r.set(_LOCK_KEY.format(ip=ip), "1",
                  ex=settings.login_ip_burst_lockout_sec)
            r.delete(key)
            log.warning(
                "rate_limit: ip=%s burst-locked for %ds (count=%d)",
                ip, settings.login_ip_burst_lockout_sec, count,
            )
        elif count >= settings.login_fail_threshold:
            r.set(_LOCK_KEY.format(ip=ip), "1",
                  ex=settings.login_lockout_sec)
            r.delete(key)
            log.warning(
                "rate_limit: ip=%s locked for %ds (count=%d)",
                ip, settings.login_lockout_sec, count,
            )
    except redis.RedisError:
        log.warning("rate_limit.record_failure: redis unavailable; fail-open", exc_info=True)


def record_success(ip: str) -> None:
    """Clear the per-IP fail counter on a successful login."""
    try:
        _get_redis().delete(_FAIL_KEY.format(ip=ip))
    except redis.RedisError:
        log.warning("rate_limit.record_success: redis unavailable; ignoring", exc_info=True)
