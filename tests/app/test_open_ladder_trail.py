"""open 전략의 청산 규칙 — 사다리 절반 + 잔여 트레일링.

2026-08-27: 금호에이치티(214330)가 08-25 상한가, 08-26 장중 +54% 까지 갔는데
`open` 은 **랭크 이탈로만 팔기 때문에** 그 상승을 하나도 확정하지 못했다. 그리고
`open` 에는 익절·손절·트레일링이 **하나도 없었다** — 커밋 b31728ce 가 "노출을
100%로 키우면서 방어가 0" 이라고 1순위 과제로 지목해 둔 상태였다.

규칙: +10% 에 절반(예약 지정가) → 잔여는 최고 종가 대비 −7% 트레일링(장중 폴링).
구조적 손절(전저점 −1%, 캡 −10%)이 공통 분기에서 자동으로 딸려온다.

여기서 막는 사고:
  * **main 계좌 포지션을 카페 계좌로 팔려 드는 것** — 실계좌 브래킷이 cafereal
    하나뿐이던 시절의 하드코딩이 남아 있었다.
  * 예약 지정가가 sellable_qty 를 잡은 채로 전량 매도를 내 거부되는 것
  * 같은 종목에 사다리 예약이 매일 중복으로 쌓이는 것
  * 취소 스윕이 사다리 예약을 쓸어가는 것
  * 시뮬 5종의 규칙이 함께 바뀌어 A/B 가 무의미해지는 것
"""

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.api.db import (  # noqa: E402
    ACCOUNT_STRATEGIES, CAFE_ACCOUNT_ID, DEFAULT_ACCOUNT_ID,
    STRATEGY_CAFEREAL, STRATEGY_OPEN, STRATEGY_SCALE,
)
from app.api.services import live_trader as lt  # noqa: E402


# ─── 커밋 1 — 실계좌 브래킷의 계좌 축 ───────────────────────────────


def test_bracket_account_follows_the_strategy():
    """open 은 main, cafereal 은 cafe. 예전엔 둘 다 cafe 로 갔다."""
    assert lt._account_for(STRATEGY_OPEN) == DEFAULT_ACCOUNT_ID
    assert lt._account_for(STRATEGY_CAFEREAL) == CAFE_ACCOUNT_ID


def test_simulated_strategy_falls_back_to_default_account():
    """시뮬 전략은 어느 계좌에도 속하지 않는다 — 실주문을 내지 않으므로
    계좌 해석이 호출될 일이 없지만, 물어보면 기본 계좌로 답한다."""
    assert lt._account_for(STRATEGY_SCALE) == DEFAULT_ACCOUNT_ID


def test_account_map_covers_every_real_bracket_strategy():
    """실주문 브래킷 전략은 반드시 어느 계좌에 속해야 한다.

    안 그러면 _account_for 가 기본 계좌로 물러나 **다른 계좌의 포지션을
    엉뚱한 계좌로 팔려 든다.**
    """
    owned = {s for strategies in ACCOUNT_STRATEGIES.values() for s in strategies}
    for s in lt.REAL_BRACKET_STRATEGIES:
        assert s in owned, f"{s} 가 ACCOUNT_STRATEGIES 에 없다"


def test_no_hardcoded_cafe_account_in_bracket_paths():
    """계좌 하드코딩 회귀 가드.

    _sell_bracket 과 evaluate_bracket_exits 가 CAFE_ACCOUNT_ID / ACCOUNT_CAFE 를
    직접 쓰면 안 된다 — _account_for 를 거쳐야 한다.
    """
    import inspect
    for fn in (lt._sell_bracket, lt.evaluate_bracket_exits):
        src = inspect.getsource(fn)
        assert "CAFE_ACCOUNT_ID" not in src, f"{fn.__name__} 에 계좌 하드코딩"
        assert "ACCOUNT_CAFE" not in src, f"{fn.__name__} 에 계좌 하드코딩"


# ─── 시뮬 5종은 건드리지 않는다 (A/B 보존) ──────────────────────────


def test_sim_exit_rules_unchanged():
    """scale 은 여전히 10/15/20 전량이어야 한다.

    open 이 [0.10]+트레일로 가는 것과 규칙이 달라야 두 곡선의 A/B 가 유효하다.
    같아지면 무엇을 비교하는지 알 수 없게 된다.
    """
    assert lt.EXIT_RULES[STRATEGY_SCALE]["ladder"] == [0.10, 0.15, 0.20]
    assert lt.EXIT_RULES[STRATEGY_SCALE].get("floor_gap") == 0.05
    assert "trail_rest" not in lt.EXIT_RULES[STRATEGY_SCALE]
    assert lt.EXIT_RULES["trail"] == {"trail": 0.07}
    assert lt.EXIT_RULES["close"] == {"tp": 0.10}


def test_open_stays_out_of_the_1625_bracket_sweep():
    """16:25 스윕(`close_bracket_exits`)은 BRACKET_STRATEGIES 를 돈다.

    open 을 거기 넣으면 **장 마감 후에 실주문**이 나가 다음 장까지 걸려 있게 된다.
    open 의 사다리는 09:25 예약, 트레일은 장중 폴링이 담당한다.
    """
    assert STRATEGY_OPEN not in lt.BRACKET_STRATEGIES
    assert STRATEGY_CAFEREAL in lt.BRACKET_STRATEGIES


def test_sync_account_reads_the_strategys_own_account():
    """스냅샷도 계좌 축을 따라야 한다.

    open 을 REAL_BRACKET_STRATEGIES 에 넣는 순간, sync_account 의 하드코딩된
    `get_kis_client(ACCOUNT_CAFE)` 가 **main 잔고 자리에 카페 잔고를 적는다.**
    """
    import inspect
    src = inspect.getsource(lt.sync_account)
    assert "ACCOUNT_CAFE" not in src
    assert "_account_for(strategy)" in src
