"use client";

import { useMemo, useState } from "react";
import { LiveSignalRow } from "@/lib/api";
import { FeatureContribList, MetricBadges } from "@/components/ReasonBadges";
import SignalCompareTable from "@/components/SignalCompareTable";
import { buildThesis, compositeScores, thesisSegments } from "@/lib/thesis";
import ChartLink from "@/components/ChartLink";

function Thesis({ pick, picks }: { pick: LiveSignalRow; picks: LiveSignalRow[] }) {
  const text = buildThesis(pick, picks);
  if (!text) return null;
  return (
    <p className="text-xs text-gray-800 leading-relaxed mb-1">
      💡{" "}
      {thesisSegments(text).map((s, i) =>
        s.bold ? <strong key={i} className="text-emerald-800">{s.t}</strong> : <span key={i}>{s.t}</span>
      )}
    </p>
  );
}

function CompositeBadge({ comp }: { comp?: { score: number; pct?: number; tied: boolean } }) {
  if (!comp) return <span className="text-gray-400">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="font-semibold text-gray-900 tabular-nums"
        title={comp.pct != null ? "당일 채점된 전체 종목 중 위치 (신호 생성 시 저장)" : "당일 10개 픽 내 상대 확신도"}
      >
        {comp.pct != null ? `상위 ${comp.pct}%` : `${comp.score}점`}
      </span>
      <span className="inline-block w-10 h-1.5 rounded-full bg-gray-200 overflow-hidden align-middle">
        <span className="block h-full bg-emerald-500" style={{ width: `${comp.score}%` }} />
      </span>
      {comp.tied && (
        <span className="px-1 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px]"
              title="다른 종목과 알파 점수가 동일 — 이 순위는 코드순 참고용입니다 (모델 변별력 없음)">
          동점
        </span>
      )}
    </span>
  );
}

export default function SignalPicksTable({ picks }: { picks: LiveSignalRow[] }) {
  const [openCodes, setOpenCodes] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"list" | "compare">("list");
  const composites = useMemo(() => compositeScores(picks), [picks]);
  const toggleCode = (code: string) =>
    setOpenCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });

  const toggle = (
    <div className="flex justify-end mb-2 gap-1 text-xs">
      {([["list", "목록"], ["compare", "지표 비교표"]] as const).map(([v, label]) => (
        <button
          key={v}
          onClick={() => setView(v)}
          className={`px-2.5 py-1 rounded border ${
            view === v
              ? "bg-emerald-600 text-white border-emerald-600"
              : "bg-white text-gray-600 border-gray-200 hover:border-emerald-400"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );

  if (view === "compare") {
    return (
      <div>
        {toggle}
        <SignalCompareTable picks={picks} />
      </div>
    );
  }

  return (
    <div>
    {toggle}

    {/* Mobile: card list (< md) */}
    <div className="md:hidden bg-white rounded-md border border-emerald-100 divide-y divide-emerald-50">
      {picks.map((p) => {
        const expanded = openCodes.has(p.code);
        const hasDetail = !!p.reasons;
        return (
          <div key={p.code} className="px-3 py-2.5">
            <div
              className={hasDetail ? "cursor-pointer active:bg-emerald-50/40" : ""}
              onClick={() => hasDetail && toggleCode(p.code)}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm truncate">
                  <span className="font-semibold text-emerald-700 mr-1.5">#{p.rank}</span>
                  <span className="text-gray-900 font-medium">{p.name ?? p.code}</span>
                  <span className="font-mono text-[11px] text-gray-400 ml-1.5">({p.code})</span>
                </span>
                <span className="text-sm font-mono shrink-0">
                  {p.last_close != null ? `${p.last_close.toLocaleString()}원` : "—"}
                </span>
              </div>
              {p.reasons ? (
                <>
                  <div className="mt-1"><Thesis pick={p} picks={picks} /></div>
                  <div className="text-xs text-gray-500">
                    {p.reasons.summary}
                    {hasDetail && (
                      <span className="text-emerald-600 ml-1">{expanded ? "▲" : "▼ 상세"}</span>
                    )}
                  </div>
                  <div className="mt-1"><MetricBadges m={p.reasons.metrics} /></div>
                </>
              ) : (
                <div className="text-xs text-gray-400 mt-1">분석 정보 없음 (다음 신호부터 표시)</div>
              )}
              <div className="flex items-center justify-between mt-1 text-[11px]">
                <span className="flex items-center gap-2">
                  <CompositeBadge comp={composites.get(p.code)} />
                  <span className="font-mono text-gray-400">α {p.score == null ? "—" : p.score.toFixed(4)}</span>
                </span>
                <ChartLink code={p.code} />
              </div>
            </div>
            {expanded && p.reasons && p.reasons.top_features.length > 0 && (
              <div className="mt-2 bg-emerald-50/30 rounded px-2 py-2">
                <p className="text-[11px] text-gray-500 mb-1.5">
                  모델(LightGBM)이 이 종목 점수에 가장 크게 반영한 지표 Top {p.reasons.top_features.length}
                  — 양수(+)는 점수를 올린 요인, 음수(−)는 낮춘 요인
                </p>
                <FeatureContribList features={p.reasons.top_features} />
              </div>
            )}
          </div>
        );
      })}
    </div>

    {/* Desktop: full table (md+) */}
    <div className="hidden md:block overflow-x-auto bg-white rounded-md border border-emerald-100">
      <table className="w-full text-sm">
        <thead className="bg-emerald-50 text-emerald-900">
          <tr>
            <th className="text-left px-3 py-2 font-medium">순위</th>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
            <th className="text-right px-3 py-2 font-medium" title="신호 기준 최근 종가 — 매수 수량 계산에 쓰이는 가격">종가</th>
            <th className="text-left px-3 py-2 font-medium">매수 근거</th>
            <th className="text-right px-3 py-2 font-medium" title="전체 채점 종목(약 183개) 중 상위 몇 %인지 — 신호 생성 시 저장 (구신호는 픽 내 상대점수)">종합점수</th>
            <th className="text-left px-3 py-2 font-medium">조회</th>
          </tr>
        </thead>
        <tbody>
          {picks.map((p) => {
            const expanded = openCodes.has(p.code);
            const hasDetail = !!p.reasons;
            return (
              <>
                <tr
                  key={p.code}
                  className={`border-t border-emerald-50 ${hasDetail ? "cursor-pointer hover:bg-emerald-50/40" : ""}`}
                  onClick={() => hasDetail && toggleCode(p.code)}
                >
                  <td className="px-3 py-2 font-semibold text-emerald-700">#{p.rank}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="text-gray-900">{p.name ?? p.code}</span>
                    {p.name && p.name !== p.code && (
                      <span className="font-mono text-xs text-gray-500 ml-2">({p.code})</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono whitespace-nowrap align-top">
                    {p.last_close != null ? `${p.last_close.toLocaleString()}원` : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {p.reasons ? (
                      <div>
                        <Thesis pick={p} picks={picks} />
                        <div className="text-xs text-gray-500 mb-1">
                          {p.reasons.summary}
                          {hasDetail && (
                            <span className="text-emerald-600 ml-1">{expanded ? "▲" : "▼ 상세"}</span>
                          )}
                        </div>
                        <MetricBadges m={p.reasons.metrics} />
                      </div>
                    ) : (
                      <span className="text-xs text-gray-400">
                        분석 정보 없음 (다음 신호부터 표시)
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right align-top whitespace-nowrap">
                    <CompositeBadge comp={composites.get(p.code)} />
                    <div className="font-mono text-[10px] text-gray-400 mt-0.5">
                      α {p.score == null ? "—" : p.score.toFixed(6)}
                    </div>
                  </td>
                  <td className="px-3 py-2 align-top whitespace-nowrap">
                    <ChartLink code={p.code} />
                  </td>
                </tr>
                {expanded && p.reasons && p.reasons.top_features.length > 0 && (
                  <tr key={`${p.code}-detail`} className="bg-emerald-50/30">
                    <td />
                    <td colSpan={5} className="px-3 py-2">
                      <p className="text-[11px] text-gray-500 mb-1.5">
                        모델(LightGBM)이 이 종목 점수에 가장 크게 반영한 지표 Top {p.reasons.top_features.length}
                        — 양수(+)는 점수를 올린 요인, 음수(−)는 낮춘 요인
                      </p>
                      <FeatureContribList features={p.reasons.top_features} />
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
    </div>
  );
}
