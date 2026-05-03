import { BacktestMetrics, ExtendedMetrics } from "@/lib/api";

const fmtPct = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
const fmtNum = (v: number | null | undefined, digits = 4) =>
  v == null ? "—" : v.toFixed(digits);
const fmtKRW = (v: number | null | undefined) => {
  if (v == null) return "—";
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}억`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}만`;
  return v.toFixed(0);
};

const colorOf = (v: number | null | undefined, good: (n: number) => boolean) =>
  v == null ? "text-gray-500" : good(v) ? "text-emerald-600" : "text-red-600";

export default function MetricsTable({
  metrics,
  extended,
  benchmark,
}: {
  metrics: BacktestMetrics;
  extended: ExtendedMetrics | null;
  benchmark: string | null;
}) {
  // Headline cards on top
  const cards = [
    {
      label: "연환산 수익률 (ARR)",
      value: fmtPct(metrics.annualized_return),
      hint: "1년 기준으로 환산한 수익률",
      color: colorOf(metrics.annualized_return, (n) => n > 0),
    },
    {
      label: "정보비율 (IR)",
      value: fmtNum(metrics.information_ratio, 2),
      hint: "위험 한 단위당 알파 (>1 양호, >2 우수)",
      color: colorOf(metrics.information_ratio, (n) => n > 1),
    },
    {
      label: "최대낙폭 (MDD)",
      value: fmtPct(metrics.max_drawdown),
      hint: "고점 대비 최대 손실폭",
      color: colorOf(metrics.max_drawdown, (n) => n > -0.2),
    },
    {
      label: "샤프지수 (Sharpe)",
      value: fmtNum(extended?.sharpe ?? null, 2),
      hint: "초과수익 ÷ 변동성 (연환산)",
      color: colorOf(extended?.sharpe ?? null, (n) => n > 1),
    },
    {
      label: "칼마지수 (Calmar)",
      value: fmtNum(extended?.calmar ?? null, 2),
      hint: "ARR ÷ |MDD| — 1 이상이면 양호",
      color: colorOf(extended?.calmar ?? null, (n) => n > 1),
    },
    {
      label: "승률 (Win Rate)",
      value: fmtPct(extended?.win_rate ?? null, 1),
      hint: "양수 수익률을 기록한 거래일 비율",
      color: colorOf(extended?.win_rate ?? null, (n) => n > 0.5),
    },
  ];

  // Detail rows
  const rows = [
    { label: "누적 수익률", value: fmtPct(extended?.cumulative_return ?? null), note: "백테스트 전체 기간 누적" },
    { label: "일평균 수익률", value: fmtPct(metrics.mean, 4), note: "거래일 평균" },
    { label: "일변동성 (Std)", value: fmtPct(metrics.std, 4), note: "일별 수익률의 표준편차" },
    { label: "수익팩터 (Profit Factor)", value: fmtNum(extended?.profit_factor ?? null, 2), note: "이익합 / 손실합 — 1 미만이면 손실 우위" },
    { label: "거래일 수", value: extended?.trading_days != null ? `${extended.trading_days} 일` : "—", note: "백테스트 기간 내 실제 거래일" },
    { label: "총 회전율", value: fmtNum(extended?.total_turnover ?? null, 2), note: "백테스트 전체 누적 (1.0 = 자본 한 번 회전)" },
    { label: "일평균 회전율", value: fmtPct(extended?.avg_daily_turnover ?? null, 2), note: "포트폴리오의 매일 평균 매매 비중" },
    { label: "총 거래비용", value: extended?.total_cost != null ? `${(extended.total_cost * 100).toFixed(2)}% (자본 대비)` : "—", note: "수수료 + 거래세 누적" },
    { label: "벤치마크 종목코드", value: benchmark ?? "—", note: "비교 기준 (KODEX 200 ETF 등)" },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {cards.map((c) => (
          <div key={c.label} className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="text-xs text-gray-500 mb-1">{c.label}</div>
            <div className={`text-2xl font-semibold ${c.color}`}>{c.value}</div>
            <div className="text-xs text-gray-400 mt-1">{c.hint}</div>
          </div>
        ))}
      </div>

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-gray-600">상세 지표</th>
              <th className="text-right px-4 py-2 font-medium text-gray-600">값</th>
              <th className="text-left px-4 py-2 font-medium text-gray-500">설명</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b last:border-0">
                <td className="px-4 py-2 text-gray-700">{r.label}</td>
                <td className="px-4 py-2 text-right font-mono">{r.value}</td>
                <td className="px-4 py-2 text-xs text-gray-500">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export { fmtKRW };
