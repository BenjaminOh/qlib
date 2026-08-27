#!/usr/bin/env python
"""레짐 데이터 — 한국 거래일 기준으로 지수 수준과 미국 등락을 붙인다.

## 왜 다시 썼나 (2026-08-28)

첫 버전은 두 가지가 틀렸다:

1. `us.index + Timedelta(days=1)` — 금요일 미국 종가가 **토요일**로 가고,
   `dropna(subset=["nq"])` 가 nq 없는 **월요일을 전부 삭제**했다.
   백테스트 645일 중 133일(월요일 127일 전부, 20.6%)이 사라졌고,
   `risk_on()` 폴백 때문에 그 날들은 방어가 조용히 꺼진 채 돌았다.
2. 등락률만 저장하고 소비부가 `(1+r).cumprod()` 로 수준을 복원했다 —
   날짜가 빠지고(162일) 휴장일이 0% 로 끼면서(38일) 복원 지수의
   MA 상/하 판정이 실제 지수와 **~20% 어긋났다**.

교훈: **기준 인덱스는 판정 대상 시장(한국)의 실제 거래일**이어야 하고,
다른 시장 데이터는 asof(직전 관측)로 붙인다. 그리고 산출물은 저장 전에
스스로 검증한다 — 요일 하나가 통째로 비는 오류는 요일 분포 한 줄이면 잡힌다.

## 스키마

| 열 | 뜻 | 09:00 에 아는가 |
|---|---|---|
| kq_lvl, ks_lvl | 코스닥/코스피 **그날 종가 수준** | ❌ 사후 — 소비부가 shift(1) |
| kq, ks | 그날 등락률 | ❌ 사후 오라클 전용 |
| nq, sp | **직전 미국 세션** 등락률 (asof) | ✅ 미국 t일 종가는 한국 t+1 새벽 확정 |
| us_lag | 미국 관측이 며칠 전 것인지 | 검증용 |
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# kr_data 캘린더와 행수를 대조한다. 컨테이너 기준 경로가 기본값.
DEFAULT_CALENDAR = "/root/.qlib/qlib_data/kr_data/calendars/day.txt"


def build(start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    t = yf.download(["^GSPC", "^IXIC", "^KS11", "^KQ11"],
                    start=start, end=end,
                    progress=False, auto_adjust=True)["Close"]

    # ── 기준 인덱스 = 한국 실제 거래일 (^KQ11 이 거래한 날) ──
    kr = t[["^KS11", "^KQ11"]].dropna(how="all").copy()
    kr.columns = ["ks_lvl", "kq_lvl"]
    kr["ks"] = kr["ks_lvl"].pct_change()
    kr["kq"] = kr["kq_lvl"].pct_change()
    kr = kr.reset_index().rename(columns={kr.index.name or "Date": "Date"})

    # ── 미국: 한국 D일 09:00 에 아는 최신 종가 = D **이전** 마지막 미국 세션.
    #    asof(backward, exact 제외) 가 그 정의 그대로다. 월요일이면 금요일 종가.
    us = t[["^GSPC", "^IXIC"]].dropna(how="all").copy()
    us.columns = ["sp_lvl", "nq_lvl"]
    us["sp"] = us["sp_lvl"].pct_change()
    us["nq"] = us["nq_lvl"].pct_change()
    us = us.reset_index().rename(columns={us.index.name or "Date": "Date"})
    us = us.rename(columns={"Date": "us_date"})

    df = pd.merge_asof(kr.sort_values("Date"), us.sort_values("us_date"),
                       left_on="Date", right_on="us_date",
                       direction="backward", allow_exact_matches=False)
    df["us_lag"] = (df["Date"] - df["us_date"]).dt.days
    df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df[["date", "kq_lvl", "ks_lvl", "kq", "ks", "nq", "sp", "us_lag"]]


def validate(df: pd.DataFrame, calendar: str | None) -> list[str]:
    """실패 사유 목록. 비어 있으면 통과. 하나라도 있으면 저장하지 않는다."""
    errs: list[str] = []
    dts = pd.to_datetime(df["date"])

    # 1) 요일 분포 — 첫 버전은 월요일이 0건이었다. 월~금 각각 15% 이상.
    wd = dts.dt.weekday.value_counts()
    for i, name in enumerate("월화수목금"):
        share = wd.get(i, 0) / len(df)
        if share < 0.15:
            errs.append(f"요일분포: {name}요일 {wd.get(i, 0)}건 ({share:.1%} < 15%)")
    if (dts.dt.weekday >= 5).any():
        errs.append("요일분포: 토/일 행이 존재한다")

    # 2) 수준 점프 — 0.0 수익률 삽입·이음새·단위 오류 검출.
    jump = df["kq_lvl"].pct_change().abs()
    bad = int((jump > 0.30).sum())
    if bad:
        errs.append(f"수준점프: kq_lvl 전일 대비 ±30% 초과 {bad}건")
    if (df["kq_lvl"] <= 0).any() or df["kq_lvl"].isna().any():
        errs.append("수준값: kq_lvl 에 0/음수/NaN 존재")

    # 3) 미국 asof 지연 — 조인 방향이 틀리면 지연이 음수거나 커진다.
    if (df["us_lag"] < 1).any():
        errs.append("us_lag<1: 당일/미래 미국 종가가 붙었다 (룩어헤드)")
    if (df["us_lag"] > 5).any():
        worst = int(df["us_lag"].max())
        errs.append(f"us_lag>5: 최대 {worst}일 — 조인 방향/결측 확인 필요")

    # 4) kr_data 캘린더와 행수 대조 (같은 구간, ±2%).
    if calendar:
        p = Path(calendar)
        if not p.exists():
            errs.append(f"캘린더 파일 없음: {calendar} (--no-calendar 로 명시적 생략 가능)")
        else:
            cal = [l.strip()[:10] for l in p.read_text().splitlines() if l.strip()]
            lo, hi = df["date"].iloc[0], df["date"].iloc[-1]
            span = [d for d in cal if lo <= d <= hi]
            if span:
                ratio = len(df) / len(span)
                if not (0.98 <= ratio <= 1.02):
                    errs.append(f"캘린더대조: 내 {len(df)}일 vs kr_data {len(span)}일 "
                                f"(비율 {ratio:.3f}, 허용 0.98~1.02)")
            else:
                errs.append(f"캘린더대조: kr_data 캘린더에 {lo}~{hi} 구간이 없다")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-02")
    ap.add_argument("--end", default="2026-08-28")
    ap.add_argument("--out", default="/root/.qlib/bt_regime.parquet")
    ap.add_argument("--calendar", default=DEFAULT_CALENDAR)
    ap.add_argument("--no-calendar", action="store_true",
                    help="kr_data 캘린더 대조 생략 (로컬 등 캘린더가 없는 환경)")
    args = ap.parse_args()

    df = build(args.start, args.end)
    errs = validate(df, None if args.no_calendar else args.calendar)

    # 통과/실패와 무관하게 요일 분포를 항상 보여준다 — 사람 눈 검증용.
    dts = pd.to_datetime(df["date"])
    wd = dts.dt.weekday.value_counts()
    print("요일 분포:", {n: int(wd.get(i, 0)) for i, n in enumerate("월화수목금토일")})
    print(f"구간 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}  {len(df)}행  "
          f"us_lag 분포 {df['us_lag'].value_counts().sort_index().to_dict()}")

    if errs:
        print("\n✖ 검증 실패 — 파일을 쓰지 않는다:", file=sys.stderr)
        for e in errs:
            print("  -", e, file=sys.stderr)
        return 1

    df.to_parquet(args.out, index=False)
    print(f"✔ 검증 통과, 저장 {args.out}")
    print(df.tail(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
