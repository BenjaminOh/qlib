"use client";

import Link from "next/link";
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, RetroEpisode } from "@/lib/api";
import { TradeHistory } from "@/components/HoldingsTable";
import TossLink from "@/components/TossLink";

const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;

const pctCls = (v: number | null | undefined) =>
  v == null ? "text-gray-400" : v >= 0 ? "text-emerald-700" : "text-red-700";

/** 매매 회고 — 에피소드 원장 + 가설 스코어보드 + 신호 IC. */
export default function RetroPage() {
  const [open, setOpen] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["retro"],
    queryFn: () => api.getRetro("open"),
    refetchInterval: 300_000,
  });

  const eps = data?.episodes ?? [];
  const closed = eps.filter((e) => e.ret_pct != null);
  const wins = closed.filter((e) => (e.ret_pct ?? 0) > 0).length;
  const avgOf = (vals: (number | null | undefined)[]) => {
    const xs = vals.filter((v): v is number => v != null);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  };
  const avgDrift = avgOf(closed.map((e) => e.post5_drift_pct));
  const avgGb = avgOf(closed.map((e) => e.give_back_pp));
  const ics = data?.daily_ic ?? [];
  const avgIc = avgOf(ics.map((i) => i.rank_ic));

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold text-gray-900">🔁 매매 회고</h1>
        <Link href="/live" className="text-blue-600 hover:underline text-sm">← 대시보드</Link>
      </div>

      {isLoading ? (
        <div className="text-center text-gray-400 py-10 text-sm">회고 계산 중…</div>
      ) : (
        <>
          {/* Summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              ["청산 에피소드", `${closed.length}건 · 승률 ${closed.length ? Math.round((wins / closed.length) * 100) : 0}%`],
              ["평균 give-back", avgGb != null ? `${avgGb.toFixed(1)}pp` : "—"],
              ["매도 후 5일 드리프트", pct(avgDrift)],
              ["신호 rank IC (평균)", avgIc != null ? avgIc.toFixed(3) : "—"],
            ].map(([label, value]) => (
              <div key={label} className="bg-white rounded-lg border border-gray-200 p-3">
                <p className="text-[11px] text-gray-500">{label}</p>
                <p className="text-lg font-semibold text-gray-900 tabular-nums">{value}</p>
              </div>
            ))}
          </div>

          {/* Hypothesis scoreboard */}
          <section className="bg-white rounded-lg border border-gray-200 p-4">
            <h2 className="text-lg font-semibold mb-2">가설 스코어보드</h2>
            <p className="text-xs text-gray-500 mb-3">
              회고는 가설을 채점만 합니다 — 임계 도달 → 승인 → 시뮬 곡선 검증을 통과해야 전략에 반영됩니다.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs sm:text-sm whitespace-nowrap">
                <thead className="text-gray-500">
                  <tr>
                    <th className="text-left py-1.5 pr-3 font-medium">가설</th>
                    <th className="text-left py-1.5 pr-3 font-medium">현재 증거</th>
                    <th className="text-right py-1.5 pr-3 font-medium">지지/반박</th>
                    <th className="text-left py-1.5 font-medium">채택 임계</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.scoreboard ?? []).map((h) => (
                    <tr key={h.key} className="border-t border-gray-100 align-top">
                      <td className="py-2 pr-3">
                        <span className="font-medium text-gray-900">{h.key}</span>
                        <div className="text-[11px] text-gray-500 whitespace-normal max-w-[180px]">{h.label}</div>
                      </td>
                      <td className="py-2 pr-3 whitespace-normal max-w-[300px] text-gray-700">{h.evidence}</td>
                      <td className="py-2 pr-3 text-right font-mono">
                        <span className="text-emerald-700">{h.support}</span>
                        {" / "}
                        <span className="text-red-700">{h.refute}</span>
                      </td>
                      <td className="py-2 whitespace-normal max-w-[240px] text-[11px] text-gray-500">{h.threshold}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* Episode ledger */}
          <section className="bg-white rounded-lg border border-gray-200 p-4">
            <h2 className="text-lg font-semibold mb-1">에피소드 원장 (실계좌 open)</h2>
            <p className="text-xs text-gray-500 mb-3">
              종목을 클릭하면 그 종목의 전체 매매 이력·판단 근거가 펼쳐집니다. 토스↗로 차트 확인.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs sm:text-sm whitespace-nowrap">
                <thead className="text-gray-500">
                  <tr>
                    <th className="text-left py-1.5 pr-3 font-medium">보유 기간</th>
                    <th className="text-left py-1.5 pr-3 font-medium">종목</th>
                    <th className="text-right py-1.5 pr-3 font-medium" title="진입 시점의 최근 5일 수익률 (과열 가설 지표)">진입 ret5</th>
                    <th className="text-right py-1.5 pr-3 font-medium">실현/평가</th>
                    <th className="text-right py-1.5 pr-3 font-medium" title="보유 중 최고 종가 대비 실현하지 못하고 반납한 폭">give-back</th>
                    <th className="text-right py-1.5 pr-3 font-medium" title="매도 후 5일간 주가가 더 갔는지 — 양수면 너무 일찍 판 것">매도 후 5일</th>
                    <th className="text-left py-1.5 font-medium">진입 근거</th>
                  </tr>
                </thead>
                <tbody>
                  {eps.map((e: RetroEpisode) => {
                    const id = `${e.code}-${e.entry_date}-${e.exit_date ?? "open"}`;
                    const expanded = open === id;
                    return (
                      <React.Fragment key={id}>
                        <tr
                          className="border-t border-gray-100 cursor-pointer hover:bg-gray-50"
                          onClick={() => setOpen(expanded ? null : id)}
                        >
                          <td className="py-1.5 pr-3 font-mono text-xs text-gray-500">
                            {e.entry_date} → {e.exit_date ?? <span className="text-emerald-600">보유 중</span>}
                          </td>
                          <td className="py-1.5 pr-3">
                            <span className="text-gray-900">{e.name ?? e.code}</span>
                            <span className="font-mono text-[11px] text-gray-400 ml-1.5">({e.code})</span>
                            <TossLink code={e.code} className="ml-1.5" />
                            <span className="text-[10px] text-gray-400 ml-1.5">{expanded ? "▲" : "▼ 이력"}</span>
                          </td>
                          <td className={`py-1.5 pr-3 text-right font-mono ${
                            (e.entry_metrics?.ret5 ?? 0) >= 20 ? "text-amber-600 font-semibold" : "text-gray-600"
                          }`}>
                            {pct(e.entry_metrics?.ret5 as number | null)}
                          </td>
                          <td className={`py-1.5 pr-3 text-right font-mono ${pctCls(e.ret_pct ?? e.unreal_pct)}`}>
                            {e.ret_pct != null ? pct(e.ret_pct) : `${pct(e.unreal_pct)} (평가)`}
                          </td>
                          <td className="py-1.5 pr-3 text-right font-mono text-gray-600">
                            {e.give_back_pp != null ? `${e.give_back_pp.toFixed(1)}pp` : "—"}
                          </td>
                          <td className={`py-1.5 pr-3 text-right font-mono ${pctCls(e.post5_drift_pct)}`}>
                            {pct(e.post5_drift_pct)}
                          </td>
                          <td className="py-1.5 text-[11px] text-gray-600 whitespace-normal max-w-[220px]">
                            {e.entry_basis || "—"}
                          </td>
                        </tr>
                        {expanded && (
                          <tr className="bg-gray-50/60">
                            <td colSpan={7} className="px-3 py-2">
                              <TradeHistory code={e.code} />
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {/* Daily IC */}
          <section className="bg-white rounded-lg border border-gray-200 p-4">
            <h2 className="text-lg font-semibold mb-1">신호 예측력 (일일 rank IC)</h2>
            <p className="text-xs text-gray-500 mb-3">
              당일 top-10 순위와 실제 당일 수익률의 순위 상관 (+1 = 순위 그대로 적중, 0 = 무상관).
              8/5부터가 새 학습(고정 150라운드) 체제입니다.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {ics.map((i) => (
                <div key={i.as_of}
                     className={`px-2 py-1 rounded text-xs font-mono border ${
                       i.rank_ic >= 0.3 ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                         : i.rank_ic <= -0.3 ? "bg-red-50 border-red-200 text-red-700"
                         : "bg-gray-50 border-gray-200 text-gray-600"
                     }`}
                     title={`표본 ${i.n}종목`}>
                  {i.as_of.slice(5)} · {i.rank_ic.toFixed(2)}
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
