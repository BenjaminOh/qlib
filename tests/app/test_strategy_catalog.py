"""전략 카탈로그 — 목록이 갈라지는 것을 막는다.

2026-08-21: 전략 목록이 네 곳에 손으로 복사돼 있었고, 전날 추가된 cafereal 과
cafecool 이 그중 셋에서 빠졌다. 오타가 아니라 구조였다 — 어느 목록도 다른 셋을
강제하지 않았다.

여기서 막는 사고:
  * 곡선 기준선(seed_cash)에서 빠지면 EquityChart 가 그 전략의 데이터를 통째로
    버린다(`if (r && seed)`). DailyPnL 에 행이 쌓여도 선이 그려지지 않는다.
    cafe(시뮬) 대비 cafereal(실계좌) 격차를 재려고 만든 전략인데 그 격차를
    볼 수 없었다.
  * 시드가 0이면 같은 이유로 곡선이 사라진다 — 누락과 증상이 구분되지 않는다.
  * 새 전략을 만들고 ALL_STRATEGIES 에 넣는 것을 잊는 것.
"""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.api.db import ACCOUNT_STRATEGIES, ALL_STRATEGIES
from app.api.db import models


def _declared_strategies() -> set[str]:
    """models 에 선언된 STRATEGY_* 상수 전부."""
    return {v for k, v in vars(models).items()
            if k.startswith("STRATEGY_") and isinstance(v, str)}


def test_catalog_covers_every_declared_strategy():
    """전략을 새로 만들고 카탈로그에 넣지 않으면 여기서 걸린다."""
    missing = _declared_strategies() - set(ALL_STRATEGIES)
    assert not missing, f"ALL_STRATEGIES 에서 빠진 전략: {sorted(missing)}"


def test_catalog_has_no_phantom_entries():
    unknown = set(ALL_STRATEGIES) - _declared_strategies()
    assert not unknown, f"선언되지 않은 전략이 카탈로그에: {sorted(unknown)}"


def test_catalog_has_no_duplicates():
    assert len(ALL_STRATEGIES) == len(set(ALL_STRATEGIES))


def test_real_account_strategies_are_in_the_catalog():
    for account, strategies in ACCOUNT_STRATEGIES.items():
        for s in strategies:
            assert s in ALL_STRATEGIES, f"{account} 의 {s} 가 카탈로그에 없다"


def test_every_strategy_has_a_positive_seed():
    """시드가 0이거나 없으면 EquityChart 가 그 곡선을 통째로 버린다."""
    from app.api.services.live_trader import _seed_for

    for s in ALL_STRATEGIES:
        assert _seed_for(s) > 0, f"{s} 의 시드가 0 — 곡선이 그려지지 않는다"


def test_pnl_response_carries_a_seed_for_every_strategy():
    """/live/pnl/daily 의 seed_cash 가 카탈로그를 통째로 실어야 한다.

    손으로 적었을 때 cafereal·cafecool 이 빠져 있었다.
    """
    from app.api.routers import live as api

    assert set(api._seed_cash_map()) == set(ALL_STRATEGIES)
    assert all(v > 0 for v in api._seed_cash_map().values())


def test_strategy_codes_fit_the_column():
    """Order.strategy 는 String(8) — 더 긴 이름은 저장 자체가 안 된다."""
    for s in ALL_STRATEGIES:
        assert len(s) <= 8, f"{s} 는 {len(s)}자"
