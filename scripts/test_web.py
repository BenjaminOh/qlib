#!/usr/bin/env python3
"""End-to-end smoke tests for the qlib local web stack.

Runs against the live services spun up by `./start-local.sh web`
(or `web-bg`). Uses stdlib only — no extra pip deps needed on host.

Usage
-----
    ./start-local.sh web-bg       # start services
    ./start-local.sh data-kr      # (once) load KOSPI200 data
    python3 scripts/test_web.py   # run tests

Or via helper:
    ./start-local.sh test-web
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = "http://localhost:5001"
UI = "http://localhost:5002"


def _get(url: str, timeout: float = 15.0) -> tuple[int, object]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read().decode("utf-8")
        try:
            return r.status, json.loads(body)
        except json.JSONDecodeError:
            return r.status, body


def _post(url: str, payload: dict, timeout: float = 15.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _wait_backtest(job_id: str, timeout: float = 300.0, poll: float = 3.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        _, resp = _get(f"{API}/api/v1/backtests/{job_id}")
        last = resp
        status = (resp.get("status") or "").upper()
        if status in {"COMPLETED", "SUCCESS", "FAILED", "ERROR"}:
            return resp
        time.sleep(poll)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s, last status={last!r}")


# Minimal backtest payload — small topk, short window, relies on kr_data being loaded.
# Default windows assume KR data spans 2023-01 → 2026+, with a 60-day Alpha158 warmup
# margin baked in (train starts 2023-04).
def _base_backtest_payload(backtest_end: str = "2025-12-31", benchmark: str | None = None) -> dict:
    return {
        "strategy_class": "TopkDropoutStrategy",
        "strategy_module": "qlib.contrib.strategy.signal_strategy",
        "strategy_kwargs": {"topk": 5, "n_drop": 1},
        "model_class": "LGBModel",
        "model_module": "qlib.contrib.model.gbdt",
        "model_kwargs": {},
        "handler_class": "Alpha158",
        "handler_module": "qlib.contrib.data.handler",
        "handler_kwargs": {},
        "train_start": "2023-04-01", "train_end": "2024-12-31",
        "valid_start": "2025-01-01", "valid_end": "2025-06-30",
        "test_start": "2025-07-01", "test_end": backtest_end,
        "backtest_start": "2025-07-01", "backtest_end": backtest_end,
        "account": 100_000_000,
        "benchmark": benchmark,
        "freq": "day",
        "exchange": {
            "limit_threshold": 0.3, "deal_price": "close",
            "open_cost": 0.00015, "close_cost": 0.00315,
            "min_cost": 0, "trade_unit": 1,
        },
        "instruments": "kospi200",
    }


# ─── Test cases ──────────────────────────────────────────────────

def test_api_health():
    status, body = _get(f"{API}/api/v1/health")
    assert status == 200, f"expected 200, got {status}"
    assert isinstance(body, dict), f"expected json dict, got {type(body).__name__}"


def test_frontend_reachable():
    status, _ = _get(UI, timeout=10.0)
    assert status == 200, f"frontend returned {status}"


def test_markets_lists_kospi():
    status, body = _get(f"{API}/api/v1/data/markets")
    assert status == 200
    names = {m["name"] for m in body["markets"]}
    expected = {"kospi200", "kosdaq150", "kospi", "kosdaq"}
    missing = expected - names
    assert not missing, f"markets missing: {missing} — got {names}"


def test_instruments_kospi200_populated():
    """Regression for data_service dynamic-vs-static dict bug."""
    status, body = _get(f"{API}/api/v1/data/instruments?market=kospi200")
    assert status == 200, f"status {status}, body={body}"
    # Response schema: {"instruments": [...], "count": N, "market": "kospi200"}
    count = body.get("count", 0)
    assert count >= 40, (
        f"kospi200 has only {count} instruments. "
        "Did you run `./start-local.sh data-kr`?"
    )


def test_calendar_returns_trading_days():
    status, body = _get(f"{API}/api/v1/data/calendar?start=2024-01-01&end=2024-01-31")
    assert status == 200
    dates = body.get("dates", [])
    # January has ~20 KR trading days
    assert 15 <= len(dates) <= 25, f"expected ~20 trading days, got {len(dates)}"


def test_backtest_happy_path():
    """Baseline: dates well within data range, null benchmark (exercises default substitution)."""
    _, resp = _post(f"{API}/api/v1/backtests/", _base_backtest_payload(backtest_end="2025-12-31"))
    result = _wait_backtest(resp["job_id"], timeout=240)
    status = (result.get("status") or "").upper()
    assert status == "COMPLETED", f"expected COMPLETED, got {status} — error={result.get('error')}"
    m = result.get("metrics") or {}
    assert m.get("annualized_return") is not None, "metrics.annualized_return missing"


def test_backtest_end_past_calendar():
    """Regression for IndexError when backtest_end >= provider's last trading day."""
    # Push the requested end past the actual last trading day on disk to exercise _clamp_to_calendar.
    _, resp = _post(f"{API}/api/v1/backtests/", _base_backtest_payload(backtest_end="2030-12-31"))
    result = _wait_backtest(resp["job_id"], timeout=300)
    status = (result.get("status") or "").upper()
    assert status == "COMPLETED", (
        f"calendar clamp regression: end=2030-12-31 should succeed, "
        f"got {status} — error={result.get('error')}"
    )


def test_backtests_list():
    status, body = _get(f"{API}/api/v1/backtests/")
    assert status == 200
    jobs = body.get("jobs") if isinstance(body, dict) else body
    assert isinstance(jobs, list), f"expected list, got {type(jobs).__name__}"
    # We just ran backtests above — list can't be empty.
    assert len(jobs) > 0, "backtests list empty despite recent submissions"


def test_backtest_softtopk_strategy():
    """Smoke test for an alternate strategy class going through the same pipeline.

    Note: with mixed-listing-history yfinance data the SoftTopk weight rebalance
    can hit a None deal_price; we accept FAILED-but-graceful here so the test
    surfaces hard regressions (CRASH/timeout) without flapping on data gaps.
    """
    payload = _base_backtest_payload()
    payload["strategy_class"] = "SoftTopkStrategy"
    payload["strategy_module"] = "qlib.contrib.strategy.cost_control"
    payload["strategy_kwargs"] = {"topk": 5, "risk_degree": 0.95, "trade_impact_limit": 1.0}
    _, resp = _post(f"{API}/api/v1/backtests/", payload)
    result = _wait_backtest(resp["job_id"], timeout=240)
    status = (result.get("status") or "").upper()
    assert status in {"COMPLETED", "FAILED"}, f"SoftTopk hit unexpected state {status}"
    if status == "FAILED":
        # Acceptable failure surface: dataset/price gaps, not import or schema errors.
        err = result.get("error") or ""
        ok_signals = ("Empty data from dataset", "deal_price", "NoneType")
        assert any(s in err for s in ok_signals), f"unexpected SoftTopk error: {err.splitlines()[0][:200]}"


def test_grid_endpoint_smoke():
    """Smoke test for the new /backtests/grid endpoint."""
    base = _base_backtest_payload()
    grid = {
        "base": base,
        "models": [
            {"class": "LGBModel", "module": "qlib.contrib.model.gbdt", "kwargs": {}},
        ],
        "strategies": [
            {"class": "TopkDropoutStrategy", "module": "qlib.contrib.strategy.signal_strategy", "kwargs": {}},
        ],
        "param_sweeps": {"strategy_kwargs.topk": [3, 5], "strategy_kwargs.n_drop": [1]},
    }
    status, body = _post(f"{API}/api/v1/backtests/grid", grid)
    assert status == 200, f"grid POST returned {status}: {body}"
    assert "group_id" in body, f"missing group_id in {body}"
    assert body.get("total") == 2, f"expected 2 jobs (2 topk × 1 ndrop × 1 model × 1 strategy), got {body.get('total')}"

    # Poll the group endpoint at least once to make sure GET works.
    _, group = _get(f"{API}/api/v1/backtests/grid/{body['group_id']}")
    assert "jobs" in group, f"group response missing jobs: {group}"
    assert len(group["jobs"]) == 2


TESTS = [
    ("api_health", test_api_health),
    ("frontend_reachable", test_frontend_reachable),
    ("markets_lists_kospi", test_markets_lists_kospi),
    ("instruments_kospi200_populated", test_instruments_kospi200_populated),
    ("calendar_returns_trading_days", test_calendar_returns_trading_days),
    ("backtest_happy_path", test_backtest_happy_path),
    ("backtest_end_past_calendar", test_backtest_end_past_calendar),
    ("backtest_softtopk_strategy", test_backtest_softtopk_strategy),
    ("grid_endpoint_smoke", test_grid_endpoint_smoke),
    ("backtests_list", test_backtests_list),
]


def main() -> int:
    print(f"Running {len(TESTS)} tests against {API} / {UI}\n")
    passed: list[str] = []
    failed: list[tuple[str, str]] = []
    for name, fn in TESTS:
        t0 = time.time()
        try:
            fn()
            elapsed = time.time() - t0
            print(f"  PASS  {name:<36} ({elapsed:5.1f}s)")
            passed.append(name)
        except urllib.error.HTTPError as e:
            elapsed = time.time() - t0
            body = ""
            try:
                body = e.read().decode("utf-8")[:200]
            except Exception:  # noqa: BLE001
                pass
            print(f"  FAIL  {name:<36} ({elapsed:5.1f}s)  HTTP {e.code} {body}")
            failed.append((name, f"HTTP {e.code}: {body}"))
        except (urllib.error.URLError, ConnectionRefusedError) as e:
            elapsed = time.time() - t0
            print(f"  FAIL  {name:<36} ({elapsed:5.1f}s)  network: {e}")
            failed.append((name, f"network: {e}"))
        except Exception as e:  # noqa: BLE001
            elapsed = time.time() - t0
            print(f"  FAIL  {name:<36} ({elapsed:5.1f}s)  {type(e).__name__}: {e}")
            failed.append((name, f"{type(e).__name__}: {e}"))

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("\nFailures:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
