# Plan: kr-data-refresh

| | |
|---|---|
| **Feature** | `kr-data-refresh` |
| **Owner** | BenjaminOh |
| **Created** | 2026-05-19 |
| **PDCA Phase** | Plan |
| **Status** | Draft |

## Context (Why)

운영 서버 (rocky-monitor / `claude-admin`) 의 qlib `kr_data` 저장소가 **2026-04-30 까지만** 채워져 있어, 매일 15:35 firing 하는 `live_signal` task 가 `as_of=2026-05-01` 로 신호를 적재한다. 실제 KRX 다음 거래일은 5/20 (오늘이 5/19 화요일). 결과적으로:

- 모델 입력 데이터는 **4/30 기준**, 추천 종목 점수도 4/30 시점의 모델로 산출
- 다음날 09:00 `live_orders` 가 이 신호로 KIS 실주문 발사 → 현재 시세(5/19~5/20)와 18거래일 가량 시차 → 가격 괴리로 미체결, 슬리피지, 의도 외 매매 가능성
- KIS 실거래 잔고 (`position_snapshots` table) 는 5/14, 5/15, 5/18 데이터까지 정상 적재 → KIS API 사이드는 멀쩡, qlib 데이터 갱신만 끊긴 상태

오늘 (5/19) 스키마 드리프트 복구를 끝낸 다음 단계로, **데이터 신선도를 운영 가능한 수준으로 회복** 하고 **재발 방지** 한다.

## Goals

1. **즉시 (catch-up)**: 운영 `kr_data` 를 오늘 (또는 가장 최근 거래일) 까지 증분 갱신. 모델 입력이 5/19 종가 기준이 되도록.
2. **항상 (automation)**: 매 거래일 장 마감 직후 (15:45 ~ 16:00 KST) `kr_data` 가 자동 갱신되도록 Celery beat 에 cron 추가. 다음 firing (15:45 체인 `live_signal`(구 15:35 슬롯은 폐기)) 은 그 다음날부터 fresh data 사용.
3. **관찰 가능성**: 갱신 실패 시 즉시 감지할 수 있어야 함 (worker log + DB 또는 simple healthcheck endpoint).

## Non-goals

- **Intraday 데이터**: 일봉만 다룸. 분봉/틱은 별도 feature.
- **Universe 확장**: 현재 KOSPI200 + KOSDAQ150 + KODEX200 그대로. 새 종목군 추가는 별도.
- **데이터 품질 검증**: yfinance 가 반환한 값의 정합성 (분할/배당 조정 정확성, missing 값 패턴 등) 검사는 별도. 이번엔 "오면 받음" 수준.
- **PostgreSQL 이관, ML 모델 재학습** 등은 본 plan 범위 밖.

## Constraints

- 운영 데이터 디렉터리는 root 소유 `/home/qlib/data/qlib/qlib_data/kr_data/` (bind-mount). `claude-admin` 은 호스트 직접 쓰기 불가 → 도커 컨테이너 안에서 작업.
- 갱신 도중 `live_signal` 또는 `live_orders` 가 동시 firing 하면 부분 작성 데이터를 읽을 위험 → 마감 후 firing 안 도는 시간 (15:45 ~ 다음날 08:55) 에 실행.
- yfinance 는 외부 무료 API. rate limit / 일시 장애 가능성 있어 retry 로직 필요.
- KRX 휴장일 (공휴일) 은 yfinance 가 자동으로 row 없이 반환 → 캘린더 자동 정합.

## Approach (recommended)

### Phase A — 즉시 catch-up (수동, 30분 이내)

1. 운영서버에 임시 컨테이너 띄워 `scripts/kr_data_fetch.py` 실행:
   ```
   sudo docker run --rm \
     -v /home/qlib/data/qlib:/root/.qlib \
     --entrypoint python \
     127.0.0.1:5000/qlib-api:latest \
     scripts/kr_data_fetch.py --start 2026-04-01 --end 2026-05-19 \
     --csv_dir /tmp/kr_csv --qlib_dir /root/.qlib/qlib_data/kr_data
   ```
   `--start` 를 4/1 로 잡아 약간 overlap 시켜 dump_bin 가 idempotent 하게 갱신하도록.
2. `day.txt` 마지막 줄이 5/19 (또는 직전 거래일) 인지 확인.
3. `qlib_scheduler_green` 한 번 재기동 (또는 다음 15:35 firing 자연 대기) → 다음 `live_signal` 호출에서 `as_of` 가 실제 다음 거래일로 잡히는지 확인.

### Phase B — 자동화 (Celery beat task 추가)

1. 새 task `refresh_kr_data` 를 `app/api/workers/tasks.py` 에 추가:
   - 기존 `scripts/kr_data_fetch.py` 의 fetch / generate_instruments / run_dump_bin 함수를 호출.
   - `start = max(qlib_calendar_last_date - 5, "2023-01-01")` 로 5일 overlap 증분.
   - 결과(`{'fetched': N, 'latest_date': '2026-05-19'}`)를 logger 에 기록 + 성공/실패 redis key 1개 set (`celery_app.conf.beat_schedule` 의 메타데이터 또는 별도 `last_kr_data_refresh` key).
2. `app/api/workers/celery_app.py` beat_schedule 에 추가:
   ```python
   "kr-data-daily-refresh": {
       "task": "refresh_kr_data",
       "schedule": crontab(hour=15, minute=45, day_of_week="mon-fri"),
   },
   ```
   15:40 `live_sync*` 가 끝난 뒤 15:45 에 발사. 다음날 09:00 `live_orders` 전에 충분히 끝남.
3. 실패 시 동작:
   - retry: Celery `autoretry_for=(Exception,), retry_backoff=True, max_retries=3`
   - 그래도 실패면 `notes` 컬럼이 있는 `daily_pnl` row 의 `notes` 에 "kr_data refresh failed: <reason>" 기록하거나, redis stale 키만 두고 다음 15:35 firing 이 자체 detect.

### Phase C — 관찰 가능성 (lightweight)

1. `/api/v1/health` 에 필드 추가: `"kr_data_last_date": "2026-05-19"` — qlib calendar 의 마지막 날짜를 노출.
2. 옵션: 그 날짜가 N 거래일 이상 stale 하면 `status=stale` 반환. 모니터링 대시보드/롤백 트리거에 활용.

→ Phase A 와 B 가 우선, C 는 nice-to-have.

## Risks

| Risk | Mitigation |
|------|------------|
| yfinance 일시 장애로 매일 갱신 실패 | retry + redis stale 키, 며칠 stale 허용 (모델 입력이 며칠 늦어도 KIS 자체는 그대로 매매 가능) |
| **`dump_bin.py dump_all` 모드가 destructive — fetch 범위 밖 history 를 wipe 함** | 2026-05-19 실측으로 확인됨. `--start 2026-04-01` 만 주면 2023~3월 calendar/instruments 가 잘림. Design 단계에서 (a) 매일 full range fetch (b) `dump_update` 같은 증분 모드 사용 (c) CSV 누적 후 dump_all 중 택일 |
| beat task 가 다른 firing 과 동시 실행 (worker concurrency=2) | 15:45 cron 은 그 시간대에 다른 firing 없음. concurrent 락은 불요. |
| 휴장일에도 task 발사 | 휴장 평일이라도 yfinance 가 row 0개 반환 → 무해, calendar 도 자동 정합 |
| 갱신 도중 `live_signal` 이 우연히 fire | 15:35 firing 직후 15:45 cron, 15:50 까지 안 끝나면 다음 사이클 살짝 영향. cron 시간 조정 가능 |

## Open Questions

1. **Phase A 즉시 catch-up 을 Plan/Design 끝낸 다음 이번 세션에 같이 처리할지, 별도 처리할지?**
2. **`refresh_kr_data` task 의 retry 실패 시 알림 채널** — Slack webhook? 그냥 worker log 만? 현재 운영에서 알림 채널 정해진 게 있나?
3. **Universe 변경 시점** — 새 시즌마다 KOSPI200 멤버 바뀜. 이건 별도 feature 로 분리해도 OK?

## Affected files

| 파일 | 변경 |
|------|------|
| `app/api/workers/tasks.py` | `refresh_kr_data` task 신규 추가 |
| `app/api/workers/celery_app.py:35-64` | `beat_schedule` 에 `kr-data-daily-refresh` 추가 |
| `app/api/routers/health.py` (또는 `main.py` health 엔드포인트) | `kr_data_last_date` 필드 추가 (Phase C, 옵션) |
| `scripts/kr_data_fetch.py` | refactor → 함수 호출 가능하게 일부 분리 (optional, task 가 import 해서 쓰려면) |

## Reused utilities

- `scripts/kr_data_fetch.py:60` `fetch_stock_data`
- `scripts/kr_data_fetch.py:79` `fetch_market_data`
- `scripts/kr_data_fetch.py:118` `generate_instruments_files`
- `scripts/kr_data_fetch.py:157` `run_dump_bin`
- `app/api/core/kr_universes.py` `KOSPI200, KOSDAQ150, KOSPI200_BENCHMARK, MARKETS`

## Verification

- Phase A: 운영 컨테이너에서 `cat /root/.qlib/qlib_data/kr_data/calendars/day.txt | tail -3` → 마지막 날짜가 오늘 (또는 직전 거래일) 인지.
- Phase B: 다음날 15:46 ~ 15:50 사이에 `ssh rocky-monitor container_log qlib_worker_green | grep refresh_kr_data` 에 `succeeded` 로그 있는지. day.txt 가 갱신됐는지.
- Phase B end-to-end: 다음다음날 15:45 체인 `live_signal`(구 15:35 슬롯은 폐기) 의 `as_of` 가 진짜 다음 거래일 (e.g., 2026-05-21) 로 찍히는지.
- Phase C: `curl https://qlib.tmanager.kr/api/v1/health` 응답에 `kr_data_last_date` 필드.

## Next step

`/pdca design kr-data-refresh` 로 Design 단계 진입 → `refresh_kr_data` task 의 구체적 인터페이스, retry 정책, error model 설계.
