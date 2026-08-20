"""Live trading persistence (SQLite for dev, PostgreSQL on production).

The schema is intentionally narrow — only what the daily-rebalance loop and
the live UI need. Backtest results stay in Celery/Redis, not here.
"""

from .session import Base, SessionLocal, engine, init_db
from .models import (
    DailyPnL, Fill, MarketFlow, CafeCandidate, CafeScout, MarketPoolSnapshot, SurgePick,
    Order, OrderbookSnapshot, PositionSnapshot, Signal, TradingAccount, User,
    DEFAULT_ACCOUNT_ID, ACCOUNT_ID_LEN,
    ORD_TYPE_MARKET, ORD_TYPE_LIMIT, ORD_TYPES,
    BASE_PREV_CLOSE, BASE_OPEN, BASE_QUOTE, PRICE_BASES,
    STRATEGY_OPEN, STRATEGY_CLOSE, STRATEGY_FLOW,
    STRATEGY_TRAIL, STRATEGY_SCALE, STRATEGY_LIMIT, STRATEGY_CAFE, STRATEGY_SURGE,
    STRATEGY_CAFEOPEN, STRATEGY_CAFECOOL,
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
    "CafeScout",
    "MarketPoolSnapshot",
    "SurgePick",
    "OrderbookSnapshot",
    "PositionSnapshot",
    "DailyPnL",
    "User",
    "TradingAccount",
    "DEFAULT_ACCOUNT_ID",
    "ACCOUNT_ID_LEN",
    "ORD_TYPE_MARKET",
    "ORD_TYPE_LIMIT",
    "ORD_TYPES",
    "BASE_PREV_CLOSE",
    "BASE_OPEN",
    "BASE_QUOTE",
    "PRICE_BASES",
    "STRATEGY_OPEN",
    "STRATEGY_CLOSE",
    "STRATEGY_FLOW",
    "STRATEGY_TRAIL",
    "STRATEGY_SCALE",
    "STRATEGY_LIMIT",
    "STRATEGY_CAFE",
    "STRATEGY_SURGE",
    "STRATEGY_CAFEOPEN",
    "STRATEGY_CAFECOOL",
]
