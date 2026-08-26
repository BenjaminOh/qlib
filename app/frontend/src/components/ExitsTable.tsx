"use client";

import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ExitRow, StockTrade } from "@/lib/api";
import { FeatureContribList, MetricBadges } from "@/components/ReasonBadges";
import ChartLink from "@/components/ChartLink";

/** 행 식별자 — 같은 종목이 기간 안에 두 번 청산되면 두 줄이 나온다.
 *
 * 예전에는 `key={e.code}` 였다. 한 코드가 두 줄이 되는 순간 React key 가 충돌하고
 * 펼침 상태(`open === e.code`)도 공유돼 **두 줄이 같이 펼쳐진다.** */
const rowKey = (e: ExitRow) => `${e.code}-${e.episode ?? 1}`;

/** Full buy→sell timeline for an exited stock (same data as holdings drill-down).
 *
 * `strategy` must match the ledger /live/exits was read from — the exits list
 * and this drill-down showing different strategies would be two answers to the
 * same question. Also keeps the query cache keyed the same way HoldingsTable
 * keys it, so the two surfaces share one entry instead of racing.
 */
function ExitTimeline({ code, strategy }: { code: string; strategy: string }) {
  const trades = useQuery({
    queryKey: ["stock-trades", code, strategy],
    queryFn: () => api.getStockTrades(code, strategy),
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

export default function ExitsTable({
  exits,
  strategy = "open",
}: {
  exits: ExitRow[];
  /** Which ledger the exits came from — /live/exits defaults to "open". */
  strategy?: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
    {/* Mobile: card list (< md) */}
    <div className="md:hidden divide-y divide-gray-100">
      {exits.map((e) => {
        const rowId = rowKey(e);
        const expanded = open === rowId;
        const pnlCls = e.realized_pnl == null ? "text-gray-400"
          : e.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700";
        const pct = e.ret_pct != null ? e.ret_pct * 100 : null;
        return (
          <div key={rowId} className="py-2.5">
            <div
              className="cursor-pointer active:bg-gray-50"
              onClick={() => setOpen(expanded ? null : rowId)}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-gray-900 truncate">
                  {e.name ?? e.code}
                  <span className="font-mono text-[11px] text-gray-400 ml-1.5">({e.code})</span>
                  <ChartLink code={e.code} className="ml-1.5" />
                </span>
                <span className="font-mono text-[11px] text-gray-500 shrink-0">{e.last_sell_date}</span>
              </div>
              <div className="flex items-baseline justify-between gap-2 mt-0.5 text-xs font-mono text-gray-500">
                <span>
                  {e.sold_qty.toLocaleString()}주 ·{" "}
                  {e.avg_buy_price != null ? Math.round(e.avg_buy_price).toLocaleString() : "—"}
                  {" → "}
                  {e.est_sell_price != null ? `${Math.round(e.est_sell_price).toLocaleString()}${e.price_est ? "*" : ""}` : "—"}
                </span>
                <span className={`shrink-0 ${pnlCls}`}>
                  {e.realized_pnl != null
                    ? `${e.realized_pnl >= 0 ? "+" : ""}${Math.round(e.realized_pnl).toLocaleString()}${pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)` : ""}${e.price_est ? " 추정*" : " 확정"}`
                    : "—"}
                </span>
              </div>
              <div className="flex items-baseline justify-between gap-2 mt-0.5 text-[11px]">
                <span className="text-gray-600 truncate">{e.reasons?.basis ?? "—"}</span>
                <span className="text-gray-400 shrink-0">{expanded ? "▲ 접기" : "▼ 이력"}</span>
              </div>
            </div>
            {expanded && (
              <div className="mt-2 bg-gray-50/60 rounded px-2">
                <ExitTimeline code={e.code} strategy={strategy} />
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
            <th className="text-left px-3 py-2 font-medium">매도일</th>
            <th className="text-right px-3 py-2 font-medium">수량</th>
            <th className="text-right px-3 py-2 font-medium">평단 → 매도가</th>
            <th className="text-right px-3 py-2 font-medium" title="* 표시는 체결가 수신 전 추정치 — 매일 09:20 실체결가로 확정됩니다">손익</th>
            <th className="text-left px-3 py-2 font-medium">매도 사유</th>
          </tr>
        </thead>
        <tbody>
          {exits.map((e) => {
            const rowId = rowKey(e);
            const expanded = open === rowId;
            const pnlCls = e.realized_pnl == null ? "text-gray-400"
              : e.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700";
            const pct = e.ret_pct != null ? e.ret_pct * 100 : null;
            return (
              <Fragment key={rowId}>
                <tr
                  className="border-t border-gray-100 cursor-pointer hover:bg-gray-50"
                  onClick={() => setOpen(expanded ? null : rowId)}
                >
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="text-gray-900">{e.name ?? e.code}</span>
                    <span className="font-mono text-xs text-gray-500 ml-2">({e.code})</span>
                    <ChartLink code={e.code} className="ml-2" />
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
                      ? `${e.realized_pnl >= 0 ? "+" : ""}${Math.round(e.realized_pnl).toLocaleString()}${pct != null ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)` : ""}${e.price_est ? " 추정*" : " 확정"}`
                      : "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-700 max-w-[260px]">
                    {e.reasons?.basis ?? "—"}
                  </td>
                </tr>
                {expanded && (
                  <tr className="bg-gray-50/60">
                    <td colSpan={6} className="px-4 py-2">
                      <ExitTimeline code={e.code} strategy={strategy} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
    </>
  );
}
