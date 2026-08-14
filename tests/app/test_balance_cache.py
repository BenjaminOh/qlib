"""Read-path balance cache + token-storm containment.

Regression tests for 2026-08-14, when the dashboard's 총 평가금액/보유종목 cards
stopped rendering. `/api/v1/live/balance` answered 500 for 86 of 204 requests
over one container lifetime, each after a 10s block.

The chain was:
  1. KIS's paper gateway returned HTTP 500 `EGW00300 Gateway 라우팅 오류`.
  2. `get_balance` treated *any* 500 as token invalidation and dropped the
     SHARED redis token.
  3. The next call re-issued, but KIS caps issuance at ~1/min — so it 403'd or
     read-timed-out at 10s, and nothing was ever written back to redis.
  4. With the token cache permanently empty and no response cache, every 30s
     poll repeated the whole thing, feeding the rate limit that caused it.

Two independent defects, tested separately below: no read-path cache, and a
`_drop_token` trigger far too broad.
"""

import json
import time

import pytest

pytest.importorskip("requests")

from app.api.services import balance_cache as bc  # noqa: E402
from app.api.services import kis_client as kc  # noqa: E402


class FakeRedis:
    """Just enough redis for the snapshot cache, with expiry honoured."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.expiry = {}
        self.deleted = []

    def _live(self, key):
        exp = self.expiry.get(key)
        if exp is not None and exp <= time.time():
            self.data.pop(key, None)
            self.expiry.pop(key, None)
            return False
        return key in self.data

    def get(self, key):
        return self.data.get(key) if self._live(key) else None

    def set(self, key, value, ex=None, nx=False, px=None):
        if nx and self._live(key):
            return None
        self.data[key] = value
        if ex is not None:
            self.expiry[key] = time.time() + ex
        elif px is not None:
            self.expiry[key] = time.time() + px / 1000.0
        return True

    def delete(self, key):
        self.deleted.append(key)
        self.data.pop(key, None)
        self.expiry.pop(key, None)


def _snapshot(total=1_000_000.0):
    return kc.AccountSnapshot(
        cash=400_000.0,
        total_eval=total,
        holdings=[kc.Holding(code="005930", name="삼성전자", qty=10,
                             avg_price=60_000.0, eval_price=62_000.0,
                             eval_value=620_000.0, pnl=20_000.0, pnl_pct=0.033)],
    )


class _Client:
    """Stand-in for KISClient that counts real balance fetches."""

    env = "paper"
    cano = "12345678"
    is_mock = False

    def __init__(self, snapshot=None, exc=None):
        self._snapshot = snapshot
        self._exc = exc
        self.calls = 0

    def get_balance(self):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._snapshot


@pytest.fixture
def wired(monkeypatch):
    """Point balance_cache at a fake redis and a counting client."""
    def _wire(client, redis_obj=None):
        r = FakeRedis() if redis_obj is None else redis_obj
        monkeypatch.setattr(bc, "_redis", lambda: r)
        monkeypatch.setattr(bc, "get_kis_client", lambda: client)
        return r
    return _wire


# ─── The missing cache ──────────────────────────────────────────────


def test_second_read_inside_ttl_does_not_hit_kis(wired):
    """The whole point: /balance, /exits and /stock/trades share one fetch.

    Before this, one dashboard load made three separate KIS balance calls,
    each serialised behind the account-wide 1.2s gate.
    """
    client = _Client(_snapshot())
    wired(client)

    snap1, src1, _ = bc.get_balance_for_read()
    snap2, src2, _ = bc.get_balance_for_read()

    assert client.calls == 1, "second read must be served from the cache"
    assert src1 == "live"
    assert src2 == "cache"
    assert snap2.total_eval == snap1.total_eval
    assert [h.code for h in snap2.holdings] == ["005930"]


def test_cache_expires_after_ttl(wired, monkeypatch):
    client = _Client(_snapshot())
    wired(client)
    monkeypatch.setattr(bc, "_TTL_S", 1)

    bc.get_balance_for_read()
    time.sleep(1.1)
    _, src, _ = bc.get_balance_for_read()

    assert client.calls == 2
    assert src == "live"


# ─── Surviving a KIS outage ─────────────────────────────────────────


def test_kis_failure_falls_back_to_last_known_good(wired):
    """A KIS outage must not blank the card — or raise. This is what turned
    into an HTTP 500 for the dashboard."""
    good = _Client(_snapshot(total=1_234_567.0))
    r = wired(good)
    bc.get_balance_for_read()  # seeds both the fresh and the stale key
    r.delete(bc._keys()[0])    # fresh window elapsed

    broken = _Client(exc=RuntimeError("KIS gateway down"))
    wired(broken, r)

    snap, src, _ = bc.get_balance_for_read()

    assert src == "stale"
    assert snap.total_eval == 1_234_567.0
    assert [h.code for h in snap.holdings] == ["005930"]


def test_repeated_polls_during_an_outage_stop_calling_kis(wired):
    """Serving stale is not enough — the poll must stop paying for the failure.

    With only a 5s fresh window, every 30s poll would otherwise spend another
    failed round trip (up to the 15s HTTP timeout) before falling back, which
    is the block the user sees and the traffic that feeds KIS's rate limit.
    """
    good = _Client(_snapshot())
    r = wired(good)
    bc.get_balance_for_read()
    r.delete(bc._keys()[0])

    broken = _Client(exc=RuntimeError("KIS gateway down"))
    wired(broken, r)

    first = bc.get_balance_for_read()
    second = bc.get_balance_for_read()
    third = bc.get_balance_for_read()

    assert broken.calls == 1, "only the first poll may probe a known-down KIS"
    assert [x[1] for x in (first, second, third)] == ["stale", "stale", "stale"]


def test_recovery_after_the_down_window(wired, monkeypatch):
    """The down marker must expire, not latch."""
    monkeypatch.setattr(bc, "_DOWN_TTL_S", 1)
    good = _Client(_snapshot())
    r = wired(good)
    bc.get_balance_for_read()
    r.delete(bc._keys()[0])

    broken = _Client(exc=RuntimeError("KIS gateway down"))
    wired(broken, r)
    assert bc.get_balance_for_read()[1] == "stale"

    time.sleep(1.1)
    back = _Client(_snapshot(total=7_777.0))
    wired(back, r)
    snap, src, _ = bc.get_balance_for_read()

    assert src == "live"
    assert snap.total_eval == 7_777.0
    assert r.get(bc._keys()[2]) is None, "a good fetch must clear the down marker"


def test_falls_back_to_position_snapshot_when_redis_empty(wired, monkeypatch):
    """Fresh container during an outage: no redis history at all, so recover
    from the PositionSnapshot that live_sync writes at 09:30/15:40."""
    broken = _Client(exc=RuntimeError("KIS gateway down"))
    wired(broken)

    from datetime import date, datetime

    class _Row:
        cash = 500_000.0
        total_eval = 900_000.0
        snapshot_date = date(2026, 8, 14)
        created_at = datetime(2026, 8, 14, 6, 40)
        holdings_json = json.dumps([{
            "code": "000660", "name": "SK하이닉스", "qty": 3, "avg": 100_000.0,
            "eval_price": 110_000.0, "eval_value": 330_000.0,
            "pnl": 30_000.0, "pnl_pct": 0.1,
        }], ensure_ascii=False)

    monkeypatch.setattr(bc, "_from_db", lambda: (
        kc.AccountSnapshot(
            cash=_Row.cash, total_eval=_Row.total_eval,
            holdings=[kc.Holding(code="000660", name="SK하이닉스", qty=3,
                                 avg_price=100_000.0, eval_price=110_000.0,
                                 eval_value=330_000.0, pnl=30_000.0, pnl_pct=0.1)],
        ),
        _Row.created_at,
    ))

    snap, src, as_of = bc.get_balance_for_read()

    assert src == "db"
    assert snap.total_eval == 900_000.0
    assert as_of == _Row.created_at


def test_never_raises_when_everything_is_unavailable(wired, monkeypatch):
    broken = _Client(exc=RuntimeError("KIS gateway down"))
    wired(broken)
    monkeypatch.setattr(bc, "_from_db", lambda: None)

    snap, src, _ = bc.get_balance_for_read()

    assert src == "empty"
    assert snap.total_eval == 0.0
    assert snap.holdings == []


def test_redis_unavailable_still_serves_live(wired, monkeypatch):
    """Redis down must degrade to a plain live fetch, not an error."""
    client = _Client(_snapshot())
    monkeypatch.setattr(bc, "_redis", lambda: None)
    monkeypatch.setattr(bc, "get_kis_client", lambda: client)

    snap, src, _ = bc.get_balance_for_read()

    assert src == "live"
    assert snap.total_eval == 1_000_000.0


# ─── _drop_token must not fire on unrelated 500s ────────────────────


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


@pytest.mark.parametrize("resp, expected", [
    # The 2026-08-14 trigger: a gateway fault, nothing to do with the token.
    (_Resp(500, '{"rt_cd":"1","msg_cd":"EGW00300","msg1":"Gateway 라우팅 오류가 발생했습니다."}'), False),
    # Throttling — also a 500, also not a token problem.
    (_Resp(500, '{"rt_cd":"1","msg_cd":"EGW00215","msg1":"원장에서 허용 가능한 초당 거래건수를 초과하였습니다."}'), False),
    # The real thing.
    (_Resp(500, '{"rt_cd":"1","msg_cd":"EGW00123","msg1":"기간이 만료된 token 입니다."}'), True),
    (_Resp(401, "unauthorized"), True),
    (_Resp(200, "ok"), False),
])
def test_is_token_error_only_for_actual_token_faults(resp, expected):
    assert kc._is_token_error(resp) is expected


def test_token_cooldown_blocks_display_reads_after_failure(monkeypatch):
    """A failed issue must not be retried by every subsequent dashboard poll.

    Each attempt costs the caller the full 10s HTTP timeout while feeding the
    1/min issuance cap that caused the failure.
    """
    r = FakeRedis()
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_redis", lambda: r)
    monkeypatch.setattr(c, "_gate", lambda: None)

    c._set_token_cooldown()
    assert r.get("kis:token:cooldown:paper:12345678")

    def _must_not_issue(*a, **kw):
        raise AssertionError("issuance must be suppressed during the cooldown")

    monkeypatch.setattr(kc.requests, "post", _must_not_issue)

    with kc.fail_fast_tokens():
        with pytest.raises(RuntimeError, match="cooldown"):
            c._ensure_token()


def test_cooldown_does_not_touch_the_order_path(monkeypatch):
    """Order tasks must keep the full recovery the 09:00 buys depend on.

    The cooldown is a display-side give-up. If it leaked into the order path a
    dashboard poll's failure could silently disarm that morning's trading.
    """
    r = FakeRedis()
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_redis", lambda: r)
    monkeypatch.setattr(c, "_gate", lambda: None)

    c._set_token_cooldown()  # a dashboard poll just gave up

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "FRESH", "expires_in": 86400}

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(kc.requests, "post", lambda *a, **kw: Resp())

    # No fail_fast_tokens() context — this is how tasks.py/live_trader call in.
    assert c._ensure_token() == "FRESH"


def test_display_read_does_not_sit_through_the_throttle_recovery(monkeypatch):
    """The 5s+65s re-read is right for an order task and wrong for a browser."""
    r = FakeRedis()
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_redis", lambda: r)
    monkeypatch.setattr(c, "_gate", lambda: None)

    class Throttled:
        status_code = 403
        text = "EGW00133"

    monkeypatch.setattr(kc.requests, "post", lambda *a, **kw: Throttled())

    def _no_sleep(_s):
        raise AssertionError("a display read must not sleep out the rate limit")

    monkeypatch.setattr(kc.time, "sleep", _no_sleep)

    with kc.fail_fast_tokens():
        with pytest.raises(RuntimeError, match="throttled"):
            c._ensure_token()

    # ...and it leaves the cooldown behind so the next poll gives up instantly.
    assert r.get("kis:token:cooldown:paper:12345678")


def test_successful_issue_clears_the_cooldown(monkeypatch):
    r = FakeRedis()
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="12345678-01")
    monkeypatch.setattr(c, "_redis", lambda: r)
    monkeypatch.setattr(c, "_gate", lambda: None)

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
    assert r.get("kis:token:cooldown:paper:12345678") is None
