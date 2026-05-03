"use client";

import { useMemo, useState } from "react";
import { TradeDay } from "@/lib/api";

const fmtKRW = (v: number | null) => {
  if (v == null) return "—";
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(2)}억`;
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(0)}만`;
  return `${sign}${a.toFixed(0)}`;
};

const fmtPct = (v: number | null) => {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
};

const PAGE_SIZE = 10;

export default function RecentTrades({ trades }: { trades: TradeDay[] }) {
  const [shown, setShown] = useState(PAGE_SIZE);

  // newest first
  const ordered = useMemo(() => [...trades].reverse(), [trades]);
  const visible = ordered.slice(0, shown);

  const totals = useMemo(() => {
    let buys = 0, sells = 0, realizedPnL = 0, wins = 0, losses = 0, sells_with_pnl = 0;
    for (const day of trades) {
      for (const o of day.orders) {
        if (o.side === "BUY") buys += 1;
        else {
          sells += 1;
          if (o.pnl != null) {
            realizedPnL += o.pnl;
            sells_with_pnl += 1;
            if (o.pnl > 0) wins += 1;
            else if (o.pnl < 0) losses += 1;
          }
        }
      }
    }
    return { buys, sells, realizedPnL, wins, losses, sells_with_pnl };
  }, [trades]);

  if (!trades || trades.length === 0) return null;

  return (
    <section className="bg-white rounded-lg border border-gray-200 p-5">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="text-lg font-semibold">🧾 최근 거래 내역 (최근 {trades.length}일)</h2>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        백테스트 기간 마지막 약 3개월의 시뮬레이션 매매 내역입니다. 매도 시점의 손익은 평균 매수 단가 기준 실현 손익입니다.
      </p>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-4 text-xs">
        <div className="bg-gray-50 rounded p-2"><div className="text-gray-500">매수 건수</div><div className="font-semibold">{totals.buys}</div></div>
        <div className="bg-gray-50 rounded p-2"><div className="text-gray-500">매도 건수</div><div className="font-semibold">{totals.sells}</div></div>
        <div className="bg-emerald-50 rounded p-2"><div className="text-emerald-700">승 / 패</div><div className="font-semibold text-emerald-800">{totals.wins} / {totals.losses}</div></div>
        <div className="bg-emerald-50 rounded p-2"><div className="text-emerald-700">매도 승률</div><div className="font-semibold text-emerald-800">{totals.sells_with_pnl > 0 ? `${(totals.wins / totals.sells_with_pnl * 100).toFixed(1)}%` : "—"}</div></div>
        <div className={`rounded p-2 ${totals.realizedPnL >= 0 ? "bg-emerald-100" : "bg-red-100"}`}>
          <div className={totals.realizedPnL >= 0 ? "text-emerald-700" : "text-red-700"}>실현 손익 합계</div>
          <div className={`font-semibold ${totals.realizedPnL >= 0 ? "text-emerald-800" : "text-red-800"}`}>{fmtKRW(totals.realizedPnL)}</div>
        </div>
      </div>

      <div className="space-y-3">
        {visible.map((day) => {
          const dayPnL = day.orders.reduce((s, o) => s + (o.pnl ?? 0), 0);
          const colorPnL = dayPnL > 0 ? "text-emerald-700" : dayPnL < 0 ? "text-red-700" : "text-gray-500";
          return (
            <div key={day.date} className="border border-gray-100 rounded-md">
              <div className="bg-gray-50 px-3 py-2 text-sm font-medium text-gray-700 border-b border-gray-100 flex justify-between">
                <span>{day.date} · {day.orders.length}건</span>
                <span className={`text-xs font-mono ${colorPnL}`}>당일 실현 손익 {fmtKRW(dayPnL)}</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-gray-500">
                    <tr>
                      <th className="text-left px-3 py-1 font-normal">방향</th>
                      <th className="text-left px-3 py-1 font-normal">종목명 (코드)</th>
                      <th className="text-right px-3 py-1 font-normal">수량</th>
                      <th className="text-right px-3 py-1 font-normal">체결가</th>
                      <th className="text-right px-3 py-1 font-normal">금액</th>
                      <th className="text-right px-3 py-1 font-normal">실현 손익</th>
                    </tr>
                  </thead>
                  <tbody>
                    {day.orders.map((o, i) => {
                      const pnlColor = o.pnl == null ? "text-gray-400" : o.pnl > 0 ? "text-emerald-700" : o.pnl < 0 ? "text-red-700" : "text-gray-500";
                      return (
                        <tr key={`${o.code}-${i}`} className="border-t border-gray-50">
                          <td className="px-3 py-1">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                              o.side === "BUY" ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"
                            }`}>
                              {o.side === "BUY" ? "매수" : "매도"}
                            </span>
                          </td>
                          <td className="px-3 py-1">
                            <span className="text-gray-900">{o.name ?? o.code}</span>
                            {o.name && o.name !== o.code && (
                              <span className="font-mono text-xs text-gray-500 ml-2">({o.code})</span>
                            )}
                          </td>
                          <td className="px-3 py-1 text-right font-mono">
                            {o.deal_amount == null ? "—" : Math.abs(o.deal_amount).toFixed(0)}
                          </td>
                          <td className="px-3 py-1 text-right font-mono">
                            {o.trade_price == null ? "—" : o.trade_price.toLocaleString()}
                          </td>
                          <td className="px-3 py-1 text-right font-mono">{fmtKRW(o.trade_value)}</td>
                          <td className={`px-3 py-1 text-right font-mono ${pnlColor}`}>
                            {o.side === "SELL" ? (
                              <>
                                {fmtKRW(o.pnl)}
                                {o.pnl_pct != null && (
                                  <span className="ml-2 text-xs">({fmtPct(o.pnl_pct)})</span>
                                )}
                              </>
                            ) : (
                              <span className="text-gray-300">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </div>

      {ordered.length > shown && (
        <div className="mt-3 text-center">
          <button
            type="button"
            onClick={() => setShown((s) => s + PAGE_SIZE)}
            className="text-sm text-blue-600 hover:underline"
          >
            {Math.min(PAGE_SIZE, ordered.length - shown)}일 더 보기 (남은 {ordered.length - shown}일)
          </button>
        </div>
      )}
    </section>
  );
}
