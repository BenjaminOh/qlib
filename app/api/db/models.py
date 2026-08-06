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


# Three parallel virtual portfolios share the live DB:
#   - 'open'  → KIS-real orders fired at 09:00 (existing behavior)
#   - 'close' → simulated fills at 15:20 call-auction close, DB-only
#   - 'flow'  → same execution as 'close', but the picks are re-ranked by
#               기관/외국인 net buying (market_flow overlay). Identical
#               execution is deliberate: the equity-curve gap vs 'close' IS
#               the overlay's effect, with no other variable in between.
# Tag every Order/Fill/Snapshot/DailyPnL with this string so per-strategy
# PnL can be compared without two databases.
STRATEGY_OPEN = "open"
STRATEGY_CLOSE = "close"
STRATEGY_FLOW = "flow"
STRATEGY_TRAIL = "trail"
STRATEGY_SCALE = "scale"
STRATEGY_LIMIT = "limit"
STRATEGY_CAFE = "cafe"


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
    # Why this pick — JSON: {"summary": str, "metrics": {...},
    # "top_features": [{"name","desc","contrib"}]}. Populated best-effort by
    # signal_reasons.build_reasons; display-only, never required by trading.
    reasons_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_signals_asof_rank", "as_of", "rank"),)


class MarketFlow(Base):
    """Per-(day, stock) investor net-buy row from KIS TR FHKST01010900.

    Populated for the day's top-30 signal candidates only — one KIS call per
    code returns ~30 days of history, so a single fetch fills the whole
    lookback window and the table accumulates naturally over time.

    Quantities are in SHARES and are what the flow score uses: the amount
    fields come straight from KIS whose unit is not documented in the example
    spec, so scoring normalises qty against qlib's own $volume instead. The
    amounts are stored raw for later analysis, not for arithmetic today.
    """
    __tablename__ = "market_flow"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    code = Column(String(8), nullable=False, index=True)
    frgn_net_qty = Column(Float, nullable=True)   # 외국인(+기타외국인) 순매수 수량
    orgn_net_qty = Column(Float, nullable=True)   # 기관계 순매수 수량
    prsn_net_qty = Column(Float, nullable=True)   # 개인 순매수 수량
    frgn_net_amt = Column(Float, nullable=True)   # raw KIS 거래대금 (단위 미확정)
    orgn_net_amt = Column(Float, nullable=True)
    prsn_net_amt = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_date", "code", name="uq_market_flow_date_code"),
    )


class CafeCandidate(Base):
    """Daily output of the cafe-mimic market screener (15:05).

    One row per (day, code) candidate that matched one of the four
    reverse-engineered recommender patterns. stop_px is the STRUCTURAL stop
    computed at screening time (prev high / breakout-day low / pullback low /
    rebound low depending on the pattern) — the bracket engine reads it from
    the entry order's reasons_json after the 15:28 sim buy copies it there.
    """
    __tablename__ = "cafe_candidates"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    code = Column(String(8), nullable=False, index=True)
    name = Column(String(64), nullable=True)
    pattern = Column(String(2), nullable=False)  # B/A/C/D (priority order)
    rank = Column(Integer, nullable=False)       # selection order within the day
    close = Column(Float, nullable=True)         # price at screening time
    stop_px = Column(Float, nullable=False)
    metrics_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_date", "code", name="uq_cafe_candidate_date_code"),
    )


class CafeScout(Base):
    """Observation-only screener scans (14:30 / 15:00) — no trades.

    Measures whether 종가 베팅 candidates are identifiable EARLIER in the
    session (user hypothesis 2026-08-06): compare pick overlap vs the 15:05
    scan and the price drift from scan time into the close. After 1-2 weeks
    the distribution decides whether cafe entries move earlier and whether an
    "early entry → sell into close" day-trade variant is worth a 9th curve.
    """
    __tablename__ = "cafe_scouts"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    slot = Column(String(5), nullable=False)     # "1430" / "1500"
    code = Column(String(8), nullable=False)
    name = Column(String(64), nullable=True)
    pattern = Column(String(2), nullable=False)
    rank = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)         # price at scan time
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_date", "slot", "code", name="uq_cafe_scout_date_slot_code"),
    )


class Order(Base):
    """One outbound order attempt (real KIS for 'open', simulated otherwise)."""
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
    # Decision basis CAPTURED AT ORDER TIME — {"action","basis","summary",
    # "metrics","top_features"}. Sells can't be reconstructed later (the sell
    # trigger is the stock VANISHING from that day's signal), so this snapshot
    # is the only record of why. Display-only.
    reasons_json = Column(Text, nullable=True)
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
