"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import TossLink from "@/components/TossLink";

const PATTERN_BADGE: Record<string, string> = {
  A: "bg-rose-100 text-rose-700",
  B: "bg-orange-100 text-orange-700",
  R: "bg-violet-100 text-violet-700",
  C: "bg-sky-100 text-sky-700",
  D: "bg-emerald-100 text-emerald-700",
};

/** Daily picks of the recommender-mimic screener (15:05 scan → 15:28 sim buy). */
export default function CafeCandidates() {
  const { data, isLoading } = useQuery({
    queryKey: ["cafe-candidates"],
    queryFn: () => api.getCafeCandidates(7),
    refetchInterval: 60_000,
  });

  const rows = data?.candidates ?? [];

  return (
    <section className="bg-stone-50 rounded-lg border border-stone-200 p-4">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className="text-lg font-semibold text-stone-800">☕ 카페 모사 스크리너 (시뮬)</h2>
        <span className="text-xs text-stone-500">매일 15:05 스캔 → 상위 2종목 15:28 시뮬 매수</span>
      </div>
      {isLoading ? (
        <p className="text-xs text-stone-400 py-3">Loading...</p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-stone-500 py-3">
          아직 스캔 결과가 없습니다 — 오늘 15:05 첫 스캔 이후 여기에 후보가 표시됩니다.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs sm:text-sm whitespace-nowrap">
            <thead className="text-stone-500">
              <tr>
                <th className="text-left py-1.5 pr-3 font-medium">날짜</th>
                <th className="text-left py-1.5 pr-3 font-medium">종목</th>
                <th className="text-left py-1.5 pr-3 font-medium">패턴</th>
                <th className="text-right py-1.5 pr-3 font-medium">기준가</th>
                <th className="text-right py-1.5 pr-3 font-medium" title="스크리너가 계산한 구조적 손절선 (패턴별 앵커 × 버퍼)">손절선</th>
                <th className="text-left py-1.5 font-medium">매수</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={`${c.trade_date}-${c.code}`} className="border-t border-stone-200/70">
                  <td className="py-1.5 pr-3 font-mono text-xs text-stone-500">{c.trade_date}</td>
                  <td className="py-1.5 pr-3">
                    <span className="text-stone-900">{c.name ?? c.code}</span>
                    <span className="font-mono text-[11px] text-stone-400 ml-1.5">({c.code})</span>
                    <TossLink code={c.code} className="ml-1.5" />
                  </td>
                  <td className="py-1.5 pr-3">
                    <span className={`px-1.5 py-0.5 rounded text-[11px] font-medium ${PATTERN_BADGE[c.pattern] || "bg-stone-100 text-stone-600"}`}>
                      {c.pattern} · {c.pattern_label}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono">
                    {c.close != null ? `${c.close.toLocaleString()}원` : "—"}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono text-red-600">
                    {c.stop_px != null ? `${c.stop_px.toLocaleString()}원` : "—"}
                  </td>
                  <td className="py-1.5">
                    {c.bought ? (
                      <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 text-[11px] font-medium">시뮬 매수</span>
                    ) : (
                      <span className="text-[11px] text-stone-400">후보만</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] text-stone-400 mt-2">
        카페 추천자 역설계 규칙(급등 테마 + 신고가 돌파/타이트 눌림/재진입/낙폭 반등 + 구조적 손절)로
        KIS 등락률·거래량 랭킹을 스캔합니다. 매매 내역은 주문 이력에서 &ldquo;☕ 카페&rdquo; 필터로 볼 수 있습니다.
      </p>
    </section>
  );
}
