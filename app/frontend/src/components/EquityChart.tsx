"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { DailyPnLRow } from "@/lib/api";

const STRATEGY_COLORS: Record<string, string> = {
  open: "#10b981",   // emerald — KIS real paper account
  close: "#6366f1",  // indigo — DB-only simulated portfolio
};

const STRATEGY_LABELS: Record<string, string> = {
  open: "시초가 매수 (open)",
  close: "종가 매수 ±5% 브래킷 (close, 시뮬)",
};

type WideRow = { date: string } & Record<string, number | string | undefined>;

export default function EquityChart({
  rows,
  seedCash,
}: {
  rows: DailyPnLRow[];
  seedCash?: Record<string, number>;
}) {
  if (!rows || rows.length === 0) {
    return (
      <div className="text-center text-gray-400 py-12 text-sm">
        아직 누적된 거래일이 없습니다. (모의투자가 시작되면 여기 표시됩니다)
      </div>
    );
  }

  // Group rows by strategy and normalize each line against its own seed cash.
  // Open and close run on different starting balances (real KIS paper vs
  // DB-only simulated), so a shared baseline would distort one of the lines.
  const strategies = Array.from(new Set(rows.map((r) => r.strategy)));
  const dates = Array.from(new Set(rows.map((r) => r.trade_date))).sort();
  const byKey = new Map<string, DailyPnLRow>();
  for (const r of rows) byKey.set(`${r.trade_date}|${r.strategy}`, r);

  const data: WideRow[] = dates.map((date) => {
    const wide: WideRow = { date };
    for (const s of strategies) {
      const r = byKey.get(`${date}|${s}`);
      const seed = seedCash?.[s];
      if (r && seed) {
        wide[s] = +(((r.ending_equity / seed) - 1) * 100).toFixed(2);
      }
    }
    return wide;
  });

  return (
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
          formatter={(v: number, name: string) => [`${v}%`, STRATEGY_LABELS[name] || name]}
          labelFormatter={(l: string) => `📅 ${l}`}
        />
        <Legend
          formatter={(value: string) => STRATEGY_LABELS[value] || value}
          wrapperStyle={{ fontSize: 12 }}
        />
        <ReferenceLine y={0} stroke="#9ca3af" strokeDasharray="3 3" />
        {strategies.map((s) => (
          <Line
            key={s}
            type="monotone"
            dataKey={s}
            stroke={STRATEGY_COLORS[s] || "#888"}
            dot={false}
            strokeWidth={2}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
    </div>
  );
}
