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

전략 id 는 계좌 id 와 같다(`models.STRATEGY_ACCT1` 주석 참조). 그래서 여기서
계좌를 찾으면 전략도 따라온다 — 둘을 따로 짝지을 필요가 없다.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

# 지원하는 매매 방식. 화면 드롭다운도 이 목록에서 나온다.
#
# ⚠ 여기 없는 방식(cafeopen·limit·surge)은 진입 함수가 전략 이름을 안에 박아 둬서
#   아직 인자로 표현되지 않는다. 그쪽을 먼저 파라미터화해야 추가할 수 있다.
TEMPLATES: dict[str, str] = {
    "cafe": "카페 스크리너 픽 · 15:28 현재가 매수",
    "close": "종가 신호 top-10 매수",
}

# 템플릿 → beat 가 그 템플릿을 실행할 시각(참고용 표기). 실제 시각은
# celery_app.py 의 슬롯이 정한다 — 두 곳이 어긋나면 이 표가 거짓말이 된다.
TEMPLATE_SLOT_HHMM: dict[str, str] = {"cafe": "15:28", "close": "15:21"}


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
                              "strategy": row.account_id,   # 전략 id = 계좌 id
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
