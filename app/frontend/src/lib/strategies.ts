/**
 * 전략 식별자 ↔ 화면 라벨.
 *
 * OrdersTable 안에만 살던 STRATEGY_SHORT 를 끌어냈다. 보유 종목 배지가 같은
 * 어휘를 써야 하는데, 두 벌이 되면 한쪽만 고쳐진 채로 "카페실계좌"와
 * "cafereal"이 같은 화면에 동시에 뜨게 된다.
 *
 * 백엔드 상수는 app/api/db/models.py (Order.strategy 는 String(8) — 8자 초과
 * 전략명은 애초에 저장되지 않는다).
 */
export const STRATEGY_SHORT: Record<string, string> = {
  open: "실계좌", close: "종가", flow: "수급", trail: "트레일",
  scale: "사다리", limit: "지정가", cafe: "카페", surge: "급등",
  cafeopen: "카페익일", cafecool: "카페냉각", cafereal: "카페실계좌",
  // 의사 전략 — 원장으로 설명되지 않는 수량. 전략이 아니라 "전략을 모른다"는 표시.
  manual: "수동/미상",
};

/** holding_attribution.MANUAL 과 같은 문자열이어야 한다. */
export const MANUAL_STRATEGY = "manual";

/** 계좌 → 그 계좌의 대표 전략. 백엔드 ACCOUNT_STRATEGIES[account][0] 과 일치. */
export const PRIMARY_STRATEGY: Record<string, string> = {
  main: "open",
  cafe: "cafereal",
};

export function strategyLabel(strategy: string): string {
  return STRATEGY_SHORT[strategy] ?? strategy;
}
