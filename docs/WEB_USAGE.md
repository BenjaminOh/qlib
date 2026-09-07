# qlib Web UI — 사용법

브라우저에서 qlib의 모델·핸들러·전략·백테스트를 클릭만으로 실험할 수 있도록 Docker 로 묶어둔 웹 플랫폼 사용 가이드입니다. 모든 조작은 프로젝트 루트의 `start-local.sh` 한 개로 끝납니다.

---

## 1. 구성 요소

`docker-compose.dev.yml` 이 다음 서비스를 띄웁니다.

| 서비스     | 포트  | 역할 |
|-----------|------|------|
| frontend  | 5002 | Next.js 14 웹 UI (host 5000은 macOS AirPlay 점유 회피) |
| api       | 5001 | FastAPI (백테스트 제출/조회, 카탈로그, 데이터, 실험) |
| worker    | —    | Celery 워커 (학습·백테스트 실제 실행) |
| redis     | 6379 | Celery 브로커 + 잡 상태 저장소 |
| qlib      | —    | 인터랙티브 dev 컨테이너 (쉘/주피터/qrun 용) |

공유 볼륨:
- `qlib_data` → `~/.qlib/qlib_data` (다운로드한 시장 데이터)
- `mlruns` → MLflow 실험 로그

---

## 2. 빠른 시작

```bash
# 1. 웹 플랫폼 기동 (포그라운드 — 모든 서비스 로그가 한 화면에 실시간으로 흐름)
./start-local.sh web
#   → Ctrl+C 한 번이면 redis/api/worker/frontend 모두 자동 정리됨

# 2. 다른 터미널에서 브라우저 열기
./start-local.sh open           # http://localhost:5002

# 3. (최초 1회) 시장 데이터 다운로드
./start-local.sh data us        # 미국
./start-local.sh data cn        # 중국
./start-local.sh data-kr        # 한국 (KOSPI200, yfinance)

# 백그라운드로 띄우고 싶다면
./start-local.sh web-bg         # 백그라운드 기동
./start-local.sh logs api       # 특정 서비스 로그 follow
./start-local.sh web-down       # 일시 정지
./start-local.sh stop           # 컨테이너 제거
```

최초 실행 시 이미지 빌드에 수 분이 걸릴 수 있습니다. 이후에는 수 초 내 기동됩니다.

---

## 3. `start-local.sh` 명령 레퍼런스

### 웹 운영
| 명령 | 설명 |
|------|------|
| `./start-local.sh web` | **포그라운드** 기동. 모든 서비스 로그가 컬러 prefix 와 함께 한 화면에 실시간 출력. **Ctrl+C 한 번이면 전부 자동 정리** |
| `./start-local.sh web-bg` | 백그라운드 기동 (CI/원격 개발용). 이후 `logs`/`web-down` 등으로 관리 |
| `./start-local.sh web-down` | 위 네 서비스 정지 (컨테이너는 유지) |
| `./start-local.sh open` | `http://localhost:5002` 브라우저로 열기 |
| `./start-local.sh health` | API/UI 핑 + `docker compose ps` |
| `./start-local.sh status` | 실행 중인 컨테이너 목록 |
| `./start-local.sh logs` | 모든 서비스 로그 follow |
| `./start-local.sh logs api` | 특정 서비스만 (api/worker/frontend/redis/qlib) |
| `./start-local.sh restart` | 웹 서비스 전체 재시작 |
| `./start-local.sh restart api` | 특정 서비스만 재시작 |
| `./start-local.sh rebuild` | 이미지 `--no-cache` 재빌드 |
| `./start-local.sh rebuild frontend` | 특정 서비스 이미지만 재빌드 |
| `./start-local.sh stop` | 전체 `down --remove-orphans` |

### 데이터
| 명령 | 설명 |
|------|------|
| `./start-local.sh data` | 미국 시장 데이터 다운로드 (기본값) |
| `./start-local.sh data cn` | 중국 시장 데이터 |
| `./start-local.sh data both` | 미국 + 중국 |
| `./start-local.sh data-kr` | 한국 (KOSPI200, yfinance → qlib bin) |

### 셸 / 개발
| 명령 | 설명 |
|------|------|
| `./start-local.sh` | 일회성 인터랙티브 bash (종료 시 컨테이너 제거) |
| `./start-local.sh up` | 모든 서비스 백그라운드 기동 |
| `./start-local.sh shell` | 실행 중 qlib 컨테이너 bash 진입 |
| `./start-local.sh exec <cmd>` | 실행 중 컨테이너에서 명령 실행 |
| `./start-local.sh jupyter` | Jupyter Notebook 기동 (포트 자동 탐색) |
| `./start-local.sh run <yaml>` | `qrun <yaml>` CLI 실행 |
| `./start-local.sh test [args]` | `pytest tests/ -m "not slow"` |
| `./start-local.sh setup` | 원클릭 셋업 (빌드 + 미국 데이터 + 검증) |

---

## 4. 웹 UI 화면 안내

모든 화면은 `http://localhost:5002` 하위에 있습니다.
### 라이브 자동매매 (`/live/**`) — **이 저장소의 본체**

⚠️ 아래 백테스트 화면 설명은 2026-04 시점 문서다. **실제 운영의 중심은 라이브 대시보드**이고
아래 라우트가 그 전부다(총 14개 라우트).

| 경로 | 기능 |
|---|---|
| `/live` | 라이브 대시보드 — 11전략 곡선, 보유, 청산, 추천 TOP10, 최근 주문 |
| `/live/orders` | 주문 원장 (전략 칩으로 필터 · 실주문/시뮬 뷰) |
| `/live/positions` | 잔고 스냅샷 이력 |
| `/live/accounts` | **계좌 주문 정책** — 기본 계좌(open)·카페 계좌(cafereal) 주문 방식 |
| `/live/retro` | 매매 회고 — 에피소드 원장·가설 스코어보드·rank IC |
| `/guide` | **운영 중인 시스템 전체 해설**(타임라인·11전략 매트릭스·카페 역설계·급등 전야) |
| `/login` | 인증 |
| `/backtest/optimize`, `/backtest/optimize/[group_id]` | 파라미터 최적화 |

REST API 도 아래 목록이 전부가 아니다 — `/live/**`(`app/api/routers/live.py`, 1,600줄+)와
인증 라우터가 별도로 있다. 오너용 개념 설명은 `docs/00-운영-매매-방식-해설.md` 참조.

### 백테스트 화면 (4개)


### `/` — 대시보드
- 최근 백테스트 잡 목록 (상태/전략/모델/종목군/기간/생성시각)
- 각 행 클릭 시 결과 페이지로 이동

### `/backtest/new` — 백테스트 설정
- **Market** — `kospi200` / `kosdaq150` / `kospi` / `kosdaq` (API `/data/markets`)
- **Handler** — 프론트엔드에 하드코딩된 옵션 (Alpha158 등)
- **Model** — LGBModel 등 (현재 프론트엔드 기본 옵션)
- **Strategy** — TopkDropoutStrategy 등 (topk/n_drop kwargs)
- **Data split** — train / valid / test / backtest 기간
- **Exchange** — 수수료(open/close), min_cost, trade_unit, limit_threshold
- **Account** — 초기 자본
- 제출 후 `/backtest/{id}` 로 리다이렉트, 폴링으로 상태 추적

### `/backtest/{id}` — 결과
- 잡 상태(pending / running / success / failed)
- 성공 시 메트릭 / 포트폴리오 시계열
- 실패 시 에러 메시지

### `/data` — 데이터 탐색
- 시장 선택 후 종목 리스트 조회 (`/api/v1/data/instruments`)
- 거래일 확인 (`/api/v1/data/calendar`)
- 피처 조회 (`/api/v1/data/features`)

> **참고**: 과거 문서에서 언급했던 `/workflows` (YAML 업로드), `/experiments` (MLflow 브라우저), 데이터 다운로드 버튼 / 진행률 UI 는 **현재 구현되어 있지 않습니다**. YAML 실행은 `./start-local.sh run <yaml>` CLI 를 쓰고, MLflow 는 `./start-local.sh jupyter` 후 `mlflow ui` 로 따로 띄우거나 `mlruns/` 볼륨을 직접 읽으세요.

---

## 5. REST API 직접 호출

프론트 없이 API만 써도 동일한 기능을 쓸 수 있습니다. 전체 스펙은 Swagger 에서 확인하세요.

- Swagger UI: <http://localhost:5001/docs>
- Health: `GET /api/v1/health`
- Data:
  - `GET /api/v1/data/markets`
  - `GET /api/v1/data/instruments?market=<name>`
  - `GET /api/v1/data/calendar?start=<YYYY-MM-DD>&end=<YYYY-MM-DD>`
  - `GET /api/v1/data/features?...` (세부 파라미터는 Swagger 참고)
- Backtest:
  - `POST /api/v1/backtests/`   ← body: 전략/모델/핸들러/기간 config JSON
  - `GET /api/v1/backtests/`   ← 전체 잡 목록
  - `GET /api/v1/backtests/{id}`

예시:
```bash
curl -s localhost:5001/api/v1/data/markets | jq
curl -s "localhost:5001/api/v1/data/instruments?market=kospi200" | jq '.[:3]'
```

---

## 6. 문제 해결

| 증상 | 조치 |
|------|------|
| `web` 후 UI 가 하얗게 뜸 | `./start-local.sh logs frontend` 로 빌드 에러 확인 |
| 백테스트가 `pending` 에서 멈춤 | `./start-local.sh logs worker` — Celery/Redis 연결 또는 데이터 누락 확인 |
| `provider_uri` 에러 | 해당 리전 데이터 미다운로드. `./start-local.sh data <region>` |
| 코드 수정이 반영 안 됨 | `./start-local.sh restart api` (또는 `rebuild api` 후 `restart`) |
| 포트 충돌 (5002/5001) | 기존 프로세스 종료 후 `./start-local.sh web` 재시도 |
| 완전 초기화 | `./start-local.sh stop && docker volume rm qlib_qlib_data qlib_mlruns` |

로그를 한 번에 보고 싶다면 `./start-local.sh logs` 로 전체 서비스를 follow 할 수 있습니다.

---

## 7. 참고

- 전체 구현 계획: `~/.claude/plans/cheeky-humming-beacon.md`
- Compose 정의: `docker-compose.dev.yml`
- 스크립트: `start-local.sh`
- 백엔드: `app/api/`  (routers/services/schemas)
- 프론트: `app/frontend/` (Next.js 14, React Query, Tailwind, Recharts)
