"""Celery tasks: backtests + live trading loop."""

import functools
import logging
from datetime import date

from .celery_app import celery_app
from ..services.backtest_service import run_backtest

log = logging.getLogger(__name__)


def market_day_only(fn):
    """Skip a scheduled task when KRX is closed.

    Beat fires on `day_of_week="mon-fri"` and has no notion of Korean market
    holidays. On 2026-08-17 (광복절 대체공휴일) that sent four real orders into
    a closed market — KIS refused them, which was luck rather than design:
    the simulated strategies have no broker to refuse them, and KIS quotes
    answer on a holiday with the previous session's price and halted=False.
    They would have written fills, a position snapshot and a PnL row for a day
    the market never opened.

    Fails OPEN: `is_market_open` returning None (redis down, KIS unreachable,
    no credentials) runs the task as before. Losing a session to a broken
    calendar lookup would be worse than the phantom row this guards against.
    """
    @functools.wraps(fn)
    def _wrapped(self, *args, **kwargs):
        from ..services.trading_calendar import is_market_open
        today = date.today()
        if is_market_open(today) is False:
            log.info("%s skipped — KRX closed on %s", fn.__name__, today.isoformat())
            return {"status": "market_closed", "date": today.isoformat()}
        return fn(self, *args, **kwargs)
    return _wrapped


@celery_app.task(bind=True, name="run_backtest")
def run_backtest_task(self, config: dict) -> dict:
    """Execute a qlib backtest in an isolated worker process.

    The worker process has its own qlib.init() (via worker_process_init signal),
    so the global C singleton is safe from concurrent mutation.
    """
    self.update_state(state="RUNNING")
    return run_backtest(config)


# ─── Live trading (KIS) ─────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="live_signal",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=2,
)
@market_day_only
def live_signal_task(self) -> dict:
    """Post-close — train/score → persist top-K Signal rows for tomorrow's open.

    Primary trigger: chained from refresh_kr_data (so it always trains on a
    calendar that includes today). The 16:20 beat entry is a fallback and is
    safe to double-fire — signal writes are idempotent per as_of.
    """
    from ..services.live_trader import generate_daily_signal
    from ..services.notify import notify_signal
    self.update_state(state="RUNNING")
    result = generate_daily_signal()
    notify_signal(result)
    return result


@celery_app.task(bind=True, name="live_orders")
@market_day_only
def live_orders_task(self) -> dict:
    """09:00 KST — open strategy: read today's Signal, submit KIS orders."""
    from ..services.live_trader import submit_daily_orders
    from ..services.notify import notify_open_orders
    self.update_state(state="RUNNING")
    result = submit_daily_orders(strategy="open", simulated=False)
    notify_open_orders(result)
    return result


@celery_app.task(bind=True, name="cancel_unfilled_orders")
@market_day_only
def cancel_unfilled_orders_task(self) -> dict:
    """Every 10 min during the session — retire resting limit orders whose
    account cutoff has passed.

    A sweep rather than one scheduled slot: the cutoff is per-account data
    (and per side), so there is no single time beat could fire at. Ticks with
    nothing due cost one indexed query and return.

    Sweeps EVERY account. It used to call the function bare, which defaulted to
    account_id="main" — and main is 시장가 with no cutoff, so the sweep returned
    at `no_cutoff` every single tick. The cafe account's seeded 15:30 buy cutoff
    had therefore never once executed, leaving unfilled cafereal limits resting
    into the close. Iterating the account map also means a third account starts
    being swept the day it is added, with no beat entry to remember.
    """
    from ..db import ACCOUNT_STRATEGIES
    from ..services.live_trader import cancel_unfilled_orders
    self.update_state(state="RUNNING")
    return {account_id: cancel_unfilled_orders(account_id=account_id)
            for account_id in ACCOUNT_STRATEGIES}


@celery_app.task(
    bind=True,
    name="live_sync",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
@market_day_only
def live_sync_task(self) -> dict:
    """09:30 + 15:40 KST — open strategy: pull KIS balance, snapshot, PnL.

    Balance reads are idempotent, so retrying on transient KIS errors is
    safe. live_orders deliberately has NO autoretry — order submission is
    not idempotent (a retry after a partial run would double-buy); its
    failure modes are handled inside the token layer instead.
    """
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="open")


@celery_app.task(bind=True, name="live_orders_close")
@market_day_only
def live_orders_close_task(self) -> dict:
    """15:20 KST — close strategy: simulate fills priced at the kr_data close.

    Uses the same Signal(as_of=today) the open task consumed at 09:00, so the
    only variable is execution timing.
    """
    from ..services.live_trader import submit_daily_orders
    self.update_state(state="RUNNING")
    return submit_daily_orders(strategy="close", simulated=True)


@celery_app.task(bind=True, name="live_sync_close")
@market_day_only
def live_sync_close_task(self) -> dict:
    """15:40 KST — close strategy: snapshot the simulated portfolio, PnL roll-up."""
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="close")


@celery_app.task(bind=True, name="live_orders_trail")
@market_day_only
def live_orders_trail_task(self) -> dict:
    """15:20 KST — trail strategy: same close-priced buys as `close`; exits are
    a −7% trailing stop (no fixed TP) via the shared bracket loop."""
    from ..services.live_trader import submit_daily_orders
    self.update_state(state="RUNNING")
    return submit_daily_orders(strategy="trail", simulated=True)


@celery_app.task(bind=True, name="live_sync_trail")
@market_day_only
def live_sync_trail_task(self) -> dict:
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="trail")


@celery_app.task(bind=True, name="live_orders_scale")
@market_day_only
def live_orders_scale_task(self) -> dict:
    """15:20 KST — scale strategy: close-priced buys; +7% sells half, the
    remainder rides the −7% trail (shared bracket loop)."""
    from ..services.live_trader import submit_daily_orders
    self.update_state(state="RUNNING")
    return submit_daily_orders(strategy="scale", simulated=True)


@celery_app.task(bind=True, name="live_sync_scale")
@market_day_only
def live_sync_scale_task(self) -> dict:
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="scale")


@celery_app.task(bind=True, name="live_orders_flow")
@market_day_only
def live_orders_flow_task(self) -> dict:
    """15:20 KST — flow strategy: same close-priced simulation as `close`, but
    the picks are the top-30 signal re-ranked by 기관/외국인 net buying."""
    from ..services.live_trader import submit_daily_orders
    self.update_state(state="RUNNING")
    return submit_daily_orders(strategy="flow", simulated=True)


@celery_app.task(bind=True, name="live_sync_flow")
@market_day_only
def live_sync_flow_task(self) -> dict:
    """15:40 KST — flow strategy: snapshot the simulated portfolio, PnL roll-up."""
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="flow")


@celery_app.task(
    bind=True,
    name="fetch_market_flow",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=2,
)
@market_day_only
def fetch_market_flow_task(self) -> dict:
    """Load 기관/외국인 net-buy rows for the candidates the flow overlay will use.

    Primary trigger: chained after live_signal, which is when we first know
    tomorrow's top-30. The 18:10 beat entry is a fallback. KIS only publishes
    the current day's investor row AFTER the close, so both slots see it.
    Idempotent — codes already covered for the day are skipped.
    """
    from ..db import SessionLocal, Signal, init_db
    from ..services.live_trader import (
        SIGNAL_STORE_TOP_N, _last_trading_day, _next_trading_day, _reset_qlib_caches,
    )
    from ..services.market_flow import ensure_flow_data

    self.update_state(state="RUNNING")
    init_db()
    _reset_qlib_caches()
    today = _last_trading_day()
    signal_for = _next_trading_day(today)
    with SessionLocal() as db:
        codes = [c for (c,) in db.query(Signal.code)
                                 .filter(Signal.as_of == signal_for,
                                         Signal.rank <= SIGNAL_STORE_TOP_N)
                                 .order_by(Signal.rank.asc())
                                 .all()]
        if not codes:
            return {"status": "no_signal", "as_of": signal_for.isoformat()}
        return ensure_flow_data(db, codes, today)


@celery_app.task(
    bind=True,
    name="cafe_screen",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,
)
@market_day_only
def cafe_screen_task(self) -> dict:
    """15:05 KST — recommender-mimic screener: KIS ranking pools → pattern
    classifier → today's cafe candidates (with structural stops)."""
    from ..services.market_screener import run_screener
    from ..services.notify import notify_scout
    self.update_state(state="RUNNING")
    result = run_screener()
    notify_scout(result, slot_label="15:05", final=True)
    return result


@celery_app.task(
    bind=True,
    name="cafe_scout",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=1,
)
@market_day_only
def cafe_scout_task(self, slot: str) -> dict:
    """14:30 / 15:00 KST — observation-only screener scans (no trades).

    Accumulates early-scan candidates + prices to measure whether cafe
    entries could move earlier than 15:28 (pick overlap + drift to close)."""
    from ..services.market_screener import run_scout_scan
    from ..services.notify import notify_scout
    self.update_state(state="RUNNING")
    result = run_scout_scan(slot)
    notify_scout(result, slot_label=f"{slot[:2]}:{slot[2:]}")
    return result


@celery_app.task(
    bind=True,
    name="surge_screen",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=1,
)
@market_day_only
def surge_screen_task(self) -> dict:
    """15:12 KST — score today's pool snapshots with the surge-eve profile,
    store the TOP10 (reads DB only — no KIS calls)."""
    from ..services.market_screener import run_surge_screen
    from ..services.notify import notify_surge_picks
    self.update_state(state="RUNNING")
    result = run_surge_screen()
    notify_surge_picks(result)
    return result


@celery_app.task(bind=True, name="live_orders_surge")
@market_day_only
def live_orders_surge_task(self) -> dict:
    """15:29 KST — surge strategy: sim-buy today's top surge picks."""
    from ..services.market_screener import submit_surge_orders
    self.update_state(state="RUNNING")
    return submit_surge_orders()


@celery_app.task(bind=True, name="live_sync_surge")
@market_day_only
def live_sync_surge_task(self) -> dict:
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="surge")


@celery_app.task(bind=True, name="live_orders_cafe")
@market_day_only
def live_orders_cafe_task(self) -> dict:
    """15:28 KST — cafe strategy: sim-buy today's top candidates at the
    current KIS quote (≈ closing auction price)."""
    from ..services.market_screener import submit_cafe_orders
    self.update_state(state="RUNNING")
    return submit_cafe_orders()


@celery_app.task(bind=True, name="live_orders_cafereal")
@market_day_only
def live_orders_cafereal_task(self) -> dict:
    """15:28 KST — cafereal: cafe 와 같은 픽을 **실계좌**로 매수.

    카페 계좌(KIS_CAFE_*)가 설정돼 있지 않으면 no_account 로 조용히 끝난다 —
    선택 전략이므로 미설정이 실패는 아니다.
    """
    from ..services.market_screener import submit_cafereal_orders
    self.update_state(state="RUNNING")
    return submit_cafereal_orders()


@celery_app.task(bind=True, name="live_sync_cafereal")
@market_day_only
def live_sync_cafereal_task(self) -> dict:
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="cafereal")


@celery_app.task(bind=True, name="live_orders_cafecool")
@market_day_only
def live_orders_cafecool_task(self) -> dict:
    """15:28 KST — cafecool: cafe 와 같은 후보에서 ret20 상한 초과분만 뺀 쌍둥이.

    cafe 와 같은 분에 돈다. 같은 `cafe_candidates` 행을 읽으므로 스캔은 한 번뿐이고,
    두 곡선의 차이는 진입 조건 하나로 한정된다.
    """
    from ..services.market_screener import submit_cafecool_orders
    self.update_state(state="RUNNING")
    return submit_cafecool_orders()


@celery_app.task(bind=True, name="live_sync_cafecool")
@market_day_only
def live_sync_cafecool_task(self) -> dict:
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="cafecool")


@celery_app.task(bind=True, name="live_sync_cafe")
@market_day_only
def live_sync_cafe_task(self) -> dict:
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="cafe")


@celery_app.task(
    bind=True,
    name="capture_orderbook",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=1,
)
@market_day_only
def capture_orderbook_task(self, slot: str) -> dict:
    """15:07 / 15:27 KST — book depth for today's cafe candidates.

    Research only. 15:07 is 정규장 (real ask ladder), 15:27 is 동시호가
    (예상체결수량). Together they say whether the 15:28 sim buy could have
    filled 장중 and whether it could have filled at the close."""
    from ..services.market_screener import capture_orderbook
    self.update_state(state="RUNNING")
    return capture_orderbook(slot)


@celery_app.task(bind=True, name="live_orders_cafeopen")
@market_day_only
def live_orders_cafeopen_task(self) -> dict:
    """09:00 KST — cafeopen twin: rest a −3% limit off today's open for every
    code cafe bought yesterday. No fill yet; resolve_cafeopen judges at 10:00."""
    from ..services.live_trader import submit_cafeopen_orders
    self.update_state(state="RUNNING")
    return submit_cafeopen_orders()


@celery_app.task(bind=True, name="resolve_cafeopen")
@market_day_only
def resolve_cafeopen_task(self) -> dict:
    """10:00 KST — fill the resting cafeopen limits the 09:00~10:00 range
    touched, cancel the rest. Idempotent (PENDING rows only)."""
    from ..services.live_trader import resolve_cafeopen_orders
    self.update_state(state="RUNNING")
    return resolve_cafeopen_orders()


@celery_app.task(bind=True, name="live_sync_cafeopen")
@market_day_only
def live_sync_cafeopen_task(self) -> dict:
    from ..services.live_trader import sync_account
    self.update_state(state="RUNNING")
    return sync_account(strategy="cafeopen")


@celery_app.task(bind=True, name="close_bracket_exits")
@market_day_only
def close_bracket_exits_task(self) -> dict:
    """Chained after refresh_kr_data (+ 16:25 fallback beat) — exit-rule matrix
    for every sim strategy (close/flow: TP+prev-low, trail: −7% trailing,
    scale: +7% half then trail, limit: TP+prev-low).

    Must run AFTER the day's bars land: the touch test reads that day's
    $open/$high/$low. Idempotent (sold positions leave the reconstructed
    balance), so the fallback re-run is harmless. Re-syncs each snapshot
    afterwards so daily_pnl reflects same-day exits.

    limit strategy ALSO buys here (exits first frees cash): its −3% resting
    limit fills are judged against the same day bar, which only exists after
    the refresh — hence no 15:20 buy slot for limit.
    """
    from ..services.live_trader import (
        BRACKET_STRATEGIES, evaluate_bracket_exits, evaluate_limit_entries,
        sync_account,
    )
    from ..services.notify import notify_bracket_exits
    self.update_state(state="RUNNING")
    results = {}
    for strategy in BRACKET_STRATEGIES:
        results[strategy] = evaluate_bracket_exits(strategy=strategy)
        notify_bracket_exits(strategy, results[strategy])
        sync_account(strategy=strategy)
    results["limit_entries"] = evaluate_limit_entries()
    sync_account(strategy="limit")
    return results


@celery_app.task(
    bind=True,
    name="reconcile_fills",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
@market_day_only
def reconcile_fills_task(self) -> dict:
    """09:20 KST — pin actual KIS fill prices onto this morning's orders.

    Idempotent read-modify-write; retries are safe. Freezes realized pnl
    (the exits card used to re-estimate it from bars all day)."""
    from ..services.live_trader import reconcile_fills
    from ..services.notify import notify_reconcile
    self.update_state(state="RUNNING")
    result = reconcile_fills()
    notify_reconcile(result if isinstance(result, dict) else {})
    return result


@celery_app.task(
    bind=True,
    name="reconcile_fills_cafereal",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
@market_day_only
def reconcile_fills_cafereal_task(self, prev_day: bool = False) -> dict:
    """cafereal 실주문 대사. 15:35(당일) + 익일 09:05(재확인) 두 번 돈다.

    cafereal 은 15:28 에 −3% 지정가를 낸다. 상한가 종목이 대부분이라 대개
    체결되지 않는데, **그 체결률이 이 실험의 측정값 자체**다. 지금까지는
    reconcile_fills 를 부르는 곳이 open 하나뿐이라 cafereal 주문이 영구히
    SUBMITTED 로 남았고, "안 샀다"와 "샀는지 모른다"를 구분할 수 없었다.

    두 번 도는 이유: 15:30 동시호가 체결이 KIS 조회에 반영되는 시점이
    확정적이지 않다. 15:35 에 못 잡으면 다음 날 아침에 다시 잡는다.
    Idempotent 하므로 두 번 도는 것 자체는 무해하다.

    `prev_day=True` 면 직전 거래일을 대사한다 — 익일 아침 슬롯용.
    """
    from datetime import date

    from ..db import STRATEGY_CAFEREAL
    from ..services.live_trader import _prev_trading_day, reconcile_fills
    from ..services.notify import notify_reconcile
    self.update_state(state="RUNNING")
    day = _prev_trading_day(date.today()) if prev_day else date.today()
    result = reconcile_fills(day, strategy=STRATEGY_CAFEREAL)
    slot = "익일 09:05 재확인" if prev_day else "15:35 대사"
    notify_reconcile(result if isinstance(result, dict) else {},
                     strategy=STRATEGY_CAFEREAL, slot_label=slot)
    return result


@celery_app.task(
    bind=True,
    name="refresh_kr_data",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
@market_day_only
def refresh_kr_data_task(self) -> dict:
    """15:45 KST — incrementally extend kr_data so tomorrow's live_signal sees today's bars.

    On a successful refresh that actually appended calendar days, chain the
    signal generation immediately — this guarantees live_signal always runs
    AFTER today's bars landed (the old independent 15:35 slot trained on a
    calendar ending yesterday, and its as_of drifted from the 09:00 reader).
    """
    from ..services.kr_data_refresh import refresh_kr_data
    self.update_state(state="RUNNING")
    result = refresh_kr_data()
    if result.get("status") == "ok" and result.get("new_calendar_days", 0) > 0:
        # Flow data is fetched for the codes live_signal is about to store, so
        # it has to queue behind it rather than beside it.
        live_signal_task.apply_async(link=fetch_market_flow_task.si())
        close_bracket_exits_task.delay()
    return result
