"""Surge-eve profile mining — what did tomorrow's surgers look like at today's close?

For every (stock, day D) in the local kr_data universe (~KOSPI200+KOSDAQ150,
~3y), label D as a SURGE-EVE if the D+1 close-to-close return ≥ +8% (and a
stronger ≥ +15% cohort). Snapshot D's close-time features, compare against a
same-date random control (next-day |ret| < 3%), and measure how often our
screener patterns (approximate B/A/R/C/D conditions) would have matched on
the eve. Research only — no strategy change (freeze principle 2026-08-07).

Run inside a worker container:
    ssh rocky-prod "docker exec -i <worker> python -" < scripts/mine_surge_eve.py
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, "/app")

import numpy as np
import pandas as pd


def load_panel() -> pd.DataFrame:
    from qlib.data import D
    inst = D.instruments("kospi200")
    df = D.features(inst, ["$open", "$high", "$low", "$close", "$volume"],
                    freq="day").reset_index()
    df.columns = ["code", "date", "open", "high", "low", "close", "volume"]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def eve_features(g: pd.DataFrame) -> pd.DataFrame:
    c, o, h, l, v = g["close"], g["open"], g["high"], g["low"], g["volume"]
    f = pd.DataFrame(index=g.index)
    f["code"], f["date"] = g["code"], g["date"]
    f["next_ret"] = c.shift(-1) / c - 1
    f["ret1"] = c / c.shift(1) - 1
    f["ret5"] = c / c.shift(5) - 1
    f["ret20"] = c / c.shift(20) - 1
    vol20 = v.shift(1).rolling(20).mean()
    f["vol_x"] = v / vol20
    f["ma20_gap"] = c / c.rolling(20).mean() - 1
    f["pos_vs_5d_high"] = c / h.shift(1).rolling(5).max() - 1
    f["off_30d_high"] = c / h.shift(1).rolling(30).max() - 1
    f["green"] = (c > o).astype(int)
    rng = (h - l).replace(0, np.nan)
    f["upper_wick"] = (h - np.maximum(c, o)) / rng
    up = (c > c.shift(1)).astype(int)
    f["consec_up"] = up * (up.groupby((up != up.shift()).cumsum()).cumcount() + 1)
    f["near_20d_low"] = (c / c.rolling(20).min() - 1 <= 0.05).astype(int)
    f["first_green_after_drop"] = ((f["green"] == 1) & (f["ret5"] <= -0.10)).astype(int)

    # Approximate screener patterns evaluated ON THE EVE (would we have held it?)
    prev_ret = c.shift(1) / c.shift(2) - 1
    day_range = (h - l) / c.shift(1)
    f["pat_B"] = ((prev_ret >= 0.15) & (day_range <= 0.06) & (c > l.shift(1))).astype(int)
    f["pat_A"] = ((c >= c.shift(1).rolling(30).max()) & (f["ret20"] >= 0.30)
                  & (f["vol_x"] >= 2)).astype(int)
    f["pat_R"] = ((c > h.shift(1).rolling(5).max()) & (h < h.shift(1).rolling(30).max())
                  & (f["ret20"] >= 0.15) & (f["vol_x"] >= 2)).astype(int)
    peak10 = c.rolling(10).max()
    base = c.shift(8).rolling(12).min()
    f["pat_C"] = ((peak10 / base >= 1.25)
                  & (c / peak10 - 1).between(-0.25, -0.08)
                  & (f["green"] == 1)).astype(int)
    ma20 = c.rolling(20).mean()
    f["pat_D"] = ((c / h.rolling(30).max() <= 0.60) & (f["ret1"] >= 0.05)
                  & (c > ma20) & (ma20 >= c.shift(1))).astype(int)
    return f


FEATS = ["ret1", "ret5", "ret20", "vol_x", "ma20_gap", "pos_vs_5d_high",
         "off_30d_high", "green", "upper_wick", "consec_up", "near_20d_low",
         "first_green_after_drop"]
PATS = ["pat_B", "pat_A", "pat_R", "pat_C", "pat_D"]


def main() -> None:
    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()
    panel = load_panel()
    feats = (panel.groupby("code", group_keys=False)
                  .apply(eve_features)
                  .dropna(subset=["next_ret", "ret20", "vol_x"]))

    surge8 = feats[feats["next_ret"] >= 0.08]
    surge15 = feats[feats["next_ret"] >= 0.15]
    control = (feats[feats["next_ret"].abs() < 0.03]
               .groupby("date", group_keys=False)
               .apply(lambda g: g.sample(min(len(g), 3), random_state=42)))

    def profile(df: pd.DataFrame) -> dict:
        out = {"n": int(len(df))}
        for k in FEATS:
            out[k] = round(float(df[k].mean()), 4)
        for p in PATS:
            out[p + "_pct"] = round(float(df[p].mean()) * 100, 2)
        out["any_pattern_pct"] = round(float((df[PATS].sum(axis=1) > 0).mean()) * 100, 2)
        return out

    result = {
        "period": [str(feats['date'].min().date()), str(feats['date'].max().date())],
        "universe_rows": int(len(feats)),
        "surge8": profile(surge8),
        "surge15": profile(surge15),
        "control": profile(control),
        "surge8_by_month": {str(k): int(v) for k, v in
                            surge8.groupby(surge8["date"].dt.to_period("M")).size().items()},
        "recent_surge8_examples": [
            {"date": str(r.date.date()), "code": r.code,
             "next_ret": round(float(r.next_ret) * 100, 1),
             "ret5": round(float(r.ret5) * 100, 1),
             "vol_x": round(float(r.vol_x), 1)}
            for r in surge8.sort_values("date").tail(12).itertuples()
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
