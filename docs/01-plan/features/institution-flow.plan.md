# Plan: institution-flow — 기관·외국인 수급 추종 오버레이

| 항목 | 값 |
|------|-----|
| Feature | institution-flow |
| Owner | BenjaminOh |
| Created | 2026-08-03 |
| PDCA Phase | Plan |
| Status | Confirmed (사용자 방향 확정) |
| 상위 로드맵 | `toss-autotrading.roadmap.md` Phase 2 |
| 연관 | `scenario-matrix.plan.md` (프레임워크 미구현 상태에서 독립 진행) |

## 1. 배경 / 문제

현재 추천 파이프라인이 보는 정보는 **OHLCV 하나뿐**이다.
`live_trader.generate_daily_signal()` → Alpha158(가격·거래량 파생 158개) → LGBM →
KOSPI200 스코어링 → top-30 저장. 즉 **"누가 사고 있는가"(투자주체별 수급)는 모델이 구조적으로
볼 수 없는 축**이다.

사용자 제안(2026-08-03): 다음 거래일 추천에 기관·외국인 순매수 종목 추종을 반영.

### 1.1 데이터 확보 실측 결과

| 소스 | 판정 | 근거 |
|------|------|------|
| pykrx | ❌ 불가 | 1.2.8부터 투자자별 매매동향 계열 전체가 KRX 로그인(`KRX_ID`/`KRX_PW`) 요구. 실측: `get_market_ohlcv`는 정상, `get_market_net_purchases_of_equities`·`get_market_trading_value_by_date`·`get_market_trading_value_by_investor`는 `KRX 로그인 실패` 후 빈 DataFrame |
| **KIS `inquire-investor` (FHKST01010900)** | ✅ 채택 | 공식 예제에 **모의투자(demo) 지원 명시**, TR ID 실전 동일. 호출 1회당 해당 종목 **일별 개인/외국인/기관계 순매수 수량·거래대금 약 30일치** 반환 |
| KRX OpenAPI / 네이버 스크래핑 | 보류 | 전자는 계정·인증키 필요, 후자는 취약 |

KIS 채택의 실질 이점: 신규 파이썬 의존성 0, 스크래핑 취약성 0, 외부 계정 0,
기존 `KISClient`의 토큰 캐시·계좌 단위 콜 게이트·재시도 재사용. 후보 top-30만 조회하므로
하루 30콜(paper 게이트 1.2s → 약 36초).

## 2. 목표

1. 기존 `open`(실주문)·`close`(종가 시뮬)와 **나란히 도는 세 번째 시뮬 전략 `flow`**.
   실행 방식은 `close`와 완전히 동일(종가 매수 + ±5% 브래킷 청산),
   **차이는 종목 선정 하나뿐** → 에쿼티 곡선 차이 = 수급 오버레이의 효과.
2. 판정: 10거래일 후 `close` 대비 누적수익률·MDD·회전율 비교.
3. 데이터 실패가 주문을 절대 막지 않을 것 — 수급이 없는 날의 `flow`는 곧 `close`.

## 3. 비목표 (Non-goals)

- 실계좌(`open`, 09:00) 주문 경로 변경 — 무변경
- 모델 재학습·kr_data 스토어 스키마 변경
  (신규 필드는 `dump_bin` UPDATE_MODE로 안전하게 붙지 않아 전체 재구축이 강제됨. 3항 참조)
- 과거 구간 백테스트 (KIS 창이 약 30일이라 불가. 전진 A/B로 판정)
- 기관 세부 주체(연기금·투신·사모) 분해 — `inquire-investor`는 기관계 합계만 제공

## 4. 방식 선택 근거 — 왜 오버레이인가

모델 피처로 넣으려면 `$inst_net`/`$frgn_net`를 kr_data에 추가해야 하는데,
`scripts/dump_bin.py`의 UPDATE_MODE는 **신규 필드를 안전하게 붙이지 못한다**:
`_data_to_bin`이 존재하지 않는 bin을 만들 때 `get_datetime_index`를 증분 캘린더 기준으로
계산하므로 시작 인덱스 0인 손상된 bin이 생성된다. 즉 전체 재구축(dump_all) + 스왑이 강제.

오버레이는 그 비용 없이 며칠 안에 곡선으로 검증되고, 결과가 좋으면 그때 피처로 승격한다.

## 5. 성공 기준 (Check 단계)

- [ ] 프로덕션에서 `fetch_market_flow`가 하루 top-30 코드의 행을 적재
- [ ] `flow` 전략이 매 거래일 snapshot/daily_pnl 각 1행 생성, `close`와 이력 분리
- [ ] `/live` 에쿼티 차트에 3번째 선(amber) 표시
- [ ] 수급 데이터 결손일에 `flow` 픽 = `close` 픽 (폴백 정상)
- [ ] 10거래일 후 close 대비 성과 비교표 작성 → `/pdca analyze institution-flow`

## 다음 단계

`docs/02-design/features/institution-flow.design.md`
