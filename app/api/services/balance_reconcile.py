"""잔고 차이로 원장을 맞추는 정산기.

한 줄 규칙:

    **원장 순수량 − KIS 보유수량 = 우리가 모르는 매도량.**
    그 양을 **우리 미체결 매도 주문에 먼저 배분**하고, **그래도 남으면 수동 매도**다.

두 가지 서로 달라 보이는 문제가 사실 같은 문제다.

**1) 사용자가 MTS 앱에서 직접 판 경우.** `/live/orders` 는 로컬 `orders` 테이블만 읽고
KIS 를 한 번도 부르지 않는다. `reconcile_fills` 도 방향이 DB → KIS 라 우리 `kis_order_id`
로 조회해 없으면 그냥 넘어간다. 그래서 수동 매도는 최근 주문·최근 청산·종목 매매이력·
당일 실현손익 어디에도 나타나지 않고, **전량을 팔면 KIS 잔고에서도 사라져 전 화면에서
흔적 없이 증발한다.**

**2) (구) 09:25 사다리 예약이 체결된 경우.** — 2026-09-07 제거. 2026-08-27 자 주문에만 해당한다. `reconcile_fills` 는 09:20 에 돌고 예약은 09:25 에
걸린다 — 정산기가 이미 지나간 뒤다. 게다가 Pass 2 는 `o.price is None` 인 주문만 보는데
사다리는 지정가라 대상 밖이고, 다음 날 정산기는 `trade_date == today` 라 어제 예약을 안
본다. **체결돼도 정산하는 곳이 아무 데도 없다.**

지금 계좌(모의투자)에서는 체결내역 TR 이 빈 값을 돌려주므로(`kis_fills: 0` 실측, 2026-08-26)
**체결을 알 수 있는 유일한 길이 잔고 차이**다. 그래서 둘을 하나로 푼다.

배분 순서가 핵심이다. 우리 미체결 주문에 먼저 배분하지 않으면 **우리가 낸 사다리 체결을
"사용자가 팔았다"로 오인해 가짜 행을 쓴다.**

수동 **매수**(gap < 0)는 기록하지 않는다. 합성 BUY 를 쓰면 그 종목이 `open` 포지션이 되어
**청산 규칙이 사용자가 직접 산 종목을 팔러 간다.** 지금처럼 `manual` 배지로만 남긴다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..db import (
    EXIT_KIND_MANUAL, Fill, Order, SessionLocal, STRATEGY_OPEN, init_db,
)

log = logging.getLogger(__name__)

# 합성 행은 이 조합으로 **유일하게** 식별된다: 우리가 낸 주문은 반드시
# kis_order_id 를 갖고(REJECTED 는 SUBMITTED 가 되지 못한다), 시뮬 행은
# status="SIMULATED" 다. 되돌릴 때 이 조건으로 지우면 된다.
#   DELETE FROM fills WHERE order_id IN (
#       SELECT id FROM orders WHERE kis_order_id IS NULL AND status='FILLED');
#   DELETE FROM orders WHERE kis_order_id IS NULL AND status='FILLED';
SYNTHETIC_STATUS = "FILLED"


@dataclass
class Gap:
    """한 종목의 원장 vs 잔고 차이. 순수 데이터 — DB·가격 무관."""
    code: str
    ledger_qty: int
    kis_qty: int
    open_sells: list[Order] = field(default_factory=list)

    @property
    def gap(self) -> int:
        return self.ledger_qty - self.kis_qty


def _executed_statuses() -> tuple[str, ...]:
    from .live_trader import EXECUTED_STATUSES
    return EXECUTED_STATUSES


def _filled_qty(o: Order) -> int:
    """이 주문이 실제로 체결한 수량. Fill 이 있으면 그게 진실이다."""
    total = sum(int(f.qty or 0) for f in (o.fills or []))
    return total or int(o.qty or 0)


def _ledger_qty(rows: list[Order]) -> int:
    """**체결된** 주문만으로 되짚은 순보유수량. 양쪽 다 체결분만 센다.

    `live_trader._episode_entry` 는 매수를 미체결까지 세는데(09:00 시장가는
    09:20 정산 전까지 SUBMITTED), 여기서는 **세지 않는다.** 목적이 다르고 오차의
    대가가 비대칭이기 때문이다:

      * 체결된 매수를 원장이 아직 SUBMITTED 로 알고 있으면 → 원장 < 잔고 →
        gap 이 음수 → 그냥 건너뛴다. **그날 봉합이 미뤄질 뿐 피해가 없다.**
      * 체결 안 된 매수를 보유로 세면 → 원장 > 잔고 → **한 번도 가진 적 없는
        포지션에 가짜 청산을 찍는다.** 되돌릴 수는 있지만 그 사이 곡선·알림·
        청산 카드가 전부 거짓말을 한다.

    16:00 시점에는 09:20 정산기가 이미 그날 매수를 FILLED 로 확정해 두었으므로
    평상시 판정에는 차이가 없다.

    매도를 체결분만 세는 것도 이 모듈의 전제다: 미체결 예약 매도를 보유 감소로
    세면 gap 이 0이 되어 **체결을 영영 감지하지 못한다.**
    """
    executed = _executed_statuses()
    cum = 0
    for o in rows:
        side = (o.side or "").upper()
        if o.status not in executed:
            continue
        if side == "BUY":
            cum += _filled_qty(o)
        elif side == "SELL":
            cum -= _filled_qty(o)
            if cum < 0:
                cum = 0
    return cum


def diff_by_code(db: Session, kis_qty: dict[str, int], *,
                 strategy: str = STRATEGY_OPEN) -> list[Gap]:
    """종목별 (원장, 잔고, 미체결 매도 주문). 순수 조회 — 아무것도 쓰지 않는다.

    검사 대상은 **원장에 이력이 있는 종목 ∪ 잔고 보유 종목**이다. 잔고 쪽만 보면
    전량 매도된 종목이 대상에서 빠져 — 정확히 가장 안 보이는 경우를 놓친다.
    """
    codes = {c for (c,) in db.query(Order.code)
                            .filter(Order.strategy == strategy).distinct()}
    codes |= set(kis_qty)
    out: list[Gap] = []
    for code in sorted(codes):
        rows = (db.query(Order)
                  .filter(Order.strategy == strategy, Order.code == code)
                  .order_by(Order.trade_date.asc(), Order.id.asc())
                  .all())
        if not rows:
            continue
        open_sells = [o for o in rows
                      if (o.side or "").upper() == "SELL"
                      and o.status in ("SUBMITTED", "PARTIAL")]
        out.append(Gap(code=code, ledger_qty=_ledger_qty(rows),
                       kis_qty=int(kis_qty.get(code, 0)), open_sells=open_sells))
    return out


def _close_price(code: str, day: date) -> tuple[float | None, str]:
    """(가격, 출처). 그날 종가 → 마지막 종가 순으로 물러난다."""
    from .live_trader import _day_ohlc_any, _last_close
    bar = _day_ohlc_any(code, day)
    if bar and bar.get("close"):
        return float(bar["close"]), "close"
    px = _last_close(code)
    return (float(px), "last_close") if px else (None, "none")


def _write_fill(db: Session, o: Order, qty: int, price: float,
                *, strategy: str) -> None:
    """이 주문의 Fill 을 생성하거나 갱신한다. 실현손익은 net 이다."""
    from .live_trader import _episode_avg, trade_cost
    fee = trade_cost(o.side, qty, price)
    pnl = None
    if (o.side or "").upper() == "SELL":
        avg = _episode_avg(db, o)
        if avg is not None:
            pnl = round((price - avg) * qty - fee - trade_cost("BUY", qty, avg), 2)
    existing = db.query(Fill).filter(Fill.order_id == o.id).first()
    if existing is not None:
        existing.qty, existing.price = qty, price
        existing.fee, existing.pnl = fee, pnl
        return
    db.add(Fill(order_id=o.id, filled_at=datetime.utcnow(), qty=qty,
                price=price, fee=fee, pnl=pnl, strategy=strategy))


# ⚠ 사다리 예약 표식의 생산자가 2026-09-07 에 사라져, 실질적으로 2026-08-27 자
# 유물 주문만 대상이 된다. 새 대상은 생기지 않는다.
def _expire_stale_reservations(db: Session, day: date, *, strategy: str) -> int:
    """어제 이전의 미체결 사다리 예약을 닫는다.

    한국 주식 주문은 **당일 유효**다. 어제 걸어둔 예약은 장 마감에 이미 소멸했는데
    DB 는 그걸 모르고 SUBMITTED 로 남긴다. 10종목이면 하루 10건씩 죽은 행이 쌓인다.
    """
    from .live_trader import _is_ladder_reservation
    rows = (db.query(Order)
              .filter(Order.strategy == strategy,
                      Order.side == "SELL",
                      Order.status == "SUBMITTED",
                      Order.trade_date < day)
              .all())
    n = 0
    for o in rows:
        if not _is_ladder_reservation(o.reasons_json):
            continue
        o.status = "CANCELLED"
        o.error = "장 마감 — 당일 유효 주문 소멸"
        n += 1
    return n


def reconcile_by_balance(trade_date: date | None = None, *,
                         strategy: str = STRATEGY_OPEN,
                         client=None, write: bool = True) -> dict:
    """16:00 — 잔고와 원장의 차이를 메운다. 멱등.

    한 번 쓰면 차이가 0이 되므로 재실행이 저절로 안전하다.

    `write=False` 면 판정만 하고 아무것도 쓰지 않는다(진단용).
    """
    init_db()
    from .live_trader import (
        _account_for, _persist_order, _reset_qlib_caches, _stock_name,
        _last_trading_day,
    )
    from .kis_client import AccountNotConfigured, OrderResult, get_kis_client

    _reset_qlib_caches()
    day = trade_date or _last_trading_day()
    account_id = _account_for(strategy)
    try:
        client = client or get_kis_client(account_id)
    except (AccountNotConfigured, ValueError) as exc:
        log.info("reconcile_balance: %s 건너뜀 — %s", strategy, exc)
        return {"status": "no_account", "strategy": strategy,
                "trade_date": day.isoformat(), "repaired": []}

    # ⚠ 잔고 조회가 실패하면 **아무것도 하지 않는다.** 빈 잔고를 "전량 매도"로
    # 읽으면 원장 전체에 가짜 청산이 찍힌다. 되돌릴 수는 있지만 그 사이 곡선·
    # 알림·청산 카드가 전부 거짓말을 한다.
    try:
        snapshot = client.get_balance()
    except Exception as exc:  # noqa: BLE001
        log.warning("reconcile_balance: 잔고 조회 실패 — 정산 보류: %s", exc)
        return {"status": "balance_unavailable", "strategy": strategy,
                "trade_date": day.isoformat(), "repaired": [], "error": str(exc)}
    kis_qty = {h.code: int(h.qty or 0) for h in snapshot.holdings}

    # 실계좌면 진짜 체결가를 준다. 모의계좌는 빈 dict 를 돌려주므로 종가로 물러난다.
    try:
        ccld = client.get_daily_fills(day, day)
    except Exception as exc:  # noqa: BLE001
        log.info("reconcile_balance: 체결내역 조회 실패 — 종가로 대체: %s", exc)
        ccld = {}
    ccld_by_code: dict[str, dict] = {}
    for f in ccld.values():
        if (f.get("side") or "").upper() == "SELL" and f.get("code"):
            ccld_by_code.setdefault(str(f["code"]), f)

    matched: list[dict] = []      # 우리 미체결 주문에 배분된 것
    manual: list[dict] = []       # 원장에 없던 매도 = 수동 매매
    cap = settings.live_manual_exit_max_per_day
    with SessionLocal() as db:
        gaps = [g for g in diff_by_code(db, kis_qty, strategy=strategy) if g.gap > 0]
        for g in gaps:
            log.info("reconcile_balance %s: 원장 %d / 잔고 %d / 차이 %d",
                     g.code, g.ledger_qty, g.kis_qty, g.gap)

        # 상한을 넘으면 **쓰지 않는다.** 잔고가 부분 응답이거나 조회가 이상한 날
        # 원장이 폭주하는 것을 막는다. 사람이 보고 판단할 몫이다.
        over_cap = len(gaps) > cap
        if over_cap:
            log.error("reconcile_balance: 불일치 %d종목 > 상한 %d — 쓰지 않고 알림만",
                      len(gaps), cap)

        do_write = write and not over_cap
        for g in gaps:
            remaining = g.gap
            # ① 우리 미체결 매도에 먼저 배분한다. 오래된 것부터 — 먼저 낸 주문이
            #    먼저 체결된다(가격·시간 우선). 이 단계가 없으면 우리가 낸 사다리
            #    체결을 수동 매도로 오인한다.
            for o in g.open_sells:
                if remaining <= 0:
                    break
                already = sum(int(f.qty or 0) for f in (o.fills or []))
                room = int(o.qty or 0) - already
                if room <= 0:
                    continue
                take = min(remaining, room)
                px = o.price
                if px is None:
                    px, _src = _close_price(g.code, day)
                    if px is None:
                        continue
                if do_write:
                    o.status = "FILLED" if (already + take) >= o.qty else "PARTIAL"
                    o.price = float(px)
                    _write_fill(db, o, already + take, float(px), strategy=strategy)
                remaining -= take
                matched.append({"code": g.code, "name": _stock_name(g.code),
                                "order_id": o.id, "qty": take,
                                "price": float(px),
                                "status": o.status if do_write else "(dry)"})

            if remaining <= 0:
                continue

            # ② 남은 것은 우리 원장에 없는 매도 — 사용자가 MTS 로 판 것이다.
            hit = ccld_by_code.get(g.code)
            if hit and hit.get("avg_price"):
                px, src = float(hit["avg_price"]), "ccld"
            else:
                px, src = _close_price(g.code, day)
            if px is None:
                log.warning("reconcile_balance %s: 가격을 못 구해 봉합 보류 (%d주)",
                            g.code, remaining)
                continue
            row = {"code": g.code, "name": _stock_name(g.code), "qty": remaining,
                   "price": px, "price_source": src,
                   "ledger_qty": g.ledger_qty, "kis_qty": g.kis_qty}
            manual.append(row)
            if not do_write:
                continue
            reasons = {
                "action": "sell",
                "basis": (f"수동 매도 봉합 — 원장 {g.ledger_qty}주 / 잔고 "
                          f"{g.kis_qty}주. 주문 기록에 없는 {remaining}주가 "
                          f"팔렸다(MTS 직접 매매 등). 체결가는 "
                          + ("KIS 체결내역" if src == "ccld" else "그날 종가 추정")
                          + f" {round(px):,}원."),
                "summary": "", "metrics": {}, "top_features": [],
                "exit": {"kind": EXIT_KIND_MANUAL, "judged": "balance_diff",
                         "price_source": src, "ledger_qty": g.ledger_qty,
                         "kis_qty": g.kis_qty},
            }
            # kis_order_id=None 이 합성 행의 표식이다 — 우리가 낸 주문은 반드시
            # 주문번호를 갖는다. 상태는 새로 만들지 않고 FILLED 를 쓴다: 그래야
            # EXECUTED_STATUSES·CONFIRMED_STATUSES·_episodes·view=real 필터가
            # 전부 손대지 않고도 올바르게 동작한다.
            o = _persist_order(db, day, g.code, "SELL", remaining, float(px),
                               OrderResult(ok=True, order_id=None, code=g.code,
                                           side="SELL", qty=remaining, price=px,
                                           raw={"synthetic": "balance_diff"},
                                           error=None),
                               strategy=strategy, reasons=reasons)
            o.status = SYNTHETIC_STATUS
            db.flush()
            _write_fill(db, o, remaining, float(px), strategy=strategy)

        expired = _expire_stale_reservations(db, day, strategy=strategy) if do_write else 0
        if do_write:
            db.commit()

    result = {"status": "ok", "strategy": strategy, "account_id": account_id,
              "trade_date": day.isoformat(), "written": do_write,
              "over_cap": over_cap, "cap": cap,
              "matched": matched, "manual": manual, "expired": expired}
    log.info("reconcile_balance: %s", json.dumps(
        {k: v for k, v in result.items() if k not in ("matched", "manual")},
        ensure_ascii=False))
    if manual:
        log.warning("reconcile_balance: 수동 매도 %d건 봉합%s — %s",
                    len(manual), "" if do_write else "(미기록)",
                    ", ".join(f"{m['name'] or m['code']} {m['qty']}주" for m in manual))
    return result
