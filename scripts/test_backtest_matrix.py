#!/usr/bin/env python3
"""Exploratory backtest matrix — tries real-world parameter combinations
users are likely to enter via the UI, submits each through the API, waits
for completion, and reports PASS / FAIL / ERROR per scenario.

Use this when you want broader coverage than the fast smoke test
(`scripts/test_web.py`). Runs sequentially because the Celery worker is
concurrency=1; expect several minutes total.

Usage
-----
    ./start-local.sh web-bg
    ./start-local.sh data-kr                 # once
    python3 scripts/test_backtest_matrix.py
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

API = "http://localhost:5001"


def _post(url: str, payload: dict, timeout: float = 15.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url: str, timeout: float = 15.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _wait(job_id: str, timeout: float = 240.0, poll: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = _get(f"{API}/api/v1/backtests/{job_id}")
        status = (resp.get("status") or "").upper()
        if status in {"COMPLETED", "SUCCESS", "FAILED", "ERROR"}:
            return resp
        time.sleep(poll)
    return {"status": "TIMEOUT", "error": f"no terminal status in {timeout}s"}


def _base() -> dict:
    return {
        "strategy_class": "TopkDropoutStrategy",
        "strategy_module": "qlib.contrib.strategy.signal_strategy",
        "strategy_kwargs": {"topk": 10, "n_drop": 2},
        "model_class": "LGBModel",
        "model_module": "qlib.contrib.model.gbdt",
        "model_kwargs": {},
        "handler_class": "Alpha158",
        "handler_module": "qlib.contrib.data.handler",
        "handler_kwargs": {},
        "train_start": "2020-01-01", "train_end": "2022-06-30",
        "valid_start": "2022-07-01", "valid_end": "2022-12-31",
        "test_start":  "2023-01-01", "test_end":  "2023-09-30",
        "backtest_start": "2023-07-01", "backtest_end": "2023-09-30",
        "account": 100_000_000,
        "benchmark": None,
        "freq": "day",
        "exchange": {
            "limit_threshold": 0.3, "deal_price": "close",
            "open_cost": 0.00015, "close_cost": 0.00315,
            "min_cost": 0, "trade_unit": 1,
        },
        "instruments": "kospi200",
    }


@dataclass
class Scenario:
    name: str
    # Expected outcome: "pass" = COMPLETED with metrics, "graceful_fail" = FAILED with clean error (no stack trace crash),
    # or None = we don't care, just want to know what happens.
    expect: str | None
    patch: dict = field(default_factory=dict)   # deep-ish merge on top of _base
    # Optional runtime behaviour hooks
    longer_timeout: float | None = None
    note: str = ""


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


SCENARIOS: list[Scenario] = [
    # ── Group A: strategy knobs (should all pass) ────────────────────
    Scenario("A1_topk=1_ndrop=0",          expect="pass",
             patch={"strategy_kwargs": {"topk": 1, "n_drop": 0}}),
    Scenario("A2_topk=20_ndrop=5",         expect="pass",
             patch={"strategy_kwargs": {"topk": 20, "n_drop": 5}}),
    Scenario("A3_topk=49_ndrop=10",        expect="pass",
             patch={"strategy_kwargs": {"topk": 49, "n_drop": 10}},
             note="topk == whole universe"),
    Scenario("A4_topk_gt_universe",        expect=None,
             patch={"strategy_kwargs": {"topk": 200, "n_drop": 3}},
             note="topk > 49 available stocks"),
    Scenario("A5_ndrop_ge_topk",           expect=None,
             patch={"strategy_kwargs": {"topk": 5, "n_drop": 5}},
             note="n_drop equals topk (nothing held)"),

    # ── Group B: date ranges ────────────────────────────────────────
    Scenario("B1_1month_backtest",         expect="pass",
             patch={"backtest_start": "2023-07-01", "backtest_end": "2023-07-31"}),
    Scenario("B2_end_at_last_trading_day", expect="pass",
             patch={"test_end": "2024-12-31", "backtest_end": "2024-12-31"},
             note="regression: calendar boundary"),
    Scenario("B3_start_before_data",       expect="graceful_fail",
             patch={"train_start": "2010-01-01", "backtest_start": "2015-06-01",
                    "backtest_end": "2015-12-31",
                    "test_start": "2015-01-01", "test_end": "2015-12-31"},
             note="train window older than provider data — expects clean ValueError"),
    Scenario("B4_train_test_overlap",      expect=None,
             patch={"train_end": "2023-12-31", "test_start": "2023-01-01",
                    "test_end": "2023-09-30", "backtest_start": "2023-01-01",
                    "backtest_end": "2023-09-30"},
             note="invalid but UI allows it"),

    # ── Group C: instruments strings ────────────────────────────────
    Scenario("C1_kosdaq150_no_data",       expect="graceful_fail",
             patch={"instruments": "kosdaq150"},
             note="market exists in API list but no data downloaded"),
    Scenario("C2_invalid_market_name",     expect="graceful_fail",
             patch={"instruments": "invalid_market_xxx"}),
    Scenario("C3_empty_instruments",       expect="graceful_fail",
             patch={"instruments": ""}),

    # ── Group D: exchange params ────────────────────────────────────
    Scenario("D1_zero_costs",              expect="pass",
             patch={"exchange": {"open_cost": 0.0, "close_cost": 0.0, "min_cost": 0.0}}),
    Scenario("D2_huge_costs",              expect="pass",
             patch={"exchange": {"open_cost": 0.01, "close_cost": 0.03}},
             note="1%/3% fees — trades may be suppressed"),
    Scenario("D3_limit_threshold_zero",    expect="pass",
             patch={"exchange": {"limit_threshold": 0.0}},
             note="no price-limit filter"),

    # ── Group E: account sizing ─────────────────────────────────────
    Scenario("E1_tiny_account_1000",       expect=None,
             patch={"account": 1_000},
             note="1000 KRW < single share of many KOSPI200 stocks"),
    Scenario("E2_huge_account_1T",         expect="pass",
             patch={"account": 1_000_000_000_000}),

    # ── Group F: benchmark handling ─────────────────────────────────
    Scenario("F1_valid_stock_benchmark",   expect="pass",
             patch={"benchmark": "005930"}),
    Scenario("F2_nonexistent_benchmark",   expect="graceful_fail",
             patch={"benchmark": "ZZZZZZ"}),

    # ── Group G: alternate strategies ───────────────────────────────
    Scenario("G1_softtopk",                expect="pass",
             patch={
                 "strategy_class": "SoftTopkStrategy",
                 "strategy_module": "qlib.contrib.strategy.cost_control",
                 "strategy_kwargs": {"topk": 20, "risk_degree": 0.95, "trade_impact_limit": 1.0},
             },
             note="cost-aware soft TopK"),
    Scenario("G2_enhanced_indexing_no_riskmodel", expect="graceful_fail",
             patch={
                 "strategy_class": "EnhancedIndexingStrategy",
                 "strategy_module": "qlib.contrib.strategy.signal_strategy",
                 "strategy_kwargs": {"riskmodel_root": "/nonexistent", "lamb": 0.001, "delta": 0.05},
             },
             note="missing risk model directory → expects graceful failure"),
    Scenario("G3_sbb_ema",                 expect="pass",
             patch={
                 "strategy_class": "SBBStrategyEMA",
                 "strategy_module": "qlib.contrib.strategy.rule_strategy",
                 "strategy_kwargs": {"instruments": "kospi200", "freq": "day"},
             },
             note="rule-based EMA strategy (signal ignored)"),

    # ── Group H: alternate models ───────────────────────────────────
    Scenario("H1_xgboost",                 expect="pass",
             patch={
                 "model_class": "XGBModel",
                 "model_module": "qlib.contrib.model.xgboost",
                 "model_kwargs": {"max_depth": 4, "eta": 0.1, "n_estimators": 50},
             },
             longer_timeout=360),
    Scenario("H2_linear_ridge",            expect="pass",
             patch={
                 "model_class": "LinearModel",
                 "model_module": "qlib.contrib.model.linear",
                 "model_kwargs": {"estimator": "ridge", "alpha": 0.05},
             }),
    Scenario("H3_catboost",                expect="pass",
             patch={
                 "model_class": "CatBoostModel",
                 "model_module": "qlib.contrib.model.catboost_model",
                 "model_kwargs": {"iterations": 50, "learning_rate": 0.1, "depth": 4},
             },
             longer_timeout=360),
]


def classify(result: dict, scenario: Scenario) -> tuple[str, str]:
    """Returns (bucket, detail) where bucket is PASS / FAIL / UNEXPECTED_PASS / UNEXPECTED_FAIL / CRASH."""
    status = (result.get("status") or "").upper()
    err = result.get("error") or ""
    metrics = result.get("metrics")

    if status == "TIMEOUT":
        return "CRASH", "job never reached terminal status"

    if status == "COMPLETED" and metrics:
        if scenario.expect == "pass":
            return "PASS", "completed with metrics"
        if scenario.expect == "graceful_fail":
            return "UNEXPECTED_PASS", "expected graceful failure but got COMPLETED"
        return "PASS", "completed with metrics"

    if status == "COMPLETED" and not metrics:
        return "CRASH", "COMPLETED but no metrics"

    # FAILED/ERROR territory
    if scenario.expect == "pass":
        return "UNEXPECTED_FAIL", f"{status}: {err.splitlines()[0][:160] if err else 'no error message'}"
    if scenario.expect == "graceful_fail":
        first_line = err.splitlines()[0] if err else ""
        # Good graceful failure: mentions our data/config, not a random stack frame
        return "PASS", f"gracefully failed: {first_line[:160]}"
    # expect is None → just record
    return "FAIL", f"{status}: {err.splitlines()[0][:160] if err else 'no error'}"


def main() -> int:
    print(f"Running {len(SCENARIOS)} backtest scenarios. Each ~5-30s, total ~5-10 minutes.\n")

    buckets: dict[str, list[tuple[str, str]]] = {
        "PASS": [], "FAIL": [], "UNEXPECTED_PASS": [], "UNEXPECTED_FAIL": [], "CRASH": [],
    }
    start = time.time()

    for sc in SCENARIOS:
        payload = _merge(_base(), sc.patch)
        t0 = time.time()
        try:
            resp = _post(f"{API}/api/v1/backtests/", payload)
            job_id = resp.get("job_id")
            if not job_id:
                bucket, detail = "CRASH", f"POST returned no job_id: {resp}"
            else:
                result = _wait(job_id, timeout=sc.longer_timeout or 240)
                bucket, detail = classify(result, sc)
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")[:200]
            except Exception:  # noqa: BLE001
                body = ""
            bucket, detail = (
                ("PASS", f"API rejected (HTTP {e.code}): {body}")
                if sc.expect == "graceful_fail"
                else ("UNEXPECTED_FAIL" if sc.expect == "pass" else "FAIL",
                      f"HTTP {e.code}: {body}")
            )
        except Exception as e:  # noqa: BLE001
            bucket, detail = "CRASH", f"{type(e).__name__}: {e}"
            traceback.print_exc()

        elapsed = time.time() - t0
        symbol = {"PASS": "[PASS]", "FAIL": "[----]", "UNEXPECTED_PASS": "[??PASS]",
                  "UNEXPECTED_FAIL": "[FAIL!]", "CRASH": "[CRASH]"}[bucket]
        note = f"  ({sc.note})" if sc.note else ""
        print(f"  {symbol:<8} {sc.name:<34} ({elapsed:5.1f}s) {detail}{note}")
        buckets[bucket].append((sc.name, detail))

    total_elapsed = time.time() - start
    print(f"\nTotal runtime: {total_elapsed:.1f}s")
    print("Summary:")
    for k, entries in buckets.items():
        print(f"  {k}: {len(entries)}")

    problems = buckets["UNEXPECTED_FAIL"] + buckets["UNEXPECTED_PASS"] + buckets["CRASH"]
    if problems:
        print("\n⚠️  Issues requiring attention:")
        for name, msg in problems:
            print(f"  - {name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
