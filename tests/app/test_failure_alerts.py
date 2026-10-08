"""모든 장애·실패는 텔레그램으로 알린다 (2026-10-07 운영 요구).

그날 09:00 주문 태스크 4개가 죽었고, 카페 계좌는 그 전날부터 키가 거부돼 멈춰
있었다. 둘 다 아무 알림이 없어서 사람이 "오늘 매매가 없다"를 보고서야 알았다.
"""

from datetime import datetime

import pytest

pytest.importorskip("requests")

from app.api.services import notify  # noqa: E402


# ─── 결과 dict 에서 실패 신호 찾기 ─────────────────────────────────


@pytest.mark.parametrize("result", [
    {"status": "ok", "buys": 2, "sells": 2, "rejected": 0},
    {"status": "market_closed", "date": "2026-10-09"},
    {"status": "no_accounts", "template": "close"},     # 계좌가 안 붙은 템플릿 — 정상
    {"status": "no_candidates", "trade_date": "2026-10-07"},
    {"main": {"status": "no_cutoff"}, "cafe": {"status": "ok", "cancelled": 0, "failed": 0}},
    "not a dict",
    None,
])
def test_normal_results_are_quiet(result):
    assert notify.result_problems(result) == []


def test_failure_status_is_reported_with_reason():
    p = notify.result_problems({"status": "balance_unavailable", "reason": "KIS 잔고 조회 실패"})
    assert p == ["status=balance_unavailable (KIS 잔고 조회 실패)"]


def test_key_rejection_results_are_left_to_the_daily_key_alert():
    # 키 거부는 계좌·날짜당 1번 따로 알린다. 같은 원인으로 건너뛴 태스크마다 또 보내지 않는다.
    reason = "카페 계좌 KIS 키 거부 — {\"error_code\":\"EGW00105\"}"
    assert notify.result_problems({"status": "no_account", "reason": reason}) == []
    assert notify.result_problems({"cafe": {"status": "no_account", "reason": reason},
                                   "cool": {"status": "ok", "failed": 1}}) == ["cool/failed=1"]


def test_empty_account_slots_are_quiet():
    # acct1~4 는 아직 쓰지 않는 자리다. 매일 no_account 가 나오지만 고장이 아니다.
    reason = "acct1 계좌 미설정(빈 슬롯) — KIS_ACCT1_APP_KEY, KIS_ACCT1_APP_SECRET, KIS_ACCT1_ACCOUNT_NO 없음"
    assert notify.result_problems({"acct1": {"status": "no_account", "reason": reason}}) == []


def test_partly_missing_credentials_are_still_reported():
    reason = "cool 계좌 미설정 — KIS_COOL_APP_SECRET 없음"
    assert notify.result_problems({"status": "no_account", "reason": reason}) != []


def test_empty_slot_message_carries_the_mark(monkeypatch):
    from app.api.services import kis_client as kc
    monkeypatch.setattr(kc, "_db_creds", lambda account: None)
    with pytest.raises(kc.AccountNotConfigured) as e:
        kc._build_client("acct1")
    assert notify.EMPTY_SLOT_MARK in str(e.value)


def test_other_no_account_reasons_are_still_reported():
    p = notify.result_problems({"status": "no_account",
                                "reason": "KIS 가 계좌 1-01 를 거부했다 — rt_cd=1"})
    assert len(p) == 1 and p[0].startswith("status=no_account")


def test_rejected_orders_are_reported():
    assert notify.result_problems({"status": "ok", "rejected": 2}) == ["rejected=2"]


def test_nested_account_failures_are_found():
    p = notify.result_problems({"main": {"status": "ok", "failed": 0},
                                "cafe": {"status": "ok", "failed": 1}})
    assert p == ["cafe/failed=1"]


def test_failed_list_counts_as_failures():
    assert notify.result_problems({"status": "ok", "failed": ["005930"]}) == ["failed=1"]


# ─── 중복 억제 ─────────────────────────────────────────────────────


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


def test_same_failure_is_sent_once_per_window(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_once_redis", FakeRedis())
    monkeypatch.setattr(notify, "send_telegram", lambda text: sent.append(text) or True)
    for _ in range(5):
        notify.notify_task_failure("live_orders", TimeoutError("read timed out"))
    notify.notify_task_failure("live_orders", KeyError("x"))          # 다른 예외는 따로
    notify.notify_task_failure("live_orders_cafeopen", TimeoutError("t"))  # 다른 태스크도 따로
    assert len(sent) == 3


def test_alert_still_goes_out_when_redis_is_down(monkeypatch):
    class Broken:
        def set(self, *a, **kw):
            raise ConnectionError("redis down")

    sent = []
    monkeypatch.setattr(notify, "_once_redis", Broken())
    monkeypatch.setattr(notify, "send_telegram", lambda text: sent.append(text) or True)
    notify.notify_task_failure("live_orders", TimeoutError("t"))
    assert len(sent) == 1


# ─── Celery 신호 연결 ──────────────────────────────────────────────


def test_celery_failure_signal_sends_alert(monkeypatch):
    pytest.importorskip("celery")
    from app.api.workers import celery_app as ca

    got = []
    monkeypatch.setattr(notify, "notify_task_failure", lambda task, exc: got.append((task, exc)))

    class _T:
        name = "live_orders"

    exc = TimeoutError("read timed out")
    ca._alert_task_failure(sender=_T(), exception=exc)
    assert got == [("live_orders", exc)]


def test_celery_success_signal_reports_bad_results_only(monkeypatch):
    pytest.importorskip("celery")
    from app.api.workers import celery_app as ca

    got = []
    monkeypatch.setattr(notify, "notify_task_problems", lambda task, p: got.append((task, p)))

    class _T:
        name = "live_orders_cafereal"

    ca._alert_task_problems(sender=_T(), result={"status": "ok", "buys": []})
    ca._alert_task_problems(sender=_T(), result={"status": "no_account", "reason": "키 거부"})
    assert got == [("live_orders_cafereal", ["status=no_account (키 거부)"])]


def test_alert_hooks_never_raise(monkeypatch):
    pytest.importorskip("celery")
    from app.api.workers import celery_app as ca

    def _boom(*a, **kw):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify, "notify_task_failure", _boom)
    ca._alert_task_failure(sender=None, exception=ValueError("x"))   # 예외가 새면 안 된다


# ─── 09:00 주문은 10:00 까지 기다린다 ────────────────────────────


def test_morning_order_tasks_retry_until_ten(monkeypatch):
    pytest.importorskip("celery")
    from app.api.services import kis_client as kc
    from app.api.workers import tasks

    seen = []

    @tasks.kis_retry_until(10, 0)
    def _task(self):
        seen.append(kc._retry_deadline.get())

    _task(None)
    expected = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0).timestamp()
    assert seen == [expected]
    assert kc._retry_deadline.get() is None          # 블록 밖으로 새지 않는다


@pytest.mark.parametrize("name", ["live_orders", "live_orders_cafeopen",
                                  "live_orders_cafereal", "live_orders_coolreal"])
def test_every_morning_order_task_has_the_deadline(name):
    pytest.importorskip("celery")
    import inspect

    from app.api.workers import tasks

    src = inspect.getsource(tasks)
    block = src[src.index(f'name="{name}")'):]
    head = block[:block.index("def ")]
    assert "@kis_retry_until(10, 0)" in head


def test_morning_open_task_turns_on_the_late_guard():
    pytest.importorskip("celery")
    import inspect

    from app.api.workers import tasks

    src = inspect.getsource(tasks.live_orders_task)
    assert "late_open_guard=True" in src
