"use client";

import { LiveSignalRow } from "@/lib/api";
import { compositeScores } from "@/lib/thesis";

/**
 * Side-by-side evaluation matrix for the recommended picks:
 * rows = stocks, columns = alpha score + the 6 intuitive metrics + the
 * model features that mattered most across ALL picks today (same columns
 * for every stock). Cells are heat-colored per column so the eye can scan
 * why each stock ranked where it did.
 */

const METRIC_COLS: { key: string; label: string; pct?: boolean }[] = [
  { key: "ret5", label: "5일 수익률", pct: true },
  { key: "ret20", label: "20일 수익률", pct: true },
  { key: "ret60", label: "60일 수익률", pct: true },
  { key: "ma20_gap", label: "20일선 괴리", pct: true },
  { key: "vol_ratio", label: "거래량 배율" },
  { key: "high60_pos", label: "60일고점 대비", pct: true },
];

function heat(value: number | null | undefined, maxAbs: number): React.CSSProperties {
  if (value == null || !isFinite(value) || maxAbs <= 0) return {};
  const t = Math.min(Math.abs(value) / maxAbs, 1);
  const alpha = 0.08 + t * 0.45;
  return {
    backgroundColor: value >= 0 ? `rgba(239,68,68,${alpha})` : `rgba(59,130,246,${alpha})`,
  };
}

export default function SignalCompareTable({ picks }: { picks: LiveSignalRow[] }) {
  const rows = picks.filter((p) => p.reasons);
  if (rows.length === 0)
    return <p className="text-xs text-gray-400 py-3">비교할 분석 데이터가 없습니다 (다음 신호부터 표시).</p>;

  // Model-feature columns: taken from the first pick carrying common_features.
  const featCols = rows.find((p) => (p.reasons?.common_features?.length ?? 0) > 0)
    ?.reasons?.common_features ?? [];

  // Per-column max |value| for heat normalization.
  const metricMax: Record<string, number> = {};
  for (const c of METRIC_COLS) {
    metricMax[c.key] = Math.max(
      ...rows.map((p) => Math.abs((p.reasons?.metrics as Record<string, number | null | undefined>)?.[c.key] ?? 0)),
      0,
    );
  }
  const featMax: Record<string, number> = {};
  for (const f of featCols) {
    featMax[f.name] = Math.max(
      ...rows.map((p) => Math.abs(p.reasons?.common_features?.find((x) => x.name === f.name)?.contrib ?? 0)),
      0,
    );
  }
  const scoreMax = Math.max(...rows.map((p) => Math.abs(p.score ?? 0)), 0);
  const composites = compositeScores(picks);

  return (
    <div className="bg-white rounded-md border border-emerald-100">
      <div className="overflow-x-auto">
      <table className="text-xs whitespace-nowrap">
        <thead className="bg-emerald-50 text-emerald-900">
          <tr>
            <th className="sticky left-0 bg-emerald-50 text-left px-2 py-2 font-medium z-10">종목</th>
            <th className="text-right px-2 py-2 font-medium" title="당일 픽 내 상대 확신도 (0~100)">종합점수</th>
            <th className="text-right px-2 py-2 font-medium border-r border-emerald-100">알파점수</th>
            {METRIC_COLS.map((c) => (
              <th key={c.key} className="text-right px-2 py-2 font-medium">{c.label}</th>
            ))}
            {featCols.length > 0 && (
              <th className="px-1 border-l border-emerald-200" title="이번 신호에서 전 종목에 걸쳐 영향이 컸던 모델 지표들 — 양수(적색)는 점수를 올림, 음수(청색)는 낮춤">
                ⟵ 시세 지표 · 모델 기여도 ⟶
              </th>
            )}
            {featCols.map((f) => (
              <th key={f.name} className="text-right px-2 py-2 font-medium" title={f.name}>
                {f.desc}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.code} className="border-t border-emerald-50">
              <td className="sticky left-0 bg-white px-2 py-1.5 z-10">
                <span className="font-semibold text-emerald-700 mr-1.5">#{p.rank}</span>
                <span className="text-gray-900">{p.name ?? p.code}</span>
              </td>
              <td className="text-right px-2 py-1.5 font-semibold tabular-nums">
                {composites.get(p.code)?.score ?? "—"}점
                {composites.get(p.code)?.tied && <span className="ml-1 text-[9px] text-amber-600">동점</span>}
              </td>
              <td className="text-right px-2 py-1.5 font-mono border-r border-emerald-100"
                  style={heat(p.score, scoreMax)}>
                {p.score?.toFixed(4) ?? "—"}
              </td>
              {METRIC_COLS.map((c) => {
                const v = (p.reasons?.metrics as Record<string, number | null | undefined>)?.[c.key];
                return (
                  <td key={c.key} className="text-right px-2 py-1.5 font-mono" style={heat(v, metricMax[c.key])}>
                    {v == null ? "—" : c.pct ? `${v > 0 ? "+" : ""}${v}%` : `${v}배`}
                  </td>
                );
              })}
              {featCols.length > 0 && <td className="border-l border-emerald-200" />}
              {featCols.map((f) => {
                const v = p.reasons?.common_features?.find((x) => x.name === f.name)?.contrib;
                return (
                  <td key={f.name} className="text-right px-2 py-1.5 font-mono" style={heat(v, featMax[f.name])}>
                    {v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(4)}`}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <p className="text-[10px] text-gray-400 px-2 py-1.5">
        <span className="md:hidden">← 표를 좌우로 밀면 전체 지표를 볼 수 있습니다 (종목명 열 고정) · </span>
        적색 = 상승/점수를 올린 요인 · 청색 = 하락/점수를 낮춘 요인 · 색 진하기 = 해당 열 내 상대 크기.
        모델 기여도 열은 이번 신호에서 전 종목 합산 영향이 큰 순서.
      </p>
    </div>
  );
}
