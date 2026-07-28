"""Why-did-we-buy-this explanations for daily signal picks.

Two layers, both display-only (trading never depends on them):

1. Intuitive metrics — plain-Korean momentum/volume/moving-average stats
   computed from the qlib store, plus a one-line rule-based summary.
2. Model attribution — LightGBM ``pred_contrib`` top features per stock,
   with Alpha158 feature names translated to Korean via prefix rules.

``build_reasons`` is called from generate_daily_signal inside a try/except:
any failure here must never block signal persistence.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ─── Alpha158 feature-name → Korean description ─────────────────────

_PREFIX_DESC = {
    "KMID": "캔들 몸통 비율", "KLEN": "캔들 길이", "KMID2": "몸통/전체 비율",
    "KUP": "위꼬리 비율", "KUP2": "위꼬리/전체 비율",
    "KLOW": "아래꼬리 비율", "KLOW2": "아래꼬리/전체 비율",
    "KSFT": "종가 위치 편향", "KSFT2": "종가 위치/전체 비율",
    "OPEN": "시가 수준", "HIGH": "고가 수준", "LOW": "저가 수준", "VWAP": "거래량가중평균가 수준",
    "ROC": "수익률 모멘텀", "MA": "이동평균 대비 가격", "STD": "가격 변동성",
    "BETA": "추세 기울기", "RSQR": "추세 신뢰도(R²)", "RESI": "추세 이탈(잔차)",
    "MAX": "기간 최고가 대비", "MIN": "기간 최저가 대비",
    "QTLU": "상위 분위 가격 대비", "QTLD": "하위 분위 가격 대비",
    "RANK": "가격 백분위 순위", "RSV": "스토캐스틱 위치",
    "IMAX": "고점 후 경과일", "IMIN": "저점 후 경과일", "IMXD": "고점-저점 간격",
    "CORR": "가격-거래량 상관", "CORD": "수익률-거래량변화 상관",
    "CNTP": "상승일 비율", "CNTN": "하락일 비율", "CNTD": "상승-하락일 차",
    "SUMP": "상승폭 비중(RSI형)", "SUMN": "하락폭 비중", "SUMD": "상승-하락폭 차",
    "VMA": "거래량 이동평균 대비", "VSTD": "거래량 변동성",
    "WVMA": "거래량가중 가격 변동성",
    "VSUMP": "거래량 증가 비중", "VSUMN": "거래량 감소 비중", "VSUMD": "거래량 증감 차",
}

_FEATURE_RE = re.compile(r"^([A-Z]+?)(\d+)?$")


def describe_feature(name: str) -> str:
    """'ROC60' → '수익률 모멘텀(60일)'. Unknown names pass through unchanged."""
    m = _FEATURE_RE.match(name.strip())
    if not m:
        return name
    prefix, window = m.group(1), m.group(2)
    desc = _PREFIX_DESC.get(prefix)
    if desc is None:
        return name
    return f"{desc}({window}일)" if window else desc


# ─── Intuitive metrics ──────────────────────────────────────────────


def _metrics_from_series(close: pd.Series, volume: pd.Series) -> dict:
    """Momentum/volume stats from (calendar-ordered) close & volume series."""
    c = close.dropna()
    if len(c) < 2:
        return {}
    last = float(c.iloc[-1])

    def _ret(n: int) -> float | None:
        if len(c) <= n:
            return None
        base = float(c.iloc[-(n + 1)])
        return round((last / base - 1) * 100, 2) if base > 0 else None

    out: dict = {"ret5": _ret(5), "ret20": _ret(20), "ret60": _ret(60)}

    if len(c) >= 20:
        ma20 = float(c.iloc[-20:].mean())
        out["ma20_gap"] = round((last / ma20 - 1) * 100, 2) if ma20 > 0 else None
    hi = float(c.iloc[-60:].max())
    out["high60_pos"] = round((last / hi - 1) * 100, 2) if hi > 0 else None

    v = volume.dropna()
    if len(v) >= 6 and float(v.iloc[-6:-1].mean()) > 0:
        out["vol_ratio"] = round(float(v.iloc[-1]) / float(v.iloc[-6:-1].mean()), 2)
    return out


def summarize(m: dict) -> str:
    """Rule-based one-liner in Korean, joined with ' · '."""
    parts: list[str] = []
    r5, r20 = m.get("ret5"), m.get("ret20")
    if r5 is not None and r5 >= 5:
        parts.append(f"최근 5일 +{r5}% 강한 모멘텀")
    elif r5 is not None and r5 <= -5:
        parts.append(f"최근 5일 {r5}% 조정 후 반등 포착")
    elif r20 is not None and abs(r20) >= 5:
        parts.append(f"최근 20일 {'+' if r20 > 0 else ''}{r20}%")
    vr = m.get("vol_ratio")
    if vr is not None and vr >= 2:
        parts.append(f"거래량 {vr}배 급증")
    hp = m.get("high60_pos")
    if hp is not None and hp >= -2:
        parts.append("60일 고점 근접")
    elif hp is not None and hp <= -20:
        parts.append(f"60일 고점 대비 {hp}% 낙폭 구간")
    gap = m.get("ma20_gap")
    if not parts and gap is not None and abs(gap) >= 3:
        parts.append(f"20일 이평 대비 {'+' if gap > 0 else ''}{gap}%")
    return " · ".join(parts) if parts else "모델 종합 점수 상위"


def build_intuitive_metrics(codes: list[str]) -> dict[str, dict]:
    """Per-code metrics from the qlib store (last ~70 trading days)."""
    from qlib.data import D

    out: dict[str, dict] = {}
    try:
        df = D.features(list(codes), ["$close", "$volume"], freq="day")
    except Exception as exc:  # noqa: BLE001
        log.warning("signal_reasons: D.features failed: %s", exc)
        return out
    if df is None or df.empty:
        return out
    for code, sub in df.groupby(level="instrument"):
        try:
            sub = sub.droplevel("instrument").tail(70)
            out[str(code)] = _metrics_from_series(sub["$close"], sub["$volume"])
        except Exception as exc:  # noqa: BLE001
            log.warning("signal_reasons: metrics failed for %s: %s", code, exc)
    return out


# ─── Model attribution (LightGBM pred_contrib) ──────────────────────


def top_model_features(model, dataset, codes: list[str], top_n: int = 5) -> dict[str, list[dict]]:
    """Per-code top-N contributing features for the latest test-date row."""
    out: dict[str, list[dict]] = {}
    try:
        from qlib.data.dataset.handler import DataHandlerLP
        booster = getattr(model, "model", None)
        if booster is None:
            return out
        feats = dataset.prepare("test", col_set="feature", data_key=DataHandlerLP.DK_I)
        if feats is None or feats.empty:
            return out
        last_dt = feats.index.get_level_values(0).max()
        snap = feats.loc[last_dt]
        cols = [c[1] if isinstance(c, tuple) else c for c in snap.columns]
        wanted = [c for c in codes if c in snap.index]
        if not wanted:
            return out
        X = snap.loc[wanted]
        contribs = booster.predict(X.values, pred_contrib=True)  # (n, n_feat+1)
        for i, code in enumerate(wanted):
            row = contribs[i][:-1]  # drop base value
            order = np.argsort(-np.abs(row))[:top_n]
            out[str(code)] = [
                {
                    "name": str(cols[j]),
                    "desc": describe_feature(str(cols[j])),
                    "contrib": round(float(row[j]), 5),
                }
                for j in order
            ]
    except Exception as exc:  # noqa: BLE001
        log.warning("signal_reasons: pred_contrib failed: %s", exc)
    return out


# ─── Combined entry point ───────────────────────────────────────────


def build_reasons(codes: list[str], model=None, dataset=None) -> dict[str, dict]:
    """Return {code: {"summary", "metrics", "top_features"}} — best effort."""
    metrics = build_intuitive_metrics(codes)
    features = top_model_features(model, dataset, codes) if model is not None and dataset is not None else {}
    out: dict[str, dict] = {}
    for code in codes:
        m = metrics.get(code, {})
        out[code] = {
            "summary": summarize(m),
            "metrics": m,
            "top_features": features.get(code, []),
        }
    return out
