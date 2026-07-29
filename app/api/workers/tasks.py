"""Celery tasks: backtests + live trading loop."""

from .celery_app import celery_app
from ..services.backtest_service import run_backtest


@celery_app.task(bind=True, name="run_backtest")
def run_backtest_task(self, config: dict) -> dict:
    """Execute a qlib backtest in an isolated worker process.

    The worker process has its own qlib.init() (via worker_process_init signal),
    so the global C singleton is safe from concurrent mutation.
    """
    self.update_state(state="RUNNING")
    return run_backtest(config)


# ─── Live trading (KIS) ─────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="live_signal",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=2,
)
def live_signal_task(self) -> dict:
    """Post-close — train/score → persist top-K Signal rows for tomorrow's open.

    Primary trigger: chained from refresh_kr_data (so it always trains on a
    calendar that includes today). The 16:20 beat entry is a fallback and is
    safe to double-fire — signal writes are idempotent per as_of.
    """
    from ..services.live_trader import generate_daily_signal
    self.update_state(state="RUNNING")
    return generate_daily_signal()


@celery_app.task(bind=True, name="live_orders")
def live_orders_task(self) -> dict:
    """09:00 KST — open strategy: read today's Signal, submit KIS orders."""
    from ..services.live_trader import submit_daily_orders
    self.update_state(state="RUNNING")
    return submit_daily_orders(strategy="open", simulated=False)


@celery_app.task(
    bind=True,
    name="live_sync",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def live_sync_task(self) -> dict:
    """09:30 + 15:40 KST — open strategy: pull KIS balance, snapshot, PnL.

    Balance reads are idempotent, so retrying on transient KIS errors is
    safe. live_orders deliberately has NO autoretry — order submission is
    not idempotent (a retry after a partial run would double-buy); its
    failure modes are handled inside the token layer instead.
    """
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="open")


@celery_app.task(bind=True, name="live_orders_close")
def live_orders_close_task(self) -> dict:
    """15:20 KST — close strategy: simulate fills priced at the kr_data close.

    Uses the same Signal(as_of=today) the open task consumed at 09:00, so the
    only variable is execution timing.
    """
    from ..services.live_trader import submit_daily_orders
    self.update_state(state="RUNNING")
    return submit_daily_orders(strategy="close", simulated=True)


@celery_app.task(bind=True, name="live_sync_close")
def live_sync_close_task(self) -> dict:
    """15:40 KST — close strategy: snapshot the simulated portfolio, PnL roll-up."""
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="close")


@celery_app.task(
    bind=True,
    name="refresh_kr_data",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def refresh_kr_data_task(self) -> dict:
    """15:45 KST — incrementally extend kr_data so tomorrow's live_signal sees today's bars.

    On a successful refresh that actually appended calendar days, chain the
    signal generation immediately — this guarantees live_signal always runs
    AFTER today's bars landed (the old independent 15:35 slot trained on a
    calendar ending yesterday, and its as_of drifted from the 09:00 reader).
    """
    from ..services.kr_data_refresh import refresh_kr_data
    self.update_state(state="RUNNING")
    result = refresh_kr_data()
    if result.get("status") == "ok" and result.get("new_calendar_days", 0) > 0:
        live_signal_task.delay()
    return result
