"""백테스트 하네스가 **운영과 같은 규칙**을 쓰는지 고정한다.

백테스트는 운영 코드를 복제한다. 복제는 원본이 바뀌면 조용히 갈라진다 —
그리고 갈라진 백테스트는 틀린 답을 **그럴듯하게** 낸다. 여기서 막는 것:

  * 워크포워드 창 계산이 운영 `_walk_forward_windows` 와 어긋나는 것
  * 학습 시작일이 운영(`generate_daily_signal` 의 "2023-04-01")과 달라지는 것
  * 지정가 체결 판정이 `evaluate_limit_entries` 와 달라지는 것

2026-08-26 실측: 신호 하네스는 08-05(커밋 5bf863c8, 고정 150라운드 학습) 이후
운영 Signal 과 top10 **10/10 일치**했고, 체결 엔진은 신호 일치 구간에서 운영
수익률과 −1.68%p 차이로 재현했다. 그 정합이 이 테스트가 지키는 것이다.
"""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_walk_forward_matches_production():
    """창 계산이 운영과 한 날도 어긋나면 안 된다.

    train_end / valid_start 는 순수 날짜 산술이라 운영 함수와 직접 대조된다.
    valid_end 는 거래일 스냅이라 같은 함수를 주입해 비교한다.
    """
    bs = _load("backtest_signals")
    from app.api.services import live_trader as lt

    for day in (dt.date(2024, 1, 2), dt.date(2025, 6, 30), dt.date(2026, 8, 26),
                dt.date(2026, 3, 1)):
        mine = bs.walk_forward(day, lambda d: d - dt.timedelta(days=1))
        # 운영 함수의 앞 두 값은 달력과 무관하다.
        import pandas as pd
        ts = pd.Timestamp(day)
        assert mine[0] == (ts - pd.DateOffset(months=3)).date()
        assert mine[1] == (ts - pd.DateOffset(months=2, days=29)).date()
        assert mine[2] == day - dt.timedelta(days=1)   # 주입한 스냅 함수 그대로


def test_train_start_matches_production():
    """학습 시작일이 갈라지면 백테스트가 운영을 재현하지 못한다."""
    bs = _load("backtest_signals")
    src = (ROOT / "app" / "api" / "services" / "live_trader.py").read_text()
    assert f'"start_time": "{bs.TRAIN_START}"' in src, (
        f"backtest TRAIN_START={bs.TRAIN_START} 가 live_trader 의 핸들러 "
        f"start_time 과 다르다")
    assert f'"train": ("{bs.TRAIN_START}"' in src


def test_mode_parsing():
    bm = _load("backtest_entry_modes")
    assert bm.parse_mode("market") == ("market", None)
    assert bm.parse_mode("limit2") == ("limit", 0.02)
    assert bm.parse_mode("limit5") == ("limit", 0.05)
    with pytest.raises(SystemExit):
        bm.parse_mode("nonsense")


def test_limit_fill_rule_matches_production():
    """체결 판정 두 줄이 `evaluate_limit_entries` 와 문자 그대로 같아야 한다.

    이 규칙이 갈라지면 A/B 의 '지정가' 쪽이 다른 실험이 된다.
    """
    src = (ROOT / "app" / "api" / "services" / "live_trader.py").read_text()
    engine = (ROOT / "scripts" / "backtest_entry_modes.py").read_text()
    for line in ('filled = bar["low"] <= limit_px',
                 'fill_px = bar["open"] if (filled and bar["open"] < limit_px) else limit_px'):
        assert line in src, f"운영에서 사라진 규칙: {line}"
        assert line in engine, f"백테스트에서 사라진 규칙: {line}"


def test_backtest_scripts_do_not_touch_the_live_db():
    """백테스트는 운영 DB 를 절대 건드리면 안 된다.

    임시 SQLite 도 쓰지 않는 순수 루프이므로, 세션을 여는 코드가 들어오면
    그 자체가 사고 신호다.
    """
    for name in ("backtest_signals", "backtest_entry_modes"):
        src = (ROOT / "scripts" / f"{name}.py").read_text()
        assert "SessionLocal" not in src, f"{name}: 운영 DB 세션을 연다"
        assert "db.commit" not in src, f"{name}: DB 에 쓴다"
