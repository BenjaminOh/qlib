"use client";

import { useState } from "react";
import { LiveSignalRow } from "@/lib/api";

function Badge({ label, tone }: { label: string; tone: "up" | "down" | "flat" }) {
  const cls =
    tone === "up"
      ? "bg-red-50 text-red-700 border-red-200"
      : tone === "down"
        ? "bg-blue-50 text-blue-700 border-blue-200"
        : "bg-gray-50 text-gray-600 border-gray-200";
  return (
    <span className={`inline-block text-[11px] px-1.5 py-0.5 rounded border mr-1 mb-1 ${cls}`}>
      {label}
    </span>
  );
}

function MetricBadges({ m }: { m: NonNullable<LiveSignalRow["reasons"]>["metrics"] }) {
  const badges: JSX.Element[] = [];
  if (m.ret5 != null)
    badges.push(<Badge key="r5" label={`5일 ${m.ret5 > 0 ? "+" : ""}${m.ret5}%`} tone={m.ret5 > 0 ? "up" : m.ret5 < 0 ? "down" : "flat"} />);
  if (m.ret20 != null)
    badges.push(<Badge key="r20" label={`20일 ${m.ret20 > 0 ? "+" : ""}${m.ret20}%`} tone={m.ret20 > 0 ? "up" : m.ret20 < 0 ? "down" : "flat"} />);
  if (m.vol_ratio != null && m.vol_ratio >= 1.5)
    badges.push(<Badge key="vr" label={`거래량 ${m.vol_ratio}배`} tone="up" />);
  if (m.ma20_gap != null)
    badges.push(<Badge key="ma" label={`20일선 ${m.ma20_gap > 0 ? "+" : ""}${m.ma20_gap}%`} tone={m.ma20_gap > 0 ? "up" : "down"} />);
  if (m.high60_pos != null)
    badges.push(<Badge key="hp" label={`60일고점 ${m.high60_pos}%`} tone={m.high60_pos >= -2 ? "up" : "flat"} />);
  return <div className="flex flex-wrap">{badges}</div>;
}

export default function SignalPicksTable({ picks }: { picks: LiveSignalRow[] }) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div className="overflow-x-auto bg-white rounded-md border border-emerald-100">
      <table className="w-full text-sm">
        <thead className="bg-emerald-50 text-emerald-900">
          <tr>
            <th className="text-left px-3 py-2 font-medium">순위</th>
            <th className="text-left px-3 py-2 font-medium">종목명 (코드)</th>
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
                    <td colSpan={4} className="px-3 py-2">
                      <p className="text-[11px] text-gray-500 mb-1.5">
                        모델(LightGBM)이 이 종목 점수에 가장 크게 반영한 지표 Top {p.reasons.top_features.length}
                        — 양수(+)는 점수를 올린 요인, 음수(−)는 낮춘 요인
                      </p>
                      <div className="space-y-1">
                        {p.reasons.top_features.map((f) => {
                          const width = Math.min(Math.abs(f.contrib) * 2000, 100);
                          return (
                            <div key={f.name} className="flex items-center gap-2 text-xs">
                              <span className="w-44 shrink-0 text-gray-700">{f.desc}</span>
                              <span className="font-mono text-[10px] text-gray-400 w-16 shrink-0">{f.name}</span>
                              <div className="flex-1 h-2 bg-gray-100 rounded overflow-hidden">
                                <div
                                  className={`h-full ${f.contrib >= 0 ? "bg-red-400" : "bg-blue-400"}`}
                                  style={{ width: `${width}%` }}
                                />
                              </div>
                              <span className={`font-mono w-16 text-right ${f.contrib >= 0 ? "text-red-600" : "text-blue-600"}`}>
                                {f.contrib >= 0 ? "+" : ""}{f.contrib.toFixed(4)}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
