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
    """Client IP, counted from the RIGHT of X-Forwarded-For.

    The left end is attacker-controlled. nginx uses
    `proxy_add_x_forwarded_for`, which *appends* the peer it actually saw, so
    a client that sends `X-Forwarded-For: 1.2.3.4` produces `1.2.3.4, <real>`.
    Reading the first hop therefore let anyone rotate a header value and reset
    their own lockout counter on every request.

    Counting from the right instead: with `trusted_proxy_hops = 1` (this
    deployment — see infra/nginx/qlib.tmanager.kr.conf, a single proxy_pass),
    the last entry is the one nginx wrote and cannot be forged. Raise the
    setting if another proxy is put in front, or every request will collapse
    onto that proxy's address and per-IP limiting stops working.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if hops:
            idx = max(len(hops) - settings.trusted_proxy_hops, 0)
            return hops[idx]
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

        # The counter must OUTLIVE the lockout it triggers. Deleting it here —
        # which is what this did — reset the tally to zero every time a lock
        # was applied, so `count` could never exceed login_fail_threshold and
        # the burst tier below was unreachable dead code. Extending the TTL
        # past the lockout lets failures accumulate across cycles:
        # 5 → lock, 10 → lock, … 20 → the long burst lock.
        if count >= settings.login_ip_burst_threshold:
            r.set(_LOCK_KEY.format(ip=ip), "1",
                  ex=settings.login_ip_burst_lockout_sec)
            r.expire(key, settings.login_ip_burst_lockout_sec
                     + settings.login_fail_window_sec)
            log.warning(
                "rate_limit: ip=%s burst-locked for %ds (count=%d)",
                ip, settings.login_ip_burst_lockout_sec, count,
            )
        elif count >= settings.login_fail_threshold:
            r.set(_LOCK_KEY.format(ip=ip), "1",
                  ex=settings.login_lockout_sec)
            r.expire(key, settings.login_lockout_sec
                     + settings.login_fail_window_sec)
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
