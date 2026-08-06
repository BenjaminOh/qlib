"""Celery application configuration for background backtest jobs."""

from celery import Celery
from celery.signals import worker_process_init

from ..config import settings

celery_app = Celery(
    "qlib_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.api.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_max_tasks_per_child=5,  # recycle workers to free qlib memory
    timezone="Asia/Seoul",  # crontab below uses KST
    enable_utc=False,
    # Concurrency is set on the worker CLI (--concurrency=N). Using prefork
    # gives each child its own qlib config singleton, so multiple backtests
    # can run in parallel without mutating shared state.
)


# ─── Live trading beat schedule (KST) ──────────────────────────────
# Skip Sat/Sun via day_of_week='mon-fri'. Holiday handling is done inside
# each task by checking the qlib calendar — if today isn't a trading day
# the task no-ops cheaply.
from celery.schedules import crontab  # noqa: E402

celery_app.conf.beat_schedule = {
    # FALLBACK slot only — the primary trigger is the chain at the end of
    # refresh_kr_data (15:45), so signal training always sees today's bars.
    # The old independent 15:35 slot ran BEFORE the refresh and trained on a
    # calendar ending yesterday (as_of drift → chronic no_signal at 09:00).
    # Idempotent per as_of, so double-firing with the chain is harmless.
    "live-signal-fallback": {
        "task": "live_signal",
        "schedule": crontab(hour=16, minute=20, day_of_week="mon-fri"),
    },
    # Open strategy: KIS real orders at the opening auction.
    "live-orders-at-open": {
        "task": "live_orders",
        "schedule": crontab(hour=9, minute=0, day_of_week="mon-fri"),
    },
    # Pin actual fill prices onto the 09:00 orders — market orders submit
    # with price=None and realized pnl would otherwise be re-estimated from
    # bars (and drift) all day.
    "reconcile-fills-morning": {
        "task": "reconcile_fills",
        "schedule": crontab(hour=9, minute=20, day_of_week="mon-fri"),
    },
    "live-sync-mid-morning": {
        "task": "live_sync",
        "schedule": crontab(hour=9, minute=30, day_of_week="mon-fri"),
    },
    "live-sync-after-close": {
        "task": "live_sync",
        "schedule": crontab(hour=15, minute=40, day_of_week="mon-fri"),
    },
    # Close strategy: simulated DB fills at the call-auction window.
    # Buys follow the same Signal(as_of=today) the open strategy used at
    # 09:00; exits are ±N% brackets (close_bracket_exits), not rank dropout.
    "live-orders-at-close": {
        "task": "live_orders_close",
        "schedule": crontab(hour=15, minute=20, day_of_week="mon-fri"),
    },
    # FALLBACK slot — primary trigger is the refresh_kr_data chain (needs
    # the day's own $high/$low bar). Idempotent, double-firing harmless.
    "close-bracket-exits-fallback": {
        "task": "close_bracket_exits",
        "schedule": crontab(hour=16, minute=25, day_of_week="mon-fri"),
    },
    "live-sync-close-after-close": {
        "task": "live_sync_close",
        "schedule": crontab(hour=15, minute=40, day_of_week="mon-fri"),
    },
    # Flow strategy: identical execution to close (sim fills at the same
    # last close, ±5% brackets) — the ONLY difference is that picks come from
    # the top-30 signal re-ranked by 기관/외국인 net buying.
    # Offset by a minute from the close slots on purpose: prod runs SQLite
    # with --concurrency=2, and stacking a third writer on 15:20/15:40 is how
    # "database is locked" starts. Both strategies price off the same stored
    # close, so the minute of offset changes nothing about the comparison.
    "live-orders-at-close-flow": {
        "task": "live_orders_flow",
        "schedule": crontab(hour=15, minute=22, day_of_week="mon-fri"),
    },
    "live-sync-flow-after-close": {
        "task": "live_sync_flow",
        "schedule": crontab(hour=15, minute=42, day_of_week="mon-fri"),
    },
    # Exit-rule matrix sims (trail/scale): same close-priced buys as `close`,
    # exits differ via the shared bracket loop. Minute offsets continue the
    # SQLite-writer stagger. limit strategy has NO 15:2x slot — its −3%
    # resting-limit fills are judged inside the refresh chain (needs the
    # day's own bar; see close_bracket_exits_task).
    "live-orders-at-close-trail": {
        "task": "live_orders_trail",
        "schedule": crontab(hour=15, minute=24, day_of_week="mon-fri"),
    },
    "live-sync-trail-after-close": {
        "task": "live_sync_trail",
        "schedule": crontab(hour=15, minute=43, day_of_week="mon-fri"),
    },
    "live-orders-at-close-scale": {
        "task": "live_orders_scale",
        "schedule": crontab(hour=15, minute=26, day_of_week="mon-fri"),
    },
    "live-sync-scale-after-close": {
        "task": "live_sync_scale",
        "schedule": crontab(hour=15, minute=44, day_of_week="mon-fri"),
    },
    # cafe strategy: recommender-mimic screener. Screen the whole market at
    # 15:05 (KIS ranking TRs), sim-buy the top candidates at 15:28 near the
    # closing auction. Sync offset continues the SQLite writer stagger.
    # Observation-only scout scans — measure early-entry feasibility
    # (pick overlap + price drift into the close). No trades.
    "cafe-scout-1430": {
        "task": "cafe_scout",
        "schedule": crontab(hour=14, minute=30, day_of_week="mon-fri"),
        "args": ("1430",),
    },
    "cafe-scout-1500": {
        "task": "cafe_scout",
        "schedule": crontab(hour=15, minute=0, day_of_week="mon-fri"),
        "args": ("1500",),
    },
    "cafe-screen": {
        "task": "cafe_screen",
        "schedule": crontab(hour=15, minute=5, day_of_week="mon-fri"),
    },
    "live-orders-at-close-cafe": {
        "task": "live_orders_cafe",
        "schedule": crontab(hour=15, minute=28, day_of_week="mon-fri"),
    },
    "live-sync-cafe-after-close": {
        "task": "live_sync_cafe",
        "schedule": crontab(hour=15, minute=46, day_of_week="mon-fri"),
    },
    # FALLBACK slot — primary trigger is the live_signal chain (it knows the
    # fresh top-30). KIS publishes the day's investor row only after the
    # close, so 18:10 is safely past it. Idempotent per (day, code).
    "market-flow-fetch-fallback": {
        "task": "fetch_market_flow",
        "schedule": crontab(hour=18, minute=10, day_of_week="mon-fri"),
    },
    # Incrementally extend qlib kr_data after the close-day sync wraps, then
    # chain live_signal (see refresh_kr_data_task) so the model sees today's
    # bars. dump_update mode preserves prior history.
    "kr-data-daily-refresh": {
        "task": "refresh_kr_data",
        "schedule": crontab(hour=15, minute=45, day_of_week="mon-fri"),
    },
}


_STRATEGY_CLASSES = [
    ("qlib.contrib.strategy.signal_strategy", "TopkDropoutStrategy"),
    ("qlib.contrib.strategy.signal_strategy", "EnhancedIndexingStrategy"),
    ("qlib.contrib.strategy.cost_control", "SoftTopkStrategy"),
    ("qlib.contrib.strategy.rule_strategy", "TWAPStrategy"),
    ("qlib.contrib.strategy.rule_strategy", "SBBStrategyEMA"),
]
_MODEL_CLASSES = [
    ("qlib.contrib.model.gbdt", "LGBModel"),
    ("qlib.contrib.model.xgboost", "XGBModel"),
    ("qlib.contrib.model.catboost_model", "CatBoostModel"),
    ("qlib.contrib.model.linear", "LinearModel"),
]


@worker_process_init.connect
def init_qlib_in_worker(**kwargs):
    """Initialize qlib and verify catalog classes resolve at boot time."""
    from ..core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()

    # Ensure the live-trading SQLite/Postgres tables exist before tasks run.
    try:
        from ..db import init_db
        init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] live DB init failed: {exc}")

    import importlib
    for module_path, cls in _STRATEGY_CLASSES + _MODEL_CLASSES:
        try:
            module = importlib.import_module(module_path)
            getattr(module, cls)
        except Exception as exc:  # noqa: BLE001 - we want every miss surfaced
            print(f"[worker] catalog import failed: {module_path}.{cls}: {exc}")
