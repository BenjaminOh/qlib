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


@celery_app.task(bind=True, name="live_signal")
def live_signal_task(self) -> dict:
    """15:35 KST — train/score → persist top-K Signal rows for tomorrow's open."""
    from ..services.live_trader import generate_daily_signal
    self.update_state(state="RUNNING")
    return generate_daily_signal()


@celery_app.task(bind=True, name="live_orders")
def live_orders_task(self) -> dict:
    """09:00 KST — open strategy: read today's Signal, submit KIS orders."""
    from ..services.live_trader import submit_daily_orders
    self.update_state(state="RUNNING")
    return submit_daily_orders(strategy="open", simulated=False)


@celery_app.task(bind=True, name="live_sync")
def live_sync_task(self) -> dict:
    """09:30 + 15:40 KST — open strategy: pull KIS balance, snapshot, PnL."""
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
