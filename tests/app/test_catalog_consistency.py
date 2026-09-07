"""카탈로그 정합성 게이트 — 목록끼리 어긋나면 배포를 막는다.

이 파일이 존재하는 이유는 2026-09-07 에 같은 모양의 사고가 **두 번** 나왔기 때문이다.

1. `ladder_reserve` 태스크가 beat 에서만 지워지고 함수는 남아, 손으로 부르면
   계좌 폴백을 타고 실계좌 보유 전량에 지정가 매도를 냈다.
2. `REAL_BRACKET_STRATEGIES` 하나가 "잔고를 KIS 에서 읽는다"와 "청산이 실주문을
   낸다" **두 뜻으로** 쓰여, `open` 이 `EXIT_RULES` 폴백 `{"tp": 0.10}` 을 타고
   실계좌 익절 주문을 낼 수 있었다.

둘 다 "목록 A 에는 있는데 목록 B 에는 없다"가 원인이다. 사람이 매번 눈으로 맞출
수 없으므로 Jenkins 배포 게이트(`pytest tests/app`)에서 기계가 본다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import app.api.services.live_trader as lt
from app.api.db import models as M

ROOT = Path(__file__).resolve().parents[2]


# ── 전략 카탈로그 ────────────────────────────────────────────────
def test_all_strategies_matches_the_strategy_constants():
    """`ALL_STRATEGIES` 가 모든 `STRATEGY_*` 상수를 빠짐없이 담는가."""
    declared = {v for k, v in vars(M).items()
                if k.startswith("STRATEGY_") and isinstance(v, str)}
    assert set(M.ALL_STRATEGIES) == declared, (
        f"카탈로그 누락/잉여: {declared ^ set(M.ALL_STRATEGIES)}")
    assert len(M.ALL_STRATEGIES) == len(set(M.ALL_STRATEGIES)), "중복 원소"


def test_exit_rules_keys_are_known_strategies():
    unknown = set(lt.EXIT_RULES) - set(M.ALL_STRATEGIES)
    assert not unknown, f"EXIT_RULES 에 정체불명 전략: {unknown}"


def test_bracket_strategies_are_known_strategies():
    unknown = set(lt.BRACKET_STRATEGIES) - set(M.ALL_STRATEGIES)
    assert not unknown, f"BRACKET_STRATEGIES 에 정체불명 전략: {unknown}"


def test_every_bracket_strategy_has_an_exit_rule():
    """폴백 `{"tp": ...}` 에 의존하는 전략이 있으면 안 된다 — 폴백은
    '규칙을 안 정했다'는 뜻이고, 그게 실계좌에 얹히면 사고다."""
    missing = [s for s in lt.BRACKET_STRATEGIES if s not in lt.EXIT_RULES]
    assert not missing, f"청산 규칙 없이 스윕을 도는 전략: {missing}"


# ── 🔴 지뢰 차단: 실주문 청산은 브래킷 대상의 부분집합이어야 한다 ──
def test_real_bracket_is_a_subset_of_bracket():
    """`REAL_BRACKET_STRATEGIES ⊄ BRACKET_STRATEGIES` 이면, 그 차집합 전략은
    `EXIT_RULES` 폴백을 타면서 실계좌 클라이언트를 잡는다 = 2026-09-07 지뢰."""
    leak = set(lt.REAL_BRACKET_STRATEGIES) - set(lt.BRACKET_STRATEGIES)
    assert not leak, (
        f"실계좌 청산이 브래킷 목록 밖에 있다: {leak} — "
        "EXIT_RULES 폴백이 실주문으로 나갈 수 있다")


def test_open_never_places_bracket_orders():
    """open 의 청산은 09:00 랭크 이탈 매도 하나뿐이다."""
    assert M.STRATEGY_OPEN not in lt.EXIT_RULES
    assert M.STRATEGY_OPEN not in lt.BRACKET_STRATEGIES
    assert M.STRATEGY_OPEN not in lt.REAL_BRACKET_STRATEGIES


def test_open_still_reads_its_balance_from_the_broker():
    """반대 방향 가드 — 여기서 빠지면 실계좌 곡선이 장부 재구성으로 떨어진다."""
    assert M.STRATEGY_OPEN in lt.REAL_BALANCE_STRATEGIES
    assert M.STRATEGY_CAFEREAL in lt.REAL_BALANCE_STRATEGIES


def test_real_strategies_all_have_an_account():
    """실주문 전략은 반드시 계좌가 지정돼 있어야 한다. 없으면 `_account_for`
    가 기본 계좌(open 실계좌)로 폴백해 남의 계좌로 주문이 나간다."""
    mapped = {s for strategies in M.ACCOUNT_STRATEGIES.values() for s in strategies}
    for s in set(lt.REAL_BALANCE_STRATEGIES) | set(lt.REAL_BRACKET_STRATEGIES):
        assert s in mapped, f"{s} 가 ACCOUNT_STRATEGIES 에 없다"


# ── 계좌 카탈로그 ────────────────────────────────────────────────
def test_account_ids_match_the_api_pattern():
    """`ACCOUNT_STRATEGIES` 에 계좌를 추가하면 API 도 받아줘야 한다.
    `routers/live.py` 의 정규식이 하드코딩이라 여기서 대조한다."""
    src = (ROOT / "app/api/routers/live.py").read_text(encoding="utf-8")
    patterns = re.findall(r'pattern=r?"\^\(([^)]+)\)\$"', src)
    # `^(all|real|sim)$`(주문 view 필터) 같은 다른 축의 정규식이 섞여 있으므로
    # **계좌 축만** 고른다 — 기본 계좌 id 를 포함하는 것이 계좌 패턴이다.
    account_patterns = [p for p in patterns if M.DEFAULT_ACCOUNT_ID in p.split("|")]
    assert account_patterns, "계좌 pattern 을 찾지 못했다 — 정규식 형태가 바뀌었나?"
    for pat in account_patterns:
        allowed = set(pat.split("|"))
        assert set(M.ACCOUNT_STRATEGIES) <= allowed, (
            f"API 가 거부하는 계좌: {set(M.ACCOUNT_STRATEGIES) - allowed}")


# ── 표시 카탈로그 (사람이 읽는 이름) ─────────────────────────────
def _frontend_src() -> str:
    """프론트 소스. 없으면 skip — `Dockerfile.prod --target test` 는
    `app/api` 와 `tests/` 만 복사하다가 2026-09-07 에 이 파일 하나를 추가했다.
    옛 이미지로 도는 환경에서는 조용히 건너뛴다."""
    import pytest

    f = ROOT / "app/frontend/src/lib/strategies.ts"
    if not f.exists():
        pytest.skip("프론트 소스 없음 (이 실행 환경에는 복사되지 않았다)")
    return f.read_text(encoding="utf-8")


def test_frontend_knows_every_strategy():
    """프론트 `STRATEGY_ORDER` 가 11개를 전부 알아야 화면에서 안 사라진다.
    (카페 실매매가 화면에서 사라졌던 사고가 이 축이다.)"""
    src = _frontend_src()
    block = src[src.index("export const STRATEGY_ORDER"):]
    # `string[]` 의 대괄호에 걸리지 않도록 배열 리터럴을 `= [` 로 앵커한다.
    block = block[block.index("= [") + 3:]
    block = block[:block.index("]")]
    listed = set(re.findall(r'"([a-z]+)"', block))
    assert listed == set(M.ALL_STRATEGIES), (
        f"프론트 목록 불일치: {listed ^ set(M.ALL_STRATEGIES)}")


def test_frontend_labels_cover_every_strategy():
    src = _frontend_src()
    block = src[src.index("export const STRATEGY_LABELS"):]
    block = block[:block.index("};")]
    labelled = set(re.findall(r'^\s*([a-z]+):', block, re.M))
    missing = set(M.ALL_STRATEGIES) - labelled
    assert not missing, f"화면 설명이 없는 전략: {missing}"


def test_notify_titles_cover_every_real_strategy():
    """실주문 전략은 알림 제목이 있어야 한다 — 없으면 원문 코드명이 나간다."""
    from app.api.services import notify

    real = set(lt.REAL_BALANCE_STRATEGIES)
    missing = real - set(notify.STRATEGY_TITLES)
    assert not missing, f"알림 제목이 없는 실계좌 전략: {missing}"


# ── 시드 카탈로그 ────────────────────────────────────────────────
def test_every_strategy_has_a_seed():
    for s in M.ALL_STRATEGIES:
        seed = lt._seed_for(s)
        assert isinstance(seed, (int, float)) and seed > 0, f"{s} 시드 이상: {seed}"


# ── 쌍둥이 불변식 ────────────────────────────────────────────────
def test_cafe_twins_share_identical_exit_rules():
    """cafe·cafeopen·cafecool·cafereal 은 **진입만 다른 쌍둥이**다.
    청산이 갈리면 A/B 가 무엇을 재는지 알 수 없게 된다."""
    rules = {s: lt.EXIT_RULES[s] for s in
             (M.STRATEGY_CAFE, M.STRATEGY_CAFEOPEN,
              M.STRATEGY_CAFECOOL, M.STRATEGY_CAFEREAL)}
    base = rules[M.STRATEGY_CAFE]
    for s, r in rules.items():
        assert r == base, f"{s} 청산 규칙이 cafe 와 다르다: {r} != {base}"


def test_ladder_belongs_only_to_scale():
    laddered = sorted(k for k, v in lt.EXIT_RULES.items() if "ladder" in v)
    assert laddered == [M.STRATEGY_SCALE], laddered
