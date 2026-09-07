"""실계좌 `open` 의 청산은 **순위 이탈 매도 하나**임을 못박는 가드.

배경 — 2026-08-26 에 "사다리 +10% 절반 + 잔여 트레일 −7%" 를 실계좌에 넣었다가
하루 만에 철회했다. 644 거래일 백테스트가 랭크만(+167.8%) 대비 사다리+트레일
(+45.3%)이 **수익 122%p 를 깎는다**고 확증했기 때문이다(이익 나는 종목을
1,307건 잘랐다). 2026-08-27 에 beat 등록을 지웠고, **2026-09-07 에 실행 코드까지
지웠다** — 잠재우기만 하면 다시 켜지고, 실제로 문 하나가 열려 있었다:
`ladder_reserve` 태스크를 `strategy="scale"` 로 손호출하면 `_account_for` 가
기본 계좌(= open 실계좌)로 폴백해 보유 **전량**에 지정가 매도를 냈다.

이 파일은 기능을 시험하지 않는다. **없음을 시험한다.** 되살리려면 먼저
`scripts/backtest_entry_modes.py` 로 백테스트를 돌리고(INSIGHTS §2 게이트 ⑤:
백테스트 → 모의 → 실계좌), 그 다음 이 테스트들을 의도적으로 고쳐야 한다.
"""
from __future__ import annotations

import json

import app.api.services.live_trader as lt
from app.api.db.models import STRATEGY_OPEN


def test_open_has_no_exit_rule():
    """EXIT_RULES 에 open 키가 있으면 브래킷 청산이 살아난다."""
    assert STRATEGY_OPEN not in lt.EXIT_RULES


def test_open_is_not_a_bracket_strategy():
    """16:25 청산 스윕이 도는 목록. open 이 들어가면 실주문이 나간다."""
    assert STRATEGY_OPEN not in lt.BRACKET_STRATEGIES


def test_open_reads_its_balance_from_the_broker_but_places_no_bracket_orders():
    """2026-09-07 정정 — 예전엔 `REAL_BRACKET_STRATEGIES` 하나가 두 뜻으로 쓰였고,
    이 테스트가 그 위험한 소속을 오히려 고정하고 있었다. 이제 상수가 갈렸다:

    - `REAL_BALANCE_STRATEGIES` = 잔고를 KIS 에서 읽는다 → open 이 **있어야** 한다
      (빼면 실계좌 곡선이 장부 재구성으로 떨어진다)
    - `REAL_BRACKET_STRATEGIES` = 청산이 실주문을 낸다 → open 이 **없어야** 한다
    """
    assert STRATEGY_OPEN in lt.REAL_BALANCE_STRATEGIES
    assert STRATEGY_OPEN not in lt.REAL_BRACKET_STRATEGIES


def test_bracket_sweep_refuses_a_non_bracket_strategy():
    """하드 가드 — EXIT_RULES 폴백이 임의 전략에 씌워지는 경로를 막는다.
    KIS 클라이언트를 만들기 전에 물러나야 한다."""
    out = lt.evaluate_bracket_exits(strategy=STRATEGY_OPEN)
    assert out["status"] == "not_a_bracket_strategy"
    assert out["exits"] == []


def test_ladder_and_trail_functions_are_gone():
    """함수가 남아 있으면 누군가 다시 호출한다."""
    for name in ("reserve_ladder_exits", "watch_trailing_exits", "_trail_line"):
        assert not hasattr(lt, name), f"{name} 이(가) 되살아났다"


def test_ladder_and_trail_tasks_are_not_registered():
    """수동 호출(flower/CLI)로 실계좌에 주문을 낼 수 있던 입구."""
    from app.api.workers.celery_app import celery_app

    for name in ("ladder_reserve", "trail_watch"):
        assert name not in celery_app.tasks, f"{name} 태스크가 되살아났다"


def test_ladder_and_trail_are_not_scheduled():
    from app.api.workers.celery_app import celery_app

    for key in ("ladder-reserve-open", "trail-watch-open"):
        assert key not in celery_app.conf.beat_schedule


def test_only_scale_uses_a_ladder():
    """사다리는 DB 전용 시뮬 곡선 `scale` 하나만 쓴다."""
    laddered = [k for k, v in lt.EXIT_RULES.items() if "ladder" in v]
    assert laddered == ["scale"], laddered


# 2026-08-27 운영 원장에서 그대로 가져온 GS 사다리 예약(주문 취소됨).
# 손으로 지어낸 모양이 아니라 **실제로 저장돼 있는 바이트**여야 판별기를
# 시험하는 의미가 있다 — 처음에 basis 문자열로 짐작해 썼다가 틀렸다.
GS_LADDER_RESERVATION = json.dumps({
    "action": "sell",
    "basis": "사다리 예약 +10% — 평단 132,650 → 145,900원 지정가로 보유 4주 중 2주.",
    "exit": {"kind": "ladder_reserve", "judged": "resting_limit", "rung": 0.1,
             "rung_px": 145900, "entry_avg": 132650.0, "entry_date": "2026-08-27",
             "entry_order_id": 408, "position_qty": 4, "ladder": [0.1]},
}, ensure_ascii=False)

RANK_DROPOUT_SELL = json.dumps({
    "action": "sell",
    "basis": "당일 신호 top-10 이탈 — 전일 24위 → 금일 30위권 밖",
}, ensure_ascii=False)


def test_ladder_reservation_reader_survives():
    """8/27 자 사다리 예약 주문이 원장에 남아 있다(GS 취소분 포함).
    잔고 대사·보유 귀속·취소 스윕이 이 판별기로 과거 주문을 해석하므로,
    실행 코드를 지워도 **판별기는 남아야 한다.**"""
    assert lt._is_ladder_reservation(GS_LADDER_RESERVATION)
    assert not lt._is_ladder_reservation(RANK_DROPOUT_SELL)
    assert not lt._is_ladder_reservation(None)
    assert not lt._is_ladder_reservation("깨진 json{{{")


def test_archived_rule_lives_next_to_its_only_consumer():
    """철회한 파라미터는 지우지 않고 백테스트 스크립트로 옮겼다 —
    다시 켤 때 같은 값으로 비교할 수 있어야 한다. 단, 운영 모듈에는 없다."""
    assert not hasattr(lt, "OPEN_EXIT_RULES_ARCHIVED")
    import importlib.util
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "backtest_entry_modes.py"
    text = src.read_text(encoding="utf-8")
    assert "OPEN_EXIT_RULES_ARCHIVED" in text
    assert '"ladder": [0.10]' in text and '"trail_rest": 0.07' in text
