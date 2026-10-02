"""청산 판정 태스크가 전략 하나의 실패로 **통째로** 멈추지 않는지 고정한다.

2026-09-23~10-02 6거래일 동안 cafe 계좌 토큰 발급이 403 으로 거부됐다.
`close_bracket_exits` 는 전략 15개를 순서대로 도는데, 10번째(cafereal)의
잔고 조회 예외가 태스크를 죽여 그 뒤의 coolreal·acct1~4 청산과 **limit 시뮬의
매수 판정**이 한 번도 돌지 않았다 — limit 곡선이 9/21 이후 매수 0건으로 멈췄다.
"""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("celery")

from app.api.db import STRATEGY_CAFEREAL
from app.api.services import live_trader as lt
from app.api.services import notify
from app.api.services import trading_calendar


@pytest.fixture
def run(monkeypatch):
    from app.api.workers import tasks

    calls = {"exits": [], "sync": [], "limit": 0}

    def fake_exits(strategy):
        calls["exits"].append(strategy)
        if strategy == STRATEGY_CAFEREAL:
            raise RuntimeError("403 Client Error: Forbidden for url: .../oauth2/tokenP")
        return {"status": "ok", "exits": []}

    def fake_limit():
        calls["limit"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(trading_calendar, "is_market_open", lambda d=None: True)
    monkeypatch.setattr(lt, "evaluate_bracket_exits", fake_exits)
    monkeypatch.setattr(lt, "evaluate_limit_entries", fake_limit)
    monkeypatch.setattr(lt, "sync_account", lambda strategy: calls["sync"].append(strategy))
    monkeypatch.setattr(notify, "notify_bracket_exits", lambda *a, **kw: None)
    task = tasks.close_bracket_exits_task
    monkeypatch.setattr(task, "update_state", lambda **kw: None)
    return task, calls


def test_one_failing_strategy_does_not_stop_the_rest(run):
    task, calls = run

    with pytest.raises(RuntimeError, match=STRATEGY_CAFEREAL):
        task.run()

    # 실패한 전략 뒤의 전략도 전부 판정됐다.
    assert calls["exits"] == list(lt.BRACKET_STRATEGIES)
    # limit 매수 판정과 그 스냅샷도 돌았다 — 이번 사고에서 사라졌던 것.
    assert calls["limit"] == 1
    assert "limit" in calls["sync"]
    # 실패한 전략은 스냅샷을 찍지 않는다(반쯤 된 상태로 곡선 점을 남기지 않게).
    assert STRATEGY_CAFEREAL not in calls["sync"]


def test_no_failure_returns_results(run, monkeypatch):
    task, calls = run
    monkeypatch.setattr(lt, "evaluate_bracket_exits",
                        lambda strategy: {"status": "ok", "exits": []})

    res = task.run()

    assert set(lt.BRACKET_STRATEGIES) <= set(res)
    assert res["limit_entries"] == {"status": "ok"}
