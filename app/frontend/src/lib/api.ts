const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5001";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

// --- Types ---

export interface BacktestRequest {
  strategy_class: string;
  strategy_module: string;
  strategy_kwargs: Record<string, unknown>;
  model_class: string;
  model_module: string;
  model_kwargs: Record<string, unknown>;
  handler_class: string;
  handler_module: string;
  handler_kwargs: Record<string, unknown>;
  train_start: string;
  train_end: string;
  valid_start: string;
  valid_end: string;
  test_start: string;
  test_end: string;
  backtest_start: string;
  backtest_end: string;
  account: number;
  benchmark: string | null;
  freq: string;
  exchange: {
    limit_threshold: number | null;
    deal_price: string;
    open_cost: number;
    close_cost: number;
    min_cost: number;
    trade_unit: number | null;
  };
  instruments: string;
}

export type JobStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface BacktestJobResponse {
  job_id: string;
  status: JobStatus;
}

export interface BacktestMetrics {
  annualized_return: number | null;
  information_ratio: number | null;
  max_drawdown: number | null;
  mean: number | null;
  std: number | null;
}

export interface PortfolioPoint {
  date: string;
  value: number;
  ret: number | null;
  turnover: number | null;
  cost: number | null;
  cash: number | null;
  bench: number | null;
}

export interface ExtendedMetrics {
  sharpe: number | null;
  cumulative_return: number | null;
  calmar: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  total_cost: number | null;
  total_turnover: number | null;
  avg_daily_turnover: number | null;
  trading_days: number | null;
}

export interface RecommendedPick {
  rank: number;
  code: string;
  name: string | null;
  score: number | null;
  as_of: string;
}

export interface TradeOrder {
  code: string;
  name: string | null;
  side: "BUY" | "SELL";
  deal_amount: number | null;
  trade_price: number | null;
  trade_value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
}

export interface TradeDay {
  date: string;
  orders: TradeOrder[];
}

export interface BacktestResult {
  job_id: string;
  status: JobStatus;
  metrics: BacktestMetrics | null;
  extended_metrics: ExtendedMetrics | null;
  portfolio: PortfolioPoint[] | null;
  recommended_picks: RecommendedPick[] | null;
  recent_trades: TradeDay[] | null;
  benchmark_used: string | null;
  error: string | null;
}

export interface BacktestListItem {
  job_id: string;
  status: JobStatus;
  instruments: string;
  strategy_class: string;
  model_class: string;
  backtest_start: string;
  backtest_end: string;
  created_at: string;
}

export interface HealthResponse {
  status: string;
  qlib_initialized: boolean;
  provider_uri: string;
}

// --- Grid search ---

export interface CatalogEntry {
  class: string;
  module: string;
  kwargs: Record<string, unknown>;
}

export interface GridBacktestRequest {
  base: BacktestRequest;
  models: CatalogEntry[];
  strategies: CatalogEntry[];
  param_sweeps: Record<string, (number | string)[]>;
  max_jobs?: number;
}

export interface GridJobResponse {
  group_id: string;
  job_ids: string[];
  total: number;
}

export interface GridMember {
  job_id: string;
  status: JobStatus;
  config_summary: Record<string, unknown>;
  metrics: BacktestMetrics | null;
  error: string | null;
}

export interface GridResultResponse {
  group_id: string;
  total: number;
  completed: number;
  failed: number;
  jobs: GridMember[];
}

// --- API Functions ---

export const api = {
  health: () => fetchApi<HealthResponse>("/api/v1/health"),

  submitBacktest: (req: BacktestRequest) =>
    fetchApi<BacktestJobResponse>("/api/v1/backtests/", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  getBacktestResult: (jobId: string) =>
    fetchApi<BacktestResult>(`/api/v1/backtests/${jobId}`),

  listBacktests: () =>
    fetchApi<{ jobs: BacktestListItem[]; count: number }>("/api/v1/backtests/"),

  getCalendar: (start: string, end: string) =>
    fetchApi<{ dates: string[]; count: number }>(
      `/api/v1/data/calendar?start=${start}&end=${end}`
    ),

  getInstruments: (market: string) =>
    fetchApi<{ instruments: Array<{ code: string; start_time: string; end_time: string }>; count: number }>(
      `/api/v1/data/instruments?market=${market}`
    ),

  getMarkets: () =>
    fetchApi<{ markets: Array<{ name: string; description: string }> }>(
      "/api/v1/data/markets"
    ),

  submitGrid: (req: GridBacktestRequest) =>
    fetchApi<GridJobResponse>("/api/v1/backtests/grid", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  getGridResult: (groupId: string) =>
    fetchApi<GridResultResponse>(`/api/v1/backtests/grid/${groupId}`),

  // ── Live trading ───────────────────────────────────────────
  getLiveBalance: () => fetchApi<LiveBalanceResponse>("/api/v1/live/balance"),
  getLiveSignals: () => fetchApi<LiveSignalsResponse>("/api/v1/live/signals"),
  getLiveOrders: (limit = 100) =>
    fetchApi<LiveOrdersResponse>(`/api/v1/live/orders?limit=${limit}`),
  getLiveDailyPnL: (days = 180) =>
    fetchApi<DailyPnLResponse>(`/api/v1/live/pnl/daily?days=${days}`),
  getLivePositionHistory: (limit = 60) =>
    fetchApi<PositionHistoryResponse>(`/api/v1/live/positions/history?limit=${limit}`),
};

// ─── Live trading types ──────────────────────────────────────

export interface LiveHolding {
  code: string;
  name: string | null;
  qty: number;
  avg_price: number;
  eval_price: number;
  eval_value: number;
  pnl: number;
  pnl_pct: number;
}

export interface LiveBalanceResponse {
  cash: number;
  total_eval: number;
  holdings: LiveHolding[];
  fetched_at: string;
  mode: "real" | "paper" | "mock";
}

export interface LiveSignalRow {
  rank: number;
  code: string;
  name: string | null;
  score: number | null;
  as_of: string;
}

export interface LiveSignalsResponse {
  as_of: string | null;
  picks: LiveSignalRow[];
}

export interface LiveOrderRow {
  id: number;
  submitted_at: string;
  trade_date: string;
  code: string;
  name: string | null;
  side: "BUY" | "SELL";
  qty: number;
  price: number | null;
  status: string;
  error: string | null;
  kis_order_id: string | null;
}

export interface LiveOrdersResponse {
  orders: LiveOrderRow[];
}

export interface DailyPnLRow {
  trade_date: string;
  starting_equity: number;
  ending_equity: number;
  realised_pnl: number;
  unrealised_pnl: number;
  fees: number;
}

export interface DailyPnLResponse {
  rows: DailyPnLRow[];
}

export interface PositionSnapshotRow {
  snapshot_date: string;
  cash: number;
  total_eval: number;
  holdings: LiveHolding[];
}

export interface PositionHistoryResponse {
  snapshots: PositionSnapshotRow[];
}
