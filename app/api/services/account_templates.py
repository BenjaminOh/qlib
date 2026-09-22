"""계좌마다 고른 **매매 방식(template)** 을 실제 진입 함수로 잇는다 (2026-09-21).

왜 필요한가: 계좌 자격증명은 DB 로 옮겼는데 "그 계좌가 무엇을 사는가"는 여전히
코드였다. 전략마다 beat 슬롯과 래퍼 함수를 손으로 복제하는 구조라, 계좌가 늘 때마다
같은 일을 반복해야 했다(계좌 1개 = 편집 지점 45~50곳).

여기서 바뀌는 것은 **디스패치 방향**이다:

    예전: beat 슬롯 → 전략 전용 래퍼 → 진입 함수
    지금: beat 슬롯 1개(시각) → 그 시각의 템플릿을 고른 계좌들을 순회 → 진입 함수

선례가 둘 있고 그대로 따랐다 — `tasks.cancel_unfilled_orders_task`(계좌 순회),
`tasks.close_bracket_exits_task`(전략 순회). 둘 다 "카탈로그를 순회하니 계좌가
늘어도 beat 를 손댈 일이 없다"는 같은 이유로 쓰였다.

⚠ 전략 id 는 **계좌 슬롯(acct1~4)에서만** 계좌 id 와 같다. main·cafe·cool 은
전략이 open·cafereal·coolreal 이라 다르다. 그래서 전략은 반드시
`ACCOUNT_STRATEGIES` 에서 읽는다 — `template_strategy()` 가 그 유일한 통로다.
"""

from __future__ import annotations

import json
import logging

from ..db import (STRATEGY_ACCT1, STRATEGY_ACCT2, STRATEGY_ACCT3,
                  STRATEGY_ACCT4)
from .holding_attribution import account_strategies

log = logging.getLogger(__name__)

# 지원하는 매매 방식. 화면 드롭다운도 이 목록에서 나온다.
#
# 이름은 시스템의 다른 곳과 같은 어휘를 써야 한다 — 곡선·칩·회고가 이 계열을
# 전부 "카페 모사"로 부른다(`frontend/src/lib/strategies.ts`). 여기만 "카페
# 스크리너 픽"이라고 적어 뒀더니 사용자가 드롭다운에서 찾지 못했다(2026-09-22).
#
# ⚠ 여기 없는 방식(cafeopen·limit·surge)은 진입 함수가 전략 이름을 안에 박아 둬서
#   아직 인자로 표현되지 않는다. 그쪽을 먼저 파라미터화해야 추가할 수 있다.
TEMPLATES: dict[str, str] = {
    "cafe": "카페 모사 · 15:28 현재가 매수",
    "close": "qlib 종가 신호 top-10 매수",
}

# 템플릿 → beat 가 그 템플릿을 실행할 시각(참고용 표기). 실제 시각은
# celery_app.py 의 슬롯이 정한다 — 두 곳이 어긋나면 이 표가 거짓말이 된다.
TEMPLATE_SLOT_HHMM: dict[str, str] = {"cafe": "15:28", "close": "15:21"}

# 템플릿으로 돌릴 수 있는 전략 = 계좌 슬롯 전용 전략.
#
# main·cafe·cool 이 빠진 것은 기능 부족이 아니라 **이중 주문 방지**다. 그 셋은
# celery_app.py 에 전용 beat 슬롯이 이미 있다(`live-orders-at-close-cafereal`·
# `-coolreal` 이 같은 15:28). 거기에 `live-entries-cafe` 까지 그 계좌를 집으면
# 같은 분에 두 번 주문이 나간다.
SLOT_STRATEGIES: frozenset[str] = frozenset(
    (STRATEGY_ACCT1, STRATEGY_ACCT2, STRATEGY_ACCT3, STRATEGY_ACCT4))


def template_strategy(account_id: str) -> str | None:
    """이 계좌를 템플릿으로 돌릴 때 주문에 붙일 전략. 대상이 아니면 `None`.

    **계좌 id 를 전략으로 쓰면 안 된다.** `live_trader._account_for` 는 모르는
    전략을 만나면 기본 계좌(main)로 폴백하므로, 지어낸 전략 id 는 그 즉시
    "남의 계좌로 주문이 나간다"가 된다. 여기서 `None` 을 돌려주는 계좌는
    호출부가 **건너뛰어야** 한다.

    같은 이유로 `holding_attribution.primary_strategy` 도 쓰지 않는다 — 그쪽은
    미등록 계좌에 `open` 을 돌려주는 읽기용 기본값이라, 주문 경로에서는 똑같은
    사고가 된다. 빈 튜플을 그대로 주는 `account_strategies` 가 맞는 통로다.
    """
    strategies = account_strategies(account_id)   # 미등록 계좌면 빈 튜플
    if not strategies:
        return None
    strategy = strategies[0]
    return strategy if strategy in SLOT_STRATEGIES else None


def account_plans(template: str) -> list[dict]:
    """이 템플릿을 고른 **활성 계좌** 목록.

    자격증명 유무는 여기서 보지 않는다 — 진입 함수가 `AccountNotConfigured` 를
    "오늘은 건너뜀"으로 처리하고, 그 편이 사유를 한 곳에서만 다루게 한다.
    """
    from ..db import SessionLocal, TradingAccount

    plans: list[dict] = []
    try:
        with SessionLocal() as db:
            rows = (db.query(TradingAccount)
                      .filter(TradingAccount.template == template)
                      .order_by(TradingAccount.account_id).all())
            for row in rows:
                if getattr(row, "strategy_enabled", True) is False:
                    continue
                if getattr(row, "enabled", True) is False:
                    continue
                strategy = template_strategy(row.account_id)
                if strategy is None:
                    # 전용 beat 슬롯이 있거나 카탈로그에 없는 계좌. 전자는 그쪽이
                    # 진실이고, 후자는 전략을 지어내면 main 으로 새어 나간다.
                    log.info("계좌 %s: 템플릿 대상이 아니다 — 건너뛴다",
                             row.account_id)
                    continue
                params: dict = {}
                raw = getattr(row, "strategy_params", None)
                if raw:
                    try:
                        params = json.loads(raw) or {}
                    except Exception:  # noqa: BLE001
                        # 설정이 깨졌다고 주문을 내면 안 된다 — 기본값으로
                        # 조용히 돌리는 쪽이 더 위험하다(ret20 상한이 사라진다).
                        log.warning("계좌 %s: strategy_params 파싱 실패 — 건너뛴다",
                                    row.account_id)
                        continue
                plans.append({"account_id": row.account_id,
                              "strategy": strategy,
                              "params": params})
    except Exception as exc:  # noqa: BLE001 — DB 가 없으면 돌릴 계좌도 없다
        log.warning("계좌 템플릿 조회 실패(%s): %s", template, exc)
    return plans


def _run_cafe(plan: dict) -> dict:
    from .market_screener import _submit_cafe_like

    ret20_max = plan["params"].get("ret20_max")
    return _submit_cafe_like(None, strategy=plan["strategy"],
                             ret20_max=float(ret20_max) if ret20_max else None,
                             real=True)


def _run_close(plan: dict) -> dict:
    from .live_trader import submit_daily_orders

    # simulated=False → 실주문. 주문 방식(시장가/지정가)은 그 계좌의
    # trading_accounts 행이 정한다 — 여기서 정하지 않는다.
    return submit_daily_orders(strategy=plan["strategy"], simulated=False)


_RUNNERS = {"cafe": _run_cafe, "close": _run_close}


def run_template(template: str) -> dict:
    """그 템플릿을 고른 계좌를 전부 돌린다. 한 계좌의 실패가 나머지를 막지 않는다."""
    runner = _RUNNERS.get(template)
    if runner is None:
        return {"status": "unknown_template", "template": template}

    plans = account_plans(template)
    if not plans:
        return {"status": "no_accounts", "template": template}

    out: dict[str, dict] = {}
    for plan in plans:
        try:
            out[plan["account_id"]] = runner(plan)
        except Exception as exc:  # noqa: BLE001
            # 계좌 하나가 터져도 다음 계좌는 돌아야 한다. 예전 구조에서는
            # 전략마다 태스크가 따로라 이 격리가 저절로 됐다.
            log.exception("계좌 %s 템플릿 %s 실행 실패", plan["account_id"], template)
            out[plan["account_id"]] = {"status": "error", "error": str(exc)[:200]}
    return {"status": "ok", "template": template, "accounts": out}
