# INSIGHTS — 일일 점검 에이전트 누적 학습

> **이 파일의 목적**: 매일 16:40 점검 에이전트가 세션을 시작할 때 **가장 먼저 읽는 지식 베이스.**
> 일일 보고서(`YYYY-MM-DD.daily.md`)는 그날의 사실 기록, 이 파일은 날짜를 관통하는 **지속 지식**만 담는다.
> 갱신 규칙: 새 교훈은 날짜와 함께 추가, 반증된 항목은 삭제하지 말고 ~~취소선~~+반증 근거 기록.

## 1. 시스템 운영 지식 (검증됨)

- **[2026-07-29] 배포는 push→Jenkins 전용.** 수동 deploy.sh 금지(사용자 지시). 웹훅이 간헐 유실됨
  (7/29 2회) — push 후 2~3분 내 `/home/qlib/deploy.log`에 "배포 시작" 확인, 미반응이면 사실만 보고.
  빌드 소요 ~30-45분. 거래 크론 창(08:50-09:40, 15:10-16:30 KST) 중 push 금지.
- **[2026-07-29] KIS 토큰**: 발급 1분 1회 제한. redis 캐시 공유 + 경합 시 캐시 재확인 로직 있음
  (`kis_client._ensure_token`). 배포 시 redis가 재생성돼 캐시 소실 → 다음 첫 호출이 재발급.
- **[2026-07-29] KIS 주문 스로틀**: 1거래/초 (주문 1건 = hashkey+주문 2콜). 코드 간격 1.2초.
- **[2026-07-29] KIS 예수금은 D+2 정산** — 매수 당일 cash가 안 줄어 보임. 정산 전 평가는
  total_eval 기준으로 볼 것.
- **[2026-07-28] kr_data 스토어**: 증분 갱신은 수정주가 이음새를 누적(배당락/분할 소급조정).
  대규모 갭은 증분 백필 금지 — 전체 재수집→rebuild 디렉터리→검증→스왑. 월 1회 전체 재구축 권장(미구현).
- **[2026-07-22~29] 결함 7건 이력**: `docs/03-check/features/kr-data-refresh.check.md` 참조.
  요지: instruments 클로버·토큰 경합·int 종목코드·피처 동결·mlflow 드리프트·수정주가 이음새·
  instruments 선연장 순서 결함 — 전부 수정 배포됨.

- **[2026-07-29] 서버 진단 시 `/var/lib/docker` 전체 du 금지** — 오버레이 수백만 파일 스캔이
  1시간 I/O를 점유해 진행 중 빌드를 심각하게 지연시킴(자책 사례). `docker system df`/`docker buildx du`로 대체.
- **[2026-07-29] 빌드 30-40분의 구조 원인**: ① scripts/가 Cython 컴파일 레이어에 결합(7f9e1ec2로 분리)
  ② 디스크 83%에서 buildkit 캐시 evict ③ 이미지 export I/O. 캐시 42GB 정리로 68%까지 회복.
  주기 점검 항목: 디스크 75% 초과 시 `docker buildx prune --keep-storage 8gb` 제안할 것.

## 2. 모델·신호 지식

- **[2026-07-29] 학습 퇴화 원인 확정**: 기본 학습률(0.1)이 노이즈 큰 날 트리 1개로 조기 수렴 →
  전 종목 동일 점수 → 픽이 코드순. **lr=0.005 + early_stopping=200 채택** (4/4일 안정,
  best_iteration 63~216). 진단: `scripts/diagnose_training_stability.py`,
  실험표: `docs/02-design/features/scenario-matrix.design.md` §0.
- **[관찰 목표 — 진행 중]** lr=0.005 적용 후 **5거래일 연속 top10 고유값 ≥ 5** 확인이
  scenario-matrix Check 게이트. 매일 보고서에 `unique_scores`(신호 태스크 결과에 포함됨) 기록할 것.
- **[열린 질문] 이탈 매도의 성격**: top-10 이탈 매도가 익절형인지 손절형인지 — 매도 발생 시
  `orders.reasons_json`(sell 스냅샷)과 `fills.pnl` 축적 후 분포 분석 (2~3주 데이터 필요).
- **[2026-07-30 실측] top-10 일간 회전율 = 100%** (7/29→7/30 생존 0/10) — 단기(1일) 예측 모델
  특성상 순위가 매일 갈아엎어짐. n_drop=2가 유일한 완충. 첫 회전에서 -12.8만 실현손실.
- **[가설 — Phase 2 시나리오 후보] 히스테리시스 매도**: 진입 top-10 / 퇴출 top-20 밖 —
  경계 종목의 회전·수수료 절감 기대. `sim_weekly`(주간 리밸런싱)와 함께 검증할 것.
  매일 top-30 저장(2241b125)이 이 실험의 데이터 기반.

## 3. 점검 체크리스트 (매일 확인할 것)

1. 09:00 `live_orders` 결과 — submitted/rejected/skipped_expensive (worker 로그 + orders 테이블)
2. 15:20 `live_orders_close` (close 시뮬) 동일 확인
3. 15:40 `live_sync` — snapshot/pnl 기록 여부
4. 15:45 `refresh_kr_data` — status ok + **bin이 실제 append됐는지** (캘린더 날짜 vs bin 끝 날짜 —
   과거에 캘린더만 늘고 bin은 동결된 사고 2회)
5. 신호 생성 — as_of=익일, picks=10, **unique_scores ≥ 5** (degenerate 플래그 확인)
6. celery 태스크 FAILURE — redis `celery-task-meta-*` (1일 보존)
7. 대시보드 서빙 — web/api 응답 코드

## 4. 접속·명령 요약

- 서버: `ssh rocky-prod` (root). 컨테이너: `qlib_api_{blue|green}`, `qlib_worker_*`, `qlib_scheduler_*`
  (blue/green은 배포마다 교대 — `docker ps`로 활성 확인).
- DB: `docker exec -i <api컨테이너> python - <<EOF ... sqlite3.connect("/app/db/live.sqlite") ...`
- 신호 재발화(저위험 수리): `docker exec <worker> celery -A app.api.workers.celery_app call live_signal`
- 로그: `docker logs <worker> --since ...` (컨테이너 재생성 시 로그 소실 주의 — redis 결과 백엔드 활용)

## 5. 추세 데이터 (에이전트가 매일 한 줄씩 추가)

| 날짜 | unique_scores | best_it(추정) | 주문(제출/거부) | 특이사항 |
|------|--------------|---------------|----------------|----------|
| 07-29 | 1 (구코드 마지막) → 수정 배포 | 1→216(재발화 예정) | 2/1(스로틀)→수동복구 | lr=0.005 배포일 |
