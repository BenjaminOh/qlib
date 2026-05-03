"use client";

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
import { DailyPnLRow } from "@/lib/api";

export default function EquityChart({ rows }: { rows: DailyPnLRow[] }) {
  if (!rows || rows.length === 0) {
    return (
      <div className="text-center text-gray-400 py-12 text-sm">
        아직 누적된 거래일이 없습니다. (모의투자가 시작되면 여기 표시됩니다)
      </div>
    );
  }
  const initial = rows[0]?.starting_equity || 1;
  const data = rows.map((r) => ({
    date: r.trade_date,
    equity: +(((r.ending_equity / initial) - 1) * 100).toFixed(2),
  }));
  return (
    <ResponsiveContainer width="100%" height={320}>
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
        <Tooltip formatter={(v: string) => `${v}%`} labelFormatter={(l: string) => `📅 ${l}`} />
        <ReferenceLine y={0} stroke="#9ca3af" strokeDasharray="3 3" />
        <Line type="monotone" dataKey="equity" stroke="#10b981" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}
