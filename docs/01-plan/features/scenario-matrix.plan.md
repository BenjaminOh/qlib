# Plan: scenario-matrix — N-시나리오 실험 프레임워크 + 모델 학습 안정화

| 항목 | 값 |
|------|-----|
| Feature | scenario-matrix |
| Owner | BenjaminOh |
| Created | 2026-07-29 |
| PDCA Phase | Plan |
| Status | Draft |
| 상위 로드맵 | `toss-autotrading.roadmap.md` Phase 2 |

## 1. 배경 / 문제

### 1.1 Phase 1 완료 상태
결함 7건 수정 후 2026-07-29 파이프라인이 무인 완주 확인(데이터 갱신→신호 체인→주문→동기화).
상세: `docs/03-check/features/kr-data-refresh.check.md`.

### 1.2 🔴 1순위 문제 — 모델 학습 불안정 (실측)
| 시점 | 학습 데이터 | 결과 |
|------|-----------|------|
| 7/28 저녁 (재구축 직후) | ~7/28 | top-2 분화(0.181/0.055) + 3~10위 동률 플라토 |
| 7/29 저녁 (자동 체인) | ~7/29 | **전 종목 동일 점수(4.5e-05)** — 완전 퇴화 |

- 메커니즘: LightGBM 검증 성능이 개선되지 않아 `best_iteration=1`(사실상 트리 1개) →
  테스트일 전 종목이 같은 리프 → 상수 예측 → 순위가 코드순으로 무의미해짐.
- 데이터는 정상(캘린더·bin·instruments 모두 7/29 정합 확인) — 순수 학습 문제.
- 영향: 이 상태의 날은 매수 선정이 사실상 무작위(코드순) — 실험 전체의 전제가 무너짐.
  **시나리오 비교는 신호가 유의미할 때만 의미가 있으므로 안정화가 선행 조건.**

### 1.3 프레임워크 필요성 (로드맵 Phase 2)
현재 open/close 실행타이밍 A/B뿐. 최적 시나리오 탐색(topk·리밸런싱 주기·모델 다변화)을
위해 가상 포트폴리오 N개 병행 운영 구조가 필요.

## 2. 목표

1. **(1순위) 학습 안정화**: 매일 재학습이 안정적으로 변별력 있는 점수를 생산
   - 판정: 5거래일 연속, 상위 10개 점수의 고유값 ≥ 5개 && best_iteration ≥ 10
2. **시나리오 프레임워크**: 가상 포트폴리오 4~8개 병행(시드 각 1,000만 가상) + 1개 KIS 실주문
3. **리더보드**: 시나리오별 누적수익·MDD 비교 UI → 8주+ 후 우승 선정 근거

## 3. 비목표 (Non-goals)

- 토스 어댑터/브로커 추상화(Phase 4), Telegram 알림(Phase 3 별도)
- 유니버스 확장, 분봉/실시간, 펀더멘털 피처
- 실계좌 자금 투입

## 4. 접근

### Phase A — 학습 안정화 (선행, ~2-3일)
1. **진단 스크립트** `scripts/diagnose_training_stability.py`(신규):
   기준일을 최근 10거래일로 옮겨가며 재학습 → best_iteration / valid l2 / 점수 분산 분포 수집.
2. **개선 후보** (진단 결과로 채택 결정, Design에서 확정):
   - early_stopping_rounds 50→200, num_boost_round 명시, learning_rate 하향
   - 검증 창 3개월→6개월 (짧은 창의 노이즈가 조기 종료 유발 가설)
   - 시드 고정 + k회 학습 점수 평균(미니 앙상블)
   - 라벨 처리 점검: CSZScoreNorm 이상치 클리핑(RobustZScoreNorm 검토)
   - 보험: LinearModel profile 병행(트리 퇴화와 무관한 기저 신호 확보)
3. 안정화 판정 통과 후 Phase B 진행.

### Phase B — 시나리오 프레임워크 (~4-5일)
로드맵 설계 그대로:
- `Scenario` 테이블 + `scenario_config.py`(git 선언, boot 시 upsert)
- **signal profile** = 모델 학습 단위, profile당 **top-30 랭크 저장** → topk 5/10/15가 공유
- `Signal.profile` 컬럼, `submit_daily_orders(scenario)`/`sync_account(scenario)` 일반화
- `live_scenarios_run`(15:20)/`live_scenarios_sync`(15:40)로 시뮬 시나리오 직렬 루프
- SQLite WAL + busy_timeout
- 초기 매트릭스: open(실주문)/close/sim_topk5/sim_topk15/sim_weekly/sim_linear(+안정화 결과에 따라 lgbm_tuned)

### Phase C — 리더보드 (~1-2일)
- `GET /live/scenarios`(누적수익률·MDD·실현손익·운영일수) + `/live/scenarios` 페이지
  (정렬 테이블 + 멀티라인 에쿼티 차트, EquityChart 일반화)

## 5. 위험

| 위험 | 대응 |
|------|------|
| 파라미터 튜닝이 과거 구간 과적합 | 진단은 분포(10일 롤링)로 판단, 단일일 성능 아님. walk-forward 유지 |
| 15:45 창 내 다중 profile 학습 시간 | 직렬 루프 + 다음날 09:00까지 창 충분. 소요시간 로그로 계측 |
| N-시나리오 SQLite 경합 | 태스크 내 직렬화 + WAL. deploy.sh 스냅샷 기존 유지 |
| 상수 신호일의 기존 데이터 오염 | 리더보드 시작 전 안정화 통과 필수(2.1 판정 기준) |

## 6. 열린 질문

1. 안정화 판정 기간(5거래일) 동안 open 전략 실주문을 계속 둘지, 신호 무의미일은 스킵할지
   → Design에서 "상수 신호 감지 시 주문 스킵 가드" 추가 여부 결정
2. sim 시나리오 개수 상한(워커 메모리) — 진단 스크립트로 학습 1회 RAM 측정 후 확정

## 7. 영향 파일

- `app/api/services/live_trader.py` — LIVE_CONFIG→profile화, 시나리오 일반화
- `app/api/db/models.py` — Scenario 테이블, Signal.profile
- `app/api/services/scenario_config.py` (신규)
- `app/api/workers/tasks.py`, `celery_app.py` — 시나리오 태스크
- `app/api/db/session.py` — WAL PRAGMA
- `app/api/routers/live.py`, `app/frontend/src/app/live/scenarios/` (신규)
- `scripts/diagnose_training_stability.py` (신규)

## 8. 다음 단계

`/pdca design scenario-matrix` — Phase A 진단 결과를 반영해 학습 파라미터·시나리오 매트릭스 확정.
