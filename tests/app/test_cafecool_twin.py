"""cafecool — cafe 의 진입조건 쌍둥이. 진입 외에는 전부 같아야 한다.

2026-08-20: cafe 의 A패턴은 ret20 하한(+30%)만 있고 상한이 없어 후보 20건이
전부 과열 상태로 진입했다(평균 ret20 +68%, 최고 +368%). 진입 후 D+1 −2.18% /
D+5 −3.31%, 65%가 5일 내 −10% 이상 낙폭. ret20 50%를 경계로 D+5 중앙값이
+3.19%(30~50%) vs −9.50%(50%+)로 갈려 상한을 건 쌍둥이를 만들었다.

쌍둥이의 값어치는 **변수가 하나뿐**이라는 데 있다. 청산·슬롯·시드가 갈리는 순간
두 곡선의 차이가 무엇 때문인지 말할 수 없게 된다. 아래가 그 계약을 고정한다.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from app.api.config import settings
from app.api.db import STRATEGY_CAFE, STRATEGY_CAFECOOL
from app.api.services import live_trader as lt


def test_cafecool_is_a_bracket_strategy():
    # 브래킷 목록에 없으면 청산 엔진이 아예 돌지 않는다.
    assert STRATEGY_CAFECOOL in lt.BRACKET_STRATEGIES


def test_exit_rules_are_identical_to_cafe():
    # 진입만 다른 쌍둥이 — 청산이 갈리면 무엇을 재는지 알 수 없다.
    assert lt.EXIT_RULES[STRATEGY_CAFECOOL] == lt.EXIT_RULES[STRATEGY_CAFE]


def test_seed_matches_cafe():
    # 시드가 다르면 수익률(%) 비교가 무의미해진다.
    assert lt._seed_for(STRATEGY_CAFECOOL) == lt._seed_for(STRATEGY_CAFE)


def test_strategy_code_fits_the_column():
    # Order.strategy 는 String(8). 넘치면 조용히 잘려 다른 전략과 섞인다.
    assert len(STRATEGY_CAFECOOL) <= 8
    assert STRATEGY_CAFECOOL != STRATEGY_CAFE


def test_ret20_ceiling_is_configured():
    # 상한이 없으면 cafe 와 완전히 같은 전략이 하나 더 생길 뿐이다.
    assert settings.live_cafecool_ret20_max > 0
    # A패턴 하한이 +30%이므로 상한은 그보다 커야 후보가 남는다.
    assert settings.live_cafecool_ret20_max > 30.0


def test_cafe_entry_path_is_unchanged():
    # cafe 는 상한 없이(ret20_max=None) 종전 그대로 돌아야 한다.
    import inspect
    from app.api.services.market_screener import submit_cafe_orders
    src = inspect.getsource(submit_cafe_orders)
    assert "ret20_max=None" in src
