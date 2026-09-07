const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5001";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...options,
  });
  if (res.status === 401) {
    // Session expired or never authenticated — bounce to login. Preserve the
    // current location as `?next=` so we land back here after re-auth.
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      const next = window.location.pathname + window.location.search;
      window.location.href = `/login?next=${encodeURIComponent(next)}`;
    }
    throw new Error("Authentication required");
  }
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
  /** Omitted since 2026-08-18 — /health is unauthenticated and this
   *  exposed a container-internal path. Kept optional for older clients. */
  provider_uri?: string;
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
  /** account="main" 기본 계좌, "cafe" 카페 실매매 계좌. 합산하지 않는다. */
  getLiveBalance: (account: "main" | "cafe" = "main") =>
    fetchApi<LiveBalanceResponse>(`/api/v1/live/balance?account=${account}`),
  getHalt: () => fetchApi<HaltStatus>("/api/v1/live/halt"),
  /** reason=null releases the halt; any string engages it. */
  setHalt: (reason: string | null) =>
    fetchApi<HaltStatus>("/api/v1/live/halt", {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  getAccounts: () => fetchApi<AccountPolicyRow[]>("/api/v1/live/accounts"),
  updateAccount: (
    accountId: string,
    body: Pick<AccountPolicyRow, "label" | "buy" | "sell">,
  ) =>
    fetchApi<AccountPolicyRow>(`/api/v1/live/accounts/${accountId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getLiveSignals: () => fetchApi<LiveSignalsResponse>("/api/v1/live/signals"),
  /**
   * 종목별 매매 이력. `strategy` 를 넘기지 않으면 서버가 "open" 원장을 본다 —
   * 카페 계좌 종목을 인자 없이 조회하면 늘 빈 목록이 나오던 이유다.
   */
  getStockTrades: (code: string, strategy?: string) =>
    fetchApi<StockTrade[]>(
      `/api/v1/live/stock/${code}/trades` +
        (strategy ? `?strategy=${strategy}` : ""),
    ),
  getRecentExits: () => fetchApi<ExitRow[]>("/api/v1/live/exits"),
  getStockCurves: (strategy = "open", days = 120) =>
    fetchApi<StockCurvesResponse>(
      `/api/v1/live/stocks/curves?strategy=${strategy}&days=${days}`,
    ),
  getTodayRealized: () =>
    fetchApi<TodayRealized>("/api/v1/live/realized/today"),
  getLiveOrders: (limit = 100, view: "all" | "real" | "sim" = "real", strategy?: string) =>
    fetchApi<LiveOrdersResponse>(
      `/api/v1/live/orders?limit=${limit}&view=${view}` +
        (strategy ? `&strategy=${strategy}` : ""),
    ),
  getOrderStory: (orderId: number) =>
    fetchApi<OrderStory>(`/api/v1/live/orders/${orderId}/story`),
  getCafeCandidates: (days = 7) =>
    fetchApi<CafeCandidatesResponse>(`/api/v1/live/cafe/candidates?days=${days}`),
  getSurgePicks: (days = 7) =>
    fetchApi<SurgePicksResponse>(`/api/v1/live/surge/picks?days=${days}`),
  getRetro: (strategy = "open") =>
    fetchApi<RetroResponse>(`/api/v1/live/retro?strategy=${strategy}`),
  getLiveDailyPnL: (days = 180) =>
    fetchApi<DailyPnLResponse>(`/api/v1/live/pnl/daily?days=${days}`),
  getLivePositionHistory: (limit = 60) =>
    fetchApi<PositionHistoryResponse>(`/api/v1/live/positions/history?limit=${limit}`),
};

/** TradingView chart for a Korean stock (KRX:code covers KOSPI+KOSDAQ). */
export const chartUrl = (code: string) => `https://kr.tradingview.com/chart/?symbol=KRX%3A${code}`;

// ─── Live trading types ──────────────────────────────────────

export interface StockTradeReasons extends SignalReasons {
  action?: "buy" | "sell";
  basis?: string;
}

export interface StockTrade {
  order_id: number | null;
  trade_date: string;
  strategy: string;
  side: "BUY" | "SELL";
  qty: number;
  status: string;
  error: string | null;
  exec_price: number | null;
  price_est: boolean;
  cum_qty: number | null;
  avg_price: number | null;
  day_close: number | null;
  ret_pct: number | null;
  pnl_amt: number | null;
  realized_pnl: number | null;
  reasons: StockTradeReasons | null;
}

/** 보유 수량 중 한 전략에 귀속된 몫. qty 의 합은 항상 보유 수량과 같다. */
export interface HoldingStrategy {
  strategy: string;
  qty: number;
  ledger_qty: number;
  /** false = 정산되지 않은 주문에서 추정한 값 */
  confirmed: boolean;
}

export interface LiveHolding {
  code: string;
  name: string | null;
  qty: number;
  avg_price: number;
  eval_price: number;
  eval_value: number;
  pnl: number;
  pnl_pct: number;
  /** 전략 귀속. 구버전 API 와 섞여도 깨지지 않도록 옵셔널. */
  strategies?: HoldingStrategy[];
  /** "confirmed" | "inferred" | "mismatch" | "unknown" */
  attribution?: string;
  /**
   * 불일치의 **원인**. `attribution` 은 어긋났다는 사실만 말한다.
   *   "ladder_pending" — (구) 사다리 예약이 체결됐는데 ※ 2026-09-07 제거, 과거 원장 전용 — 원장이 아직 모른다.
   *                      16:00 잔고 대사가 채운다. 정상 과도기다.
   *   null/undefined  — 설명되지 않는 차이. 경고를 유지한다.
   */
  attribution_reason?: string | null;
}

export interface HaltStatus {
  halted: boolean;
  reason: string | null;
}

/** Per-account order-execution policy (see /live/accounts). */
export type OrdType = "market" | "limit";
export type PriceBase = "prev_close" | "open" | "quote";

export interface AccountSidePolicy {
  ord_type: OrdType;
  /** null while ord_type is "market" — nothing to measure an offset from. */
  base: PriceBase | null;
  /** 0.03 = 매수 −3% / 매도 +3%. */
  offset_pct: number;
  /** "15:20"; null leaves an unfilled limit resting until the close. */
  cancel_hhmm: string | null;
}

export interface AccountPolicyRow {
  account_id: string;
  label: string | null;
  buy: AccountSidePolicy;
  sell: AccountSidePolicy;
  updated_at: string | null;
}

export interface LiveBalanceResponse {
  cash: number;
  total_eval: number;
  holdings: LiveHolding[];
  /** When the numbers were read from KIS — for a stale payload, the last-known-good time. */
  fetched_at: string;
  mode: "real" | "paper" | "mock";
  /** Freshness of this payload. */
  /** no_account = 해당 계좌 자격증명 미설정 (에러가 아님) */
  source?: "live" | "cache" | "stale" | "db" | "empty" | "no_account";
  /** True when KIS was unreachable and these are last-known-good numbers. */
  stale?: boolean;
}

export interface SignalReasonFeature {
  name: string;
  desc: string;
  contrib: number;
}

export interface SignalReasons {
  summary: string;
  metrics: {
    ret5?: number | null;
    ret20?: number | null;
    ret60?: number | null;
    ma20_gap?: number | null;
    high60_pos?: number | null;
    vol_ratio?: number | null;
  };
  top_features: SignalReasonFeature[];
  /** Same feature columns for every pick — comparison-table ready. */
  common_features?: SignalReasonFeature[];
  /** 상위 X% within the full scored universe (stored at generation time). */
  universe_pct?: number;
  universe_n?: number;
}

export interface LiveSignalRow {
  rank: number;
  code: string;
  name: string | null;
  score: number | null;
  as_of: string;
  last_close: number | null;
  reasons: SignalReasons | null;
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
  /** net — 수수료·세금 차감. 원장(Fill.pnl)·곡선·텔레그램과 같은 숫자다. */
  realized_pnl: number | null;
  /** 매도 시점의 에피소드 평단. 프론트에서 역산하지 말 것. */
  avg_buy_price?: number | null;
  /** net 손익 ÷ 매입금액. 서버가 계산한다(정의를 한 곳에 둔다). */
  ret_pct?: number | null;
  realized_gross?: number | null;
  realized_cost?: number | null;
  /** "net"(원장 확정) | "gross"(미정산 추정) */
  pnl_basis?: string | null;
  realized_est: boolean;
  strategy: string;
  // True order kind — not inferable from `price`, since a reconciled market
  // order also carries one.
  order_kind: "market" | "limit" | "sim";
  basis: string | null;
  /**
   * 이 매도의 성격. `null` 이면 평범한 봇 주문이다.
   *   "ladder_reserve" — (구) 09:25 사다리 예약. 2026-09-07 제거 — 과거 원장 전용
   *   "manual_exit"    — 원장에 없던 매도를 잔고 차이로 되짚은 합성 행.
   *                      사용자가 MTS 로 직접 판 경우다.
   * 이 구분이 없으면 추정 가격이 확정 체결가와 똑같은 얼굴로 표에 앉는다.
   */
  exit_kind?: string | null;
  /** "ccld"(실제 체결내역) | "close"·"last_close"(종가 추정) */
  price_source?: string | null;
  // 지정가 entries only: prev close and the ACTUAL discount vs it.
  prev_close: number | null;
  discount_pct: number | null;
}

export interface LiveOrdersResponse {
  orders: LiveOrderRow[];
  limit_discount: number;
}

// ─── Order story (row-click "why did this trade happen" detail) ─────

export interface StoryBar {
  open: number;
  high: number;
  low: number;
  close: number;
  source: "recorded" | "qlib" | "kis";
}

export interface StoryRuleLine {
  kind: "tp" | "sl" | "trail" | "ladder_rung";
  label: string;
  px: number | null;
  hit: boolean | null;
}

export interface StoryRules {
  strategy: string;
  exit_model: "bracket" | "ladder" | "trail" | "rank_dropout" | "none";
  lines: StoryRuleLine[];
  sl_kind: string | null;
  /** True = rebuilt from CURRENT rules, not the values recorded at the time. */
  reconstructed: boolean;
}

export interface StoryEntry {
  order_id: number | null;
  trade_date: string | null;
  exec_price: number | null;
  qty: number | null;
  avg_at_sale: number | null;
  basis: string | null;
  reasons: (StockTradeReasons & { stop_px?: number | null }) | null;
  rank: number | null;
}

export interface StoryJudgment {
  mode: "sim_daily_bar" | "real_order";
  text: string;
  recorded_at: string | null;
}

export interface RankPoint {
  as_of: string;
  rank: number | null; // null = outside the stored top-30
}

export interface PricePoint {
  trade_date: string;
  close: number;
}

export interface LimitEntryStory {
  limit_px: number | null;
  prev_close: number | null;
  fill_px: number | null;
  discount_pct: number | null;
  gap_down_fill: boolean;
}

export interface OrderStory {
  order: LiveOrderRow;
  reasons: (StockTradeReasons & { stop_px?: number | null }) | null;
  entry: StoryEntry | null;
  rules: StoryRules | null;
  bar: StoryBar | null;
  judgment: StoryJudgment;
  position_before: number | null;
  position_after: number | null;
  stage: number | null;
  rank_history: RankPoint[];
  topk: number;
  rank_store_n: number;
  post_closes: PricePoint[];
  give_back_pct: number | null;
  limit_entry: LimitEntryStory | null;
  notes: string[];
}

export interface CafeCandidateRow {
  trade_date: string;
  code: string;
  name: string | null;
  pattern: string;
  pattern_label: string;
  rank: number;
  close: number | null;
  stop_px: number | null;
  bought: boolean;
}

export interface CafeCandidatesResponse {
  candidates: CafeCandidateRow[];
}

export interface SurgePickRow {
  trade_date: string;
  rank: number;
  code: string;
  name: string | null;
  close: number | null;
  score: number;
  bought: boolean;
}

export interface SurgePicksResponse {
  picks: SurgePickRow[];
}

export interface RetroEpisode {
  code: string;
  name: string | null;
  strategy: string;
  entry_date: string | null;
  exit_date: string | null;
  avg: number;
  exit_px: number | null;
  qty: number;
  ret_pct: number | null;
  unreal_pct: number | null;
  max_unreal_pct: number | null;
  give_back_pp: number | null;
  post5_drift_pct: number | null;
  hold_days: number | null;
  entry_metrics: Record<string, number | null | undefined>;
  entry_basis: string;
}

export interface RetroHypothesis {
  key: string;
  label: string;
  evidence: string;
  support: number;
  refute: number;
  threshold: string;
}

export interface RetroIC {
  as_of: string;
  n: number;
  rank_ic: number;
}

export interface SurgeSelectionRow {
  trade_date: string;
  rank: number;
  code: string;
  name: string | null;
  close: number | null;
  score: number;
  next_ret_pct: number | null;
  hit: boolean | null;
}

export interface RetroResponse {
  episodes: RetroEpisode[];
  scoreboard: RetroHypothesis[];
  daily_ic: RetroIC[];
  surge_selection?: SurgeSelectionRow[] | null;
}

export interface ExitRow {
  code: string;
  name: string | null;
  strategy: string;
  /** 코드별 1,2,… — 같은 종목이 기간 안에 두 번 청산되면 두 행이다. 행 key 로 쓴다. */
  episode?: number;
  entry_date?: string | null;
  sell_count?: number;
  last_sell_date: string;
  sold_qty: number;
  avg_buy_price: number | null;
  est_sell_price: number | null;
  price_est: boolean;
  /** net — 수수료·세금 차감. 원장(Fill.pnl)과 같은 숫자다. */
  realized_pnl: number | null;
  realized_gross?: number | null;
  realized_cost?: number | null;
  /** net 손익 ÷ 매입금액. 서버가 계산한다 — 프론트에서 다시 유도하지 말 것. */
  ret_pct?: number | null;
  pnl_basis?: string | null;
  reasons: StockTradeReasons | null;
}

export interface StockCurvePoint {
  date: string;
  ret_pct: number; // close / running avg − 1
}

export interface StockCurve {
  code: string;
  name: string | null;
  status: "held" | "exited";
  episode: number;
  start: string;
  end: string | null;
  avg_price: number | null;
  points: StockCurvePoint[];
}

export interface StockCurvesResponse {
  strategy: string;
  curves: StockCurve[];
}

export interface TodayRealized {
  date: string;
  strategy: string;
  realized_pnl: number;
  sell_count: number;
  estimated: boolean;
}

/** DB timestamps are stored as naive UTC — parse them as UTC so the browser
 *  renders KST correctly instead of misreading UTC as local (+9h shift). */
export function parseUtc(ts: string): Date {
  return new Date(/Z|[+-]\d{2}:?\d{2}$/.test(ts) ? ts : ts + "Z");
}

/** Project-wide datetime format: `YYYY-MM-DD HH:MM` in KST. */
export function fmtDateTime(d: Date): string {
  const kst = new Date(d.getTime() + 9 * 3600 * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${kst.getUTCFullYear()}-${p(kst.getUTCMonth() + 1)}-${p(kst.getUTCDate())} ` +
         `${p(kst.getUTCHours())}:${p(kst.getUTCMinutes())}`;
}

export interface DailyPnLRow {
  trade_date: string;
  strategy: string;
  starting_equity: number;
  ending_equity: number;
  realised_pnl: number;
  unrealised_pnl: number;
  fees: number;
}

export interface DailyPnLResponse {
  rows: DailyPnLRow[];
  seed_cash: Record<string, number>;
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
