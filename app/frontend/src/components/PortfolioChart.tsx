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
} from "recharts";
import { PortfolioPoint } from "@/lib/api";

export default function PortfolioChart({ data }: { data: PortfolioPoint[] }) {
  if (!data || data.length === 0) return null;

  // Normalize cumulative-return (%) for portfolio and benchmark.
  const initial = data[0]?.value || 1;
  const benchSeed = data.find((p) => p.bench != null && (p.bench as number) > 0)?.bench;

  const chartData = data.map((p) => ({
    date: p.date,
    portfolio: ((p.value / initial - 1) * 100).toFixed(2),
    benchmark:
      p.bench != null && benchSeed
        ? ((p.bench / benchSeed - 1) * 100).toFixed(2)
        : null,
  }));

  return (
    <ResponsiveContainer width="100%" height={360}>
      <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
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
          formatter={(value: string, name: string) => [`${value}%`, name === "portfolio" ? "포트폴리오" : "벤치마크"]}
          labelFormatter={(label: string) => `📅 ${label}`}
        />
        <Legend
          formatter={(name: string) => (name === "portfolio" ? "포트폴리오" : "벤치마크")}
        />
        <Line type="monotone" dataKey="portfolio" stroke="#2563eb" dot={false} strokeWidth={2} />
        {chartData.some((d) => d.benchmark !== null) && (
          <Line
            type="monotone"
            dataKey="benchmark"
            stroke="#9ca3af"
            dot={false}
            strokeWidth={1.5}
            strokeDasharray="4 4"
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
