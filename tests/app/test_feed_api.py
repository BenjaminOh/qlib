"""허브 → runner 신호 feed (`/api/v1/feed`) — 2026-10-26 분리 P0.

여기서 막는 사고:
  * 토큰 없이 feed 가 열리는 것. 빈 목록·짧은 placeholder 는 "꺼짐"(503)이다.
  * feed 응답에 허브 시뮬의 orders 가 섞이는 것 — runner 가 허브 장부에 다시 묶인다.
  * 카탈로그 프리셋이 허브가 실제로 돌리는 값(EXIT_RULES·LIVE_CONFIG·settings)과
    갈라지는 것. 그러면 P3 병행 검증이 서로 다른 전략을 비교하게 된다.
  * nginx 가 feed 를 공개 도메인으로 내보내는 것.

라우터 함수를 직접 부른다(tests/app 관례 — TestClient 를 쓰지 않는다).
"""

from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import (  # noqa: E402
    ALL_STRATEGIES, Base, CafeCandidate, Order, Signal, SurgePick,
)
from app.api.db import models  # noqa: E402
from app.api.routers import feed  # noqa: E402
from app.api.services import feed_catalog  # noqa: E402

TOKEN = "t" * 40
D1 = date(2026, 10, 23)
D2 = date(2026, 10, 26)


class _NoCloseSession:
    def __init__(self, session):
        self._s = session

    def __enter__(self):
        return self._s

    def __exit__(self, *exc):
        return False


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(feed, "SessionLocal", lambda: _NoCloseSession(s))
    yield s
    s.close()
    engine.dispose()


def _signal(s, as_of, rank, code):
    s.add(Signal(as_of=as_of, rank=rank, code=code, name=code, score=1.0 / rank,
                 model_class="LGBModel", strategy_class="TopkDropoutStrategy",
                 reasons_json='{"summary": "x"}', created_at=datetime(2026, 10, 23, 7, 20)))


# ─── 토큰 ────────────────────────────────────────────────────────────

def _auth(monkeypatch, tokens, header):
    monkeypatch.setattr(feed.settings, "feed_tokens", tokens)
    feed.require_feed_token(header)


@pytest.mark.parametrize("tokens", [[], ["short-placeholder"]])
def test_feed_is_off_without_a_real_token(monkeypatch, tokens):
    with pytest.raises(HTTPException) as e:
        _auth(monkeypatch, tokens, f"Bearer {TOKEN}")
    assert e.value.status_code == 503


@pytest.mark.parametrize("header", [None, TOKEN, "Bearer " + "x" * 40])
def test_wrong_or_missing_token_is_401(monkeypatch, header):
    with pytest.raises(HTTPException) as e:
        _auth(monkeypatch, [TOKEN], header)
    assert e.value.status_code == 401


def test_any_configured_token_passes(monkeypatch):
    _auth(monkeypatch, ["o" * 40, TOKEN], f"Bearer {TOKEN}")


def test_every_feed_route_requires_the_token():
    deps = [d.dependency for d in feed.router.dependencies]
    assert feed.require_feed_token in deps


# ─── 신호·후보 ───────────────────────────────────────────────────────

def test_signals_default_to_latest_session_with_all_ranks(session):
    for r in range(1, 31):
        _signal(session, D1, r, f"{r:06d}")
    _signal(session, D2, 1, "005930")
    session.commit()

    latest = feed.get_signals(as_of=None)
    assert latest["as_of"] == D2.isoformat()
    assert [p["code"] for p in latest["picks"]] == ["005930"]

    old = feed.get_signals(as_of=D1)
    assert len(old["picks"]) == 30  # flow 재정렬에 넓은 목록이 필요하다
    assert old["picks"][0]["reasons"] == {"summary": "x"}
    assert old["generated_at"].endswith("Z")


def test_empty_tables_answer_none_not_error(session):
    assert feed.get_signals(as_of=None) == {"as_of": None, "generated_at": None, "picks": []}
    assert feed.get_cafe_candidates(session=None)["session"] is None
    assert feed.get_surge_picks(session=None)["session"] is None


def test_cafe_candidates_carry_stop_and_metrics_but_not_bought(session):
    session.add(CafeCandidate(trade_date=D1, code="111111", name="a", pattern="B", rank=1,
                              close=1000.0, stop_px=900.0, metrics_json='{"ret20": 42.0}'))
    session.add(Order(trade_date=D1, strategy="cafe", code="111111", side="BUY", qty=1,
                      status="SIMULATED"))
    session.commit()

    out = feed.get_cafe_candidates(session=D1)
    c = out["candidates"][0]
    assert c["stop_px"] == 900.0
    assert c["metrics"] == {"ret20": 42.0}
    assert "bought" not in c  # 허브 시뮬이 샀는지는 신호가 아니다


def test_surge_picks_by_session(session):
    session.add(SurgePick(trade_date=D1, rank=1, code="222222", score=3.0))
    session.add(SurgePick(trade_date=D2, rank=1, code="333333", score=2.0))
    session.commit()
    assert feed.get_surge_picks(session=D1)["picks"][0]["code"] == "222222"
    assert feed.get_surge_picks(session=None)["session"] == D2.isoformat()


def test_flow_without_signals_says_not_applied(session):
    out = feed.get_flow(as_of=D1)
    assert out["applied"] is False and out["reason"] == "no_signal"


def test_flow_failure_degrades_to_model_order(session, monkeypatch):
    for r, code in enumerate(["A", "B", "C"], start=1):
        _signal(session, D1, r, code)
    session.commit()
    from app.api.services import live_trader, market_flow

    def boom(*a, **k):
        raise RuntimeError("kis down")
    monkeypatch.setattr(live_trader, "_prev_trading_day", lambda d: date(2026, 10, 22))
    monkeypatch.setattr(market_flow, "ensure_flow_data", boom)
    out = feed.get_flow(as_of=D1)
    assert out["applied"] is False
    assert out["ordered"] == ["A", "B", "C"]


# ─── bars 입력 검증 (qlib 없이 400 으로 끊긴다) ────────────────────────

@pytest.mark.parametrize("codes,start,end", [
    ("", D1, D2),
    (",".join(str(i) for i in range(feed.MAX_BAR_CODES + 1)), D1, D2),
    ("005930", D2, D1),
    ("005930", date(2025, 1, 1), D2),
])
def test_bars_rejects_bad_ranges(codes, start, end):
    with pytest.raises(HTTPException) as e:
        feed.get_bars(codes=codes, start=start, end=end)
    assert e.value.status_code == 400


# ─── 카탈로그 ────────────────────────────────────────────────────────

ACCOUNT_SLOTS = {models.STRATEGY_ACCT1, models.STRATEGY_ACCT2,
                 models.STRATEGY_ACCT3, models.STRATEGY_ACCT4}


def test_presets_cover_every_hub_strategy():
    """허브의 전략이 늘었는데 runner 카탈로그에 없으면 여기서 걸린다."""
    assert set(feed_catalog.presets()) == set(ALL_STRATEGIES) - ACCOUNT_SLOTS


def test_preset_params_are_declared_by_their_type():
    types = feed_catalog.strategy_types()
    for sid, p in feed_catalog.presets().items():
        declared = types[p["type"]]["params"]
        unknown = set(p["params"]) - set(declared)
        assert not unknown, f"{sid}: 타입에 없는 파라미터 {unknown}"
        for k, v in p["params"].items():
            enum = declared[k].get("enum")
            if enum:
                assert v in enum, f"{sid}.{k}={v!r} not in {enum}"


def test_bracket_presets_match_exit_rules():
    from app.api.services.live_trader import EXIT_RULES
    for sid, p in feed_catalog.presets().items():
        if p["type"] != feed_catalog.TYPE_BRACKET:
            continue
        rule = EXIT_RULES[sid]
        for k in ("tp", "trail", "ladder", "floor_gap"):
            assert p["params"][k] == rule.get(k), f"{sid}.{k}"


def test_type_defaults_read_live_values():
    from app.api.config import settings
    from app.api.services.live_trader import LIVE_CONFIG
    t = feed_catalog.strategy_types()
    assert t["rank"]["params"]["topk"]["default"] == LIVE_CONFIG["strategy_kwargs"]["topk"]
    assert t["rank"]["params"]["n_drop"]["default"] == LIVE_CONFIG["strategy_kwargs"]["n_drop"]
    assert t["cafe"]["params"]["slots"]["default"] == settings.live_cafe_slots
    assert t["cafe"]["params"]["stop_cap"]["default"] == settings.live_cafe_stop_cap
    assert feed_catalog.common_params()["max_order_value"]["default"] == settings.live_max_order_value


def test_twin_presets_differ_only_where_intended():
    p = feed_catalog.presets()
    assert p["coolreal"]["params"]["ret20_max"] == p["cafecool"]["params"]["ret20_max"]
    assert p["cafereal"]["params"].get("ret20_max") is None
    assert p["flow"]["params"]["flow_overlay"] is True
    assert p["cafeopen"].get("parity") is False  # 재정의 — 병행 검증 대상 아님


def test_catalog_ships_flow_config():
    from app.api.services.market_flow import FLOW_CONFIG
    assert feed_catalog.catalog()["flow_config"] == FLOW_CONFIG


# ─── 공개 경로 차단 ───────────────────────────────────────────────────

def test_nginx_hides_feed_from_the_public_host():
    conf = (Path(__file__).resolve().parents[2] / "infra/nginx/qlib.tmanager.kr.conf").read_text()
    deny = conf.index("location /api/v1/feed/")
    assert deny < conf.index("location /api/ {")
    assert "return 404" in conf[deny:conf.index("}", deny)]


def test_nginx_allows_only_the_office_ip_on_https():
    """2026-10-26 사용자 결정: qlib 은 사무실 IP 에서만. 80(ACME)은 열어 둔다."""
    conf = (Path(__file__).resolve().parents[2] / "infra/nginx/qlib.tmanager.kr.conf").read_text()
    http_block = conf[:conf.index("listen 443")]
    https_block = conf[conf.index("listen 443"):]
    allow = https_block.index("allow 61.255.176.73;")
    deny = https_block.index("deny all;")
    assert allow < deny < https_block.index("location ")
    assert "deny all" not in http_block
