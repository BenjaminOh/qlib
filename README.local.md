# qlib 로컬 웹 플랫폼 — 사용법 진입점

Microsoft qlib(원본 `README.md`) 위에 이 저장소에서 직접 구축한 **한국 주식(KOSPI/KOSDAQ) 백테스트용 로컬 웹 플랫폼** 사용 가이드. 다시 돌아왔을 때 "어떻게 띄우더라?"를 5초 안에 해결하기 위한 치트시트.

> 자세한 화면·API 설명은 [`docs/WEB_USAGE.md`](docs/WEB_USAGE.md), qlib 자체 학습 파이프라인은 [`docs/QUICKSTART.md`](docs/QUICKSTART.md) 참고.

> **현재 범위**: 웹 UI / API / 워커는 한국 시장(kospi200 / kosdaq150 / kospi / kosdaq)만 지원. 미국·중국 데이터는 qlib CLI(`qrun`, `./start-local.sh run <yaml>`)로 직접 다룰 수 있지만 웹 UI 에서는 노출되지 않습니다.

---

## 30초 기동 (TL;DR)

```bash
# 최초 1회 — 이미지 빌드 + 한국(KOSPI200) 데이터 다운로드 (yfinance, ~5~10분)
./start-local.sh setup
./start-local.sh data-kr

# 웹 플랫폼 기동 (포그라운드 — Ctrl+C 한 번이면 전부 정리)
./start-local.sh web

# 다른 터미널에서 브라우저 열기
./start-local.sh open            # http://localhost:5002
```

> `setup`은 미국(qlib 공식) 데이터를 받지만 웹 UI 가 보는 provider는 `kr_data` 입니다. 웹에서 백테스트하려면 `data-kr` 이 필수입니다.

qlib CLI 로 다른 시장을 다루고 싶다면 (웹 UI 외):

```bash
./start-local.sh data cn                           # 중국 A주 (qlib 공식 bin)
./start-local.sh run examples/benchmarks/.../xxx.yaml   # qrun 직접 실행
```

---

## 포트 맵

| 호스트 포트 | 서비스 | 비고 |
|:--:|--|--|
| **5002** | Frontend (Next.js 14) | 메인 웹 UI (host 5000은 macOS AirPlay 점유 회피) |
| **5001** | API (FastAPI) | Swagger: <http://localhost:5001/docs> |
| 6379 | Redis | Celery 브로커 / 잡 상태 |
| 8888 | Jupyter Notebook | `./start-local.sh jupyter` 실행 시 |
| 5050 | MLflow UI | (qlib 컨테이너 내부 5000 → 호스트 5050) |

---

## 웹 UI 주요 화면 (`http://localhost:5002`)

| 경로 | 기능 |
|--|--|
| `/` | 최근 백테스트 잡 대시보드 |
| `/backtest/new` | Market(kospi200/kosdaq150/...) · 핸들러 · 모델 · 전략 · 기간 폼으로 백테스트 제출 |
| `/backtest/{id}` | 잡 상태·메트릭·포트폴리오 차트 확인 |
| `/data` | 한국 시장 데이터 상태·종목 조회 |

REST API (FastAPI, `/api/v1` prefix) — Swagger 는 <http://localhost:5001/docs>:

| 엔드포인트 | 설명 |
|--|--|
| `GET /health` | 헬스체크 |
| `GET /data/markets` | 지원 시장 목록 (kospi200, kosdaq150, kospi, kosdaq) |
| `GET /data/instruments?market=kospi200` | 시장 내 종목 리스트 |
| `GET /data/calendar?start=&end=` | 거래일 |
| `GET /data/features?...` | qlib 피처 데이터 |
| `POST /backtests/` | 백테스트 제출 (config JSON) |
| `GET /backtests/` | 잡 목록 |
| `GET /backtests/{id}` | 잡 상태·결과 |

---

## 자주 쓰는 명령

### 웹 운영

| 명령 | 설명 |
|--|--|
| `./start-local.sh web` | 포그라운드 기동 (추천, Ctrl+C로 전체 정리) |
| `./start-local.sh web-bg` | 백그라운드 기동 |
| `./start-local.sh web-down` | 웹 서비스 정지 (컨테이너는 유지) |
| `./start-local.sh stop` | 전체 down + 컨테이너 제거 |
| `./start-local.sh health` | API/UI 핑 + compose ps |
| `./start-local.sh status` | 실행 중 컨테이너 목록 |
| `./start-local.sh logs [svc]` | 전체 또는 특정 서비스 로그 follow (`api`/`worker`/`frontend`/`redis`/`qlib`) |
| `./start-local.sh restart [svc]` | 전체 또는 특정 서비스 재시작 |
| `./start-local.sh rebuild [svc]` | `--no-cache` 재빌드 |

### 개발 / 셸

| 명령 | 설명 |
|--|--|
| `./start-local.sh shell` | 실행 중 qlib 컨테이너 bash 진입 |
| `./start-local.sh exec <cmd>` | 실행 중 컨테이너에서 명령 실행 |
| `./start-local.sh jupyter` | Jupyter 기동 (포트 자동 탐색, 기본 8888) |
| `./start-local.sh run <yaml>` | `qrun <yaml>` CLI 실행 |
| `./start-local.sh test [args]` | `pytest tests/ -m "not slow"` (qlib 자체 테스트) |
| `./start-local.sh test-web` | **웹 스택 E2E 스모크 테스트** (API + 백테스트 회귀 8건, ~12초) |
| `./start-local.sh` (인자 없음) | 일회성 인터랙티브 bash (종료 시 컨테이너 삭제) |

### E2E 스모크 테스트 — `./start-local.sh test-web`

코드를 고친 뒤 커밋 전에 **한 번 돌리기만** 하면 UI·API·워커·백테스트 파이프라인이 회귀하지 않았는지 12초 안에 확인됩니다. 스크립트 위치: `scripts/test_web.py` (stdlib 전용, 추가 의존성 없음).

현재 커버하는 케이스:

| Test | 의도 |
|--|--|
| `api_health` | `/api/v1/health` 200 |
| `frontend_reachable` | UI 5002 포트 응답 |
| `markets_lists_kospi` | 4개 KR 시장 노출 |
| `instruments_kospi200_populated` | `D.instruments` dynamic dict 파싱 회귀 방어 |
| `calendar_returns_trading_days` | 월별 거래일 ~20개 확인 |
| `backtest_happy_path` | 백테스트 제출 → `COMPLETED` + 메트릭 반환 (null benchmark 기본값 회귀 방어) |
| `backtest_end_past_calendar` | `backtest_end=2024-12-31` 에서도 IndexError 안 나는지 (clamp 회귀 방어) |
| `backtests_list` | 잡 목록 API |

선행 조건: `./start-local.sh web-bg` (또는 `web`) + `./start-local.sh data-kr` 한 번 완료.

실패 시 출력 예시:
```
  FAIL  backtest_end_past_calendar           ( 45.0s)  AssertionError: ... got FAILED — error=IndexError: ...
```
실패 케이스의 이름만 보면 어느 레이어의 회귀인지 바로 특정됩니다.

### 도움말

```bash
./start-local.sh help
```

---

## 트러블슈팅 (Top 5)

| 증상 | 조치 |
|--|--|
| UI가 하얗게 뜸 / 렌더 실패 | `./start-local.sh logs frontend` 빌드 에러 확인 |
| 백테스트가 `pending`에서 멈춤 | `./start-local.sh logs worker` — Celery/Redis/데이터 누락 확인 |
| `provider_uri` 에러 / `kospi200` 백테스트 pending 지속 | `./start-local.sh data-kr` 로 한국 데이터 선행 다운로드 |
| 코드 수정이 반영 안 됨 | `./start-local.sh restart api` (또는 `rebuild api` 후 `restart`) |
| 포트 충돌 (5002/5001/6379) | `./start-local.sh stop` 후 `lsof -iTCP:<port> -sTCP:LISTEN`으로 점유 프로세스 확인 |

완전 초기화가 필요하면:

```bash
./start-local.sh stop
docker volume rm qlib_qlib_data qlib_mlruns     # 데이터/MLflow 리셋
```

---

## 구성 요소

```
┌─────────────────────────────────────────────────────────┐
│  Browser ── :5002 ──▶ frontend (Next.js 14, Tailwind)   │
│                            │                             │
│                            │ REST/JSON :5001            │
│                            ▼                             │
│                       api (FastAPI)                      │
│                            │                             │
│                ┌───────────┴────────────┐                │
│                ▼                        ▼                │
│           worker (Celery)          redis :6379           │
│                │                                         │
│                ▼                                         │
│        qlib 파이프라인  ── volumes ──▶ qlib_data / mlruns│
└─────────────────────────────────────────────────────────┘
```

- `app/api/` — FastAPI 라우터·서비스·스키마
- `app/frontend/` — Next.js 14 + React Query + Recharts
- `docker-compose.dev.yml` — 서비스·볼륨 정의
- `start-local.sh` — 전체 운영 진입점

---

## 더 자세한 문서

| 문서 | 내용 |
|--|--|
| [`docs/WEB_USAGE.md`](docs/WEB_USAGE.md) | 웹 UI 화면·REST API 스펙·문제 해결 상세 |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | Docker 셋업 + `workspace/` 학습 파이프라인 Step 1~7 |
| [`docs/USAGE_GUIDE.md`](docs/USAGE_GUIDE.md) | qlib 활용 장문 매뉴얼 |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code 작업 시 지침·빌드·린트·테스트 |
| [`README.md`](README.md) | Microsoft qlib 원본 README |
