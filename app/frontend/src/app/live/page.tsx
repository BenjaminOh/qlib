"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import EquityChart from "@/components/EquityChart";
import HoldingsTable from "@/components/HoldingsTable";
import OrdersTable from "@/components/OrdersTable";
import SignalPicksTable from "@/components/SignalPicksTable";
import ExitsTable from "@/components/ExitsTable";

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
  const exits = useQuery({
    queryKey: ["live-exits"],
    queryFn: api.getRecentExits,
    refetchInterval: 60_000,
  });
  const todayRealized = useQuery({
    queryKey: ["live-today-realized"],
    queryFn: api.getTodayRealized,
    refetchInterval: 60_000,
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
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl sm:text-2xl font-bold text-gray-900">📊 모의투자 라이브</h1>
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
          value={todayRealized.data ? `${fmtKRW(todayRealized.data.realized_pnl)}${todayRealized.data.estimated ? "*" : ""}` : "—"}
          color={todayRealized.data ? (todayRealized.data.realized_pnl >= 0 ? "good" : "bad") : "neutral"}
          hint={todayRealized.data?.sell_count ? `오늘 매도 ${todayRealized.data.sell_count}건${todayRealized.data.estimated ? " · 시가 추정" : ""}` : "오늘 매도 없음"}
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
          className="col-span-2 md:col-span-1"
        />
      </div>

      {/* Equity chart */}
      <section className="bg-white rounded-lg border border-gray-200 p-3 sm:p-5">
        <h2 className="text-lg font-semibold mb-1">💹 전략 누적 수익률 곡선</h2>
        <p className="text-xs text-gray-500 mb-3">
          개별 종목이 아니라 <strong>계좌(전략) 전체의 자산 흐름</strong>입니다 — 종목은 매일
          교체되어도 &ldquo;전략이 돈을 벌고 있는가&rdquo;는 이 곡선으로 이어집니다. 시초가
          실주문(open)과 종가 시뮬(close) 두 실행 방식의 성적 비교가 목적. 시드 open{" "}
          {seedCash?.open ? fmtKRW(seedCash.open) : "…"} / close {seedCash?.close ? fmtKRW(seedCash.close) : "…"} · 30초 갱신.
        </p>
        <EquityChart rows={pnl.data?.rows || []} seedCash={seedCash} />
      </section>

      {/* Holdings */}
      <section className="bg-white rounded-lg border border-gray-200 p-3 sm:p-5">
        <h2 className="text-lg font-semibold mb-1">📦 현재 보유 종목</h2>
        <p className="text-xs text-gray-500 mb-3">
          {b ? `${b.holdings.length}종목 / 평가금액 ${fmtKRW(b.total_eval - b.cash)}` : "…"}
          {" · 평균단가/현재가는 KIS 모의투자 API 기준이라 실제 KRX 시세와 다를 수 있습니다."}
        </p>
        <HoldingsTable holdings={b?.holdings || []} totalEval={b?.total_eval} />
      </section>

      {/* Recent exits — where yesterday's holdings went */}
      {(exits.data?.length ?? 0) > 0 && (
        <section className="bg-white rounded-lg border border-gray-200 p-3 sm:p-5">
          <h2 className="text-lg font-semibold mb-1">📤 최근 청산 종목</h2>
          <p className="text-xs text-gray-500 mb-3">
            신호 top-10 이탈 등으로 전량 매도되어 보유 목록에서 빠진 종목 — 매도 사유와
            확정 손익입니다. (* = 시장가 매도라 당일 시가 기준 추정) ·{" "}
            <a href="/guide/strategy" className="text-blue-600 underline">매도 기준 설명 →</a>
          </p>
          <ExitsTable exits={exits.data || []} />
        </section>
      )}

      {/* Latest signals */}
      <section className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 sm:p-5">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-lg font-semibold text-emerald-900">📌 다음 거래일 추천 종목</h2>
          <span className="text-xs text-emerald-700">
            {signals.data?.as_of ? `as of ${signals.data.as_of}` : "신호 미생성"}
          </span>
        </div>
        {!signals.data?.picks.length ? (
          <p className="text-sm text-emerald-800/70">
            아직 시그널이 생성되지 않았습니다. 평일 장 마감 후(15:45 데이터 갱신 → 신호 생성)
            자동으로 갱신됩니다.
          </p>
        ) : (
          <SignalPicksTable picks={signals.data.picks} />
        )}
        <p className="text-[11px] text-emerald-800/60 mt-2">
          행을 클릭하면 모델이 그 종목 점수에 반영한 상위 지표(기여도)를 볼 수 있습니다.
          어떤 기준으로 매수가 결정되는지는 <a href="/guide/strategy" className="underline font-medium">전략 설명서</a>,
          일일 스케줄·방법론은 <a href="/guide" className="underline">가이드</a> 참고.
        </p>
      </section>

      {/* Recent orders */}
      <section className="bg-white rounded-lg border border-gray-200 p-3 sm:p-5">
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
  className = "",
}: {
  label: string;
  value: string;
  hint?: string;
  color?: "good" | "bad" | "neutral";
  className?: string;
}) {
  const c =
    color === "good" ? "text-emerald-700" : color === "bad" ? "text-red-700" : "text-gray-900";
  return (
    <div className={`bg-white rounded-lg border border-gray-200 p-3 sm:p-4 ${className}`}>
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-lg sm:text-2xl font-semibold ${c}`}>{value}</div>
      {hint && <div className="text-xs text-gray-400 mt-1">{hint}</div>}
    </div>
  );
}
