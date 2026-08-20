"use client";

import Link from "next/link";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import AssetSummary from "@/components/AssetSummary";
import HaltControl from "@/components/HaltControl";
import EquityChart from "@/components/EquityChart";
import StockCurvesChart from "@/components/StockCurvesChart";
import HoldingsTable from "@/components/HoldingsTable";
import OrdersTable from "@/components/OrdersTable";
import CafeCandidates from "@/components/CafeCandidates";
import SurgePicks from "@/components/SurgePicks";
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

export default function LiveDashboardPage() {
  const [chartView, setChartView] = useState<"strategy" | "stocks">("strategy");
  // 계좌는 합산하지 않는다 — 별개의 장부라 더하면 어느 쪽이 벌고 잃는지 가려진다.
  const [account, setAccount] = useState<"main" | "cafe">("main");
  const balance = useQuery({
    queryKey: ["live-balance", account],
    queryFn: () => api.getLiveBalance(account),
    refetchInterval: 30_000,
    // Matches the server-side snapshot window, so a remount inside it reuses
    // the cached payload instead of re-hitting KIS.
    staleTime: 5_000,
    // Keep the last numbers on screen while a refetch is in flight — the cards
    // must not blank out between polls.
    placeholderData: keepPreviousData,
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
        <div className="flex items-center gap-4 text-sm">
          <HaltControl />
          <Link href="/live/accounts" className="text-blue-600 hover:underline">
            ⚙️ 주문 설정
          </Link>
          <Link href="/live/retro" className="text-blue-600 hover:underline">
            🔁 매매 회고
          </Link>
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

      {/* Asset summary — total, sparkline, invested/cash bar, holdings donut, sub-stats */}
      <AssetSummary
        balance={b}
        loading={balance.isLoading}
        todayRealized={todayRealized.data}
        todayUnrealised={today?.unrealised_pnl}
        cumulative={cumulative}
        deployed={deployed}
        deployedRoi={deployedRoi}
        pnlRows={pnl.data?.rows || []}
      />

      {/* Equity chart — strategy curves vs per-stock curves */}
      <section className="bg-white rounded-lg border border-gray-200 p-3 sm:p-5">
        <div className="flex items-center justify-between mb-1 gap-2">
          <h2 className="text-lg font-semibold">
            {chartView === "strategy" ? "💹 전략 누적 수익률 곡선" : "💹 종목별 수익률 곡선"}
          </h2>
          <div className="flex gap-1 text-xs">
            {([["strategy", "전략"], ["stocks", "종목별"]] as const).map(([v, label]) => (
              <button
                key={v}
                onClick={() => setChartView(v)}
                className={`px-2.5 py-1 rounded border ${
                  chartView === v
                    ? "bg-emerald-600 text-white border-emerald-600"
                    : "bg-white text-gray-600 border-gray-200 hover:border-emerald-400"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {chartView === "strategy" ? (
          <>
            <p className="text-xs text-gray-500 mb-2">
              개별 종목이 아니라 <strong>계좌(전략) 전체의 자산 흐름</strong>입니다 — 종목은 매일
              교체되어도 &ldquo;전략이 돈을 벌고 있는가&rdquo;는 이 곡선으로 이어집니다.
              곡선 간 차이는 <strong>청산 규칙·픽 비교</strong>: open(실주문)은 신호 top-10 이탈 시
              매도, close·flow(시뮬)는 종가 매수 후 <strong>브래킷 매도(+10% 익절 / 전 저점
              손절)</strong>. 시드 각 {seedCash?.open ? fmtKRW(seedCash.open) : "…"} · 30초 갱신.
            </p>
            <details className="text-xs text-gray-500 mb-3">
              <summary className="cursor-pointer text-emerald-700 hover:underline select-none">
                ❓ 시뮬 전략별 매매 규칙 자세히 보기 (청산 규칙 실험 매트릭스)
              </summary>
              <div className="mt-1.5 bg-emerald-50/60 border border-emerald-100 rounded p-2.5 space-y-1">
                <p>공통 — 매수는 매일 신호 top-10 미보유 상위 종목(15:20 실시간가 체결, 하루 최대 2),
                  시드 각 1,000만원. 시뮬엔 신호 이탈 매도가 없고 아래 가격 규칙으로만 청산.
                  <strong> 공통 손절</strong>: 전 저점(진입 전 10거래일 최저가 −1% 버퍼) 이탈,
                  최대 −10% 캡.</p>
                <p className="text-amber-700">⚠️ <strong>2026-08-05 이전 시뮬 매수는 전일 종가로
                  체결되던 편향</strong>(데이터 지연 버그)이 있어 수익률이 과대합니다 — 8/6부터
                  실시간가 체결로 수정. 전략 비교는 8/6 이후 구간 기준으로 보세요.</p>
                <p>• <strong>close</strong> — 익절 +10% 고정 · <strong>flow</strong> — 같은 규칙,
                  픽만 기관·외국인 순매수 재랭킹</p>
                <p>• <strong>trail</strong> — 익절 없음, 최고 종가 대비 <strong>−7% 트레일링</strong> 청산</p>
                <p>• <strong>scale</strong> — <strong>사다리 익절</strong>: +10%에서 절반 →
                  +15%에서 잔여 절반 → +20% 전량. 매도가 한 번이라도 나가면 <strong>플로어 =
                  직전 단계</strong>(1차 후 +5%, 2차 후 +10%) 이탈 시 잔여 전량 매도</p>
                <p>• <strong>limit</strong> — 매수부터 다름: 후보 5종목에 <strong>전일 종가 −3%
                  지정가 예약</strong>, 당일 저가가 닿은 것만 랭크순 최대 2개 체결(미체결 당일 취소),
                  익절 +10% (예약 매도 모델)</p>
                <p>• <strong>surge</strong> — 급등 전야 프로파일(추세+눌림+거래량) 상위 2종목 매수, 청산은 close와 동일 브래킷</p>
                <p>• <strong>cafe</strong> — 종목 선정부터 다름: 카페 추천자 역설계 스크리너가
                  15:05 전 시장을 스캔(급등 눌림·신고가 돌파·재진입·낙폭 반등 4패턴), 상위 1~2종목을
                  15:28 종가 매수. 손절은 패턴별 <strong>구조적 손절선</strong>(캡 −15%), 익절 +10%</p>
                <p>판정은 매일 15:48(당일 시세 반영 직후) 시가·고가·저가 기준 — 갭으로 뚫린 날은
                  시가 체결, 익절·손절 동시 터치 시 손절 우선 가정. 범례 클릭으로 곡선별 표시/숨김.</p>
              </div>
            </details>
            <EquityChart rows={pnl.data?.rows || []} seedCash={seedCash} />
          </>
        ) : (
          <>
            <p className="text-xs text-gray-500 mb-3">
              실계좌(open)가 거쳐 간 <strong>모든 종목</strong>(현재 보유 + 청산)의 수익률을
              각각의 <strong>보유 기간 동안만</strong> 그립니다 — 그날 종가 ÷ 당시 평균단가 기준.
              범례 칩을 클릭하면 종목별로 표시/숨김을 전환할 수 있습니다.
            </p>
            <StockCurvesChart strategy="open" />
          </>
        )}
      </section>

      {/* Holdings — 계좌별. 합산하지 않는다. */}
      <section className="bg-white rounded-lg border border-gray-200 p-3 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
          <h2 className="text-lg font-semibold">📦 현재 보유 종목</h2>
          <div className="flex gap-1" role="tablist" aria-label="계좌 선택">
            {([["main", "기본 계좌"], ["cafe", "카페 계좌"]] as const).map(([v, label]) => (
              <button
                key={v}
                role="tab"
                aria-selected={account === v}
                onClick={() => setAccount(v)}
                className={`px-3 py-1 rounded text-xs font-medium border transition-colors ${
                  account === v
                    ? "bg-emerald-600 text-white border-emerald-600"
                    : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          {b?.source === "no_account"
            ? "카페 계좌가 아직 설정되지 않았습니다 (KIS_CAFE_* 환경변수)."
            : b
              ? `${b.holdings.length}종목 / 평가금액 ${fmtKRW(b.total_eval - b.cash)}`
              : "…"}
          {b?.source !== "no_account" &&
            " · 평균단가/현재가는 KIS API 기준이라 실제 KRX 시세와 다를 수 있습니다."}
        </p>
        {balance.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-gray-400">
            <span className="inline-block w-4 h-4 border-2 border-gray-300 border-t-emerald-500 rounded-full animate-spin" />
            보유 종목 불러오는 중…
          </div>
        ) : (
          <HoldingsTable holdings={b?.holdings || []} totalEval={b?.total_eval} />
        )}
      </section>

      {/* Recent exits — where yesterday's holdings went */}
      {(exits.data?.length ?? 0) > 0 && (
        <section className="bg-white rounded-lg border border-gray-200 p-3 sm:p-5">
          <h2 className="text-lg font-semibold mb-1">📤 최근 청산 종목</h2>
          <p className="text-xs text-gray-500 mb-3">
            신호 top-10 이탈 등으로 전량 매도되어 보유 목록에서 빠진 종목 — 매도 사유와
            확정 손익입니다. (* = 시장가 매도라 당일 시가 기준 추정) ·{" "}
            <a href="/guide#strategies" className="text-blue-600 underline">매도 기준 설명 →</a>
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
          어떤 기준으로 매수가 결정되는지는 <a href="/guide#strategies" className="underline font-medium">전략 설명서</a>,
          일일 스케줄·방법론은 <a href="/guide" className="underline">가이드</a> 참고.
        </p>
      </section>

      {/* Cafe screener candidates */}
      <CafeCandidates />

      {/* Surge-eve picks */}
      <SurgePicks />

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

