// Strategy/model/handler catalogs shared between the single-backtest form
// and the grid-search optimizer page. Keep entries in sync with the qlib
// classes resolvable inside the worker container.

export type KwargValue = number | string | boolean | null;

export interface StrategyEntry {
  module: string;
  defaults: Record<string, KwargValue>;
  // Free-form notes shown under the form to flag setup requirements.
  note?: string;
  // If set, prevents the backtest form from submitting until the user
  // explicitly overrides the offending key.
  blocking?: boolean;
}

export interface ModelEntry {
  module: string;
  defaults: Record<string, KwargValue>;
  note?: string;
}

export const STRATEGY_CATALOG: Record<string, StrategyEntry> = {
  TopkDropoutStrategy: {
    module: "qlib.contrib.strategy.signal_strategy",
    defaults: { topk: 20, n_drop: 3 },
  },
  SoftTopkStrategy: {
    module: "qlib.contrib.strategy.cost_control",
    defaults: { topk: 20, risk_degree: 0.95, trade_impact_limit: 1.0 },
    note: "Soft variant of TopK with cost-aware turnover budgeting. Sensitive to data gaps — use a clean continuous-listing universe.",
  },
  EnhancedIndexingStrategy: {
    module: "qlib.contrib.strategy.signal_strategy",
    defaults: { riskmodel_root: "", lamb: 0.001, delta: 0.05 },
    note: "Requires a precomputed risk model directory. Leave riskmodel_root empty and this strategy will fail at runtime — supply a path before submitting.",
    blocking: true,
  },
  TWAPStrategy: {
    module: "qlib.contrib.strategy.rule_strategy",
    defaults: {},
    note: "Order-execution baseline (no ML signal). Useful as a sanity baseline.",
  },
  SBBStrategyEMA: {
    module: "qlib.contrib.strategy.rule_strategy",
    defaults: { instruments: "kospi200", freq: "day" },
    note: "EMA-based statistical bid/buy rule. ML signal is not used.",
  },
};

export const MODEL_CATALOG: Record<string, ModelEntry> = {
  LGBModel: {
    module: "qlib.contrib.model.gbdt",
    defaults: { num_leaves: 64, learning_rate: 0.05, n_estimators: 200 },
  },
  XGBModel: {
    module: "qlib.contrib.model.xgboost",
    defaults: { max_depth: 6, eta: 0.05, n_estimators: 200 },
  },
  CatBoostModel: {
    module: "qlib.contrib.model.catboost_model",
    defaults: { iterations: 300, learning_rate: 0.05, depth: 6 },
  },
  LinearModel: {
    module: "qlib.contrib.model.linear",
    defaults: { estimator: "ridge", alpha: 0.05 },
    note: "Default Alpha158 features contain NaN warm-up rows that LinearModel.dropna() removes entirely. Add infer/learn processors via handler_kwargs (e.g. Fillna) before this model produces signals.",
  },
};

export const HANDLER_CATALOG: Record<string, { module: string; defaults: Record<string, KwargValue> }> = {
  Alpha158: {
    module: "qlib.contrib.data.handler",
    defaults: {},
  },
  Alpha360: {
    module: "qlib.contrib.data.handler",
    defaults: {},
  },
};
