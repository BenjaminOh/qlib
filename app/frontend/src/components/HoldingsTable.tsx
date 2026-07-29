"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, LiveHolding, StockTrade } from "@/lib/api";
import { FeatureContribList, MetricBadges } from "@/components/ReasonBadges";

const fmtKRW = (v: number) => {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(2)}억`;
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(0)}만`;
  return `${sign}${a.toFixed(0)}`;
};
const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`;

function TradeHistory({ code }: { code: string }) {
  const trades = useQuery({
    queryKey: ["stock-trades", code],
    queryFn: () => api.getStockTrades(code),
  });

  if (trades.isLoading)
    return <p className="text-xs text-gray-400 py-2">매매 이력 불러오는 중…</p>;
  if (!trades.data?.length)
    return <p className="text-xs text-gray-400 py-2">기록된 매매 이력이 없습니다.</p>;

  return (
    <div className="space-y-3 py-1">
      {trades.data.map((t: StockTrade, i: number) => {
        const isBuy = t.side === "BUY";
        const failed = t.status === "REJECTED";
        return (
          <div key={i} className="border-l-2 pl-3 border-gray-200">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-mono text-gray-500">{t.trade_date}</span>
              <span className={`font-semibold ${isBuy ? "text-red-600" : "text-blue-600"}`}>
                {isBuy ? "매수" : "매도"} {t.qty.toLocaleString()}주
              </span>
              <span className="text-gray-400">
                {t.price != null ? `@${t.price.toLocaleString()}` : "시장가"}
              </span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                failed
                  ? "bg-red-50 text-red-600 border-red-200"
                  : t.status === "SIMULATED"
                    ? "bg-gray-50 text-gray-500 border-gray-200"
                    : "bg-emerald-50 text-emerald-700 border-emerald-200"
              }`}>
                {t.status}{t.strategy === "close" ? " (시뮬)" : ""}
              </span>
            </div>
            {failed && t.error && (
              <p className="text-[11px] text-red-500 mt-0.5">거부 사유: {t.error}</p>
            )}
            {t.reasons ? (
              <div className="mt-1">
                <p className="text-xs text-gray-800 mb-1">
                  <span className={`font-medium ${isBuy ? "text-red-700" : "text-blue-700"}`}>
                    {t.reasons.basis ?? (isBuy ? "매수" : "매도")}
                  </span>
                  {t.reasons.summary ? ` — ${t.reasons.summary}` : ""}
                </p>
                {t.reasons.metrics && <MetricBadges m={t.reasons.metrics} />}
                {t.reasons.top_features?.length > 0 && (
                  <div className="mt-1.5">
                    <p className="text-[10px] text-gray-400 mb-1">당시 모델 기여 지표 Top {t.reasons.top_features.length}</p>
                    <FeatureContribList features={t.reasons.top_features} />
                  </div>
                )}
              </div>
            ) : (
              <p className="text-[11px] text-gray-400 mt-0.5">
                판단 기준 미기록 (근거 저장 도입 이전 주문)
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function HoldingsTable({
  holdings,
  totalEval,
}: {
  holdings: LiveHolding[];
  totalEval?: number;
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (!holdings || holdings.length === 0) {
    return (
      <div className="text-center text-gray-400 py-8 text-sm">현재 보유 종목 없음</div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
            <th className="text-right px-3 py-2 font-medium">수량</th>
            <th className="text-right px-3 py-2 font-medium">평균단가</th>
            <th className="text-right px-3 py-2 font-medium">현재가</th>
            <th className="text-right px-3 py-2 font-medium">평가금액</th>
            <th className="text-right px-3 py-2 font-medium">평가손익</th>
            <th className="text-right px-3 py-2 font-medium">수익률</th>
            <th className="text-right px-3 py-2 font-medium" title="이 종목의 손익이 전체 평가금액에서 차지하는 비율">
              포트폴리오 기여도
            </th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h) => {
            const c = h.pnl >= 0 ? "text-emerald-700" : "text-red-700";
            const contribution = totalEval && totalEval > 0 ? h.pnl / totalEval : null;
            const expanded = open === h.code;
            return (
              <>
                <tr
                  key={h.code}
                  className="border-t border-gray-100 cursor-pointer hover:bg-gray-50"
                  onClick={() => setOpen(expanded ? null : h.code)}
                >
                  <td className="px-3 py-2">
                    <span className="text-gray-900">{h.name ?? h.code}</span>
                    {h.name && h.name !== h.code && (
                      <span className="font-mono text-xs text-gray-500 ml-2">({h.code})</span>
                    )}
                    <span className="text-[10px] text-gray-400 ml-2">{expanded ? "▲" : "▼ 매매이력"}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{h.qty.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right font-mono">{h.avg_price.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right font-mono">{h.eval_price.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right font-mono">{fmtKRW(h.eval_value)}</td>
                  <td className={`px-3 py-2 text-right font-mono ${c}`}>{fmtKRW(h.pnl)}</td>
                  <td className={`px-3 py-2 text-right font-mono ${c}`}>{fmtPct(h.pnl_pct)}</td>
                  <td className={`px-3 py-2 text-right font-mono ${c}`}>
                    {contribution != null ? `${(contribution * 100).toFixed(2)}%p` : "—"}
                  </td>
                </tr>
                {expanded && (
                  <tr key={`${h.code}-history`} className="bg-gray-50/60">
                    <td colSpan={8} className="px-4 py-2">
                      <TradeHistory code={h.code} />
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
