"""계좌를 쓸 수 없을 때 **왜** 그런지 화면까지 내려보낸다.

2026-09-16: cool 계좌에 다른 ID 소유 계좌번호를 넣었다. 화면에는 "이 계좌가 아직
설정되지 않았습니다" 한 줄만 떴고, 진짜 원인(KIS 가 rt_cd=1 로 계좌를 거부)은
서버 로그를 열어야만 보였다. 원인이 세 가지인데 화면 표현이 하나뿐이면
"환경변수를 안 넣었나?" 하고 엉뚱한 곳을 먼저 뒤지게 된다:

  * 환경변수 누락 → "cool 계좌 미설정 — KIS_COOL_APP_KEY … 없음"
  * appkey 중복  → "… appkey 가 기본 계좌와 같다 …"
  * KIS 거부     → "… rt_cd=1 … ID와 사용자정보가 상이 …"

세 경우 모두 `mode="unconfigured"` 로 뭉뚱그려지므로, 구분은 `account_error` 가 한다.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from app.api.routers import live as live_api
from app.api.services import kis_client as kc


def test_unconfigured_account_reports_the_reason(monkeypatch):
    def _missing(account="main"):
        raise kc.AccountNotConfigured(
            "cool 계좌 미설정 — KIS_COOL_APP_KEY, KIS_COOL_APP_SECRET 없음")

    monkeypatch.setattr(live_api, "get_kis_client", _missing)
    monkeypatch.setattr(
        live_api, "get_balance_for_read",
        lambda account="main": (kc.AccountSnapshot(cash=0.0, total_eval=0.0,
                                                   holdings=[]),
                                "no_account", live_api.datetime.utcnow()))

    res = live_api.get_balance(account="cool")

    assert res.mode == "unconfigured"
    assert res.source == "no_account"
    assert res.account_error and "KIS_COOL_APP_KEY" in res.account_error


def test_rejected_account_reason_is_distinguishable(monkeypatch):
    # 거부는 '미설정'과 화면에서 반드시 달라 보여야 한다 — 고칠 곳이 다르다.
    # 미설정은 .env 를, 거부는 계좌번호·앱키 발급 ID 를 손봐야 한다.
    def _rejected(account="main"):
        raise kc.AccountRejected(
            "KIS 가 계좌 50160169-01 를 거부했다 — rt_cd=1 msg_cd=40910000 "
            "모의투자 처리계좌의 ID와 사용자정보가 상이하여 처리 불가능 합니다.")

    monkeypatch.setattr(live_api, "get_kis_client", _rejected)
    monkeypatch.setattr(
        live_api, "get_balance_for_read",
        lambda account="main": (kc.AccountSnapshot(cash=0.0, total_eval=0.0,
                                                   holdings=[]),
                                "no_account", live_api.datetime.utcnow()))

    res = live_api.get_balance(account="cool")

    assert res.mode == "unconfigured"
    assert res.account_error and "rt_cd=1" in res.account_error
    assert "미설정" not in res.account_error


def test_healthy_account_carries_no_error(monkeypatch):
    # 정상 계좌에 오류 문자열이 붙으면 화면이 멀쩡한 계좌를 고장으로 그린다.
    class _Client:
        is_mock = False
        env = "paper"

    monkeypatch.setattr(live_api, "get_kis_client", lambda account="main": _Client())
    monkeypatch.setattr(
        live_api, "get_balance_for_read",
        lambda account="main": (kc.AccountSnapshot(cash=1000.0, total_eval=2000.0,
                                                   holdings=[]),
                                "live", live_api.datetime.utcnow()))

    res = live_api.get_balance(account="main")

    assert res.mode == "paper"
    assert res.account_error is None
