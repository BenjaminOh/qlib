"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  YAxis,
} from "recharts";
import {
  DailyPnLRow, LiveBalanceResponse, TodayRealized, fmtDateTime, parseUtc,
} from "@/lib/api";

/** Securities-app style asset summary: one panel with a single large figure,
 *  equity sparkline, invested/cash proportion bar, holdings donut, and a
 *  hairline-divided sub-stat row. Replaces the old scattered card grids. */

const PALETTE = [
  "#10b981", "#6366f1", "#f59e0b", "#ef4444", "#0ea5e9", "#8b5cf6",
  "#ec4899", "#84cc16", "#14b8a6", "#f97316", "#64748b", "#a855f7",
];
const CASH_COLOR = "#e5e7eb";

const won = (v: number) => `${Math.round(v).toLocaleString()}원`;
const signWon = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(Math.round(v)).toLocaleString()}`;
const pnlCls = (v: number | null | undefined) =>
  v == null ? "text-gray-400" : v >= 0 ? "text-emerald-600" : "text-red-600";

function Sparkline({ rows }: { rows: DailyPnLRow[] }) {
  const data = useMemo(
    () =>
      rows
        .filter((r) => r.strategy === "open")
        .slice(-30)
        .map((r) => ({ d: r.trade_date, v: r.ending_equity })),
    [rows],
  );
  if (data.length < 2) return null;
  return (
    <div className="h-12 mt-2 -mx-1">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 4, left: 4, bottom: 0 }}>
          <defs>
            <linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#10b981" stopOpacity={0.25} />
              <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Area type="monotone" dataKey="v" stroke="#10b981" strokeWidth={1.5}
                fill="url(#sparkFill)" dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function Stat({ label, value, hint, valueCls }: {
  label: string; value: string; hint?: string; valueCls?: string;
}) {
  return (
    <div className="px-3 first:pl-0 sm:last:pr-0">
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className={`text-sm sm:text-base font-semibold tabular-nums ${valueCls ?? "text-gray-900"}`}>
        {value}
      </div>
      {hint && <div className="text-[10px] text-gray-400 mt-0.5">{hint}</div>}
    </div>
  );
}

export default function AssetSummary({
  balance,
  loading,
  todayRealized,
  todayUnrealised,
  cumulative,
  deployed,
  deployedRoi,
  pnlRows,
}: {
  balance?: LiveBalanceResponse;
  loading: boolean;
  todayRealized?: TodayRealized;
  todayUnrealised?: number | null;
  cumulative: number | null;
  deployed: number;
  deployedRoi: number | null;
  pnlRows: DailyPnLRow[];
}) {
  const total = balance?.total_eval ?? 0;
  const cash = balance?.cash ?? 0;
  const invested = Math.max(total - cash, 0);
  const investPct = total > 0 ? (invested / total) * 100 : 0;

  const donut = useMemo(() => {
    const holdings = (balance?.holdings ?? [])
      .slice()
      .sort((a, b) => b.eval_value - a.eval_value)
      .map((h, i) => ({
        name: h.name ?? h.code,
        value: h.eval_value,
        color: PALETTE[i % PALETTE.length],
      }));
    return [...holdings, { name: "현금", value: Math.max(cash, 0), color: CASH_COLOR }];
  }, [balance, cash]);
  const donutTotal = donut.reduce((a, s) => a + s.value, 0);

  return (
    <section className="bg-white rounded-lg border border-gray-200 p-4 sm:p-6">
      <div className="flex flex-col lg:flex-row lg:items-stretch gap-5">
        {/* Hero: total + today + sparkline + proportion bar */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-1.5 min-w-0">
              <div className="text-xs text-gray-500">총 평가금액</div>
              {balance?.stale && (
                <span
                  className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[11px] font-medium tabular-nums whitespace-nowrap"
                  title="증권사 API 응답이 없어 마지막으로 조회된 값을 표시하고 있습니다."
                >
                  ⚠ {fmtDateTime(parseUtc(balance.fetched_at))} 기준
                </span>
              )}
            </div>
            {cumulative != null && (
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium tabular-nums ${
                cumulative >= 0 ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"
              }`}>
                누적 {(cumulative * 100).toFixed(2)}%
              </span>
            )}
          </div>
          {loading ? (
            <div className="h-10 w-52 bg-gray-100 rounded animate-pulse mt-1" />
          ) : (
            <div className="text-3xl sm:text-4xl font-semibold text-gray-900 tabular-nums mt-0.5">
              {won(total)}
            </div>
          )}
          <div className="text-xs text-gray-500 mt-1 tabular-nums">
            오늘{" "}
            <span className={pnlCls(todayRealized?.realized_pnl)}>
              실현 {todayRealized ? signWon(todayRealized.realized_pnl) : "—"}
              {todayRealized?.estimated ? "*" : ""}
            </span>
            {" · "}
            <span className={pnlCls(todayUnrealised)}>
              평가 {todayUnrealised != null ? signWon(todayUnrealised) : "—"}
            </span>
          </div>

          <Sparkline rows={pnlRows} />

          {/* Invested vs cash proportion */}
          <div className="mt-3">
            <div className="h-1.5 rounded-full bg-gray-200 overflow-hidden">
              <div className="h-full bg-emerald-500" style={{ width: `${investPct}%` }} />
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1.5 text-xs text-gray-600 tabular-nums">
              <span>
                <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 mr-1.5" />
                투자 {won(invested)} ({investPct.toFixed(1)}%)
              </span>
              <span>
                <span className="inline-block w-2 h-2 rounded-full bg-gray-300 mr-1.5" />
                현금 {won(cash)} ({(100 - investPct).toFixed(1)}%)
              </span>
            </div>
          </div>
        </div>

        {/* Holdings composition donut */}
        <div className="lg:w-[300px] shrink-0 flex items-center gap-3">
          <div className="relative w-28 h-28 shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={donut} dataKey="value" nameKey="name"
                     innerRadius={38} outerRadius={54} paddingAngle={1.5}
                     strokeWidth={0} isAnimationActive={false}>
                  {donut.map((s, i) => <Cell key={i} fill={s.color} />)}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              <span className="text-[10px] text-gray-400">투자</span>
              <span className="text-sm font-semibold text-gray-800 tabular-nums">
                {investPct.toFixed(1)}%
              </span>
            </div>
          </div>
          <ul className="text-xs space-y-1 min-w-0">
            {donut.map((s) => (
              <li key={s.name} className="flex items-center gap-1.5 tabular-nums">
                <span className="inline-block w-2 h-2 rounded-full shrink-0"
                      style={{ backgroundColor: s.color }} />
                <span className="truncate text-gray-700">{s.name}</span>
                <span className="text-gray-400 shrink-0">
                  {donutTotal > 0 ? ((s.value / donutTotal) * 100).toFixed(1) : "0.0"}%
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Sub-stats */}
      <div className="mt-4 pt-3 border-t border-gray-100 grid grid-cols-2 gap-y-3 sm:grid-cols-4 sm:divide-x sm:divide-gray-100">
        <Stat
          label="당일 실현 손익"
          value={todayRealized ? `${signWon(todayRealized.realized_pnl)}${todayRealized.estimated ? "*" : ""}` : "—"}
          valueCls={pnlCls(todayRealized?.realized_pnl)}
          hint={todayRealized?.sell_count ? `오늘 매도 ${todayRealized.sell_count}건` : "오늘 매도 없음"}
        />
        <Stat
          label="당일 평가손익(미실현)"
          value={todayUnrealised != null ? signWon(todayUnrealised) : "—"}
          valueCls={pnlCls(todayUnrealised)}
          hint="현재 보유 종목 합산"
        />
        <Stat
          label="누적 수익률"
          value={cumulative != null ? `${(cumulative * 100).toFixed(2)}%` : "—"}
          valueCls={pnlCls(cumulative)}
          hint="시드 자본 대비"
        />
        <Stat
          label="투입자본 수익률"
          value={deployedRoi != null ? `${(deployedRoi * 100).toFixed(2)}%` : "—"}
          valueCls={pnlCls(deployedRoi)}
          hint={deployed > 0 ? `매수원금 ${won(deployed)} 기준` : "현재 보유 없음"}
        />
      </div>
    </section>
  );
}
