import Link from "next/link";
import { LiveOrderRow, parseUtc } from "@/lib/api";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/components/EquityChart";
import TossLink from "@/components/TossLink";

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
  SIMULATED: "close/flow 등 시뮬 전략의 가상 체결 (실제 주문 아님)",
};

const STRATEGY_SHORT: Record<string, string> = {
  open: "실계좌", close: "종가", flow: "수급", trail: "트레일",
  scale: "사다리", limit: "지정가", cafe: "카페",
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
            <th className="text-right px-3 py-2 font-medium" title="지정가 주문의 주문가 또는 체결 확정 후 실체결 평균가">가격</th>
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
            return (
              <tr key={o.id} className="border-t border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">
                  <span className="hidden sm:inline">
                    {parseUtc(o.submitted_at).toLocaleString("ko-KR", {
                      hour12: false, timeZone: "Asia/Seoul",
                    })}
                  </span>
                  <span className="sm:hidden">
                    {parseUtc(o.submitted_at).toLocaleString("ko-KR", {
                      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                      hour12: false, timeZone: "Asia/Seoul",
                    })}
                  </span>
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
                  <TossLink code={o.code} className="ml-2" />
                </td>
                <td className="px-3 py-2 text-right font-mono">{o.qty.toLocaleString()}</td>
                <td className="px-3 py-2 text-right font-mono">
                  {o.price == null ? "시장가" : o.price.toLocaleString()}
                </td>
                <td className={`px-3 py-2 text-right font-mono whitespace-nowrap ${
                  o.realized_pnl == null ? "text-gray-300"
                    : o.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700"
                }`}>
                  {o.realized_pnl != null ? (
                    <>
                      {`${o.realized_pnl >= 0 ? "+" : ""}${Math.round(o.realized_pnl).toLocaleString()}${o.realized_est ? "*" : ""}`}
                      {(() => {
                        // Back out the entry average from (sell price, qty, pnl)
                        // so the return % needs no extra API field.
                        if (o.price == null || o.qty <= 0) return null;
                        const entry = o.price - o.realized_pnl / o.qty;
                        if (entry <= 0) return null;
                        const pct = (o.price / entry - 1) * 100;
                        return (
                          <span className="block text-[11px] opacity-80">
                            {`${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%${o.realized_est ? "*" : ""}`}
                          </span>
                        );
                      })()}
                    </>
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
            );
          })}
        </tbody>
      </table>
    </div>
    <p className="md:hidden text-[10px] text-gray-400 mt-1 px-1">
      ← 표를 좌우로 밀면 전체 열을 볼 수 있습니다
    </p>
    </div>
  );
}
