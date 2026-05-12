"""Orchestrates qlib backtest execution — called from Celery tasks."""

import json
import logging
import math
import traceback
from pathlib import Path

log = logging.getLogger(__name__)

import numpy as np
import pandas as pd

from qlib.contrib.evaluate import risk_analysis
from qlib.contrib.model.gbdt import LGBModel
from qlib.data import D
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.utils import init_instance_by_config
from qlib.backtest import backtest as qlib_backtest

# Lazy-load name mapping (KOSPI200/KOSDAQ150 + ETF) once per worker process.
_NAMES_PATH = Path(__file__).resolve().parents[1] / "core" / "kr_stock_names.json"
_NAMES_CACHE: dict[str, str] | None = None


def _stock_name(code: str) -> str:
    global _NAMES_CACHE
    if _NAMES_CACHE is None:
        try:
            _NAMES_CACHE = json.loads(_NAMES_PATH.read_text())
        except Exception:  # noqa: BLE001 - mapping is optional
            _NAMES_CACHE = {}
    return _NAMES_CACHE.get(str(code).strip(), str(code))

# qlib has no built-in KOSPI 200 instrument, so we benchmark against the
# KODEX 200 ETF (069500), which closely tracks the index and is fetched
# alongside the universe by scripts/kr_data_fetch.py.
KR_DEFAULT_BENCHMARK = "069500"


def run_backtest(config: dict) -> dict:
    """Run a full backtest pipeline: train model → predict → backtest.

    Returns
    -------
    dict with keys: metrics, extended_metrics, portfolio, recommended_picks, error
    """
    try:
        # 1. Build data handler
        handler_config = {
            "class": config["handler_class"],
            "module_path": config["handler_module"],
            "kwargs": {
                "instruments": config["instruments"],
                "start_time": config["train_start"],
                "end_time": config["test_end"],
                **config.get("handler_kwargs", {}),
            },
        }
        handler = init_instance_by_config(handler_config, accept_types=DataHandlerLP)

        # 2. Build dataset
        dataset = DatasetH(
            handler=handler,
            segments={
                "train": (config["train_start"], config["train_end"]),
                "valid": (config["valid_start"], config["valid_end"]),
                "test": (config["test_start"], config["test_end"]),
            },
        )

        # 3. Train model
        model_config = {
            "class": config["model_class"],
            "module_path": config["model_module"],
            "kwargs": config.get("model_kwargs", {}),
        }
        model = init_instance_by_config(model_config)
        model.fit(dataset)

        # 4. Generate predictions
        pred = model.predict(dataset)

        # 5. Build strategy config
        strategy_kwargs = config.get("strategy_kwargs", {}).copy()
        strategy_kwargs["signal"] = pred
        strategy_config = {
            "class": config["strategy_class"],
            "module_path": config["strategy_module"],
            "kwargs": strategy_kwargs,
        }

        # 6. Build executor config
        executor_config = {
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {
                "time_per_step": config.get("freq", "day"),
                "generate_portfolio_metrics": True,
            },
        }

        # 7. Build exchange kwargs
        exchange = config.get("exchange", {})
        exchange_kwargs = {
            "limit_threshold": exchange.get("limit_threshold", 0.30),
            "deal_price": exchange.get("deal_price", "close"),
            "open_cost": exchange.get("open_cost", 0.00015),
            "close_cost": exchange.get("close_cost", 0.00315),
            "min_cost": exchange.get("min_cost", 0),
            "codes": config["instruments"],
        }
        if exchange.get("trade_unit") is not None:
            exchange_kwargs["trade_unit"] = exchange["trade_unit"]

        # 8. Run backtest
        benchmark = config.get("benchmark") or KR_DEFAULT_BENCHMARK
        bt_start, bt_end = _clamp_to_calendar(
            config["backtest_start"], config["backtest_end"], config.get("freq", "day")
        )
        portfolio_metric, indicator = qlib_backtest(
            start_time=bt_start,
            end_time=bt_end,
            strategy=strategy_config,
            executor=executor_config,
            benchmark=benchmark,
            account=config.get("account", 10_000_000),
            exchange_kwargs=exchange_kwargs,
        )

        # 9. Extract portfolio time series
        report_df = portfolio_metric.get("1day", [None, None])
        report_normal = report_df[0] if isinstance(report_df, (list, tuple)) and len(report_df) > 0 else None
        # qlib returns ind["1day"] as (trade_indicator_df, Indicator) — the order
        # history we want lives on the *second* element.
        ind_pair = indicator.get("1day") if indicator else None
        indicator_obj = ind_pair[1] if isinstance(ind_pair, (list, tuple)) and len(ind_pair) > 1 else None

        metrics = None
        extended_metrics = None
        portfolio_series = None

        if isinstance(report_normal, pd.DataFrame) and not report_normal.empty:
            cols = report_normal.columns
            # Risk metrics from daily returns
            if "return" in cols:
                returns = report_normal["return"]
                risk = risk_analysis(returns, freq="day")
                risk_dict = risk["risk"].to_dict()
                metrics = {
                    "annualized_return": _safe_float(risk_dict.get("annualized_return")),
                    "information_ratio": _safe_float(risk_dict.get("information_ratio")),
                    "max_drawdown": _safe_float(risk_dict.get("max_drawdown")),
                    "mean": _safe_float(risk_dict.get("mean")),
                    "std": _safe_float(risk_dict.get("std")),
                }
                extended_metrics = _compute_extended_metrics(returns, report_normal)

            # Portfolio time series — qlib's column is "account", not "account_value".
            account_col = "account" if "account" in cols else ("account_value" if "account_value" in cols else None)
            if account_col is not None:
                portfolio_series = []
                for dt, row in report_normal.iterrows():
                    point = {
                        "date": dt.strftime("%Y-%m-%d") if isinstance(dt, pd.Timestamp) else str(dt),
                        "value": _safe_float(row.get(account_col, 0)),
                        "ret": _safe_float(row.get("return")),
                        "turnover": _safe_float(row.get("turnover")),
                        "cost": _safe_float(row.get("cost")),
                        "cash": _safe_float(row.get("cash")),
                        "bench": _safe_float(row.get("bench")),
                    }
                    portfolio_series.append(point)

        # 10. Recommended picks — top-K from the most recent prediction date
        recommended_picks = _extract_recommended_picks(pred, config)

        # 11. Recent trades from the indicator history (~3 months of trading days)
        recent_trades = _extract_recent_trades(indicator_obj, max_days=63)

        return {
            "metrics": metrics,
            "extended_metrics": extended_metrics,
            "portfolio": portfolio_series,
            "recommended_picks": recommended_picks,
            "recent_trades": recent_trades,
            "benchmark_used": benchmark,
            "error": None,
        }

    except Exception as e:
        return {
            "metrics": None,
            "extended_metrics": None,
            "portfolio": None,
            "recommended_picks": None,
            "recent_trades": None,
            "benchmark_used": None,
            "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
        }


# ─── Helpers ────────────────────────────────────────────────────────


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clamp_to_calendar(start: str, end: str, freq: str = "day") -> tuple[str, str]:
    # qlib's signal strategy looks one step ahead (shift=1) on every bar, so
    # running up to the provider's final trading day raises IndexError when
    # that lookahead lands at calendar[len(calendar)]. Always reserve at least
    # one trailing day.
    full = D.calendar(freq=freq)
    if len(full) < 2:
        raise RuntimeError("Provider calendar has fewer than 2 trading days; cannot backtest.")
    last_safe = pd.Timestamp(full[-2])
    cal = D.calendar(start_time=start, end_time=end, freq=freq)
    if len(cal) == 0:
        return pd.Timestamp(full[0]).strftime("%Y-%m-%d"), last_safe.strftime("%Y-%m-%d")
    clamped_end = min(pd.Timestamp(cal[-1]), last_safe)
    return pd.Timestamp(cal[0]).strftime("%Y-%m-%d"), clamped_end.strftime("%Y-%m-%d")


def _compute_extended_metrics(returns: pd.Series, report: pd.DataFrame) -> dict:
    """Sharpe / Calmar / win rate / profit factor / cumulative return / turnover."""
    out: dict = {}
    r = returns.dropna()
    if r.empty:
        return out

    # Annualized Sharpe (rf=0)
    mean_d = float(r.mean())
    std_d = float(r.std(ddof=0))
    out["sharpe"] = _safe_float((mean_d / std_d * math.sqrt(252)) if std_d > 0 else None)

    # Cumulative return over the full window
    cum = float((1 + r).prod() - 1)
    out["cumulative_return"] = _safe_float(cum)

    # Calmar = annualized return / |max drawdown|
    equity = (1 + r).cumprod()
    peak = equity.cummax()
    dd = (equity / peak - 1.0)
    mdd = float(dd.min()) if not dd.empty else 0.0
    if mdd < 0 and len(r) > 0:
        ann_ret = (1 + cum) ** (252 / len(r)) - 1
        out["calmar"] = _safe_float(ann_ret / abs(mdd))
    else:
        out["calmar"] = None

    # Win rate / profit factor
    wins = r[r > 0]
    losses = r[r < 0]
    out["win_rate"] = _safe_float(len(wins) / len(r))
    out["profit_factor"] = _safe_float(float(wins.sum()) / abs(float(losses.sum()))) if losses.sum() < 0 else None

    # Trading-cost / turnover summary
    if "total_cost" in report.columns:
        out["total_cost"] = _safe_float(float(report["total_cost"].iloc[-1]))
    if "total_turnover" in report.columns:
        out["total_turnover"] = _safe_float(float(report["total_turnover"].iloc[-1]))
    if "turnover" in report.columns:
        out["avg_daily_turnover"] = _safe_float(float(report["turnover"].mean()))

    out["trading_days"] = int(len(r))
    return out


def _extract_recommended_picks(pred, config: dict) -> list | None:
    """Top-K stocks ranked by the model's most recent prediction.

    These are what the strategy *would* hold the next session if you kept
    running it in production.

    Logs at every early-return / failure point. Previously the entire body
    was wrapped in `except: return None`, which silently produced picks=0
    in the live signal pipeline whenever anything went wrong.
    """
    try:
        if pred is None:
            log.warning("_extract_recommended_picks: pred is None")
            return None
        # pred is a Series with MultiIndex (datetime, instrument).
        # Some models return a DataFrame with a single 'score' column.
        if hasattr(pred, "columns"):
            if len(pred.columns) == 0:
                log.warning("_extract_recommended_picks: pred has empty columns")
                return None
            score = pred.iloc[:, 0]
        else:
            score = pred
        if score.empty:
            log.warning("_extract_recommended_picks: score Series is empty")
            return None
        last_dt = score.index.get_level_values(0).max()
        log.info(
            "_extract_recommended_picks: last_dt=%s total_rows=%d",
            last_dt, len(score),
        )
        snap = score.xs(last_dt, level=0).sort_values(ascending=False)
        log.info("_extract_recommended_picks: snap size at last_dt=%d", len(snap))
        topk = int(config.get("strategy_kwargs", {}).get("topk") or 10)
        head = snap.head(topk)
        return [
            {
                "rank": i + 1,
                "code": str(code),
                "name": _stock_name(str(code)),
                "score": _safe_float(val),
                "as_of": last_dt.strftime("%Y-%m-%d") if hasattr(last_dt, "strftime") else str(last_dt),
            }
            for i, (code, val) in enumerate(head.items())
        ]
    except Exception as exc:  # noqa: BLE001
        log.exception("_extract_recommended_picks failed: %s", exc)
        return None


def _extract_recent_trades(indicator_obj, max_days: int = 63) -> list | None:
    """Last N trading days' realized buys/sells with deal price, value, and per-trade P/L.

    P/L is computed via average-cost FIFO across the *entire* history so the
    last N days have correct realized profit on each sell. The truncation to
    the last `max_days` happens only after the running ledger is built.
    """
    try:
        if indicator_obj is None:
            return None
        history = getattr(indicator_obj, "order_indicator_his", None)
        if not history:
            return None

        # Per-stock running position: {"qty": float, "avg_cost": float}
        ledger: dict[str, dict[str, float]] = {}
        all_days: list[dict] = []

        for trade_dt, oi in history.items():
            day_str = trade_dt.strftime("%Y-%m-%d") if hasattr(trade_dt, "strftime") else str(trade_dt)
            try:
                amount = oi.get_index_data("deal_amount")
                price = oi.get_index_data("trade_price")
                value = oi.get_index_data("trade_value")
                direction = oi.get_index_data("trade_dir")
            except Exception:  # noqa: BLE001
                continue
            if amount is None or not hasattr(amount, "data"):
                continue
            amt_dict = dict(zip(amount.index, amount.data))
            px_dict = dict(zip(price.index, price.data)) if price is not None and hasattr(price, "data") else {}
            val_dict = dict(zip(value.index, value.data)) if value is not None and hasattr(value, "data") else {}
            dir_dict = dict(zip(direction.index, direction.data)) if direction is not None and hasattr(direction, "data") else {}

            day_orders: list[dict] = []
            for code, deal_amt in amt_dict.items():
                if deal_amt is None or (isinstance(deal_amt, float) and math.isnan(deal_amt)) or float(deal_amt) == 0:
                    continue
                direction_val = dir_dict.get(code)
                side = "BUY" if (direction_val is not None and float(direction_val) > 0) else "SELL"
                qty = abs(float(deal_amt))
                px = px_dict.get(code)
                px_f = _safe_float(px) or 0.0
                trade_val = val_dict.get(code)
                code_str = str(code)

                pnl: float | None = None
                pnl_pct: float | None = None
                pos = ledger.setdefault(code_str, {"qty": 0.0, "avg_cost": 0.0})
                if side == "SELL" and pos["qty"] > 1e-9 and px_f > 0:
                    avg = pos["avg_cost"]
                    pnl = (px_f - avg) * qty
                    pnl_pct = (px_f / avg - 1.0) if avg > 0 else None
                    pos["qty"] = max(pos["qty"] - qty, 0.0)
                    if pos["qty"] <= 1e-9:
                        pos["avg_cost"] = 0.0
                elif side == "BUY" and px_f > 0:
                    new_qty = pos["qty"] + qty
                    if new_qty > 0:
                        pos["avg_cost"] = (pos["qty"] * pos["avg_cost"] + qty * px_f) / new_qty
                    pos["qty"] = new_qty

                day_orders.append({
                    "code": code_str,
                    "name": _stock_name(code_str),
                    "side": side,
                    "deal_amount": _safe_float(deal_amt),
                    "trade_price": _safe_float(px),
                    "trade_value": _safe_float(trade_val),
                    "pnl": _safe_float(pnl),
                    "pnl_pct": _safe_float(pnl_pct),
                })

            if day_orders:
                all_days.append({"date": day_str, "orders": day_orders})

        return all_days[-max_days:] if all_days else None
    except Exception:  # noqa: BLE001
        return None
