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

export const STRATEGY_COLORS: Record<string, string> = {
  open: "#10b981",   // emerald — KIS real paper account
  close: "#6366f1",  // indigo — DB-only simulated portfolio
  flow: "#f59e0b",   // amber — close execution, 수급 재랭킹 픽
  trail: "#0ea5e9",  // sky — trailing −7% exits
  scale: "#a855f7",  // purple — +7% half take, remainder trails
  limit: "#ef4444",  // red — −3% resting-limit entries (사장님 방식)
  cafe: "#78716c",   // stone — recommender-mimic screener
  surge: "#db2777",  // pink — surge-eve profile picks
  cafeopen: "#0d9488", // teal — cafe's picks, next-morning limit entry
  cafecool: "#84cc16", // lime — cafe's picks minus the overheated ones
};

export const STRATEGY_LABELS: Record<string, string> = {
  open: "시초가 매수 (open)",
  close: "종가 매수 · 익절 +10%/전저점 손절 (시뮬)",
  flow: "수급 추종 · 브래킷 (시뮬)",
  trail: "트레일링 −7% (시뮬)",
  scale: "사다리 익절 10/15/20% (시뮬)",
  limit: "지정가 −3% 매수 · +10% 예약매도 (시뮬)",
  cafe: "카페 모사 스크리너 (시뮬)",
  surge: "급등 전야 프로파일 (시뮬)",
  cafeopen: "카페 모사 · 익일 시가 −3% 지정가 (시뮬)",
  cafecool: "카페 모사 · ret20 상한 50% (시뮬)",
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
