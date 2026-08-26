#!/usr/bin/env python
"""워크포워드 신호 생성기 — 진입 방식 백테스트의 비싼 절반.

`generate_daily_signal()`(live_trader.py:204)을 백테스트 루프용으로 다시 짠 것이다.
운영 함수를 그대로 못 쓰는 이유는 둘이다:

  1. `train_start` 가 호출부에 "2023-04-01" 로 **하드코딩**돼 있고 `today` 하나만 받는다.
  2. 매 호출이 Alpha158 핸들러를 **처음부터 다시 만든다.** 650일 루프에서 그러면
     같은 피처를 650번 계산한다.

여기서는 핸들러를 **한 번만** 만들고 `DatasetH` 의 segments 만 날짜별로 바꾼다.
`DatasetH.__init__` 은 핸들러 인스턴스를 그대로 재사용하므로(qlib/data/dataset/__init__.py)
하루치 비용이 LGBM 학습만 남는다.

**누수가 없는 근거** — 핸들러를 전 구간(끝 = --end)으로 만들어도 과거 예측이 미래를
보지 않는다:
  * Alpha158 피처는 전부 backward-looking(`Ref`/`Mean`/`Std` 등 과거 창).
  * 라벨 `Ref($close,-2)/Ref($close,-1)-1` 은 forward-looking 이지만 train/valid
    세그먼트에서만 쓰이고, 그 구간은 test 일(T) 이전이다.
  * `Alpha158` 기본 `infer_processors=[]` 이라 전역 `ZScoreNorm` 이 걸리지 않는다.
    `learn_processors` 는 `DropnaLabel` + `CSZScoreNorm`(**날짜별 횡단면**)뿐이라
    전 구간 통계를 쓰지 않는다. (qlib/contrib/data/handler.py:37-40, 98-106)
  이 조건이 깨지면(예: infer_processors 에 ZScoreNorm 추가) 캐싱은 즉시 무효다.

산출: `<out>/<YYYY-MM-DD>.parquet` — 그날 상위 SIGNAL_STORE_TOP_N 랭킹.
날짜별 파일이라 중단·재개가 자명하다. 8시간짜리 작업에는 이게 필수다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

log = logging.getLogger("backtest_signals")

# 운영과 같은 학습 시작일. 여기를 바꾸면 백테스트가 운영을 재현하지 못한다.
TRAIN_START = "2023-04-01"


def walk_forward(today: date, prev_trading_day) -> tuple[date, date, date]:
    """운영 `_walk_forward_windows`(live_trader.py:2535)와 **같은 식**.

    시작 고정·끝 확장(qlib RollingGen 의 ROLL_EX). train 은 today−3개월에서 끊고,
    valid 는 그 다음날부터 today 직전 거래일까지, test 는 today 하루다.
    valid_end 를 거래일로 스냅하는 이유는 운영 docstring 에 있다 — 월요일이면
    valid_end 가 일요일이 돼서 test 슬라이스가 비고 picks=0 이 됐었다.
    """
    ts = pd.Timestamp(today)
    train_end = (ts - pd.DateOffset(months=3)).date()
    valid_start = (ts - pd.DateOffset(months=2, days=29)).date()
    valid_end = prev_trading_day(today)
    return train_end, valid_start, valid_end


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="/tmp/bt_signals")
    ap.add_argument("--threads", type=int, default=8,
                    help="LGBM num_threads. 40코어를 다 쓰면 거래 크론과 다툰다.")
    ap.add_argument("--top", type=int, default=None,
                    help="저장할 랭크 수. 기본은 운영의 SIGNAL_STORE_TOP_N.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()

    from qlib.data import D
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.utils import init_instance_by_config

    from app.api.services.backtest_service import _extract_recommended_picks
    from app.api.services.live_trader import (
        LIVE_CONFIG, SIGNAL_STORE_TOP_N, _next_trading_day, _prev_trading_day,
        _stale_codes,
    )

    top_n = args.top or SIGNAL_STORE_TOP_N
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    days = [pd.Timestamp(c).date() for c in
            D.calendar(start_time=args.start, end_time=args.end)]
    todo = [d for d in days if not (out / f"{d.isoformat()}.parquet").exists()]
    log.info("대상 %d 거래일 (%s ~ %s), 이미 있는 것 제외하고 %d 일",
             len(days), args.start, args.end, len(todo))
    if not todo:
        log.info("전부 생성돼 있다. 할 일 없음.")
        return 0

    # ── 핸들러는 한 번만 ── 여기가 이 스크립트의 존재 이유다.
    t0 = time.time()
    handler = init_instance_by_config({
        "class": LIVE_CONFIG["handler_class"],
        "module_path": LIVE_CONFIG["handler_module"],
        "kwargs": {
            "instruments": LIVE_CONFIG["instruments"],
            "start_time": TRAIN_START,
            "end_time": max(todo).isoformat(),
            **LIVE_CONFIG.get("handler_kwargs", {}),
        },
    }, accept_types=DataHandlerLP)
    log.info("Alpha158 핸들러 1회 생성: %.1f초", time.time() - t0)

    # 스레드 상한만 덮어쓴다. 나머지 하이퍼파라미터는 운영과 같아야 한다.
    model_kwargs = {**LIVE_CONFIG.get("model_kwargs", {}), "num_threads": args.threads}

    ok = failed = 0
    for i, today in enumerate(todo, 1):
        t = time.time()
        try:
            train_end, valid_start, valid_end = walk_forward(today, _prev_trading_day)
            dataset = DatasetH(handler=handler, segments={
                "train": (TRAIN_START, train_end.isoformat()),
                "valid": (valid_start.isoformat(), valid_end.isoformat()),
                "test":  (valid_end.isoformat(), today.isoformat()),
            })
            model = init_instance_by_config({
                "class": LIVE_CONFIG["model_class"],
                "module_path": LIVE_CONFIG["model_module"],
                "kwargs": model_kwargs,
            })
            model.fit(dataset)
            pred = model.predict(dataset)
            if not hasattr(pred, "shape") or len(pred) == 0:
                raise RuntimeError("빈 예측")

            store_cfg = {**LIVE_CONFIG,
                         "strategy_kwargs": {**LIVE_CONFIG["strategy_kwargs"],
                                             "topk": top_n}}
            picks = _extract_recommended_picks(pred, store_cfg) or []

            # 운영과 같은 정지·상폐 필터. 과거 시점에도 봉 신선도로 계산 가능하다.
            try:
                stale = _stale_codes([p["code"] for p in picks], today, max_lag_days=5)
                if stale:
                    picks = [p for p in picks if p["code"] not in stale]
                    for r, p in enumerate(picks, 1):
                        p["rank"] = r
            except Exception as exc:  # noqa: BLE001
                log.warning("%s stale 필터 실패(무시): %s", today, exc)

            if not picks:
                raise RuntimeError("picks 0건")

            df = pd.DataFrame(picks)[["rank", "code", "score"]]
            # 파일 이름 = **생성일**, `as_of` = **소비일**(다음 거래일).
            # 운영 `Signal.as_of` 와 같은 뜻이다(generate_daily_signal:225) —
            # 오늘 저녁에 만든 신호를 내일 아침 시가에 쓴다. 둘을 헷갈리면
            # 백테스트 전체가 하루씩 밀린 채로 그럴듯한 숫자를 낸다.
            df["generated_on"] = today.isoformat()
            df["as_of"] = _next_trading_day(today).isoformat()
            # 원자적 쓰기 — 중간에 죽어도 반쪽 파일이 남지 않는다.
            tmp = out / f".{today.isoformat()}.tmp.parquet"
            df.to_parquet(tmp, index=False)
            os.replace(tmp, out / f"{today.isoformat()}.parquet")
            ok += 1
            uniq = df["score"].nunique()
            log.info("[%d/%d] %s  picks=%d  고유점수=%d  %.1f초",
                     i, len(todo), today, len(df), uniq, time.time() - t)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.error("[%d/%d] %s 실패: %s", i, len(todo), today, exc)

    log.info("완료: 성공 %d / 실패 %d", ok, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
