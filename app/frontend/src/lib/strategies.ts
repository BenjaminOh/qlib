/**
 * 전략 식별자 ↔ 화면 라벨.
 *
 * 라벨은 **누가 골랐나 + 어떻게 들어갔나**를 말한다. 계좌를 말하지 않는다.
 *
 * 예전에는 open 이 "실계좌", cafereal 이 "카페실계좌" 였다. 둘 다 전략이 아니라
 * **계좌**를 가리키는 이름이라, 보유 종목 배지가 6행 전부 "실계좌"로만 떴다 —
 * 탭에서 이미 "기본 계좌"라고 말해놓고 배지가 같은 말을 반복하면서 정작
 * 물어본 것(어느 전략이 샀나)에는 답하지 않았다. 두 축은 다르다:
 *   계좌 = 어느 증권 계좌인가 (탭 · ACCOUNT_STRATEGIES)
 *   전략 = 누가 종목을 골라 어떤 규칙으로 들어갔나 (이 맵)
 *
 * 접두어가 신호의 출처다. close/flow/trail/scale/limit 은 open 과 **같은 qlib
 * 신호**를 쓰고 진입·청산 규칙만 다르다 — 접두어 없이 "종가"라고만 쓰면 다른
 * 모델이 고른 것처럼 읽힌다.
 *
 * 백엔드 상수는 app/api/db/models.py (Order.strategy 는 String(8) — 8자 초과
 * 전략명은 애초에 저장되지 않는다).
 */
export const STRATEGY_SHORT: Record<string, string> = {
  // qlib 모델(Alpha158 + LGBM TopkDropout)이 고른 것들. 규칙만 다르다.
  open: "qlib 시초가", close: "qlib 종가", flow: "qlib 수급",
  trail: "qlib 트레일", scale: "qlib 사다리", limit: "qlib 지정가",
  // 카페 모사 스크리너가 고른 것들.
  cafe: "카페 시뮬", cafeopen: "카페 익일", cafecool: "카페 냉각",
  cafereal: "카페 실전",
  // 급등 전야 프로파일.
  surge: "급등 전야",
  // 의사 전략 — 원장으로 설명되지 않는 수량. 전략이 아니라 "전략을 모른다"는 표시.
  manual: "수동/미상",
};

/**
 * 전략별 색. 곡선 차트와 주문 배지가 같은 색을 써야 한 화면에서 같은 전략이
 * 두 색으로 보이지 않는다. EquityChart 안에 있던 것을 어휘와 함께 모았다.
 */
export const STRATEGY_COLORS: Record<string, string> = {
  open: "#10b981",   // emerald — qlib 모델, 09:00 시가 실주문
  close: "#6366f1",  // indigo — DB-only simulated portfolio
  flow: "#f59e0b",   // amber — close execution, 수급 재랭킹 픽
  trail: "#0ea5e9",  // sky — trailing −7% exits
  scale: "#a855f7",  // purple — +7% half take, remainder trails
  limit: "#ef4444",  // red — −3% resting-limit entries (사장님 방식)
  cafe: "#78716c",   // stone — recommender-mimic screener
  surge: "#db2777",  // pink — surge-eve profile picks
  cafeopen: "#0d9488", // teal — cafe's picks, next-morning limit entry
  cafecool: "#84cc16", // lime — cafe's picks minus the overheated ones
  cafereal: "#c026d3", // fuchsia — cafe's picks on a REAL second account
};

/**
 * 한 줄 설명 — 배지·범례의 툴팁. 짧은 라벨이 "누가 골랐나"까지만 말하므로,
 * "어떤 규칙으로 사고 파는가"는 여기서 답한다.
 */
export const STRATEGY_LABELS: Record<string, string> = {
  open: "qlib 모델 · 09:00 시가 실주문 (기본 계좌)",
  close: "qlib 모델 · 종가 매수 · 익절 +10%/전저점 손절 (시뮬)",
  flow: "qlib 모델 · 수급 재랭킹 · 브래킷 (시뮬)",
  trail: "qlib 모델 · 트레일링 −7% (시뮬)",
  scale: "qlib 모델 · 사다리 익절 10/15/20% (시뮬)",
  limit: "qlib 모델 · 지정가 −3% 매수 · +10% 예약매도 (시뮬)",
  cafe: "카페 모사 스크리너 · 15:28 종가 베팅 (시뮬)",
  surge: "급등 전야 프로파일 (시뮬)",
  cafeopen: "카페 모사 · 익일 시가 −3% 지정가 (시뮬)",
  cafecool: "카페 모사 · ret20 상한 50% (시뮬)",
  cafereal: "카페 모사 · 실주문 (카페 계좌)",
  manual: "주문 원장에 없는 수량 — 수동 매매·대체입고·액면분할 등",
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
