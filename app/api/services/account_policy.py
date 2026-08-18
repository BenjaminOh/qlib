"""Per-account order-execution policy — the one place a limit price is computed.

Why this module exists
----------------------
The real-money path submitted every order as a market order: `place_order`
translates `price=None` into `ORD_DVSN="01"`, and both call sites passed
`price=None`. That contradicted the written policy ("매수는 시장가 금지 —
기준가 −3% 지정가 예약") which until now existed only inside the *simulated*
`limit` / `cafeopen` curves, neither of which ever reaches KIS.

Rather than hard-code the other choice, execution style became per-account
data (`trading_accounts`). Accounts differ; a deploy is the wrong instrument
for changing how an account trades.

Design notes
------------
* A missing account row degrades to MARKET, i.e. exactly the behaviour that
  existed before this module. Absence must never be a surprise.
* If the base price can't be fetched, `BasePriceUnavailable` is raised and the
  caller skips that code. Falling back to a market order would break the
  account's stated policy *silently*, which is the one outcome worth avoiding —
  a skipped buy costs an opportunity, a surprise market buy costs money.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from ..db.models import (
    BASE_OPEN, BASE_PREV_CLOSE, BASE_QUOTE, DEFAULT_ACCOUNT_ID,
    ORD_TYPE_LIMIT, ORD_TYPE_MARKET, TradingAccount,
)
from .kis_client import KISClient, round_to_tick

log = logging.getLogger(__name__)


class BasePriceUnavailable(Exception):
    """The policy wants a limit price but its base price could not be read."""


@dataclass(frozen=True)
class OrderPolicy:
    """How one side (buy or sell) of one account submits orders."""

    side: str                      # "BUY" | "SELL"
    ord_type: str = ORD_TYPE_MARKET
    base: str | None = None        # prev_close | open | quote
    offset_pct: float = 0.0        # BUY discounts, SELL adds a premium
    cancel_hhmm: str | None = None  # unfilled sweep cutoff; None = no sweep

    @property
    def is_limit(self) -> bool:
        return self.ord_type == ORD_TYPE_LIMIT

    @property
    def signed_offset(self) -> float:
        """Multiplier applied to the base price. Buy below, sell above."""
        return (1.0 - self.offset_pct) if self.side == "BUY" else (1.0 + self.offset_pct)


MARKET_BUY = OrderPolicy(side="BUY")
MARKET_SELL = OrderPolicy(side="SELL")


def get_policies(db: Session, account_id: str = DEFAULT_ACCOUNT_ID
                 ) -> tuple[OrderPolicy, OrderPolicy]:
    """(buy, sell) policy for an account. Unknown account → market/market."""
    row = db.get(TradingAccount, account_id)
    if row is None:
        log.info("account_policy: no row for account_id=%r — using market/market", account_id)
        return MARKET_BUY, MARKET_SELL
    return (
        OrderPolicy(side="BUY", ord_type=row.buy_ord_type, base=row.buy_base,
                    offset_pct=row.buy_offset_pct or 0.0,
                    cancel_hhmm=row.buy_cancel_hhmm),
        OrderPolicy(side="SELL", ord_type=row.sell_ord_type, base=row.sell_base,
                    offset_pct=row.sell_offset_pct or 0.0,
                    cancel_hhmm=row.sell_cancel_hhmm),
    )


def base_price(client: KISClient, code: str, policy: OrderPolicy,
               day: date, *, quote: dict | None = None) -> float | None:
    """The reference price this policy measures its offset from.

    `quote` lets a caller hand over a KIS quote it already paid for — quotes
    are gated to ~1/sec per appkey on paper, so re-fetching per candidate is
    the difference between a 2-second and a 14-second order run.
    """
    if policy.base == BASE_PREV_CLOSE:
        from .live_trader import _prev_close_before  # local: avoids an import cycle
        return _prev_close_before(code, day)

    if policy.base in (BASE_OPEN, BASE_QUOTE):
        q = quote if quote is not None else (client.get_quote(code) or {})
        return q.get("open") if policy.base == BASE_OPEN else q.get("price")

    return None


def order_price(client: KISClient, code: str, policy: OrderPolicy,
                day: date, *, quote: dict | None = None) -> float | None:
    """Price to pass to `place_order`: None for market, a tick-aligned limit
    otherwise.

    Raises BasePriceUnavailable when a limit policy has no usable base price.
    """
    if not policy.is_limit:
        return None

    base = base_price(client, code, policy, day, quote=quote)
    if not base or base <= 0:
        raise BasePriceUnavailable(
            f"{code}: {policy.side} 정책이 지정가({policy.base})인데 기준가를 못 읽음")

    px = round_to_tick(base * policy.signed_offset)
    if px <= 0:
        raise BasePriceUnavailable(
            f"{code}: 기준가 {base}에서 계산한 지정가가 0 이하")
    return float(px)
