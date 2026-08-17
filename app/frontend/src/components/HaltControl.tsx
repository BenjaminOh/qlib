"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** Trading kill switch.
 *
 * Until this existed the only way to stop order submission was a deploy —
 * minutes away, and itself risky inside a trading window. The flag lives in
 * redis, so engaging it here stops the scheduler and worker too, not just
 * whatever container served this request.
 */
export default function HaltControl() {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const halt = useQuery({
    queryKey: ["trading-halt"],
    queryFn: api.getHalt,
    refetchInterval: 30_000,
  });

  const mutate = useMutation({
    mutationFn: (reason: string | null) => api.setHalt(reason),
    onSuccess: (data) => {
      qc.setQueryData(["trading-halt"], data);
      setConfirming(false);
    },
  });

  const halted = halt.data?.halted ?? false;

  if (halted) {
    return (
      <button
        onClick={() => mutate.mutate(null)}
        disabled={mutate.isPending}
        className="px-2.5 py-1 rounded bg-red-600 text-white text-xs font-medium hover:bg-red-700 disabled:opacity-50"
        title={halt.data?.reason ? `중지 사유: ${halt.data.reason}` : undefined}
      >
        ⛔ 거래 중지됨 — 해제
      </button>
    );
  }

  if (confirming) {
    return (
      <span className="flex items-center gap-1.5">
        <span className="text-xs text-gray-600">모든 주문을 막을까요?</span>
        <button
          onClick={() => mutate.mutate("대시보드에서 수동 중지")}
          disabled={mutate.isPending}
          className="px-2 py-1 rounded bg-red-600 text-white text-xs font-medium hover:bg-red-700 disabled:opacity-50"
        >
          중지
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="px-2 py-1 rounded border border-gray-300 text-xs text-gray-600 hover:bg-gray-50"
        >
          취소
        </button>
      </span>
    );
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      className="px-2.5 py-1 rounded border border-red-300 text-red-700 text-xs font-medium hover:bg-red-50"
    >
      거래 중지
    </button>
  );
}
