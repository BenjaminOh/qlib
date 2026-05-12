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
    DailyPnL, Fill, Order, PositionSnapshot, SessionLocal, Signal, init_db,
)
from .backtest_service import _extract_recommended_picks, _stock_name
from .kis_client import KISClient, OrderResult, get_kis_client


# Same model/strategy as the user's validated backtest — Phase A targets
# ARR/IR parity with H6 walk-forward by reusing that exact configuration.
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


# ─── Signal generation (15:35 KST) ──────────────────────────────────


def generate_daily_signal(today: date | None = None) -> dict:
    """Train (or reuse cached) model, score KOSPI200, persist top-K signal rows.

    Returns a summary dict for the calling task to log.
    """
    init_db()
    today = today or _last_trading_day()
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

    picks = _extract_recommended_picks(pred, LIVE_CONFIG) or []
    log.info("generate_daily_signal: picks count=%d for today=%s", len(picks), today)

    with SessionLocal() as db:
        # Idempotent: nuke today's previous picks before re-inserting
        db.query(Signal).filter(Signal.as_of == today).delete()
        for p in picks:
            db.add(Signal(
                as_of=today,
                rank=p["rank"],
                code=p["code"],
                name=p.get("name") or _stock_name(p["code"]),
                score=p.get("score"),
                model_class=LIVE_CONFIG["model_class"],
                strategy_class=LIVE_CONFIG["strategy_class"],
            ))
        db.commit()

    return {"as_of": today.isoformat(), "picks": len(picks)}


# ─── Order submission (09:00 KST) ───────────────────────────────────


def submit_daily_orders(today: date | None = None,
                         client: KISClient | None = None) -> dict:
    """Compute (sell, buy) lists from yesterday's signal vs current holdings,
    then place market orders at the opening auction.

    Strategy: TopkDropout-style. We hold up to `topk` codes at all times.
    Each session we drop the worst N held that fell out of today's top-K, and
    buy the top-N that aren't currently held. N is capped by `n_drop`.
    """
    init_db()
    today = today or _last_trading_day()
    client = client or get_kis_client()

    with SessionLocal() as db:
        signals = (db.query(Signal)
                     .filter(Signal.as_of == today)
                     .order_by(Signal.rank.asc())
                     .all())
        if not signals:
            return {"status": "no_signal", "as_of": today.isoformat()}

        snapshot = client.get_balance()
        held_codes = {h.code for h in snapshot.holdings}
        target_codes = [s.code for s in signals]
        topk = LIVE_CONFIG["strategy_kwargs"]["topk"]
        n_drop = LIVE_CONFIG["strategy_kwargs"]["n_drop"]

        # SELL: held codes that are no longer in top-K, worst-rank-first
        to_sell_codes = [c for c in held_codes if c not in set(target_codes)]
        to_sell_codes = to_sell_codes[:n_drop]

        # BUY: top-K codes we don't yet hold, best-rank-first
        to_buy_codes = [c for c in target_codes if c not in held_codes][:n_drop]

        results: list[OrderResult] = []
        # Sells first to free cash
        for code in to_sell_codes:
            holding = next((h for h in snapshot.holdings if h.code == code), None)
            if not holding or holding.qty <= 0:
                continue
            res = client.place_order(code, "SELL", holding.qty, price=None)
            _persist_order(db, today, code, "SELL", holding.qty, None, res)
            results.append(res)

        # Refresh cash after sells
        snapshot_after = client.get_balance()
        cash = snapshot_after.cash
        if to_buy_codes:
            per_code_budget = cash / max(len(to_buy_codes), 1)
            for code in to_buy_codes:
                px = _last_close(code)
                if px is None or px <= 0:
                    _persist_order(db, today, code, "BUY", 0, None,
                                   OrderResult(ok=False, order_id=None, code=code, side="BUY",
                                               qty=0, price=None, raw={}, error="no price"))
                    continue
                qty = max(int(per_code_budget // px), 0)
                if qty <= 0:
                    continue
                res = client.place_order(code, "BUY", qty, price=None)
                _persist_order(db, today, code, "BUY", qty, None, res)
                results.append(res)

        db.commit()
        return {
            "status": "ok",
            "as_of": today.isoformat(),
            "sells": len(to_sell_codes),
            "buys": len(to_buy_codes),
            "submitted": sum(1 for r in results if r.ok),
            "rejected": sum(1 for r in results if not r.ok),
        }


# ─── Account sync + daily PnL roll-up ───────────────────────────────


def sync_account(client: KISClient | None = None,
                 trade_date: date | None = None) -> dict:
    """Pull current balance, snapshot it, roll up DailyPnL for the equity curve."""
    init_db()
    client = client or get_kis_client()
    trade_date = trade_date or _last_trading_day()
    snapshot = client.get_balance()

    with SessionLocal() as db:
        existing = db.query(PositionSnapshot).filter(PositionSnapshot.snapshot_date == trade_date).first()
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
                cash=snapshot.cash,
                total_eval=snapshot.total_eval,
                holdings_json=holdings_json,
            ))

        # DailyPnL: compute vs. previous day's ending_equity
        prev = (db.query(PositionSnapshot)
                  .filter(PositionSnapshot.snapshot_date < trade_date)
                  .order_by(PositionSnapshot.snapshot_date.desc())
                  .first())
        starting = prev.total_eval if prev else snapshot.total_eval
        unrealised = sum(h.pnl for h in snapshot.holdings)
        # Realised PnL today = sum of fills.pnl from orders dated today
        realised = (db.query(Fill)
                      .join(Order)
                      .filter(Order.trade_date == trade_date)
                      .with_entities(Fill.pnl)
                      .all())
        realised_sum = sum((p[0] or 0) for p in realised)

        existing_pnl = db.query(DailyPnL).filter(DailyPnL.trade_date == trade_date).first()
        if existing_pnl:
            existing_pnl.ending_equity = snapshot.total_eval
            existing_pnl.realised_pnl = realised_sum
            existing_pnl.unrealised_pnl = unrealised
        else:
            db.add(DailyPnL(
                trade_date=trade_date,
                starting_equity=starting,
                ending_equity=snapshot.total_eval,
                realised_pnl=realised_sum,
                unrealised_pnl=unrealised,
                fees=0.0,
            ))
        db.commit()

    return {
        "trade_date": trade_date.isoformat(),
        "cash": snapshot.cash,
        "total_eval": snapshot.total_eval,
        "holdings": len(snapshot.holdings),
    }


# ─── Helpers ────────────────────────────────────────────────────────


def _persist_order(db: Session, trade_date: date, code: str, side: str,
                   qty: int, price: float | None, res: OrderResult) -> None:
    db.add(Order(
        trade_date=trade_date,
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
    ))


def _last_trading_day(today: date | None = None) -> date:
    today = today or date.today()
    # Mon-Fri. Doesn't account for KRX holidays — caller can override during backfill.
    while today.weekday() >= 5:
        today = pd.Timestamp(today).date() - pd.Timedelta(days=1).to_pytimedelta()
    return today


def _walk_forward_windows(today: date) -> tuple[date, date, date]:
    """Same shape as our H6 walk-forward — train cuts off ~3 months before today."""
    today_ts = pd.Timestamp(today)
    train_end = (today_ts - pd.DateOffset(months=3)).date()
    valid_start = (today_ts - pd.DateOffset(months=2, days=29)).date()
    valid_end = (today_ts - pd.Timedelta(days=1)).date()
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
