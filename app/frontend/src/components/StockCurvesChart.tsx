"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { api, StockCurve } from "@/lib/api";

/** Per-stock holding-period return curves with a chip legend that toggles
 *  each stock's line. Exited stocks render dashed; each line spans only the
 *  dates the position was actually held (nulls elsewhere, no connect). */

const PALETTE = [
  "#10b981", "#6366f1", "#f59e0b", "#ef4444", "#0ea5e9", "#8b5cf6",
  "#ec4899", "#84cc16", "#14b8a6", "#f97316", "#64748b", "#a855f7",
];

const seriesKey = (c: StockCurve) => `${c.code}#${c.episode}`;

export default function StockCurvesChart({ strategy = "open" }: { strategy?: string }) {
  const curvesQ = useQuery({
    queryKey: ["stock-curves", strategy],
    queryFn: () => api.getStockCurves(strategy),
    refetchInterval: 60_000,
  });
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const curves = useMemo(() => curvesQ.data?.curves ?? [], [curvesQ.data]);

  // One legend chip per code (episodes share color + toggle together).
  const codes = useMemo(() => {
    const seen = new Map<string, { name: string; held: boolean }>();
    for (const c of curves) {
      const prev = seen.get(c.code);
      seen.set(c.code, {
        name: c.name ?? c.code,
        held: (prev?.held ?? false) || c.status === "held",
      });
    }
    return Array.from(seen.entries()); // [code, {name, held}]
  }, [curves]);

  const colorOf = useMemo(() => {
    const m = new Map<string, string>();
    codes.forEach(([code], i) => m.set(code, PALETTE[i % PALETTE.length]));
    return m;
  }, [codes]);

  const data = useMemo(() => {
    const dates = Array.from(new Set(curves.flatMap((c) => c.points.map((p) => p.date)))).sort();
    return dates.map((d) => {
      const row: Record<string, string | number | null> = { date: d };
      for (const c of curves) {
        const pt = c.points.find((p) => p.date === d);
        row[seriesKey(c)] = pt ? +(pt.ret_pct * 100).toFixed(2) : null;
      }
      return row;
    });
  }, [curves]);

  const labelOf = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of curves) counts.set(c.code, (counts.get(c.code) ?? 0) + 1);
    const m = new Map<string, string>();
    for (const c of curves) {
      const base = c.name ?? c.code;
      m.set(seriesKey(c), (counts.get(c.code) ?? 1) > 1 ? `${base} #${c.episode}` : base);
    }
    return m;
  }, [curves]);

  const toggle = (code: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });

  if (curvesQ.isLoading)
    return <div className="text-center text-gray-400 py-10 text-sm">종목 곡선 불러오는 중…</div>;
  if (!curves.length)
    return <div className="text-center text-gray-400 py-10 text-sm">아직 매매 기록이 없습니다.</div>;

  return (
    <div>
      {/* Chip legend — click to show/hide a stock's line(s) */}
      <div className="flex flex-wrap gap-1.5 mb-3 text-xs">
        {codes.map(([code, meta]) => {
          const off = hidden.has(code);
          return (
            <button
              key={code}
              onClick={() => toggle(code)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded border ${
                off
                  ? "bg-white text-gray-400 border-gray-200"
                  : "bg-white text-gray-800 border-gray-300"
              }`}
              title={off ? "클릭하면 표시" : "클릭하면 숨김"}
            >
              <span
                className="inline-block w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: off ? "#d1d5db" : colorOf.get(code) }}
              />
              <span className={off ? "line-through" : ""}>{meta.name}</span>
              <span className={`text-[10px] ${meta.held ? "text-emerald-600" : "text-gray-400"}`}>
                {meta.held ? "보유중" : "청산"}
              </span>
            </button>
          );
        })}
        <button
          onClick={() =>
            setHidden(hidden.size ? new Set() : new Set(codes.map(([code]) => code)))
          }
          className="px-2.5 py-1 rounded border bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100"
        >
          {hidden.size ? "전체 표시" : "전체 숨김"}
        </button>
      </div>

      <div className="h-[220px] sm:h-[320px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: string) => v.slice(5)}
              interval="preserveStartEnd"
              minTickGap={28}
            />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v}%`} />
            <Tooltip
              formatter={(v: number, name: string) => [`${v}%`, labelOf.get(name) || name]}
              labelFormatter={(l: string) => `📅 ${l}`}
            />
            <ReferenceLine y={0} stroke="#9ca3af" strokeDasharray="3 3" />
            {curves.map((c) => (
              <Line
                key={seriesKey(c)}
                type="monotone"
                dataKey={seriesKey(c)}
                stroke={colorOf.get(c.code) || "#888"}
                strokeDasharray={c.status === "exited" ? "5 3" : undefined}
                dot={false}
                strokeWidth={2}
                connectNulls={false}
                hide={hidden.has(c.code)}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-[10px] text-gray-400 mt-1.5">
        각 선은 해당 종목을 <strong>보유한 기간에만</strong> 그려집니다 — 수익률은 그날 종가 ÷
        당시 평균단가 기준. 점선 = 청산 종목(추가 매수 시 평단이 바뀌며 선이 꺾일 수 있음).
        매도일 수치는 종가 기준 근사치로, 확정 손익은 청산 카드를 참고하세요.
      </p>
    </div>
  );
}
