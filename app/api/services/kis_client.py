"""KIS (Korea Investment & Securities) Open API thin client.

Wraps the official REST endpoints used by our daily-rebalance loop:
  - OAuth token issue/refresh
  - Account balance / cash position
  - Daily OHLCV (so model features can be refreshed without yfinance)
  - Order placement (cash buy/sell, paper or real env)
  - Order status / fills

Designed to operate in three modes selected by the `KIS_ENV` env var:
  - "paper" → 모의투자 host (openapivts.koreainvestment.com:29443)
  - "real"  → 실전 (openapi.koreainvestment.com:9443)
  - "mock"  → no network, returns synthetic responses (used until appkey/secret arrive)

The mock mode is critical: the rest of the live-trading pipeline can be wired
up and validated end-to-end before KIS credentials exist.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests

from ..config import settings

log = logging.getLogger(__name__)


# ─── Endpoint hosts ─────────────────────────────────────────────────

_HOSTS = {
    "real":  "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}

# Anything outside this set is a configuration error, not a default — see
# KISClient.__init__.
_VALID_ENVS = {"real", "paper", "mock"}

# Kill switch. A redis flag so it takes effect across api/worker/scheduler
# immediately — before this, the only way to stop trading was a deploy, which
# is minutes away and itself risky mid-session.
_HALT_KEY = "qlib:trading:halt"

# TR_ID prefixes: T... = real, V... = paper. Same trailing 7 chars.
_TR = {
    "real":  {"buy": "TTTC0802U", "sell": "TTTC0801U", "balance": "TTTC8434R", "psbl": "TTTC8908R",
              "fills": "TTTC8001R", "cancel": "TTTC0803U"},
    "paper": {"buy": "VTTC0802U", "sell": "VTTC0801U", "balance": "VTTC8434R", "psbl": "VTTC8908R",
              "fills": "VTTC8001R", "cancel": "VTTC0803U"},
}

# Minimum spacing between ANY two KIS calls sharing one APPKEY, across all
# containers (see KISClient._gate). Paper enforces ~1 req/s, real ~20 req/s,
# and the limit follows the appkey — so several accounts behind one appkey draw
# on a single budget and must queue behind the same slot.
_MIN_INTERVAL_MS = {"real": 250, "paper": 1200}

# Rejection markers that mean "rate limited, order NOT accepted" — the only
# rejections that are safe (and correct) to retry verbatim.
_THROTTLE_MARKERS = ("초당 거래건수", "EGW00201")
# EGW00123: the token was invalidated (another process issued a new one for
# this appkey). Like a throttle rejection, KIS never accepted the order — so
# re-auth + retry cannot double-execute.
_TOKEN_MARKERS = ("기간이 만료된 token", "EGW00123")

# Shared redis handle for the token cache + call gate (see KISClient._redis).
_redis_singleton = None

# After a failed token issue, suppress further attempts for this long. KIS caps
# issuance at ~1/min, so hammering it while it is unhealthy only extends the
# outage — and each attempt blocks the caller for the full 10s HTTP timeout.
_TOKEN_COOLDOWN_S = 60

# Set only for display reads (see `fail_fast_tokens`). Order and sync tasks
# leave it False and keep the full token-recovery behaviour they rely on: the
# 09:00 buys depend on the 5s/65s re-read that rides out KIS's 1/min issuance
# limit, and must never inherit a dashboard poll's give-up semantics.
_fail_fast: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "kis_fail_fast_tokens", default=False)


@contextlib.contextmanager
def fail_fast_tokens():
    """Within this block, give up instead of waiting out a token outage.

    Used by the read path, which has a last-known-good snapshot to fall back
    on and whose caller is a browser waiting on a response.
    """
    token = _fail_fast.set(True)
    try:
        yield
    finally:
        _fail_fast.reset(token)


def _halt_redis():
    """Redis handle for the kill switch, sharing the module pool."""
    global _redis_singleton
    if _redis_singleton is None:
        try:
            import redis  # type: ignore[import-not-found]
            _redis_singleton = redis.Redis.from_url(
                settings.celery_broker_url,
                socket_connect_timeout=2, socket_timeout=2)
        except Exception:  # noqa: BLE001
            return None
    return _redis_singleton


def trading_halted() -> str | None:
    """Reason string if trading is halted, else None.

    Fails OPEN (returns None) when redis is unreachable: a broken switch must
    not silently stop a working system. The inverse — trading through an
    intended halt — is caught by the operator, who can see orders still firing.
    """
    r = _halt_redis()
    if r is None:
        return None
    try:
        raw = r.get(_HALT_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("kill switch read failed, proceeding: %s", exc)
        return None
    if not raw:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def set_trading_halt(reason: str | None) -> bool:
    """Engage (reason set) or release (None) the kill switch. True on success."""
    r = _halt_redis()
    if r is None:
        return False
    try:
        if reason is None:
            r.delete(_HALT_KEY)
        else:
            r.set(_HALT_KEY, reason)   # no TTL — stays until explicitly cleared
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("kill switch write failed: %s", exc)
        return False


def _is_token_error(r: "requests.Response") -> bool:
    """True only when KIS says the TOKEN is the problem.

    KIS answers HTTP 500 for unrelated server faults too — `EGW00300`
    (게이트웨이 라우팅 오류), `EGW00215` (초당 거래건수 초과). Treating those as
    token invalidation (the old `status_code in (401, 500)` test) deleted the
    shared redis token on every gateway hiccup, and since issuance is capped at
    1/min the re-issue then 403'd or hung — turning a KIS blip into a
    self-sustaining outage (2026-08-14). Only 401, or an explicit token marker
    in the body, may drop the token.
    """
    if r.status_code == 401:
        return True
    try:
        return any(m in r.text for m in _TOKEN_MARKERS)
    except Exception:  # noqa: BLE001
        return False


# ─── Domain models ──────────────────────────────────────────────────

@dataclass
class OrderResult:
    ok: bool
    order_id: str | None
    code: str
    side: str  # "BUY" | "SELL"
    qty: int
    price: float | None  # None = market order
    raw: dict[str, Any]
    error: str | None = None


@dataclass
class Holding:
    code: str
    qty: int
    avg_price: float
    eval_price: float
    eval_value: float
    pnl: float
    pnl_pct: float
    name: str | None = None  # KIS prdt_name (실계좌) / _stock_name (시뮬)
    # 매도가능수량 (ord_psbl_qty). Differs from `qty` when part of the position
    # is already committed to a resting sell, or is unsettled. Selling `qty`
    # blind is how a sell gets rejected outright — and a rejected sell means
    # holding a position you meant to exit. None = unknown (simulated books).
    # MUST stay last with a default: balance_cache._decode does Holding(**h)
    # over cached JSON written before this field existed.
    sellable_qty: int | None = None


@dataclass
class AccountSnapshot:
    cash: float
    total_eval: float
    holdings: list[Holding]


# ─── Tick size table (KRX 가격대별 호가단위) ─────────────────────────

# (upper_bound_exclusive, tick) — last tier covers all prices ≥ 200,000.
_TICKS = [
    (2_000,    1),
    (5_000,    5),
    (20_000,   10),
    (50_000,   50),
    (200_000,  100),
    (500_000,  500),
    (math.inf, 1_000),
]


def round_to_tick(price: float) -> int:
    """Round a float price down to the KRX tick grid for that price tier."""
    for upper, tick in _TICKS:
        if price < upper:
            return int(math.floor(price / tick) * tick)
    return int(math.floor(price / 1_000) * 1_000)


# ─── Client ─────────────────────────────────────────────────────────


class KISClient:
    """Minimal-surface KIS REST client with token caching + mock fallback.

    Thread-safe for the small concurrency we need (1 scheduler + 1 worker).
    """

    def __init__(self,
                 env: str | None = None,
                 app_key: str | None = None,
                 app_secret: str | None = None,
                 account_no: str | None = None,
                 account_product: str | None = None):
        self.env = env or settings.kis_env or "paper"
        # A typo'd env is NOT a harmless default. `host` and `tr_set` compare
        # against "real" exactly, so "REAL"/"live"/"real " would quietly route
        # live credentials at the PAPER host with V-series TR ids — every call
        # fails auth and the system looks merely broken instead of misconfigured.
        if self.env not in _VALID_ENVS:
            raise ValueError(
                f"KIS_ENV must be one of {sorted(_VALID_ENVS)}, got {self.env!r}. "
                "Refusing to start rather than silently fall back to 모의투자.")
        self.app_key = app_key if app_key is not None else settings.kis_app_key
        self.app_secret = app_secret if app_secret is not None else settings.kis_app_secret
        self.account_no = (account_no if account_no is not None else settings.kis_account_no).split("-")[0]
        # CANO/ACNT_PRDT_CD split: KIS uses 8-digit CANO + 2-digit product code
        raw = account_no if account_no is not None else settings.kis_account_no
        if "-" in raw:
            self.cano, self.acnt_prdt_cd = raw.split("-", 1)
        else:
            self.cano = raw
            self.acnt_prdt_cd = account_product or settings.kis_account_product

        # Credential-less mock is a convenience for paper/dev. On a REAL
        # account it is a trap: place_order would return ok=True with a
        # "MOCK-…" id, and the dashboard and Telegram would report filled
        # orders that never existed. Fail at construction instead.
        if self.env == "real" and not (self.app_key and self.app_secret and self.cano):
            missing = [n for n, v in (("app_key", self.app_key),
                                      ("app_secret", self.app_secret),
                                      ("account_no", self.cano)) if not v]
            raise ValueError(
                f"KIS_ENV=real but {', '.join(missing)} missing — refusing to run. "
                "Mock mode would report fake fills as real orders.")

        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0  # local fallback for the account-wide call gate

    # ─── Mode helpers ──────────────────────────────────────────

    @property
    def is_mock(self) -> bool:
        """Mock mode is automatic if creds missing or env explicitly 'mock'.

        Never true for env="real" — __init__ rejects that combination.
        """
        if self.env == "mock":
            return True
        return not (self.app_key and self.app_secret and self.cano)

    @property
    def host(self) -> str:
        if self.env == "real":
            return _HOSTS["real"]
        return _HOSTS["paper"]

    @property
    def tr_set(self) -> dict[str, str]:
        return _TR["real" if self.env == "real" else "paper"]

    # ─── Auth ──────────────────────────────────────────────────

    @property
    def _appkey_scope(self) -> str:
        """Short digest of the appkey — the unit KIS actually limits by.

        Tokens and the request budget are enforced per APPKEY, not per account
        (`_ensure_token`'s own comment: KIS kills the previous token the instant
        a new one is issued *for this appkey*). Keying redis by `cano` was
        harmless only because one appkey served one account. With several
        accounts behind one appkey — the likely shape once a second account is
        added — cano-keyed caches would each issue their own token, every issue
        killing the others, and each would pass its own gate slot while sharing
        one budget. That is the 2026-07-29 / 08-11 lost-order mechanism.

        Digested, never raw: redis keys show up in logs and `--scan` output, and
        the appkey is a credential.
        """
        return hashlib.sha256(self.app_key.encode()).hexdigest()[:12] if self.app_key else "nokey"

    @property
    def _redis_token_key(self) -> str:
        return f"kis:token:{self.env}:{self._appkey_scope}"

    def _redis(self):
        """Best-effort redis client — None if unavailable (never raises).

        Process singleton: this used to build a fresh `redis.from_url` pool on
        every call (twice per KIS request, via `_gate` + `_ensure_token`) and
        never close it, leaking a pool each time. Explicit socket timeouts stop
        an unreachable redis from hanging a request thread indefinitely.
        """
        global _redis_singleton
        if _redis_singleton is None:
            try:
                import redis  # type: ignore[import-not-found]
                _redis_singleton = redis.Redis.from_url(
                    settings.celery_broker_url,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
            except Exception:  # noqa: BLE001
                return None
        return _redis_singleton

    @property
    def _redis_cooldown_key(self) -> str:
        return f"kis:token:cooldown:{self.env}:{self._appkey_scope}"

    def _ensure_token(self) -> str:
        """Return a valid access token, shared across worker processes via redis.

        KIS invalidates the previous token whenever a new one is issued and
        rate-limits issuance (~1/min). With prefork workers recycled every few
        tasks, per-process tokens caused constant re-issue churn — the freshly
        issued token in one child invalidated the cached token in another,
        surfacing as intermittent 401/500 on inquire-balance. A shared redis
        cache makes issuance rare (once per expiry) and consistent.
        """
        if self.is_mock:
            return "MOCK_TOKEN"
        with self._lock:
            now = time.time()
            r_client = self._redis()
            # Redis is the authority — never the local copy. KIS kills the
            # previous token the instant a new one is issued for this appkey,
            # so a local token that is nowhere near its own expiry can already
            # be dead. Trusting it is what lost the 09:00 buys on 2026-08-11:
            # the sells 14s earlier went through on the same cached token.
            # The local copy is only a fallback for when redis is unreachable.
            if r_client is not None:
                try:
                    cached = r_client.get(self._redis_token_key)
                    if cached:
                        d = json.loads(cached)
                        if d.get("expires_at", 0) - now > 600:
                            self._token = d["access_token"]
                            self._token_expires_at = d["expires_at"]
                            return self._token
                except Exception as exc:  # noqa: BLE001
                    log.warning("KIS token redis read failed: %s", exc)
                    if self._token and self._token_expires_at - now > 600:
                        return self._token
            elif self._token and self._token_expires_at - now > 600:  # >10min left
                return self._token
            # No usable token, and a recent issue attempt failed. A display
            # caller gives up here rather than spending another 10s HTTP
            # timeout — balance_cache serves the last-known-good snapshot
            # instead. Order tasks never take this branch: they must keep every
            # bit of the recovery below, so trading is unaffected.
            if _fail_fast.get() and r_client is not None:
                try:
                    cooling = r_client.get(self._redis_cooldown_key)
                except Exception as exc:  # noqa: BLE001
                    log.warning("KIS token cooldown read failed: %s", exc)
                    cooling = None
                if cooling:
                    raise RuntimeError(
                        "KIS token issuance in cooldown after a recent failure")
            url = f"{self.host}/oauth2/tokenP"
            payload = {"grant_type": "client_credentials",
                       "appkey": self.app_key, "appsecret": self.app_secret}
            try:
                r = requests.post(url, json=payload, timeout=10)
            except Exception:
                self._set_token_cooldown()
                raise
            if r.status_code in (403, 429):
                # EGW00133: issuance is rate-limited to 1/min. When two
                # processes race (e.g. the 09:00 order task vs a dashboard
                # balance poll right after a deploy wiped redis), the loser
                # lands here — but the WINNER stored its token in redis
                # moments ago. Re-read the cache before giving up; only then
                # wait out the rate-limit window and re-issue once.
                # (2026-07-29: this exact race killed the day's buy orders.)
                log.warning("KIS token issue throttled (%s) — re-reading shared cache",
                            r.status_code)
                # Open the cooldown BEFORE the long waits below, so display
                # callers fail fast instead of each piling up behind its own
                # 70s wait. Whoever is here rides the rate-limit window out.
                self._set_token_cooldown()
                if _fail_fast.get():
                    # A dashboard poll must never sit through 70s of sleeps —
                    # that is the block the user sees. The order tasks below do.
                    raise RuntimeError("KIS token issuance throttled")
                for wait_s in (5, 65):
                    time.sleep(wait_s)
                    if r_client is not None:
                        try:
                            cached = r_client.get(self._redis_token_key)
                            if cached:
                                d = json.loads(cached)
                                if d.get("expires_at", 0) - time.time() > 600:
                                    self._token = d["access_token"]
                                    self._token_expires_at = d["expires_at"]
                                    return self._token
                        except Exception as exc:  # noqa: BLE001
                            log.warning("KIS token redis re-read failed: %s", exc)
                    r = requests.post(url, json=payload, timeout=10)
                    if r.status_code == 200:
                        break
            if r.status_code != 200:
                log.error("KIS token issue failed: %s %s", r.status_code, r.text[:300])
                self._set_token_cooldown()
            r.raise_for_status()
            d = r.json()
            self._token = d["access_token"]
            self._token_expires_at = now + int(d.get("expires_in", 86400))
            if r_client is not None:
                try:
                    r_client.set(
                        self._redis_token_key,
                        json.dumps({"access_token": self._token,
                                    "expires_at": self._token_expires_at}),
                        ex=int(d.get("expires_in", 86400)),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("KIS token redis write failed: %s", exc)
            self._clear_token_cooldown()
            return self._token

    def _set_token_cooldown(self) -> None:
        """Suppress token issuance for a minute after a failure (best effort)."""
        r_client = self._redis()
        if r_client is None:
            return
        try:
            r_client.set(self._redis_cooldown_key, "1", ex=_TOKEN_COOLDOWN_S)
        except Exception:  # noqa: BLE001
            pass

    def _clear_token_cooldown(self) -> None:
        r_client = self._redis()
        if r_client is None:
            return
        try:
            r_client.delete(self._redis_cooldown_key)
        except Exception:  # noqa: BLE001
            pass

    def _drop_token(self) -> None:
        """Invalidate the cached token (local + redis) so the next call re-auths."""
        with self._lock:
            self._token = None
            self._token_expires_at = 0.0
        r_client = self._redis()
        if r_client is not None:
            try:
                r_client.delete(self._redis_token_key)
            except Exception:  # noqa: BLE001
                pass

    # ─── Account-wide call gate ───────────────────────────────

    @property
    def _redis_slot_key(self) -> str:
        return f"kis:apislot:{self.env}:{self._appkey_scope}"

    def _gate(self) -> None:
        """Space out ALL KIS calls sharing this appkey, across every container.

        KIS rate-limits per APPKEY (paper ~1 req/s), but our calls come from
        web (dashboard polling), worker and scheduler concurrently — per-task
        spacing alone let a 09:00 balance poll collide with a sell order and
        get it rejected (2026-07-31 미래에셋 sell). A redis slot key with a
        TTL equal to the minimum interval serializes them globally; if redis
        is unavailable we still enforce the interval within this process.

        Scoped by appkey rather than account (`_appkey_scope`): with several
        accounts behind one appkey, per-account slots would each let a call
        through and blow one shared budget. The local fallback below is
        per-process and cannot see sibling accounts — acceptable only because
        it is the redis-unavailable path.
        """
        if self.is_mock:
            return
        interval_ms = _MIN_INTERVAL_MS.get(self.env, 1200)
        r_client = self._redis()
        if r_client is not None:
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                try:
                    if r_client.set(self._redis_slot_key, "1", nx=True, px=interval_ms):
                        return
                except Exception as exc:  # noqa: BLE001
                    log.warning("KIS gate redis failed, local fallback: %s", exc)
                    break
                time.sleep(0.12)
            else:
                log.warning("KIS gate wait exceeded 20s — proceeding")
                return
        with self._lock:
            wait = self._last_call + interval_ms / 1000.0 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _hashkey(self, body: dict) -> str:
        if self.is_mock:
            return "MOCK_HASHKEY"
        self._gate()  # hashkey counts toward the per-second budget too
        url = f"{self.host}/uapi/hashkey"
        headers = {"content-type": "application/json",
                   "appkey": self.app_key, "appsecret": self.app_secret}
        r = requests.post(url, headers=headers, json=body, timeout=10)
        r.raise_for_status()
        return r.json()["HASH"]

    def _headers(self, tr_id: str, body: dict | None = None) -> dict:
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }
        if body is not None:
            h["hashkey"] = self._hashkey(body)
        return h

    # ─── Account balance ──────────────────────────────────────

    def get_balance(self) -> AccountSnapshot:
        if self.is_mock:
            return AccountSnapshot(
                cash=10_000_000.0,
                total_eval=10_000_000.0,
                holdings=[],
            )
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        # KIS paper env intermittently 500s (also on auth churn). Re-auth once
        # and retry with backoff before surfacing — the daily sync must not
        # die on a transient server hiccup. A display read takes one attempt
        # only: the 2s+4s+6s backoff is 12s of blocking a browser cannot spend,
        # and balance_cache has a last-known-good snapshot to answer with.
        attempts = 1 if _fail_fast.get() else 3
        last_exc: Exception | None = None
        for attempt in range(attempts):
            self._gate()
            r = requests.get(self.host + path, headers=self._headers(self.tr_set["balance"]),
                             params=params, timeout=15)
            if r.status_code == 200:
                break
            log.warning(
                "KIS get_balance HTTP %s (attempt %d/%d): %s",
                r.status_code, attempt + 1, attempts, r.text[:300],
            )
            if _is_token_error(r):
                self._drop_token()  # token was invalidated elsewhere
            try:
                r.raise_for_status()
            except requests.HTTPError as exc:
                last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
        else:
            raise last_exc if last_exc else RuntimeError("KIS get_balance failed")
        d = r.json()
        holdings = []
        for row in d.get("output1", []) or []:
            qty = int(float(row.get("hldg_qty") or 0))
            if qty <= 0:
                continue
            avg = float(row.get("pchs_avg_pric") or 0)
            ep = float(row.get("prpr") or 0)
            ev = float(row.get("evlu_amt") or 0)
            pnl = float(row.get("evlu_pfls_amt") or 0)
            pnl_pct = float(row.get("evlu_pfls_rt") or 0) / 100.0
            raw_sellable = row.get("ord_psbl_qty")
            sellable = int(float(raw_sellable)) if raw_sellable not in (None, "") else None
            holdings.append(Holding(code=str(row.get("pdno")),
                                     name=row.get("prdt_name") or None,
                                     qty=qty, avg_price=avg,
                                     eval_price=ep, eval_value=ev,
                                     pnl=pnl, pnl_pct=pnl_pct,
                                     sellable_qty=sellable))
        summary = (d.get("output2") or [{}])[0]
        # 쓸 수 있는 현금은 **D+2 정산금액**이다.
        #
        # KIS output2 에는 결제 시점이 다른 현금이 세 개 있다:
        #   dnca_tot_amt       예수금총금액        D+0 — 미결제 매수대금이 아직 안 빠짐
        #   nxdy_excc_amt      익일정산금액        D+1
        #   prvs_rcdl_excc_amt 가수도정산금액      D+2 — 실제로 쓸 수 있는 돈
        #
        # 예전에는 D+0 을 썼다. 그러면 화면이 있지도 않은 돈을 현금으로 센다 —
        # 2026-08-26 실측: D+0 2,602,518 vs D+2 971,429, **163만원 과대**.
        # (docs/05-daily/INSIGHTS.md:29 의 "매수 당일 cash 가 안 줄어 보임"이
        #  이 현상이고, 이번에 금액까지 확정됐다.)
        #
        # 결정적 근거는 KIS 자신의 총평가금액이 D+2 로 만들어진다는 것이다:
        #   prvs_rcdl_excc_amt + scts_evlu_amt == tot_evlu_amt   (실측 일치)
        #   dnca_tot_amt       + scts_evlu_amt != tot_evlu_amt
        # 총평가는 D+2 로 쓰면서 현금만 D+0 을 붙이면 한 화면에서 결제 시점이
        # 다른 두 숫자를 섞게 된다.
        #
        # 매수 예산도 이 값을 쓴다. 정상일에는 결과가 같다 —
        # 예산은 min(cash, 주문가능현금) 이고 주문가능현금(966,571)이 D+2(971,429)와
        # 거의 같기 때문이다. 달라지는 것은 그 가드가 꺼진 날뿐이고, 그때
        # D+0 을 쓰면 없는 돈으로 주문해 미수가 난다.
        #
        # 필드가 없으면 D+0 으로 물러난다 — 모의계좌가 안 줄 수 있다.
        cash = float(summary.get("prvs_rcdl_excc_amt")
                     or summary.get("dnca_tot_amt") or 0)
        total = float(summary.get("tot_evlu_amt") or 0)
        # 관측 — 세 현금과 당일 거래액을 남긴다. 새 KIS 호출은 없다(이미 받은 응답).
        # gap 이 0 이 아니면 위 항등식 가정이 깨진 것이므로 즉시 보인다.
        scts = float(summary.get("scts_evlu_amt") or 0)
        log.info("balance: d0=%s d1=%s d2=%s scts=%s tot=%s "
                 "thdt_buy=%s thdt_sll=%s gap=%s",
                 summary.get("dnca_tot_amt"), summary.get("nxdy_excc_amt"),
                 summary.get("prvs_rcdl_excc_amt"), summary.get("scts_evlu_amt"),
                 summary.get("tot_evlu_amt"), summary.get("thdt_buy_amt"),
                 summary.get("thdt_sll_amt"), round(total - scts - cash))
        return AccountSnapshot(cash=cash, total_eval=total, holdings=holdings)

    # ─── 매수가능금액 (주문 예산의 진실) ────────────────────────

    def get_orderable_cash(self, code: str, price: float | None = None) -> dict:
        """{"cash", "max_qty"} — 주문가능현금 and 최대매수수량 for one code.

        The buy budget has been `dnca_tot_amt` (예수금 총액) off get_balance,
        which on a REAL account includes funds already committed to unsettled
        (D+2) trades. Sizing against it is how a 미수 (margin) position gets
        opened by accident. This TR is the number KIS itself would allow.

        Returns {} on mock or any failure — callers must treat an empty dict
        as "unknown" and fall back rather than sizing to zero.
        """
        if self.is_mock:
            return {}
        self._gate()
        try:
            r = requests.get(
                self.host + "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                headers=self._headers(self.tr_set["psbl"]),
                params={
                    "CANO": self.cano,
                    "ACNT_PRDT_CD": self.acnt_prdt_cd,
                    "PDNO": code.zfill(6),
                    # 지정가면 그 가격 기준, 시장가면 0 + ORD_DVSN 01
                    "ORD_UNPR": "0" if price is None else str(round_to_tick(float(price))),
                    "ORD_DVSN": "01" if price is None else "00",
                    "CMA_EVLU_AMT_ICLD_YN": "N",   # CMA 평가금액 미포함
                    "OVRS_ICLD_YN": "N",           # 해외분 미포함
                },
                timeout=10)
            if r.status_code != 200:
                log.warning("KIS inquire-psbl-order HTTP %s: %s", r.status_code, r.text[:200])
                return {}
            out = r.json().get("output") or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("KIS inquire-psbl-order failed for %s: %s", code, exc)
            return {}
        # 관측 (2026-08-24) — 이 docstring 은 ord_psbl_cash 를 "미수 없이 쓸 수 있는
        # 금액"이라 단정하지만 그건 **검증된 적 없는 가정**이다. KIS 응답에는
        # nrcvb_buy_amt(미수없는매수금액) 전용 필드가 따로 있고, ord_psbl_cash 가
        # 종목별 증거금률을 반영한 레버리지 금액일 가능성이 남아 있다. 그렇다면
        # 호출부의 min() 을 푸는 순간 이 코드의 미수 방어가 사라진다.
        # 필드를 나란히 찍어 그 가정을 판정한다. 하루치면 충분하다.
        log.info("psbl-order %s: ord_psbl=%s nrcvb=%s max_amt=%s max_qty=%s",
                 code, out.get("ord_psbl_cash"), out.get("nrcvb_buy_amt"),
                 out.get("max_buy_amt"), out.get("max_buy_qty"))
        return {
            # 주문가능현금 — 미수 없이 쓸 수 있는 금액(위 주석의 미검증 가정)
            "cash": float(out.get("ord_psbl_cash") or 0) or None,
            "max_qty": int(float(out.get("max_buy_qty") or 0)) or None,
            # 미수없는매수금액. 아직 아무도 쓰지 않는다 — 관측용으로만 실어 보낸다.
            "nrcvb": float(out.get("nrcvb_buy_amt") or 0) or None,
        }

    # ─── 국내휴장일 (개장일 여부) ───────────────────────────────

    def get_open_days(self, bass_dt: date) -> dict[str, bool]:
        """{iso_date: is_open} from `bass_dt` forward — KIS 국내휴장일조회.

        `opnd_yn` (개장일여부) is the field KIS itself points at for "can I
        place an order today"; 영업일(bzdy_yn)/결제일(sttl_day_yn) answer
        different questions and are not interchangeable.

        KIS asks for **at most one call per day** on this TR, so callers must
        come through `services.trading_calendar`, which caches by month.
        Returns {} on mock or any failure — the caller treats empty as
        "unknown" and carries on rather than halting trading.
        """
        if self.is_mock:
            return {}
        out: dict[str, bool] = {}
        fk = nk = ""
        tr_cont = ""
        for _ in range(10):          # paginated; 10 pages covers months of days
            self._gate()
            try:
                r = requests.get(
                    self.host + "/uapi/domestic-stock/v1/quotations/chk-holiday",
                    headers={**self._headers("CTCA0903R"), "tr_cont": tr_cont},
                    params={"BASS_DT": bass_dt.strftime("%Y%m%d"),
                            "CTX_AREA_FK": fk, "CTX_AREA_NK": nk},
                    timeout=10)
            except Exception as exc:  # noqa: BLE001
                log.warning("KIS chk-holiday request failed: %s", exc)
                break
            if r.status_code != 200:
                log.warning("KIS chk-holiday HTTP %s: %s", r.status_code, r.text[:200])
                break
            d = r.json()
            for row in d.get("output") or []:
                raw = str(row.get("bass_dt") or "")
                if len(raw) != 8:
                    continue
                iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
                out[iso] = str(row.get("opnd_yn") or "").upper() == "Y"
            tr_cont = r.headers.get("tr_cont", "")
            if tr_cont not in ("M", "F"):     # no further pages
                break
            fk = d.get("ctx_area_fk") or ""
            nk = d.get("ctx_area_nk") or ""
            tr_cont = "N"                     # continuation marker for the next call
        return out

    # ─── Quote (현재가/시가) ───────────────────────────────────

    def get_quote(self, code: str) -> dict:
        """{"open", "price"} for a stock — market-data TRs work on paper too."""
        if self.is_mock:
            return {}
        self._gate()
        r = requests.get(
            self.host + "/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._headers("FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            timeout=10)
        if r.status_code != 200:
            return {}
        d = r.json().get("output") or {}
        return {
            "open": float(d.get("stck_oprc") or 0) or None,
            "price": float(d.get("stck_prpr") or 0) or None,
            # Session range SO FAR. Called at 10:00 these are the 09:00~10:00
            # extremes, which is exactly what judges a morning resting limit —
            # no minute-bar TR needed. 상한가/하한가 flag 상한가 pins.
            "high": float(d.get("stck_hgpr") or 0) or None,
            "low": float(d.get("stck_lwpr") or 0) or None,
            "upper_limit": float(d.get("stck_mxpr") or 0) or None,
            "lower_limit": float(d.get("stck_llam") or 0) or None,
            # Risk flags — the ONLY fundamental-risk signal this system has:
            # trht_yn: 거래정지 여부, iscd_stat_cls_code: 51 관리종목 /
            # 52 투자위험 / 53 투자경고, mrkt_warn_cls_code: 시장경고.
            "halted": str(d.get("trht_yn") or "N").upper() == "Y",
            "status_code": str(d.get("iscd_stat_cls_code") or ""),
            "warn_code": str(d.get("mrkt_warn_cls_code") or ""),
        }

    def get_orderbook(self, code: str) -> dict:
        """10-level bid/ask book + closing-auction expectation (TR FHKST01010200).

        Observation-only — feeds orderbook_snapshots, never a trading decision.
        What it answers: the cafe sim books a full fill at the 15:28 quote
        without ever looking at depth, so a 상한가 close with an empty ask side
        and a queue of unfilled bids reads identically to a liquid one.

        During 정규장 output1's ask/bid ladders are the live book. Inside the
        15:20~15:30 동시호가 no book matches yet, so the ladder goes sparse and
        output2's 예상체결가/예상체결수량 (antc_*) carry the information
        instead. Callers get both and pick by slot; {} on any failure.
        """
        if self.is_mock:
            return {}
        self._gate()
        r = requests.get(
            self.host + "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            headers=self._headers("FHKST01010200"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code.zfill(6)},
            timeout=10)
        d = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or d.get("rt_cd") != "0":
            log.warning("KIS get_orderbook %s failed HTTP %s: %s",
                        code, r.status_code, r.text[:200])
            return {}
        o1 = d.get("output1") or {}
        o2 = d.get("output2") or {}

        def _f(v) -> float:
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0

        asks = [{"px": _f(o1.get(f"askp{i}")), "qty": _f(o1.get(f"askp_rsqn{i}"))}
                for i in range(1, 11)]
        bids = [{"px": _f(o1.get(f"bidp{i}")), "qty": _f(o1.get(f"bidp_rsqn{i}"))}
                for i in range(1, 11)]
        return {
            "asks": asks,
            "bids": bids,
            "total_ask_qty": _f(o1.get("total_askp_rsqn")),
            "total_bid_qty": _f(o1.get("total_bidp_rsqn")),
            "antc_price": _f(o2.get("antc_cnpr")) or None,
            "antc_qty": _f(o2.get("antc_cnqn")) or None,
            "antc_volume": _f(o2.get("antc_vol")) or None,
        }

    # ─── 투자자별 매매동향 (기관/외국인 수급) ──────────────────

    def get_investor_daily(self, code: str) -> list[dict]:
        """Daily 개인/외국인/기관계 net-buy rows for one stock (~30 days).

        TR FHKST01010900 (주식현재가 투자자). One call returns the whole
        lookback window, so a 5-day cumulative flow score costs a single
        request per candidate. Per KIS docs the CURRENT day's row only
        appears after the close — which is exactly what keeps the flow
        overlay free of look-ahead.

        '외국인' here means 외국인 + 기타외국인 (KIS aggregates them).
        Returns [] on mock/any failure — callers must treat empty as
        "no flow data today", never as "zero net buying".
        """
        if self.is_mock:
            return []
        self._gate()
        r = requests.get(
            self.host + "/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=self._headers("FHKST01010900"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code.zfill(6)},
            timeout=10)
        if r.status_code != 200:
            log.warning("KIS get_investor_daily %s HTTP %s: %s",
                        code, r.status_code, r.text[:200])
            return []
        rows = (r.json() or {}).get("output") or []
        out: list[dict] = []
        for row in rows:
            day = str(row.get("stck_bsop_date") or "").strip()
            if len(day) != 8:
                continue

            def _num(key: str) -> float:
                try:
                    return float(row.get(key) or 0)
                except (TypeError, ValueError):
                    return 0.0

            out.append({
                "date": day,
                "frgn_net_qty": _num("frgn_ntby_qty"),
                "orgn_net_qty": _num("orgn_ntby_qty"),
                "prsn_net_qty": _num("prsn_ntby_qty"),
                "frgn_net_amt": _num("frgn_ntby_tr_pbmn"),
                "orgn_net_amt": _num("orgn_ntby_tr_pbmn"),
                "prsn_net_amt": _num("prsn_ntby_tr_pbmn"),
            })
        return out

    # ─── Market screener sources (순위·일봉) ───────────────────

    def get_rank_fluctuation(self, count: int = 30) -> list[dict]:
        """등락률 상위 (TR FHPST01700000). [{code, name, price, change_pct}].

        Market-data TR — works on the paper env like quotes do. Returns []
        on mock/any failure; callers treat empty as "no pool today"."""
        if self.is_mock:
            return []
        self._gate()
        r = requests.get(
            self.host + "/uapi/domestic-stock/v1/ranking/fluctuation",
            headers=self._headers("FHPST01700000"),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",  # 상승률순
                "fid_input_cnt_1": "0",
                "fid_prc_cls_code": "0",
                "fid_input_price_1": "", "fid_input_price_2": "",
                "fid_vol_cnt": "", "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0", "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "", "fid_rsfl_rate2": "",
            }, timeout=10)
        d = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or d.get("rt_cd") != "0":
            log.warning("KIS rank_fluctuation failed HTTP %s: %s",
                        r.status_code, r.text[:200])
            return []
        out = []
        for row in (d.get("output") or [])[:count]:
            code = str(row.get("stck_shrn_iscd") or "").strip()
            if len(code) != 6:
                continue
            try:
                out.append({"code": code,
                            "name": str(row.get("hts_kor_isnm") or "").strip(),
                            "price": float(row.get("stck_prpr") or 0),
                            "change_pct": float(row.get("prdy_ctrt") or 0)})
            except (TypeError, ValueError):
                continue
        return out

    def get_rank_volume(self, count: int = 30) -> list[dict]:
        """거래량 순위 (TR FHPST01710000). Same shape as get_rank_fluctuation."""
        if self.is_mock:
            return []
        self._gate()
        r = requests.get(
            self.host + "/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=self._headers("FHPST01710000"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "1",  # 거래증가율
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
            }, timeout=10)
        d = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or d.get("rt_cd") != "0":
            log.warning("KIS rank_volume failed HTTP %s: %s",
                        r.status_code, r.text[:200])
            return []
        out = []
        for row in (d.get("output") or [])[:count]:
            code = str(row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd") or "").strip()
            if len(code) != 6:
                continue
            try:
                out.append({"code": code,
                            "name": str(row.get("hts_kor_isnm") or "").strip(),
                            "price": float(row.get("stck_prpr") or 0),
                            "change_pct": float(row.get("prdy_ctrt") or 0)})
            except (TypeError, ValueError):
                continue
        return out

    def get_daily_bars(self, code: str) -> list[dict]:
        """~30 recent daily OHLCV bars, oldest→newest (TR FHKST01010400).

        The price source for out-of-universe codes (cafe strategy) whose bars
        never land in kr_data. Adjusted prices (FID_ORG_ADJ_PRC=0)."""
        if self.is_mock:
            return []
        self._gate()
        r = requests.get(
            self.host + "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            headers=self._headers("FHKST01010400"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code.zfill(6),
                    "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
            timeout=10)
        d = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or d.get("rt_cd") != "0":
            log.warning("KIS get_daily_bars %s failed HTTP %s: %s",
                        code, r.status_code, r.text[:200])
            return []
        out = []
        for row in d.get("output") or []:
            day = str(row.get("stck_bsop_date") or "").strip()
            if len(day) != 8:
                continue
            try:
                out.append({"date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                            "open": float(row.get("stck_oprc") or 0),
                            "high": float(row.get("stck_hgpr") or 0),
                            "low": float(row.get("stck_lwpr") or 0),
                            "close": float(row.get("stck_clpr") or 0),
                            "volume": float(row.get("acml_vol") or 0)})
            except (TypeError, ValueError):
                continue
        out.reverse()  # KIS returns newest first
        return [b for b in out if b["close"] > 0]

    # ─── Daily fills (주문체결조회) ────────────────────────────

    def get_daily_fills(self, start: "date", end: "date") -> dict[str, dict]:
        """Actual fills per order number over [start, end].

        Returns {odno: {"code", "side", "ccld_qty", "avg_price"}}. Market
        orders carry no price at submission — this is the ONLY source of the
        real average fill price, which pins realized pnl permanently instead
        of re-estimating it from bars. Mock mode returns {}.
        """
        if self.is_mock:
            return {}
        path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        out: dict[str, dict] = {}
        fk, nk = "", ""
        for _page in range(10):  # pagination guard
            params = {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": start.strftime("%Y%m%d"),
                "INQR_END_DT": end.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",   # all
                "INQR_DVSN": "00",         # 역순
                "PDNO": "",
                "CCLD_DVSN": "01",         # 체결
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": fk,
                "CTX_AREA_NK100": nk,
            }
            self._gate()
            r = requests.get(self.host + path,
                             headers=self._headers(self.tr_set["fills"]),
                             params=params, timeout=15)
            if r.status_code != 200:
                log.warning("KIS get_daily_fills HTTP %s: %s", r.status_code, r.text[:300])
                break
            d = r.json()
            for row in d.get("output1", []) or []:
                odno = str(row.get("odno") or "").lstrip("0") or str(row.get("odno") or "")
                qty = int(float(row.get("tot_ccld_qty") or 0))
                amt = float(row.get("tot_ccld_amt") or 0)
                avg = float(row.get("avg_prvs") or 0) or ((amt / qty) if qty else 0.0)
                if not odno or qty <= 0 or avg <= 0:
                    continue
                out[odno] = {
                    "code": str(row.get("pdno") or ""),
                    "side": "SELL" if str(row.get("sll_buy_dvsn_cd")) == "01" else "BUY",
                    "ccld_qty": qty,
                    "avg_price": avg,
                }
            tr_cont = (r.headers.get("tr_cont") or "").strip()
            if tr_cont not in ("F", "M"):
                break
            fk = d.get("ctx_area_fk100", "")
            nk = d.get("ctx_area_nk100", "")
            time.sleep(1.2)
        return out

    # ─── Order placement ──────────────────────────────────────

    def place_order(self, code: str, side: str, qty: int,
                    price: float | None = None) -> OrderResult:
        """Place a cash order. price=None → market order."""
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        qty = int(qty)
        if qty <= 0:
            return OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                               price=price, raw={}, error="qty must be > 0")

        # Two rails that sit in FRONT of every order, mock included — a mock
        # run that would have blown the cap should fail loudly in testing, not
        # pass and then surprise us on the real account.
        halted = trading_halted()
        if halted:
            log.warning("KIS order blocked by kill switch: %s %s x%d", side, code, qty)
            return OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                               price=price, raw={"halted": True},
                               error=f"거래 중지 스위치 활성 ({halted})")
        # The cap can only be enforced where the notional is knowable up front,
        # i.e. limit orders. A market order's fill price is unknown until it
        # fills — one more reason the real-money strategy is 지정가.
        cap = settings.live_max_order_value
        if cap > 0 and price is not None:
            notional = qty * float(price)
            if notional > cap:
                log.error("KIS order exceeds cap: %s %s x%d @ %s = %s > %s",
                          side, code, qty, price, f"{notional:,.0f}", f"{cap:,.0f}")
                return OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                                   price=price, raw={"cap": cap, "notional": notional},
                                   error=f"주문금액 {notional:,.0f}원이 상한 {cap:,.0f}원 초과")
        elif cap > 0 and price is None and not self.is_mock:
            log.warning("KIS market order not covered by live_max_order_value: %s %s x%d",
                        side, code, qty)

        if self.is_mock:
            mock_id = f"MOCK-{int(time.time()*1000)}"
            return OrderResult(ok=True, order_id=mock_id, code=code, side=side, qty=qty,
                               price=price, raw={"mock": True}, error=None)

        path = "/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": code.zfill(6),
            "ORD_DVSN": "01" if price is None else "00",  # 01 시장가, 00 지정가
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0" if price is None else str(round_to_tick(float(price))),
        }
        tr_id = self.tr_set["sell" if side == "SELL" else "buy"]
        # Throttle rejections ("초당 거래건수 초과") mean the order was NOT
        # accepted — retrying verbatim cannot double-execute. All other
        # rejections stay no-retry (a rejected sell today = 미래에셋 held all
        # weekend, 2026-07-31).
        last: OrderResult | None = None
        for attempt in range(3):
            try:
                headers = self._headers(tr_id, body=body)  # hashkey call inside (gated)
                self._gate()  # separate slot for the order POST itself
                r = requests.post(self.host + path,
                                  headers=headers,
                                  data=json.dumps(body), timeout=15)
            except requests.RequestException as e:
                return OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                                   price=price, raw={}, error=str(e))

            d: dict[str, Any] = {}
            try:
                d = r.json()
            except Exception:  # noqa: BLE001
                pass
            rt_cd = d.get("rt_cd")
            if r.status_code == 200 and rt_cd == "0":
                out = d.get("output") or {}
                return OrderResult(ok=True,
                                   order_id=str(out.get("ODNO") or out.get("KRX_FWDG_ORD_ORGNO") or ""),
                                   code=code, side=side, qty=qty, price=price, raw=d, error=None)
            err = f"{rt_cd} {d.get('msg1', r.text[:200])}"
            msg_cd = str(d.get("msg_cd") or "")
            last = OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                               price=price, raw=d, error=err)
            # "영업일이 아닙니다" is the broker stating the market is shut. On a
            # paper account this is the ONLY such signal we get — CTCA0903R is
            # a real-env TR — and it arrives at 09:00, hours before the 15:20
            # simulated strategies would write fills for the non-existent
            # session. Record it so they skip.
            try:
                from .trading_calendar import looks_closed, mark_closed
                if looks_closed(err):
                    mark_closed()
                    return last          # retrying a closed market is pointless
            except Exception:  # noqa: BLE001
                pass
            if attempt < 2 and any(m in err or m in msg_cd for m in _TOKEN_MARKERS):
                log.warning("KIS token rejection %s %s x%d (attempt %d/3) — re-auth and retry: %s",
                            side, code, qty, attempt + 1, err)
                self._drop_token()  # force a fresh issue on the next _headers()
                time.sleep(1.0)
                continue
            if attempt < 2 and any(m in err or m in msg_cd for m in _THROTTLE_MARKERS):
                log.warning("KIS throttle rejection %s %s x%d (attempt %d/3) — retrying in 2.5s: %s",
                            side, code, qty, attempt + 1, err)
                time.sleep(2.5)
                continue
            return last
        return last  # pragma: no cover — loop always returns

    def cancel_order(self, *, code: str, side: str, qty: int,
                     org_no: str, orgn_odno: str,
                     ord_dvsn: str = "00") -> OrderResult:
        """Cancel the remaining quantity of a resting order (TR *TTC0803U).

        Needed the moment an account submits limit orders: an unfilled limit
        ties up 주문가능현금 for the rest of the session, so the sweep that
        retires it at the account's cutoff has to be able to actually cancel.

        `org_no` (KRX_FWDG_ORD_ORGNO) and `orgn_odno` (ODNO) come from the
        original order's stored KIS response.

        Deliberately NOT gated on the kill switch. The switch exists to stop
        NEW exposure; blocking cancels would strand orders in the book at
        exactly the moment someone has decided to stop trading.
        """
        qty = int(qty)
        if not org_no or not orgn_odno:
            return OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                               price=None, raw={},
                               error="원주문 식별자(KRX_FWDG_ORD_ORGNO/ODNO) 없음")
        if self.is_mock:
            return OrderResult(ok=True, order_id=orgn_odno, code=code, side=side, qty=qty,
                               price=None, raw={"mock": True, "cancelled": True}, error=None)

        path = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": str(org_no),
            "ORGN_ODNO": str(orgn_odno),
            "ORD_DVSN": ord_dvsn,
            "RVSE_CNCL_DVSN_CD": "02",   # 01 정정 / 02 취소
            "ORD_QTY": "0",              # ignored when QTY_ALL_ORD_YN = Y
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",       # 잔량 전부
        }
        tr_id = self.tr_set["cancel"]
        last: OrderResult | None = None
        for attempt in range(3):
            try:
                headers = self._headers(tr_id, body=body)
                self._gate()
                r = requests.post(self.host + path, headers=headers,
                                  data=json.dumps(body), timeout=15)
            except requests.RequestException as e:
                return OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                                   price=None, raw={}, error=str(e))

            d: dict[str, Any] = {}
            try:
                d = r.json()
            except Exception:  # noqa: BLE001
                pass
            rt_cd = d.get("rt_cd")
            if r.status_code == 200 and rt_cd == "0":
                out = d.get("output") or {}
                return OrderResult(ok=True, order_id=str(out.get("ODNO") or orgn_odno),
                                   code=code, side=side, qty=qty, price=None, raw=d, error=None)
            err = f"{rt_cd} {d.get('msg1', r.text[:200])}"
            msg_cd = str(d.get("msg_cd") or "")
            last = OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                               price=None, raw=d, error=err)
            # Unlike a new order, re-sending a cancel is safe — the second one
            # is rejected as "이미 처리" rather than doubling anything.
            if attempt < 2 and any(m in err or m in msg_cd for m in _TOKEN_MARKERS):
                log.warning("KIS token rejection on cancel %s (attempt %d/3): %s",
                            orgn_odno, attempt + 1, err)
                self._drop_token()
                time.sleep(1.0)
                continue
            if attempt < 2 and any(m in err or m in msg_cd for m in _THROTTLE_MARKERS):
                log.warning("KIS throttle rejection on cancel %s (attempt %d/3): %s",
                            orgn_odno, attempt + 1, err)
                time.sleep(2.5)
                continue
            return last
        return last  # pragma: no cover — loop always returns


# ─── Per-account clients (lazy singletons) ───────────────────────
#
# One client per account, not per call: each holds its own access token and
# KIS kills the previous token the instant a new one is issued for the same
# appkey. Rebuilding clients would thrash the 1/min issuance limit.
#
# Accounts are keyed by name. "main" is the original account backed by the
# KIS_* settings; "cafe" is the second real account (KIS_CAFE_*) that trades
# the cafe screener picks. Redis keys for tokens, cooldowns and the request
# gate are already scoped by appkey hash (`_appkey_scope`), so two accounts
# with different appkeys never contend — which is exactly why the second
# account MUST use its own appkey.

ACCOUNT_MAIN = "main"
ACCOUNT_CAFE = "cafe"

_clients: dict[str, KISClient] = {}
_clients_lock = threading.Lock()


class AccountNotConfigured(RuntimeError):
    """Raised when an account's credentials are absent.

    Callers treat this as "skip this strategy today", not as a failure: the
    cafe account is optional and the system must run normally without it.
    """


def _build_client(account: str) -> KISClient:
    if account == ACCOUNT_MAIN:
        return KISClient()
    if account == ACCOUNT_CAFE:
        if not (settings.kis_cafe_app_key and settings.kis_cafe_app_secret
                and settings.kis_cafe_account_no):
            missing = [n for n, v in (
                ("KIS_CAFE_APP_KEY", settings.kis_cafe_app_key),
                ("KIS_CAFE_APP_SECRET", settings.kis_cafe_app_secret),
                ("KIS_CAFE_ACCOUNT_NO", settings.kis_cafe_account_no)) if not v]
            raise AccountNotConfigured(
                "cafe 계좌 미설정 — " + ", ".join(missing) + " 없음")
        if settings.kis_cafe_app_key == settings.kis_app_key:
            # Same appkey means shared token and shared rate limit: the 09:00
            # real-account run and the 15:28 cafe run would evict each other's
            # token. Refuse rather than debug that at 15:28.
            raise AccountNotConfigured(
                "cafe 계좌의 appkey 가 기본 계좌와 같다 — KIS 한도는 appkey 단위라 "
                "토큰과 호출 한도를 서로 깎는다. 별도 appkey 를 발급할 것.")
        return KISClient(
            env=settings.kis_cafe_env or settings.kis_env,
            app_key=settings.kis_cafe_app_key,
            app_secret=settings.kis_cafe_app_secret,
            account_no=settings.kis_cafe_account_no,
            account_product=settings.kis_cafe_account_product or None)
    raise ValueError(f"unknown account: {account!r}")


def get_kis_client(account: str = ACCOUNT_MAIN) -> KISClient:
    """Client for `account`. Default keeps every existing call site unchanged."""
    c = _clients.get(account)
    if c is None:
        with _clients_lock:
            c = _clients.get(account)
            if c is None:
                c = _build_client(account)
                _clients[account] = c
    return c


def cafe_account_configured() -> bool:
    """True if the cafe account can be built — cheap check for schedulers."""
    try:
        get_kis_client(ACCOUNT_CAFE)
        return True
    except (AccountNotConfigured, ValueError):
        return False
