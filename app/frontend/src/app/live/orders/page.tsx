"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import OrdersTable from "@/components/OrdersTable";

type Filter = "ALL" | "BUY" | "SELL" | "REJECTED";

/** Strategy filter chips — "" = 실계좌+숨김 규칙(기본), others = that strategy only. */
const STRATEGY_TABS: { key: string; label: string }[] = [
  { key: "", label: "기본(실계좌)" },
  { key: "open", label: "실계좌 open" },
  { key: "close", label: "종가" },
  { key: "flow", label: "수급" },
  { key: "trail", label: "트레일" },
  { key: "scale", label: "사다리" },
  { key: "limit", label: "지정가" },
  { key: "cafe", label: "☕ 카페" },
];

export default function LiveOrdersPage() {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [limit, setLimit] = useState(200);
  const [includeSim, setIncludeSim] = useState(false);
  const [strategy, setStrategy] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["live-orders-all", limit, includeSim, strategy],
    queryFn: () => api.getLiveOrders(limit, includeSim, strategy || undefined),
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

      {/* Strategy tabs — sim strategies auto-include their SIMULATED rows */}
      <div className="flex flex-wrap gap-1.5">
        {STRATEGY_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setStrategy(t.key)}
            className={`px-2.5 py-1 rounded-full text-xs border ${
              strategy === t.key
                ? "bg-emerald-600 border-emerald-600 text-white"
                : "bg-white border-gray-200 text-gray-600 hover:border-emerald-400"
            }`}
          >
            {t.label}
          </button>
        ))}
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
        {!strategy && (
          <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer"
                 title="시뮬 전략(종가·수급·트레일·사다리·지정가·카페) 주문 전부 포함 — 기본 숨김, 데이터는 보존됩니다">
            <input
              type="checkbox"
              checked={includeSim}
              onChange={(e) => setIncludeSim(e.target.checked)}
            />
            시뮬 전체 포함
          </label>
        )}
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
        <strong>체결</strong> = 실체결가 확정 · <strong>시뮬</strong> = 시뮬레이션 체결 ·
        배지에 마우스를 올리면 상세 설명이 보입니다.
      </p>

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="text-center text-gray-400 py-8 text-sm">Loading...</div>
        ) : (
          <OrdersTable orders={filtered} showStrategy={includeSim || !!strategy} />
        )}
      </div>
    </div>
  );
}
