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
    STRATEGY_OPEN, STRATEGY_CLOSE, STRATEGY_FLOW,
    STRATEGY_TRAIL, STRATEGY_SCALE, STRATEGY_LIMIT, STRATEGY_CAFE, init_db,
)
from .backtest_service import _extract_recommended_picks, _stock_name
from .kis_client import (
    AccountSnapshot, Holding, KISClient, OrderResult, get_kis_client,
    round_to_tick,
)


def _seed_for(strategy: str) -> float:
    """Per-strategy seed cash — picks the right config setting for the chart
    baseline and the simulated cash starting point."""
    seeds = {
        STRATEGY_CLOSE: settings.live_seed_cash_close,
        STRATEGY_FLOW: settings.live_seed_cash_flow,
        STRATEGY_TRAIL: settings.live_seed_cash_trail,
        STRATEGY_SCALE: settings.live_seed_cash_scale,
        STRATEGY_LIMIT: settings.live_seed_cash_limit,
        STRATEGY_CAFE: settings.live_seed_cash_cafe,
    }
    return seeds.get(strategy, settings.live_seed_cash_open)


# Strategies that hold DB-only positions exited by price brackets rather than
# by rank dropout. All share the same signal picks (flow re-ranks them) and
# 1천만 seed — the exit-rule matrix (2026-08-04):
#   close/flow : fixed TP +10%  + structural prev-low stop
#   trail      : no TP — trailing stop −7% off the peak close (+ prev-low stop)
#   scale      : TP +7% sells HALF, remainder rides the −7% trail
#   limit      : entries via −3% resting limit orders; TP +10% (예약 매도 모델)
BRACKET_STRATEGIES = (STRATEGY_CLOSE, STRATEGY_FLOW, STRATEGY_TRAIL,
                      STRATEGY_SCALE, STRATEGY_LIMIT, STRATEGY_CAFE)

EXIT_RULES: dict[str, dict] = {
    STRATEGY_CLOSE: {"tp": 0.10},
    STRATEGY_FLOW:  {"tp": 0.10},
    STRATEGY_TRAIL: {"trail": 0.07},
    # Ladder scale-out (2026-08-04, user's design): sell 50% at +10%, 50% of
    # the remainder at +15%, everything at +20%. Once ANY rung has sold, a
    # ratcheting floor sits one rung below the last fill (+5% after rung 1,
    # +10% after rung 2) — breaking it liquidates the rest. Before rung 1
    # the shared structural stop applies.
    STRATEGY_SCALE: {"ladder": [0.10, 0.15, 0.20], "floor_gap": 0.05},
    STRATEGY_LIMIT: {"tp": 0.10},
    # cafe: TP +10%; the stop is the screener's STRUCTURAL level stored on
    # the entry order (reasons_json.stop_px), capped at −live_cafe_stop_cap.
    STRATEGY_CAFE:  {"tp": 0.10, "stop_source": "entry"},
}


# Same model/strategy as the user's validated backtest — Phase A targets
# ARR/IR parity with H6 walk-forward by reusing that exact configuration.
# KIS enforces 1 TRANSACTION per second per account, and one place_order is
# actually TWO api calls (hashkey + order). 0.35s between orders got the
# second order rejected with "1 초당 거래건수를 초과" (2026-07-29) — keep
# comfortably above 1s.
KIS_THROTTLE_SECONDS = 1.2

# How many signal ranks to PERSIST daily (trading still uses topk below).
# Needed so sell reasons can state where an exited stock ranked today.
SIGNAL_STORE_TOP_N = 30


LIVE_CONFIG = {
    "strategy_class": "TopkDropoutStrategy",
    "strategy_module": "qlib.contrib.strategy.signal_strategy",
    "strategy_kwargs": {"topk": 10, "n_drop": 2},
    "model_class": "LGBModel",
    "model_module": "qlib.contrib.model.gbdt",
    # Stability fix (2026-07-29, diagnostic-backed): the default learning
    # rate converged in ONE tree on noisy days (best_iteration=1 → every
    # stock got the same constant score and picks fell back to code order;
    # 2 of the last 4 days degenerated). lr=0.005 with 200-round patience
    # trained 63-216 trees with 8-10 distinct top-10 scores on all four
    # days. See scripts/diagnose_training_stability.py and the
    # scenario-matrix design doc for the full experiment table.
    "model_kwargs": {"learning_rate": 0.005, "early_stopping_rounds": 200},
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

    # Store the top-30 ranks (not just the tradable top-10): the ONLY sell
    # trigger is "dropped out of the signal", so explaining a sell requires
    # knowing where the stock LANDED today ("전일 3위 → 금일 21위" vs
    # "30위권 밖"). Orders and the dashboard still consume rank <= topk.
    store_cfg = {**LIVE_CONFIG,
                 "strategy_kwargs": {**LIVE_CONFIG["strategy_kwargs"],
                                      "topk": SIGNAL_STORE_TOP_N}}
    picks = _extract_recommended_picks(pred, store_cfg) or []

    # Drop stocks whose bars FROZE (no data for 5+ trading days = suspended /
    # delisting track). Their Alpha158 rows are computed from stale pre-halt
    # prices and can produce ghost scores — 콘텐트리중앙 ranked #3 while
    # trade-halted since 6/15 (2026-07-30 incident).
    try:
        stale = _stale_codes([p["code"] for p in picks], today, max_lag_days=5)
        if stale:
            log.warning("generate_daily_signal: dropping stale/halted codes: %s", sorted(stale))
            picks = [p for p in picks if p["code"] not in stale]
            for i, p in enumerate(picks, 1):
                p["rank"] = i  # re-rank after removal
    except Exception as exc:  # noqa: BLE001
        log.warning("generate_daily_signal: stale filter failed: %s", exc)
    log.info(
        "generate_daily_signal: picks count=%d today=%s signal_for=%s",
        len(picks), today, signal_for,
    )

    # Degeneration guard: all-equal scores mean the model learned nothing
    # today and the "ranking" is just code order. Signals are still stored
    # (the record is factual) but the flag is surfaced in the task result /
    # logs so monitoring can catch a relapse of the constant-score bug.
    topk = LIVE_CONFIG["strategy_kwargs"]["topk"]
    top_picks = [p for p in picks if p["rank"] <= topk]
    unique_scores = len({round(p.get("score") or 0.0, 8) for p in top_picks})
    degenerate = len(top_picks) >= 5 and unique_scores < 5
    if degenerate:
        log.warning(
            "generate_daily_signal: DEGENERATE signal — %d picks share only "
            "%d distinct scores (model best_iteration likely collapsed)",
            len(picks), unique_scores,
        )

    # Why-explanations for the dashboard — best effort, never blocks signals.
    reasons: dict[str, dict] = {}
    try:
        from .signal_reasons import build_reasons
        reasons = build_reasons([p["code"] for p in picks], model=model, dataset=dataset)
    except Exception as exc:  # noqa: BLE001
        log.warning("generate_daily_signal: build_reasons failed: %s", exc)

    with SessionLocal() as db:
        # Idempotent: nuke any prior picks targeting the same trade date.
        db.query(Signal).filter(Signal.as_of == signal_for).delete()
        for p in picks:
            reason = reasons.get(p["code"])
            db.add(Signal(
                as_of=signal_for,
                rank=p["rank"],
                code=p["code"],
                name=p.get("name") or _stock_name(p["code"]),
                score=p.get("score"),
                model_class=LIVE_CONFIG["model_class"],
                strategy_class=LIVE_CONFIG["strategy_class"],
                reasons_json=json.dumps(reason, ensure_ascii=False) if reason else None,
            ))
        db.commit()

    return {
        "as_of": signal_for.isoformat(),
        "generated_on": today.isoformat(),
        "picks": len(picks),
        "unique_scores": unique_scores,
        "degenerate": degenerate,
    }


# ─── Order submission (09:00 KST) ───────────────────────────────────


def _is_risky(code: str, client: KISClient | None = None) -> str | None:
    """Return a reason string if the stock must not be bought — 거래정지,
    관리종목, 투자위험/경고 (KIS quote flags). None = tradable.

    The model only sees prices, so a stock in receivership can score high on
    its FROZEN pre-halt data (콘텐트리중앙 ranked #3 while trade-halted,
    2026-07-30) — this is the safety net in front of the order."""
    try:
        client = client or get_kis_client()
        if client.is_mock:
            return None
        q = client.get_quote(code) or {}
        time.sleep(KIS_THROTTLE_SECONDS)
        if q.get("halted"):
            return "거래정지"
        names = {"51": "관리종목", "52": "투자위험", "53": "투자경고"}
        if q.get("status_code") in names:
            return names[q["status_code"]]
    except Exception as exc:  # noqa: BLE001
        log.warning("_is_risky check failed for %s: %s", code, exc)
    return None


def _select_affordable_buys(candidates: list[str],
                            slot_budget: float,
                            n_drop: int,
                            price_fn=None,
                            risk_fn=None) -> tuple[list[tuple[str, float]], list[str]]:
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
    risk_fn = risk_fn if risk_fn is not None else _is_risky
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
        risk = risk_fn(code)
        if risk:
            log.warning(
                "_select_affordable_buys: %s RISKY (%s) — skipped, next rank "
                "takes the slot", code, risk,
            )
            skipped.append(f"{code}({risk})")
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
    n_topk = LIVE_CONFIG["strategy_kwargs"]["topk"]

    with SessionLocal() as db:
        # rank <= topk: signals persist SIGNAL_STORE_TOP_N (30) ranks for
        # sell-reason context, but trading only ever targets the top-K.
        # The flow overlay is the exception — it needs the WIDE candidate
        # list to re-rank before slicing top-K.
        rank_cutoff = SIGNAL_STORE_TOP_N if strategy == STRATEGY_FLOW else n_topk
        signals = (db.query(Signal)
                     .filter(Signal.as_of == today,
                             Signal.rank <= rank_cutoff)
                     .order_by(Signal.rank.asc())
                     .all())
        if not signals:
            return {"status": "no_signal", "as_of": today.isoformat(),
                    "strategy": strategy, "simulated": simulated}

        flow_info: dict = {}
        flow_metrics: dict[str, dict] = {}
        if strategy == STRATEGY_FLOW:
            signals, flow_metrics, flow_info = _apply_flow_overlay(
                db, signals, today, n_topk)

        if simulated:
            snapshot = _simulated_balance(db, strategy=strategy)
        else:
            client = client or get_kis_client()
            snapshot = client.get_balance()
        held_codes = {h.code for h in snapshot.holdings}
        target_codes = [s.code for s in signals]
        n_drop = LIVE_CONFIG["strategy_kwargs"]["n_drop"]

        if simulated and strategy in BRACKET_STRATEGIES:
            # Bracket-exit mode (2026-08-03): these sim positions exit ONLY
            # via evaluate_bracket_exits (±N% touch after the 15:45 refresh),
            # never on rank dropout. Buys below stay signal-driven.
            to_sell_codes: list[str] = []
        else:
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
            sell_why = _sell_reasons(db, today, code)
            if simulated:
                px = _last_close(code) or holding.eval_price or holding.avg_price
                if not px or px <= 0:
                    continue
                realised = (px - holding.avg_price) * holding.qty
                _persist_simulated_fill(db, today, code, "SELL", holding.qty, px,
                                        strategy=strategy, pnl=realised,
                                        reasons=sell_why)
                submitted += 1
            else:
                res = client.place_order(code, "SELL", holding.qty, price=None)
                _persist_order(db, today, code, "SELL", holding.qty, None, res,
                               strategy=strategy, reasons=sell_why)
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
                buy_why = _buy_reasons(db, today, code, flow=flow_metrics.get(code))
                if simulated:
                    _persist_simulated_fill(db, today, code, "BUY", qty, px,
                                            strategy=strategy, reasons=buy_why)
                    submitted += 1
                else:
                    res = client.place_order(code, "BUY", qty, price=None)
                    _persist_order(db, today, code, "BUY", qty, None, res,
                                   strategy=strategy, reasons=buy_why)
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

        result = {
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
        if strategy == STRATEGY_FLOW:
            result["flow"] = flow_info
        return result


def reconcile_fills(trade_date: date | None = None,
                    strategy: str = STRATEGY_OPEN,
                    client: KISClient | None = None) -> dict:
    """Pin actual fill prices onto today's orders (fill reconciliation).

    Market orders are submitted with price=None, so every display had to
    ESTIMATE the fill from bars — and the estimate drifted as better bars
    arrived (the '확정 손익 keeps changing' bug). KIS knows the real average
    fill; write it into Order.price once and the number is immutable.
    Idempotent: orders that already carry a price are skipped.
    """
    init_db()
    trade_date = trade_date or date.today()
    client = client or get_kis_client()
    if client.is_mock:
        return {"status": "skipped", "reason": "mock"}
    fills = client.get_daily_fills(trade_date, trade_date)
    matched = updated = 0
    with SessionLocal() as db:
        rows = (db.query(Order)
                  .filter(Order.trade_date == trade_date,
                          Order.strategy == strategy,
                          Order.status.in_(("SUBMITTED", "PARTIAL")),
                          Order.kis_order_id.isnot(None))
                  .all())
        for o in rows:
            key = (o.kis_order_id or "").lstrip("0") or o.kis_order_id
            f = fills.get(key)
            if f is None:
                continue
            matched += 1
            if o.price is not None:
                continue  # already pinned
            o.price = f["avg_price"]
            o.status = "FILLED" if f["ccld_qty"] >= o.qty else "PARTIAL"
            updated += 1

        # Pass 2 — the PAPER env returns nothing from daily-ccld ("모의투자
        # 조회할 내역이 없습니다", verified 2026-07-30). Two exact substitutes:
        #  * BUY of a currently-held code: the KIS balance avg IS the real fill.
        #  * An order submitted into the 09:00 opening auction fills AT the
        #    opening price by definition (single-price auction) — take 시가
        #    from the quote endpoint (market-data TRs work on paper).
        pending = [o for o in rows if o.price is None]
        if pending:
            try:
                bal_avg = {h.code: h.avg_price for h in client.get_balance().holdings}
            except Exception:  # noqa: BLE001
                bal_avg = {}
            quote_cache: dict[str, float | None] = {}
            for o in pending:
                px = None
                if o.side == "BUY" and bal_avg.get(o.code):
                    px = bal_avg[o.code]
                else:
                    sub_kst_min = (o.submitted_at.hour * 60 + o.submitted_at.minute + 540) % 1440
                    if 8 * 60 + 55 <= sub_kst_min <= 9 * 60 + 6:  # opening auction window
                        if o.code not in quote_cache:
                            quote_cache[o.code] = (client.get_quote(o.code) or {}).get("open")
                            time.sleep(KIS_THROTTLE_SECONDS)
                        px = quote_cache[o.code]
                if px:
                    o.price = float(px)
                    o.status = "FILLED"
                    updated += 1
        db.commit()
    result = {"status": "ok", "trade_date": trade_date.isoformat(),
              "kis_fills": len(fills), "matched": matched, "updated": updated}
    log.info("reconcile_fills: %s", result)
    return result


# ─── Account sync + daily PnL roll-up ───────────────────────────────


def sync_account(client: KISClient | None = None,
                 trade_date: date | None = None,
                 *,
                 strategy: str = STRATEGY_OPEN) -> dict:
    """Snapshot the per-strategy portfolio and roll up DailyPnL.

    strategy='open' → reads KIS get_balance (real paper account)
    anything else   → reconstructs from that strategy's simulated Fills in the DB
    """
    init_db()
    _reset_qlib_caches()
    trade_date = trade_date or _last_trading_day()
    if strategy != STRATEGY_OPEN:
        with SessionLocal() as db:
            snapshot = _simulated_balance(db, strategy=strategy)
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
                   *, strategy: str = STRATEGY_OPEN,
                   reasons: dict | None = None) -> Order:
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
        reasons_json=json.dumps(reasons, ensure_ascii=False) if reasons else None,
    )
    db.add(o)
    db.flush()  # populate o.id for Fill FK
    return o


def _apply_flow_overlay(db: Session, signals: list[Signal], today: date,
                        n_topk: int) -> tuple[list[Signal], dict[str, dict], dict]:
    """Re-rank the wide candidate list by 기관/외국인 net buying, slice top-K.

    Self-healing: the evening `fetch_market_flow` task normally has the data
    loaded already, but if it was missed (deploy window, KIS hiccup) we fetch
    on demand here. Any failure degrades to the plain model order — a 'flow'
    day without supply/demand data is simply a 'close' day.
    """
    codes = [s.code for s in signals]
    try:
        from . import market_flow
        as_of_flow = _prev_trading_day(today)  # today's rows only exist post-close
        market_flow.ensure_flow_data(db, codes, as_of_flow)
        metrics = market_flow.flow_scores(db, codes, as_of_flow)
        ordered, info = market_flow.rerank(codes, metrics)
    except Exception as exc:  # noqa: BLE001
        log.warning("flow overlay failed — falling back to model order: %s", exc)
        return signals[:n_topk], {}, {"applied": False, "reason": f"error: {exc}"}

    by_code = {s.code: s for s in signals}
    picked = [by_code[c] for c in ordered[:n_topk] if c in by_code]
    info["model_top"] = codes[:n_topk]
    info["flow_top"] = [s.code for s in picked]
    log.info("flow overlay: applied=%s scored=%s dropped=%s model_top=%s flow_top=%s",
             info.get("applied"), info.get("scored"), info.get("dropped"),
             info["model_top"], info["flow_top"])
    return picked, metrics, info


def _buy_reasons(db: Session, as_of: date, code: str,
                 flow: dict | None = None) -> dict | None:
    """Decision basis for a buy = that day's signal reasons + entry rank.

    `flow` (the 'flow' strategy's supply/demand metrics) is folded in when
    present so the dashboard shows what actually drove the pick.
    """
    try:
        sig = (db.query(Signal)
                 .filter(Signal.as_of == as_of, Signal.code == code)
                 .first())
        if sig is None:
            return None
        base = json.loads(sig.reasons_json) if sig.reasons_json else {}
        out = {"action": "buy", "basis": f"신호 {sig.rank}위 진입", **base}
        if flow:
            from .market_flow import flow_summary
            out["basis"] = f"신호 {sig.rank}위 + 수급 상위 진입"
            out["flow"] = flow
            summary = flow_summary(flow)
            if summary:
                out["summary"] = f"{out.get('summary', '')} · {summary}".strip(" ·")
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("_buy_reasons failed for %s: %s", code, exc)
        return None


def _sell_reasons(db: Session, as_of: date, code: str) -> dict | None:
    """Decision basis for a sell — the stock DROPPED OUT of today's top-K.

    Signals persist SIGNAL_STORE_TOP_N (30) ranks, so we can usually say
    exactly where the stock landed ("전일 3위 → 금일 21위" or "30위권 밖")
    plus an at-sale metrics snapshot. Best effort, never blocks orders."""
    topk = LIVE_CONFIG["strategy_kwargs"]["topk"]
    basis = f"당일 신호 top-{topk} 이탈 (보유 유지 근거 소멸)"
    try:
        prev = (db.query(Signal)
                  .filter(Signal.code == code, Signal.as_of < as_of)
                  .order_by(Signal.as_of.desc(), Signal.rank.asc())
                  .first())
        cur = (db.query(Signal)
                 .filter(Signal.code == code, Signal.as_of == as_of)
                 .first())
        prev_txt = f"전일 {prev.rank}위" if prev else "전일 순위 기록 없음"
        cur_txt = f"금일 {cur.rank}위" if cur else f"금일 {SIGNAL_STORE_TOP_N}위권 밖"
        basis = f"당일 신호 top-{topk} 이탈 — {prev_txt} → {cur_txt}"
    except Exception as exc:  # noqa: BLE001
        log.warning("_sell_reasons rank context failed for %s: %s", code, exc)
    try:
        from .signal_reasons import build_intuitive_metrics, summarize
        m = build_intuitive_metrics([code]).get(code, {})
        return {"action": "sell", "basis": basis,
                "summary": summarize(m), "metrics": m, "top_features": []}
    except Exception as exc:  # noqa: BLE001
        log.warning("_sell_reasons failed for %s: %s", code, exc)
        return {"action": "sell", "basis": basis,
                "summary": "", "metrics": {}, "top_features": []}


def _persist_simulated_fill(db: Session, trade_date: date, code: str, side: str,
                            qty: int, price: float, strategy: str = STRATEGY_CLOSE,
                            pnl: float | None = None,
                            reasons: dict | None = None) -> None:
    """Write a paper Order+Fill pair for the close strategy. No KIS round-trip."""
    res = OrderResult(ok=True, order_id=f"SIM-{int(datetime.utcnow().timestamp()*1000)}",
                      code=code, side=side, qty=qty, price=price,
                      raw={"simulated": True}, error=None)
    o = _persist_order(db, trade_date, code, side, qty, price, res, strategy=strategy,
                       reasons=reasons)
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
        holdings.append(Holding(code=code, name=_stock_name(code),
                                qty=p["qty"], avg_price=avg,
                                eval_price=last_px, eval_value=ev_value,
                                pnl=pnl, pnl_pct=pnl_pct))
        total_eval += ev_value
    return AccountSnapshot(cash=cash, total_eval=total_eval, holdings=holdings)


def evaluate_bracket_exits(trade_date: date | None = None,
                           strategy: str = STRATEGY_CLOSE) -> dict:
    """Sim exits driven by the per-strategy EXIT_RULES table:

      - tp: day high touches avg × (1+tp) → sell (tp_fraction<1 sells that
        fraction ONCE — scale's +7% half-sell — remainder rides the trail)
      - trail: effective stop tightens to peak_close × (1−trail) as the
        position's peak rises (peak = entry..yesterday closes, deterministic)
      - structural stop (always on): lowest $low of the `low_window` days
        before entry × (1 − low_buffer), capped at avg × (1 − sl).
        No valid prior low (entry at fresh lows) → the cap is the stop.

    Runs after the 15:45 kr_data refresh so `trade_date`'s own bar exists
    ($open/$high/$low). Idempotent: a position sold in one run disappears
    from the reconstructed balance, so a re-run finds nothing to sell.

    Fill-price conventions (daily-bar approximation):
      - gap through the level at the open → fill at the open
      - otherwise → fill at the bracket level itself
      - day touches BOTH levels → assume the stop hit first (pessimistic),
        counted in `ambiguous`
    Entry-day bars are excluded — the modeled entry is that day's close, so
    the day's intraday range precedes the position.
    """
    init_db()
    _reset_qlib_caches()
    day = trade_date or _last_trading_day()
    rule = EXIT_RULES.get(strategy, {"tp": settings.live_close_bracket_tp})
    tp = rule.get("tp")
    tp_fraction = float(rule.get("tp_fraction", 1.0))
    trail = rule.get("trail")
    sl_cap = settings.live_close_bracket_sl
    low_window = settings.live_close_bracket_low_window
    low_buffer = settings.live_close_bracket_low_buffer
    exits: list[dict] = []
    ambiguous = 0
    with SessionLocal() as db:
        snapshot = _simulated_balance(db, strategy=strategy)
        for h in snapshot.holdings:
            if h.qty <= 0 or h.avg_price <= 0:
                continue
            # Episode entry = first BUY after the last full SELL (sim episodes
            # hold exactly one BUY today, but keep this robust to re-entries).
            last_sell = (db.query(Order)
                           .filter(Order.strategy == strategy,
                                   Order.code == h.code,
                                   Order.side == "SELL")
                           .order_by(Order.trade_date.desc())
                           .first())
            entry_q = (db.query(Order)
                         .filter(Order.strategy == strategy,
                                 Order.code == h.code,
                                 Order.side == "BUY"))
            if last_sell is not None:
                entry_q = entry_q.filter(Order.trade_date > last_sell.trade_date)
            entry = entry_q.order_by(Order.trade_date.asc()).first()
            if entry is None or entry.trade_date >= day:
                continue  # entered at this day's close — range predates entry
            bar = _day_ohlc_any(h.code, day)
            if not bar:
                continue
            if rule.get("stop_source") == "entry":
                # cafe: the screener stored a structural stop on the entry
                # order — out-of-universe codes have no kr_data prev-low.
                prev_low = None
                cap_px = h.avg_price * (1.0 - settings.live_cafe_stop_cap)
                entry_stop = None
                try:
                    entry_stop = (json.loads(entry.reasons_json or "{}")
                                  .get("stop_px"))
                except Exception:  # noqa: BLE001
                    pass
                if entry_stop and float(entry_stop) < h.avg_price and float(entry_stop) > cap_px:
                    sl_px, sl_kind = float(entry_stop), "entry_stop"
                else:
                    sl_px, sl_kind = cap_px, "cap"
            else:
                # Structural stop (all strategies): prev-low with risk cap.
                cap_px = h.avg_price * (1.0 - sl_cap)
                prev_low = _prev_low(h.code, entry.trade_date, low_window)
                low_stop = prev_low * (1.0 - low_buffer) if prev_low else None
                if low_stop is not None and low_stop < h.avg_price and low_stop > cap_px:
                    sl_px, sl_kind = low_stop, "prev_low"
                else:
                    sl_px, sl_kind = cap_px, "cap"
            # Ladder scale-out (scale strategy) — its own branch: staged rung
            # sells with a ratcheting floor one rung below the last fill.
            ladder = rule.get("ladder")
            if ladder:
                floor_gap = float(rule.get("floor_gap", 0.05))
                stage = (db.query(Order)
                           .filter(Order.strategy == strategy,
                                   Order.code == h.code,
                                   Order.side == "SELL",
                                   Order.trade_date >= entry.trade_date)
                           .count())

                def _ladder_sell(qty_s: int, px_s: float, kind_s: str, basis_s: str) -> None:
                    realised_s = (px_s - h.avg_price) * qty_s
                    reasons_s = {"action": "sell", "basis": basis_s,
                                 "summary": "", "metrics": {}, "top_features": []}
                    try:
                        from .signal_reasons import build_intuitive_metrics, summarize
                        m_s = build_intuitive_metrics([h.code]).get(h.code, {})
                        reasons_s["summary"] = summarize(m_s)
                        reasons_s["metrics"] = m_s
                    except Exception as exc:  # noqa: BLE001
                        log.warning("ladder exit reasons failed for %s: %s", h.code, exc)
                    _persist_simulated_fill(db, day, h.code, "SELL", qty_s, px_s,
                                            strategy=strategy, pnl=realised_s,
                                            reasons=reasons_s)
                    exits.append({"code": h.code, "name": _stock_name(h.code),
                                  "kind": kind_s, "qty": qty_s,
                                  "price": round(px_s, 2), "realised": round(realised_s),
                                  "sl_px": round(sl_px, 2), "sl_kind": sl_kind})

                # Ratcheting floor: one rung below the last filled rung.
                if 0 < stage <= len(ladder):
                    floor_px = h.avg_price * (1.0 + ladder[stage - 1] - floor_gap)
                    if floor_px > sl_px:
                        sl_px, sl_kind = floor_px, "ladder_floor"
                # Floor / structural stop wins a both-touch day (pessimistic).
                if bar["low"] <= sl_px:
                    next_rung_px = h.avg_price * (1.0 + ladder[min(stage, len(ladder) - 1)])
                    if bar["high"] >= next_rung_px:
                        ambiguous += 1
                    px = bar["open"] if bar["open"] <= sl_px else sl_px
                    if sl_kind == "ladder_floor":
                        basis = (f"사다리 플로어 이탈 — {stage}차 매도 후 "
                                 f"+{(ladder[stage - 1] - floor_gap) * 100:.0f}% 선"
                                 f"({round(sl_px):,}) 붕괴, 잔여 전량 "
                                 f"(평단 {round(h.avg_price):,} → 체결 {round(px):,})")
                        _ladder_sell(h.qty, px, "플로어 청산", basis)
                    elif sl_kind == "prev_low":
                        basis = (f"브래킷 손절 — 전 저점 {round(prev_low):,}원 이탈 "
                                 f"(손절선 {round(sl_px):,} · 평단 {round(h.avg_price):,} "
                                 f"→ 체결 {round(px):,})")
                        _ladder_sell(h.qty, px, "손절", basis)
                    else:
                        basis = (f"브래킷 손절 −{sl_cap * 100:.1f}% (리스크 캡) — "
                                 f"평단 {round(h.avg_price):,} → 체결 {round(px):,}")
                        _ladder_sell(h.qty, px, "손절", basis)
                    continue
                # Cascade every rung the day's high covers (resting limit
                # sells at each rung would all have filled).
                remaining = h.qty
                while stage < len(ladder) and remaining > 0:
                    rung_px = h.avg_price * (1.0 + ladder[stage])
                    if bar["high"] < rung_px:
                        break
                    fill_px = bar["open"] if bar["open"] > rung_px else rung_px
                    last = stage == len(ladder) - 1
                    qty_s = remaining if (last or remaining <= 1) else max(remaining // 2, 1)
                    tail = "" if (last or qty_s == remaining) else f", 잔여 {remaining - qty_s}주 유지"
                    basis = (f"사다리 {stage + 1}차 익절 +{ladder[stage] * 100:.0f}% — "
                             f"{qty_s}주 매도{tail} "
                             f"(평단 {round(h.avg_price):,} → 체결 {round(fill_px):,})")
                    _ladder_sell(qty_s, fill_px, "익절" if last else "부분 익절", basis)
                    remaining -= qty_s
                    stage += 1
                continue

            # Trailing stop tightens the effective stop as the peak rises.
            if trail:
                peak = _peak_close(h.code, entry.trade_date, day)
                if peak:
                    trail_stop = peak * (1.0 - trail)
                    if trail_stop > sl_px:
                        sl_px, sl_kind = trail_stop, "trail"
            # scale: the +tp% half-sell fires only while the position is still
            # whole — a reduced qty vs the episode's entry means it already did.
            partial_done = tp_fraction < 1.0 and h.qty < entry.qty
            tp_active = tp is not None and not partial_done
            tp_px = h.avg_price * (1.0 + tp) if tp_active else None
            tp_hit = tp_px is not None and bar["high"] >= tp_px
            sl_hit = bar["low"] <= sl_px
            if not tp_hit and not sl_hit:
                continue
            if tp_hit and sl_hit:
                ambiguous += 1
            sell_qty = h.qty
            if sl_hit:
                px = bar["open"] if bar["open"] <= sl_px else sl_px
                kind = "손절" if sl_kind != "trail" else "트레일 청산"
                if sl_kind == "prev_low":
                    basis = (f"브래킷 손절 — 전 저점 {round(prev_low):,}원 이탈 "
                             f"(손절선 {round(sl_px):,} · 평단 {round(h.avg_price):,} "
                             f"→ 체결 {round(px):,})")
                elif sl_kind == "trail":
                    basis = (f"트레일링 청산 — 최고 종가 대비 −{trail * 100:.1f}% 이탈 "
                             f"(청산선 {round(sl_px):,} · 평단 {round(h.avg_price):,} "
                             f"→ 체결 {round(px):,})")
                elif sl_kind == "entry_stop":
                    basis = (f"구조적 손절 이탈 — 스크리너 손절선 {round(sl_px):,}원 "
                             f"(평단 {round(h.avg_price):,} → 체결 {round(px):,})")
                else:
                    basis = (f"브래킷 손절 −{sl_cap * 100:.1f}% (리스크 캡) — "
                             f"평단 {round(h.avg_price):,} → 체결 {round(px):,}")
            else:
                px = bar["open"] if bar["open"] >= tp_px else tp_px
                if tp_fraction < 1.0 and h.qty > 1:
                    sell_qty = max(h.qty // 2, 1)
                    kind = "부분 익절"
                    basis = (f"부분 익절 {tp * 100:+.1f}% — {sell_qty}주 매도, "
                             f"잔여 {h.qty - sell_qty}주 트레일 전환 "
                             f"(평단 {round(h.avg_price):,} → 체결 {round(px):,})")
                else:
                    kind = "익절"
                    basis = (f"브래킷 익절 {tp * 100:+.1f}% 도달 — "
                             f"평단 {round(h.avg_price):,} → 체결 {round(px):,}")
            realised = (px - h.avg_price) * sell_qty
            reasons = {"action": "sell", "basis": basis,
                       "summary": "", "metrics": {}, "top_features": []}
            try:
                from .signal_reasons import build_intuitive_metrics, summarize
                m = build_intuitive_metrics([h.code]).get(h.code, {})
                reasons["summary"] = summarize(m)
                reasons["metrics"] = m
            except Exception as exc:  # noqa: BLE001
                log.warning("bracket exit reasons failed for %s: %s", h.code, exc)
            _persist_simulated_fill(db, day, h.code, "SELL", sell_qty, px,
                                    strategy=strategy, pnl=realised,
                                    reasons=reasons)
            exits.append({"code": h.code, "name": _stock_name(h.code),
                          "kind": kind, "qty": sell_qty,
                          "price": round(px, 2), "realised": round(realised),
                          "sl_px": round(sl_px, 2), "sl_kind": sl_kind})
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "strategy": strategy,
            "rule": rule, "sl_cap": sl_cap, "low_window": low_window,
            "exits": exits, "ambiguous": ambiguous}


def evaluate_limit_entries(trade_date: date | None = None) -> dict:
    """limit strategy entries — the user's own trading style, daily-bar model:

    Rest `live_limit_candidates` virtual limit buys at prev_close × (1 −
    live_limit_discount) for the day's top-10 signal codes not already held.
    A candidate "fills" when the day's low touches its limit price — capped
    at `live_limit_max_fills`, taken in RANK order (daily bars can't tell
    which order touched first; rank priority is the documented convention).
    Gap-down opens below the limit fill at the open (better price). Unfilled
    candidates are simply cancelled — no rows — and re-rested off the next
    day's signal. Runs after the 15:45 refresh (needs the day's own bar).
    Idempotent: filled codes become holdings, so a re-run skips them.
    """
    init_db()
    _reset_qlib_caches()
    day = trade_date or _last_trading_day()
    discount = settings.live_limit_discount
    n_candidates = settings.live_limit_candidates
    max_fills = settings.live_limit_max_fills
    topk = LIVE_CONFIG["strategy_kwargs"]["topk"]

    with SessionLocal() as db:
        signals = (db.query(Signal)
                     .filter(Signal.as_of == day, Signal.rank <= topk)
                     .order_by(Signal.rank.asc())
                     .all())
        if not signals:
            return {"status": "no_signal", "trade_date": day.isoformat(),
                    "strategy": STRATEGY_LIMIT}
        snapshot = _simulated_balance(db, strategy=STRATEGY_LIMIT)
        held = {h.code for h in snapshot.holdings}
        slot_budget = max(snapshot.total_eval, snapshot.cash) / max(topk, 1)

        rested: list[dict] = []  # the virtual order book for today
        for s in signals:
            if s.code in held or len(rested) >= n_candidates:
                continue
            bar = _day_ohlc(s.code, day)
            prev_close = _prev_close_before(s.code, day)
            if not bar or not prev_close:
                continue
            limit_px = round_to_tick(prev_close * (1.0 - discount))
            if limit_px <= 0 or limit_px > slot_budget:
                continue  # affordability: one share must fit the slot
            filled = bar["low"] <= limit_px
            fill_px = bar["open"] if (filled and bar["open"] < limit_px) else limit_px
            rested.append({"code": s.code, "rank": s.rank, "limit_px": limit_px,
                           "prev_close": prev_close, "filled": filled,
                           "fill_px": fill_px if filled else None})

        fills = [r for r in rested if r["filled"]][:max_fills]
        cancelled = len(rested) - len(fills)
        cash = snapshot.cash
        if fills:
            per_code_budget = min(cash / len(fills), slot_budget)
            for f in fills:
                qty = max(int(per_code_budget // f["fill_px"]), 0)
                if qty <= 0:
                    f["qty"] = 0
                    continue
                f["qty"] = qty
                buy_why = _buy_reasons(db, day, f["code"])
                base = (f"지정가 −{discount * 100:.1f}% 체결 — 예약가 "
                        f"{f['limit_px']:,} (전일종가 {round(f['prev_close']):,}, "
                        f"신호 {f['rank']}위)")
                if buy_why:
                    buy_why["basis"] = base + (" · " + buy_why.get("basis", "") if buy_why.get("basis") else "")
                else:
                    buy_why = {"action": "buy", "basis": base,
                               "summary": "", "metrics": {}, "top_features": []}
                _persist_simulated_fill(db, day, f["code"], "BUY", qty, f["fill_px"],
                                        strategy=STRATEGY_LIMIT, reasons=buy_why)
        db.commit()
    return {"status": "ok", "trade_date": day.isoformat(), "strategy": STRATEGY_LIMIT,
            "discount": discount, "rested": len(rested), "cancelled": cancelled,
            "fills": [{k: f[k] for k in ("code", "rank", "limit_px", "fill_px")} | {"qty": f.get("qty", 0)}
                      for f in fills]}


def _prev_close_before(code: str, day: date) -> float | None:
    """Close of the last trading day strictly before `day`."""
    try:
        from qlib.data import D
        df = D.features([f"{code}"], ["$close"], end_time=day.isoformat(), freq="day")
        if df is None or df.empty:
            return None
        s = df.droplevel("instrument")["$close"].dropna()
        s = s[s.index.map(lambda ts: pd.Timestamp(ts).date() < day)]
        if s.empty:
            return None
        v = float(s.iloc[-1])
        return v if v > 0 else None
    except Exception:  # noqa: BLE001
        return None


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


def _stale_codes(codes: list[str], today: date, max_lag_days: int = 5) -> set[str]:
    """Codes whose LAST real bar is max_lag_days+ trading days behind `today`
    — i.e. price data froze (trade halt / delisting). Batched via one
    D.features call over a recent window."""
    from qlib.data import D
    lookback_start = (pd.Timestamp(today) - pd.Timedelta(days=max_lag_days * 3)).date()
    df = D.features(list(codes), ["$close"], start_time=lookback_start.isoformat(),
                    end_time=today.isoformat(), freq="day")
    stale: set[str] = set(codes)  # no rows at all in window = stale
    if df is None or df.empty:
        return stale
    cal = [pd.Timestamp(c).date() for c in D.calendar(
        start_time=lookback_start.isoformat(), end_time=today.isoformat())]
    if len(cal) < max_lag_days:
        return set()
    cutoff = cal[-max_lag_days]  # must have a bar on/after this trading day
    closes = df["$close"] if "$close" in df.columns else df.iloc[:, 0]
    for code, sub in closes.groupby(level="instrument"):
        s = sub.droplevel("instrument").dropna()
        if not s.empty and s.index.max().date() >= cutoff:
            stale.discard(str(code))
    return stale


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


def _peak_close(code: str, entry_date: date, day: date) -> float | None:
    """Highest $close from entry_date through the day BEFORE `day`.

    The trailing-stop anchor. Deterministic from bars, so re-runs (fallback
    beat) compute the same stop. Today's close is excluded — the trail
    updates end-of-day, so today's touch is judged against yesterday's peak."""
    try:
        from qlib.data import D
        cal = D.calendar(start_time=entry_date.isoformat(), end_time=day.isoformat())
        days = [pd.Timestamp(c).date() for c in cal if pd.Timestamp(c).date() < day]
        if not days:
            return None
        df = D.features([f"{code}"], ["$close"],
                        start_time=days[0].isoformat(), end_time=days[-1].isoformat(),
                        freq="day")
        if df is None or df.empty:
            return None
        v = float(df["$close"].max())
        return v if v > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _day_ohlc_any(code: str, day: date) -> dict | None:
    """OHLC bar for `day` — kr_data first, KIS daily-price fallback.

    The fallback exists for out-of-universe codes (cafe strategy): their bars
    never land in the local store, so the bracket judgment reads KIS instead.
    Gated like every KIS call; None on both-source miss."""
    bar = _day_ohlc(code, day)
    if bar:
        return bar
    try:
        rows = get_kis_client().get_daily_bars(code)
        iso = day.isoformat()
        for b in rows:
            if b["date"] == iso:
                if all(b[k] > 0 for k in ("open", "high", "low", "close")):
                    return {k: b[k] for k in ("open", "high", "low", "close")}
                return None
    except Exception:  # noqa: BLE001
        pass
    return None


def _prev_low(code: str, entry_date: date, window: int) -> float | None:
    """Lowest $low over the `window` trading days strictly BEFORE entry_date.

    The structural-stop anchor: a close-bet position is invalidated when the
    day range breaks the low it based its entry on."""
    try:
        from qlib.data import D
        cal = D.calendar(end_time=entry_date.isoformat())
        days = [pd.Timestamp(c).date() for c in cal]
        days = [d for d in days if d < entry_date][-window:]
        if not days:
            return None
        df = D.features([f"{code}"], ["$low"], start_time=days[0].isoformat(),
                        end_time=days[-1].isoformat(), freq="day")
        if df is None or df.empty:
            return None
        v = float(df["$low"].min())
        return v if v > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _day_ohlc(code: str, day: date) -> dict | None:
    """One day's OHLC bar for a code, or None if missing/invalid."""
    try:
        from qlib.data import D
        df = D.features([f"{code}"], ["$open", "$high", "$low", "$close"],
                        start_time=day.isoformat(), end_time=day.isoformat(),
                        freq="day")
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        bar = {"open": float(row["$open"]), "high": float(row["$high"]),
               "low": float(row["$low"]), "close": float(row["$close"])}
        if any(v <= 0 for v in bar.values()):
            return None
        return bar
    except Exception:  # noqa: BLE001
        return None
