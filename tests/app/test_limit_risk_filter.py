"""위험종목 필터가 `limit` 진입에도 걸리는지.

`_is_risky`(거래정지·관리종목·투자위험/경고)는 `_select_affordable_buys`를 통해
open/close/flow/trail/scale 계열에만 적용돼 있었고, **실거래 대상인 `limit`
진입 경로에는 호출 자체가 없었다.**

−3% 눌림 진입은 구조적으로 하락 종목을 끌어당긴다 — 2026-07-28(지수 −11.19%)
같은 날은 후보의 90.3%가 −3% 지정가를 터치했다. 그 집합에는 정상 눌림과
악재로 무너지는 종목이 섞여 있고, 진입 규칙만으로는 구분되지 않는다.
모델이 보는 건 가격뿐이라 거래정지된 종목이 정지 직전의 얼어붙은 데이터로
상위 랭크에 오르는 일도 실제로 있었다(콘텐트리중앙, 2026-07-30).
"""

from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Signal  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402


@pytest.fixture
def wired(monkeypatch):
    """In-memory DB + stubbed bars so only the risk filter varies."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    day = date(2026, 8, 14)
    with Session() as s:
        for rank, code in enumerate(["000001", "000002", "000003"], start=1):
            s.add(Signal(as_of=day, rank=rank, code=code, score=1.0 / rank,
                         model_class="LGBModel", strategy_class="TopkDropoutStrategy"))
        s.commit()

    monkeypatch.setattr(lt, "SessionLocal", Session)
    monkeypatch.setattr(lt, "init_db", lambda: None)
    monkeypatch.setattr(lt, "_reset_qlib_caches", lambda: None)
    monkeypatch.setattr(lt, "_last_trading_day", lambda today=None: day)
    monkeypatch.setattr(lt, "_stock_name", lambda code: f"name-{code}")
    monkeypatch.setattr(lt, "_last_close", lambda code: 10_000.0)
    monkeypatch.setattr(lt, "_buy_reasons", lambda *a, **kw: None)
    # Every candidate: prev close 10,000 → limit 9,700, and the day's low
    # touches it, so absent the risk filter all three would fill.
    monkeypatch.setattr(lt, "_prev_close_before", lambda code, d: 10_000.0)
    monkeypatch.setattr(lt, "_day_ohlc",
                        lambda code, d: {"open": 9_900.0, "high": 10_100.0,
                                         "low": 9_000.0, "close": 9_800.0})
    return day


def test_risky_pick_is_skipped_and_next_rank_takes_the_slot(wired, monkeypatch):
    monkeypatch.setattr(lt.settings, "live_limit_candidates", 2)
    monkeypatch.setattr(lt.settings, "live_limit_max_fills", 2)
    monkeypatch.setattr(lt, "_is_risky",
                        lambda code, client=None: "관리종목" if code == "000001" else None)

    res = lt.evaluate_limit_entries()

    codes = [f["code"] for f in res["fills"]]
    assert "000001" not in codes, "관리종목이 매수되면 안 된다"
    # rank 1 dropped out, so ranks 2 and 3 fill the two candidate slots.
    assert codes == ["000002", "000003"]
    assert res["skipped_risky"] == ["000001(관리종목)"]


def test_all_risky_means_no_entries(wired, monkeypatch):
    monkeypatch.setattr(lt, "_is_risky", lambda code, client=None: "거래정지")

    res = lt.evaluate_limit_entries()

    assert res["fills"] == []
    assert res["rested"] == 0
    assert len(res["skipped_risky"]) == 3


def test_clean_picks_are_unaffected(wired, monkeypatch):
    """The filter must not change behaviour when nothing is flagged —
    otherwise the existing simulated curve breaks for the wrong reason."""
    monkeypatch.setattr(lt.settings, "live_limit_candidates", 2)
    monkeypatch.setattr(lt, "_is_risky", lambda code, client=None: None)

    res = lt.evaluate_limit_entries()

    assert [f["code"] for f in res["fills"]] == ["000001", "000002"]
    assert res["skipped_risky"] == []


def test_risk_check_runs_after_the_free_filters(wired, monkeypatch):
    """`_is_risky` costs a rate-gated KIS quote (1.2s each).

    It must not be spent on a candidate that an affordability check would have
    dropped for free — the same ordering `_select_affordable_buys` uses.
    """
    monkeypatch.setattr(lt.settings, "live_limit_candidates", 3)
    # Slot budget below the limit price → every candidate is unaffordable.
    monkeypatch.setattr(lt, "_simulated_balance",
                        lambda db, strategy=None, seed_cash=None: lt.AccountSnapshot(
                            cash=1_000.0, total_eval=1_000.0, holdings=[]))
    called = []
    monkeypatch.setattr(lt, "_is_risky",
                        lambda code, client=None: called.append(code))

    lt.evaluate_limit_entries()

    assert called == [], "감당 불가 종목에 시세 조회를 쓰면 안 된다"


def test_mock_client_reports_no_risk(monkeypatch):
    """Documents why a paper run never exercises this filter.

    `_is_risky` returns None whenever the client is mock, so the simulated
    `limit` curve was produced with the filter permanently open. Real-account
    entries will therefore differ from the paper ones — that is expected, and
    the A/B comparison has to account for it.
    """
    class _MockClient:
        is_mock = True

        def get_quote(self, code):  # pragma: no cover — must never be reached
            raise AssertionError("mock client must not be quoted")

    assert lt._is_risky("000001", client=_MockClient()) is None
