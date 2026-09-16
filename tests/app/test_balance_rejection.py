"""KIS 가 계좌를 거부하면 **잔고 0원으로 읽지 않는다.**

2026-09-16 실사고. cool 계좌에 다른 ID 소유 계좌번호를 넣자 KIS 가
HTTP 200 + `rt_cd=1` + `output2` 없음으로 답했다. 그때 `get_balance` 는
`(output2 or [{}])[0]` 로 빈 딕셔너리를 받아 **예수금 0·평가금 0**을 돌려줬고,
`sync_account` 가 그걸 진짜 잔고로 믿어 스냅샷과 손익행을 썼다.
화면에는 시드 1천만 → 0원, **하루 만에 −100%** 인 가짜 곡선이 찍혔다.

0원은 "돈이 없다"는 사실처럼 보이기 때문에 위험하다 — 곡선·매수 예산·청산 판정이
전부 그 숫자 위에 얹힌다. 거부는 사실이 아니라 **조회 실패**이고, 그때 우리가 할 일은
아무것도 기록하지 않고 오늘을 건너뛰는 것이다.
"""

from datetime import date

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("requests")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.db import Base, PositionSnapshot, DailyPnL
from app.api.services import kis_client as kc
from app.api.services import live_trader as lt

DAY = date(2026, 9, 16)

# 실사고에서 KIS 가 실제로 돌려준 본문
REJECT_BODY = {"rt_cd": "1", "msg_cd": "40910000",
               "msg1": "모의투자 처리계좌의 ID와 사용자정보가 상이하여 처리 불가능 합니다."}


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _NoCloseSession:
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client(monkeypatch):
    c = kc.KISClient(env="paper", app_key="k", app_secret="s",
                     account_no="50160169-01")
    monkeypatch.setattr(c, "_ensure_token", lambda: "tok")
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_hashkey", lambda body: "hash")
    return c


def test_rejected_account_raises_instead_of_returning_zero(client, monkeypatch):
    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: _Resp(REJECT_BODY))
    with pytest.raises(kc.AccountRejected) as e:
        client.get_balance()
    assert "rt_cd=1" in str(e.value)


def test_missing_summary_also_raises(client, monkeypatch):
    # rt_cd 는 0 인데 요약 행이 없는 경우. 보유 0종목이어도 요약은 오므로
    # 이건 "빈 계좌"가 아니라 조회가 성립하지 않은 것이다.
    monkeypatch.setattr(kc.requests, "get",
                        lambda *a, **kw: _Resp({"rt_cd": "0", "output1": [], "output2": []}))
    with pytest.raises(kc.AccountRejected):
        client.get_balance()


def test_rejection_is_treated_like_missing_credentials():
    # 기존 호출부는 전부 AccountNotConfigured 를 "오늘은 건너뜀"으로 처리한다.
    # 하위 클래스여야 코드 변경 없이 같은 처리를 받는다.
    assert issubclass(kc.AccountRejected, kc.AccountNotConfigured)


def test_normal_response_still_parses(client, monkeypatch):
    # 방어가 정상 응답까지 막으면 안 된다.
    body = {"rt_cd": "0", "output1": [],
            "output2": [{"prvs_rcdl_excc_amt": "824970", "tot_evlu_amt": "10638205",
                         "scts_evlu_amt": "9813235"}]}
    monkeypatch.setattr(kc.requests, "get", lambda *a, **kw: _Resp(body))
    snap = client.get_balance()
    assert snap.cash == 824970.0
    assert snap.total_eval == 10638205.0


def test_read_path_does_not_fall_back_to_db_when_rejected(monkeypatch):
    """거부당한 계좌는 **과거 스냅샷으로 대체하지 않는다.**

    2026-09-16 2차 사고. 자격증명을 다 채우자 클라이언트 생성은 성공하고 거부는
    잔고 조회에서 났다. 그런데 화면용 읽기 경로는 그 예외를 "KIS 일시 장애"로 보고
    stale → db 로 물러났고, 결과적으로 **과거 스냅샷(0원)이 현재 잔고처럼** 실려
    화면에는 `mode=paper` 로 '연결됨'처럼 보였다. 거부는 장애가 아니라 "이 계좌는
    못 쓴다"이므로, 폴백이 아니라 no_account 로 끝나야 한다.
    """
    from app.api.services import balance_cache as bc

    class _Rejecting:
        is_mock = False
        env = "paper"
        # redis 키 이름이 계좌번호 해시를 쓴다(balance_cache._account_scope).
        # 가짜에도 있어야 읽기 경로가 키를 만들 수 있다.
        cano = "50160169"
        acnt_prdt_cd = "01"

        def get_balance(self):
            raise kc.AccountRejected("KIS 가 계좌를 거부했다 — rt_cd=1")

    monkeypatch.setattr(bc, "get_kis_client", lambda account="main": _Rejecting())
    monkeypatch.setattr(bc, "_redis", lambda: None)          # 캐시·stale 경로 제거
    monkeypatch.setattr(bc, "_from_db", lambda account: None or _FAIL_IF_CALLED())

    snap, source, _ = bc.get_balance_for_read("cool")

    assert source == "no_account", f"거부인데 {source} 로 물러났다"
    assert snap.cash == 0.0 and snap.total_eval == 0.0


def _FAIL_IF_CALLED():
    raise AssertionError("거부당한 계좌인데 DB 폴백을 읽었다")


def test_sync_account_writes_no_row_when_rejected(monkeypatch):
    """이것이 실사고의 회귀 테스트다 — 거부당한 날 **행이 생기면 안 된다.**"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    class _Rejecting:
        def get_balance(self):
            raise kc.AccountRejected("KIS 가 계좌를 거부했다 — rt_cd=1")

    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "SessionLocal", lambda: _NoCloseSession(session))
    monkeypatch.setattr(kc, "get_kis_client", lambda account="main": _Rejecting())

    res = lt.sync_account(strategy="coolreal", trade_date=DAY)

    assert res["status"] == "no_account", res
    assert session.query(PositionSnapshot).count() == 0, "거부당했는데 스냅샷이 생겼다"
    assert session.query(DailyPnL).count() == 0, "거부당했는데 손익행이 생겼다"

    session.close()
    engine.dispose()
