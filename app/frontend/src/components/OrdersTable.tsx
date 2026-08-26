"use client";

import { Fragment, useMemo, useState } from "react";
import Link from "next/link";
import { LiveOrderRow, fmtDateTime, parseUtc } from "@/lib/api";
import ChartLink from "@/components/ChartLink";
import OrderStoryPanel from "@/components/OrderStoryPanel";
import { STRATEGY_COLORS, STRATEGY_LABELS, STRATEGY_SHORT } from "@/lib/strategies";

/** 손익 셀 툴팁 — 큰 숫자는 계좌와 같은 net 이되, 가격 등락과 비용을 숨기지 않는다.
 *
 * 예전에는 프론트가 손익에서 평단을 역산해 %를 냈다. net 은 (매도가 − 평단) × 수량 이
 * 아니라 그 역산이 틀어진다. 이제 서버가 avg_buy_price·ret_pct 를 직접 내려준다. */
function pnlTitle(o: LiveOrderRow): string | undefined {
  if (o.realized_pnl == null) return undefined;
  const parts: string[] = [];
  if (o.avg_buy_price != null && o.price != null) {
    const gross = (o.price / o.avg_buy_price - 1) * 100;
    parts.push(`가격 ${gross >= 0 ? "+" : ""}${gross.toFixed(2)}%`);
  }
  if (o.realized_cost != null && o.realized_cost !== 0) {
    parts.push(`수수료·세금 −${Math.round(Math.abs(o.realized_cost)).toLocaleString()}원`);
  }
  if (o.pnl_basis === "gross") parts.push("미정산 — 비용 미반영 추정치");
  return parts.length ? parts.join(" · ") : undefined;
}

const statusBadge = (status: string) => {
  const m: Record<string, string> = {
    SUBMITTED: "bg-blue-100 text-blue-700",
    FILLED: "bg-emerald-100 text-emerald-700",
    PARTIAL: "bg-amber-100 text-amber-700",
    REJECTED: "bg-red-100 text-red-700",
    CANCELLED: "bg-gray-100 text-gray-600",
    SIMULATED: "bg-violet-100 text-violet-700",
  };
  return m[status] || "bg-gray-100 text-gray-600";
};

const STATUS_LABELS: Record<string, string> = {
  SUBMITTED: "접수",
  FILLED: "체결",
  PARTIAL: "일부 체결",
  REJECTED: "거부",
  CANCELLED: "취소",
  SIMULATED: "시뮬",
};

const STATUS_TITLES: Record<string, string> = {
  SUBMITTED: "KIS에 접수된 상태 — 시장가 주문의 실체결가는 매일 09:20 대사에서 확정됩니다 (그 전까지 가격·손익은 시가 추정치*)",
  FILLED: "실체결가 확정 완료",
  PARTIAL: "주문 수량 중 일부만 체결",
  REJECTED: "KIS가 주문을 거부 — 사유는 행의 오류 메시지 참고",
  SIMULATED: "시뮬 전략의 장부상 가상 체결 — KIS에 주문이 나가지 않습니다",
};

// What the 가격 column actually means, per order kind. Derived server-side from
// status + ord_dvsn: a reconciled 시장가 order carries a price too, so `price`
// alone cannot tell these apart.
const KIND_LABELS: Record<string, string> = {
  market: "시장가 실체결",
  limit: "지정가 주문가",
  sim: "시뮬 체결가",
};

const KIND_TITLES: Record<string, string> = {
  market: "시장가 주문 — 표시 가격은 09:20 대사에서 확정된 실체결 평균가입니다",
  limit: "지정가 주문 — 표시 가격은 주문 지정가입니다",
  sim: "시뮬레이션 장부 체결가 — KIS에 주문이 나가지 않았으므로 지정가/시장가 구분이 없습니다",
};

export default function OrdersTable({
  orders,
  compact = false,
  showStrategy = false,
}: {
  orders: LiveOrderRow[];
  compact?: boolean;
  showStrategy?: boolean;
}) {
  // Row-click → inline story expansion (same pattern as HoldingsTable).
  const [openId, setOpenId] = useState<number | null>(null);

  // Footer totals. Declared BEFORE the empty-list early return — hooks must run
  // unconditionally. Only executed orders count: a REJECTED row still carries
  // qty/price, and adding those would overstate how much was actually put at
  // risk (the whole reason this column was asked for).
  const totals = useMemo(() => {
    const EXECUTED = new Set(["FILLED", "PARTIAL", "SIMULATED"]);
    let buy = 0, sell = 0, counted = 0;
    for (const o of orders || []) {
      if (!EXECUTED.has(o.status) || o.price == null) continue;
      counted++;
      if (o.side === "BUY") buy += o.qty * o.price;
      else sell += o.qty * o.price;
    }
    return { buy, sell, counted };
  }, [orders]);

  const nCols = 8 + (showStrategy ? 1 : 0) + (compact ? 0 : 1);
  if (!orders || orders.length === 0) {
    return (
      <div className="text-center text-gray-400 py-8 text-sm">
        아직 주문 이력이 없습니다.
      </div>
    );
  }
  return (
    <div>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] text-xs sm:text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th className="text-left px-3 py-2 font-medium">제출시각</th>
            {showStrategy && <th className="text-left px-3 py-2 font-medium">전략</th>}
            <th className="text-left px-3 py-2 font-medium">방향</th>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
            <th className="text-right px-3 py-2 font-medium">수량</th>
            <th className="text-right px-3 py-2 font-medium" title="실주문 — 시장가는 대사 후 실체결 평균가, 지정가는 주문가 · 시뮬 — 장부상 가상 체결가(실제 주문 아님)">가격</th>
            <th className="text-right px-3 py-2 font-medium" title="수량 × 가격 — 매수는 매수대금, 매도는 매도대금. 대사 전 시장가 주문은 가격이 없어 —로 표시됩니다">금액</th>
            <th className="text-right px-3 py-2 font-medium" title="매도 시 확정 손익과 수익률 (시장가는 당일 시가 추정)">손익 (수익률)</th>
            <th className="text-left px-3 py-2 font-medium">상태</th>
            {!compact && (
              <th className="text-left px-3 py-2 font-medium">KIS ID</th>
            )}
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => {
            const sideColor =
              o.side === "BUY" ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700";
            const expanded = openId === o.id;
            return (
              <Fragment key={o.id}>
              <tr
                className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                onClick={() => setOpenId(expanded ? null : o.id)}
                title="클릭하면 이 주문의 상세 스토리(진입 근거·규칙 선·당일 봉·판정)를 보여줍니다"
              >
                <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">
                  <span className="text-gray-400 mr-1">{expanded ? "▲" : "▼"}</span>
                  {fmtDateTime(parseUtc(o.submitted_at))}
                </td>
                {showStrategy && (
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span
                      className="px-1.5 py-0.5 rounded text-[11px] font-medium text-white cursor-help"
                      style={{ backgroundColor: STRATEGY_COLORS[o.strategy] || "#6b7280" }}
                      title={STRATEGY_LABELS[o.strategy] || o.strategy}
                    >
                      {STRATEGY_SHORT[o.strategy] || o.strategy}
                    </span>
                  </td>
                )}
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${sideColor}`}>
                    {o.side === "BUY" ? "매수" : "매도"}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span className="text-gray-900">{o.name ?? o.code}</span>
                  {o.name && o.name !== o.code && (
                    <span className="font-mono text-xs text-gray-500 ml-2">({o.code})</span>
                  )}
                  <ChartLink code={o.code} className="ml-2" />
                </td>
                <td className="px-3 py-2 text-right font-mono">{o.qty.toLocaleString()}</td>
                <td className="px-3 py-2 text-right font-mono">
                  {o.price == null ? (
                    <span className="cursor-help" title={KIND_TITLES[o.order_kind]}>시장가</span>
                  ) : (
                    <>
                      <span className="cursor-help" title={o.basis || KIND_TITLES[o.order_kind]}>
                        {o.price.toLocaleString()}
                      </span>
                      <span
                        className="block text-[11px] text-gray-400 cursor-help"
                        title={KIND_TITLES[o.order_kind]}
                      >
                        {KIND_LABELS[o.order_kind] ?? o.order_kind}
                      </span>
                      {o.discount_pct != null && (
                        // Blue-for-down (KR convention), deliberately NOT the
                        // emerald/red PnL palette — a deeper discount is good,
                        // so PnL sign-colouring would invert the meaning.
                        <span
                          className={`block text-[11px] ${
                            o.discount_pct < 0 ? "text-blue-600" : "text-gray-500"
                          }`}
                          title={`전일종가 ${o.prev_close?.toLocaleString() ?? "—"} 대비 실제 체결 할인율 — 예약가보다 깊으면 갭하락 시가에 체결된 것입니다`}
                        >
                          {`${o.discount_pct.toFixed(1)}% vs 전일`}
                        </span>
                      )}
                    </>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono whitespace-nowrap">
                  {o.price == null
                    ? <span className="text-gray-300">—</span>
                    : Math.round(o.qty * o.price).toLocaleString()}
                </td>
                <td className={`px-3 py-2 text-right font-mono whitespace-nowrap ${
                  o.realized_pnl == null ? "text-gray-300"
                    : o.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700"
                }`}>
                  {o.realized_pnl != null ? (
                    <span title={pnlTitle(o)}>
                      {`${o.realized_pnl >= 0 ? "+" : ""}${Math.round(o.realized_pnl).toLocaleString()}${o.realized_est ? "*" : ""}`}
                      {o.ret_pct != null && (
                        <span className="block text-[11px] opacity-80">
                          {`${o.ret_pct >= 0 ? "+" : ""}${(o.ret_pct * 100).toFixed(1)}%${o.realized_est ? "*" : ""}`}
                        </span>
                      )}
                    </span>
                  ) : "—"}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-medium cursor-help ${statusBadge(o.status)}`}
                    title={STATUS_TITLES[o.status] || o.status}
                  >
                    {STATUS_LABELS[o.status] || o.status}
                  </span>
                  {o.error && (
                    <div className="text-xs text-red-600 truncate max-w-xs" title={o.error}>
                      {o.error.split("\n")[0]}
                    </div>
                  )}
                </td>
                {!compact && (
                  <td className="px-3 py-2 font-mono text-xs text-gray-600">
                    {o.kis_order_id ?? "—"}
                  </td>
                )}
              </tr>
              {expanded && (
                <tr className="border-t border-gray-100 bg-gray-50/40">
                  <td colSpan={nCols} className="px-3 py-2">
                    <OrderStoryPanel order={o} />
                  </td>
                </tr>
              )}
              </Fragment>
            );
          })}
        </tbody>
        {/* Totals over the rows actually on screen. The parent applies the
            strategy / BUY-SELL filters before handing us `orders`
            (live/orders/page.tsx), so labelling the scope matters — otherwise
            the number reads as an all-time total. Hidden in compact mode: the
            dashboard widget is a preview, not a ledger. */}
        {!compact && totals.counted > 0 && (
          <tfoot className="bg-gray-50 text-gray-700 border-t-2 border-gray-200">
            <tr>
              <td className="px-3 py-2 text-xs text-gray-500" colSpan={nCols - 3}>
                표시된 {totals.counted.toLocaleString()}건 기준
                <span className="text-gray-400"> (체결분만 · 거부/미체결 제외)</span>
              </td>
              <td className="px-3 py-2 text-right font-mono whitespace-nowrap" colSpan={3}>
                <span className="text-emerald-700">
                  매수 {Math.round(totals.buy).toLocaleString()}원
                </span>
                <span className="text-gray-300 mx-2">·</span>
                <span className="text-red-700">
                  매도 {Math.round(totals.sell).toLocaleString()}원
                </span>
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
    <p className="md:hidden text-[10px] text-gray-400 mt-1 px-1">
      ← 표를 좌우로 밀면 전체 열을 볼 수 있습니다
    </p>
    </div>
  );
}
