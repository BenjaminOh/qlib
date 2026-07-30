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
        """Best-effort redis client — None if unavailable (never raises)."""
        try:
            import redis  # type: ignore[import-not-found]
            return redis.from_url(settings.celery_broker_url)
        except Exception:  # noqa: BLE001
            return None

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
            if self._token and self._token_expires_at - now > 600:  # >10min left
                return self._token
            r_client = self._redis()
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
            url = f"{self.host}/oauth2/tokenP"
            payload = {"grant_type": "client_credentials",
                       "appkey": self.app_key, "appsecret": self.app_secret}
            r = requests.post(url, json=payload, timeout=10)
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
            return self._token

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

    def _hashkey(self, body: dict) -> str:
        if self.is_mock:
            return "MOCK_HASHKEY"
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
        # die on a transient server hiccup.
        last_exc: Exception | None = None
        for attempt in range(3):
            r = requests.get(self.host + path, headers=self._headers(self.tr_set["balance"]),
                             params=params, timeout=15)
            if r.status_code == 200:
                break
            log.warning(
                "KIS get_balance HTTP %s (attempt %d/3): %s",
                r.status_code, attempt + 1, r.text[:300],
            )
            if r.status_code in (401, 500):
                self._drop_token()  # token may have been invalidated elsewhere
            try:
                r.raise_for_status()
            except requests.HTTPError as exc:
                last_exc = exc
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
            # Risk flags — the ONLY fundamental-risk signal this system has:
            # trht_yn: 거래정지 여부, iscd_stat_cls_code: 51 관리종목 /
            # 52 투자위험 / 53 투자경고, mrkt_warn_cls_code: 시장경고.
            "halted": str(d.get("trht_yn") or "N").upper() == "Y",
            "status_code": str(d.get("iscd_stat_cls_code") or ""),
            "warn_code": str(d.get("mrkt_warn_cls_code") or ""),
        }

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
        try:
            r = requests.post(self.host + path,
                              headers=self._headers(tr_id, body=body),
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
        if r.status_code != 200 or rt_cd != "0":
            return OrderResult(ok=False, order_id=None, code=code, side=side, qty=qty,
                               price=price, raw=d,
                               error=f"{rt_cd} {d.get('msg1', r.text[:200])}")
        out = d.get("output") or {}
        return OrderResult(ok=True,
                           order_id=str(out.get("ODNO") or out.get("KRX_FWDG_ORD_ORGNO") or ""),
                           code=code, side=side, qty=qty, price=price, raw=d, error=None)


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
