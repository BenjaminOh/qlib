"""KIS 가 응답을 안 줄 때 **조회는 살아날 때까지 다시 묻고, 주문은 다시 내지 않는다.**

2026-10-07 실사고. 09:00 직후 모의서버가 30초가량 응답하지 않았고, 주문 태스크
4개(open·cafeopen·cafereal·coolreal)가 **첫 조회**에서 각각 ReadTimeout 으로 죽었다.
처음엔 3회 재시도로 막았지만 서버가 언제 살아날지는 알 수 없다. 그래서 횟수가 아니라
**마감 시각**까지 계속 묻고, 살아나는 순간 진행한다. 오래 죽어 있으면 알린다.

조회와 토큰 발급은 다시 보내도 부작용이 없다. 주문은 다르다 — 타임아웃이 나도
KIS 가 이미 접수했을 수 있으므로, 다시 보내면 같은 주문이 두 번 들어간다.
"""

import pytest

pytest.importorskip("requests")

from app.api.services import kis_client as kc  # noqa: E402

BALANCE_OK = {"rt_cd": "0", "output1": [],
              "output2": [{"prvs_rcdl_excc_amt": "824970", "tot_evlu_amt": "10638205",
                           "scts_evlu_amt": "9813235"}]}


class _Resp:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class _Flaky:
    """처음 `fails` 번은 실패(예외 또는 `bad` 응답)하고 그다음부터 `resp` 를 돌려준다."""

    def __init__(self, fails, resp, exc=None, bad=None):
        self.fails = fails
        self.resp = resp
        self.exc = exc or kc.requests.ReadTimeout("read timed out")
        self.bad = bad
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        if self.calls <= self.fails:
            if self.bad is not None:
                return self.bad
            raise self.exc
        return self.resp


class FakeClock:
    """time.time / time.sleep 대역 — sleep 은 시계를 그만큼 민다."""

    def __init__(self, t=1_000_000.0):
        self.t = t
        self.sleeps = []

    def time(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(kc, "time", c)
    monkeypatch.setattr(kc, "_halt_redis", lambda: None)
    return c


@pytest.fixture
def alerts(monkeypatch):
    from app.api.services import notify
    sent = []
    monkeypatch.setattr(notify, "notify_kis_outage",
                        lambda what, elapsed, err: sent.append(("outage", what, int(elapsed))))
    monkeypatch.setattr(notify, "notify_kis_recovered",
                        lambda what, elapsed: sent.append(("recovered", what, int(elapsed))))
    return sent


@pytest.fixture
def client(monkeypatch, clock):
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="50160169-01")
    monkeypatch.setattr(c, "_ensure_token", lambda: "tok")
    monkeypatch.setattr(c, "_gate", lambda: None)
    monkeypatch.setattr(c, "_hashkey", lambda body: "hash")
    return c


def test_balance_survives_two_timeouts(client, clock, monkeypatch, alerts):
    get = _Flaky(2, _Resp(BALANCE_OK))
    monkeypatch.setattr(kc.requests, "get", get)
    snap = client.get_balance()
    assert snap.cash == 824970.0
    assert get.calls == 3
    assert clock.sleeps == [5, 10]
    assert alerts == []                    # 15초 장애는 알릴 일이 아니다


def test_keeps_asking_until_the_server_comes_back(client, clock, monkeypatch, alerts):
    # 3회가 아니라 살아날 때까지. 10번 실패 후 살아나도 잡는다.
    get = _Flaky(10, _Resp(BALANCE_OK))
    monkeypatch.setattr(kc.requests, "get", get)
    with kc.retry_until(clock.t + 3600):
        snap = client.get_balance()
    assert snap.cash == 824970.0
    assert get.calls == 11
    assert clock.sleeps == [5, 10, 20, 30, 30, 30, 30, 30, 30, 30]   # 30초 고정


def test_long_outage_alerts_once_then_reports_recovery(client, clock, monkeypatch, alerts):
    get = _Flaky(6, _Resp(BALANCE_OK))
    monkeypatch.setattr(kc.requests, "get", get)
    with kc.retry_until(clock.t + 3600):
        client.get_balance()
    kinds = [a[0] for a in alerts]
    assert kinds == ["outage", "recovered"]
    assert alerts[0][2] >= 60


def test_gives_up_at_the_deadline(client, clock, monkeypatch, alerts):
    get = _Flaky(10_000, _Resp(BALANCE_OK))
    monkeypatch.setattr(kc.requests, "get", get)
    start = clock.t
    with kc.retry_until(start + 300), pytest.raises(kc.requests.ReadTimeout):
        client.get_balance()
    assert clock.t <= start + 300
    assert get.calls > 3


def test_default_window_is_ten_minutes(client, clock, monkeypatch, alerts):
    get = _Flaky(10_000, _Resp(BALANCE_OK))
    monkeypatch.setattr(kc.requests, "get", get)
    start = clock.t
    with pytest.raises(kc.requests.ReadTimeout):
        client.get_balance()
    assert start + 570 <= clock.t <= start + 600


def test_gateway_5xx_is_retried_too(client, clock, monkeypatch, alerts):
    get = _Flaky(2, _Resp(BALANCE_OK), bad=_Resp({}, status_code=503))
    monkeypatch.setattr(kc.requests, "get", get)
    assert client.get_balance().cash == 824970.0
    assert get.calls == 3


def test_quote_survives_a_dropped_connection(client, monkeypatch, alerts):
    get = _Flaky(1, _Resp({"output": {"stck_oprc": "4290", "stck_prpr": "4300"}}),
                 exc=kc.requests.ConnectionError("reset"))
    monkeypatch.setattr(kc.requests, "get", get)
    assert client.get_quote("200350")["open"] == 4290.0
    assert get.calls == 2


def test_other_reads_retry_as_well(client, monkeypatch, alerts):
    # 09:00 주문 경로는 잔고·시세 말고도 주문가능금액을 읽는다.
    get = _Flaky(1, _Resp({"rt_cd": "0", "output": {"ord_psbl_cash": "1000"}}))
    monkeypatch.setattr(kc.requests, "get", get)
    client.get_orderable_cash("003670", price=203000)
    assert get.calls == 2


def test_token_issue_survives_a_timeout(monkeypatch, clock, alerts):
    c = kc.KISClient(env="paper", app_key="k", app_secret="s", account_no="50160169-01")
    monkeypatch.setattr(c, "_redis", lambda: None)
    post = _Flaky(1, _Resp({"access_token": "T", "expires_in": 86400}))
    monkeypatch.setattr(kc.requests, "post", post)
    assert c._ensure_token() == "T"
    assert post.calls == 2


def test_display_read_does_not_wait_out_retries(client, monkeypatch, alerts):
    # 화면 조회는 balance_cache 가 마지막 정상값으로 답한다. 브라우저가 백오프를 기다리면 안 된다.
    get = _Flaky(1, _Resp(BALANCE_OK))
    monkeypatch.setattr(kc.requests, "get", get)
    with kc.fail_fast_tokens(), pytest.raises(kc.requests.ReadTimeout):
        client.get_balance()
    assert get.calls == 1


def test_order_is_never_resent_after_a_timeout(client, monkeypatch, alerts):
    # 타임아웃 난 주문은 접수됐을 수도 있다. 다시 보내면 포지션이 두 배가 된다.
    post = _Flaky(99, _Resp({"rt_cd": "0", "output": {"ODNO": "1"}}))
    monkeypatch.setattr(kc.requests, "post", post)
    res = client.place_order("200350", "BUY", 10, price=4290)
    assert res.ok is False
    assert post.calls == 1
