"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, BacktestListItem, JobStatus, fmtDateTime } from "@/lib/api";

const statusColors: Record<JobStatus, string> = {
  PENDING: "bg-yellow-100 text-yellow-800",
  RUNNING: "bg-blue-100 text-blue-800",
  COMPLETED: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
};

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["backtests"],
    queryFn: api.listBacktests,
    refetchInterval: 5000,
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <Link
          href="/backtest/new"
          className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700"
        >
          + New Backtest
        </Link>
      </div>

      {isLoading && <p className="text-gray-500">Loading...</p>}
      {error && (
        <p className="text-red-500">
          API connection failed. Is the backend running?
        </p>
      )}

      {data && data.jobs.length === 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
          <p className="text-gray-500 mb-4">No backtests yet.</p>
          <Link
            href="/backtest/new"
            className="text-blue-600 hover:underline"
          >
            Run your first backtest
          </Link>
        </div>
      )}

      {data && data.jobs.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-gray-600">
                  Status
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">
                  Strategy
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">
                  Model
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">
                  Instruments
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">
                  Period
                </th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">
                  Created
                </th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((job: BacktestListItem) => (
                <tr
                  key={job.job_id}
                  className="border-b hover:bg-gray-50 cursor-pointer"
                >
                  <td className="px-4 py-3">
                    <Link href={`/backtest/${job.job_id}`}>
                      <span
                        className={`px-2 py-1 rounded-full text-xs font-medium ${
                          statusColors[job.status]
                        }`}
                      >
                        {job.status}
                      </span>
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <Link href={`/backtest/${job.job_id}`}>
                      {job.strategy_class}
                    </Link>
                  </td>
                  <td className="px-4 py-3">{job.model_class}</td>
                  <td className="px-4 py-3">{job.instruments}</td>
                  <td className="px-4 py-3 text-gray-500">
                    {job.backtest_start} ~ {job.backtest_end}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {fmtDateTime(new Date(job.created_at))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
