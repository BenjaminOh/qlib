#!/usr/bin/env python
"""`open` 에 알파가 있는가 — 종목 선정 능력만 따로 잰다.

실거래 20일에서 `open` 은 +10.79%, 같은 기간 코스닥은 +17.18%였다. 시장을 못
이겼다는 뜻인데, 20일로는 운과 실력을 못 가른다. 여기서는 645 거래일 신호로
**진입·청산·비용을 전부 걷어내고 종목 선정만** 본다.

측정하는 것:

1. **선택 알파** = top-K 픽의 평균 수익 − 유니버스 평균 수익.
   0 이면 모델이 고른 종목이 아무 종목이나 고른 것과 다르지 않다는 뜻이다.
   이게 0인데 진입 방식을 손보는 것은 없는 알파를 키우려는 것이다.

2. **롱-숏 스프레드** = top10 − (21~30위). 모델이 순위를 매기는 능력이 있으면
   상위가 하위를 이겨야 한다. 저장된 랭킹이 30위까지라 그 안에서만 잰다.

3. **랭크 IC** (스피어만) — 저장된 30위 안에서 순위와 실현수익의 상관.
   기존 `retrospective.daily_ic` 는 top-10 안에서만 재는데, 10개로는 IC 가
   거의 잡음이다. 30개로 넓힌다.

**기존 회고와 다른 점**: `daily_ic` 는 "1위가 10위보다 나은가"만 답한다.
"픽이 시장을 이기는가"는 아무도 재지 않았다 — 그게 알파 질문 본체다.

기준가는 **as_of 당일 시가**다. `open` 이 실제로 사는 가격이라 진입 타이밍
효과가 섞이지 않는다. 청산은 없다 — k일 뒤 종가로 평가만 한다.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

log = logging.getLogger("backtest_alpha")


def spearman(a: list[float], b: list[float]) -> float:
    """동점을 평균 순위로 처리한 스피어만 상관."""
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(ra.corr(rb))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default="/root/.qlib/bt_signals")
    ap.add_argument("--horizons", default="1,3,5,10")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    horizons = [int(h) for h in args.horizons.split(",")]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()
    from qlib.data import D
    from app.api.services.live_trader import LIVE_CONFIG

    files = sorted(glob.glob(os.path.join(args.signals, "*.parquet")))
    if not files:
        log.error("신호 파일이 없다: %s", args.signals)
        return 1
    log.info("신호 %d일", len(files))

    # 유니버스 전체를 한 번에 읽어 벡터화한다.
    universe = D.list_instruments(D.instruments(LIVE_CONFIG["instruments"]), as_list=True)
    cal = [str(c)[:10] for c in D.calendar()]
    px = D.features(universe, ["$open", "$close"], freq="day")
    opens = px["$open"].unstack(level="instrument")
    closes = px["$close"].unstack(level="instrument")
    opens.index = [str(i)[:10] for i in opens.index]
    closes.index = [str(i)[:10] for i in closes.index]
    log.info("유니버스 %d 종목, 봉 %d일", len(universe), len(opens))

    rows = []
    for f in files:
        gen = os.path.basename(f)[:10]
        sig = pd.read_parquet(f).sort_values("rank")
        as_of = str(sig["as_of"].iloc[0]) if "as_of" in sig.columns else None
        if not as_of or as_of not in opens.index:
            continue
        i = cal.index(as_of) if as_of in cal else None
        if i is None:
            continue
        o = opens.loc[as_of].dropna()
        if o.empty:
            continue
        top = [c for c in sig[sig["rank"] <= args.topk]["code"] if c in o.index]
        bot = [c for c in sig[sig["rank"] > 20]["code"] if c in o.index]
        all30 = [(int(r), c) for r, c in zip(sig["rank"], sig["code"]) if c in o.index]
        if len(top) < 5:
            continue

        rec = {"generated_on": gen, "as_of": as_of, "n_top": len(top),
               "n_univ": int(len(o))}
        for k in horizons:
            j = i + k
            if j >= len(cal) or cal[j] not in closes.index:
                continue
            c = closes.loc[cal[j]]
            ret = (c / o - 1.0).dropna()          # 시가 진입 → k일 뒤 종가
            if ret.empty:
                continue
            t = ret.reindex(top).dropna()
            if t.empty:
                continue
            rec[f"pick{k}"] = float(t.mean())
            rec[f"univ{k}"] = float(ret.mean())
            rec[f"alpha{k}"] = float(t.mean() - ret.mean())
            b = ret.reindex(bot).dropna()
            if not b.empty:
                rec[f"spread{k}"] = float(t.mean() - b.mean())
            pairs = [(r, ret[c2]) for r, c2 in all30 if c2 in ret.index]
            if len(pairs) >= 10:
                # 랭크는 작을수록 좋으므로 부호를 뒤집어 "높을수록 좋음" 으로 맞춘다
                rec[f"ic{k}"] = spearman([-p[0] for p in pairs], [p[1] for p in pairs])
        rows.append(rec)

    df = pd.DataFrame(rows)
    if df.empty:
        log.error("계산된 행이 없다")
        return 1

    print(f"\n측정 구간: {df['as_of'].min()} ~ {df['as_of'].max()}  ({len(df)}일)")
    print(f"유니버스 평균 {df['n_univ'].mean():.0f} 종목 / 픽 {df['n_top'].mean():.1f} 개\n")
    print(f"{'지표':<26}{'평균':>10}{'중앙값':>10}{'표준편차':>10}{'t값':>8}{'승률':>8}{'n':>6}")
    print("─" * 80)
    summary = {}
    for k in horizons:
        for key, label in ((f"alpha{k}", f"선택 알파 {k}일"),
                           (f"spread{k}", f"롱숏 스프레드 {k}일"),
                           (f"ic{k}", f"랭크 IC {k}일")):
            if key not in df.columns:
                continue
            s = df[key].dropna()
            if s.empty:
                continue
            t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if s.std(ddof=1) > 0 else float("nan")
            win = (s > 0).mean()
            unit = "" if key.startswith("ic") else "%"
            mul = 1 if key.startswith("ic") else 100
            print(f"{label:<26}{s.mean()*mul:>9.3f}{unit}{s.median()*mul:>9.3f}{unit}"
                  f"{s.std()*mul:>9.3f}{unit}{t:>8.2f}{win*100:>7.0f}%{len(s):>6}")
            summary[key] = {"mean": float(s.mean()), "t": float(t),
                            "win_rate": float(win), "n": int(len(s))}
        print()

    # 픽 vs 유니버스 누적(단순 평균 수익의 합) — 방향 감을 잡는 용도
    for k in horizons[:1]:
        if f"pick{k}" in df.columns:
            print(f"[{k}일 보유 기준] 픽 평균 {df[f'pick{k}'].mean()*100:+.3f}% / "
                  f"유니버스 평균 {df[f'univ{k}'].mean()*100:+.3f}% "
                  f"→ 차이 {df[f'alpha{k}'].mean()*100:+.3f}%p")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(args.out, index=False)
        Path(str(args.out) + ".summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2))
        log.info("저장: %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
