# Design: scenario-matrix — 모델 학습 안정화 + N-시나리오 프레임워크

| 항목 | 값 |
|------|-----|
| Feature | scenario-matrix |
| PDCA Phase | Design |
| Status | Confirmed (진단 실측 기반) |
| Created | 2026-07-29 |
| Plan | `docs/01-plan/features/scenario-matrix.plan.md` |

## 0. 진단 결과 (2026-07-29 프로덕션 실측 — 설계의 근거)

`scripts/diagnose_training_stability.py` — 최근 4거래일 × 변형별 재학습:

| 변형 | best_iteration (7/24·27·28·29) | top10 고유값 | 판정 |
|------|-------------------------------|-------------|------|
| baseline (현행) | 10 · **1** · 8 · **1** | 10 · 3 · 8 · **1** | 4일 중 2일 퇴화 |
| es200 | baseline과 완전 동일 | 동일 | 인내 단독 무효 |
| valid6m | 1 · 1 · 1 · 4 | 2 · 3 · 1 · 2 | 역효과 |
| **lr005_es200** | **105 · 63 · 148 · 216** | **10 · 8 · 10 · 9** | ✅ 전일 건강 |

**결론**: 퇴화 원인은 기본 학습률(0.1)의 과속 수렴 — 첫 트리가 신호를 소진해 검증 개선이
멈추고, 노이즈 큰 날은 best_iteration=1(상수 예측)로 종료. `learning_rate=0.005 +
early_stopping_rounds=200`이 전일 안정 (fit ~11초, num_boost_round 1000 내 수렴).
※ linear 대조군은 스크립트 세그먼트 이슈로 미측정 — Phase B에서 profile로 재시도(참고용).

## 1. Phase A — 학습 안정화 (즉시 적용)

### A-1. `LIVE_CONFIG.model_kwargs` 변경 — `app/api/services/live_trader.py`
```python
"model_kwargs": {"learning_rate": 0.005, "early_stopping_rounds": 200},
```
(num_boost_round 기본 1000 유지 — 실측 최대 216에서 수렴)

### A-2. 퇴화 감지 가드 — `generate_daily_signal`
- 픽 저장 직전: `top10 고유 점수 < 5` 이면 `log.warning("degenerate signal ...")` +
  결과 dict에 `"degenerate": true` (저장은 함 — 기록 자체는 사실이므로. 주문 스킵은 하지 않음:
  모의계좌 단계에선 관찰 가치가 더 큼. Phase 3 알림 도입 시 이 플래그로 통지).

### A-3. 검증
- 배포 후 신호 재발화 → as_of=7/30 신호가 분화(top1 ≈ 0.21 수준)로 재생성되는지 확인.
- 이후 5거래일 연속 top10 고유값 ≥ 5 모니터(Plan 목표 2.1) — Check 단계 판정 기준.

## 2. Phase B — 시나리오 프레임워크

### B-1. 데이터 모델 — `app/api/db/models.py`
```python
class Scenario(Base):
    key(String32 uq) name enabled(bool) broker(String16: sim|kis_paper|toss_real)
    seed_cash(Float) signal_profile(String32) params_json(Text) created_at
```
- `Signal.profile = Column(String(32), default="default", index=True)` 추가
- 기존 strategy 문자열 컬럼들은 그대로 시나리오 key를 담음 (String(8)→32 확장, SQLite 무마이그레이션)
- 프로덕션 ALTER: `signals ADD COLUMN profile TEXT DEFAULT 'default'` (additive)

### B-2. 선언적 설정 — `app/api/services/scenario_config.py` (신규)
```python
PROFILES = {
  "default": {model_kwargs: A-1 값, handler: Alpha158, instruments: kospi200, top_n_store: 30},
  # 추후: "linear", "lgbm_fast" 등
}
SCENARIOS = [
  {key:"open",       broker:"kis_paper", profile:"default", topk:10, n_drop:2},   # 기존 실주문
  {key:"close",      broker:"sim",       profile:"default", topk:10, n_drop:2},   # 기존 종가 시뮬
  {key:"sim_topk5",  broker:"sim", profile:"default", topk:5,  n_drop:2},
  {key:"sim_topk15", broker:"sim", profile:"default", topk:15, n_drop:3},
  {key:"sim_weekly", broker:"sim", profile:"default", topk:10, n_drop:2, rebalance_days:5},
]
def sync_scenarios(db): idempotent upsert (worker/api boot 시 호출)
```
- **profile당 top-30 랭크 저장** → topk 5/10/15가 동일 신호 공유 (학습 1회)

### B-3. 실행 경로 일반화 — `live_trader.py`, `tasks.py`, `celery_app.py`
- `generate_daily_signal(profile="default")`: PROFILES에서 설정 로드, Signal에 profile 태깅,
  top-30 저장. `live_signal_task`: enabled profile 직렬 루프(try/except per profile).
- `submit_daily_orders(scenario)`: scenario.params로 topk/n_drop/rebalance_days,
  rank ≤ topk 슬라이스, `_seed_for`→scenario.seed_cash. 기존 simulated 경로 재사용.
- `sync_account(scenario)`: broker 분기 (sim→`_simulated_balance(key)`, kis_paper→KIS).
- beat: `live_orders_close`/`live_sync_close` → `live_scenarios_run`(15:20)/`live_scenarios_sync`(15:40)
  — sim 시나리오 직렬 루프. open(09:00)은 기존 유지.
- `app/api/db/session.py`: connect 이벤트에 `PRAGMA journal_mode=WAL; busy_timeout=5000`.

### B-4. 하위 호환
- 기존 open/close 데이터는 시나리오 key 그대로라 이력 연속. `_seed_for` 폴백 유지.

## 3. Phase C — 리더보드

- `GET /live/scenarios`: 시나리오별 {누적수익률(시드 대비), MDD, 실현손익 합, 운영일수,
  최근 동기화}. daily_pnl/position_snapshots 집계.
- `app/frontend/src/app/live/scenarios/page.tsx`: 정렬 테이블 + 멀티라인 에쿼티 차트
  (EquityChart를 시나리오 키 배열로 일반화).

## 4. 구현 순서 (Do 단계)

1. A-1 + A-2 (모델 안정화 — 최우선, 단독 배포 가능)
2. 신호 재발화로 7/30 신호 교체 → 분화 확인
3. B-1~B-4 (프레임워크) — 로컬 검증(prod sqlite 사본) 후 배포
4. C (리더보드)
5. 5거래일 안정성 관찰 → `/pdca analyze scenario-matrix`

## 5. 검증 (Check 기준)

- [ ] 5거래일 연속: 신호 top10 고유값 ≥ 5, best_iteration ≥ 10
- [ ] sim 시나리오 4개+ 가 일일 snapshot/pnl 각 1행 생성, 이력 무손상
- [ ] 리더보드에 시나리오별 곡선 분화 표시
- [ ] 15:45 창 전체 소요(profile 학습 포함) < 30분

## 6. 리스크·주의

- lr 하향으로 점수 절대값이 작아짐(0.09~0.21) — 순위 기반이라 영향 없음, UI 표기만 유의
- 진단은 최근 4일 기준 — 5거래일 관찰 게이트로 과적합 여부 재확인
- 배포 정책: **커밋+푸시만, 배포는 Jenkins** (2026-07-29 사용자 지시)

## 다음 단계

`/pdca do scenario-matrix` — 구현 순서 1~4 실행.
