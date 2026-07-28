"""Daily live-trading orchestrator.

Three entry points called by the Celery beat schedule:
  - generate_daily_signal()  ← 15:35 KST, after market close
  - submit_daily_orders()    ← 09:00 KST, opening auction window
  - sync_account()           ← 09:30 + 15:35 KST, balance reconciliation

Everything else (signals, orders, fills, snapshots) is persisted in the
SQLite/PostgreSQL store from `app.api.db`.
"""

from __future__ import annotations

import json
import logging
import math
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from ..config import settings
from ..core import kr_universes
from ..db import (
    DailyPnL, Fill, Order, PositionSnapshot, SessionLocal, Signal,
    STRATEGY_OPEN, STRATEGY_CLOSE, init_db,
)
from .backtest_service import _extract_recommended_picks, _stock_name
from .kis_client import (
    AccountSnapshot, Holding, KISClient, OrderResult, get_kis_client,
)


def _seed_for(strategy: str) -> float:
    """Per-strategy seed cash — picks the right config setting for the chart
    baseline and the simulated cash starting point."""
    if strategy == STRATEGY_CLOSE:
        return settings.live_seed_cash_close
    return settings.live_seed_cash_open


# Same model/strategy as the user's validated backtest — Phase A targets
# ARR/IR parity with H6 walk-forward by reusing that exact configuration.
# KIS Open API throttles ~1 request per second per account (EGW00201 on burst).
# Sleep between consecutive place_order / get_balance calls to stay under that.
KIS_THROTTLE_SECONDS = 0.35


LIVE_CONFIG = {
    "strategy_class": "TopkDropoutStrategy",
    "strategy_module": "qlib.contrib.strategy.signal_strategy",
    "strategy_kwargs": {"topk": 10, "n_drop": 2},
    "model_class": "LGBModel",
    "model_module": "qlib.contrib.model.gbdt",
    "model_kwargs": {},
    "handler_class": "Alpha158",
    "handler_module": "qlib.contrib.data.handler",
    "handler_kwargs": {},
    "instruments": "kospi200",
    "freq": "day",
}


def _reset_qlib_caches() -> None:
    """Drop qlib's in-process calendar/instrument/feature memcache.

    Long-lived worker children cache the calendar from *before* the daily
    refresh_kr_data appended today's row, so date math (`_last_trading_day`,
    `_next_trading_day`) silently runs one day behind and the writer/reader
    `as_of` contract drifts. Call this at the top of every task entry point
    that derives dates from the calendar.
    """
    try:
        from qlib.data.cache import H
        H.clear()
    except Exception:  # noqa: BLE001 — qlib may not be initialized yet
        pass


# ─── Signal generation (post-close, chained after refresh_kr_data) ──


def generate_daily_signal(today: date | None = None) -> dict:
    """Train (or reuse cached) model, score KOSPI200, persist top-K signal rows.

    Returns a summary dict for the calling task to log.
    """
    init_db()
    _reset_qlib_caches()
    today = today or _last_trading_day()
    # Freshness guard: if kr_data is grossly stale (e.g. the refresh cron has
    # been failing), refuse to write signals keyed to an old trade date —
    # raising lets Celery retry/alert instead of silently training on old bars.
    staleness = (date.today() - today).days
    if staleness > 5:
        raise RuntimeError(
            f"live_signal: kr_data last trading day {today.isoformat()} is "
            f"{staleness} days behind {date.today().isoformat()} — refusing to "
            "generate signals from stale data; check refresh_kr_data"
        )
    # The signal generated tonight is consumed by submit_daily_orders at the
    # NEXT trading session's open. Tag it with that target date so the morning
    # query (`Signal.as_of == today`) matches.
    signal_for = _next_trading_day(today)
    train_end, valid_start, valid_end = _walk_forward_windows(today)

    from qlib.utils import init_instance_by_config
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP

    handler = init_instance_by_config({
        "class": LIVE_CONFIG["handler_class"],
        "module_path": LIVE_CONFIG["handler_module"],
        "kwargs": {
            "instruments": LIVE_CONFIG["instruments"],
            "start_time": "2023-04-01",
            "end_time": today.isoformat(),
            **LIVE_CONFIG.get("handler_kwargs", {}),
        },
    }, accept_types=DataHandlerLP)

    dataset = DatasetH(handler=handler, segments={
        "train": ("2023-04-01", train_end.isoformat()),
        "valid": (valid_start.isoformat(), valid_end.isoformat()),
        "test":  ((valid_end.isoformat()), today.isoformat()),
    })

    model = init_instance_by_config({
        "class": LIVE_CONFIG["model_class"],
        "module_path": LIVE_CONFIG["model_module"],
        "kwargs": LIVE_CONFIG.get("model_kwargs", {}),
    })
    model.fit(dataset)
    pred = model.predict(dataset)

    # Diagnostics: surface what the model produced before picks extraction
    log.info(
        "generate_daily_signal: pred type=%s shape=%s index_levels=%s today=%s",
        type(pred).__name__,
        getattr(pred, "shape", None),
        getattr(getattr(pred, "index", None), "nlevels", None),
        today.isoformat(),
    )
    log.info(
        "generate_daily_signal: segments train=(%s..%s) valid=(%s..%s) test=(%s..%s)",
        "2023-04-01", train_end.isoformat(),
        valid_start.isoformat(), valid_end.isoformat(),
        valid_end.isoformat(), today.isoformat(),
    )
    if hasattr(pred, "index") and hasattr(pred.index, "get_level_values"):
        try:
            dts = pred.index.get_level_values(0)
            log.info(
                "generate_daily_signal: pred date range min=%s max=%s unique=%d",
                dts.min(), dts.max(), len(set(dts)),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("generate_daily_signal: pred index introspection failed: %s", exc)

    # Fail-fast: an empty prediction means picks=0 would silently overwrite
    # prior signals. Let Celery surface the failure instead.
    if not hasattr(pred, "shape") or len(pred) == 0:
        raise RuntimeError(
            f"live_signal: empty prediction for today={today.isoformat()} "
            f"(test segment {valid_end.isoformat()}..{today.isoformat()}); "
            "check kr_data freshness and trading-day windows"
        )

    picks = _extract_recommended_picks(pred, LIVE_CONFIG) or []
    log.info(
        "generate_daily_signal: picks count=%d today=%s signal_for=%s",
        len(picks), today, signal_for,
    )

    with SessionLocal() as db:
        # Idempotent: nuke any prior picks targeting the same trade date.
        db.query(Signal).filter(Signal.as_of == signal_for).delete()
        for p in picks:
            db.add(Signal(
                as_of=signal_for,
                rank=p["rank"],
                code=p["code"],
                name=p.get("name") or _stock_name(p["code"]),
                score=p.get("score"),
                model_class=LIVE_CONFIG["model_class"],
                strategy_class=LIVE_CONFIG["strategy_class"],
            ))
        db.commit()

    return {
        "as_of": signal_for.isoformat(),
        "generated_on": today.isoformat(),
        "picks": len(picks),
    }


# ─── Order submission (09:00 KST) ───────────────────────────────────


def _select_affordable_buys(candidates: list[str],
                            slot_budget: float,
                            n_drop: int,
                            price_fn=None) -> tuple[list[tuple[str, float]], list[str]]:
    """Pick up to n_drop buy candidates in rank order, skipping stocks whose
    single-share price exceeds the target per-stock slot budget
    (= total equity / topk — the weight one stock is *supposed* to hold).

    With a small account (1천만 seed, topk=10 → slot ≈ 100만) a stock like
    Samsung Biologics (155만/주) can never be held at its target weight: it
    either swallows half the portfolio during the initial ramp or floors to
    qty=0 and silently wastes the day's buy slot. Filtering up front lets the
    next-ranked affordable stock take the slot instead.

    Returns (selected [(code, last_close)], skipped_expensive [codes]).
    """
    price_fn = price_fn or _last_close
    selected: list[tuple[str, float]] = []
    skipped: list[str] = []
    for code in candidates:
        if len(selected) >= n_drop:
            break
        px = price_fn(code)
        if not px or px <= 0:
            log.warning("_select_affordable_buys: no price for %s — skipped", code)
            continue
        if px > slot_budget:
            log.info(
                "_select_affordable_buys: %s price %.0f > slot budget %.0f — "
                "skipped, next rank takes the slot", code, px, slot_budget,
            )
            skipped.append(code)
            continue
        selected.append((code, px))
    return selected, skipped


def submit_daily_orders(today: date | None = None,
                         client: KISClient | None = None,
                         *,
                         strategy: str = STRATEGY_OPEN,
                         simulated: bool = False) -> dict:
    """Compute (sell, buy) lists from today's signal vs current holdings,
    then either:
      - strategy='open', simulated=False (default): place KIS market orders at the opening auction
      - strategy='close', simulated=True: write SIMULATED Order+Fill rows priced at the kr_data last close

    Strategy: TopkDropout-style. We hold up to `topk` codes at all times.
    Each session we drop the worst N held that fell out of today's top-K, and
    buy the top-N that aren't currently held. N is capped by `n_drop`.
    """
    init_db()
    _reset_qlib_caches()
    # Signals are stored with as_of=next_trading_day(last_trading_day) in
    # generate_daily_signal; lookup must mirror that key. Using bare
    # _last_trading_day() here was off by one trading day — 09:00 firing on
    # 5/20 read today=5/19 from the calendar and missed the signals saved for
    # as_of=5/20. The cache reset above keeps both sides computing from the
    # same on-disk calendar (a stale in-process cache re-introduced the skew).
    today = today or _next_trading_day(_last_trading_day())

    with SessionLocal() as db:
        signals = (db.query(Signal)
                     .filter(Signal.as_of == today)
                     .order_by(Signal.rank.asc())
                     .all())
        if not signals:
            return {"status": "no_signal", "as_of": today.isoformat(),
                    "strategy": strategy, "simulated": simulated}

        if simulated:
            snapshot = _simulated_balance(db, strategy=strategy)
        else:
            client = client or get_kis_client()
            snapshot = client.get_balance()
        held_codes = {h.code for h in snapshot.holdings}
        target_codes = [s.code for s in signals]
        n_drop = LIVE_CONFIG["strategy_kwargs"]["n_drop"]

        to_sell_codes = [c for c in held_codes if c not in set(target_codes)][:n_drop]
        # Buy candidates keep full rank order — affordability filtering below
        # (after sells refresh the cash) decides which n_drop actually get bought.
        buy_candidates = [c for c in target_codes if c not in held_codes]

        submitted = 0
        rejected = 0
        # Sells first to free cash
        for code in to_sell_codes:
            holding = next((h for h in snapshot.holdings if h.code == code), None)
            if not holding or holding.qty <= 0:
                continue
            if simulated:
                px = _last_close(code) or holding.eval_price or holding.avg_price
                if not px or px <= 0:
                    continue
                realised = (px - holding.avg_price) * holding.qty
                _persist_simulated_fill(db, today, code, "SELL", holding.qty, px,
                                        strategy=strategy, pnl=realised)
                submitted += 1
            else:
                res = client.place_order(code, "SELL", holding.qty, price=None)
                _persist_order(db, today, code, "SELL", holding.qty, None, res,
                               strategy=strategy)
                if not res.ok:
                    log.warning("live_orders: SELL REJECTED code=%s qty=%d error=%s",
                                code, holding.qty, res.error)
                submitted += int(res.ok)
                rejected += int(not res.ok)
                time.sleep(KIS_THROTTLE_SECONDS)

        # Refresh cash after sells
        if simulated:
            snapshot_after = _simulated_balance(db, strategy=strategy)
        else:
            snapshot_after = client.get_balance()
            time.sleep(KIS_THROTTLE_SECONDS)
        cash = snapshot_after.cash
        # Target per-stock slot: the weight one position is supposed to hold
        # in the finished top-K portfolio. Used both to filter unaffordable
        # stocks and to cap the per-day buy size — without the cap, day one
        # of an empty account dumped ALL cash into the first n_drop picks
        # (2 x 500만 whales) and later days bought dust with the leftovers.
        topk = LIVE_CONFIG["strategy_kwargs"]["topk"]
        total_equity = max(snapshot_after.total_eval, cash)
        slot_budget = total_equity / max(topk, 1)
        to_buy, skipped_expensive = _select_affordable_buys(buy_candidates, slot_budget, n_drop)
        if to_buy:
            per_code_budget = min(cash / max(len(to_buy), 1), slot_budget)
            for code, px in to_buy:
                qty = max(int(per_code_budget // px), 0)
                if qty <= 0:
                    continue
                if simulated:
                    _persist_simulated_fill(db, today, code, "BUY", qty, px,
                                            strategy=strategy)
                    submitted += 1
                else:
                    res = client.place_order(code, "BUY", qty, price=None)
                    _persist_order(db, today, code, "BUY", qty, None, res,
                                   strategy=strategy)
                    if not res.ok:
                        log.warning("live_orders: BUY REJECTED code=%s qty=%d error=%s",
                                    code, qty, res.error)
                    submitted += int(res.ok)
                    rejected += int(not res.ok)
                    time.sleep(KIS_THROTTLE_SECONDS)

        db.commit()

        # Surface a one-line summary if anything was rejected — operators don't
        # have to grep through individual order rows to find the last KIS error.
        if rejected > 0 and not simulated:
            last_err_row = (db.query(Order)
                              .filter(Order.trade_date == today,
                                      Order.strategy == strategy,
                                      Order.status == "REJECTED")
                              .order_by(Order.submitted_at.desc())
                              .first())
            last_err = last_err_row.error if last_err_row else None
            log.warning(
                "live_orders: strategy=%s submitted=%d rejected=%d last_error=%s",
                strategy, submitted, rejected, last_err,
            )

        return {
            "status": "ok",
            "as_of": today.isoformat(),
            "strategy": strategy,
            "simulated": simulated,
            "sells": len(to_sell_codes),
            "buys": len(to_buy),
            "skipped_expensive": skipped_expensive,
            "submitted": submitted,
            "rejected": rejected,
        }


# ─── Account sync + daily PnL roll-up ───────────────────────────────


def sync_account(client: KISClient | None = None,
                 trade_date: date | None = None,
                 *,
                 strategy: str = STRATEGY_OPEN) -> dict:
    """Snapshot the per-strategy portfolio and roll up DailyPnL.

    strategy='open'  → reads KIS get_balance (real paper account)
    strategy='close' → reconstructs from simulated Fills in the DB
    """
    init_db()
    _reset_qlib_caches()
    trade_date = trade_date or _last_trading_day()
    if strategy == STRATEGY_CLOSE:
        with SessionLocal() as db:
            snapshot = _simulated_balance(db, strategy=STRATEGY_CLOSE)
    else:
        client = client or get_kis_client()
        snapshot = client.get_balance()

    with SessionLocal() as db:
        existing = (db.query(PositionSnapshot)
                      .filter(PositionSnapshot.snapshot_date == trade_date,
                              PositionSnapshot.strategy == strategy)
                      .first())
        holdings_json = json.dumps([
            {"code": h.code, "name": _stock_name(h.code),
             "qty": h.qty, "avg": h.avg_price,
             "eval_price": h.eval_price, "eval_value": h.eval_value,
             "pnl": h.pnl, "pnl_pct": h.pnl_pct}
            for h in snapshot.holdings
        ], ensure_ascii=False)

        if existing:
            existing.cash = snapshot.cash
            existing.total_eval = snapshot.total_eval
            existing.holdings_json = holdings_json
        else:
            db.add(PositionSnapshot(
                snapshot_date=trade_date,
                strategy=strategy,
                cash=snapshot.cash,
                total_eval=snapshot.total_eval,
                holdings_json=holdings_json,
            ))

        prev = (db.query(PositionSnapshot)
                  .filter(PositionSnapshot.snapshot_date < trade_date,
                          PositionSnapshot.strategy == strategy)
                  .order_by(PositionSnapshot.snapshot_date.desc())
                  .first())
        # First-ever sync's starting_equity must be the strategy's seed (not
        # today's KIS-reported total) so the equity chart normalises against
        # the user's actual capital rather than baking pre-existing positions
        # into the baseline. open and close strategies have different seeds.
        starting = prev.total_eval if prev else _seed_for(strategy)
        unrealised = sum(h.pnl for h in snapshot.holdings)
        realised = (db.query(Fill)
                      .join(Order)
                      .filter(Order.trade_date == trade_date,
                              Fill.strategy == strategy)
                      .with_entities(Fill.pnl)
                      .all())
        realised_sum = sum((p[0] or 0) for p in realised)

        existing_pnl = (db.query(DailyPnL)
                          .filter(DailyPnL.trade_date == trade_date,
                                  DailyPnL.strategy == strategy)
                          .first())
        if existing_pnl:
            existing_pnl.ending_equity = snapshot.total_eval
            existing_pnl.realised_pnl = realised_sum
            existing_pnl.unrealised_pnl = unrealised
        else:
            db.add(DailyPnL(
                trade_date=trade_date,
                strategy=strategy,
                starting_equity=starting,
                ending_equity=snapshot.total_eval,
                realised_pnl=realised_sum,
                unrealised_pnl=unrealised,
                fees=0.0,
            ))
        db.commit()

    return {
        "trade_date": trade_date.isoformat(),
        "strategy": strategy,
        "cash": snapshot.cash,
        "total_eval": snapshot.total_eval,
        "holdings": len(snapshot.holdings),
    }


# ─── Helpers ────────────────────────────────────────────────────────


def _persist_order(db: Session, trade_date: date, code: str, side: str,
                   qty: int, price: float | None, res: OrderResult,
                   *, strategy: str = STRATEGY_OPEN) -> Order:
    o = Order(
        trade_date=trade_date,
        strategy=strategy,
        code=code,
        name=_stock_name(code),
        side=side,
        qty=qty,
        price=price,
        ord_dvsn="01" if price is None else "00",
        kis_order_id=res.order_id,
        status="SUBMITTED" if res.ok else "REJECTED",
        error=res.error,
        raw_response=json.dumps(res.raw, ensure_ascii=False)[:4000],
    )
    db.add(o)
    db.flush()  # populate o.id for Fill FK
    return o


def _persist_simulated_fill(db: Session, trade_date: date, code: str, side: str,
                            qty: int, price: float, strategy: str = STRATEGY_CLOSE,
                            pnl: float | None = None) -> None:
    """Write a paper Order+Fill pair for the close strategy. No KIS round-trip."""
    res = OrderResult(ok=True, order_id=f"SIM-{int(datetime.utcnow().timestamp()*1000)}",
                      code=code, side=side, qty=qty, price=price,
                      raw={"simulated": True}, error=None)
    o = _persist_order(db, trade_date, code, side, qty, price, res, strategy=strategy)
    o.status = "SIMULATED"
    db.add(Fill(
        order_id=o.id,
        strategy=strategy,
        qty=qty,
        price=price,
        fee=0.0,
        pnl=pnl,
    ))


def _simulated_balance(db: Session, strategy: str = STRATEGY_CLOSE,
                       seed_cash: float | None = None) -> AccountSnapshot:
    """Reconstruct cash + holdings for a paper strategy from its Fill history.

    Cash = seed − sum(buy.qty × buy.price) + sum(sell.qty × sell.price)
    Holdings per code: net buy − sell qty; average price weighted by buy fills.
    `seed_cash=None` falls back to the strategy-specific config setting so
    callers don't have to thread the right number through every call site.
    """
    if seed_cash is None:
        seed_cash = _seed_for(strategy)
    rows = (db.query(Fill, Order)
              .join(Order, Fill.order_id == Order.id)
              .filter(Fill.strategy == strategy)
              .order_by(Fill.filled_at.asc())
              .all())
    cash = seed_cash
    pos: dict[str, dict] = {}  # code -> {qty, cost}
    for fill, order in rows:
        value = fill.qty * fill.price
        if order.side == "BUY":
            cash -= value
            p = pos.setdefault(order.code, {"qty": 0, "cost": 0.0})
            p["qty"] += fill.qty
            p["cost"] += value
        elif order.side == "SELL":
            cash += value
            p = pos.get(order.code)
            if p:
                # Proportional cost reduction
                if p["qty"] > 0:
                    p["cost"] -= (fill.qty / p["qty"]) * p["cost"]
                p["qty"] -= fill.qty
                if p["qty"] <= 0:
                    pos.pop(order.code, None)
    holdings: list[Holding] = []
    total_eval = cash
    for code, p in pos.items():
        if p["qty"] <= 0:
            continue
        last_px = _last_close(code) or 0.0
        avg = (p["cost"] / p["qty"]) if p["qty"] else 0.0
        ev_value = p["qty"] * last_px
        pnl = ev_value - p["cost"]
        pnl_pct = (pnl / p["cost"]) if p["cost"] else 0.0
        holdings.append(Holding(code=code, qty=p["qty"], avg_price=avg,
                                eval_price=last_px, eval_value=ev_value,
                                pnl=pnl, pnl_pct=pnl_pct))
        total_eval += ev_value
    return AccountSnapshot(cash=cash, total_eval=total_eval, holdings=holdings)


def _last_trading_day(today: date | None = None) -> date:
    """Most recent KRX trading day on or before `today`.

    Uses qlib's calendar (which encodes actual holidays); falls back to a
    weekday-only check if the calendar isn't available.
    """
    today = today or date.today()
    try:
        from qlib.data import D
        cal = D.calendar(end_time=today.isoformat())
        if len(cal) > 0:
            return pd.Timestamp(cal[-1]).date()
    except Exception:  # noqa: BLE001
        log.warning(
            "_last_trading_day: qlib calendar unavailable; falling back to weekday check",
            exc_info=True,
        )
    while today.weekday() >= 5:
        today = today - pd.Timedelta(days=1).to_pytimedelta()
    return today


def _prev_trading_day(d: date) -> date:
    """The trading day strictly before `d`. Weekend-only fallback if the
    qlib calendar isn't loaded.
    """
    try:
        from qlib.data import D
        cal = D.calendar(end_time=d.isoformat())
        prior = [pd.Timestamp(c).date() for c in cal if pd.Timestamp(c).date() < d]
        if prior:
            return prior[-1]
    except Exception:  # noqa: BLE001
        log.warning(
            "_prev_trading_day: qlib calendar unavailable; falling back to weekday check",
            exc_info=True,
        )
    prev = d - pd.Timedelta(days=1).to_pytimedelta()
    while prev.weekday() >= 5:
        prev = prev - pd.Timedelta(days=1).to_pytimedelta()
    return prev


def _next_trading_day(d: date) -> date:
    """The trading day strictly after `d`. Weekend-only fallback if the
    qlib calendar isn't loaded or doesn't yet contain the future window.
    """
    try:
        from qlib.data import D
        cal = D.calendar(
            start_time=d.isoformat(),
            end_time=(pd.Timestamp(d) + pd.Timedelta(days=14)).date().isoformat(),
        )
        future = [pd.Timestamp(c).date() for c in cal if pd.Timestamp(c).date() > d]
        if future:
            return future[0]
    except Exception:  # noqa: BLE001
        log.warning(
            "_next_trading_day: qlib calendar unavailable; falling back to weekday check",
            exc_info=True,
        )
    nxt = d + pd.Timedelta(days=1).to_pytimedelta()
    while nxt.weekday() >= 5:
        nxt = nxt + pd.Timedelta(days=1).to_pytimedelta()
    return nxt


def _walk_forward_windows(today: date) -> tuple[date, date, date]:
    """Same shape as our H6 walk-forward — train cuts off ~3 months before today.

    `valid_end` is snapped to the **trading day strictly before `today`** so that
    the test segment `(valid_end, today)` always spans at least one trading day.
    Before this snap, a Monday `today` produced `valid_end = Sunday`, which fed
    an empty test slice into the model and silently emitted `picks=0`.
    """
    today_ts = pd.Timestamp(today)
    train_end = (today_ts - pd.DateOffset(months=3)).date()
    valid_start = (today_ts - pd.DateOffset(months=2, days=29)).date()
    valid_end = _prev_trading_day(today)
    return train_end, valid_start, valid_end


def _last_close(code: str) -> float | None:
    """Pull last close from qlib provider — fast path for per-code budgeting."""
    try:
        from qlib.data import D
        df = D.features([f"{code}"], ["$close"], freq="day").reset_index()
        if df.empty:
            return None
        return float(df.iloc[-1]["$close"])
    except Exception:  # noqa: BLE001
        return None
