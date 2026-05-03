"""Live trading persistence (SQLite for dev, PostgreSQL on production).

The schema is intentionally narrow — only what the daily-rebalance loop and
the live UI need. Backtest results stay in Celery/Redis, not here.
"""

from .session import Base, SessionLocal, engine, init_db
from .models import Signal, Order, Fill, PositionSnapshot, DailyPnL

__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "init_db",
    "Signal",
    "Order",
    "Fill",
    "PositionSnapshot",
    "DailyPnL",
]
