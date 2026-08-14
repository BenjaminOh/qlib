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

# TR_ID prefixes: T... = real, V... = paper. Same trailing 7 chars.
_TR = {
    "real":  {"buy": "TTTC0802U", "sell": "TTTC0801U", "balance": "TTTC8434R", "psbl": "TTTC8908R",
              "fills": "TTTC8001R"},
    "paper": {"buy": "VTTC0802U", "sell": "VTTC0801U", "balance": "VTTC8434R", "psbl": "VTTC8908R",
              "fills": "VTTC8001R"},
}

# Minimum spacing between ANY two KIS calls on the same account, across all
# containers (see KISClient._gate). Paper enforces ~1 req/s account-wide;
# real allows ~20 req/s but we stay far under it.
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

        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()
        self._last_call = 0.0  # local fallback for the account-wide call gate

    # ─── Mode helpers ──────────────────────────────────────────

    @property
    def is_mock(self) -> bool:
        """Mock mode is automatic if creds missing or env explicitly 'mock'."""
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
    def _redis_token_key(self) -> str:
        return f"kis:token:{self.env}:{self.cano}"

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
        return f"kis:token:cooldown:{self.env}:{self.cano}"

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
        return f"kis:apislot:{self.env}:{self.cano}"

    def _gate(self) -> None:
        """Space out ALL KIS calls for this account across every container.

        KIS rate-limits per ACCOUNT (paper ~1 req/s), but our calls come from
        web (dashboard polling), worker and scheduler concurrently — per-task
        spacing alone let a 09:00 balance poll collide with a sell order and
        get it rejected (2026-07-31 미래에셋 sell). A redis slot key with a
        TTL equal to the minimum interval serializes them globally; if redis
        is unavailable we still enforce the interval within this process.
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
            holdings.append(Holding(code=str(row.get("pdno")),
                                     name=row.get("prdt_name") or None,
                                     qty=qty, avg_price=avg,
                                     eval_price=ep, eval_value=ev,
                                     pnl=pnl, pnl_pct=pnl_pct))
        summary = (d.get("output2") or [{}])[0]
        cash = float(summary.get("dnca_tot_amt") or 0)
        total = float(summary.get("tot_evlu_amt") or 0)
        return AccountSnapshot(cash=cash, total_eval=total, holdings=holdings)

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


# ─── Module-level singleton (lazy) ───────────────────────────────

_default_client: KISClient | None = None
_default_lock = threading.Lock()


def get_kis_client() -> KISClient:
    global _default_client
    if _default_client is None:
        with _default_lock:
            if _default_client is None:
                _default_client = KISClient()
    return _default_client
