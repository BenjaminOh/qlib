"use client";

import { useState } from "react";
import { LiveSignalRow } from "@/lib/api";
import { FeatureContribList, MetricBadges } from "@/components/ReasonBadges";
import SignalCompareTable from "@/components/SignalCompareTable";

export default function SignalPicksTable({ picks }: { picks: LiveSignalRow[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const [view, setView] = useState<"list" | "compare">("list");

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
    <div className="overflow-x-auto bg-white rounded-md border border-emerald-100">
      <table className="w-full text-sm">
        <thead className="bg-emerald-50 text-emerald-900">
          <tr>
            <th className="text-left px-3 py-2 font-medium">순위</th>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
            <th className="text-right px-3 py-2 font-medium" title="신호 기준 최근 종가 — 매수 수량 계산에 쓰이는 가격">종가</th>
            <th className="text-left px-3 py-2 font-medium">매수 근거</th>
            <th className="text-right px-3 py-2 font-medium">알파 점수</th>
            <th className="text-left px-3 py-2 font-medium">조회</th>
          </tr>
        </thead>
        <tbody>
          {picks.map((p) => {
            const expanded = open === p.code;
            const hasDetail = !!p.reasons;
            return (
              <>
                <tr
                  key={p.code}
                  className={`border-t border-emerald-50 ${hasDetail ? "cursor-pointer hover:bg-emerald-50/40" : ""}`}
                  onClick={() => hasDetail && setOpen(expanded ? null : p.code)}
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
                        <div className="text-xs text-gray-800 mb-1">
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
                  <td className="px-3 py-2 text-right font-mono align-top">
                    {p.score == null ? "—" : p.score.toFixed(6)}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <a
                      href={`https://finance.naver.com/item/main.naver?code=${p.code}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline text-xs"
                      onClick={(e) => e.stopPropagation()}
                    >
                      네이버 ↗
                    </a>
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
