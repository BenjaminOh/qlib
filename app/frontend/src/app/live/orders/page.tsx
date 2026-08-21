"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import OrdersTable from "@/components/OrdersTable";
import { strategyTabs } from "@/lib/strategies";

type Filter = "ALL" | "BUY" | "SELL" | "REJECTED";

/** Strategy filter chips — "" = every strategy, others = that strategy only.
 *
 * 목록을 손으로 적어두었더니 cafereal·cafecool·cafeopen 이 빠졌다. 카페 실매매
 * 주문이 「전체 전략」에 섞여서만 보이고 골라낼 방법이 없었다는 뜻이다.
 * 이제 전략 카탈로그에서 만든다 — 전략을 추가하면 칩도 따라온다. */
const STRATEGY_TABS: { key: string; label: string }[] = [
  { key: "", label: "전체 전략" },
  ...strategyTabs(),
];

type View = "all" | "real" | "sim";

const VIEW_TABS: { key: View; label: string; title: string }[] = [
  { key: "all", label: "전체", title: "실거래 + 시뮬레이션 전부" },
  { key: "real", label: "테스트(실거래)", title: "KIS 모의계좌에 실제 접수된 주문만" },
  { key: "sim", label: "시뮬레이션", title: "시뮬 전략들의 가상 체결만 (실주문 아님)" },
];

export default function LiveOrdersPage() {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [limit, setLimit] = useState(200);
  const [view, setView] = useState<View>("all");
  const [strategy, setStrategy] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["live-orders-all", limit, view, strategy],
    queryFn: () => api.getLiveOrders(limit, view, strategy || undefined),
    refetchInterval: 30_000,
  });

  // Picking a strategy auto-switches the ledger so the list is never
  // empty-by-mismatch (open lives in 실거래, the rest are sim-only).
  const pickStrategy = (key: string) => {
    setStrategy(key);
    if (key === "open") setView("real");
    else if (key) setView("sim");
    else setView("all");
  };

  const filtered = useMemo(() => {
    const all = data?.orders || [];
    if (filter === "ALL") return all;
    if (filter === "REJECTED") return all.filter((o) => o.status === "REJECTED");
    return all.filter((o) => o.side === filter);
  }, [data, filter]);

  // 지정가 전략만: 예약가 대비 실제로 얼마나 싸게 담겼는지. 갭하락 시가에
  // 체결된 건은 설정 할인율보다 깊게 찍히므로 그 건수를 따로 센다.
  const limitStats = useMemo(() => {
    if (strategy !== "limit") return null;
    const ds = filtered
      .map((o) => o.discount_pct)
      .filter((d): d is number => d != null);
    if (!ds.length) return null;
    const nominal = -(data?.limit_discount ?? 0.03) * 100;
    return {
      n: ds.length,
      avg: ds.reduce((a, b) => a + b, 0) / ds.length,
      deepest: Math.min(...ds),
      // 0.2%p slack absorbs tick rounding on the resting price.
      gapped: ds.filter((d) => d < nominal - 0.2).length,
      nominal,
    };
  }, [filtered, strategy, data]);

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold text-gray-900">📋 주문 이력</h1>
        <Link href="/live" className="text-blue-600 hover:underline text-sm">
          ← 대시보드
        </Link>
      </div>

      {/* Ledger selector — 전체 / 테스트(실거래) / 시뮬레이션 */}
      <div className="flex flex-wrap gap-1.5 items-center">
        <span className="text-xs text-gray-400 mr-1">구분</span>
        {VIEW_TABS.map((v) => (
          <button
            key={v.key}
            type="button"
            title={v.title}
            onClick={() => setView(v.key)}
            className={`px-2.5 py-1 rounded-full text-xs border ${
              view === v.key
                ? "bg-violet-600 border-violet-600 text-white"
                : "bg-white border-gray-200 text-gray-600 hover:border-violet-400"
            }`}
          >
            {v.label}
          </button>
        ))}
      </div>

      {/* Strategy tabs — selecting one flips the ledger to where it lives */}
      <div className="flex flex-wrap gap-1.5 items-center">
        <span className="text-xs text-gray-400 mr-1">전략</span>
        {STRATEGY_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => pickStrategy(t.key)}
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

      {limitStats && (
        <p className="text-xs text-gray-600 bg-blue-50 border border-blue-100 rounded px-3 py-2 -mt-2">
          지정가 체결 <strong>{limitStats.n}건</strong> · 평균 할인{" "}
          <strong>{limitStats.avg.toFixed(1)}%</strong> · 최대 할인{" "}
          <strong>{limitStats.deepest.toFixed(1)}%</strong> · 갭하락으로 예약가(
          {limitStats.nominal.toFixed(1)}%)보다 깊게 체결{" "}
          <strong>{limitStats.gapped}건</strong>
        </p>
      )}

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="text-center text-gray-400 py-8 text-sm">Loading...</div>
        ) : (
          <OrdersTable orders={filtered} showStrategy={view !== "real" || !!strategy} />
        )}
      </div>
    </div>
  );
}
