"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api, GridMember } from "@/lib/api";

type SortKey = "annualized_return" | "information_ratio" | "max_drawdown" | "std" | "mean";

const SORT_LABELS: Record<SortKey, string> = {
  annualized_return: "Annual Return",
  information_ratio: "IR",
  max_drawdown: "Max Drawdown",
  std: "Volatility",
  mean: "Mean Daily Return",
};

function fmt(n: number | null | undefined, digits = 4): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

function summaryCells(summary: Record<string, unknown>): { label: string; value: string }[] {
  return Object.entries(summary).map(([k, v]) => ({
    label: k,
    value: typeof v === "number" ? String(v) : String(v ?? "—"),
  }));
}

export default function GridResultPage() {
  const params = useParams<{ group_id: string }>();
  const groupId = params.group_id;
  const [sortKey, setSortKey] = useState<SortKey>("annualized_return");
  const [sortDesc, setSortDesc] = useState(true);

  const { data, isLoading, error } = useQuery({
    queryKey: ["grid", groupId],
    queryFn: () => api.getGridResult(groupId),
    refetchInterval: (query) => {
      const d = query.state.data;
      if (!d) return 3000;
      const allTerminal = d.completed + d.failed >= d.total;
      return allTerminal ? false : 3000;
    },
  });

  if (isLoading) return <p className="text-gray-500">Loading group {groupId}...</p>;
  if (error) return <p className="text-red-600">Error: {(error as Error).message}</p>;
  if (!data) return null;

  const sorted = [...data.jobs].sort((a, b) => {
    const av = a.metrics?.[sortKey];
    const bv = b.metrics?.[sortKey];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return sortDesc ? bv - av : av - bv;
  });

  const allKeys = sorted[0] ? Object.keys(sorted[0].config_summary) : [];

  return (
    <div className="max-w-5xl">
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Grid Results</h1>
        <Link href="/backtest/optimize" className="text-blue-600 hover:underline text-sm">
          ← New Grid
        </Link>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6 grid grid-cols-3 gap-4 text-sm">
        <div>
          <div className="text-gray-500">Total</div>
          <div className="text-xl font-semibold">{data.total}</div>
        </div>
        <div>
          <div className="text-gray-500">Completed</div>
          <div className="text-xl font-semibold text-green-700">{data.completed}</div>
        </div>
        <div>
          <div className="text-gray-500">Failed</div>
          <div className="text-xl font-semibold text-red-700">{data.failed}</div>
        </div>
      </div>

      <div className="mb-3 flex items-center space-x-3 text-sm">
        <span className="text-gray-700">Sort by:</span>
        <select
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as SortKey)}
          className="border rounded px-2 py-1"
        >
          {Object.entries(SORT_LABELS).map(([k, label]) => (
            <option key={k} value={k}>{label}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setSortDesc((d) => !d)}
          className="border rounded px-2 py-1 hover:bg-gray-50"
        >
          {sortDesc ? "↓ desc" : "↑ asc"}
        </button>
      </div>

      <div className="overflow-x-auto bg-white border border-gray-200 rounded-lg">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-gray-700">
            <tr>
              {allKeys.map((k) => (
                <th key={k} className="text-left px-3 py-2 font-medium">{k}</th>
              ))}
              <th className="text-right px-3 py-2 font-medium">Annual Return</th>
              <th className="text-right px-3 py-2 font-medium">IR</th>
              <th className="text-right px-3 py-2 font-medium">Max DD</th>
              <th className="text-right px-3 py-2 font-medium">Volatility</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
              <th className="text-left px-3 py-2 font-medium">Job</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((job: GridMember) => (
              <tr key={job.job_id} className="border-t border-gray-100 hover:bg-gray-50">
                {summaryCells(job.config_summary).map((c) => (
                  <td key={c.label} className="px-3 py-2 text-gray-700">{c.value}</td>
                ))}
                <td className="px-3 py-2 text-right">{fmt(job.metrics?.annualized_return)}</td>
                <td className="px-3 py-2 text-right">{fmt(job.metrics?.information_ratio)}</td>
                <td className="px-3 py-2 text-right">{fmt(job.metrics?.max_drawdown)}</td>
                <td className="px-3 py-2 text-right">{fmt(job.metrics?.std)}</td>
                <td className="px-3 py-2">
                  <span
                    className={
                      job.status === "COMPLETED"
                        ? "text-green-700"
                        : job.status === "FAILED"
                        ? "text-red-700"
                        : "text-gray-500"
                    }
                  >
                    {job.status}
                  </span>
                  {job.error && (
                    <div className="text-xs text-red-600 truncate max-w-xs" title={job.error}>
                      {job.error.split("\n")[0]}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2">
                  <Link href={`/backtest/${job.job_id}`} className="text-blue-600 hover:underline">
                    open →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
