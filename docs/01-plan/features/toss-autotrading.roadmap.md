# 토스증권 자동매매 최종 로드맵

> 2026-07-22 수립·승인. 최종 목표: qlib 분석으로 토스증권 자동매매.
> KIS 모의투자로 최적 시나리오를 찾고, 안정화 후 동결(무개입 운영).

| 항목 | 값 |
|------|-----|
| Owner | BenjaminOh |
| 승인일 | 2026-07-22 |
| 토스 API | 사전신청 완료·키 대기 중 (2026-07 기준). REST+OAuth, 주문 API 존재, **샌드박스 없음** → 실계좌 소액 테스트 필요. 문서: developers.tossinvest.com |

## Phase 1 — 결함 수정 (매매 재가동) ✅ 2026-07-28 완료

7건 수정: ① instruments 클로버 ② KIS 토큰 경합 500 ③ dump int 코드 크래시
④ 피처 append 동결+쓰레기 디렉터리 ⑤ mlflow 드리프트 ⑥ 수정주가 이음새(스토어 재구축)
⑦ instruments 선연장→dump no-op 순서 결함. 상세: `docs/03-check/features/kr-data-refresh.check.md`

## Phase 2 — N-시나리오 실험 프레임워크 (~5-8일, 다음 작업)

open/close A/B를 일반화: `Scenario` 테이블 + git 선언 config(`scenario_config.py`),
signal profile(모델 학습 단위, top-30 랭크 저장)로 topk 5/10/15·리밸런싱 주기·모델 다변화.
가상 포트폴리오 4~8개 병행 → 리더보드(`/live/scenarios`)로 우승 선정. SQLite WAL 필요.
※ 현재 신호 3~10위 동률(플라토) 문제도 여기서 모델 다변화로 해소.

## Phase 3 — 알림·관찰성 (~2-3일, "안 건드리는 시스템"의 전제)

Telegram 봇(`notify.py`) + `task_failure` 훅 + 논리적 실패 알림(no_signal/rejected/refresh skip)
+ 워치독(17:00, 당일 산출물 존재 검사) + 일일 요약 + `/health`에 `kr_data_last_date`.
백로그: **월 1회 kr_data 전체 재구축 크론**(수정주가 이음새 누적 방지), mlflow 버전 고정.

## Phase 4 — 브로커 추상화 + 토스 어댑터

4.1 (키 무관, 즉시 가능): `brokers/base.py` ExecutionBroker ABC + `get_broker()` 팩토리, KIS 이동.
4.2 (키 도착 시): `brokers/toss.py` — OAuth, POST /api/v1/orders, **체결 재조정**(폴링→Fill 기록),
주문 상한 캡(`TOSS_MAX_ORDER_VALUE`). 4.3: `toss_smoke.py` 1주 수동 테스트 → `toss_pilot` 시나리오 1-2주.

## Phase 5 — 우승 시나리오 토스 전환 + 동결 (4-8주 캘린더)

우승 기준: 시뮬 ≥8주 + MDD ≤ 백테스트×1.5 + KODEX200 상회.
`prod` 시나리오 승격(toss_real) + 시뮬 쌍둥이 섀도우(카나리).
동결 게이트: 4주 연속 무알림 + 체결률 ≥95% + 쌍둥이 괴리 ≤1%p → 태그·runbook 작성·동결.

## 운영 규칙 (2026-07 확립)

- **push = Jenkins 자동배포** (수동 deploy.sh 금지, 커밋 모아 1회 push, 거래 크론 창 08:50-09:40/14:30-16:35 KST 회피)
- KIS 모의계좌는 만료·재발급됨 — 재발급 시 `KIS_APP_KEY/SECRET/ACCOUNT_NO`(env 3파일) + `live_seed_cash_open`(가상자금 일치) 갱신
- kr_data 대규모 갭은 증분 백필 금지 → 전체 재수집·rebuild 디렉터리 빌드·검증·스왑
