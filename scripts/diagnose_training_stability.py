"""Training-stability diagnostic for the live signal model.

Rolls the exact live training setup over the last N trading days under
several parameter variants and reports, per (day, variant):

  - best_iteration      (1 == degenerate: validation never improved)
  - top10_unique        (distinct scores among the top-10 picks — 1 == constant)
  - score_std           (cross-sectional std of all scores on the test day)
  - top1_score

Motivation: 2026-07-28 the nightly retrain produced differentiated scores,
2026-07-29 it collapsed to a single constant (best_iteration=1) — picks
became code-order. This script quantifies how often that happens and which
parameter change fixes it. Run inside the api/worker container:

    python scripts/diagnose_training_stability.py --days 4 \
        --variants baseline,es200,valid6m,lr005_es200,linear
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date

sys.path.insert(0, "/app")

import pandas as pd  # noqa: E402

VARIANTS: dict[str, dict] = {
    # current production config
    "baseline": {"valid_months": 3, "model": {}},
    # give validation far more patience before declaring "no improvement"
    "es200": {"valid_months": 3, "model": {"early_stopping_rounds": 200}},
    # longer validation window — less day-to-day noise in the early-stop signal
    "valid6m": {"valid_months": 6, "model": {"early_stopping_rounds": 200}},
    # slower learning + patience
    "lr005_es200": {"valid_months": 3,
                     "model": {"early_stopping_rounds": 200, "learning_rate": 0.005}},
    # tree-free reference — immune to early-stop degeneration
    "linear": {"valid_months": 3, "linear": True},
}


def _windows(today: date, valid_months: int):
    """Same shape as live_trader._walk_forward_windows but with a
    parameterizable validation-window length."""
    t = pd.Timestamp(today)
    train_end = (t - pd.DateOffset(months=valid_months)).date()
    valid_start = (t - pd.DateOffset(months=valid_months) + pd.Timedelta(days=1)).date()
    from app.api.services.live_trader import _prev_trading_day
    valid_end = _prev_trading_day(today)
    return train_end, valid_start, valid_end


def build_handler(today: date):
    from qlib.utils import init_instance_by_config
    from qlib.data.dataset.handler import DataHandlerLP
    return init_instance_by_config({
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
        "kwargs": {"instruments": "kospi200", "start_time": "2023-04-01",
                    "end_time": today.isoformat()},
    }, accept_types=DataHandlerLP)


def run_variant(handler, today: date, name: str, cfg: dict) -> dict:
    from qlib.data.dataset import DatasetH
    train_end, valid_start, valid_end = _windows(today, cfg["valid_months"])
    dataset = DatasetH(handler=handler, segments={
        "train": ("2023-04-01", train_end.isoformat()),
        "valid": (valid_start.isoformat(), valid_end.isoformat()),
        "test": (valid_end.isoformat(), today.isoformat()),
    })
    t0 = time.time()
    if cfg.get("linear"):
        from qlib.contrib.model.linear import LinearModel
        model = LinearModel()
        best_iter = None
    else:
        from qlib.contrib.model.gbdt import LGBModel
        model = LGBModel(**cfg["model"])
    model.fit(dataset)
    if not cfg.get("linear"):
        best_iter = getattr(model.model, "best_iteration", None)
    pred = model.predict(dataset)
    fit_s = round(time.time() - t0, 1)

    last_dt = pred.index.get_level_values(0).max()
    snap = pred.loc[last_dt].sort_values(ascending=False)
    top10 = snap.head(10)
    return {
        "day": today.isoformat(),
        "variant": name,
        "best_iteration": best_iter,
        "top10_unique": int(len(set(round(float(v), 8) for v in top10))),
        "score_std": round(float(snap.std()), 6),
        "top1_score": round(float(top10.iloc[0]), 6),
        "n_scored": int(len(snap)),
        "fit_seconds": fit_s,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--variants", default="baseline,es200,valid6m,lr005_es200,linear")
    args = ap.parse_args()

    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()
    from qlib.data import D

    cal = [pd.Timestamp(c).date() for c in D.calendar()]
    days = cal[-args.days:]
    names = [v.strip() for v in args.variants.split(",") if v.strip() in VARIANTS]

    print(f"# diagnosing days={days[0]}..{days[-1]} variants={names}", flush=True)
    for today in days:
        handler = build_handler(today)  # one data load per day, shared by variants
        for name in names:
            try:
                res = run_variant(handler, today, name, VARIANTS[name])
            except Exception as exc:  # noqa: BLE001
                res = {"day": today.isoformat(), "variant": name, "error": str(exc)[:200]}
            print(json.dumps(res, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
