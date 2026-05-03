"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import PortfolioChart from "@/components/PortfolioChart";
import DailyActivityChart from "@/components/DailyActivityChart";
import MetricsTable from "@/components/MetricsTable";
import RecommendedPicks from "@/components/RecommendedPicks";
import RecentTrades from "@/components/RecentTrades";

export default function BacktestResultPage() {
  const { id } = useParams<{ id: string }>();

  const { data, isLoading } = useQuery({
    queryKey: ["backtest", id],
    queryFn: () => api.getBacktestResult(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "COMPLETED" || status === "FAILED") return false;
      return 3000;
    },
  });

  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (!data) return <p className="text-red-500">Backtest not found.</p>;

  const portfolioRange =
    data.portfolio && data.portfolio.length > 0
      ? `${data.portfolio[0].date} → ${data.portfolio[data.portfolio.length - 1].date}`
      : null;

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <Link href="/" className="text-gray-400 hover:text-gray-600">
          &larr; Back
        </Link>
        <h1 className="text-2xl font-bold text-gray-900">Backtest Result</h1>
        <StatusBadge status={data.status} />
      </div>

      <div className="text-xs text-gray-500 mb-4 flex flex-wrap gap-x-4">
        <span>Job: <span className="font-mono">{id}</span></span>
        {portfolioRange && <span>Period: <span className="font-mono">{portfolioRange}</span></span>}
        {data.benchmark_used && <span>Benchmark: <span className="font-mono">{data.benchmark_used}</span></span>}
      </div>

      {(data.status === "PENDING" || data.status === "RUNNING") && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-8 text-center">
          <div className="animate-spin w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-blue-800 font-medium">
            {data.status === "PENDING" ? "Waiting for worker..." : "Running backtest... This may take a few minutes."}
          </p>
        </div>
      )}

      {data.status === "FAILED" && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-6">
          <h2 className="text-red-800 font-semibold mb-2">Backtest Failed</h2>
          <pre className="text-red-700 text-sm whitespace-pre-wrap overflow-auto max-h-64">{data.error}</pre>
        </div>
      )}

      {data.status === "COMPLETED" && (
        <div className="space-y-6">
          {data.recommended_picks && data.recommended_picks.length > 0 && (
            <RecommendedPicks picks={data.recommended_picks} />
          )}

          {data.metrics && (
            <section>
              <h2 className="text-lg font-semibold mb-3">📈 성과 지표</h2>
              <MetricsTable
                metrics={data.metrics}
                extended={data.extended_metrics}
                benchmark={data.benchmark_used}
              />
            </section>
          )}

          {data.portfolio && data.portfolio.length > 0 && (
            <>
              <section className="bg-white rounded-lg border border-gray-200 p-5">
                <h2 className="text-lg font-semibold mb-1">💹 누적 수익률 곡선</h2>
                <p className="text-xs text-gray-500 mb-3">
                  포트폴리오 vs 벤치마크. 둘 다 백테스트 시작일을 0%로 정규화.
                </p>
                <PortfolioChart data={data.portfolio} />
              </section>

              <section className="bg-white rounded-lg border border-gray-200 p-5">
                <h2 className="text-lg font-semibold mb-1">📊 일별 수익률 & 회전율</h2>
                <p className="text-xs text-gray-500 mb-3">
                  녹색 선: 일수익률 / 노란 막대: 일회전율(매매 활성도). 회전율이 높은 날은 거래비용도 큼.
                </p>
                <DailyActivityChart data={data.portfolio} />
              </section>
            </>
          )}

          {data.recent_trades && data.recent_trades.length > 0 && (
            <RecentTrades trades={data.recent_trades} />
          )}

          {!data.metrics && !data.portfolio && !data.recommended_picks && (
            <p className="text-gray-500">Backtest completed but no results available.</p>
          )}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    PENDING: "bg-yellow-100 text-yellow-800",
    RUNNING: "bg-blue-100 text-blue-800",
    COMPLETED: "bg-green-100 text-green-800",
    FAILED: "bg-red-100 text-red-800",
  };
  return (
    <span className={`px-3 py-1 rounded-full text-xs font-medium ${colors[status] || "bg-gray-100"}`}>
      {status}
    </span>
  );
}
