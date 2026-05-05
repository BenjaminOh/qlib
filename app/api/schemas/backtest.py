from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExchangeConfig(BaseModel):
    limit_threshold: float | None = 0.30
    deal_price: str = "close"
    open_cost: float = 0.00015
    close_cost: float = 0.00315
    min_cost: float = 0
    trade_unit: int | None = 1


class BacktestRequest(BaseModel):
    # Strategy
    strategy_class: str = "TopkDropoutStrategy"
    strategy_module: str = "qlib.contrib.strategy.signal_strategy"
    strategy_kwargs: dict = Field(default_factory=lambda: {"topk": 20, "n_drop": 3})

    # Model
    model_class: str = "LGBModel"
    model_module: str = "qlib.contrib.model.gbdt"
    model_kwargs: dict = Field(default_factory=dict)

    # Dataset handler
    handler_class: str = "Alpha158"
    handler_module: str = "qlib.contrib.data.handler"
    handler_kwargs: dict = Field(default_factory=dict)

    # Data segments
    train_start: str = "2020-01-01"
    train_end: str = "2022-12-31"
    valid_start: str = "2023-01-01"
    valid_end: str = "2023-06-30"
    test_start: str = "2023-07-01"
    test_end: str = "2024-12-31"

    # Backtest
    backtest_start: str = "2023-07-01"
    backtest_end: str = "2024-12-31"
    account: float = 10_000_000
    benchmark: str | None = None
    freq: str = "day"

    # Exchange
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)

    # Instruments
    instruments: str = "kospi200"


class BacktestJobResponse(BaseModel):
    job_id: str
    status: JobStatus


class BacktestMetrics(BaseModel):
    annualized_return: float | None = None
    information_ratio: float | None = None
    max_drawdown: float | None = None
    mean: float | None = None
    std: float | None = None


class PortfolioPoint(BaseModel):
    date: str
    value: float
    ret: float | None = None
    turnover: float | None = None
    cost: float | None = None
    cash: float | None = None
    bench: float | None = None


class ExtendedMetrics(BaseModel):
    sharpe: float | None = None
    cumulative_return: float | None = None
    calmar: float | None = None
    win_rate: float | None = None
    profit_factor: float | None = None
    total_cost: float | None = None
    total_turnover: float | None = None
    avg_daily_turnover: float | None = None
    trading_days: int | None = None


class RecommendedPick(BaseModel):
    rank: int
    code: str
    name: str | None = None
    score: float | None = None
    as_of: str


class TradeOrder(BaseModel):
    code: str
    name: str | None = None
    side: str  # "BUY" | "SELL"
    deal_amount: float | None = None
    trade_price: float | None = None
    trade_value: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None


class TradeDay(BaseModel):
    date: str
    orders: list[TradeOrder]


class BacktestResultResponse(BaseModel):
    job_id: str
    status: JobStatus
    metrics: BacktestMetrics | None = None
    extended_metrics: ExtendedMetrics | None = None
    portfolio: list[PortfolioPoint] | None = None
    recommended_picks: list[RecommendedPick] | None = None
    recent_trades: list[TradeDay] | None = None
    benchmark_used: str | None = None
    error: str | None = None


class BacktestListItem(BaseModel):
    job_id: str
    status: JobStatus
    instruments: str
    strategy_class: str
    model_class: str
    backtest_start: str
    backtest_end: str
    created_at: str
    group_id: str | None = None


class BacktestListResponse(BaseModel):
    jobs: list[BacktestListItem]
    count: int


# ─── Grid search ────────────────────────────────────────────────────


class CatalogEntry(BaseModel):
    class_: str = Field(alias="class")
    module: str
    kwargs: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class GridBacktestRequest(BaseModel):
    """Cartesian product of (model × strategy × parameter sweep) backtests.

    Each sweep key uses dotted notation against the BacktestRequest dict, e.g.
    "strategy_kwargs.topk", "model_kwargs.learning_rate", "instruments".
    """

    base: BacktestRequest
    models: list[CatalogEntry]
    strategies: list[CatalogEntry]
    param_sweeps: dict[str, list] = Field(default_factory=dict)
    # Hard cap to keep one accidental click from queueing 10k jobs.
    max_jobs: int = 100


class GridJobResponse(BaseModel):
    group_id: str
    job_ids: list[str]
    total: int


class GridMember(BaseModel):
    job_id: str
    status: JobStatus
    config_summary: dict
    metrics: BacktestMetrics | None = None
    error: str | None = None


class GridResultResponse(BaseModel):
    group_id: str
    total: int
    completed: int
    failed: int
    jobs: list[GridMember]
