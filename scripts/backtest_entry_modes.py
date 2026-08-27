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
    """진입 방식.

      market  — 당일 **시가** (운영 `open` 이 실제로 하는 것)
      close   — 당일 **종가**. 같은 신호를 그대로 쓰되 09:00 이 아니라 15:20 에 산다.
                IC 분해에서 **매수일 장중이 알파 −0.068%p** 로 가장 나쁜 구간이었다
                (모델이 고른 종목은 갭업으로 열린 뒤 되밀린다). 종가 진입은 그
                구간을 통째로 피하면서 **지정가와 달리 반드시 체결된다.**
      limitN  — 전일종가 −N%% 지정가. 갭업을 안 쫓지만 갭업 종목은 체결이 안 된다.
      switchN — 레짐이면 limitN, 아니면 market.
    """
    if mode == "market":
        return "market", None
    if mode == "close":
        return "close", None
    if mode.startswith("limit"):
        return "limit", float(mode[5:]) / 100.0
    if mode.startswith("switch"):
        return "switch", float(mode[6:]) / 100.0
    raise SystemExit(f"알 수 없는 모드: {mode}")


def exit_line(code, entry_day, day, avg, *, peak_close, prev_low,
              sl_cap, low_window, low_buffer, trail):
    """(청산선, 근거). 운영 `live_trader._trail_line` 과 **같은 max 구조**.

    구조적 손절(전 저점 −1%, 캡 −10%)이 바닥을 깔고, 주가가 오르면 트레일이
    그 위로 올라선다. 최고 종가가 평단 아래면 트레일선이 손절 아래로 내려가므로
    손절이 이긴다 — 그래서 max 다.

    `trail=None` 이면 트레일 없이 구조적 손절만 (rank_stop 모드).
    """
    cap = avg * (1.0 - sl_cap)
    pl = prev_low(code, entry_day, low_window)
    low = pl * (1.0 - low_buffer) if pl else None
    line, kind = ((low, "prev_low") if (low and cap < low < avg) else (cap, "cap"))
    if trail:
        peak = peak_close(code, entry_day, day)
        if peak and peak * (1.0 - trail) > line:
            line, kind = peak * (1.0 - trail), "trail"
    return line, kind


def regime_series(regime: dict, key: str, ma: int):
    """(지수수준, 이동평균) — 날짜별 dict.

    지수 **종가 수준을 직접** 쓴다 (`kq_lvl`/`ks_lvl`). 첫 버전은 등락률을
    `(1+r).cumprod()` 로 복원했는데, 날짜가 빠지고 휴장일 0% 가 끼면서 MA
    상/하 판정이 실제 지수와 ~20% 어긋났다 — 복원은 다시 쓰지 않는다.
    """
    import pandas as pd
    if not regime:
        return {}, {}
    ser = pd.Series({d: v.get(f"{key}_lvl") for d, v in regime.items()}).dropna().sort_index()
    lvl = ser
    mav = lvl.rolling(ma).mean()
    # ★ 하루 민다. D일 **09:00** 에 매매를 결정하는데, D일 지수 종가는 그때
    # 존재하지 않는다. 밀지 않으면 미래를 보고 방어를 켜는 백테스트가 된다.
    # (첫 실행에서 이걸 빼먹었더니 cash20 이 기준선을 +22.9%p 앞섰다.)
    return lvl.shift(1).to_dict(), mav.shift(1).to_dict()


def risk_on(day, lvl: dict, mav: dict) -> bool:
    """오늘 시장이 이동평균 위인가. **판정할 수 없으면 True**(현행 유지).

    초기 N일은 이동평균이 없다. 그때 방어를 켜면 "데이터가 없어서 안 샀다" 가
    되는데, 그건 전략이 아니라 사고다.
    """
    k = day.isoformat()
    a, b = lvl.get(k), mav.get(k)
    if a is None or b is None or b != b:
        return True
    return float(a) >= float(b)


def valid_bar(b: dict | None) -> dict | None:
    """NaN·비양수 봉을 걸러낸다.

    운영 `_day_ohlc`(live_trader.py) 는 `any(v <= 0 for v in bar.values())` 로만
    거르는데 **NaN 은 어떤 비교도 False** 라 그대로 통과한다(`nan <= 0` is False).
    2026년 20일 구간에서는 안 터지다가 2024~2026 전 구간에서 터졌다 — 거래정지·
    상장폐지 종목의 봉이 NaN 으로 들어오고, 그걸 보유 평가에 쓰면 **자산 전체가
    NaN 이 되어 전파**된다. `v != v` 가 NaN 판정이다.
    """
    if not b:
        return None
    for v in b.values():
        if v != v or v <= 0:      # NaN 또는 비양수
            return None
    return b


def load_regime(path: str | None) -> dict[str, dict]:
    """날짜 → {sp, nq, ks, kq, kq_lvl, ks_lvl}. 없으면 빈 dict.

    구스키마(등락률만, 월요일 누락) 파일이 들어오면 **즉시 거부**한다 —
    그 파일로 낸 결과가 한 번 무효가 됐다(2026-08-28, 월요일 127일 누락).
    """
    if not path:
        return {}
    import pandas as pd
    df = pd.read_parquet(path)
    if "kq_lvl" not in df.columns:
        raise SystemExit(f"--regime {path}: 구스키마(수준 열 없음). "
                         "backtest_regime_data.py 를 다시 돌려 재생성할 것")
    keys = ("sp", "nq", "ks", "kq", "kq_lvl", "ks_lvl")
    return {r["date"]: {k: r[k] for k in keys if k in r}
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
    ap.add_argument("--exit", default="rank",
                    choices=["rank", "ladder_trail", "trail_only", "rank_stop"],
                    help="청산 규칙. rank=랭크 이탈만(구체제) / "
                         "ladder_trail=현행 운영 / trail_only=트레일만 / "
                         "rank_stop=랭크+손절")
    ap.add_argument("--regime-filter", default="none",
                    choices=["none", "nobuy", "halfk", "cash"],
                    help="지수가 이동평균 아래일 때: none=현행 / nobuy=신규매수중단 / "
                         "halfk=topk 절반 / cash=전량청산")
    ap.add_argument("--regime-ma", type=int, default=60)
    ap.add_argument("--regime-index", default="kq", choices=["kq", "ks"])
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
    if args.regime_filter != "none" and not regime:
        raise SystemExit("--regime-filter 에는 --regime 이 필요하다")
    lvl, mav = regime_series(regime, args.regime_index, args.regime_ma)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()

    from qlib.data import D
    from app.api.services.kis_client import round_to_tick
    from app.api.config import settings
    from app.api.services.live_trader import (
        LIVE_CONFIG, OPEN_EXIT_RULES_ARCHIVED, _buy_count, _day_ohlc, _peak_close,
        _prev_close_before, _prev_low, _prev_trading_day, trade_cost,
    )

    # 2026-08-27 철회 후 EXIT_RULES 에서 빠졌다. 보관 상수를 읽어 **같은
    # 파라미터로** 비교를 계속할 수 있게 한다 — 다시 켤지 판단하려면 필요하다.
    rule = OPEN_EXIT_RULES_ARCHIVED
    ladder_pct = float(rule["ladder"][0])     # +10%
    ladder_frac = float(rule.get("ladder_fraction", 0.5))
    trail_pct = float(rule["trail_rest"])     # −7%
    use_ladder = args.exit == "ladder_trail"
    use_trail = args.exit in ("ladder_trail", "trail_only")
    use_stop = args.exit in ("ladder_trail", "trail_only", "rank_stop")
    use_rank = args.exit in ("rank", "rank_stop")

    topk = LIVE_CONFIG["strategy_kwargs"]["topk"]
    n_drop = LIVE_CONFIG["strategy_kwargs"]["n_drop"]
    sig_dir = Path(args.signals)

    days = [pd.Timestamp(c).date() for c in D.calendar(start_time=args.start, end_time=args.end)]

    # ── 레짐 커버리지 게이트 — 경고가 아니라 중단이다.
    # `risk_on()`/`wants_limit()` 은 데이터가 없는 날 조용히 기본값(방어 꺼짐/
    # 시장가)으로 넘어간다. 첫 버전은 그 폴백 때문에 **월요일 127일 전부**
    # 방어가 꺼진 채 완주했고 결과가 통째로 무효가 됐다(2026-08-28).
    if regime:
        miss = [d for d in days if d.isoformat() not in regime]
        rate = len(miss) / max(len(days), 1)
        if rate > 0.02:
            raise SystemExit(
                f"레짐 커버리지 부족: 백테스트 {len(days)}일 중 {len(miss)}일"
                f"({rate:.1%})에 레짐 데이터가 없다 (허용 2%). "
                f"예: {[d.isoformat() for d in miss[:5]]} — "
                "backtest_regime_data.py 를 다시 돌릴 것")

    cash = args.seed
    pos: dict[str, dict] = {}          # code -> {"qty": int, "cost": float}
    rows, trades = [], []
    last_px: dict[str, float] = {}     # 정지 종목 평가용 마지막 유효 종가
    entry_day: dict[str, object] = {}  # 종목 → 진입일 (트레일·손절 기준)
    rung_done: set = set()             # 이번 에피소드에서 사다리를 이미 판 종목
    exit_kinds: list = []              # 청산 사유 집계
    risk_off_days = 0                  # 레짐 방어가 켜진 날 수
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

        bars = {c: valid_bar(_day_ohlc(c, day)) for c in set(target) | set(pos)}
        for c, b in bars.items():
            if b:
                last_px[c] = b["close"]

        def mark(code: str) -> float:
            """보유 평가가. 오늘 봉이 없으면(정지) **마지막 유효 종가**로 든다.
            0 으로 떨어뜨리면 정지 종목을 상폐 처리하는 셈이 된다."""
            b = bars.get(code)
            if b:
                return b["close"]
            return last_px.get(code, pos[code]["cost"] / max(pos[code]["qty"], 1))

        # ── 0) 포트폴리오 레짐 — 시장 전체가 이동평균 아래면 노출을 줄인다.
        #
        # 종목별 손절과 **작동 방식이 다르다**: 개별 종목의 상승을 자르지 않고
        # 신규 노출만 줄인다(cash 모드는 예외 — 그래서 대조군으로 넣었다).
        on = risk_on(day, lvl, mav) if args.regime_filter != "none" else True
        if not on:
            risk_off_days += 1
        topk_eff = topk
        if not on and args.regime_filter == "halfk":
            topk_eff = max(topk // 2, 1)

        # ── 1) 매도 — 랭크 이탈. 오늘 순위가 나쁜 것부터(_rank_sorted_sells 와 동일)
        held = set(pos)
        dropped = (held - set(target)) if use_rank else set()
        # 30위권 밖은 SIGNAL_STORE_TOP_N+1 로 취급 (_today_rank 와 같다)
        worst = max(sig["rank"].max() + 1, 31)
        if not on and args.regime_filter == "cash":
            dropped = set(held)                    # 전량 청산
        elif not on and args.regime_filter == "halfk" and len(held) > topk_eff:
            # 초과분만 순위 나쁜 순으로 정리한다
            over = sorted(held, key=lambda c: (-ranks.get(c, worst), c))
            dropped = dropped | set(over[:len(held) - topk_eff])
        # 평시에는 하루 n_drop 개만 판다. 레짐 청산(cash/halfk)은 그 상한을
        # 적용하지 않는다 — "노출을 줄인다" 가 목적인데 이틀에 걸쳐 줄이면
        # 그 사이 낙폭을 그대로 맞는다.
        cap_sells = (args.regime_filter in ("cash", "halfk")) and not on
        ordered = sorted(dropped, key=lambda c: (-ranks.get(c, worst), c))
        to_sell = ordered if cap_sells else ordered[:n_drop]
        for code in to_sell:
            bar = bars.get(code)
            if not bar:
                continue
            px, qty = bar["open"], pos[code]["qty"]
            fee = trade_cost("SELL", qty, px)
            cash += qty * px - fee
            avg = pos[code]["cost"] / max(qty, 1)
            exit_kinds.append(("rank", (px / avg - 1.0)))
            trades.append({"date": day.isoformat(), "side": "SELL", "code": code,
                           "qty": qty, "px": px, "filled": True, "kind": "rank"})
            pos.pop(code); entry_day.pop(code, None); rung_done.discard(code)

        # ── 2) 매수
        equity = cash + sum(pos[c]["qty"] * mark(c) for c in pos)
        slot_budget = equity / max(topk_eff, 1)
        n_buy = _buy_count(held=len(pos), selling=0, topk=topk_eff,
                           n_drop=n_drop, simulated=False)
        if not on and args.regime_filter in ("nobuy", "halfk", "cash"):
            n_buy = 0                              # 신규 노출 중단
        cands = [c for c in target if c not in pos]
        # _select_affordable_buys 와 같은 규칙: 1주 값이 슬롯 예산을 넘으면 건너뛴다
        planned = []
        for c in cands:
            if len(planned) >= n_buy:
                break
            bar = bars.get(c)
            if not bar:
                continue
            if day_kind == "market":
                ref = bar["open"]
            elif day_kind == "close":
                ref = bar["close"]
            else:
                ref = _prev_close_before(c, day) or bar["open"]
            if ref > slot_budget:
                continue
            planned.append(c)
        # 분모는 **계획 건수**로 고정 — 체결 결과가 종목당 예산을 바꾸면 안 된다
        per_code = min(cash / max(len(planned), 1), slot_budget) if planned else 0.0

        for code in planned:
            bar = bars[code]
            if day_kind == "market":
                fill_px, filled = bar["open"], True
            elif day_kind == "close":
                fill_px, filled = bar["close"], True
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
            if fill_px != fill_px or fill_px <= 0 or per_code != per_code:
                continue
            qty = int(per_code // fill_px)
            if qty <= 0:
                continue
            fee = trade_cost("BUY", qty, fill_px)
            if qty * fill_px + fee > cash:
                continue
            cash -= qty * fill_px + fee
            pos[code] = {"qty": qty, "cost": qty * fill_px}
            entry_day[code] = day
            rung_done.discard(code)
            trades.append({"date": day.isoformat(), "side": "BUY", "code": code,
                           "qty": qty, "px": fill_px, "filled": True})

        # ── 3) 규칙 청산 — **매수 뒤에 온다.** 운영 순서가 그렇다:
        #   09:00 랭크 매도 + 매수 → 09:25 사다리 예약 → 09:30~ 트레일 폴링
        # 앞에 두면 트레일로 판 종목을 **같은 날 다시 사게 된다** — 운영에서는
        # 불가능한 회전이고, 그 인위적 회전이 사다리·트레일 모드만 깎는다.
        #
        # 운영 순서를 따른다: 09:25 사다리 예약이 먼저 걸리고, 09:30~ 폴링이
        # 트레일을 본다. 그래서 같은 날 둘 다 조건을 만족하면 **사다리가 먼저**다.
        for code in list(pos):
            bar = bars.get(code)
            if not bar or code not in entry_day:
                continue
            same_day = entry_day[code] >= day
            avg = pos[code]["cost"] / max(pos[code]["qty"], 1)

            # 사다리 — **매도** 지정가라 `고가 >= 지정가` 다. 진입 쪽 규칙
            # (`저가 <= 지정가`)을 그대로 베끼면 부호가 뒤집힌다.
            if use_ladder and code not in rung_done:
                rung = float(round_to_tick(avg * (1.0 + ladder_pct)))
                if bar["high"] >= rung:
                    q = int(pos[code]["qty"] * ladder_frac)
                    if q > 0:
                        px = bar["open"] if bar["open"] > rung else rung
                        fee = trade_cost("SELL", q, px)
                        cash += q * px - fee
                        pos[code]["cost"] -= pos[code]["cost"] * q / pos[code]["qty"]
                        pos[code]["qty"] -= q
                        rung_done.add(code)
                        exit_kinds.append(("ladder", (px / avg - 1.0)))
                        trades.append({"date": day.isoformat(), "side": "SELL",
                                       "code": code, "qty": q, "px": px,
                                       "filled": True, "kind": "ladder"})
                        if pos[code]["qty"] <= 0:
                            pos.pop(code); entry_day.pop(code, None)
                            rung_done.discard(code)
                            continue

            # 트레일·손절 — 저가가 선을 깨면 전량. 갭하락이면 시가 체결.
            # 트레일·손절은 **진입 당일 건너뛴다** — 운영 `watch_trailing_exits`
            # 의 `entry.trade_date >= day` 가드와 같다(기준이 될 종가가 없다).
            # 사다리는 그 가드가 없다: 09:25 예약은 그날 매수분에도 걸린다.
            if (use_trail or use_stop) and code in pos and not same_day:
                # ⚠ 변수명에 주의 — 바깥의 `kind` 는 **진입 모드**다.
                # 여기서 `kind` 로 받으면 그걸 덮어써서 다음 날 매수가 엉뚱한
                # 분기로 빠진다(실제로 한 번 그랬다).
                line, line_kind = exit_line(
                    code, entry_day[code], day, avg,
                    peak_close=_peak_close, prev_low=_prev_low,
                    sl_cap=settings.live_close_bracket_sl,
                    low_window=settings.live_close_bracket_low_window,
                    low_buffer=settings.live_close_bracket_low_buffer,
                    trail=trail_pct if use_trail else None)
                if line and bar["low"] <= line:
                    px = bar["open"] if bar["open"] < line else line
                    q = pos[code]["qty"]
                    fee = trade_cost("SELL", q, px)
                    cash += q * px - fee
                    exit_kinds.append((line_kind, (px / avg - 1.0)))
                    trades.append({"date": day.isoformat(), "side": "SELL",
                                   "code": code, "qty": q, "px": px,
                                   "filled": True, "kind": line_kind})
                    pos.pop(code); entry_day.pop(code, None)
                    rung_done.discard(code)

        eq = cash + sum(pos[c]["qty"] * mark(c) for c in pos)
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
    from collections import Counter
    kinds = Counter(k for k, _ in exit_kinds)
    by_kind = {}
    for k in kinds:
        rets = [r for kk, r in exit_kinds if kk == k]
        by_kind[k] = {"n": len(rets), "avg_ret_pct": round(sum(rets) / len(rets) * 100, 2)}

    summary = {
        "mode": args.mode,
        "exit": args.exit,
        "regime_filter": args.regime_filter,
        "regime_ma": args.regime_ma if args.regime_filter != "none" else None,
        "risk_off_days": risk_off_days,
        "exit_kinds": by_kind,
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
