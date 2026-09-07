"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  api,
  AccountPolicyRow,
  AccountSidePolicy,
  OrdType,
  PriceBase,
} from "@/lib/api";

/** Which price the offset is measured from. Empty base = market order. */
const BASES: { key: PriceBase; label: string; hint: string }[] = [
  { key: "prev_close", label: "전일 종가", hint: "limit 시뮬 곡선과 같은 기준 — 실측 데이터와 바로 비교됩니다" },
  { key: "open", label: "당일 시가", hint: "09:00 시가 기준 — 갭 상승일 추격을 더 강하게 회피합니다" },
  { key: "quote", label: "현재가(호가)", hint: "주문 시점 현재가 — 기준가가 매일 흔들립니다" },
];

const ORD_TYPES: { key: OrdType; label: string }[] = [
  { key: "market", label: "시장가" },
  { key: "limit", label: "지정가" },
];

function sideDefaults(side: "buy" | "sell", p: AccountSidePolicy): AccountSidePolicy {
  // A limit needs a base; falling back to 전일 종가 keeps the form from
  // submitting an ord_type the server will reject.
  return {
    ...p,
    base: p.ord_type === "limit" ? p.base ?? "prev_close" : null,
    offset_pct: p.ord_type === "limit" ? p.offset_pct : 0,
    cancel_hhmm: p.ord_type === "limit" ? p.cancel_hhmm : null,
  };
}

function SideEditor({
  side,
  value,
  onChange,
}: {
  side: "buy" | "sell";
  value: AccountSidePolicy;
  onChange: (next: AccountSidePolicy) => void;
}) {
  const isBuy = side === "buy";
  const isLimit = value.ord_type === "limit";
  const pct = Math.round((value.offset_pct || 0) * 1000) / 10; // 0.03 -> 3

  return (
    <div className="rounded-lg border border-gray-200 p-4 space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="font-semibold text-gray-900">{isBuy ? "매수" : "매도"}</h3>
        <span className="text-xs text-gray-400">
          {isLimit
            ? `기준가 ${isBuy ? "−" : "+"}${pct}% 지정가`
            : "시장가 — 즉시 체결, 체결가는 사후 확정"}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {ORD_TYPES.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(sideDefaults(side, { ...value, ord_type: t.key }))}
            className={`px-3 py-1 rounded-full text-xs border ${
              value.ord_type === t.key
                ? "bg-emerald-600 border-emerald-600 text-white"
                : "bg-white border-gray-200 text-gray-600 hover:border-emerald-400"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {isLimit && (
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">기준가</label>
            <div className="flex flex-wrap gap-1.5">
              {BASES.map((b) => (
                <button
                  key={b.key}
                  type="button"
                  title={b.hint}
                  onClick={() => onChange({ ...value, base: b.key })}
                  className={`px-2.5 py-1 rounded-full text-xs border ${
                    value.base === b.key
                      ? "bg-blue-600 border-blue-600 text-white"
                      : "bg-white border-gray-200 text-gray-600 hover:border-blue-400"
                  }`}
                >
                  {b.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-4">
            <label className="block">
              <span className="block text-xs text-gray-500 mb-1">
                {isBuy ? "할인율 (−%)" : "프리미엄 (+%)"}
              </span>
              <input
                type="number"
                step={0.1}
                min={0}
                max={30}
                value={pct}
                onChange={(e) =>
                  onChange({ ...value, offset_pct: (Number(e.target.value) || 0) / 100 })
                }
                className="w-28 px-2 py-1 border border-gray-300 rounded text-sm"
              />
            </label>

            <label className="block">
              <span
                className="block text-xs text-gray-500 mb-1 cursor-help"
                title="이 시각이 지나면 미체결 지정가를 취소합니다. 비우면 장 마감까지 그대로 둡니다."
              >
                미체결 취소 시각 (?)
              </span>
              <input
                type="time"
                value={value.cancel_hhmm ?? ""}
                onChange={(e) =>
                  onChange({ ...value, cancel_hhmm: e.target.value || null })
                }
                className="w-32 px-2 py-1 border border-gray-300 rounded text-sm"
              />
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

/** Editor for one account's order policy.
 *
 * Saving changes how real orders are submitted from the next 09:00 run, so it
 * goes through an explicit confirm step — the same treatment the kill switch
 * gets, and for the same reason.
 */
export default function AccountPolicyForm({ account }: { account: AccountPolicyRow }) {
  const qc = useQueryClient();
  const [buy, setBuy] = useState<AccountSidePolicy>(account.buy);
  const [sell, setSell] = useState<AccountSidePolicy>(account.sell);
  const [confirming, setConfirming] = useState(false);

  // Re-seed when the server sends a newer row (another tab, another operator).
  useEffect(() => {
    setBuy(account.buy);
    setSell(account.sell);
    setConfirming(false);
  }, [account]);

  const dirty = useMemo(
    () =>
      JSON.stringify(buy) !== JSON.stringify(account.buy) ||
      JSON.stringify(sell) !== JSON.stringify(account.sell),
    [buy, sell, account],
  );

  const save = useMutation({
    mutationFn: () =>
      api.updateAccount(account.account_id, {
        label: account.label,
        buy,
        sell,
      }),
    onSuccess: () => {
      setConfirming(false);
      qc.invalidateQueries({ queryKey: ["live-accounts"] });
    },
  });

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 space-y-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-lg font-bold text-gray-900">
          {account.label || account.account_id}
          <span className="ml-2 text-xs font-normal text-gray-400">{account.account_id}</span>
        </h2>
        {account.updated_at && (
          <span className="text-xs text-gray-400">
            수정 {account.updated_at.slice(0, 16).replace("T", " ")}
          </span>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <SideEditor side="buy" value={buy} onChange={setBuy} />
        <SideEditor side="sell" value={sell} onChange={setSell} />
      </div>

      {save.isError && (
        <p className="text-sm text-red-600">{(save.error as Error).message}</p>
      )}

      {confirming ? (
        <div className="flex flex-wrap items-center gap-2 rounded bg-amber-50 border border-amber-200 p-3">
          <span className="text-sm text-amber-900">
            다음 주문부터 이 방식으로 나갑니다 (기본 계좌 09:00 · 카페 계좌 15:28). 해당 계좌의 성과 연속성은 여기서
            끊깁니다 — 저장할까요?
          </span>
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="px-3 py-1 rounded bg-amber-600 text-white text-sm font-medium hover:bg-amber-700 disabled:opacity-50"
          >
            저장
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="px-3 py-1 rounded border border-gray-300 text-sm text-gray-600 hover:bg-gray-50"
          >
            취소
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setConfirming(true)}
            disabled={!dirty}
            className="px-3 py-1 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-40"
          >
            변경 저장
          </button>
          {!dirty && <span className="text-xs text-gray-400">변경 사항 없음</span>}
        </div>
      )}
    </section>
  );
}
