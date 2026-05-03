"""Service layer wrapping qlib's data APIs (D.calendar, D.instruments, D.features)."""

import math

import pandas as pd

from qlib.data import D


def get_calendar(start_time: str, end_time: str, freq: str = "day") -> list[str]:
    cal = D.calendar(start_time=start_time, end_time=end_time, freq=freq)
    return [ts.strftime("%Y-%m-%d") for ts in cal]


def get_instruments(market: str) -> list[dict]:
    inst = D.instruments(market=market)
    # D.instruments returns two shapes:
    #   - Static mapping {code: (start, end)} when the market file is a direct code list
    #   - Dynamic config dict {"market": ..., "filter_pipe": [...]} for named markets
    # Detect the config shape by its known keys, otherwise treat as static mapping.
    if isinstance(inst, dict) and "market" in inst and "filter_pipe" in inst:
        codes = D.list_instruments(instruments=inst, as_list=True)
        return [{"code": code, "start_time": "", "end_time": ""} for code in sorted(codes)]
    if isinstance(inst, dict):
        return [
            {
                "code": code,
                "start_time": pd.Timestamp(times[0]).strftime("%Y-%m-%d"),
                "end_time": pd.Timestamp(times[1]).strftime("%Y-%m-%d"),
            }
            for code, times in sorted(inst.items())
        ]
    codes = D.list_instruments(instruments=inst, as_list=True)
    return [{"code": code, "start_time": "", "end_time": ""} for code in sorted(codes)]


def get_features(
    instruments: list[str],
    fields: list[str],
    start_time: str,
    end_time: str,
    freq: str = "day",
) -> list[dict]:
    df = D.features(instruments, fields, start_time=start_time, end_time=end_time, freq=freq)
    if df.empty:
        return []

    rows = []
    for (instrument, dt), values in df.iterrows():
        row_values = {}
        for field_name, val in zip(df.columns, values):
            row_values[field_name] = None if (isinstance(val, float) and math.isnan(val)) else float(val)
        rows.append(
            {
                "datetime": dt.strftime("%Y-%m-%d") if isinstance(dt, pd.Timestamp) else str(dt),
                "instrument": instrument,
                "values": row_values,
            }
        )
    return rows
