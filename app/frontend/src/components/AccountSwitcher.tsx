"use client";

import { useEffect } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api, LiveBalanceResponse } from "@/lib/api";
import { PRIMARY_STRATEGY, STRATEGY_COLORS, strategyLabel } from "@/lib/strategies";

/** 화면에 띄우는 계좌 순서. 백엔드 ACCOUNT_STRATEGIES 와 같은 구성이다. */
const ACCOUNTS = ["main", "cafe", "cool"] as const;
export type AccountId = (typeof ACCOUNTS)[number];

const FALLBACK_LABEL: Record<AccountId, string> = {
  main: "기본 계좌",
  cafe: "카페 계좌",
  cool: "냉각 계좌",
};

const fmtKRW = (n: number | undefined) =>
  n === undefined ? "—" : `${Math.round(n).toLocaleString()}원`;

/**
 * 계좌 전환 팝업.
 *
 * 탭만으로는 "왜 이 계좌가 비어 있는지"를 알 수 없었다. 세 계좌를 한 화면에
 * 놓고 각자의 연결 상태와 잔고를 같이 보여주면, 고를 때 이미 판단이 끝난다.
 * 미연결이면 서버가 내려주는 `account_error` 를 그대로 보여준다 — 환경변수
 * 누락인지, appkey 중복인지, KIS 가 계좌를 거부한 것인지가 여기서 갈린다.
 */
export default function AccountSwitcher({
  current,
  onSelect,
  onClose,
}: {
  current: AccountId;
  onSelect: (a: AccountId) => void;
  onClose: () => void;
}) {
  // ESC 로 닫기. 모달 전례가 없는 코드베이스라 최소한의 관례만 지킨다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const accountsQ = useQuery({
    queryKey: ["live-accounts"],
    queryFn: () => api.getAccounts(),
    staleTime: 60_000,
  });

  // 계좌별 잔고는 따로 조회한다 — 합산하지 않는 원칙 그대로, 각자의 책이다.
  const balances = useQueries({
    queries: ACCOUNTS.map((a) => ({
      queryKey: ["live-balance", a],
      queryFn: () => api.getLiveBalance(a),
      staleTime: 5_000,
    })),
  });

  const labelOf = (a: AccountId) =>
    accountsQ.data?.find((r) => r.account_id === a)?.label || FALLBACK_LABEL[a];

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label="계좌 선택"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <h2 className="text-base font-semibold">계좌 선택</h2>
          <button
            onClick={onClose}
            className="rounded px-2 py-1 text-sm text-gray-500 hover:bg-gray-100"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        <div className="divide-y divide-gray-100">
          {ACCOUNTS.map((a, i) => {
            const q = balances[i];
            const b = q.data as LiveBalanceResponse | undefined;
            const off = b?.source === "no_account" || b?.mode === "unconfigured";
            const strategy = PRIMARY_STRATEGY[a];
            const selected = a === current;
            return (
              <button
                key={a}
                onClick={() => {
                  onSelect(a);
                  onClose();
                }}
                className={`flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-gray-50 ${
                  selected ? "bg-emerald-50" : ""
                }`}
              >
                <span
                  className="mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: STRATEGY_COLORS[strategy] || "#888" }}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-gray-900">{labelOf(a)}</span>
                    {selected && (
                      <span className="rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] text-white">
                        보는 중
                      </span>
                    )}
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        off
                          ? "bg-gray-200 text-gray-600"
                          : "bg-emerald-100 text-emerald-700"
                      }`}
                    >
                      {q.isLoading ? "확인 중" : off ? "미연결" : "연결됨"}
                    </span>
                  </span>

                  <span className="mt-0.5 block text-xs text-gray-500">
                    {strategyLabel(strategy)} 전략
                  </span>

                  {off ? (
                    <span className="mt-1 block text-xs text-amber-700">
                      {b?.account_error || "계좌 자격증명이 설정되지 않았습니다."}
                    </span>
                  ) : (
                    <span className="mt-1 block text-xs text-gray-600">
                      예수금 {fmtKRW(b?.cash)} · 평가금 {fmtKRW(b?.total_eval)} ·{" "}
                      {b ? `${b.holdings.length}종목` : "—"}
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>

        <p className="border-t border-gray-100 px-4 py-2 text-[11px] text-gray-400">
          계좌는 각각 별개의 장부입니다 — 합산하지 않습니다.
        </p>
      </div>
    </div>
  );
}
