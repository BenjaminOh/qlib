#!/usr/bin/env python
"""진입 방식 A/B 체결 엔진 — 시장가 vs 지정가 −d%.

`backtest_signals.py` 가 만든 랭킹 위에서 **진입 방식만** 바꿔 포트폴리오를 굴린다.
종목 선정·수량 산식·청산 규칙은 양쪽 동일하다. 그래야 차이가 진입에서만 온다.

## 왜 운영 `submit_daily_orders` 를 직접 태우지 않는가

계획 단계에서는 임시 SQLite 에 운영 경로를 그대로 태우려 했다. 실제로 짜보니
**지정가 미체결을 표현할 수 없다**: 시뮬 경로의 체결가는 `_sim_fill_price` 하나로
정해지고(`live_trader.py:730-738`), 그게 None 을 돌려주면 `px = qpx or px` 로
**전일 종가에 그냥 사버린다**(`:738`). "안 샀다"를 표현할 자리가 없다.

그래서 일일 루프는 직접 굴리되 **판정·비용·틱 규칙은 운영 함수를 그대로 import** 한다:
  * 체결 판정 — `evaluate_limit_entries`(`:2372-2373`)와 **같은 두 줄**
  * `round_to_tick`(kis_client.py:232) · `trade_cost`(live_trader.py:2417)
  * `_buy_count`(`:415`) — 빈 슬롯 계산까지 운영과 동일
재구현한 것은 현금·보유 장부뿐이고, 그게 맞는지는 **실거래 구간 역산**으로 검증한다
(`--start 2026-07-28 --end 2026-08-25 --mode market` → `open` 실적 ±2%p).

## 양쪽을 맞춰둔 것 (안 맞추면 A/B 가 깨진다)

  * **종목당 예산 분모를 매수 *계획* 건수로 고정**한다. 운영은 시장가가 `len(to_buy)`,
    지정가가 `len(fills)` 로 서로 다른데(`:728` vs `:2383`), 그러면 체결 종목이 적은
    쪽이 종목당 예산을 더 받아 진입가 외의 변수가 끼어든다.
  * 청산은 양쪽 다 **랭크 이탈**. 운영 `limit` 은 브래킷으로만 나가지만
    (`STRATEGY_LIMIT ∈ BRACKET_STRATEGIES`), 그건 진입이 아니라 청산 차이다.
  * 미체결 종목의 슬롯은 **비워둔다.** 다음 랭크가 대신 들어오지 않는다 —
    `open` 이 그 종목을 못 산 것이 이 실험이 재려는 비용 그 자체다.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date as dt_date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

log = logging.getLogger("backtest_entry_modes")


def parse_mode(mode: str) -> tuple[str, float | None]:
    """'market' | 'limitN' (N = 할인 %) | 'switchN' (레짐이면 limitN, 아니면 market)."""
    if mode == "market":
        return "market", None
    if mode.startswith("limit"):
        return "limit", float(mode[5:]) / 100.0
    if mode.startswith("switch"):
        return "switch", float(mode[6:]) / 100.0
    raise SystemExit(f"알 수 없는 모드: {mode}")


def load_regime(path: str | None) -> dict[str, dict]:
    """날짜 → {sp, nq, ks, kq}. 없으면 빈 dict."""
    if not path:
        return {}
    import pandas as pd
    df = pd.read_parquet(path)
    return {r["date"]: {k: r[k] for k in ("sp", "nq", "ks", "kq") if k in r}
            for _, r in df.iterrows()}


def wants_limit(day, regime: dict, signal: str, threshold: float) -> bool:
    """오늘 지정가를 쓸 것인가.

    `signal="nq"|"sp"` 는 **전날 미국 종가**라 09:00 에 알 수 있다(예측).
    `signal="kq"|"ks"` 는 그날 한국 실제 등락이라 **알 수 없다** — 오라클
    상한선을 재는 용도로만 허용한다. 그 차이가 곧 예측 손실이다.
    """
    row = regime.get(day.isoformat())
    if not row:
        return False              # 정보가 없으면 기본값(시장가)을 유지한다
    v = row.get(signal)
    if v is None or v != v:       # NaN
        return False
    return float(v) < threshold


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default="/tmp/bt_signals")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--mode", required=True, help="market | limit2 | limit3 | limit4 | limit5")
    ap.add_argument("--seed", type=float, default=10_000_000.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--regime", default=None, help="backtest_regime_data.py 산출 parquet")
    ap.add_argument("--switch-signal", default="nq", choices=["nq", "sp", "kq", "ks"],
                    help="kq/ks 는 그날 실제 등락 = 사후 오라클(상한선) 전용")
    ap.add_argument("--switch-threshold", type=float, default=-0.01,
                    help="이 값 미만이면 지정가. 예: -0.01 = 나스닥 −1%% 초과 하락")
    args = ap.parse_args()
    kind, disc = parse_mode(args.mode)
    regime = load_regime(args.regime)
    if kind == "switch" and not regime:
        raise SystemExit("switch 모드에는 --regime 이 필요하다")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()

    from qlib.data import D
    from app.api.services.kis_client import round_to_tick
    from app.api.services.live_trader import (
        LIVE_CONFIG, _buy_count, _day_ohlc, _prev_close_before, _prev_trading_day,
        trade_cost,
    )

    topk = LIVE_CONFIG["strategy_kwargs"]["topk"]
    n_drop = LIVE_CONFIG["strategy_kwargs"]["n_drop"]
    sig_dir = Path(args.signals)

    days = [pd.Timestamp(c).date() for c in D.calendar(start_time=args.start, end_time=args.end)]
    cash = args.seed
    pos: dict[str, dict] = {}          # code -> {"qty": int, "cost": float}
    rows, trades = [], []
    skipped_days = 0

    for day in days:
        # 오늘 아침에 쓰는 신호는 **직전 거래일 저녁**에 만들어진 것이다.
        f = sig_dir / f"{_prev_trading_day(day).isoformat()}.parquet"
        if not f.exists():
            skipped_days += 1
            continue
        # switch 모드는 **날마다** 진입 방식을 고른다.
        day_kind = kind
        if kind == "switch":
            day_kind = ("limit" if wants_limit(day, regime, args.switch_signal,
                                               args.switch_threshold) else "market")

        sig = pd.read_parquet(f).sort_values("rank")
        ranks = dict(zip(sig["code"], sig["rank"]))
        target = list(sig[sig["rank"] <= topk]["code"])

        bars = {c: _day_ohlc(c, day) for c in set(target) | set(pos)}

        # ── 1) 매도 — 랭크 이탈. 오늘 순위가 나쁜 것부터(_rank_sorted_sells 와 동일 규칙)
        held = set(pos)
        dropped = held - set(target)
        # 30위권 밖은 SIGNAL_STORE_TOP_N+1 로 취급 (_today_rank 와 같다)
        worst = max(sig["rank"].max() + 1, 31)
        to_sell = sorted(dropped, key=lambda c: (-ranks.get(c, worst), c))[:n_drop]
        for code in to_sell:
            bar = bars.get(code)
            if not bar:
                continue
            px, qty = bar["open"], pos[code]["qty"]
            fee = trade_cost("SELL", qty, px)
            cash += qty * px - fee
            trades.append({"date": day.isoformat(), "side": "SELL", "code": code,
                           "qty": qty, "px": px, "filled": True})
            pos.pop(code)

        # ── 2) 매수
        equity = cash + sum(p["qty"] * (bars.get(c) or {}).get("close", 0) for c, p in pos.items())
        slot_budget = equity / max(topk, 1)
        n_buy = _buy_count(held=len(pos), selling=0, topk=topk, n_drop=n_drop, simulated=False)
        cands = [c for c in target if c not in pos]
        # _select_affordable_buys 와 같은 규칙: 1주 값이 슬롯 예산을 넘으면 건너뛴다
        planned = []
        for c in cands:
            if len(planned) >= n_buy:
                break
            bar = bars.get(c)
            if not bar:
                continue
            ref = bar["open"] if day_kind == "market" else (_prev_close_before(c, day) or bar["open"])
            if ref > slot_budget:
                continue
            planned.append(c)
        # 분모는 **계획 건수**로 고정 — 체결 결과가 종목당 예산을 바꾸면 안 된다
        per_code = min(cash / max(len(planned), 1), slot_budget) if planned else 0.0

        for code in planned:
            bar = bars[code]
            if day_kind == "market":
                fill_px, filled = bar["open"], True
            else:
                prev_c = _prev_close_before(code, day)
                if not prev_c:
                    continue
                limit_px = float(round_to_tick(prev_c * (1.0 - disc)))
                # evaluate_limit_entries(live_trader.py:2372-2373) 와 같은 두 줄
                filled = bar["low"] <= limit_px
                fill_px = bar["open"] if (filled and bar["open"] < limit_px) else limit_px
            if not filled:
                trades.append({"date": day.isoformat(), "side": "BUY", "code": code,
                               "qty": 0, "px": fill_px, "filled": False})
                continue
            qty = int(per_code // fill_px)
            if qty <= 0:
                continue
            fee = trade_cost("BUY", qty, fill_px)
            if qty * fill_px + fee > cash:
                continue
            cash -= qty * fill_px + fee
            pos[code] = {"qty": qty, "cost": qty * fill_px}
            trades.append({"date": day.isoformat(), "side": "BUY", "code": code,
                           "qty": qty, "px": fill_px, "filled": True})

        eq = cash + sum(p["qty"] * (bars.get(c) or {}).get("close", 0) for c, p in pos.items())
        rows.append({"date": day.isoformat(), "cash": cash, "equity": eq, "n_pos": len(pos)})

    if not rows:
        log.error("신호 파일이 하나도 없다 — --signals 경로를 확인하라")
        return 1

    eq = pd.DataFrame(rows)
    tr = pd.DataFrame(trades)
    buys = tr[tr["side"] == "BUY"] if not tr.empty else tr
    n_fill = int(buys["filled"].sum()) if not buys.empty else 0
    n_try = len(buys)
    ret = eq["equity"].iloc[-1] / args.seed - 1
    mdd = float((eq["equity"] / eq["equity"].cummax() - 1).min())

    limit_days = sum(1 for d in eq["date"]
                     if kind == "limit"
                     or (kind == "switch" and wants_limit(
                         dt_date.fromisoformat(d), regime, args.switch_signal,
                         args.switch_threshold)))
    summary = {
        "mode": args.mode,
        "switch": (f"{args.switch_signal}<{args.switch_threshold}"
                   if kind == "switch" else None),
        "limit_days": limit_days,
        "start": args.start, "end": args.end,
        "days": len(eq), "skipped_days": skipped_days,
        "final_equity": round(float(eq["equity"].iloc[-1])),
        "return_pct": round(ret * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "buy_attempts": n_try, "buy_filled": n_fill,
        "fill_rate_pct": round(n_fill / n_try * 100, 1) if n_try else None,
        "sells": int((tr["side"] == "SELL").sum()) if not tr.empty else 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        eq.to_parquet(out / f"equity_{args.mode}.parquet", index=False)
        tr.to_parquet(out / f"trades_{args.mode}.parquet", index=False)
        (out / f"summary_{args.mode}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2))
        log.info("저장: %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
