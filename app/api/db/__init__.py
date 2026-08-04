"""Live trading persistence (SQLite for dev, PostgreSQL on production).

The schema is intentionally narrow — only what the daily-rebalance loop and
the live UI need. Backtest results stay in Celery/Redis, not here.
"""

from .session import Base, SessionLocal, engine, init_db
from .models import (
    DailyPnL, Fill, MarketFlow, CafeCandidate, Order, PositionSnapshot, Signal, User,
    STRATEGY_OPEN, STRATEGY_CLOSE, STRATEGY_FLOW,
    STRATEGY_TRAIL, STRATEGY_SCALE, STRATEGY_LIMIT, STRATEGY_CAFE,
)

__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "init_db",
    "Signal",
    "Order",
    "Fill",
    "MarketFlow",
    "CafeCandidate",
    "PositionSnapshot",
    "DailyPnL",
    "User",
    "STRATEGY_OPEN",
    "STRATEGY_CLOSE",
    "STRATEGY_FLOW",
    "STRATEGY_TRAIL",
    "STRATEGY_SCALE",
    "STRATEGY_LIMIT",
    "STRATEGY_CAFE",
]
