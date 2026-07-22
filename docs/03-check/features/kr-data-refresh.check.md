# kr-data-refresh — Check(진단) 리포트

| 항목 | 값 |
|------|-----|
| Feature | kr-data-refresh (범위 확장: 라이브 매매 파이프라인 전반) |
| PDCA Phase | Check |
| 진단 일시 | 2026-07-22 (KST) |
| 진단 방법 | 프로덕션 서버 **읽기 전용 실측** (`rocky-monitor`, claude-ro) — 컨테이너 목록 + 워커/스케줄러 로그 |
| 진단 대상 | GREEN 슬롯 (`qlib_api_green` / `qlib_worker_green` / `qlib_scheduler_green` / `qlib_redis`) |
| 마지막 개발 커밋 | `fcc59da9` 2026-05-21 — 이후 약 2개월간 로그 미확인 상태로 방치 |

---

## 요약

시스템은 **배포돼 있고 Celery 크론도 정상 발화**하지만, **런타임 3곳이 깨져 실질 매매가 멈춘 상태**다.
"살아있는 것처럼 보이지만 실제로는 주문이 나가지 않고, 계좌 동기화도 실패"하고 있다.
kr-data-refresh 기능 자체(데이터 신선도 유지)의 **원래 목적은 달성**되었으나(§정상), 배포 후 Check가 한 번도
수행되지 않아 인접 파이프라인의 결함(§결함 ①②)이 드러나지 않았다.

---

## 정상 (실측 확인)

| 항목 | 근거 |
|------|------|
| 인프라 무중단 | `qlib_api_green`(healthy)/`qlib_worker_green`/`qlib_scheduler_green`/`qlib_redis`(healthy) 2개월째 Up. redis도 정상 — 배포문서가 우려했던 external-redis 블로커는 해소됨 |
| 크론(beat) 정상 | 스케줄러 로그상 2026-07-02~07-22 매 거래일 전체 태스크(09:00 `live_orders` → 09:30 `live_sync` → 15:20 `live_orders_close` → 15:35 `live_signal` → 15:40 `live_sync`/`live_sync_close` → 15:45 `refresh_kr_data`)가 정시 발화. 주말(7/11·12·18·19) 자동 제외 |
| KIS 모의투자 실계정 운영 | mock이 아니라 paper. `openapivts.koreainvestment.com:29443`, 계좌 `CANO=50160169` — paper 자격증명 이미 투입됨 |
| kr_data 신선도 | `refresh_kr_data → {'status': 'skipped', 'reason': 'up_to_date', 'last_date': '2026-07-21'}` — 데이터 스토어 최신 유지(기능 목적 달성) |

---

## 결함 (실질 매매 중단 원인)

### ① 신호 미생성 → 주문 0건 — 심각도: **높음(Critical)**

- **증상**: 매일 09:00 주문 태스크가 "신호 없음"으로 종료 → 봇이 아무 주문도 내지 않음.
- **근거 로그** (2026-07-22 09:00):
  ```
  Task live_orders ... succeeded: {'status': 'no_signal', 'as_of': '2026-07-21',
                                    'strategy': 'open', 'simulated': False}
  ```
- **근본원인 (2026-07-22 확정)**: 증분 갱신(`_fetch_universe`)이 매일 `generate_instruments_files`를
  2일치 CSV로 호출 → **instruments 멤버십이 fetch 창(2일)으로 클로버**
  (`kospi200.txt` 전 종목이 `2026-07-20 ~ 2026-07-21`). train/valid 구간에 상장 종목 0개 →
  `ValueError: Empty data from dataset`(gbdt `_prepare_data`) → 신호 미기록. `signals` 테이블
  마지막 행 = as_of 2026-05-20 — **첫 크론 가동일부터 한 번도 성공한 적 없음.**
  `dump_update`의 `save_instruments`가 올바른 병합 로직으로 이를 복원할 수 있었으나
  결함③ int 크래시로 매일 사망 — 두 결함이 맞물린 구조.
- **수정 (2026-07-22 완료)**: `generate_instruments_files`에 merge 모드(start 보존·end 연장),
  `scripts/repair_kr_instruments.py`(bin 데이터로 멤버십 일회 복구), refresh→signal 체이닝 +
  캘린더 캐시 리셋 + 신선도 가드.

### ② KIS 잔고 조회 500 → 계좌 동기화 실패 — 심각도: **높음(High)**

- **증상**: 09:30 계좌 동기화가 KIS 모의투자 잔고 API에서 500을 받아 실패 → 대시보드 잔고/PnL 미갱신.
- **근거 로그** (2026-07-22 09:30):
  ```
  Task live_sync ... raised unexpected: HTTPError('500 Server Error: Internal Server Error
    for url: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/
    inquire-balance?CANO=50160169&ACNT_PRDT_CD=01&...')
    at app/api/services/kis_client.py:219 (get_balance → r.raise_for_status())
  ```
- **유력 원인**: 워커 자식 프로세스가 5태스크마다 재생성(`worker_max_tasks_per_child=5`)되며 프로세스별
  토큰을 매번 재발급 → KIS는 신규 발급 시 기존 토큰 무효화 + 발급 rate limit → 간헐 401/500.
- **수정 (2026-07-22 완료)**: 토큰을 Redis 공유 캐시(`kis:token:{env}:{cano}`)로 이전, 401/500 시
  토큰 폐기→재인증→3회 백오프 재시도, 에러 응답 본문 로깅 (`kis_client.py`).

### ③ kr_data 증분 덤프 간헐 크래시 — 심각도: **중간(Medium, 잠복)**

- **증상**: 신규 데이터가 있을 때 `dump_update`가 종목코드 파싱 오류로 크래시. 재시도로 up_to_date가
  되면서 최종적으로는 데이터가 최신이지만, **실제 신규 데이터가 있는 날엔 덤프 실패 가능**.
- **근거 로그** (2026-07-21 15:47):
  ```
  File "/app/scripts/dump_bin.py", line 221, in <lambda>
      lambda x: fname_to_code(x.lower()).upper()
  AttributeError: 'int' object has no attribute 'lower'
  ...
  Task refresh_kr_data ... retry: RuntimeError('refresh_kr_data: dump_bin.py dump_update returned non-zero')
  ```
- **원인 (확정)**: `_read_instruments`가 기존 all.txt를 dtype 지정 없이 읽어 종목코드가 int로 파싱
  (leading zero 소실 포함) → `save_instruments`의 `.lower()`에서 크래시.
- **수정 (2026-07-22 완료)**: `_read_instruments`에 `dtype={symbol: str}`, `save_instruments`
  람다에 `str(x)` 방어 (`scripts/dump_bin.py`).

### ④ 피처 append 2개월 정지 + 쓰레기 디렉터리 (배포 후 심층 조사에서 확정) — 심각도: **높음**

- **증상**: 캘린더는 매일 늘었지만 **피처 bin은 5/19에서 정지**. `features/150/` 같은
  zero-stripped 쓰레기 디렉터리 202개가 매일 생성됨.
- **원인**: fetch CSV의 `symbol` 컬럼이 pandas에서 int로 재파싱(`000150`→`150`) →
  `dump_update`가 기존 종목 매칭 실패 → 전부 "신규 종목"으로 오인.
- **수정 (2026-07-22 완료)**: CSV에서 symbol 컬럼 제거(파일명이 코드 담당, `97b6b480`),
  쓰레기 202개 격리(`quarantine_features_20260722/`), 5/20~7/22 피처 수동 백필.

### ⑤ mlflow 의존성 드리프트 — 심각도: **중간**

- **증상**: 새로 빌드한 이미지에서 `MlflowException: filesystem tracking backend ... maintenance mode`.
- **원인**: mlflow 버전 미고정 → 재빌드 시 최신 버전이 `./mlruns` 파일 스토어를 기본 차단.
- **수정 (2026-07-22 완료)**: compose 3개 서비스에 `MLFLOW_ALLOW_FILE_STORE=true` (`e0b09afb`).
  장기적으로는 의존성 버전 고정 권장.

### ⑥ 수정주가 이음새(seam)로 인한 모델 퇴화 — 심각도: **높음**

- **증상**: 백필 후 첫 신호에서 전 종목 동일 점수(0.00057) — 상수 예측.
- **원인**: 기존 데이터(과거 fetch 기준 조정)와 백필(오늘 기준 조정)의 **수정주가 기준 불일치**.
  23종목에서 이음새 확인, 극단 사례 001230: 39,229→2,280 (17배, 분할 소급조정) →
  검증 구간 오염 → LGBM early stop iteration 1.
- **수정 (2026-07-22 완료)**: 전체 히스토리(2023-01-01~) 재수집으로 스토어 재구축 후 스왑
  (구 스토어는 `kr_data_old_20260722/` 백업). 재구축 후 신호 분화 확인(1위 0.139, 2위 0.056).
- **⚠️ 구조적 잔여 리스크**: 증분 갱신은 배당락/분할 시마다 소규모 이음새를 재유발.
  **월 1회 전체 재구축 크론 추가 권장** (Phase 3 백로그).

---

## 미해결 조사 항목

- (2026-07-22 해소) ①의 근본원인 — instruments 클로버로 확정, 수정 완료.
- **신호 품질**: 재구축 후에도 3~10위 점수 동률(플라토) — 파이프라인 정상이나 모델 변별력 약함.
  Phase 2 시나리오 매트릭스(모델/파라미터 다변화)에서 다룰 주제.

---

## PDCA 상태 메모

- 배포는 완료됐으나(`implementation-complete-pending-deploy`) Check가 한 번도 수행되지 않아
  ①②의 인접 파이프라인 결함이 2개월간 드러나지 않았다.
- 본 문서가 최초의 Check 기록이다. **결함 수정은 범위 밖(다음 작업)** 이며, 본 사이클에서는 진단만 확정한다.

## 다음 작업(범위 밖)

1. ① 신호 미생성 근본원인 규명(rocky-prod 로그).
2. 3개 결함 코드 수정 및 재배포.
3. 실패 알림 채널(Slack 등), 유니버스 드리프트 처리 — 원 plan의 open questions.
