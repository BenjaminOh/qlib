#!/usr/bin/env python
"""자산 곡선에서 **반납**을 잰다 — "벌었던 걸 다 토해내나" 에 답하는 지표.

MDD 만으로는 사용자 질문에 답할 수 없다. −30% 낙폭이 원금에서 난 건지 두 배
불린 뒤에 난 건지가 전혀 다르기 때문이다. 그래서 **반납률**을 함께 낸다:

    반납률 = (고점 − 저점) / (고점 − 시드)

  · 1.0 에 가까우면 **벌어둔 것을 전부 토해냈다**
  · 0.3 이면 번 것의 30%만 반납했다
  · 1.0 을 넘으면 원금까지 깎였다

여기에 **수중 기간**(고점 회복까지 걸린 거래일)을 붙인다. 반납이 작아도 3년을
못 돌아오면 견디기 어렵고, 반납이 커도 두 달 만에 회복하면 다른 이야기다.
"""

from __future__ import annotations

import argparse
import glob
import os

import pandas as pd


def analyse(eq: pd.Series, seed: float, label: str) -> dict:
    """고점·저점·반납률·수중 기간. `eq` 는 날짜 인덱스의 자산 시계열."""
    peak = eq.cummax()
    dd = eq / peak - 1.0
    i_trough = dd.idxmin()
    mdd = float(dd.min())
    peak_at_trough = float(peak.loc[i_trough])
    trough = float(eq.loc[i_trough])
    # 그 저점을 만든 고점이 언제였나
    i_peak = eq.loc[:i_trough].idxmax()

    gained = peak_at_trough - seed          # 고점까지 벌어둔 것
    given_back = peak_at_trough - trough    # 그 뒤 토해낸 것
    giveback_ratio = (given_back / gained) if gained > 0 else float("nan")

    # 수중 기간 — 고점을 회복한 첫 날까지
    after = eq.loc[i_peak:]
    rec = after[after >= peak_at_trough]
    recovered_at = rec.index[1] if len(rec) > 1 else None
    underwater = (len(after.loc[:recovered_at]) - 1) if recovered_at is not None \
        else len(after) - 1

    return {
        "구간": label, "일수": len(eq),
        "최종": float(eq.iloc[-1]), "수익률%": (float(eq.iloc[-1]) / seed - 1) * 100,
        "고점": peak_at_trough, "고점일": str(i_peak)[:10],
        "저점": trough, "저점일": str(i_trough)[:10],
        "MDD%": mdd * 100,
        "벌었던것": gained, "반납액": given_back,
        "반납률": giveback_ratio,
        "수중일": underwater,
        "회복": str(recovered_at)[:10] if recovered_at is not None else "미회복",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="equity_*.parquet 이 있는 디렉터리")
    ap.add_argument("--seed", type=float, default=10_000_000.0)
    args = ap.parse_args()

    rows = []
    for f in sorted(glob.glob(os.path.join(args.dir, "equity_*.parquet"))):
        name = os.path.basename(f)[len("equity_"):-len(".parquet")]
        df = pd.read_parquet(f)
        eq = pd.Series(df["equity"].values, index=pd.to_datetime(df["date"]))
        rows.append(analyse(eq, args.seed, f"{name} 전체"))
        for y in ("2024", "2025", "2026"):
            sub = eq[eq.index.year == int(y)]
            if len(sub) < 20:
                continue
            # 연도별은 그 해 첫날 자산을 시드로 본다 — 그 해에 번 것 대비 반납
            rows.append(analyse(sub, float(sub.iloc[0]), f"{name} {y}"))

    if not rows:
        print("equity_*.parquet 이 없다:", args.dir)
        return 1

    print(f"{'구간':<18}{'수익률':>9}{'MDD':>8}{'벌었던것':>12}{'반납액':>12}"
          f"{'반납률':>8}{'수중일':>7}  {'고점일':<11}{'저점일':<11}{'회복'}")
    print("─" * 118)
    for r in rows:
        gb = f"{r['반납률']:.0%}" if r["반납률"] == r["반납률"] else "-"
        print(f"{r['구간']:<18}{r['수익률%']:>8.1f}%{r['MDD%']:>7.1f}%"
              f"{r['벌었던것']:>12,.0f}{r['반납액']:>12,.0f}{gb:>8}{r['수중일']:>7}  "
              f"{r['고점일']:<11}{r['저점일']:<11}{r['회복']}")
    print("\n반납률 = (고점−저점) / (고점−시드).  1.0 이면 벌어둔 것을 전부 토해낸 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
