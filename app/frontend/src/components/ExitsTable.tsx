"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ExitRow, StockTrade } from "@/lib/api";
import { FeatureContribList, MetricBadges } from "@/components/ReasonBadges";

/** Full buy→sell timeline for an exited stock (same data as holdings drill-down). */
function ExitTimeline({ code }: { code: string }) {
  const trades = useQuery({
    queryKey: ["stock-trades", code],
    queryFn: () => api.getStockTrades(code),
  });
  if (trades.isLoading) return <p className="text-xs text-gray-400 py-2">이력 불러오는 중…</p>;
  if (!trades.data?.length) return <p className="text-xs text-gray-400 py-2">이력 없음</p>;
  return (
    <div className="space-y-2 py-1">
      {trades.data.map((t: StockTrade, i: number) => (
        <div key={i} className="text-xs border-l-2 pl-2 border-gray-200">
          <span className="font-mono text-gray-500 mr-2">{t.trade_date}</span>
          <span className={`font-semibold ${t.side === "BUY" ? "text-red-600" : "text-blue-600"}`}>
            {t.side === "BUY" ? "매수" : "매도"} {t.qty.toLocaleString()}주
          </span>
          {t.exec_price != null && (
            <span className="text-gray-500 ml-1">@{Math.round(t.exec_price).toLocaleString()}{t.price_est ? "*" : ""}</span>
          )}
          {t.realized_pnl != null && (
            <span className={`ml-2 font-mono ${t.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700"}`}>
              {t.realized_pnl >= 0 ? "+" : ""}{Math.round(t.realized_pnl).toLocaleString()} 실현
            </span>
          )}
          {t.reasons?.basis && <span className="text-gray-700 ml-2">— {t.reasons.basis}</span>}
          {t.reasons?.summary && <div className="text-gray-500 mt-0.5">{t.reasons.summary}</div>}
          {t.reasons?.metrics && <div className="mt-1"><MetricBadges m={t.reasons.metrics} /></div>}
          {(t.reasons?.top_features?.length ?? 0) > 0 && (
            <div className="mt-1"><FeatureContribList features={t.reasons!.top_features} /></div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ExitsTable({ exits }: { exits: ExitRow[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
            <th className="text-left px-3 py-2 font-medium">매도일</th>
            <th className="text-right px-3 py-2 font-medium">수량</th>
            <th className="text-right px-3 py-2 font-medium">평단 → 매도가</th>
            <th className="text-right px-3 py-2 font-medium">확정 손익</th>
            <th className="text-left px-3 py-2 font-medium">매도 사유</th>
          </tr>
        </thead>
        <tbody>
          {exits.map((e) => {
            const expanded = open === e.code;
            const pnlCls = e.realized_pnl == null ? "text-gray-400"
              : e.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700";
            const pct = e.realized_pnl != null && e.avg_buy_price && e.sold_qty
              ? (e.realized_pnl / (e.avg_buy_price * e.sold_qty)) * 100 : null;
            return (
              <>
                <tr
                  key={e.code}
                  className="border-t border-gray-100 cursor-pointer hover:bg-gray-50"
                  onClick={() => setOpen(expanded ? null : e.code)}
                >
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="text-gray-900">{e.name ?? e.code}</span>
                    <span className="font-mono text-xs text-gray-500 ml-2">({e.code})</span>
                    <span className="text-[10px] text-gray-400 ml-2">{expanded ? "▲" : "▼ 이력"}</span>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{e.last_sell_date}</td>
                  <td className="px-3 py-2 text-right font-mono">{e.sold_qty.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right font-mono whitespace-nowrap">
                    {e.avg_buy_price != null ? Math.round(e.avg_buy_price).toLocaleString() : "—"}
                    {" → "}
                    {e.est_sell_price != null ? `${Math.round(e.est_sell_price).toLocaleString()}${e.price_est ? "*" : ""}` : "—"}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${pnlCls}`}>
                    {e.realized_pnl != null
                      ? `${e.realized_pnl >= 0 ? "+" : ""}${Math.round(e.realized_pnl).toLocaleString()}${pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)` : ""}`
                      : "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-700 max-w-[260px]">
                    {e.reasons?.basis ?? "—"}
                  </td>
                </tr>
                {expanded && (
                  <tr key={`${e.code}-tl`} className="bg-gray-50/60">
                    <td colSpan={6} className="px-4 py-2">
                      <ExitTimeline code={e.code} />
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
