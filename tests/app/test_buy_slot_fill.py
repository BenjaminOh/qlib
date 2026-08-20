"""매수 상한 — 빈 슬롯을 채우되 하루 상한을 넘지 않는다.

2026-08-20: 실계좌가 topk=10 설계인데 4종목에서 고착돼 있었다. 원인은 매수가
n_drop(2)으로 묶여 매도 2 / 매수 2가 매일 반복되면서 순증이 0이었던 것.
투입률이 20~41%에 머물고 50%를 넘은 적이 없었다.

여기서 고정하는 계약:
  * 빈 슬롯이 있으면 그만큼 산다 (단 live_max_buys_per_day 상한)
  * 이미 topk를 채웠으면 교체분(n_drop)만 산다 — 종전 동작
  * 시뮬 곡선은 종전 그대로다. 청산 규칙 A/B의 기준선이라 자본 투입 속도가
    바뀌면 변수가 둘이 되어 비교가 무의미해진다.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from app.api.config import settings
from app.api.services.live_trader import _buy_count


def _n(held, selling, *, topk=10, n_drop=2, simulated=False):
    return _buy_count(held=held, selling=selling, topk=topk,
                      n_drop=n_drop, simulated=simulated)


def test_empty_slots_are_filled_up_to_daily_cap():
    # 4 보유 / 2 매도 → 빈 슬롯 8. 하루 상한(기본 4)이 물린다.
    assert _n(4, 2) == settings.live_max_buys_per_day


def test_full_book_buys_only_the_replacement():
    # topk를 채운 상태면 판 만큼만 산다 — 종전 동작 그대로.
    assert _n(10, 2) == 2


def test_nearly_full_book_never_drops_below_n_drop():
    # 빈 슬롯이 n_drop보다 적어도 교체분은 보장한다. 9 보유 / 2 매도 →
    # 빈 슬롯 3이지만 max(n_drop, 3)=3.
    assert _n(9, 2) == 3
    # 10 보유 / 0 매도 → 빈 슬롯 0이어도 n_drop 은 유지.
    assert _n(10, 0) == 2


def test_daily_cap_bounds_the_ramp():
    # 0에서 출발해도 하루에 상한 이상 사지 않는다.
    assert _n(0, 0) == settings.live_max_buys_per_day
    assert _n(0, 0) <= settings.live_max_buys_per_day


def test_simulated_curves_keep_the_old_cap():
    # 시뮬은 빈 슬롯이 아무리 많아도 n_drop 그대로 — 기준선 보존.
    assert _n(0, 0, simulated=True) == 2
    assert _n(4, 2, simulated=True) == 2


def test_selling_more_than_held_does_not_go_negative():
    # 방어적: 매도 수가 보유를 넘겨도 음수 슬롯이 나오지 않는다.
    assert _n(2, 5) == settings.live_max_buys_per_day
