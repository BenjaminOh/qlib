"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, BacktestRequest, GridBacktestRequest } from "@/lib/api";
import { MODEL_CATALOG, STRATEGY_CATALOG } from "@/lib/catalogs";

// Defaults mirror /backtest/new but tuned for the KOSPI 2023+ universe.
const baseDefault: BacktestRequest = {
  strategy_class: "TopkDropoutStrategy",
  strategy_module: STRATEGY_CATALOG.TopkDropoutStrategy.module,
  strategy_kwargs: { ...STRATEGY_CATALOG.TopkDropoutStrategy.defaults },
  model_class: "LGBModel",
  model_module: MODEL_CATALOG.LGBModel.module,
  model_kwargs: { ...MODEL_CATALOG.LGBModel.defaults },
  handler_class: "Alpha158",
  handler_module: "qlib.contrib.data.handler",
  handler_kwargs: {},
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

function parseList(raw: string): number[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map(Number)
    .filter((n) => Number.isFinite(n));
}

export default function OptimizePage() {
  const router = useRouter();
  const [base, setBase] = useState(baseDefault);
  const [models, setModels] = useState<Set<string>>(new Set(["LGBModel"]));
  const [strategies, setStrategies] = useState<Set<string>>(
    new Set(["TopkDropoutStrategy"]),
  );
  const [topkRaw, setTopkRaw] = useState("10,20,30");
  const [ndropRaw, setNdropRaw] = useState("1,3,5");

  const toggle = (set: Set<string>, key: string, setter: (s: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setter(next);
  };

  const topkList = useMemo(() => parseList(topkRaw), [topkRaw]);
  const ndropList = useMemo(() => parseList(ndropRaw), [ndropRaw]);

  const sweeps: Record<string, number[]> = useMemo(() => {
    const out: Record<string, number[]> = {};
    if (topkList.length > 0) out["strategy_kwargs.topk"] = topkList;
    if (ndropList.length > 0) out["strategy_kwargs.n_drop"] = ndropList;
    return out;
  }, [topkList, ndropList]);

  const sweepCount = Math.max(1, Object.values(sweeps).reduce((acc, arr) => acc * arr.length, 1));
  const totalJobs = models.size * strategies.size * sweepCount;

  const mutation = useMutation({
    mutationFn: api.submitGrid,
    onSuccess: (data) => router.push(`/backtest/optimize/${data.group_id}`),
  });

  const submit = () => {
    if (totalJobs === 0) return;
    const payload: GridBacktestRequest = {
      base,
      models: Array.from(models).map((cls) => ({
        class: cls,
        module: MODEL_CATALOG[cls].module,
        kwargs: { ...MODEL_CATALOG[cls].defaults },
      })),
      strategies: Array.from(strategies).map((cls) => ({
        class: cls,
        module: STRATEGY_CATALOG[cls].module,
        kwargs: { ...STRATEGY_CATALOG[cls].defaults },
      })),
      param_sweeps: sweeps,
      max_jobs: 100,
    };
    mutation.mutate(payload);
  };

  return (
    <div className="max-w-3xl">
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Grid Search</h1>
        <Link href="/backtest/new" className="text-blue-600 hover:underline text-sm">
          ← Single Backtest
        </Link>
      </div>

      <div className="space-y-6">
        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Models</h2>
          <div className="grid grid-cols-2 gap-2">
            {Object.keys(MODEL_CATALOG).map((cls) => (
              <label key={cls} className="flex items-center space-x-2 text-sm">
                <input
                  type="checkbox"
                  checked={models.has(cls)}
                  onChange={() => toggle(models, cls, setModels)}
                />
                <span>{cls}</span>
              </label>
            ))}
          </div>
        </section>

        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Strategies</h2>
          <div className="grid grid-cols-2 gap-2">
            {Object.keys(STRATEGY_CATALOG).map((cls) => {
              const entry = STRATEGY_CATALOG[cls];
              return (
                <label key={cls} className="flex items-start space-x-2 text-sm">
                  <input
                    type="checkbox"
                    checked={strategies.has(cls)}
                    disabled={entry.blocking}
                    onChange={() => toggle(strategies, cls, setStrategies)}
                  />
                  <span>
                    {cls}
                    {entry.blocking && (
                      <span className="block text-xs text-red-600">
                        disabled — requires risk model
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
          </div>
        </section>

        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Parameter sweeps</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                strategy_kwargs.topk (comma-separated)
              </label>
              <input
                type="text"
                value={topkRaw}
                onChange={(e) => setTopkRaw(e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
                placeholder="10,20,30"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                strategy_kwargs.n_drop (comma-separated)
              </label>
              <input
                type="text"
                value={ndropRaw}
                onChange={(e) => setNdropRaw(e.target.value)}
                className="w-full border rounded-lg px-3 py-2"
                placeholder="1,3,5"
              />
            </div>
          </div>
          <p className="mt-3 text-xs text-gray-500">
            Sweeps only apply to compatible strategies. TopK/SoftTopK use
            <code className="mx-1 px-1 bg-gray-100 rounded">topk</code>;
            n_drop is TopkDropout-only.
          </p>
        </section>

        <section className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">Common settings</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Instruments</label>
              <select
                value={base.instruments}
                onChange={(e) => setBase((p) => ({ ...p, instruments: e.target.value }))}
                className="w-full border rounded-lg px-3 py-2"
              >
                <option value="kospi200">KOSPI 200</option>
                <option value="kosdaq150">KOSDAQ 150</option>
                <option value="kr_all">KOSPI200 + KOSDAQ150</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Backtest end</label>
              <input
                type="date"
                value={base.backtest_end}
                onChange={(e) => setBase((p) => ({ ...p, backtest_end: e.target.value, test_end: e.target.value }))}
                className="w-full border rounded-lg px-3 py-2"
              />
            </div>
          </div>
        </section>

        <div className="flex items-center justify-between bg-gray-50 border border-gray-200 rounded-lg p-4">
          <div className="text-sm text-gray-700">
            <strong>{totalJobs}</strong> jobs will be queued
            <span className="ml-2 text-gray-500">
              ({models.size} models × {strategies.size} strategies × {sweepCount} sweep combos)
            </span>
          </div>
          <button
            type="button"
            disabled={mutation.isPending || totalJobs === 0 || totalJobs > 100}
            onClick={submit}
            className="bg-blue-600 text-white px-4 py-2 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {mutation.isPending ? "Queuing..." : "Run Grid"}
          </button>
        </div>

        {totalJobs > 100 && (
          <p className="text-sm text-red-600">
            Grid exceeds the 100-job cap. Trim sweeps or models/strategies before running.
          </p>
        )}

        {mutation.isError && (
          <p className="text-sm text-red-600">Error: {(mutation.error as Error).message}</p>
        )}
      </div>
    </div>
  );
}
