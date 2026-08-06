"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import TossLink from "@/components/TossLink";

const fmtKRW = (v: number) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(2)}억`;
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(0)}만`;
  return `${sign}${a.toFixed(0)}`;
};

export default function LivePositionsPage() {
  const [shown, setShown] = useState(10);
  const { data, isLoading } = useQuery({
    queryKey: ["live-positions"],
    queryFn: () => api.getLivePositionHistory(60),
    refetchInterval: 60_000,
  });

  const visible = (data?.snapshots || []).slice(0, shown);

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold text-gray-900">📚 일별 보유 종목 스냅샷</h1>
        <Link href="/live" className="text-blue-600 hover:underline text-sm">
          ← 대시보드
        </Link>
      </div>

      <p className="text-sm text-gray-500">
        매 거래일 종가 후(15:40 KST) 기록된 잔고 스냅샷입니다. 일별 종목 회전과 평가손익 변화를
        확인할 수 있습니다.
      </p>

      {isLoading ? (
        <div className="text-center text-gray-400 py-8 text-sm">Loading...</div>
      ) : visible.length === 0 ? (
        <div className="text-center text-gray-400 py-12 bg-white rounded-lg border border-gray-200">
          아직 스냅샷이 없습니다. (모의투자 시작 후 매일 1회 자동 기록됩니다)
        </div>
      ) : (
        <div className="space-y-4">
          {visible.map((snap) => (
            <details key={snap.snapshot_date} className="bg-white border border-gray-200 rounded-lg" open>
              <summary className="cursor-pointer px-4 py-3 flex items-center justify-between bg-gray-50 border-b border-gray-100">
                <div>
                  <span className="font-semibold">{snap.snapshot_date}</span>
                  <span className="ml-3 text-sm text-gray-600">
                    {snap.holdings.length}종목 · 평가 {fmtKRW(snap.total_eval)} · 예수금 {fmtKRW(snap.cash)}
                  </span>
                </div>
              </summary>
              {snap.holdings.length === 0 ? (
                <div className="px-4 py-3 text-sm text-gray-500">보유 종목 없음</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-gray-500">
                      <tr>
                        <th className="text-left px-3 py-1 font-normal">종목명 (코드)</th>
                        <th className="text-right px-3 py-1 font-normal">수량</th>
                        <th className="text-right px-3 py-1 font-normal">평균단가</th>
                        <th className="text-right px-3 py-1 font-normal">평가가</th>
                        <th className="text-right px-3 py-1 font-normal">평가손익</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snap.holdings.map((h, i) => (
                        <tr key={`${h.code}-${i}`} className="border-t border-gray-50">
                          <td className="px-3 py-1">
                            <span className="text-gray-900">{h.name ?? h.code}</span>
                            <TossLink code={h.code} className="ml-1.5" />
                            {h.name && h.name !== h.code && (
                              <span className="font-mono text-xs text-gray-500 ml-2">({h.code})</span>
                            )}
                          </td>
                          <td className="px-3 py-1 text-right font-mono">{h.qty.toLocaleString()}</td>
                          <td className="px-3 py-1 text-right font-mono">{h.avg_price?.toLocaleString() ?? "—"}</td>
                          <td className="px-3 py-1 text-right font-mono">{h.eval_price?.toLocaleString() ?? "—"}</td>
                          <td className={`px-3 py-1 text-right font-mono ${h.pnl >= 0 ? "text-emerald-700" : "text-red-700"}`}>
                            {fmtKRW(h.pnl)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </details>
          ))}

          {(data?.snapshots.length ?? 0) > shown && (
            <div className="text-center">
              <button
                type="button"
                onClick={() => setShown((s) => s + 10)}
                className="text-sm text-blue-600 hover:underline"
              >
                {Math.min(10, (data?.snapshots.length ?? 0) - shown)}일 더 보기
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
