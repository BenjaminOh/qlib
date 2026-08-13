"""Trade retrospective engine — shared by the API (/live/retro) and the
weekly script (scripts/trade_retrospective.py).

Reconstructs closed BUY→SELL episodes for a strategy, joins each with its
entry-time evidence (reasons_json snapshot) and outcome (realised %,
give-back vs the peak close while held, post-exit 5-day drift), scores the
standing improvement hypotheses, and computes the daily rank-IC of the
signal. Prices come from kr_data (fast, local) with a KIS fallback for
out-of-universe codes."""

from __future__ import annotations

import json
import logging
from datetime import date

from ..db import SessionLocal, Order, Signal, SurgePick, init_db

log = logging.getLogger(__name__)


def _close_series(code: str) -> dict[str, float]:
    """date-iso -> close. kr_data first, KIS daily bars as fallback."""
    try:
        import pandas as pd
        from qlib.data import D
        df = D.features([code], ["$close"], freq="day").reset_index()
        if not df.empty:
            df["datetime"] = pd.to_datetime(df["datetime"]).dt.date
            return {d.isoformat(): float(v)
                    for d, v in zip(df["datetime"], df["$close"])}
    except Exception:  # noqa: BLE001
        pass
    try:
        from .kis_client import get_kis_client
        return {b["date"]: float(b["close"])
                for b in (get_kis_client().get_daily_bars(code) or [])}
    except Exception:  # noqa: BLE001
        return {}


def build_episodes(strategy: str = "open") -> list[dict]:
    init_db()
    with SessionLocal() as db:
        rows = (db.query(Order)
                  .filter(Order.strategy == strategy,
                          Order.status.in_(("FILLED", "PARTIAL", "SIMULATED")),
                          Order.price.isnot(None))
                  .order_by(Order.code, Order.submitted_at)
                  .all())
    out: list[dict] = []
    pos: dict[str, dict] = {}
    for o in rows:
        why = {}
        try:
            why = json.loads(o.reasons_json) if o.reasons_json else {}
        except Exception:  # noqa: BLE001
            pass
        if o.side == "BUY":
            p = pos.setdefault(o.code, {"qty": 0, "cost": 0.0, "entry": None})
            if p["qty"] == 0:
                p["entry"] = {"date": o.trade_date.isoformat(), "name": o.name,
                              "metrics": why.get("metrics") or {},
                              "basis": why.get("basis") or ""}
            p["qty"] += o.qty
            p["cost"] += o.qty * o.price
        elif o.side == "SELL":
            p = pos.get(o.code)
            if not p or p["qty"] <= 0:
                continue
            avg = p["cost"] / p["qty"]
            e = p["entry"] or {}
            out.append({"code": o.code, "name": o.name or e.get("name") or o.code,
                        "strategy": strategy,
                        "entry_date": e.get("date"), "exit_date": o.trade_date.isoformat(),
                        "avg": round(avg, 1), "exit_px": o.price, "qty": o.qty,
                        "ret_pct": round((o.price / avg - 1) * 100, 2),
                        "entry_metrics": e.get("metrics") or {},
                        "entry_basis": e.get("basis") or ""})
            p["qty"] -= o.qty
            p["cost"] -= avg * o.qty
            if p["qty"] <= 0:
                pos.pop(o.code, None)
    # open holdings as unrealised rows (entry only)
    for code, p in pos.items():
        if p["qty"] > 0 and p["entry"]:
            e = p["entry"]
            out.append({"code": code, "name": e.get("name") or code,
                        "strategy": strategy, "entry_date": e.get("date"),
                        "exit_date": None, "avg": round(p["cost"] / p["qty"], 1),
                        "exit_px": None, "qty": p["qty"], "ret_pct": None,
                        "entry_metrics": e.get("metrics") or {},
                        "entry_basis": e.get("basis") or ""})
    return out


def enrich_episode(ep: dict) -> dict:
    closes = _close_series(ep["code"])
    if not closes or not ep.get("entry_date"):
        return ep
    dates = sorted(closes)
    if ep.get("exit_date"):
        held = [closes[d] for d in dates if ep["entry_date"] <= d <= ep["exit_date"]]
        if held:
            peak = max(held)
            ep["max_unreal_pct"] = round((peak / ep["avg"] - 1) * 100, 2)
            ep["give_back_pp"] = round(ep["max_unreal_pct"] - (ep["ret_pct"] or 0), 2)
            ep["hold_days"] = len(held) - 1
        after = [closes[d] for d in dates if d > ep["exit_date"]][:5]
        if after and ep.get("exit_px"):
            ep["post5_drift_pct"] = round((after[-1] / ep["exit_px"] - 1) * 100, 2)
        # Overnight slice of the exit: prev trading day's close → the actual
        # fill. Only meaningful for `open` (09:00 market sell after the
        # evening signal drop) — sim strategies fill at bar levels, so their
        # number is not an overnight move; scoreboard filters by strategy.
        before = [closes[d] for d in dates if d < ep["exit_date"]]
        if before and ep.get("exit_px"):
            ep["overnight_pct"] = round((ep["exit_px"] / before[-1] - 1) * 100, 2)
    else:
        last = closes[dates[-1]]
        ep["unreal_pct"] = round((last / ep["avg"] - 1) * 100, 2)
    return ep


def scoreboard(episodes: list[dict]) -> list[dict]:
    """Score the standing hypotheses on CLOSED episodes."""
    closed = [e for e in episodes if e.get("ret_pct") is not None]
    hot = [e for e in closed if (e["entry_metrics"].get("ret5") or 0) >= 20]
    cool = [e for e in closed if (e["entry_metrics"].get("ret5") or 0) < 0]
    drifted = [e for e in closed if e.get("post5_drift_pct") is not None]
    rose = [e for e in drifted if e["post5_drift_pct"] > 0]
    # Overnight is only a real overnight move for `open` (09:00 market sell).
    overnight = [e for e in closed if e.get("overnight_pct") is not None
                 and e.get("strategy") == "open"]
    gapped_down = [e for e in overnight if e["overnight_pct"] < 0]

    def avg(rows, key="ret_pct"):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return [
        {"key": "H-과열", "label": "과열 진입(ret5≥+20%)은 저성과",
         "evidence": f"과열 {len(hot)}건 평균 {avg(hot)}% vs 저조정 {len(cool)}건 평균 {avg(cool)}%",
         "support": len([e for e in hot if e["ret_pct"] < 0]),
         "refute": len([e for e in hot if e["ret_pct"] >= 0]),
         "threshold": "표본 15건 & 격차 3%p — 도달 시 필터 시뮬 검증 제안"},
        {"key": "H-조기하차", "label": "순위 이탈 매도가 체계적으로 이르다",
         "evidence": (f"매도 후 5일 상승 {len(rose)}/{len(drifted)}건, "
                      f"평균 드리프트 {avg(drifted, 'post5_drift_pct')}%"),
         "support": len(rose), "refute": len(drifted) - len(rose),
         "threshold": "trail/scale 공정 곡선과 2주 대조 → 청산 규칙 교체 제안"},
        {"key": "H-갭", "label": "시가 갭 +5% 이상 추격은 저성과",
         "evidence": "갭 표본 축적 중 (셀바스AI 8/4 지지 1)",
         "support": 1, "refute": 0, "threshold": "표본 10건"},
        {"key": "H-재개주", "label": "관리종목·거래재개 30일 내 픽은 고위험",
         "evidence": "콘텐트리중앙(8/7 진입)부터 관찰",
         "support": 0, "refute": 0, "threshold": "표본 5건"},
        # 2026-08-13 사용자 제안: 15:00 예비 신호로 이탈 종목을 마감 전 선매도
        # → 오버나이트 무노출. 참이려면 이탈 종목이 밤사이 하락해야 한다.
        {"key": "H-오버나이트",
         "label": "이탈 종목은 밤사이(전일 종가→익일 시초 체결) 하락한다 — 참이면 마감 전 선매도 유리",
         "evidence": (f"이탈 매도 {len(overnight)}건 평균 오버나이트 "
                      f"{avg(overnight, 'overnight_pct')}% "
                      f"(하락 {len(gapped_down)}/{len(overnight)}건)"),
         "support": len(gapped_down),
         "refute": len(overnight) - len(gapped_down),
         "threshold": ("표본 15건 & 평균 음수 — 도달 시 '15:00 예비 신호 선매도' "
                       "시나리오 설계 제안 (예비/공식 신호 불일치 왕복 비용 포함 검증)")},
    ]


def daily_ic() -> list[dict]:
    """Spearman rank IC: signal rank (1..10) vs the pick's same-day return."""
    init_db()
    with SessionLocal() as db:
        dates = [r[0] for r in db.query(Signal.as_of).distinct()
                 .order_by(Signal.as_of).all()]
        out = []
        for as_of in dates:
            picks = (db.query(Signal.rank, Signal.code)
                       .filter(Signal.as_of == as_of, Signal.rank <= 10).all())
            rets = []
            for rank, code in picks:
                closes = _close_series(code)
                ds = sorted(closes)
                key = as_of.isoformat()
                if key not in closes:
                    continue
                i = ds.index(key)
                if i == 0:
                    continue
                rets.append((rank, closes[key] / closes[ds[i - 1]] - 1))
            if len(rets) < 5:
                continue
            n = len(rets)
            by_ret = sorted(rets, key=lambda x: -x[1])
            ret_rank = {rank: i + 1 for i, (rank, _) in enumerate(by_ret)}
            d2 = sum((rank - ret_rank[rank]) ** 2 for rank, _ in rets)
            out.append({"as_of": as_of.isoformat(), "n": n,
                        "rank_ic": round(1 - 6 * d2 / (n * (n * n - 1)), 3)})
    return out


def surge_selection_review(days: int = 30) -> list[dict]:
    """Score the WHOLE surge TOP10 (bought or not) against next-day closes.

    Measures the SELECTION's hit power separately from the strategy's PnL:
    per pick — next-day return and a 'surged ≥+8%' hit label; rows without a
    next-day bar yet stay labelled pending."""
    init_db()
    from datetime import timedelta
    out: list[dict] = []
    with SessionLocal() as db:
        rows = (db.query(SurgePick)
                  .filter(SurgePick.trade_date >= date.today() - timedelta(days=days))
                  .order_by(SurgePick.trade_date.desc(), SurgePick.rank.asc())
                  .all())
    for r in rows:
        closes = _close_series(r.code)
        ds = sorted(closes)
        key = r.trade_date.isoformat()
        nxt = next((d for d in ds if d > key), None)
        base = closes.get(key) or r.close
        item = {"trade_date": key, "rank": r.rank, "code": r.code,
                "name": r.name, "close": r.close, "score": r.score,
                "next_ret_pct": None, "hit": None}
        if nxt and base:
            ret = closes[nxt] / base - 1
            item["next_ret_pct"] = round(ret * 100, 2)
            item["hit"] = bool(ret >= 0.08)
        out.append(item)
    return out


def build_retro(strategy: str = "open") -> dict:
    eps = [enrich_episode(e) for e in build_episodes(strategy)]
    eps.sort(key=lambda e: (e.get("exit_date") or "9999", e.get("entry_date") or ""),
             reverse=True)
    out = {"episodes": eps, "scoreboard": scoreboard(eps), "daily_ic": daily_ic()}
    if strategy == "surge":
        try:
            out["surge_selection"] = surge_selection_review()
        except Exception as exc:  # noqa: BLE001
            log.warning("surge_selection_review failed: %s", exc)
            out["surge_selection"] = []
    return out
