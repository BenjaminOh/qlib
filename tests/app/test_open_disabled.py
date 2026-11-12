"""open 전략 끄기(2026-11-12) — 꺼져 있으면 KIS 를 부르지 않고, 결과 이상 알림도 나가지 않는다.

모의 3개월 테스트가 끝나고 기본 계좌(옛 모의 50199531)가 해지돼, 09:00 주문·09:30/15:40 동기화가
매일 계좌 거부 경고만 보내고 있었다.
"""

import pytest

pytest.importorskip("celery")

from app.api.config import settings  # noqa: E402
from app.api.services import live_trader, notify  # noqa: E402
from app.api.workers import tasks  # noqa: E402


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(settings, "live_open_enabled", False)
    calls = []
    monkeypatch.setattr(live_trader, "submit_daily_orders", lambda **kw: calls.append("orders") or {})
    monkeypatch.setattr(live_trader, "sync_account", lambda **kw: calls.append("sync") or {})
    # 개장일 장식이 주말·휴일 테스트에서 먼저 "건너뜀"을 내지 않게 — 언제 돌려도 같은 결과.
    from app.api.services import trading_calendar
    monkeypatch.setattr(trading_calendar, "is_market_open", lambda d: True)
    return calls


def _run(task):
    fn = getattr(task, "run", task)
    return fn()


def test_disabled_open_does_not_touch_kis(off):
    for t in (tasks.live_orders_task, tasks.live_sync_task):
        res = _run(t)
        assert res["status"] == "disabled" and res["strategy"] == "open"
    assert off == []                                       # 주문도 잔고 조회도 안 했다


def test_disabled_is_not_a_failure_for_the_result_watcher():
    res = {"status": "disabled", "strategy": "open", "reason": "open 전략 종료"}
    assert notify.result_problems(res) == []
