"""ORM models for live trading.

The five tables form a daily loop:
  Signal (15:35) → Order (09:00) → Fill (09:00~) → PositionSnapshot (15:35)
                                                  → DailyPnL (15:35)
"""

from datetime import datetime

from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .session import Base


# Two parallel virtual portfolios share the live DB:
#   - 'open'  → KIS-real orders fired at 09:00 (existing behavior)
#   - 'close' → simulated fills at 15:20 call-auction close, DB-only
# Tag every Order/Fill/Snapshot/DailyPnL with this string so per-strategy
# PnL can be compared without two databases.
STRATEGY_OPEN = "open"
STRATEGY_CLOSE = "close"


class User(Base):
    """Admin login account. Single-user model — multi-user / RBAC is out of scope."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    password_hash = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Signal(Base):
    """Top-K model output captured every trading day after 15:30 close."""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True)
    as_of = Column(Date, nullable=False, index=True)  # KRX trading date the signal is *for*
    rank = Column(Integer, nullable=False)
    code = Column(String(8), nullable=False)
    name = Column(String(120), nullable=True)
    score = Column(Float, nullable=True)
    model_class = Column(String(64), nullable=False)
    strategy_class = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_signals_asof_rank", "as_of", "rank"),)


class Order(Base):
    """One outbound order attempt (real KIS for 'open', simulated for 'close')."""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    strategy = Column(String(8), nullable=False, default=STRATEGY_OPEN, index=True)
    code = Column(String(8), nullable=False)
    name = Column(String(120), nullable=True)
    side = Column(String(4), nullable=False)  # BUY / SELL
    qty = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)  # null = market order
    ord_dvsn = Column(String(2), nullable=False, default="01")  # 00 지정가 / 01 시장가
    kis_order_id = Column(String(40), nullable=True, index=True)
    # SIMULATED is used by the 'close' strategy — no KIS round-trip, fill is
    # written immediately from the kr_data last close.
    status = Column(String(16), nullable=False, default="SUBMITTED")  # SUBMITTED/REJECTED/FILLED/PARTIAL/CANCELLED/SIMULATED
    error = Column(Text, nullable=True)
    raw_response = Column(Text, nullable=True)

    fills = relationship("Fill", back_populates="order", cascade="all, delete-orphan")


class Fill(Base):
    """Realised trade — populated from KIS order status polling, webhook, or
    (for close strategy) synthesised at order time from the kr_data close."""
    __tablename__ = "fills"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    strategy = Column(String(8), nullable=False, default=STRATEGY_OPEN, index=True)
    filled_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    qty = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    fee = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)  # realised PnL on the matched lot (sells only)

    order = relationship("Order", back_populates="fills")


class PositionSnapshot(Base):
    """End-of-day account snapshot for the equity curve and audit trail.
    One row per (snapshot_date, strategy) — open vs close are tracked separately."""
    __tablename__ = "position_snapshots"

    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    strategy = Column(String(8), nullable=False, default=STRATEGY_OPEN, index=True)
    cash = Column(Float, nullable=False)
    total_eval = Column(Float, nullable=False)
    holdings_json = Column(Text, nullable=False)  # JSON: [{code,name,qty,avg,eval,pnl,pnl_pct}, ...]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_date", "strategy", name="uq_snapshot_date_strategy"),
    )


class DailyPnL(Base):
    """Per-(trading-day, strategy) realised + unrealised PnL roll-up."""
    __tablename__ = "daily_pnl"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    strategy = Column(String(8), nullable=False, default=STRATEGY_OPEN, index=True)
    starting_equity = Column(Float, nullable=False)
    ending_equity = Column(Float, nullable=False)
    realised_pnl = Column(Float, nullable=False, default=0.0)
    unrealised_pnl = Column(Float, nullable=False, default=0.0)
    fees = Column(Float, nullable=False, default=0.0)
    benchmark_close = Column(Float, nullable=True)  # KODEX 200 close, for relative chart
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("trade_date", "strategy", name="uq_daily_pnl_date_strategy"),
    )
