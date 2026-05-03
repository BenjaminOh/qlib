"use client";

import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { PortfolioPoint } from "@/lib/api";

export default function DailyActivityChart({ data }: { data: PortfolioPoint[] }) {
  if (!data || data.length === 0) return null;

  const chart = data.map((p) => ({
    date: p.date,
    return: p.ret != null ? +(p.ret * 100).toFixed(3) : null,
    turnover: p.turnover != null ? +(p.turnover * 100).toFixed(3) : null,
  }));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <ComposedChart data={chart} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 10 }}
          tickFormatter={(v: string) => v.slice(5)}
          interval="preserveStartEnd"
          minTickGap={32}
        />
        <YAxis
          yAxisId="ret"
          tick={{ fontSize: 10 }}
          tickFormatter={(v: number) => `${v}%`}
        />
        <YAxis
          yAxisId="to"
          orientation="right"
          tick={{ fontSize: 10 }}
          tickFormatter={(v: number) => `${v}%`}
        />
        <Tooltip
          formatter={(v: number, name: string) => [
            `${v}%`,
            name === "return" ? "일수익률" : "일회전율",
          ]}
          labelFormatter={(l: string) => `📅 ${l}`}
        />
        <Legend
          formatter={(name: string) => (name === "return" ? "일수익률" : "일회전율")}
        />
        <Bar yAxisId="to" dataKey="turnover" fill="#fde68a" />
        <Line yAxisId="ret" type="monotone" dataKey="return" stroke="#10b981" dot={false} strokeWidth={1.5} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
