"""시세 조회는 기본 키가 **거부**되면 살아 있는 다른 계좌 키로 넘어간다.

2026-11-12 실사고. 기본 계좌 모의 신청을 해지하자 기본 키가 EGW00103 으로 무효가
됐고(10-29 ~ 11-11), 스크리너·급등 풀·시장 흐름·시뮬 시세가 모두 그 키 하나에
묶여 있어 냉각 계좌 키가 살아 있는데도 카페 후보 0건·급등 풀 0종목이 열흘 넘게
이어졌다. 동시에 09:00 보고서는 '제출 0 · 거부 0' 으로 정상인 날처럼 보였다.
"""

import pytest

pytest.importorskip("requests")

from app.api.services import kis_client as kc  # noqa: E402


class _Client:
    def __init__(self, name, *, rejected=False, error=None, mock=False):
        self.name = name
        self.rejected = rejected
        self.error = error
        self.is_mock = mock
        self.token_calls = 0

    def _ensure_token(self):
        self.token_calls += 1
        if self.rejected:
            raise kc.AccountRejected(f"{self.name} 계좌 KIS 키 거부 — EGW00103")
        if self.error:
            raise self.error
        return "tok"


@pytest.fixture
def world(monkeypatch):
    clients = {
        "main": _Client("main"),
        "cool": _Client("cool"),
        "cafe": _Client("cafe"),
    }
    built = []

    def fake_get(account="main"):
        built.append(account)
        if account not in clients:
            raise kc.AccountNotConfigured(f"{account} 미설정")
        return clients[account]

    alerts = []
    monkeypatch.setattr(kc, "get_kis_client", fake_get)
    monkeypatch.setattr(kc, "all_accounts", lambda: tuple(kc.ALL_ACCOUNTS))
    monkeypatch.setattr(kc, "_alert_market_fallback", lambda label, reason: alerts.append(label))
    monkeypatch.setattr(kc, "_market_pick", None)
    return clients, built, alerts


def test_healthy_main_key_is_used(world):
    clients, _, alerts = world
    assert kc.get_market_client() is clients["main"]
    assert alerts == []


def test_rejected_main_key_falls_back_to_cool_first(world):
    clients, _, alerts = world
    clients["main"].rejected = True
    assert kc.get_market_client() is clients["cool"], "냉각이 카페보다 먼저다"
    assert alerts == ["냉각"]


def test_skips_rejected_fallback_candidates(world):
    clients, _, alerts = world
    clients["main"].rejected = True
    clients["cool"].rejected = True
    assert kc.get_market_client() is clients["cafe"]
    assert alerts == ["카페"]


def test_all_rejected_returns_main_so_callers_see_the_old_error(world):
    clients, _, alerts = world
    for c in clients.values():
        c.rejected = True
    assert kc.get_market_client() is clients["main"]
    assert alerts == []


def test_transient_failure_is_not_a_reason_to_fall_back(world):
    """타임아웃은 KIS 쪽 장애라 다른 키로 가도 같다 — 남의 주문 한도만 깎는다."""
    clients, built, alerts = world
    clients["main"].error = TimeoutError("read timeout")
    assert kc.get_market_client() is clients["main"]
    assert built == ["main"]
    assert alerts == []


def test_fallback_pick_is_remembered_then_main_is_retried(world, monkeypatch):
    clients, _, _ = world
    clients["main"].rejected = True
    now = [1000.0]
    monkeypatch.setattr(kc.time, "time", lambda: now[0])
    kc.get_market_client()
    assert clients["main"].token_calls == 1

    now[0] += 60                                  # 10분 안 — 기본 키를 다시 두드리지 않는다
    assert kc.get_market_client() is clients["cool"]
    assert clients["main"].token_calls == 1

    clients["main"].rejected = False              # 키를 고쳤다
    now[0] += kc._MARKET_FALLBACK_TTL_S
    assert kc.get_market_client() is clients["main"]


def test_mock_main_is_returned_untouched(world):
    """자격증명 없는 환경(테스트·로컬)은 예전 동작 그대로."""
    clients, built, _ = world
    clients["main"].is_mock = True
    assert kc.get_market_client() is clients["main"]
    assert clients["main"].token_calls == 0
    assert built == ["main"]


def test_order_paths_never_use_the_market_client():
    """주문·잔고 경로가 시세용 클라이언트를 쓰면 남의 계좌 키로 주문이 나간다."""
    import inspect

    from app.api.services import live_trader as lt

    for fn in (lt.submit_daily_orders, lt.sync_account, lt.reconcile_fills,
               lt.cancel_unfilled_orders):
        assert "get_market_client" not in inspect.getsource(fn), fn.__name__


def test_morning_report_says_no_orders_when_account_rejected(monkeypatch):
    from app.api.services import notify

    sent = []
    monkeypatch.setattr(notify, "telegram_enabled", lambda: True)
    monkeypatch.setattr(notify, "send_telegram", lambda text: sent.append(text) or True)
    notify.notify_open_orders({
        "status": "no_account", "strategy": "open", "as_of": "2026-11-11",
        "reason": '기본 계좌 KIS 키 거부 — {"error_code":"EGW00103"}'})
    assert len(sent) == 1
    assert "오늘 주문 못 함" in sent[0]
    assert "EGW00103" in sent[0]
    assert "제출 0" not in sent[0]
    assert "09:20" not in sent[0]
