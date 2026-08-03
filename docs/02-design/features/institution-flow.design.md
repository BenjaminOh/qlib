# Design: institution-flow — 기관·외국인 수급 추종 오버레이

| 항목 | 값 |
|------|-----|
| Feature | institution-flow |
| PDCA Phase | Design → Do (구현 완료, 프로덕션 실측 대기) |
| Created | 2026-08-03 |
| Plan | `docs/01-plan/features/institution-flow.plan.md` |

## 1. 데이터 수집

### 1.1 소스 — KIS TR `FHKST01010900`

`GET /uapi/domestic-stock/v1/quotations/inquire-investor`
params: `FID_COND_MRKT_DIV_CODE=J`, `FID_INPUT_ISCD=<6자리>`

출력(`output[]`) 중 사용 필드:

| 필드 | 의미 |
|------|------|
| `stck_bsop_date` | 영업일자 (YYYYMMDD) |
| `orgn_ntby_qty` | 기관계 순매수 수량 |
| `frgn_ntby_qty` | 외국인 순매수 수량 (외국인 + 기타외국인) |
| `prsn_ntby_qty` | 개인 순매수 수량 |
| `*_ntby_tr_pbmn` | 각 주체 순매수 거래대금 |

구현: `KISClient.get_investor_daily(code)` — `_gate()` 경유, mock/실패 시 `[]`.

> **금액이 아니라 수량으로 점수를 낸다.** 예제 스펙에 거래대금 단위(원/백만원)가 명시돼
> 있지 않아, 단위가 확실한 **수량을 qlib의 `$volume`으로 정규화**한다. 금액은 향후 분석용으로
> 원본 그대로 저장만 한다.

### 1.2 저장 — `market_flow` 테이블

`(trade_date, code)` 유니크. `frgn/orgn/prsn × qty/amt` 6개 컬럼.
호출 1회가 약 30일치를 주므로 한 번 적재로 lookback 창 전체가 채워지고,
테이블은 운영일수만큼 자연 누적된다. additive 테이블이라 `init_db()`가 생성 — 마이그레이션 없음.

### 1.3 타이밍 (룩어헤드 차단)

```
T일 15:45  refresh_kr_data
     15:50  live_signal            → as_of=T+1 top-30 저장
     (chain) fetch_market_flow     → 그 30개 코드 적재 (KIS는 당일 행을 장 종료 후 공개)
     18:10  fetch_market_flow      → beat 폴백 (idempotent)
T+1  15:22  live_orders_flow       → **T일까지의** 수급으로 재랭킹 → T+1 종가 진입
     15:42  live_sync_flow
     (refresh 체인) close_bracket_exits → close·flow 각각 ±5% 브래킷 청산
```

close 슬롯(15:20/15:40)에서 1~2분 어긋나게 배치했다. 프로덕션은 SQLite +
`--concurrency=2`라 같은 분에 세 번째 writer를 얹으면 "database is locked"가 시작된다.
두 전략 모두 **저장된 동일한 종가**로 체결하므로 이 오프셋은 비교에 영향이 없다.
같은 이유로 `session.py`에 SQLite `journal_mode=WAL` + `busy_timeout=5000`을 추가했다
(scenario-matrix 설계 B-3에 이미 계획돼 있던 항목).

`_apply_flow_overlay`는 `as_of_flow = _prev_trading_day(today)`를 쓴다 —
주문 시각(15:20)에 KIS가 당일 행을 아직 주지 않으므로 T-1이 사실상 최신이고,
이 선택이 룩어헤드를 구조적으로 차단한다. 누락 시 주문 시점 온디맨드 재적재(self-healing).

## 2. 점수와 재랭킹 — `app/api/services/market_flow.py`

```python
FLOW_CONFIG = {"lookback_days": 5, "w_inst": 0.6, "w_frgn": 0.4,
               "blend": 0.5, "require_positive": True, "min_flow_days": 3}
```

```
inst_ratio = Σ기관 순매수수량 / Σ거래량      # 같은 5거래일 창
frgn_ratio = Σ외국인 순매수수량 / Σ거래량
score      = 0.6·inst_ratio + 0.4·frgn_ratio
final      = (1-blend)·model_rank_pct + blend·flow_rank_pct   # 낮을수록 상위
```

- **거래량 정규화가 핵심** — 삼성전자 1만주와 중소형주 1만주는 같은 신호가 아니다.
- **기관 가중이 더 높은 이유** — 외국인 순매수는 프로그램·ETF 유입에 희석되는 반면
  기관계는 상대적으로 재량적 매수 비중이 크다.
- **`min_flow_days` 미만은 점수 자체를 만들지 않는다** — 모르는 것을 음수처럼 취급하면
  데이터 결손이 매도 신호로 둔갑한다.
- **`require_positive`** — 양 주체 합산 순매도 종목 제외, 차순위가 슬롯 승계
  (`_select_affordable_buys`의 고가주·위험종목 스킵과 동일 패턴).
- **퍼센타일 랭크로 블렌딩** — 후보 수가 변해도 blend 가중치의 의미가 고정된다.

### 폴백 계층 (전부 "close와 동일하게 동작")

| 상황 | 결과 |
|------|------|
| 수급 행 전무 | `applied=False, reason=no_flow_data` → 모델 순위 그대로 |
| 전 종목 순매도 | `applied=False, reason=all_negative` → 모델 순위 그대로 |
| 일부 종목만 데이터 있음 | 없는 종목은 flow축 중앙값(0.5)에 놓여 가감점 없음 |
| 오버레이 예외 | try/except → 모델 순위 + `flow.applied=False`에 사유 기록 |

## 3. 실행 경로

- `STRATEGY_FLOW = "flow"` (strategy 컬럼 `String(8)`에 그대로 적재 — 마이그레이션 없음)
- `BRACKET_STRATEGIES = (close, flow)` — 랭크 이탈 매도 없음, ±5% 브래킷만
- `submit_daily_orders(strategy="flow")`: `rank <= SIGNAL_STORE_TOP_N`(30)으로 후보를 넓게
  읽고 → `_apply_flow_overlay` → top-K 슬라이스 → **기존 시뮬 경로 그대로**
- `evaluate_bracket_exits(strategy=...)`로 일반화, `close_bracket_exits_task`가 두 전략 순회
- `_buy_reasons(flow=...)`: 대시보드에 `"기관 5일 +12.3만주(거래량 +4.1%) · 외국인 …"` 노출
- seed cash는 `close`와 동일한 1천만 — **A/B 성립 조건**

## 4. 검증

- 단위 20종 (`tests/app/test_market_flow.py`): 정규화, 룩어헤드 배제, 결손 처리,
  드롭 vs 승격, blend 0/1 경계, ensure_flow_data 멱등·부분실패, 전략 격리 브래킷,
  그리고 **데이터 없을 때 close와 동일 픽**.
- 프로덕션 실측 대기 항목:
  1. 모의계좌에서 FHKST01010900이 실제로 행을 주는가
     (모의환경이 조회 TR에 빈 응답을 준 전례 있음 — `inquire-daily-ccld`, 2026-07-30)
  2. `market_flow` 행 수 ≈ 30/일
  3. `/live` 차트 3번째 선 분화

## 5. 리스크

- **모의계좌 빈 응답 가능성** — 그 경우 flow는 close와 동일 픽으로 폴백하고 로그에 남는다.
  사고가 아니라 관찰 결과이며, 그때 실전 시세 계정 또는 KRX 계정을 재검토한다.
- **수급 알파가 약할 수 있음** — 외국인 순매수는 모멘텀과 상관이 높다. 그래서 실계좌가 아닌
  시뮬 A/B로 먼저 잰다.
- **KIS 콜 예산** — 15:50대에 약 36초 추가 점유. 09:00 주문 창과 무관하며 실패해도 주문 무영향.
- 배포 정책: **커밋+푸시만, 배포는 Jenkins**. 푸시 금지창 08:50~09:40 / 14:30~16:35.

## 다음 단계

프로덕션 1거래일 관찰 → 10거래일 후 `/pdca analyze institution-flow`
