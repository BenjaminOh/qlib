"use client";

import { useMemo, useState } from "react";
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
import {
  STRATEGY_COLORS,
  STRATEGY_ICON,
  STRATEGY_LABELS,
  STRATEGY_ORDER,
  strategyLabel,
} from "@/lib/strategies";

type WideRow = { date: string } & Record<string, number | string | undefined>;

export default function EquityChart({
  rows,
  seedCash,
}: {
  rows: DailyPnLRow[];
  seedCash?: Record<string, number>;
}) {
  // 숨김 집합으로 관리한다(선택 집합이 아니라). 전략이 새로 추가됐을 때
  // 기본이 "보임"이어야 하기 때문이다 — 선택 집합이면 12번째 전략이 조용히
  // 안 보이는 채로 배포된다. StockCurvesChart 와 같은 규약이다.
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // 시드가 없는 전략은 값이 하나도 그려지지 않는다(아래 wide 계산 참조).
  // 칩까지 만들면 눌러도 아무 일도 안 일어나는 유령 범례가 된다 — 여기서 뺀다.
  // 순서는 행 도착 순서가 아니라 화면 순서(STRATEGY_ORDER)를 따른다.
  const strategies = useMemo(() => {
    const present = new Set(rows.map((r) => r.strategy));
    const plottable = Array.from(present).filter((s) => seedCash?.[s]);
    const rank = new Map(STRATEGY_ORDER.map((s, i) => [s, i]));
    return plottable.sort(
      (a, b) => (rank.get(a) ?? 999) - (rank.get(b) ?? 999)
    );
  }, [rows, seedCash]);

  const data: WideRow[] = useMemo(() => {
    // Group rows by strategy and normalize each line against its own seed cash.
    // Open and close run on different starting balances (real KIS paper vs
    // DB-only simulated), so a shared baseline would distort one of the lines.
    const dates = Array.from(new Set(rows.map((r) => r.trade_date))).sort();
    const byKey = new Map<string, DailyPnLRow>();
    for (const r of rows) byKey.set(`${r.trade_date}|${r.strategy}`, r);
    return dates.map((date) => {
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
  }, [rows, seedCash, strategies]);

  const toggle = (s: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  if (!rows || rows.length === 0) {
    return (
      <div className="text-center text-gray-400 py-12 text-sm">
        아직 누적된 거래일이 없습니다. (모의투자가 시작되면 여기 표시됩니다)
      </div>
    );
  }

  return (
    <div>
      {/* 칩 범례 — 클릭하면 그 곡선만 표시/숨김. recharts 기본 Legend 는
          클릭이 안 되고 라벨이 길어 두 줄로 흘러서 쓰지 않는다. */}
      <div className="flex flex-wrap gap-1.5 mb-3 text-xs">
        {strategies.map((s) => {
          const off = hidden.has(s);
          const icon = STRATEGY_ICON[s];
          return (
            <button
              key={s}
              onClick={() => toggle(s)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded border ${
                off
                  ? "bg-white text-gray-400 border-gray-200"
                  : "bg-white text-gray-800 border-gray-300"
              }`}
              title={
                (STRATEGY_LABELS[s] || s) +
                (off ? " · 클릭하면 표시" : " · 클릭하면 숨김")
              }
            >
              <span
                className="inline-block w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: off ? "#d1d5db" : STRATEGY_COLORS[s] || "#888" }}
              />
              <span className={off ? "line-through" : ""}>
                {icon ? `${icon} ` : ""}
                {strategyLabel(s)}
              </span>
            </button>
          );
        })}
        <button
          onClick={() =>
            setHidden(hidden.size ? new Set() : new Set(strategies))
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
              formatter={(v: number, name: string) => [`${v}%`, STRATEGY_LABELS[name] || name]}
              labelFormatter={(l: string) => `📅 ${l}`}
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
                hide={hidden.has(s)}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
