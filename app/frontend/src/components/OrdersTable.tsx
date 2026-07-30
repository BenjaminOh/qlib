import Link from "next/link";
import { LiveOrderRow, parseUtc } from "@/lib/api";

const statusBadge = (status: string) => {
  const m: Record<string, string> = {
    SUBMITTED: "bg-blue-100 text-blue-700",
    FILLED: "bg-emerald-100 text-emerald-700",
    PARTIAL: "bg-amber-100 text-amber-700",
    REJECTED: "bg-red-100 text-red-700",
    CANCELLED: "bg-gray-100 text-gray-600",
  };
  return m[status] || "bg-gray-100 text-gray-600";
};

export default function OrdersTable({
  orders,
  compact = false,
}: {
  orders: LiveOrderRow[];
  compact?: boolean;
}) {
  if (!orders || orders.length === 0) {
    return (
      <div className="text-center text-gray-400 py-8 text-sm">
        아직 주문 이력이 없습니다.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th className="text-left px-3 py-2 font-medium">제출시각</th>
            <th className="text-left px-3 py-2 font-medium">방향</th>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
            <th className="text-right px-3 py-2 font-medium">수량</th>
            <th className="text-right px-3 py-2 font-medium">지정가</th>
            <th className="text-right px-3 py-2 font-medium" title="매도 시 확정 손익 (시장가는 당일 시가 추정)">손익</th>
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
                <td className="px-3 py-2 font-mono text-xs">
                  {parseUtc(o.submitted_at).toLocaleString("ko-KR", {
                    hour12: false, timeZone: "Asia/Seoul",
                  })}
                </td>
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
                </td>
                <td className="px-3 py-2 text-right font-mono">{o.qty.toLocaleString()}</td>
                <td className="px-3 py-2 text-right font-mono">
                  {o.price == null ? "시장가" : o.price.toLocaleString()}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${
                  o.realized_pnl == null ? "text-gray-300"
                    : o.realized_pnl >= 0 ? "text-emerald-700" : "text-red-700"
                }`}>
                  {o.realized_pnl != null
                    ? `${o.realized_pnl >= 0 ? "+" : ""}${Math.round(o.realized_pnl).toLocaleString()}${o.realized_est ? "*" : ""}`
                    : "—"}
                </td>
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusBadge(o.status)}`}>
                    {o.status}
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
  );
}
