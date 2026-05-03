"""Backtest submission and result retrieval endpoints."""

import itertools
import json
import uuid
from datetime import datetime, timezone

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException

from ..schemas.backtest import (
    BacktestJobResponse,
    BacktestListItem,
    BacktestListResponse,
    BacktestMetrics,
    BacktestRequest,
    BacktestResultResponse,
    ExtendedMetrics,
    GridBacktestRequest,
    GridJobResponse,
    GridMember,
    GridResultResponse,
    JobStatus,
    PortfolioPoint,
    RecommendedPick,
    TradeDay,
    TradeOrder,
)
from ..workers.celery_app import celery_app
from ..workers.tasks import run_backtest_task

router = APIRouter(prefix="/backtests", tags=["backtests"])

# In-memory job/group registries (swap to Redis/DB for production)
_job_registry: dict[str, dict] = {}
_group_registry: dict[str, dict] = {}


@router.post("/", response_model=BacktestJobResponse)
def submit_backtest(req: BacktestRequest):
    config = req.model_dump()
    result = run_backtest_task.delay(config)
    _job_registry[result.id] = {
        "instruments": req.instruments,
        "strategy_class": req.strategy_class,
        "model_class": req.model_class,
        "backtest_start": req.backtest_start,
        "backtest_end": req.backtest_end,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return BacktestJobResponse(job_id=result.id, status=JobStatus.PENDING)


@router.get("/{job_id}", response_model=BacktestResultResponse)
def get_backtest_result(job_id: str):
    result = AsyncResult(job_id, app=celery_app)

    status = _map_celery_status(result.status)

    if status == JobStatus.COMPLETED:
        data = result.result or {}
        error = data.get("error")
        if error:
            return BacktestResultResponse(job_id=job_id, status=JobStatus.FAILED, error=error)

        metrics = None
        if data.get("metrics"):
            metrics = BacktestMetrics(**data["metrics"])

        extended = None
        if data.get("extended_metrics"):
            extended = ExtendedMetrics(**data["extended_metrics"])

        portfolio = None
        if data.get("portfolio"):
            portfolio = [PortfolioPoint(**p) for p in data["portfolio"]]

        picks = None
        if data.get("recommended_picks"):
            picks = [RecommendedPick(**p) for p in data["recommended_picks"]]

        trades = None
        if data.get("recent_trades"):
            trades = [
                TradeDay(date=d["date"], orders=[TradeOrder(**o) for o in d["orders"]])
                for d in data["recent_trades"]
            ]

        return BacktestResultResponse(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            metrics=metrics,
            extended_metrics=extended,
            portfolio=portfolio,
            recommended_picks=picks,
            recent_trades=trades,
            benchmark_used=data.get("benchmark_used"),
        )

    if status == JobStatus.FAILED:
        error_msg = str(result.result) if result.result else "Unknown error"
        return BacktestResultResponse(job_id=job_id, status=JobStatus.FAILED, error=error_msg)

    return BacktestResultResponse(job_id=job_id, status=status)


@router.get("/", response_model=BacktestListResponse)
def list_backtests():
    jobs = []
    for job_id, meta in _job_registry.items():
        result = AsyncResult(job_id, app=celery_app)
        jobs.append(
            BacktestListItem(
                job_id=job_id,
                status=_map_celery_status(result.status),
                **meta,
            )
        )
    return BacktestListResponse(jobs=jobs, count=len(jobs))


def _map_celery_status(celery_status: str) -> JobStatus:
    mapping = {
        "PENDING": JobStatus.PENDING,
        "STARTED": JobStatus.RUNNING,
        "RUNNING": JobStatus.RUNNING,
        "SUCCESS": JobStatus.COMPLETED,
        "FAILURE": JobStatus.FAILED,
        "REVOKED": JobStatus.FAILED,
    }
    return mapping.get(celery_status, JobStatus.PENDING)


# ─── Grid search ────────────────────────────────────────────────────


def _set_dotted(target: dict, path: str, value) -> None:
    """In-place assignment via dotted path: 'strategy_kwargs.topk' -> target['strategy_kwargs']['topk']."""
    parts = path.split(".")
    cursor = target
    for p in parts[:-1]:
        if p not in cursor or not isinstance(cursor[p], dict):
            cursor[p] = {}
        cursor = cursor[p]
    cursor[parts[-1]] = value


@router.post("/grid", response_model=GridJobResponse)
def submit_grid(req: GridBacktestRequest):
    """Cartesian-product job submission: every (model × strategy × sweep) combo."""
    base_dict = req.base.model_dump()

    sweep_keys = list(req.param_sweeps.keys())
    sweep_values = [req.param_sweeps[k] for k in sweep_keys]
    sweep_combos = list(itertools.product(*sweep_values)) if sweep_values else [()]

    total = len(req.models) * len(req.strategies) * len(sweep_combos)
    if total == 0:
        raise HTTPException(400, "Grid produces zero jobs — supply at least one model and one strategy.")
    if total > req.max_jobs:
        raise HTTPException(
            400,
            f"Grid would queue {total} jobs (max_jobs={req.max_jobs}). "
            "Reduce sweeps or raise max_jobs explicitly.",
        )

    group_id = str(uuid.uuid4())
    job_ids: list[str] = []
    members: list[dict] = []

    for model in req.models:
        for strategy in req.strategies:
            for combo in sweep_combos:
                cfg = json.loads(json.dumps(base_dict))  # deep copy via json round-trip
                # Replace, don't merge — base config's strategy_kwargs (e.g. n_drop)
                # are not valid for every strategy class, so each grid combo must
                # carry only the kwargs declared in its catalog entry.
                cfg["model_class"] = model.class_
                cfg["model_module"] = model.module
                cfg["model_kwargs"] = dict(model.kwargs)
                cfg["strategy_class"] = strategy.class_
                cfg["strategy_module"] = strategy.module
                cfg["strategy_kwargs"] = dict(strategy.kwargs)
                for k, v in zip(sweep_keys, combo):
                    _set_dotted(cfg, k, v)

                result = run_backtest_task.delay(cfg)
                job_ids.append(result.id)

                summary = {
                    "model_class": cfg["model_class"],
                    "strategy_class": cfg["strategy_class"],
                    **{k: v for k, v in zip(sweep_keys, combo)},
                }
                members.append({"job_id": result.id, "summary": summary})
                _job_registry[result.id] = {
                    "instruments": cfg["instruments"],
                    "strategy_class": cfg["strategy_class"],
                    "model_class": cfg["model_class"],
                    "backtest_start": cfg["backtest_start"],
                    "backtest_end": cfg["backtest_end"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "group_id": group_id,
                }

    _group_registry[group_id] = {
        "members": members,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
    }
    return GridJobResponse(group_id=group_id, job_ids=job_ids, total=total)


@router.get("/grid/{group_id}", response_model=GridResultResponse)
def get_grid_result(group_id: str):
    group = _group_registry.get(group_id)
    if not group:
        raise HTTPException(404, f"grid group {group_id} not found")

    jobs: list[GridMember] = []
    completed = 0
    failed = 0
    for member in group["members"]:
        job_id = member["job_id"]
        result = AsyncResult(job_id, app=celery_app)
        status = _map_celery_status(result.status)

        metrics = None
        error = None
        if status == JobStatus.COMPLETED:
            data = result.result or {}
            if data.get("error"):
                status = JobStatus.FAILED
                error = data["error"]
                failed += 1
            else:
                if data.get("metrics"):
                    metrics = BacktestMetrics(**data["metrics"])
                completed += 1
        elif status == JobStatus.FAILED:
            error = str(result.result) if result.result else "Unknown error"
            failed += 1

        jobs.append(GridMember(
            job_id=job_id,
            status=status,
            config_summary=member["summary"],
            metrics=metrics,
            error=error,
        ))

    return GridResultResponse(
        group_id=group_id,
        total=group["total"],
        completed=completed,
        failed=failed,
        jobs=jobs,
    )
