"""Strategy catalog served to per-user runners (`GET /feed/strategies`).

The hub/runner split (2026-10-26, plan: qlib-encapsulated-wirth): a runner
container picks ONE strategy type, then tunes its parameters. The twelve
strategies we run today are not twelve programs — they are four entry ideas
(cafe / rank / bracket / surge) with different knobs, so each becomes a
*preset* of its type.

Every default below is READ from the live objects (EXIT_RULES, LIVE_CONFIG,
FLOW_CONFIG, settings) rather than copied. A preset that drifts from what the
hub actually runs would make the runner's parity check (P3) compare two
different strategies. Regression guard: tests/app/test_feed_catalog.py.
"""

from __future__ import annotations

from ..config import settings
from ..db.models import (
    STRATEGY_CAFE, STRATEGY_CAFECOOL, STRATEGY_CAFEOPEN, STRATEGY_CAFEREAL,
    STRATEGY_CLOSE, STRATEGY_COOLREAL, STRATEGY_FLOW, STRATEGY_LIMIT,
    STRATEGY_OPEN, STRATEGY_SCALE, STRATEGY_SURGE, STRATEGY_TRAIL,
)

CATALOG_VERSION = 1

TYPE_CAFE = "cafe"
TYPE_RANK = "rank"
TYPE_BRACKET = "bracket"
TYPE_SURGE = "surge"


def _num(title: str, default, minimum=None, maximum=None, nullable=False) -> dict:
    spec: dict = {"type": ["number", "null"] if nullable else "number",
                  "title": title, "default": default}
    if minimum is not None:
        spec["minimum"] = minimum
    if maximum is not None:
        spec["maximum"] = maximum
    return spec


def _int(title: str, default, minimum=None, maximum=None) -> dict:
    spec = _num(title, default, minimum, maximum)
    spec["type"] = "integer"
    return spec


def _enum(title: str, default: str, values: list[str]) -> dict:
    return {"type": "string", "title": title, "default": default, "enum": values}


# 구조적 손절(직전 N일 최저가 × (1−버퍼), 상한 −sl) — bracket·surge 공통.
def _structural_stop() -> dict:
    return {
        "low_window": _int("구조적 손절 — 진입 전 최저가 창(거래일)",
                           settings.live_close_bracket_low_window, 1, 60),
        "low_buffer": _num("구조적 손절 — 최저가 아래 버퍼",
                           settings.live_close_bracket_low_buffer, 0.0, 0.10),
        "sl_cap": _num("손절 상한(평단 대비)", settings.live_close_bracket_sl, 0.01, 0.30),
    }


def strategy_types() -> dict[str, dict]:
    """Per-type parameter schema. Defaults = the hub's current values."""
    return {
        TYPE_CAFE: {
            "label": "카페형 — 스크리너 후보 매수, TP / 구조적 손절",
            "signal": "cafe_candidates",
            "params": {
                "entry": _enum("진입 시점", "next_open",
                               ["next_open", "same_close", "next_open_discount"]),
                "ret20_max": _num("ret20 상한(%) — 비우면 제한 없음", None, 0, 500, nullable=True),
                "slots": _int("최대 보유 종목 수", settings.live_cafe_slots, 1, 30),
                "max_buys": _int("하루 최대 매수", settings.live_cafe_max_buys, 1, 10),
                "tp": _num("익절", 0.10, 0.01, 1.0),
                "stop_cap": _num("손절 상한(평단 대비)", settings.live_cafe_stop_cap, 0.01, 0.50),
                "open_discount": _num("next_open_discount 의 시가 할인",
                                      settings.live_cafeopen_discount, 0.0, 0.20),
            },
        },
        TYPE_RANK: {
            "label": "모델 순위형 — LightGBM topk, 순위 이탈 매도만",
            "signal": "signals",
            "params": {
                "topk": _int("보유 순위 컷", _rank_kwargs()["topk"], 1, 30),
                "n_drop": _int("하루 교체 수", _rank_kwargs()["n_drop"], 0, 10),
                "max_buys": _int("하루 최대 매수", settings.live_max_buys_per_day, 1, 10),
            },
        },
        TYPE_BRACKET: {
            "label": "브래킷형 — 모델 순위 진입, 익절·트레일·사다리 청산",
            "signal": "signals",
            "params": {
                "entry": _enum("진입 방식", "close", ["close", "limit"]),
                "topk": _int("후보 순위 컷", _rank_kwargs()["topk"], 1, 30),
                "max_buys": _int("하루 최대 매수", _rank_kwargs()["n_drop"], 1, 10),
                "limit_discount": _num("limit 진입 — 전일 종가 할인",
                                       settings.live_limit_discount, 0.0, 0.20),
                "limit_candidates": _int("limit 진입 — 후보 수",
                                         settings.live_limit_candidates, 1, 30),
                "flow_overlay": {"type": "boolean", "title": "기관·외국인 수급 재정렬",
                                 "default": False},
                "tp": _num("익절 — 비우면 없음", 0.10, 0.01, 1.0, nullable=True),
                "trail": _num("트레일링(고점 대비) — 비우면 없음", None, 0.01, 0.50, nullable=True),
                "ladder": {"type": ["array", "null"], "items": {"type": "number"},
                           "title": "분할 익절 사다리 — 비우면 없음", "default": None},
                "floor_gap": _num("사다리 바닥 간격", None, 0.01, 0.50, nullable=True),
                **_structural_stop(),
            },
        },
        TYPE_SURGE: {
            "label": "급등 전야형 — surge_picks 상위 종가 매수",
            "signal": "surge_picks",
            "params": {
                "slots": _int("최대 보유 종목 수", settings.live_surge_slots, 1, 30),
                "max_buys": _int("하루 최대 매수", settings.live_surge_max_buys, 1, 10),
                "tp": _num("익절", 0.10, 0.01, 1.0),
                **_structural_stop(),
            },
        },
    }


def common_params() -> dict:
    """Settings every runner has regardless of type — safety rails first."""
    return {
        "mode": _enum("실행 모드", "sim", ["sim", "paper", "real"]),
        "seed_cash": _num("시드(원)", 10_000_000.0, 100_000, 1_000_000_000),
        # 최댓값이 없으면 runner 검증이 1e15·inf 를 받았다(2026-10-26 점검). runner 의
        # 기동 가드 상한(MAX_ORDER_VALUE_CEILING)과 같은 값.
        "max_order_value": _num("1회 매수 상한(원) — 시장가 포함", settings.live_max_order_value,
                                10_000, 50_000_000),
        "manual_exit_max_per_day": _int("수동 청산 인식 하루 상한",
                                        settings.live_manual_exit_max_per_day, 0, 50),
    }


def _rank_kwargs() -> dict:
    from .live_trader import LIVE_CONFIG
    return LIVE_CONFIG["strategy_kwargs"]


def _exit(strategy: str) -> dict:
    """EXIT_RULES row → bracket/surge exit params (keys the type declares)."""
    from .live_trader import EXIT_RULES
    rule = EXIT_RULES[strategy]
    return {"tp": rule.get("tp"), "trail": rule.get("trail"),
            "ladder": rule.get("ladder"), "floor_gap": rule.get("floor_gap")}


def presets() -> dict[str, dict]:
    """The hub's twelve strategies expressed as (type, param overrides).

    Only values that differ from the type default are spelled out — the rest
    come from `strategy_types()`, which reads the same sources.
    """
    return {
        STRATEGY_CAFEREAL: {"type": TYPE_CAFE, "params": {"entry": "next_open"}},
        STRATEGY_COOLREAL: {"type": TYPE_CAFE, "params": {
            "entry": "next_open", "ret20_max": settings.live_coolreal_ret20_max}},
        STRATEGY_CAFE: {"type": TYPE_CAFE, "params": {"entry": "same_close"}},
        STRATEGY_CAFECOOL: {"type": TYPE_CAFE, "params": {
            "entry": "same_close", "ret20_max": settings.live_cafecool_ret20_max}},
        # ⚠ hub 의 cafeopen 은 cafe 시뮬의 **체결 기록**을 신호로 쓴다
        # (live_trader.submit_cafeopen_orders). runner 에는 그 기록이 없어
        # "전일 후보 → 시가 할인 지정가"로 재정의된다 — 동작이 같지 않다.
        STRATEGY_CAFEOPEN: {"type": TYPE_CAFE, "params": {"entry": "next_open_discount"},
                            "parity": False},
        STRATEGY_OPEN: {"type": TYPE_RANK, "params": {}},
        STRATEGY_CLOSE: {"type": TYPE_BRACKET, "params": _exit(STRATEGY_CLOSE)},
        STRATEGY_FLOW: {"type": TYPE_BRACKET, "params": {
            **_exit(STRATEGY_FLOW), "flow_overlay": True}},
        STRATEGY_TRAIL: {"type": TYPE_BRACKET, "params": _exit(STRATEGY_TRAIL)},
        STRATEGY_SCALE: {"type": TYPE_BRACKET, "params": _exit(STRATEGY_SCALE)},
        STRATEGY_LIMIT: {"type": TYPE_BRACKET, "params": {
            **_exit(STRATEGY_LIMIT), "entry": "limit",
            "max_buys": settings.live_limit_max_fills}},
        STRATEGY_SURGE: {"type": TYPE_SURGE, "params": {"tp": _exit(STRATEGY_SURGE)["tp"]}},
    }


def catalog() -> dict:
    # flow_overlay=True 인 runner 가 hub 와 같은 재정렬을 하려면 가중치도 같아야 한다.
    from .market_flow import FLOW_CONFIG
    return {"version": CATALOG_VERSION, "common": common_params(),
            "types": strategy_types(), "presets": presets(),
            "flow_config": dict(FLOW_CONFIG)}
