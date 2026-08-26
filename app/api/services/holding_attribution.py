"""보유 종목 → 매수 전략 귀속.

KIS 잔고는 "이 종목을 누가 왜 샀는가"를 모른다. 화면의 "현재 보유 종목"은 그
잔고를 그대로 그리므로, 6종목이 떠 있어도 qlib 모델(open)이 산 것인지 카페
픽(cafereal)이 산 것인지 손으로 산 것인지 구분할 수 없었다. 정보는 이미
``orders.strategy`` 에 있다 — 잔고와 원장이 한 번도 조인된 적이 없었을 뿐이다.

**설계의 핵심: 원장이 수량을 결정하게 두지 않는다.**

순진한 방법은 "체결로 간주할 만한 상태의 주문을 순수량으로 합산 = 보유"인데,
이 코드베이스에서 그 계산은 cafe 계좌에 대해 거의 항상 틀린다:

  1. cafe 계좌 기본 정책은 현재가 −3% 지정가인데(db/session.py) 카페 픽은 대개
     상한가라 거의 체결되지 않는다.
  2. cafereal 주문은 status="SUBMITTED" 로 기록되고 Fill 행이 없다.
  3. **cafereal 을 정산하는 호출이 코드베이스에 없다.** reconcile_fills() 의
     유일한 호출부가 인자 없이 부르므로 strategy=open · main 클라이언트다.
  4. cafe 의 15:30 취소 컷오프도 실행된 적이 없다 — cancel_unfilled_orders() 를
     인자 없이 부르면 account_id="main" 이고, main 은 컷오프가 없어 즉시 리턴한다.

즉 cafereal BUY 행은 체결 여부와 무관하게 영구히 SUBMITTED 다. 그걸 보유로 세면
**한 번도 산 적 없는 종목을 "카페실계좌 보유"라고 주장**하게 된다.

그래서 이 모듈은 **KIS 보유수량을 분모로 고정**하고, 원장은 그 분모를 어떻게
나눌지에 대한 가중치로만 쓴다. 그리고 각 청구가 확정인지 추정인지를 함께
돌려준다 — 추정치를 확정처럼 보여주는 것이 이 기능의 가장 큰 실패 모드다.

가격을 전혀 다루지 않는다. 그래서 qlib 을 import 하지 않으며, 30초마다 폴링되는
/balance 경로에 안전하게 놓을 수 있다 (routers/live.py 가 API 프로세스에 qlib 을
끌어들이지 않으려 lazy import 하는 규약을 지킨다).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..db import (
    ACCOUNT_STRATEGIES, DEFAULT_ACCOUNT_ID, EXIT_KIND_LADDER,
    Fill, Order, SessionLocal,
)

log = logging.getLogger(__name__)

# 의사(pseudo) 전략. 원장으로 설명되지 않는 수량 — 수동 매매, 대체입고, 액면분할
# 같은 원장 밖 수량 변동. models.STRATEGY_* 에 넣지 말 것: 이건 전략이 아니라
# "전략을 모른다"는 표시다.
MANUAL = "manual"

# 주문 상태를 신뢰도로 나눈다. 하나의 "체결로 간주" 튜플로 전 전략을 재는 것은
# 불가능하다 — 같은 SUBMITTED 가 open 에서는 09:20 정산 전 임시 상태이고
# cafereal 에서는 영구 상태(체결 여부 미상)다.
CONFIRMED_STATUSES = ("FILLED", "SIMULATED")
UNCONFIRMED_STATUSES = ("SUBMITTED", "PARTIAL")
EXCLUDED_STATUSES = ("REJECTED", "CANCELLED", "PENDING")

# 귀속 결과의 신뢰도.
STATUS_CONFIRMED = "confirmed"   # 원장 = KIS. 미확정도 미상 잔여도 없다.
STATUS_INFERRED = "inferred"     # 미확정 주문 또는 미상 잔여가 섞여 있다.
STATUS_MISMATCH = "mismatch"     # 확정 원장이 KIS 를 초과하거나 순수량이 음수다.
STATUS_UNKNOWN = "unknown"       # 귀속 자체를 수행하지 못했다.


def account_strategies(account: str) -> tuple[str, ...]:
    """이 계좌에 실주문을 내는 전략들. 미등록 계좌면 빈 튜플."""
    return ACCOUNT_STRATEGIES.get(account, ())


def primary_strategy(account: str) -> str:
    """계좌의 대표 전략 — PositionSnapshot 폴백과 매매이력 기본 원장이 읽는 것."""
    strategies = account_strategies(account)
    return strategies[0] if strategies else ACCOUNT_STRATEGIES[DEFAULT_ACCOUNT_ID][0]


@dataclass(frozen=True)
class LedgerEvent:
    """귀속에 필요한 만큼으로 줄인 주문 한 건. DB·가격 무관."""
    strategy: str
    side: str                  # BUY / SELL
    qty: int                   # 주문 수량
    status: str
    fill_qty: int | None = None  # Fill 행 합계. None = 정산 기록 없음


@dataclass(frozen=True)
class Net:
    """한 전략이 이 종목에 대해 주장하는 순수량, 신뢰도별로 분리."""
    confirmed: int = 0
    unconfirmed: int = 0
    went_negative: bool = False   # 매도가 매수를 넘어섰다 = 원장 신뢰 불가

    @property
    def total(self) -> int:
        return self.confirmed + self.unconfirmed


@dataclass(frozen=True)
class StrategyClaim:
    strategy: str      # "open" | "cafereal" | MANUAL
    qty: int           # 배분 후 최종 수량. Σqty == kis_qty 가 보장된다.
    ledger_qty: int    # 원장이 주장한 순수량(배분 전) — 진단·툴팁용
    confirmed: bool


@dataclass(frozen=True)
class CodeAttribution:
    code: str
    kis_qty: int
    claims: tuple[StrategyClaim, ...]
    status: str
    ledger_total: int


def replay_net_qty(events: Sequence[LedgerEvent]) -> Net:
    """주문 이벤트들 → 신뢰도별 순수량. 순수 함수 (DB·가격·시간 무관).

    확정과 미확정을 **따로** 합산한 뒤 합친다. 순서에 의존하지 않는 이유:
    합계는 결합법칙을 따르고, 이 함수가 답해야 하는 질문은 "지금 몇 주인가"이지
    "언제 얼마에 샀는가"가 아니다. 후자는 _position_timeline 의 일이다.

    미확정 순수량이 음수면(미확정 매도가 미확정 매수를 넘음) 그만큼을 확정에서
    뺀다 — 확정 매수분을 미확정 매도로 팔았다는 뜻이기 때문이다.
    """
    conf = 0
    unconf = 0
    for e in events:
        if e.status in EXCLUDED_STATUSES:
            continue
        # 정산이 실제 체결수량을 기록해 두었으면 그쪽이 진실이다. 지금은
        # Fill.qty 가 Order.qty 의 복사본이지만(정산기가 ccld_qty 를 버린다),
        # Fill 을 읽어두면 그게 고쳐지는 순간 귀속이 저절로 정확해진다.
        qty = e.fill_qty if e.fill_qty is not None else e.qty
        signed = qty if (e.side or "").upper() == "BUY" else -qty
        if e.status in CONFIRMED_STATUSES:
            conf += signed
        elif e.status in UNCONFIRMED_STATUSES:
            unconf += signed
        # 그 외 미지의 상태는 세지 않는다. 모르는 것을 보유로 세지 않는다.

    went_negative = (conf + unconf) < 0
    if unconf < 0:
        conf += unconf      # 미확정 매도가 확정 매수분을 깎는다
        unconf = 0
    return Net(confirmed=max(conf, 0), unconfirmed=max(unconf, 0),
               went_negative=went_negative)


def _largest_remainder(shares: Mapping[str, int], total: int) -> dict[str, int]:
    """비례 배분 후 최대잉여법으로 반올림 — Σ결과 == total 을 보장한다.

    합이 어긋나면 화면에서 배지 수량의 합이 수량 컬럼과 달라진다.
    """
    pool = sum(shares.values())
    if pool <= 0 or total <= 0:
        return {k: 0 for k in shares}
    exact = {k: v * total / pool for k, v in shares.items()}
    out = {k: int(v) for k, v in exact.items()}
    left = total - sum(out.values())
    # 잉여가 큰 순으로 1주씩. 동률이면 원장 수량이 많은 쪽, 그다음 이름순 —
    # 결정적이어야 같은 입력에 같은 화면이 나온다.
    order = sorted(shares, key=lambda k: (-(exact[k] - out[k]), -shares[k], k))
    for k in order[:left]:
        out[k] += 1
    return out


def allocate(kis_qty: int, nets: Mapping[str, Net]) -> tuple[list[StrategyClaim], str]:
    """KIS 실제 수량을 전략별 청구에 배분한다.

    확정 청구를 먼저 채우고, 남은 몫을 미확정에 비례 배분하고, 그래도 남으면
    MANUAL 이다. 비례 축소를 전체에 균등 적용하지 않는 이유: 오차는 균등하지
    않고 **미확정 행에 집중**돼 있다(모듈 docstring 의 2~4번).
    """
    conf = {s: n.confirmed for s, n in nets.items() if n.confirmed > 0}
    unconf = {s: n.unconfirmed for s, n in nets.items() if n.unconfirmed > 0}
    conf_total = sum(conf.values())
    unconf_total = sum(unconf.values())
    ledger_total = conf_total + unconf_total
    negative = any(n.went_negative for n in nets.values())

    if kis_qty <= 0:
        # 보유하지 않는 종목. 유령 원장이 있으면 그 사실만 알린다.
        return [], STATUS_MISMATCH if (ledger_total or negative) else STATUS_CONFIRMED

    remaining = kis_qty
    mismatch = negative

    if conf_total <= remaining:
        conf_alloc = dict(conf)
        remaining -= conf_total
    else:
        conf_alloc = _largest_remainder(conf, remaining)
        remaining = 0
        mismatch = True     # 확정 원장이 실제 보유를 초과했다

    if unconf and remaining > 0:
        if unconf_total <= remaining:
            unconf_alloc = dict(unconf)
            remaining -= unconf_total
        else:
            unconf_alloc = _largest_remainder(unconf, remaining)
            remaining = 0
    else:
        unconf_alloc = {s: 0 for s in unconf}

    claims: list[StrategyClaim] = []
    for strategy in sorted(set(conf_alloc) | set(unconf_alloc)):
        qty = conf_alloc.get(strategy, 0) + unconf_alloc.get(strategy, 0)
        if qty <= 0:
            continue
        claims.append(StrategyClaim(
            strategy=strategy,
            qty=qty,
            ledger_qty=nets[strategy].total,
            # 미확정분이 한 주라도 섞였으면 이 청구는 추정이다.
            confirmed=unconf_alloc.get(strategy, 0) == 0,
        ))
    claims.sort(key=lambda c: (-c.qty, c.strategy))

    if remaining > 0:
        claims.append(StrategyClaim(strategy=MANUAL, qty=remaining,
                                    ledger_qty=0, confirmed=False))

    if mismatch:
        status = STATUS_MISMATCH
    elif all(c.confirmed for c in claims) and remaining == 0 and claims:
        status = STATUS_CONFIRMED
    else:
        status = STATUS_INFERRED
    return claims, status


def _pending_reservation(status: str | None, side: str | None,
                         reasons_json: str | None) -> bool:
    """아직 체결되지 않은 조건부 매도 예약인가.

    ``EXIT_KIND_LADDER`` 표식은 `reserve_ladder_exits` 가 찍는다. 이 모듈이
    live_trader 를 import 하면 순환이 되므로 표식 문자열은 db.models 에 있다.
    """
    if (side or "").upper() != "SELL" or status not in UNCONFIRMED_STATUSES:
        return False
    try:
        exit_info = (json.loads(reasons_json or "{}") or {}).get("exit") or {}
    except (ValueError, TypeError):
        return False
    return exit_info.get("kind") == EXIT_KIND_LADDER


def _events_by_code(db, codes: Sequence[str], strategies: Sequence[str]
                    ) -> dict[str, list[LedgerEvent]]:
    """주문 2회 쿼리로 종목별 이벤트를 모은다.

    ``orders.strategy`` 에는 인덱스가 있고 ``orders.code`` 에는 없다
    (db/models.py). 그래서 인덱스가 전략으로 먼저 좁히고 code 조건이 반환 행을
    줄이는 순서가 된다.
    """
    rows = (db.query(Order.id, Order.code, Order.strategy, Order.side,
                     Order.qty, Order.status, Order.reasons_json)
              .filter(Order.strategy.in_(list(strategies)),
                      Order.code.in_(list(codes)))
              .all())
    if not rows:
        return {}

    from sqlalchemy import func

    fill_qty: dict[int, int] = {}
    order_ids = [r.id for r in rows]
    # IN 절이 무한정 커지지 않도록 나눠 조회한다 (SQLite 는 변수 999개 상한).
    for i in range(0, len(order_ids), 500):
        chunk = order_ids[i:i + 500]
        for oid, total in (db.query(Fill.order_id, func.sum(Fill.qty))
                             .filter(Fill.order_id.in_(chunk))
                             .group_by(Fill.order_id).all()):
            fill_qty[oid] = int(total or 0)

    out: dict[str, list[LedgerEvent]] = defaultdict(list)
    for r in rows:
        # 미체결 사다리 예약은 보유를 줄이지 않는다. 09:25 에 걸어둔
        # "+10% 에 절반 판다"는 조건부 주문이지 매도가 아닌데, 미확정 매도로
        # 세면 그 절반이 원장에서 빠져나가 **보유 종목 배지의 절반이
        # "수동/미상"으로 뒤집힌다.** 체결되면 FILLED/PARTIAL 이 되고 그때부터
        # 정상적으로 세어진다.
        if (_pending_reservation(r.status, r.side, r.reasons_json)
                and not fill_qty.get(r.id)):
            continue
        out[r.code].append(LedgerEvent(
            strategy=r.strategy, side=r.side, qty=int(r.qty or 0),
            status=r.status, fill_qty=fill_qty.get(r.id),
        ))
    return out


def attribute_holdings(kis_qty: Mapping[str, int], account: str,
                       *, db=None) -> dict[str, CodeAttribution]:
    """{종목코드: 귀속} — ``kis_qty`` 가 분모다. 절대 raise 하지 않는다.

    호출부(/balance)는 KIS 장애에도 500 을 내면 안 되는 경로이므로, 여기서 무슨
    일이 나든 빈 dict 로 물러난다. 화면은 배지 없이 기존과 똑같이 그려진다.
    """
    kis_qty = {c: int(q or 0) for c, q in (kis_qty or {}).items()}
    if not kis_qty:
        return {}

    strategies = account_strategies(account)
    if not strategies:
        # 미등록 계좌 — 원장이 뭐라 하든 귀속할 근거가 없다. 전량 미상.
        log.warning("holding attribution: 미등록 계좌 %r — 전량 manual 처리", account)
        return {
            code: CodeAttribution(
                code=code, kis_qty=qty, ledger_total=0, status=STATUS_INFERRED,
                claims=((StrategyClaim(MANUAL, qty, 0, False),) if qty > 0 else ()),
            )
            for code, qty in kis_qty.items()
        }

    try:
        if db is not None:
            events = _events_by_code(db, list(kis_qty), strategies)
        else:
            with SessionLocal() as session:
                events = _events_by_code(session, list(kis_qty), strategies)
    except Exception as exc:  # noqa: BLE001
        log.warning("holding attribution: 원장 조회 실패 — 배지 없이 진행: %s", exc)
        return {}

    out: dict[str, CodeAttribution] = {}
    for code, qty in kis_qty.items():
        by_strategy: dict[str, list[LedgerEvent]] = defaultdict(list)
        for e in events.get(code, []):
            by_strategy[e.strategy].append(e)
        nets = {s: replay_net_qty(evs) for s, evs in by_strategy.items()}
        nets = {s: n for s, n in nets.items() if n.total > 0 or n.went_negative}
        claims, status = allocate(qty, nets)
        out[code] = CodeAttribution(
            code=code, kis_qty=qty, claims=tuple(claims), status=status,
            ledger_total=sum(n.total for n in nets.values()),
        )
    return out
