"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, BacktestRequest } from "@/lib/api";
import {
  HANDLER_CATALOG,
  KwargValue,
  MODEL_CATALOG,
  STRATEGY_CATALOG,
} from "@/lib/catalogs";

const initialStrategy = "TopkDropoutStrategy";
const initialModel = "LGBModel";
const initialHandler = "Alpha158";

const defaultConfig: BacktestRequest = {
  strategy_class: initialStrategy,
  strategy_module: STRATEGY_CATALOG[initialStrategy].module,
  strategy_kwargs: { ...STRATEGY_CATALOG[initialStrategy].defaults },
  model_class: initialModel,
  model_module: MODEL_CATALOG[initialModel].module,
  model_kwargs: { ...MODEL_CATALOG[initialModel].defaults },
  handler_class: initialHandler,
  handler_module: HANDLER_CATALOG[initialHandler].module,
  handler_kwargs: {},
  // Alpha158 needs ~60 trading days to warm up; for KR data starting at
  // 2023-01-01, train from April 2023 onward to avoid NaN-heavy windows.
  train_start: "2023-04-01",
  train_end: "2024-12-31",
  valid_start: "2025-01-01",
  valid_end: "2025-06-30",
  test_start: "2025-07-01",
  test_end: "2026-04-30",
  backtest_start: "2025-07-01",
  backtest_end: "2026-04-30",
  account: 100_000_000,
  benchmark: null,
  freq: "day",
  exchange: {
    limit_threshold: 0.3,
    deal_price: "close",
    open_cost: 0.00015,
    close_cost: 0.00315,
    min_cost: 0,
    trade_unit: 1,
  },
  instruments: "kospi200",
};

function coerceKwarg(prev: KwargValue, raw: string): KwargValue {
  if (typeof prev === "number") {
    const n = parseFloat(raw);
    return Number.isFinite(n) ? n : 0;
  }
  if (typeof prev === "boolean") return raw === "true";
  return raw;
}

export default function NewBacktestPage() {
  const router = useRouter();
  const [config, setConfig] = useState(defaultConfig);

  const mutation = useMutation({
    mutationFn: api.submitBacktest,
    onSuccess: (data) => router.push(`/backtest/${data.job_id}`),
  });

  const update = <K extends keyof BacktestRequest>(field: K, value: BacktestRequest[K]) => {
    setConfig((prev) => ({ ...prev, [field]: value }));
  };

  const onStrategyChange = (cls: string) => {
    const entry = STRATEGY_CATALOG[cls];
    setConfig((prev) => ({
      ...prev,
      strategy_class: cls,
      strategy_module: entry.module,
      strategy_kwargs: { ...entry.defaults },
    }));
  };

  const onModelChange = (cls: string) => {
    const entry = MODEL_CATALOG[cls];
    setConfig((prev) => ({
      ...prev,
      model_class: cls,
      model_module: entry.module,
      model_kwargs: { ...entry.defaults },
    }));
  };

  const onHandlerChange = (cls: string) => {
    const entry = HANDLER_CATALOG[cls];
    setConfig((prev) => ({
      ...prev,
      handler_class: cls,
      handler_module: entry.module,
    }));
  };

  const strategyEntry = STRATEGY_CATALOG[config.strategy_class];
  const blockingNote = useMemo(() => {
    if (!strategyEntry?.blocking) return null;
    // Block submit until every empty-string kwarg has been filled in.
    const empty = Object.entries(config.strategy_kwargs).filter(
      ([, v]) => typeof v === "string" && (v as string).trim() === "",
    );
    return empty.length > 0 ? empty.map(([k]) => k).join(", ") : null;
  }, [strategyEntry, config.strategy_kwargs]);

  const renderKwargInputs = (
    label: string,
    kwargs: Record<string, unknown>,
    setter: (next: Record<string, unknown>) => void,
  ) => {
    const keys = Object.keys(kwargs);
    if (keys.length === 0) {
      return <p className="text-sm text-gray-500">No parameters for {label}.</p>;
    }
    return (
      <div className="grid grid-cols-2 gap-4">
        {keys.map((key) => {
          const v = kwargs[key];
          const isNumber = typeof v === "number";
          return (
            <div key={key}>
              <label className="block text-sm font-medium text-gray-700 mb-1">{key}</label>
              <input
                type={isNumber ? "number" : "text"}
                step={isNumber ? "any" : undefined}
                value={v === null || v === undefined ? "" : String(v)}
                onChange={(e) =>
                  setter({
                    ...kwargs,
                    [key]: coerceKwarg(v as KwargValue, e.target.value),
                  })
                }
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="max-w-3xl">
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">New Backtest</h1>
        <Link href="/backtest/optimize" className="text-blue-600 hover:underline text-sm">
          Open Grid Search →
        </Link>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (blockingNote) return;
          mutation.mutate(config);
        }}
        className="space-y-6"
      >
        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Strategy & Model</h2>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Strategy</label>
              <select
                value={config.strategy_class}
                onChange={(e) => onStrategyChange(e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
              >
                {Object.keys(STRATEGY_CATALOG).map((cls) => (
                  <option key={cls} value={cls}>{cls}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
              <select
                value={config.model_class}
                onChange={(e) => onModelChange(e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
              >
                {Object.keys(MODEL_CATALOG).map((cls) => (
                  <option key={cls} value={cls}>{cls}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Handler (Features)</label>
              <select
                value={config.handler_class}
                onChange={(e) => onHandlerChange(e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
              >
                {Object.keys(HANDLER_CATALOG).map((cls) => (
                  <option key={cls} value={cls}>{cls}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="mb-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-2">Strategy parameters</h3>
            {renderKwargInputs("strategy", config.strategy_kwargs, (next) =>
              update("strategy_kwargs", next),
            )}
            {strategyEntry?.note && (
              <p className={`mt-2 text-xs ${strategyEntry.blocking ? "text-red-600" : "text-amber-600"}`}>
                {strategyEntry.note}
              </p>
            )}
          </div>

          <div>
            <h3 className="text-sm font-semibold text-gray-700 mb-2">Model parameters</h3>
            {renderKwargInputs("model", config.model_kwargs, (next) =>
              update("model_kwargs", next),
            )}
            {MODEL_CATALOG[config.model_class]?.note && (
              <p className="mt-2 text-xs text-amber-600">
                {MODEL_CATALOG[config.model_class].note}
              </p>
            )}
          </div>
        </section>

        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Data Period</h2>
          <div className="grid grid-cols-2 gap-4">
            {[
              ["train_start", "Train Start"],
              ["train_end", "Train End"],
              ["valid_start", "Valid Start"],
              ["valid_end", "Valid End"],
              ["test_start", "Test Start"],
              ["test_end", "Test End"],
            ].map(([field, label]) => (
              <div key={field}>
                <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
                <input
                  type="date"
                  value={config[field as keyof BacktestRequest] as string}
                  onChange={(e) => update(field as keyof BacktestRequest, e.target.value as never)}
                  className="w-full border rounded-lg px-3 py-2"
                />
              </div>
            ))}
          </div>
        </section>

        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Backtest Settings</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Instruments</label>
              <select
                value={config.instruments}
                onChange={(e) => update("instruments", e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
              >
                <option value="kospi200">KOSPI 200</option>
                <option value="kosdaq150">KOSDAQ 150</option>
                <option value="kr_all">KOSPI200 + KOSDAQ150</option>
                <option value="all">all (raw provider list)</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Initial Cash (KRW)</label>
              <input
                type="number"
                value={config.account}
                onChange={(e) => update("account", parseFloat(e.target.value))}
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Backtest Start</label>
              <input
                type="date"
                value={config.backtest_start}
                onChange={(e) => update("backtest_start", e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Backtest End</label>
              <input
                type="date"
                value={config.backtest_end}
                onChange={(e) => update("backtest_end", e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
          </div>
        </section>

        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Exchange Settings (KRX Defaults)</h2>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Buy Cost</label>
              <input
                type="number"
                step="0.0001"
                value={config.exchange.open_cost}
                onChange={(e) =>
                  update("exchange", { ...config.exchange, open_cost: parseFloat(e.target.value) })
                }
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Sell Cost (incl. tax)</label>
              <input
                type="number"
                step="0.0001"
                value={config.exchange.close_cost}
                onChange={(e) =>
                  update("exchange", { ...config.exchange, close_cost: parseFloat(e.target.value) })
                }
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Limit Threshold</label>
              <input
                type="number"
                step="0.01"
                value={config.exchange.limit_threshold || 0.3}
                onChange={(e) =>
                  update("exchange", { ...config.exchange, limit_threshold: parseFloat(e.target.value) })
                }
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
          </div>
        </section>

        {blockingNote && (
          <p className="text-sm text-red-600">
            Fill in required kwargs before submitting: {blockingNote}
          </p>
        )}

        <button
          type="submit"
          disabled={mutation.isPending || !!blockingNote}
          className="w-full bg-blue-600 text-white py-3 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {mutation.isPending ? "Submitting..." : "Run Backtest"}
        </button>

        {mutation.isError && (
          <p className="text-red-500 text-sm">Error: {(mutation.error as Error).message}</p>
        )}
      </form>
    </div>
  );
}
