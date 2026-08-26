#!/usr/bin/env python
"""레짐 라벨 — 미국 지수 등락을 한국 거래일에 붙인다.

미국 t일 **종가**는 한국 시간으로 t+1일 새벽에 확정된다. 그래서 한국 t+1일
09:00 주문 시점에 쓸 수 있는 정보다. 인덱스를 하루 밀어 join 하는 이유가 이것이고,
이 한 줄이 틀리면 **미래를 보고 매매하는 백테스트**가 된다.

한국 지수(^KS11/^KQ11)는 그날 **실제** 등락이므로 예측에 쓸 수 없다. 사후 오라클
(상한선) 계산에만 쓴다 — 예측 손실이 얼마인지 보이려고 함께 저장한다.
"""

from __future__ import annotations

import argparse

import pandas as pd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-06-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--out", default="/data/bt_regime.parquet")
    args = ap.parse_args()

    import yfinance as yf
    t = yf.download(["^GSPC", "^IXIC", "^KS11", "^KQ11"],
                    start=args.start, end=args.end,
                    progress=False, auto_adjust=True)["Close"]
    r = t.pct_change()

    # ★ 미국은 하루 뒤로 민다: t일 종가 → 한국 t+1일 아침에 알 수 있다.
    us = r[["^GSPC", "^IXIC"]].copy()
    us.index = us.index + pd.Timedelta(days=1)
    us.columns = ["sp", "nq"]

    kr = r[["^KS11", "^KQ11"]].copy()
    kr.columns = ["ks", "kq"]          # 사후 오라클 전용 — 예측에 쓰지 말 것

    df = us.join(kr, how="outer").dropna(subset=["nq"])
    df.index.name = "date"
    df = df.reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df.to_parquet(args.out, index=False)
    print(f"저장 {args.out}: {len(df)}행 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
    print(df.tail(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
