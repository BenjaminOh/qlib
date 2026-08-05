"""Deep root-cause diagnosis for signal-score collapse (2026-08-03~).

diagnose_training_stability.py tells us THAT the nightly retrain collapses
(top-10 scores identical); this script tells us WHY, by testing four
hypotheses per training day:

  H1 early-stop starvation — valid loss never improves → best_iteration tiny
     → few trees → piecewise-constant scores.       [model / evals sections]
  H2 label degeneration    — recent labels NaN/flat → nothing to learn.
                                                     [labels section]
  H3 feature freeze/dupes  — test-day features stale, NaN-heavy or literally
     duplicated across stocks → same leaf path.      [features section]
  H4 market regime         — cross-sectional label variance genuinely low
     (index-driven tape) → only 1-2 informative splits exist.
                                                     [labels.daily_std series]

Runs the PRODUCTION variant (lr=0.005, early_stopping=200) plus one remedy
candidate (valid6m) per day. Pipe into the worker container (no argv needed):

    ssh rocky-prod "docker exec -i qlib_worker_blue python -" \
        < scripts/diagnose_collapse.py

Read-only: trains in memory, touches no DB. Run outside the 09:00-09:40 and
15:00-16:30 cron windows.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date

sys.path.insert(0, "/app")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

VARIANTS = {
    "prod_lr005_es200": {"valid_months": 3,
                         "model": {"early_stopping_rounds": 200, "learning_rate": 0.005}},
    "remedy_valid6m": {"valid_months": 6,
                       "model": {"early_stopping_rounds": 200, "learning_rate": 0.005}},
    # Remedy: fixed 150 rounds with NO early-stopping callback at all.
    # (A huge patience is NOT enough: lightgbm's callback force-records
    # best_iteration at the final round and predict/dump then use only that
    # many trees — verified 2026-08-05, scores came out identical to prod.)
    # lgb.train is called directly so the callback never exists.
    "remedy_fixed150": {"valid_months": 3, "no_es_rounds": 150,
                        "model": {"learning_rate": 0.005}},
}
N_DAYS = 4          # last N calendar days (control 07-30/31 + degenerate 08-03/04)
DAILY_STD_DAYS = 10  # H4: per-day cross-sectional label std lookback


def _windows(today: date, valid_months: int):
    t = pd.Timestamp(today)
    train_end = (t - pd.DateOffset(months=valid_months)).date()
    valid_start = (t - pd.DateOffset(months=valid_months) + pd.Timedelta(days=1)).date()
    from app.api.services.live_trader import _prev_trading_day
    return train_end, valid_start, _prev_trading_day(today)


def build_handler(today: date):
    from qlib.utils import init_instance_by_config
    from qlib.data.dataset.handler import DataHandlerLP
    return init_instance_by_config({
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
        "kwargs": {"instruments": "kospi200", "start_time": "2023-04-01",
                   "end_time": today.isoformat()},
    }, accept_types=DataHandlerLP)


def _tree_stats(booster) -> dict:
    """Per-tree structure: how many trees actually split, on which features."""
    try:
        tdf = booster.trees_to_dataframe()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}
    splits = tdf[tdf["split_feature"].notna()]
    per_tree = splits.groupby("tree_index").size()
    feat_counts = splits["split_feature"].value_counts().head(5)
    return {
        "num_trees": int(tdf["tree_index"].nunique()),
        "trees_with_splits": int((per_tree > 0).sum()),
        "total_splits": int(len(splits)),
        "top_split_features": {str(k): int(v) for k, v in feat_counts.items()},
    }


def _label_stats(dataset, today: date) -> dict:
    """H2/H4: label health on what training/validation actually consumes."""
    from qlib.data.dataset.handler import DataHandlerLP
    out = {}
    for seg in ("train", "valid"):
        try:
            lab = dataset.prepare(seg, col_set="label",
                                  data_key=DataHandlerLP.DK_L).iloc[:, 0]
            out[seg] = {"rows": int(len(lab)),
                        "nan_pct": round(float(lab.isna().mean()) * 100, 2),
                        "std": round(float(lab.std()), 6)}
        except Exception as exc:  # noqa: BLE001
            out[seg] = {"error": str(exc)[:120]}
    # per-day cross-sectional std + NaN%, last DAILY_STD_DAYS days (infer key:
    # keeps NaN so the "labels vanish near the calendar tail" effect is visible)
    try:
        raw = dataset.prepare("valid", col_set="label",
                              data_key=DataHandlerLP.DK_I).iloc[:, 0]
        by_day = raw.groupby(raw.index.get_level_values(0))
        tail = list(by_day)[-DAILY_STD_DAYS:]
        out["daily"] = [
            {"day": str(pd.Timestamp(d).date()),
             "n": int(len(s)),
             "nan_pct": round(float(s.isna().mean()) * 100, 1),
             "xs_std": round(float(s.std()), 6),
             "uniq": int(s.dropna().round(8).nunique())}
            for d, s in tail
        ]
    except Exception as exc:  # noqa: BLE001
        out["daily"] = {"error": str(exc)[:120]}
    return out


def _feature_stats(dataset, today: date) -> dict:
    """H3: test-day (= scoring-day) feature health."""
    from qlib.data.dataset.handler import DataHandlerLP
    try:
        feats = dataset.prepare("test", col_set="feature",
                                data_key=DataHandlerLP.DK_I)
        last_dt = feats.index.get_level_values(0).max()
        snap = feats.loc[last_dt]
        zero_var = int((snap.std() == 0).sum())
        dupes = int(snap.fillna(-9e18).duplicated().sum())
        return {
            "day": str(pd.Timestamp(last_dt).date()),
            "n_stocks": int(len(snap)),
            "n_features": int(snap.shape[1]),
            "nan_pct": round(float(snap.isna().to_numpy().mean()) * 100, 2),
            "zero_variance_features": zero_var,
            "duplicate_feature_rows": dupes,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}


def run_variant(handler, today: date, name: str, cfg: dict) -> dict:
    from qlib.data.dataset import DatasetH
    from qlib.contrib.model.gbdt import LGBModel

    train_end, valid_start, valid_end = _windows(today, cfg["valid_months"])
    dataset = DatasetH(handler=handler, segments={
        "train": ("2023-04-01", train_end.isoformat()),
        "valid": (valid_start.isoformat(), valid_end.isoformat()),
        "test": (valid_end.isoformat(), today.isoformat()),
    })

    t0 = time.time()
    model = LGBModel(**cfg["model"])
    evals: dict = {}
    if cfg.get("no_es_rounds"):
        # bypass LGBModel.fit — it hard-wires an early_stopping callback
        import lightgbm as lgb
        ds_l = model._prepare_data(dataset, None)
        ds, names = list(zip(*ds_l))
        model.model = lgb.train(
            model.params, ds[0],
            num_boost_round=cfg["no_es_rounds"],
            valid_sets=list(ds), valid_names=list(names),
            callbacks=[lgb.log_evaluation(period=50),
                       lgb.record_evaluation(evals)])
    else:
        try:
            model.fit(dataset, evals_result=evals)
        except TypeError:  # older qlib fit() without evals_result kwarg
            model.fit(dataset)
    fit_s = round(time.time() - t0, 1)

    booster = model.model
    res: dict = {
        "day": today.isoformat(),
        "variant": name,
        "fit_seconds": fit_s,
        "best_iteration": getattr(booster, "best_iteration", None),
        "actual_trees": int(booster.num_trees()),
    }

    # H1: valid-loss trajectory — did boosting ever find improvement?
    curve = {}
    for split, metrics in (evals or {}).items():
        for metric, vals in metrics.items():
            if vals:
                curve[f"{split}.{metric}"] = {
                    "first": round(float(vals[0]), 8),
                    "min": round(float(min(vals)), 8),
                    "min_at": int(int(np.argmin(vals)) + 1),
                    "last": round(float(vals[-1]), 8),
                    "n_rounds": len(vals),
                }
    res["loss_curve"] = curve or None
    res["trees"] = _tree_stats(booster)

    # score collapse quantified on the scoring day (full cross-section)
    pred = model.predict(dataset)
    last_dt = pred.index.get_level_values(0).max()
    snap = pred.loc[last_dt]
    counts = snap.round(10).value_counts()
    res["scores"] = {
        "day": str(pd.Timestamp(last_dt).date()),
        "n_scored": int(len(snap)),
        "n_unique": int(len(counts)),
        "mode_share_pct": round(float(counts.iloc[0] / len(snap)) * 100, 1),
        "top10_unique": int(snap.sort_values(ascending=False).head(10)
                            .round(8).nunique()),
        "std": round(float(snap.std()), 8),
    }

    # labels/features are variant-independent — compute once (prod variant)
    if name.startswith("prod"):
        res["labels"] = _label_stats(dataset, today)
        res["features"] = _feature_stats(dataset, today)
    return res


def main() -> None:
    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()
    from qlib.data import D

    cal = [pd.Timestamp(c).date() for c in D.calendar()]
    days = cal[-N_DAYS:]
    print(f"# collapse diagnosis days={days[0]}..{days[-1]} "
          f"variants={list(VARIANTS)}", flush=True)
    for today in days:
        handler = build_handler(today)
        for name, cfg in VARIANTS.items():
            try:
                res = run_variant(handler, today, name, cfg)
            except Exception as exc:  # noqa: BLE001
                res = {"day": today.isoformat(), "variant": name,
                       "error": str(exc)[:200]}
            print(json.dumps(res, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
