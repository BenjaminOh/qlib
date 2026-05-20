import { LiveHolding } from "@/lib/api";

const fmtKRW = (v: number) => {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(2)}억`;
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(0)}만`;
  return `${sign}${a.toFixed(0)}`;
};
const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`;

export default function HoldingsTable({
  holdings,
  totalEval,
}: {
  holdings: LiveHolding[];
  totalEval?: number;
}) {
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
            return (
              <tr key={h.code} className="border-t border-gray-100">
                <td className="px-3 py-2">
                  <span className="text-gray-900">{h.name ?? h.code}</span>
                  {h.name && h.name !== h.code && (
                    <span className="font-mono text-xs text-gray-500 ml-2">({h.code})</span>
                  )}
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
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
