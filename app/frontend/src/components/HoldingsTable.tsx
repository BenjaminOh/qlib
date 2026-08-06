"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, LiveHolding, StockTrade } from "@/lib/api";
import { FeatureContribList, MetricBadges } from "@/components/ReasonBadges";
import TossLink from "@/components/TossLink";

const fmtKRW = (v: number) => {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(2)}억`;
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(0)}만`;
  return `${sign}${a.toFixed(0)}`;
};
const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`;

function TradeHistory({ code }: { code: string }) {
  const [detail, setDetail] = useState<number | null>(null);
  const trades = useQuery({
    queryKey: ["stock-trades", code],
    queryFn: () => api.getStockTrades(code),
  });

  if (trades.isLoading)
    return <p className="text-xs text-gray-400 py-2">매매 이력 불러오는 중…</p>;
  if (!trades.data?.length)
    return <p className="text-xs text-gray-400 py-2">기록된 매매 이력이 없습니다.</p>;

  const pctCls = (v: number | null) =>
    v == null ? "text-gray-400" : v >= 0 ? "text-emerald-700" : "text-red-700";

  return (
    <div className="overflow-x-auto py-1">
      <table className="w-full min-w-[640px] text-xs">
        <thead className="text-gray-500">
          <tr className="border-b border-gray-200">
            <th className="text-left px-2 py-1.5 font-medium">일자</th>
            <th className="text-left px-2 py-1.5 font-medium">구분</th>
            <th className="text-right px-2 py-1.5 font-medium">단가</th>
            <th className="text-right px-2 py-1.5 font-medium">평균단가</th>
            <th className="text-right px-2 py-1.5 font-medium">보유</th>
            <th className="text-right px-2 py-1.5 font-medium" title="그 시점 종가 기준">수익률</th>
            <th className="text-right px-2 py-1.5 font-medium" title="그 시점 종가 기준">수익금</th>
            <th className="text-left px-2 py-1.5 font-medium">판단 근거</th>
          </tr>
        </thead>
        <tbody>
          {trades.data.map((t: StockTrade, i: number) => {
            const isBuy = t.side === "BUY";
            const failed = t.status === "REJECTED";
            const expanded = detail === i;
            const hasDetail = !!t.reasons && ((t.reasons.metrics && Object.keys(t.reasons.metrics).length > 0) || (t.reasons.top_features?.length ?? 0) > 0);
            return (
              <>
                <tr
                  key={i}
                  className={`border-b border-gray-100 ${failed ? "opacity-50" : ""} ${hasDetail ? "cursor-pointer hover:bg-white" : ""}`}
                  onClick={() => hasDetail && setDetail(expanded ? null : i)}
                >
                  <td className="px-2 py-1.5 font-mono whitespace-nowrap">{t.trade_date}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    <span className={`font-semibold ${isBuy ? "text-red-600" : "text-blue-600"}`}>
                      {isBuy ? "매수" : "매도"} {t.qty.toLocaleString()}주
                    </span>
                    {failed && <span className="text-red-500 ml-1">거부</span>}
                    {t.strategy === "close" && <span className="text-gray-400 ml-1">(시뮬)</span>}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono whitespace-nowrap">
                    {failed ? "—" : t.exec_price != null ? (
                      <>
                        {t.exec_price.toLocaleString()}
                        {t.price_est && <span className="text-gray-400" title="시장가 주문 — 당일 시가로 추정">*</span>}
                      </>
                    ) : "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {t.avg_price != null ? Math.round(t.avg_price).toLocaleString() : "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {t.cum_qty != null ? `${t.cum_qty.toLocaleString()}주` : "—"}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${pctCls(t.ret_pct)}`}>
                    {t.ret_pct != null ? `${(t.ret_pct * 100).toFixed(2)}%` : "—"}
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono ${pctCls(t.realized_pnl ?? t.pnl_amt)}`}>
                    {t.realized_pnl != null
                      ? `${t.realized_pnl >= 0 ? "+" : ""}${Math.round(t.realized_pnl).toLocaleString()} 실현`
                      : t.pnl_amt != null
                        ? `${t.pnl_amt >= 0 ? "+" : ""}${Math.round(t.pnl_amt).toLocaleString()}`
                        : "—"}
                  </td>
                  <td className="px-2 py-1.5 max-w-[340px]">
                    {failed ? (
                      <span className="text-red-500">{t.error}</span>
                    ) : t.reasons ? (
                      <span className="text-gray-800">
                        <span className={`font-medium ${isBuy ? "text-red-700" : "text-blue-700"}`}>
                          {t.reasons.basis ?? ""}
                        </span>
                        {t.reasons.summary ? ` — ${t.reasons.summary}` : ""}
                        {hasDetail && (
                          <span className="text-emerald-600 ml-1">{expanded ? "▲" : "▼"}</span>
                        )}
                      </span>
                    ) : (
                      <span className="text-gray-400">미기록</span>
                    )}
                  </td>
                </tr>
                {expanded && t.reasons && (
                  <tr key={`${i}-d`} className="bg-white">
                    <td colSpan={8} className="px-3 py-2">
                      {t.reasons.metrics && <MetricBadges m={t.reasons.metrics} />}
                      {(t.reasons.top_features?.length ?? 0) > 0 && (
                        <div className="mt-1.5">
                          <p className="text-[10px] text-gray-400 mb-1">
                            당시 모델 기여 지표 Top {t.reasons.top_features.length}
                          </p>
                          <FeatureContribList features={t.reasons.top_features} />
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
      <p className="text-[10px] text-gray-400 mt-1">
        * 시장가 주문은 체결가 미수신으로 당일 시가 기준 추정치 · 수익률/수익금은 각 시점의 종가 기준
      </p>
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
    <>
    {/* Mobile: card list (< md) — same data, stacked layout */}
    <div className="md:hidden divide-y divide-gray-100">
      {holdings.map((h) => {
        const c = h.pnl >= 0 ? "text-emerald-700" : "text-red-700";
        const contribution = totalEval && totalEval > 0 ? h.pnl / totalEval : null;
        const expanded = open === h.code;
        return (
          <div key={h.code} className="py-2.5">
            <div
              className="cursor-pointer active:bg-gray-50"
              onClick={() => setOpen(expanded ? null : h.code)}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-gray-900 truncate">
                  {h.name ?? h.code}
                  <span className="font-mono text-[11px] text-gray-400 ml-1.5">({h.code})</span>
                  <TossLink code={h.code} className="ml-1.5" />
                </span>
                <span className={`text-sm font-mono font-semibold shrink-0 ${c}`}>{fmtPct(h.pnl_pct)}</span>
              </div>
              <div className="flex items-baseline justify-between gap-2 mt-0.5 text-xs text-gray-500 font-mono">
                <span>
                  {h.qty.toLocaleString()}주 · {h.avg_price.toLocaleString()} → {h.eval_price.toLocaleString()}
                </span>
                <span className={`shrink-0 ${c}`}>{fmtKRW(h.pnl)}</span>
              </div>
              <div className="mt-0.5 text-[11px] text-gray-400 font-mono">
                매수 총 {Math.round(h.qty * h.avg_price).toLocaleString()}원
              </div>
              <div className="flex items-baseline justify-between gap-2 mt-0.5 text-[11px] text-gray-400">
                <span>
                  평가 {fmtKRW(h.eval_value)}
                  {contribution != null && ` · 기여도 ${(contribution * 100).toFixed(2)}%p`}
                </span>
                <span className="text-gray-400">{expanded ? "▲ 접기" : "▼ 매매이력"}</span>
              </div>
            </div>
            {expanded && (
              <div className="mt-2 bg-gray-50/60 rounded px-2">
                <TradeHistory code={h.code} />
              </div>
            )}
          </div>
        );
      })}
    </div>

    {/* Desktop: full table (md+) */}
    <div className="hidden md:block overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
            <th className="text-right px-3 py-2 font-medium">수량</th>
            <th className="text-right px-3 py-2 font-medium">평균단가</th>
            <th className="text-right px-3 py-2 font-medium">매수 총금액</th>
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
                    <TossLink code={h.code} className="ml-2" />
                    <span className="text-[10px] text-gray-400 ml-2">{expanded ? "▲" : "▼ 매매이력"}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{h.qty.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right font-mono">{h.avg_price.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right font-mono">{Math.round(h.qty * h.avg_price).toLocaleString()}</td>
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
                    <td colSpan={9} className="px-4 py-2">
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
    </>
  );
}
