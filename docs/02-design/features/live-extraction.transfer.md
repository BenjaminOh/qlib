# 실매매 분리 지시서 — qlib 리포 → 전용 저장소

> 산출물은 이 문서 하나다. **코드는 만들지 않는다.**
> 착수 시점: **cafereal 표본 확보 후** (아래 §0 참조). 지금은 읽고 고치는 단계다.
>
> 작성 2026-08-21 · 방향은 `transfer-from-project` 스킬과 **반대**(pull 이 아니라 잘라내기)라
> 그 스킬을 그대로 돌리지 않았다. 이유는 §13.

---

## 0. 왜 지금 착수하지 않는가

**cafereal 이 아직 단 한 건도 주문을 내지 않았다.** 2026-08-20 22:33 배포 시점에 그날
15:28 슬롯은 이미 지났고, `QLIB_API_KIS_CAFE_*` 가 비어 있어 그 뒤로도 `no_account` 로
조용히 끝나고 있다. 사용자가 주말(08-22~23)에 한국투자증권 모의투자 API 키를 발급받을
예정이다.

이 리포의 존재 이유는 **cafe(시뮬) 곡선과 cafereal(실계좌) 곡선의 격차**를 재는 것이다 —
호가 스냅샷 8건이 전부 상한가·매도잔량 0이었던 질문에 답하는 유일한 방법. 표본 0인
상태에서 저장소를 쪼개면 그 측정이 흔들린다. 순서는:

```
키 발급 → cafereal 몇 주 관측 → 격차 측정 → 그다음 분리
```

`docs/../feedback_strategy_freeze` 의 동결 원칙과도 맞물린다.

---

## 1. 무엇을 왜 잘라내는가

qlib 으로 "전략을 어떻게 할 것인가"에서 출발해 서비스를 계속 붙이다 보니, **연구 도구와
운영 서비스가 한 저장소에 섞였다.** 원하는 기능이 안정화된 지금 운영 쪽만 떼어낸다.

**가져갈 것: 실매매 전체 (`open` + `cafereal`)** — 지금 실제로 돈이 나가는 경로 전부.
사용자 확정(2026-08-21).

---

## 2. 원본 실측 (추적 파일 기준)

| 디렉터리 | 파일 | 줄 |
|---|---|---|
| `qlib/` | 233 | 56,191 |
| `examples/` | 192 | 13,955 |
| `scripts/` | 52 | 8,781 |
| `tests/` | 62 | 9,096 |
| **`app/`** | **95** | **17,963** |
| `docs/` | 123 | 5,616 |

`app/api` 11,254줄 / `app/frontend/src` 6,696줄. `.git` 29MB.

**버리는 비율: 약 78%** (qlib 56K + examples 14K = 70,146줄이 사라진다.
직접 만든 것은 `app/` 17,963줄).

`app/api` 상위 파일: `live_trader.py`(2241) · `routers/live.py`(1619) ·
`kis_client.py`(1318) · `market_screener.py`(603) · `workers/tasks.py`(564) ·
`db/models.py`(458) · `backtest_service.py`(408) · `notify.py`(372)

---

## 3. qlib 포크는 버릴 수 있다 ← 이 문서의 핵심 발견

커밋 2,239개 중 `qlib/` 코어를 건드린 **로컬** 커밋은 2개, **3파일 18줄**이 전부다.
나머지는 전부 upstream microsoft/qlib.

| 파일 | 변경 | 새 프로젝트에서의 처리 |
|---|---|---|
| `qlib/constant.py` | `REG_KR = "kr"` 한 줄 | 앱에서 상수 정의 |
| `qlib/config.py` | `REG_KR` 지역 설정 dict (`trade_unit=1, limit_threshold=0.30, deal_price="close"`) | **외부 등록 가능** — `_default_region_config` 가 모듈 레벨 dict 이고 `qlib.init()` 이 그때 읽는다. 부팅 시 3줄로 주입 |
| `qlib/contrib/model/gbdt.py` | `es_rounds=0` 이면 early stopping 콜백을 건너뜀 | `LGBModel` 서브클래스로 `fit()` 오버라이드, 또는 얇은 포크 유지, 또는 upstream PR |

⚠ `gbdt.py` 패치는 **live 경로에 필수**다. `LIVE_CONFIG` 가 `early_stopping_rounds: 0`
을 넘기고, 이 패치가 없으면 2026-08-05 시그널 붕괴(1-tree 모델)가 재발한다. 근거:
`docs/06-research/2026-08-05-signal-collapse-diagnosis.md`.

⚠ `_default_region_config` 는 `_` 접두 비공개 이름이다. upstream 이 바꾸면 조용히 깨진다 →
§7 에 올린다.

**결론: 새 프로젝트는 `pip install pyqlib` + 패치 3개로 간다. 포크를 들고 가지 않는다.**

---

## 4. 가져갈 것 / 남길 것

### 가져간다
```
app/api/services/  live_trader kis_client market_screener holding_attribution
                   balance_cache trading_calendar account_policy notify
                   market_flow retrospective signal_reasons kr_data_refresh
app/api/routers/   live.py, auth
app/api/db/        models session __init__
app/api/workers/   celery_app tasks   (백테스트 항목 제거 후)
app/api/core/      qlib_manager kr_universes kr_stock_names.json
app/api/           main.py config.py auth/
app/frontend/src/  app/live/** app/login lib/ components/ middleware.ts
scripts/           kr_data_fetch.py dump_bin.py       ← kr_data_refresh 가 런타임 호출
tests/app/         전부 (현재 333 passed)
infra/             nginx cron firewall   (경로·포트 수정 필요, §6)
                   Dockerfile.prod app/frontend/Dockerfile
                   docker-compose.prod.yml deploy.sh Jenkinsfile .env.prod.example
```

### 남긴다 (약 1,000줄 + 프론트)
```
app/api/routers/backtest.py (260)   app/api/routers/data.py
app/api/schemas/backtest.py (193)   app/api/services/data_service.py
app/api/services/backtest_service.py (408)  ← 단, §5 의 두 함수는 예외
app/api/workers/tasks.py:39-51 run_backtest_task
app/api/workers/celery_app.py:246-258, 274-280  백테스트 카탈로그 검증
app/api/core/kr_market.py KR_EXCHANGE_KWARGS
app/frontend/src/app/backtest/**   app/frontend/src/lib/catalogs.ts
qlib/  examples/  .github/workflows/  Dockerfile(루트, upstream용)
```

**live 프론트가 백테스트·데이터 화면을 참조하는 곳은 0개다** (grep 확인). 프론트는
라우트 디렉터리만 지우면 끝난다.

---

## 5. 끊어야 할 결합 — 정확히 3곳

백테스트 계층을 남기려면 이 셋만 처리하면 된다. 전부 **우발적** 결합이다.

1. **`live_trader.py:42`**
   `from .backtest_service import _extract_recommended_picks, _stock_name`
   → 두 함수를 새 모듈(예: `services/picks.py`)로 옮긴다. **둘 다 qlib 의존이 없다** —
   `_extract_recommended_picks` 는 순수 pandas(`nlargest(topk)`), `_stock_name` 은
   `core/kr_stock_names.json` 조회.
   ⚠ 이 한 줄 때문에 **live_trader 를 import 하는 순간 `qlib.backtest` 전체가 딸려온다.**

2. **`tasks.py:8`** — `from ..services.backtest_service import run_backtest` (최상단).
   → 제거. 이것 때문에 **Celery 워커가 live 태스크만 돌려도 백테스트 스택을 로드**한다.

3. **`celery_app.py:246-258, 274-280`** — `_STRATEGY_CLASSES`/`_MODEL_CLASSES` importlib 검증.
   → 제거. 순수 백테스트 카탈로그다.

추가로 **양방향 참조 하나**: `scripts/kr_data_fetch.py:38` 이 `app.api.core.kr_universes`
를 import 한다. 유니버스 정의를 어느 쪽에 둘지 정해야 한다(권장: `app/api/core` 에 두고
scripts 가 계속 참조 — 새 저장소에서는 둘 다 같이 가므로 순환이 아니다).

---

## 6. 인프라 — 새로 만들어야 하는 것

| 항목 | 현재 | 새 프로젝트 |
|---|---|---|
| 포트 | blue 25022/25023, green 25024/25025 (newsro 25011-12 · mail-news 25021 과 순차) | **새 4개 배정** |
| nginx | `deploy.sh:192` 가 `/root/deploy/nginx-waf/conf.d` 에 직접 cp — **nginx-waf-blue 소유, 다른 스택 디렉터리** | 새 서버블록 + 인증서. 같은 WAF 를 쓰면 이 하드 카피 구조를 그대로 물려받는다 |
| 방화벽 | `infra/firewall/docker-user.rules` — DOCKER-USER 3줄로 25022-25025 차단 | **새 포트에 반드시 재적용.** 안 하면 WAF 우회 노출 재발(2026-08 점검에서 25023 이 HTTP 200 으로 IDC 내부에 열려 있었음). ⚠ ESTABLISHED,RELATED RETURN 이 맨 앞에 없으면 컨테이너 아웃바운드가 죽어 **KIS API 응답이 끊긴다 = 실매매 정지** |
| 호스트 경로 | `/home/qlib/{,data/{qlib,db,redis}}`, `/home/qlib/deploy.log` | 새 사용자/디렉터리 |
| 이미지명 | `127.0.0.1:5000/qlib-api` (로컬 레지스트리 주소지만 **실제 push 안 함**) | 정리 대상 |
| Jenkins | job 1개, `disableConcurrentBuilds()` + `timeout(120min)` | 새 job. **두 옵션 모두 유지** — 동시빌드로 #91·#92 가 같은 compose 프로젝트를 밟아 30분 정체한 사고, 40분 타임아웃이 #93 을 컨테이너 정리 직후 죽인 사고가 근거 |
| redis | `qlib_redis` 싱글턴, DB 0 에 Celery 큐 + 토큰 + 킬스위치 + 레이트리밋 공유 | **전용 인스턴스.** 공유하면 백테스트 큐와 실매매 킬스위치가 한 DB 에 섞인다 |
| 컨테이너 경로 | `kr_data_refresh.py:37-38` 의 `/app/scripts/dump_bin.py` 절대경로 | 유지 가능(같은 이미지 레이아웃이면). 다만 하드코딩임을 인지 |

**빌드 시간**: 현재 24~37분(실측 #90-94). Cython 컴파일이 `Dockerfile.prod` build
스테이지에 있다. **qlib 을 pip 의존으로 바꾸면 이 단계가 통째로 사라진다** — 분리의
가장 큰 부수 이득이다.

---

## 7. 🔴 조용히 깨지는 것

컴파일·타입체크·테스트가 **잡지 못하는** 것들. 이 절이 이 문서의 존재 이유다.

1. **`_default_region_config` 외부 주입** — 비공개 이름이라 upstream pyqlib 이 구조를
   바꾸면 `qlib.init(region="kr")` 이 KeyError 없이 **다른 지역 설정으로 조용히 뜬다**.
   `trade_unit`·`limit_threshold` 가 달라지면 백테스트·시뮬 수치가 소리 없이 어긋난다.
   → 부팅 시 주입 후 `C["limit_threshold"] == 0.30` 을 assert 한다.

2. **`live_seed_cash_open` 과 KIS 모의계좌 가상자금 불일치** — 코드 주석에 이미
   경고가 있다(2026-07-28, 1억→1천만). 안 맞추면 ROI 카드가 −90% 로 표시된다.
   새 계좌를 쓰면 반드시 다시 맞춘다.

3. **원장 DB 주인 문제** — SQLite 단일 파일(`/home/qlib/data/db/live.sqlite`)이라
   복사는 쉽지만, **두 프로젝트가 같은 파일을 동시에 쓰면 WAL 이 있어도 깨진다.**
   전환 시각을 정하고 구 리포의 beat 를 먼저 정지시켜야 한다.

4. **appkey 중복** — 새 프로젝트와 구 리포가 같은 appkey 를 쓰면 서로 토큰을 무효화한다.
   KIS 한도는 계좌가 아니라 **appkey 단위**. 병행 운영 기간이 있다면 키를 분리한다.

5. **beat 스케줄 키 중복** — 같은 dict 키를 두 번 쓰면 뒤엣것이 앞을 덮어쓴다.
   2026-08-20 `live-sync-cafe` 가 실제로 그렇게 사라질 뻔했다. 스케줄을 잘라낼 때
   키 유일성을 테스트로 고정한다.

6. **`@market_day_only` 는 fail-open** — `is_market_open()` 이 None 이면 태스크가 그냥
   돈다. 새 환경에서 redis·KIS 가 안 붙으면 **휴장일에도 주문 태스크가 실행**된다
   (KIS 가 거부하겠지만 시뮬은 막을 것이 없다).

7. **킬스위치도 fail-open** — redis 불통이면 매매가 계속된다. 새 redis 를 붙이기 전에
   실주문을 켜면 안 된다.

---

## 8. 남는 qlib 의존 (open 을 가져가므로 필수)

**모델 축 — 대체 불가**
`generate_daily_signal()` = Alpha158 핸들러 + DatasetH + LGBModel. `Signal` 테이블을
읽는 전략이 **open/close/flow/trail/scale/limit 6개**. `open` 이 지금 유일하게 실계좌를
돌리므로 이 파이프라인은 반드시 간다. → 이미지에 lightgbm·xgboost·catboost 유지.

**가격/캘린더 축 — 일부는 이미 KIS 폴백이 있다**

| 함수 | 용도 | KIS 폴백 |
|---|---|---|
| `_next_trading_day` | 다음 거래일 | ✅ KIS 휴장일 API 가 **1순위**, qlib 은 폴백 |
| `_day_ohlc_any` | 당일 봉(브래킷 판정) | ✅ `get_daily_bars` |
| `retrospective._close_series` | 회고 종가 | ✅ |
| `_last_close` | 매수 수량 계산 | ❌ (단 `_sim_fill_price` 가 KIS 시세 1순위) |
| `_prev_close_before` | 지정가 기준가 | ❌ |
| `_peak_close` | 트레일 스톱 앵커 | ❌ (trail/scale 전용) |
| `_prev_low` | 구조적 손절 앵커 | ❌ (**카페 계열은 안 씀** — `stop_source="entry"`) |
| `_stale_codes` | 거래정지 필터 | ❌ |

**참고**: `TopkDropoutStrategy` 는 `LIVE_CONFIG` 의 문자열일 뿐 live 에서 인스턴스화되지
않는다 — dropout 로직은 `submit_daily_orders` 가 `Signal` 테이블로 직접 재구현했다.
qlib 전략 클래스는 안 가져가도 된다.

---

## 9. 이전해야 할 상태

| 상태 | 위치 | 비고 |
|---|---|---|
| 원장 | `/home/qlib/data/db/live.sqlite` | **12 테이블.** DailyPnL 이 2026-05-03 부터. 곡선 연속성 = 실험의 전부 |
| kr_data | `/home/qlib/data/qlib/qlib_data/kr_data` | qlib 바이너리 스토어. 재수집 시 `scripts/kr_data_fetch.py` 8분+ |
| 시크릿 | `/home/qlib/.env` | JWT·admin·KIS 2계좌 |
| redis | `/home/qlib/data/redis` | 토큰·킬스위치는 **재생성 가능**. 굳이 옮기지 않는다 |
| beat 상태 | `/home/qlib/data/db/celerybeat-schedule` | 마지막 실행 시각 캐시. 새로 만들어도 무해 |

---

## 10. 구현 순서

1. 새 저장소 생성 + `pip install pyqlib` 로 qlib 의존 전환. §3 패치 3개 적용,
   §7-1 assert 추가. **이 단계에서 기존 테스트 333건이 그대로 통과해야 한다.**
2. §5 의 결합 3곳 절단 → 백테스트 계층 제외한 채 `app/api` 이식.
3. `celery_app.py` beat 를 실매매 축으로 축소(아래 §11 참조).
4. 프론트 이식 — `app/live/**` + `app/login` 만. `lib/catalogs.ts` 제외.
5. 인프라 — 새 포트·nginx·방화벽·Jenkins job. **실주문은 아직 끄고**(KIS 키 미주입 =
   mock 모드) 헬스체크까지 통과시킨다.
6. 상태 이전(§9) → 구 리포 beat 정지 → 새 리포 beat 기동. **이 순서를 지킨다.**
7. 병행 관측 1주: 두 화면의 곡선·잔고가 일치하는지. 어긋나면 6번으로 롤백.
8. 구 리포에서 `app/api` live 계층 제거(또는 리포를 연구 전용으로 동결).

---

## 11. beat 스케줄 축소

현재 36개 → 실매매 축 12개 내외.

**남긴다**: `live-orders-at-open`(09:00) · `reconcile-fills-morning`(09:20) ·
`cancel-unfilled-orders`(*/10) · `live-sync-mid-morning`(09:30) ·
`live-sync-after-close`(15:40) · `live-orders-at-close-cafereal`(15:28) ·
`reconcile-cafereal-after-close`(15:35) · `reconcile-cafereal-next-morning`(09:05) ·
`live-sync-cafereal-after-close`(15:46) · `kr-data-daily-refresh`(15:45) ·
`live-signal-fallback`(16:20) · `cafe-screen`(15:05)

⚠ `cafe-screen` 은 시뮬처럼 보이지만 **cafereal 이 그 `cafe_candidates` 행을 읽는다.**
빼면 cafereal 이 `no_candidates` 로 끝난다.

⚠ `kr-data-daily-refresh` 성공 시 `live_signal` → `fetch_market_flow` 체인이 걸린다.
15:35 독립 슬롯으로 되돌리면 refresh 이전이라 어제 끝나는 캘린더로 학습해
**09:00 에 만성 `no_signal`** 이 된다(과거 사고).

**부수 이득**: 시뮬을 떼면 15:2x/15:4x 분당 오프셋 스태거가 불필요해진다.
현재 그 오프셋은 SQLite `--concurrency=2` 환경에서 세 번째 라이터가 겹치면
`database is locked` 가 시작되기 때문에 존재한다.

---

## 12. 기준선 (착수 전 실측)

```
pytest tests/app        333 passed / skip 0        (2026-08-21)
npx tsc --noEmit        통과
npm run build           통과, /live 23.4 kB / 243 kB First Load JS
Jenkins 빌드            24~37분 (#90-94 실측)
beat 엔트리             36개
app/ 줄 수              17,963 (api 11,254 + frontend 6,696)
```

**게이트는 skip 을 실패로 친다.** `tests/app` 전 파일이 `pytest.importorskip` 으로
감싸져 있어 의존성 없는 머신에서는 전부 SKIP 되고도 exit 0 이 나온다 — 로그인 락아웃
결함이 실제로 그렇게 살아남았다. 새 저장소의 CI 도 이 규칙을 반드시 옮긴다.

---

## 13. 왜 `transfer-from-project` 를 그대로 돌리지 않았나

| 스킬의 전제 | 이 작업 |
|---|---|
| 대상에서 실행해 원본을 **끌어온다** | 원본에서 **잘라낸다** (방향 반대) |
| 2단계 `probe-conventions.sh <대상>`, §11 대상 기준선 | 대상이 아직 없다 |
| 본체는 **두 프로젝트의 규약 차이**(§7) | 새 프로젝트를 원본 규약 그대로 만든다 → 차이 0, 그 절이 빈다 |
| `analyze-scope.sh` 가 코드 결합을 잰다 | 진짜 위험은 **런타임 상태·곡선 연속성** — 스킬에 그 항목이 없다 |
| 대상: 컴포넌트·기능 하나 | 백엔드+프론트+워커+인프라+테스트 전체 |

스킬에서 **가져온 것**: 코드 전에 지시서를 쓴다는 원칙, 죽은 코드 확인 후 규모 계산,
"프리미티브는 복사가 아니라 신규 작성", 착수 전 기준선 실측(§12), 그리고 §7 을 문서의
본체로 두는 구성.

---

## 14. 미결 — 착수 시 결론을 남길 것

1. `gbdt.py` 패치를 서브클래스로 우회할지, 얇은 포크를 유지할지, upstream PR 을 낼지.
2. 도메인 — `qlib.tmanager.kr` 을 새 프로젝트가 가져갈지 새 도메인을 팔지.
3. 병행 운영 기간에 KIS appkey 를 몇 개 쓸지(구 리포 정지 후 재사용 vs 신규 발급).
4. 구 리포의 운명 — 연구 전용으로 동결할지, `app/` 을 통째로 지울지.
5. Postgres 전환을 이 기회에 할지(`.env.prod.example:37` 에 "실매매 승격 시" 로 예고돼 있음).
