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
STRATEGY_SURGE = "surge"
# cafeopen — cafe's execution twin (2026-08-14). Same picks, same exits; the
# ONLY difference is the entry: cafe sim-fills at the 15:28 quote (which for a
# 상한가 close assumes a fill nobody can prove was reachable), cafeopen rests a
# −3% limit off the NEXT morning's open and cancels it unfilled at 10:00.
# The gap between the two curves IS the entry-fill assumption's contribution.
STRATEGY_CAFEOPEN = "cafeopen"  # exactly 8 chars — the Order.strategy limit
# cafecool — cafe's ENTRY-CONDITION twin (2026-08-20). Identical picks, sizing
# and exits; the ONLY difference is an upper bound on ret20. cafe's A pattern
# requires ret20 >= +30% with no ceiling, so all 20 candidates to date entered
# already-overheated names (평균 ret20 +68%, 최고 본느 +368%) and the forward
# returns were negative across the board (D+1 −2.18%, D+5 −3.31%, 65%가 5일 내
# −10% 이상 낙폭). Splitting at 50% separated them: D+5 중앙값 +3.19% (30~50%)
# vs −9.50% (50%+). The gap between the two curves IS the ceiling's contribution.
# ⚠ 표본 4건 대 10건이고 평균 우위는 혜인(+64%) 한 건에 의존한다 — 전향적으로 잰다.
STRATEGY_CAFECOOL = "cafecool"  # exactly 8 chars — the Order.strategy limit
# cafereal — cafe 를 **실계좌**로 돌리는 전략 (2026-08-20). 픽·사이징·청산 규칙이
# cafe 와 완전히 같고 다른 것은 단 하나, 주문이 시뮬이 아니라 KIS 실주문이라는 것.
# 그래서 cafe(시뮬) 곡선과의 차이가 곧 **체결 가정이 부풀린 정확한 크기**다 —
# 호가 스냅샷 8건이 전부 상한가·매도잔량 0이었던 그 질문의 직접적인 답이 된다.
# 별도 계좌·별도 appkey 를 쓴다(KIS 한도는 appkey 단위).
STRATEGY_CAFEREAL = "cafereal"  # exactly 8 chars — the Order.strategy limit


# ─── Account axis ───────────────────────────────────────────────────
#
# One brokerage account per row. Until 10-account operation lands there is
# exactly one — "main" — and every existing code path uses it implicitly.
# The id is kept short and separate from `strategy` on purpose: strategy is
# String(8) and 'cafeopen' already uses all eight characters, so a composite
# "strategy:account" tag has nowhere to live.
DEFAULT_ACCOUNT_ID = "main"
ACCOUNT_ID_LEN = 16
CAFE_ACCOUNT_ID = "cafe"

# 계좌 ↔ 그 계좌에 REAL 주문을 내는 전략들. 단일 진실원.
#
# 순서에 의미가 있다 — [0] 이 그 계좌의 대표(primary) 전략이고, balance_cache 의
# PositionSnapshot 폴백이 읽는 행이 바로 그것이다. 한 계좌에서 두 번째 전략이
# 실주문을 시작하면 여기 튜플에 덧붙이기만 하면 된다: 귀속(holding_attribution),
# 잔고 폴백, 화면 배지가 전부 이 맵에서 파생된다.
#
# 시뮬 전략(close/flow/trail/scale/limit/cafe/surge/cafeopen/cafecool)은 어떤
# 계좌에도 속하지 않는다. 그들의 포지션은 브로커가 아니라 Fill 장부에만 있다.
ACCOUNT_STRATEGIES: dict[str, tuple[str, ...]] = {
    DEFAULT_ACCOUNT_ID: (STRATEGY_OPEN,),
    CAFE_ACCOUNT_ID: (STRATEGY_CAFEREAL,),
}

# 표시 순서를 가진 전 전략 목록. 화면의 필터 칩·회고 탭·곡선 기준선이 전부
# 여기서 파생된다. **전략을 추가할 때 갱신할 곳은 이 튜플 하나다.**
#
# 왜 필요한가: 이 목록이 손으로 복사된 곳이 네 군데였고, 어제 추가된 cafereal 과
# cafecool 이 그중 셋에서 빠졌다. 곡선 기준선(seed_cash)에서 빠지면 EquityChart 가
# 그 전략의 데이터를 통째로 버려 선이 아예 그려지지 않고, 주문 이력 칩에서
# 빠지면 그 전략의 주문만 골라볼 방법이 없어진다. 오타가 아니라 구조였다.
#
# 순서 = 화면에 놓을 순서. 실주문을 먼저, 그다음 qlib 시뮬, 카페 계열, 급등.
# 조건부 예약 주문의 표식 — 주문의 ``reasons_json`` 안 ``exit.kind`` 에 들어간다.
# "이 가격에 닿으면 판다"이지 "팔았다"가 아니다. 세 곳이 이 구분을 필요로 한다:
#   - cancel_unfilled_orders : 컷오프 스윕이 예약을 쓸어가면 익절선이 사라진다
#   - holding_attribution    : 미체결 매도 예약은 보유수량을 줄이지 않는다
#   - reserve_ladder_exits   : 오늘 이미 걸어둔 예약을 다시 걸지 않는다
EXIT_KIND_LADDER = "ladder_reserve"

ALL_STRATEGIES: tuple[str, ...] = (
    STRATEGY_OPEN, STRATEGY_CAFEREAL,                       # 실주문
    STRATEGY_CLOSE, STRATEGY_FLOW, STRATEGY_TRAIL,
    STRATEGY_SCALE, STRATEGY_LIMIT,                         # qlib 시뮬
    STRATEGY_CAFE, STRATEGY_CAFEOPEN, STRATEGY_CAFECOOL,    # 카페 시뮬
    STRATEGY_SURGE,
)

# Order-execution vocabulary shared by the model, the policy service and the
# API validators. Kept here so there is exactly one spelling of each value.
ORD_TYPE_MARKET = "market"
ORD_TYPE_LIMIT = "limit"
ORD_TYPES = (ORD_TYPE_MARKET, ORD_TYPE_LIMIT)

# Which price the limit is measured FROM.
BASE_PREV_CLOSE = "prev_close"   # yesterday's close — what the limit curve uses
BASE_OPEN = "open"               # today's opening print — what cafeopen uses
BASE_QUOTE = "quote"             # 현재가 at order time
PRICE_BASES = (BASE_PREV_CLOSE, BASE_OPEN, BASE_QUOTE)


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


class MarketPoolSnapshot(Base):
    """Daily close-time feature snapshot of the whole 15:05 ranking pool.

    Surge-eve research Track 2 (2026-08-07): every scanned code — matched or
    not, incl. out-of-universe small caps — gets its eve features stored so
    "what did tomorrow's surgers look like today?" can be answered for the
    full market. Next-day labels are joined at analysis time from KIS bars;
    NOTHING trades on this table."""
    __tablename__ = "market_pool_snapshots"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    code = Column(String(8), nullable=False)
    name = Column(String(64), nullable=True)
    close = Column(Float, nullable=True)
    ret5 = Column(Float, nullable=True)
    ret20 = Column(Float, nullable=True)
    vol_x = Column(Float, nullable=True)
    pos_vs_5d_high = Column(Float, nullable=True)
    off_30d_high = Column(Float, nullable=True)
    green = Column(Integer, nullable=True)
    ma20_gap = Column(Float, nullable=True)
    matched_pattern = Column(String(2), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_date", "code", name="uq_pool_snapshot_date_code"),
    )


class SurgePick(Base):
    """Daily surge-eve TOP10 — the mined profile scored over the 15:05 pool.

    Selection reads market_pool_snapshots (no extra KIS calls); the surge sim
    strategy buys the top 2 at 15:29. Research-born, simulation-only."""
    __tablename__ = "surge_picks"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    rank = Column(Integer, nullable=False)
    code = Column(String(8), nullable=False)
    name = Column(String(64), nullable=True)
    close = Column(Float, nullable=True)
    score = Column(Float, nullable=False)
    metrics_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_date", "code", name="uq_surge_pick_date_code"),
    )


class OrderbookSnapshot(Base):
    """10-level bid/ask book for cafe candidates, captured twice a day.

    Observation-only — NOTHING trades on this table. It exists to answer the
    one question the cafe sim cannot: when the 15:28 task "bought" 250만원
    of a 상한가 close, was there anything to buy? The sim calls get_quote(),
    which returns a price and no depth, then books a full fill at that price.

    Two slots, deliberately different in kind:
      - "1505" 정규장 — a real book. total_ask_qty is the shares actually
        offered; compare against the order qty for장중 fillability.
      - "1528" 동시호가 — no book matches yet, so KIS returns 예상체결가/
        예상체결수량 instead. antc_qty is the volume that WOULD match at the
        close; an order behind that queue is the one that doesn't fill.

    Kept per (day, slot, code) for every candidate, not just the bought two —
    the unbought ones are the control group.
    """
    __tablename__ = "orderbook_snapshots"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    slot = Column(String(5), nullable=False)      # "1505" / "1528"
    code = Column(String(8), nullable=False)
    name = Column(String(64), nullable=True)
    price = Column(Float, nullable=True)          # 현재가 at capture time
    upper_limit_px = Column(Float, nullable=True)  # 상한가
    at_upper_limit = Column(Integer, nullable=True)  # 1 = price is AT 상한가
    total_ask_qty = Column(Float, nullable=True)  # 총매도호가잔량 (정규장에서만 의미)
    total_bid_qty = Column(Float, nullable=True)  # 총매수호가잔량
    ask_qty_1 = Column(Float, nullable=True)      # 최우선 매도잔량
    antc_price = Column(Float, nullable=True)     # 예상체결가 (동시호가)
    antc_qty = Column(Float, nullable=True)       # 예상체결수량 (동시호가)
    book_json = Column(Text, nullable=True)       # JSON: {"asks":[...], "bids":[...]}
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_date", "slot", "code",
                         name="uq_orderbook_date_slot_code"),
    )


class TradingAccount(Base):
    """Per-account order-execution policy.

    Why this is data and not a constant: the buy side was hard-coded to a
    market order (`place_order(..., price=None)`), which contradicted the
    written policy — "매수는 시장가 금지, 기준가 −3% 지정가". Different
    accounts want different execution, so the choice belongs in a row the
    operator can change from the web UI, not in a deploy.

    The seeded 'main' row is deliberately `market`/`market`: creating this
    table must not alter what the live account already does. Changing a policy
    breaks that account's performance continuity, so it is an explicit act.
    """
    __tablename__ = "trading_accounts"

    account_id = Column(String(ACCOUNT_ID_LEN), primary_key=True)
    label = Column(String(64), nullable=True)

    # BUY. offset is a DISCOUNT: limit = base × (1 − buy_offset_pct).
    buy_ord_type = Column(String(8), nullable=False, default=ORD_TYPE_MARKET)
    buy_base = Column(String(12), nullable=True)      # prev_close | open | quote
    buy_offset_pct = Column(Float, nullable=False, default=0.0)
    buy_cancel_hhmm = Column(String(5), nullable=True)  # "15:20"; null = no sweep

    # SELL. offset is a PREMIUM: limit = base × (1 + sell_offset_pct).
    sell_ord_type = Column(String(8), nullable=False, default=ORD_TYPE_MARKET)
    sell_base = Column(String(12), nullable=True)
    sell_offset_pct = Column(Float, nullable=False, default=0.0)
    sell_cancel_hhmm = Column(String(5), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)


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
    ord_dvsn = Column(String(2), nullable=False, default="01")  # 00 지정가 / 01 시장가 / SM 시뮬(비-KIS)
    kis_order_id = Column(String(40), nullable=True, index=True)
    # SIMULATED is used by the 'close' strategy — no KIS round-trip, fill is
    # written immediately from the kr_data last close.
    # PENDING is the cafeopen resting limit: written at 09:00 with no Fill row
    # (so it stays invisible to _simulated_balance) and resolved at 10:00 into
    # SIMULATED (touched) or CANCELLED (never touched — kept as a row so the
    # miss rate is measurable, unlike the limit strategy which drops them).
    status = Column(String(16), nullable=False, default="SUBMITTED")  # SUBMITTED/REJECTED/FILLED/PARTIAL/CANCELLED/SIMULATED/PENDING
    error = Column(Text, nullable=True)
    # Decision basis CAPTURED AT ORDER TIME — {"action","basis","summary",
    # "metrics","top_features"}. Sells can't be reconstructed later (the sell
    # trigger is the stock VANISHING from that day's signal), so this snapshot
    # is the only record of why. Display-only.
    reasons_json = Column(Text, nullable=True)
    raw_response = Column(Text, nullable=True)

    fills = relationship("Fill", back_populates="order", cascade="all, delete-orphan")

    ORD_DVSN_SIM = "SM"  # not a KIS code — marks a DB-only paper fill

    @property
    def kind(self) -> str:
        """True order kind: 'market' | 'limit' | 'sim'.

        NOT derivable from `price`: sync_fills pins the reconciled average fill
        price onto market orders, so a non-null price does not imply 지정가.
        `status` wins over `ord_dvsn` because rows written before 2026-08-12
        carry a bogus ord_dvsn='00' — _persist_order derived it from "a price
        was passed", which is true of every simulated fill.
        """
        if self.status == "SIMULATED":
            return "sim"
        return "limit" if self.ord_dvsn == "00" else "market"


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
