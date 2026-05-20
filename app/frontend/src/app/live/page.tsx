"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import EquityChart from "@/components/EquityChart";
import HoldingsTable from "@/components/HoldingsTable";
import OrdersTable from "@/components/OrdersTable";

const fmtKRW = (v: number) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(2)}억`;
  if (a >= 1e4) return `${sign}${(a / 1e4).toFixed(0)}만`;
  return `${sign}${a.toFixed(0)}`;
};
const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`;

export default function LiveDashboardPage() {
  const balance = useQuery({
    queryKey: ["live-balance"],
    queryFn: api.getLiveBalance,
    refetchInterval: 30_000,
  });
  const signals = useQuery({
    queryKey: ["live-signals"],
    queryFn: api.getLiveSignals,
    refetchInterval: 60_000,
  });
  const orders = useQuery({
    queryKey: ["live-orders"],
    queryFn: () => api.getLiveOrders(20),
    refetchInterval: 30_000,
  });
  const pnl = useQuery({
    queryKey: ["live-pnl"],
    queryFn: () => api.getLiveDailyPnL(180),
    refetchInterval: 60_000,
  });

  const b = balance.data;
  const today = pnl.data?.rows?.[pnl.data.rows.length - 1];
  const seedCash = pnl.data?.seed_cash;
  // Cumulative card reflects the OPEN strategy (KIS real paper account) since
  // that's what `b` (live balance) corresponds to. Close strategy is plotted
  // on the chart alongside but tracked separately in the DB.
  const seedOpen = seedCash?.open;
  const cumulative = b && seedOpen ? b.total_eval / seedOpen - 1 : null;

  // "투입자본 수익률" — return on actually deployed capital (excludes idle cash).
  // Answers "what did the stocks I bought do?" rather than "what did my whole
  // portfolio do?". For a mostly-cash portfolio these diverge sharply.
  const deployed = b?.holdings.reduce((acc, h) => acc + h.qty * h.avg_price, 0) || 0;
  const evaluation = b?.holdings.reduce((acc, h) => acc + h.eval_value, 0) || 0;
  const deployedRoi = deployed > 0 ? evaluation / deployed - 1 : null;

  const modeColor =
    b?.mode === "real"
      ? "bg-red-100 text-red-700"
      : b?.mode === "paper"
      ? "bg-amber-100 text-amber-700"
      : "bg-gray-200 text-gray-600";

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold text-gray-900">📊 모의투자 라이브</h1>
          {b && (
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${modeColor}`}>
              {b.mode === "real" ? "실전" : b.mode === "paper" ? "모의투자" : "MOCK (KIS 미연결)"}
            </span>
          )}
        </div>
        <div className="space-x-4 text-sm">
          <Link href="/live/orders" className="text-blue-600 hover:underline">
            전체 주문 →
          </Link>
          <Link href="/live/positions" className="text-blue-600 hover:underline">
            일별 스냅샷 →
          </Link>
        </div>
      </div>

      {b?.mode === "mock" && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg p-3 text-sm">
          KIS_APP_KEY/SECRET이 설정되지 않아 mock 모드입니다. 실제 모의계좌 데이터를 보려면
          <code className="mx-1 px-1 bg-amber-100 rounded">.env</code>에 발급받은 키를 설정한 뒤
          worker/api 컨테이너를 재시작하세요.
        </div>
      )}

      {/* Headline cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Card label="총 평가금액" value={b ? fmtKRW(b.total_eval) : "…"} hint={b ? `예수금 ${fmtKRW(b.cash)}` : ""} />
        <Card
          label="당일 실현 손익"
          value={today ? fmtKRW(today.realised_pnl) : "—"}
          color={today?.realised_pnl != null ? (today.realised_pnl >= 0 ? "good" : "bad") : "neutral"}
          hint="장중 매도 체결 손익"
        />
        <Card
          label="당일 평가손익(미실현)"
          value={today ? fmtKRW(today.unrealised_pnl) : "—"}
          color={today?.unrealised_pnl != null ? (today.unrealised_pnl >= 0 ? "good" : "bad") : "neutral"}
          hint="현재 보유 종목 합산"
        />
        <Card
          label="누적 수익률"
          value={cumulative != null ? fmtPct(cumulative) : "—"}
          color={cumulative != null ? (cumulative >= 0 ? "good" : "bad") : "neutral"}
          hint="시드 자본 대비"
        />
        <Card
          label="투입자본 수익률"
          value={deployedRoi != null ? fmtPct(deployedRoi) : "—"}
          color={deployedRoi != null ? (deployedRoi >= 0 ? "good" : "bad") : "neutral"}
          hint={deployed > 0 ? `투입 ${fmtKRW(deployed)} 기준` : "현재 보유 0"}
        />
      </div>

      {/* Equity chart */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-lg font-semibold mb-1">💹 누적 수익률 곡선</h2>
        <p className="text-xs text-gray-500 mb-3">
          각 전략의 시드 대비 평가금액 변동 — open {seedCash?.open ? fmtKRW(seedCash.open) : "…"} / close {seedCash?.close ? fmtKRW(seedCash.close) : "…"}. 30초마다 자동 갱신.
        </p>
        <EquityChart rows={pnl.data?.rows || []} seedCash={seedCash} />
      </section>

      {/* Holdings */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-lg font-semibold mb-1">📦 현재 보유 종목</h2>
        <p className="text-xs text-gray-500 mb-3">
          {b ? `${b.holdings.length}종목 / 평가금액 ${fmtKRW(b.total_eval - b.cash)}` : "…"}
          {" · 평균단가/현재가는 KIS 모의투자 API 기준이라 실제 KRX 시세와 다를 수 있습니다."}
        </p>
        <HoldingsTable holdings={b?.holdings || []} totalEval={b?.total_eval} />
      </section>

      {/* Latest signals */}
      <section className="bg-emerald-50 border border-emerald-200 rounded-lg p-5">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-lg font-semibold text-emerald-900">📌 다음 거래일 추천 종목</h2>
          <span className="text-xs text-emerald-700">
            {signals.data?.as_of ? `as of ${signals.data.as_of}` : "신호 미생성"}
          </span>
        </div>
        {!signals.data?.picks.length ? (
          <p className="text-sm text-emerald-800/70">
            아직 시그널이 생성되지 않았습니다. 평일 15:35 KST에 자동으로 갱신됩니다.
          </p>
        ) : (
          <div className="overflow-x-auto bg-white rounded-md border border-emerald-100">
            <table className="w-full text-sm">
              <thead className="bg-emerald-50 text-emerald-900">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">순위</th>
                  <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
                  <th className="text-right px-3 py-2 font-medium">알파 점수</th>
                  <th className="text-left px-3 py-2 font-medium">조회</th>
                </tr>
              </thead>
              <tbody>
                {signals.data.picks.map((p) => (
                  <tr key={p.code} className="border-t border-emerald-50">
                    <td className="px-3 py-2 font-semibold text-emerald-700">#{p.rank}</td>
                    <td className="px-3 py-2">
                      <span className="text-gray-900">{p.name ?? p.code}</span>
                      {p.name && p.name !== p.code && (
                        <span className="font-mono text-xs text-gray-500 ml-2">({p.code})</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {p.score == null ? "—" : p.score.toFixed(6)}
                    </td>
                    <td className="px-3 py-2">
                      <a
                        href={`https://finance.naver.com/item/main.naver?code=${p.code}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:underline text-xs"
                      >
                        네이버 금융 ↗
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Recent orders */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-lg font-semibold">🧾 최근 주문 (20건)</h2>
          <Link href="/live/orders" className="text-blue-600 hover:underline text-sm">
            전체 보기 →
          </Link>
        </div>
        <OrdersTable orders={orders.data?.orders || []} compact />
      </section>
    </div>
  );
}

function Card({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  color?: "good" | "bad" | "neutral";
}) {
  const c =
    color === "good" ? "text-emerald-700" : color === "bad" ? "text-red-700" : "text-gray-900";
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-2xl font-semibold ${c}`}>{value}</div>
      {hint && <div className="text-xs text-gray-400 mt-1">{hint}</div>}
    </div>
  );
}
