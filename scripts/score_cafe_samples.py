#!/usr/bin/env python
"""카페 추천 표본 사후 채점 — "그 추천을 따라 샀으면 돈이 됐는가".

표본 대장(docs/06-research/data/cafe-samples.json)을 읽어 종목별 일봉을 받고,
진입가 3종(문서 기준가 / 수령일 종가 / 익일 시가) 각각에 대해 D+1..D+10 수익률,
손절 터치, 목표 달성, 손절 규칙 적용 실현손익을 계산한다.

일봉은 yfinance 가 주 소스다(기간 무제한·OHLCV 전부). 받은 원데이터는 CSV 로
박제해 저장소에 커밋한다 — KIS 30봉 창은 매일 밀려 과거가 사라지지만 이 CSV 는
남는다. 이후 --from-cache 로 네트워크 없이 같은 결과를 재현한다.

사용:
    python scripts/score_cafe_samples.py                 # 받아서 채점 + 캐시 갱신
    python scripts/score_cafe_samples.py --from-cache    # 캐시로만 재채점
    python scripts/score_cafe_samples.py --verify-kis    # KIS 30봉과 교차검증
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/06-research/data/cafe-samples.json"
# 박제는 JSON 이다 — CSV 는 .gitignore:15 `*.csv` 에 걸려 **조용히 커밋에서 빠진다.**
# 이 파일의 존재 이유가 "30봉 창이 닫힌 뒤에도 재채점"이므로 커밋되지 않으면 무의미하다.
CACHE = ROOT / "docs/06-research/data/cafe-samples-ohlcv.json"
BENCH = {"^KQ11": "코스닥", "^KS11": "코스피"}
HORIZONS = (1, 3, 5, 10)


# ── 데이터 수집 ──────────────────────────────────────────────────────────
def fetch_yf(code: str, start: str, end: str) -> pd.DataFrame:
    """코스닥(.KQ) 우선, 빈 결과면 코스피(.KS) 재시도.

    표본은 전부 kospi200/kosdaq150 밖이라 kr_universes 의 접미사 판정
    (kr_data_fetch.kr_code_to_yahoo)이 전부 .KS 를 붙인다 — 그래서 쓰지 않는다.
    """
    import yfinance as yf

    for suffix in (".KQ", ".KS"):
        try:
            df = yf.Ticker(code + suffix).history(
                start=start, end=end, auto_adjust=False
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {code}{suffix} 예외: {exc!r}"[:100], file=sys.stderr)
            continue
        if df.empty:
            continue
        df = df.reset_index()
        df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        out = df[["date", "Open", "High", "Low", "Close", "Volume"]].copy()
        out.columns = ["date", "open", "high", "low", "close", "volume"]
        out.insert(0, "code", code)
        out.insert(1, "ticker", code + suffix)
        return out
    return pd.DataFrame()


def fetch_index(symbol: str, start: str, end: str) -> pd.DataFrame:
    """벤치마크 지수 — 종목과 같은 스키마로 받아 같은 표에 담는다."""
    import yfinance as yf

    try:
        df = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=False)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    out = df[["date", "Open", "High", "Low", "Close", "Volume"]].copy()
    out.columns = ["date", "open", "high", "low", "close", "volume"]
    out.insert(0, "code", symbol)
    out.insert(1, "ticker", symbol)
    return out


def load_bars(samples: list[dict], from_cache: bool) -> pd.DataFrame:
    if from_cache:
        if not CACHE.exists():
            sys.exit(f"캐시 없음: {CACHE}")
        payload = json.loads(CACHE.read_text(encoding="utf-8"))
        return pd.DataFrame(payload["rows"])

    codes = sorted({s["code"] for s in samples})
    start = min(s["recv_date"] for s in samples)
    start = (pd.Timestamp(start) - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"# yfinance 수집: {len(codes)}종목 {start}~{end}", file=sys.stderr)

    frames, failed = [], []
    for code in codes:
        df = fetch_yf(code, start, end)
        if df.empty:
            failed.append(code)
            print(f"  ✗ {code} 0행", file=sys.stderr)
            continue
        frames.append(df)
        print(f"  ✓ {code} {len(df)}행 ({df.iloc[0]['date']}~{df.iloc[-1]['date']})",
              file=sys.stderr)
    if failed:
        # 빈 DataFrame 을 성공으로 오인하지 않는다 (kr_data_refresh.py:81 선례)
        sys.exit(f"수집 실패 {len(failed)}종목: {failed} — 채점 중단")

    # 벤치마크 지수 — 같은 5일 창의 시장 수익률을 빼야 베타를 실력으로 오독하지 않는다
    for sym, label in BENCH.items():
        idx = fetch_index(sym, start, end)
        if idx.empty:
            print(f"  ! 지수 {sym}({label}) 수집 실패 — 초과수익 계산 생략", file=sys.stderr)
        else:
            frames.append(idx)
            print(f"  ✓ {sym} {label} {len(idx)}행", file=sys.stderr)

    bars = pd.concat(frames, ignore_index=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(
        {"fetched_at": pd.Timestamp.now().isoformat(timespec="seconds"),
         "source": "yfinance", "adjust": "raw(auto_adjust=False)",
         "rows": bars.to_dict("records")}, ensure_ascii=False), encoding="utf-8")
    print(f"# 박제: {CACHE} ({len(bars)}행)", file=sys.stderr)
    return bars


# ── 채점 ────────────────────────────────────────────────────────────────
def score_one(s: dict, bars: pd.DataFrame) -> dict:
    """표본 1건 채점. D+0 = 수령일."""
    b = bars[bars["code"] == s["code"]].sort_values("date").reset_index(drop=True)
    idx = b.index[b["date"] == s["recv_date"]]
    r: dict = {"no": s["no"], "recv_date": s["recv_date"], "name": s["name"],
               "code": s["code"], "flags": []}
    if len(idx) == 0:
        r["flags"].append("수령일 봉 없음(휴장/미상장)")
        return r
    i0 = int(idx[0])
    day0 = b.iloc[i0]
    fwd = b.iloc[i0 + 1:].reset_index(drop=True)      # D+1 이후
    r["n_fwd"] = len(fwd)
    r["day0_close"] = float(day0["close"])
    r["day0_low"] = float(day0["low"])

    # 손절: 명시값 우선, "오늘 저가"만 말한 건은 수령일 저가로 산출
    stop = s.get("stop")
    if stop is None and s.get("stop_rule") == "day0_low":
        stop = float(day0["low"])
        r["flags"].append("손절=수령일 저가 산출")
    r["stop"] = stop
    r["target"] = s.get("target")

    # 기준가 정합성 — 문서 기준가가 수령일 봉 범위 밖이면 플래그
    ref = s.get("ref_price")
    if ref is not None and not (day0["low"] * 0.99 <= ref <= day0["high"] * 1.01):
        r["flags"].append(
            f"기준가 {ref:,.0f} 가 수령일 범위 밖 "
            f"({day0['low']:,.0f}~{day0['high']:,.0f})")

    # 진입가 3종
    entries = {
        "ref": float(ref) if ref is not None else None,       # 문서 기준가
        "close0": float(day0["close"]),                        # 수령일 종가(종배)
        "open1": float(fwd.iloc[0]["open"]) if len(fwd) else None,  # 익일 시가
    }
    r["entries"] = entries

    # 손절 터치 (D+1 이후 저가 기준) — 갭하락이면 시가 체결로 더 나쁘게 잡는다
    hit_day = hit_px = None
    if stop is not None:
        for _, row in fwd.iterrows():
            if row["low"] <= stop:
                hit_day, hit_px = row["date"], (
                    float(row["open"]) if row["open"] < stop else float(stop))
                break
    r["stop_hit_date"], r["stop_fill"] = hit_day, hit_px

    # 목표 달성 (D+1 이후 고가 기준)
    tgt_day = None
    if s.get("target"):
        for _, row in fwd.iterrows():
            if row["high"] >= s["target"]:
                tgt_day = row["date"]
                break
    r["target_hit_date"] = tgt_day

    # 진입가별 성과
    r["perf"] = {}
    for key, entry in entries.items():
        if not entry:
            continue
        p: dict = {"entry": entry}
        for h in HORIZONS:
            p[f"d{h}"] = (round((float(fwd.iloc[h - 1]["close"]) / entry - 1) * 100, 2)
                          if len(fwd) >= h else None)
        w5 = fwd.iloc[:5]
        if len(w5):
            p["max_up5"] = round((float(w5["high"].max()) / entry - 1) * 100, 2)
            p["max_dn5"] = round((float(w5["low"].min()) / entry - 1) * 100, 2)
        # 손절 규칙을 적용한 실현손익: 터치했으면 그 자리에서 청산, 아니면 D+5 종가
        if hit_px is not None:
            p["realized"] = round((hit_px / entry - 1) * 100, 2)
            p["realized_by"] = "손절"
        elif len(fwd) >= 5:
            p["realized"] = round((float(fwd.iloc[4]["close"]) / entry - 1) * 100, 2)
            p["realized_by"] = "D+5 종가"
        else:
            p["realized"], p["realized_by"] = None, "관측중"
        r["perf"][key] = p
    return r


def agg(rows: list[dict], key: str, field: str) -> dict:
    vals = [r["perf"][key][field] for r in rows
            if r.get("perf", {}).get(key, {}).get(field) is not None]
    if not vals:
        return {"n": 0}
    ser = pd.Series(vals)
    return {"n": len(vals), "mean": round(ser.mean(), 2),
            "median": round(ser.median(), 2),
            "win": int((ser > 0).sum()),
            "win_pct": round((ser > 0).mean() * 100, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true", help="네트워크 없이 캐시로 채점")
    ap.add_argument("--json-out", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    samples = ledger["samples"]
    bars = load_bars(samples, args.from_cache)
    bars["code"] = bars["code"].astype(str)

    rows = [score_one(s, bars) for s in samples]

    # ── 전표 ──
    print("\n## 전표 (진입가 = 수령일 종가 기준)\n")
    print("| # | 수령일 | 종목 | 기준가 | 종가 | 손절 | D+1 | D+3 | D+5 | D+10 | "
          "5일최대↑ | 5일최대↓ | 손절터치 | 목표 | 실현 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    def pct(v):
        return f"{v:+.1f}%" if isinstance(v, (int, float)) else "—"

    def won(v):
        return f"{v:,.0f}" if isinstance(v, (int, float)) else None

    for r in rows:
        p = r.get("perf", {}).get("close0", {})
        ref = won(r.get("entries", {}).get("ref")) or "⏳"
        c0 = won(r.get("day0_close")) or "—"
        st = won(r.get("stop")) or "—"
        hit = r["stop_hit_date"][5:] if r.get("stop_hit_date") else "—"
        if r.get("target_hit_date"):
            tgt = r["target_hit_date"][5:]
        else:
            tgt = "—" if r.get("target") else "·"
        cells = [str(r["no"]), r["recv_date"][5:], r["name"], ref, c0, st,
                 pct(p.get("d1")), pct(p.get("d3")), pct(p.get("d5")),
                 pct(p.get("d10")), pct(p.get("max_up5")), pct(p.get("max_dn5")),
                 hit, tgt, pct(p.get("realized"))]
        print("| " + " | ".join(cells) + " |")

    # ── 진입가 3분모 비교 ──
    print("\n## 진입가 3종 비교 — '언제 사느냐'\n")
    print("| 진입 기준 | 표본 | D+1 평균 | D+1 승률 | D+5 평균 | D+5 승률 | "
          "실현 평균 | 실현 승률 |")
    print("|---|---|---|---|---|---|---|---|")
    labels = {"ref": "① 문서 기준가", "close0": "② 수령일 종가(종배)",
              "open1": "③ 익일 시가"}
    for key, label in labels.items():
        d1, d5, rz = agg(rows, key, "d1"), agg(rows, key, "d5"), agg(rows, key, "realized")
        g = lambda a, k: f"{a[k]:+.2f}%" if a.get("n") and k in a else "—"
        w = lambda a: f"{a['win']}/{a['n']} ({a['win_pct']}%)" if a.get("n") else "—"
        print(f"| {label} | {d1.get('n', 0)} | {g(d1,'mean')} | {w(d1)} | "
              f"{g(d5,'mean')} | {w(d5)} | {g(rz,'mean')} | {w(rz)} |")

    # ── 집계 ──
    scored = [r for r in rows if r.get("perf")]
    hits = [r for r in scored if r.get("stop_hit_date")]
    tgts = [r for r in scored if r.get("target")]
    tgt_hit = [r for r in tgts if r.get("target_hit_date")]
    rz = [r["perf"]["close0"]["realized"] for r in scored
          if r["perf"].get("close0", {}).get("realized") is not None]
    wins = [v for v in rz if v > 0]
    losses = [v for v in rz if v <= 0]
    print("\n## 집계 (수령일 종가 진입 기준)\n")
    print(f"- 채점 가능 표본: **{len(scored)}/{len(rows)}건**")
    hit_list = ", ".join(f"{r['no']}호" for r in hits) or "없음"
    tgt_list = ", ".join(f"{r['no']}호" for r in tgt_hit) or "없음"
    hit_pct = len(hits) / max(1, len(scored)) * 100
    print(f"- 손절 터치: **{len(hits)}/{len(scored)}건 ({hit_pct:.0f}%)** — {hit_list}")
    print(f"- 목표 명시 {len(tgts)}건 중 달성 **{len(tgt_hit)}건** — {tgt_list}")
    if rz:
        print(f"- 실현손익(손절 적용, 미터치는 D+5 종가): 평균 **{sum(rz)/len(rz):+.2f}%** · "
              f"중앙 {pd.Series(rz).median():+.2f}% · 승 {len(wins)}/{len(rz)}")
        if wins and losses:
            print(f"- 손익비: 평균익 {sum(wins)/len(wins):+.2f}% / "
                  f"평균손 {sum(losses)/len(losses):+.2f}% = "
                  f"**{abs(sum(wins)/len(wins) / (sum(losses)/len(losses))):.2f}**")
    for h in HORIZONS:
        a = agg(scored, "close0", f"d{h}")
        if a.get("n"):
            print(f"- D+{h}: 평균 {a['mean']:+.2f}% · 중앙 {a['median']:+.2f}% · "
                  f"승률 {a['win_pct']}% (n={a['n']})")

    flagged = [(r["no"], r["flags"]) for r in rows if r.get("flags")]
    if flagged:
        print("\n## 플래그\n")
        for no, fl in flagged:
            print(f"- {no}호: {'; '.join(fl)}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n# JSON: {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
