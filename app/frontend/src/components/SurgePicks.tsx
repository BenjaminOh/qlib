"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import ChartLink from "@/components/ChartLink";

/** Daily surge-eve TOP10 (15:12 scan → top 2 sim-bought at 15:29). */
export default function SurgePicks() {
  const { data, isLoading } = useQuery({
    queryKey: ["surge-picks"],
    queryFn: () => api.getSurgePicks(7),
    refetchInterval: 60_000,
  });

  const rows = data?.picks ?? [];

  return (
    <section className="bg-pink-50/60 rounded-lg border border-pink-200 p-4">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className="text-lg font-semibold text-pink-900">⚡ 급등 전야 후보 (시뮬)</h2>
        <span className="text-xs text-pink-700/70">매일 15:12 선정 → 상위 2종목 15:29 시뮬 매수</span>
      </div>
      {isLoading ? (
        <p className="text-xs text-pink-400 py-3">Loading...</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-pink-800/60 py-3">
          아직 선정 결과가 없습니다 — 매 거래일 15:12 이후 표시됩니다.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs sm:text-sm whitespace-nowrap">
            <thead className="text-pink-800/60">
              <tr>
                <th className="text-left py-1.5 pr-3 font-medium">날짜</th>
                <th className="text-left py-1.5 pr-3 font-medium">순위</th>
                <th className="text-left py-1.5 pr-3 font-medium">종목</th>
                <th className="text-right py-1.5 pr-3 font-medium">기준가</th>
                <th className="text-right py-1.5 pr-3 font-medium" title="추세(20일)+눌림 적합도+거래량+20일선 조합 점수 — 3.5년 급등 전야 2,356건 프로파일 기반">점수</th>
                <th className="text-left py-1.5 font-medium">매수</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={`${p.trade_date}-${p.code}`} className="border-t border-pink-200/50">
                  <td className="py-1.5 pr-3 font-mono text-xs text-pink-800/50">{p.trade_date}</td>
                  <td className="py-1.5 pr-3 font-semibold text-pink-700">#{p.rank}</td>
                  <td className="py-1.5 pr-3">
                    <span className="text-gray-900">{p.name ?? p.code}</span>
                    <span className="font-mono text-[11px] text-gray-400 ml-1.5">({p.code})</span>
                    <ChartLink code={p.code} className="ml-1.5" />
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono">
                    {p.close != null ? `${p.close.toLocaleString()}원` : "—"}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono">{p.score.toFixed(1)}</td>
                  <td className="py-1.5">
                    {p.bought ? (
                      <span className="px-1.5 py-0.5 rounded bg-pink-100 text-pink-700 text-[11px] font-medium">시뮬 매수</span>
                    ) : (
                      <span className="text-[11px] text-gray-400">후보만</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] text-pink-800/50 mt-2">
        선정 규칙: 20일 상승 추세 + 5일 고점 대비 −3~−15% 눌림(적정 −6.5%) + 20일선 위 +
        거래량 확대 — 3.5년 유니버스 급등 전야 전수 분석에서 추출한 프로파일. 매매는 시뮬 전용.
      </p>
    </section>
  );
}
