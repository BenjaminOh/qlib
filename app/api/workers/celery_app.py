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
    # open 익절 예약 — 보유 종목마다 평단 +10% 지정가로 절반을 미리 건다.
    # 09:20 정산 뒤여야 그날 매수의 실제 평단이 확정돼 사다리 가격이 맞는다.
    # 한국 주식 주문은 당일 유효라 매일 아침 다시 건다.
    "ladder-reserve-open": {
        "task": "ladder_reserve",
        "schedule": crontab(hour=9, minute=25, day_of_week="mon-fri"),
    },
    # 잔여분 트레일링 — 역지정가(스톱) 주문이 없으므로 직접 지켜본다.
    # 트레일선은 전일까지의 최고 종가로 계산되어 장중 내내 상수다.
    # 실제로 도는 창은 09:30~15:25 — 태스크 안에서 자른다(TRAIL_WATCH_FROM).
    # 아침 배치와 같은 appkey 게이트를 두고 경합하면 주문이 밀린다.
    "trail-watch-open": {
        "task": "trail_watch",
        "schedule": crontab(minute="*/5", hour="9-15", day_of_week="mon-fri"),
    },
    # Resting limit orders: cancel whatever is past its account cutoff. The
    # cutoff lives in trading_accounts (per account, per side), so this sweeps
    # instead of firing at one fixed time. Cheap on ticks with nothing due.
    "cancel-unfilled-orders": {
        "task": "cancel_unfilled_orders",
        "schedule": crontab(minute="*/10", hour="9-15", day_of_week="mon-fri"),
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
    # cafecool — cafe 와 같은 15:28 슬롯. 같은 후보 행을 읽고 ret20 상한만
    # 다르게 적용하므로 스캔·순위는 공유되고 진입 조건 하나만 갈린다.
    # cafereal — 같은 15:28 슬롯, 실계좌 주문. 계좌 미설정이면 즉시 반환한다.
    "live-orders-at-close-cafereal": {
        "task": "live_orders_cafereal",
        "schedule": crontab(hour=15, minute=28, day_of_week="mon-fri"),
    },
    "live-orders-at-close-cafecool": {
        "task": "live_orders_cafecool",
        "schedule": crontab(hour=15, minute=28, day_of_week="mon-fri"),
    },
    # cafereal 대사 — 15:28 주문의 체결 여부를 확정한다. 15:35 는 15:30 동시호가
    # 직후이고 15:46 의 live_sync(스냅샷) 보다 앞선다: 순서가 뒤바뀌면 그날
    # 스냅샷이 미확정 원장 위에서 찍힌다. 익일 09:05 는 재확인 — 동시호가 체결이
    # KIS 조회에 반영되는 시점이 확정적이지 않아 15:35 에 놓칠 수 있다.
    # 키 이름이 겹치면 뒤엣것이 앞을 덮어쓴다(2026-08-20 live-sync-cafe 사고).
    "reconcile-cafereal-after-close": {
        "task": "reconcile_fills_cafereal",
        "schedule": crontab(hour=15, minute=35, day_of_week="mon-fri"),
    },
    "reconcile-cafereal-next-morning": {
        "task": "reconcile_fills_cafereal",
        "schedule": crontab(hour=9, minute=5, day_of_week="mon-fri"),
        "kwargs": {"prev_day": True},
    },
    # Book depth for the 15:28 buy — research only, no trades. 15:07 is
    # 정규장 (real ask ladder); 15:27 is inside 동시호가 one minute before the
    # order, where 예상체결수량 replaces the ladder. Both are needed: they
    # answer "could it fill 장중" and "could it fill at the close" separately.
    "cafe-orderbook-1505": {
        "task": "capture_orderbook",
        "schedule": crontab(hour=15, minute=7, day_of_week="mon-fri"),
        "args": ("1505",),
    },
    "cafe-orderbook-1528": {
        "task": "capture_orderbook",
        "schedule": crontab(hour=15, minute=27, day_of_week="mon-fri"),
        "args": ("1528",),
    },
    # cafeopen twin — cafe's picks entered the only way anyone provably could:
    # a −3% limit off the next morning's open, cancelled unfilled at 10:00.
    # 09:00 shares the slot with live_orders (real KIS); the twin is DB-only
    # so there is no account contention, and both need the opening print.
    "live-orders-cafeopen": {
        "task": "live_orders_cafeopen",
        "schedule": crontab(hour=9, minute=0, day_of_week="mon-fri"),
    },
    "resolve-cafeopen": {
        "task": "resolve_cafeopen",
        "schedule": crontab(hour=10, minute=0, day_of_week="mon-fri"),
    },
    "live-sync-cafeopen-after-close": {
        "task": "live_sync_cafeopen",
        "schedule": crontab(hour=15, minute=48, day_of_week="mon-fri"),
    },
    # surge strategy: surge-eve profile TOP10 (reads the pool snapshots the
    # 15:05 screen just stored — no extra KIS calls), sim-buys the top 2.
    "surge-screen": {
        "task": "surge_screen",
        "schedule": crontab(hour=15, minute=12, day_of_week="mon-fri"),
    },
    "live-orders-at-close-surge": {
        "task": "live_orders_surge",
        "schedule": crontab(hour=15, minute=29, day_of_week="mon-fri"),
    },
    "live-sync-surge-after-close": {
        "task": "live_sync_surge",
        "schedule": crontab(hour=15, minute=47, day_of_week="mon-fri"),
    },
    "live-sync-cafe-after-close": {
        "task": "live_sync_cafe",
        "schedule": crontab(hour=15, minute=46, day_of_week="mon-fri"),
    },
    "live-sync-cafereal-after-close": {
        "task": "live_sync_cafereal",
        "schedule": crontab(hour=15, minute=46, day_of_week="mon-fri"),
    },
    "live-sync-cafecool-after-close": {
        "task": "live_sync_cafecool",
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
