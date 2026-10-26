"""What a backtest request may ask qlib to import — and nothing else.

2026-10-26 보안 점검: `BacktestRequest` 의 `*_module` / `*_class` / `*_kwargs` 가
그대로 `qlib.utils.init_instance_by_config` 로 들어갔다. 그 함수는 문자열로
모듈을 import 하고 클래스를 호출하므로, 로그인한 요청 하나가
`model_module="subprocess", model_class="Popen"` 으로 워커(root)에서 명령을
실행할 수 있었다. grid 의 `param_sweeps` 는 스키마 검증 **뒤에** 점 경로로 아무
키나 덮어쓰므로 요청 스키마만 막아서는 안 된다 — 그래서 이 검사는 인스턴스를
만드는 자리(`backtest_service.run_backtest`)에서 다시 돈다.

허용 목록 = 프런트 카탈로그(app/frontend/src/lib/catalogs.ts). 새 클래스를
쓰려면 두 곳에 함께 넣는다.
"""

from __future__ import annotations

import re

ALLOWED = {
    "strategy": {
        ("qlib.contrib.strategy.signal_strategy", "TopkDropoutStrategy"),
        ("qlib.contrib.strategy.signal_strategy", "EnhancedIndexingStrategy"),
        ("qlib.contrib.strategy.cost_control", "SoftTopkStrategy"),
        ("qlib.contrib.strategy.rule_strategy", "TWAPStrategy"),
        ("qlib.contrib.strategy.rule_strategy", "SBBStrategyEMA"),
    },
    "model": {
        ("qlib.contrib.model.gbdt", "LGBModel"),
        ("qlib.contrib.model.xgboost", "XGBModel"),
        ("qlib.contrib.model.catboost_model", "CatBoostModel"),
        ("qlib.contrib.model.linear", "LinearModel"),
    },
    "handler": {
        ("qlib.contrib.data.handler", "Alpha158"),
        ("qlib.contrib.data.handler", "Alpha360"),
    },
}

# qlib resolves "a.b.ClassName" strings and {"class", "module_path"} dicts found
# inside kwargs too (e.g. handler processors). A dotted identifier path is the
# shape of an import; nothing a legitimate kwarg needs looks like one.
_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")


class UnsafeBacktestConfig(ValueError):
    pass


def _check_kwargs(where: str, value) -> None:
    if isinstance(value, dict):
        if "module_path" in value or "func" in value:
            raise UnsafeBacktestConfig(f"{where}: module_path/func not allowed")
        cls = value.get("class")
        if cls is not None and (not isinstance(cls, str) or "." in cls or cls.startswith("_")):
            raise UnsafeBacktestConfig(f"{where}.class: {cls!r} not allowed")
        for k, v in value.items():
            _check_kwargs(f"{where}.{k}", v)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_kwargs(f"{where}[{i}]", v)
    elif isinstance(value, str) and _DOTTED.match(value):
        raise UnsafeBacktestConfig(f"{where}: {value!r} looks like an import path")


def check_backtest_config(cfg: dict) -> None:
    """Raise UnsafeBacktestConfig unless every class is allow-listed and no
    kwarg can make qlib import something else."""
    for part, allowed in ALLOWED.items():
        pair = (cfg.get(f"{part}_module"), cfg.get(f"{part}_class"))
        if pair not in allowed:
            raise UnsafeBacktestConfig(f"{part} {pair[1]!r} from {pair[0]!r} is not allowed")
        _check_kwargs(f"{part}_kwargs", cfg.get(f"{part}_kwargs") or {})


# ─── /data/features field expressions ────────────────────────────────
#
# qlib turns a field into Python with parse_field() and eval()s it
# (qlib/data/data.py). Only `$feature`, numbers, operator calls and arithmetic
# are legitimate; quotes, dunders and attribute access are how an eval escapes.
_FIELD_CHARS = re.compile(r"^[\w$\s+\-*/(),<>=&|~%.]+$")
_IDENT = re.compile(r"(?<![\w$.])([A-Za-z_]\w*)(\s*\()?")


def check_field(field: str) -> None:
    if not field or len(field) > 300 or not _FIELD_CHARS.match(field) or "__" in field:
        raise UnsafeBacktestConfig(f"field {field[:40]!r} not allowed")
    if re.search(r"(?<!\d)\.|\.(?!\d)", field):
        raise UnsafeBacktestConfig(f"field {field[:40]!r}: attribute access not allowed")
    for m in _IDENT.finditer(field):
        if not m.group(2):  # a bare name, not `$x` and not an operator call
            raise UnsafeBacktestConfig(f"field {field[:40]!r}: bare name {m.group(1)!r}")
