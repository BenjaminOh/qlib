"use client";

import { HoldingStrategy } from "@/lib/api";
import { MANUAL_STRATEGY, strategyLabel } from "@/lib/strategies";

/**
 * 보유 종목이 어느 전략에서 왔는지 표시하는 배지.
 *
 * KIS 잔고에는 전략 정보가 없어 주문 원장에서 파생한다. 그래서 **확정과 추정을
 * 반드시 구분해서** 그린다 — 추정치를 확정처럼 보여주는 것이 이 기능의 가장 큰
 * 실패 모드다. 미확정(점선)은 대개 아직 정산되지 않은 지정가 주문이고,
 * "수동/미상"은 원장이 설명하지 못한 수량이다.
 */
export default function StrategyBadge({
  lot,
  showQty = false,
}: {
  lot: HoldingStrategy;
  showQty?: boolean;
}) {
  const isManual = lot.strategy === MANUAL_STRATEGY;
  const tone = isManual
    ? "border-gray-300 text-gray-500 bg-gray-50"
    : lot.confirmed
      ? "border-emerald-200 text-emerald-700 bg-emerald-50"
      : "border-amber-300 text-amber-700 bg-amber-50";
  const title = isManual
    ? "주문 원장으로 설명되지 않는 수량입니다 — 수동 매매, 대체입고, 액면분할 등"
    : lot.confirmed
      ? `${strategyLabel(lot.strategy)} 전략의 체결 기록과 일치합니다`
      : `${strategyLabel(lot.strategy)} 전략의 주문은 있으나 체결 정산 기록이 없습니다 — 추정치 (원장 ${lot.ledger_qty.toLocaleString()}주)`;

  return (
    <span
      title={title}
      className={`inline-flex items-baseline gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium align-middle whitespace-nowrap border ${
        lot.confirmed ? "" : "border-dashed"
      } ${tone}`}
    >
      {strategyLabel(lot.strategy)}
      {showQty && <span className="font-mono">{lot.qty.toLocaleString()}</span>}
      {!lot.confirmed && !isManual && <span aria-hidden>추정</span>}
    </span>
  );
}

/** 한 종목의 배지 묶음. 로트가 2개 이상일 때만 수량을 병기한다. */
export function StrategyBadges({
  lots,
  className = "",
}: {
  lots?: HoldingStrategy[] | null;
  className?: string;
}) {
  if (!lots || lots.length === 0) return null;
  return (
    <span className={`inline-flex flex-wrap gap-1 ${className}`}>
      {lots.map((lot) => (
        <StrategyBadge key={lot.strategy} lot={lot} showQty={lots.length > 1} />
      ))}
    </span>
  );
}
