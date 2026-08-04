"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import OrdersTable from "@/components/OrdersTable";

type Filter = "ALL" | "BUY" | "SELL" | "REJECTED";

export default function LiveOrdersPage() {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [limit, setLimit] = useState(200);
  const [includeSim, setIncludeSim] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["live-orders-all", limit, includeSim],
    queryFn: () => api.getLiveOrders(limit, includeSim),
    refetchInterval: 30_000,
  });

  const filtered = useMemo(() => {
    const all = data?.orders || [];
    if (filter === "ALL") return all;
    if (filter === "REJECTED") return all.filter((o) => o.status === "REJECTED");
    return all.filter((o) => o.side === filter);
  }, [data, filter]);

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold text-gray-900">📋 주문 이력</h1>
        <Link href="/live" className="text-blue-600 hover:underline text-sm">
          ← 대시보드
        </Link>
      </div>

      <div className="flex flex-wrap gap-2 items-center">
        {(["ALL", "BUY", "SELL", "REJECTED"] as Filter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded text-sm border ${
              filter === f
                ? "bg-blue-600 border-blue-600 text-white"
                : "bg-white border-gray-200 text-gray-700 hover:bg-gray-50"
            }`}
          >
            {f === "ALL" ? "전체" : f === "BUY" ? "매수만" : f === "SELL" ? "매도만" : "거부만"}
          </button>
        ))}
        <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer"
               title="close 전략(종가 시뮬) A/B 실험 주문 — 기본 숨김, 데이터는 보존됩니다">
          <input
            type="checkbox"
            checked={includeSim}
            onChange={(e) => setIncludeSim(e.target.checked)}
          />
          시뮬(A/B) 포함
        </label>
        <span className="ml-auto text-sm text-gray-500">
          {filtered.length}건 / 전체 {data?.orders.length ?? 0}건
        </span>
        <select
          value={limit}
          onChange={(e) => setLimit(parseInt(e.target.value))}
          className="border rounded px-2 py-1 text-sm"
        >
          <option value={100}>최근 100건</option>
          <option value={200}>최근 200건</option>
          <option value={500}>최근 500건</option>
        </select>
      </div>

      <p className="text-xs text-gray-500 -mt-2">
        상태: <strong>접수</strong> = KIS 접수 완료(시장가 체결가는 09:20 대사에서 확정 — 그 전 가격·손익은 시가 추정*) ·{" "}
        <strong>체결</strong> = 실체결가 확정 · 배지에 마우스를 올리면 상세 설명이 보입니다.
      </p>

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="text-center text-gray-400 py-8 text-sm">Loading...</div>
        ) : (
          <OrdersTable orders={filtered} />
        )}
      </div>
    </div>
  );
}
