# Design: kr-data-refresh

| | |
|---|---|
| **Feature** | `kr-data-refresh` |
| **Plan** | [docs/01-plan/features/kr-data-refresh.plan.md](../../01-plan/features/kr-data-refresh.plan.md) |
| **PDCA Phase** | Design |
| **Status** | Draft |
| **Created** | 2026-05-19 |

## Background recap (from Plan)

오늘 (5/19) Phase A (수동 catch-up + signal 재생성) 완료. 다음 단계는 매일 자동 갱신이 도는 Celery beat task 구현. 핵심 발견:

- `scripts/dump_bin.py` 에 **`dump_update`** 모드 존재 — 기존 `calendars/day.txt` 의 마지막 날짜 이후 데이터만 append, history 보존. Plan 의 Risk 항목 ("dump_all destructive") 해결책.
- `scripts/kr_data_fetch.py:157` `run_dump_bin` 은 현재 `dump_all` 고정. 이를 `dump_update` 로 분기 또는 별도 함수 추가 필요.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Celery beat  ── crontab(15:45 KST, mon-fri) ──▶ "refresh_kr_data" task │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
        ┌───────────────────────────────────────────────────┐
        │  app/api/workers/tasks.py :: refresh_kr_data_task │
        │   - autoretry_for=(Exception,)                    │
        │   - retry_backoff=True                            │
        │   - max_retries=3                                 │
        └───────────────────────────────────────────────────┘
                                │
                                ▼
        ┌───────────────────────────────────────────────────┐
        │  app/api/services/kr_data_refresh.py (신규)        │
        │   - get_qlib_last_date(qlib_dir) -> date          │
        │   - fetch_incremental(last_date, today)           │
        │   - dump_update(csv_dir, qlib_dir)                │
        │   - set_redis_status(...)                         │
        └───────────────────────────────────────────────────┘
                │                              │
                ▼                              ▼
    /home/qlib/data/qlib/qlib_data/kr_data    redis: last_kr_data_refresh
       (host bind-mount)                       (key: success/failure, ts, last_date)
```

매일 한 번 실행되며, worker process 안에서 작동 (별도 컨테이너 X). `scripts/kr_data_fetch.py` 의 함수를 reuse 하되, 일일 자동화 친화적 형태로 wrap.

## Interfaces

### `app/api/services/kr_data_refresh.py` (신규)

```python
from datetime import date, timedelta
from pathlib import Path
import logging

from scripts.kr_data_fetch import (
    fetch_market_data, generate_instruments_files,
)
from app.api.core.kr_universes import MARKETS, KOSPI200, KOSDAQ150, KOSPI200_BENCHMARK

log = logging.getLogger(__name__)

QLIB_DIR = Path("/root/.qlib/qlib_data/kr_data")
CSV_STAGING = Path("/tmp/kr_csv_incremental")


def get_qlib_last_date(qlib_dir: Path = QLIB_DIR) -> date:
    """Last date in qlib calendars/day.txt. Defaults to 7d ago if absent."""
    cal = qlib_dir / "calendars" / "day.txt"
    if not cal.exists():
        return date.today() - timedelta(days=7)
    last_line = cal.read_text().strip().splitlines()[-1]
    return date.fromisoformat(last_line)


def refresh_kr_data(today: date | None = None) -> dict:
    """Incrementally extend kr_data binary store up to `today`.

    1. Determine date window: (last_qlib_date + 1d) → today (or last KRX trading day).
    2. yfinance fetch for the universe (KOSPI200 + KOSDAQ150 + KODEX 200 ETF).
    3. dump_bin in 'dump_update' mode — appends only new rows, preserves history.
    4. Verify calendars/day.txt latest matches expectation.

    Returns: {'last_date': ..., 'fetched_tickers': ..., 'new_calendar_days': ...}
    Raises: RuntimeError on fetch/dump failure (Celery retry kicks in).
    """
    ...


def _run_dump_update(csv_dir: Path, qlib_dir: Path) -> bool:
    """Subprocess into scripts/dump_bin.py with mode=dump_update."""
    import subprocess, sys
    cmd = [
        sys.executable, "/app/scripts/dump_bin.py", "dump_update",
        "--data_path", str(csv_dir),
        "--qlib_dir", str(qlib_dir),
        "--freq", "day",
        "--date_field_name", "date",
        "--symbol_field_name", "symbol",
        "--include_fields", "open,high,low,close,volume",
    ]
    return subprocess.run(cmd, check=False).returncode == 0
```

### `app/api/workers/tasks.py` 추가

```python
@celery_app.task(
    bind=True, name="refresh_kr_data",
    autoretry_for=(Exception,), retry_backoff=True, retry_backoff_max=600,
    max_retries=3,
)
def refresh_kr_data_task(self) -> dict:
    """15:45 KST — incrementally extend kr_data so live_signal sees today's bars."""
    from ..services.kr_data_refresh import refresh_kr_data
    self.update_state(state="RUNNING")
    result = refresh_kr_data()
    _set_status("refresh_kr_data:ok", result)
    return result
```

`_set_status` 는 redis 에 1개 key 작성 (다음 섹션).

### `app/api/workers/celery_app.py:35-64` beat_schedule 추가

```python
"kr-data-daily-refresh": {
    "task": "refresh_kr_data",
    "schedule": crontab(hour=15, minute=45, day_of_week="mon-fri"),
},
```

15:45 체인 `live_signal`(구 15:35 슬롯은 폐기) 보다 **이전이 아니라 이후** 에 배치한 이유: 오늘 종가 데이터가 안정화될 시간 + 15:40 sync 가 끝난 다음. 다음 거래일 15:45 체인 `live_signal`(구 15:35 슬롯은 폐기) 이 fresh data 로 모델 fit.

### Health 엔드포인트 (Phase C, optional)

`app/api/routers/health.py` (혹은 `main.py` 의 health handler) 에 필드 추가:

```python
def health():
    return {
        "status": "ok",
        "qlib_initialized": True,
        "provider_uri": settings.provider_uri,
        "kr_data_last_date": _read_qlib_last_date(),         # 신규
        "kr_data_stale_days": _stale_days(),                  # 신규
    }
```

`_stale_days` 가 5 이상이면 `status="stale"` 반환 (캘린더는 영업일만 카운트 — 휴장일 stale 오탐 방지).

## Data flow

```
yfinance API ── 320~334 종목 × N일 ──▶  /tmp/kr_csv_incremental/<code>.csv
                                                       │
                                                       ▼
                                          dump_bin.py dump_update
                                                       │
                                                       ▼
                  /home/qlib/data/qlib/qlib_data/kr_data/
                       ├── calendars/day.txt    (append)
                       ├── instruments/all.txt   (end_date 갱신)
                       └── features/<code>/      (append rows)
```

## Error model

| 실패 케이스 | 대응 | 사용자 노출 |
|------------|------|-----------|
| yfinance HTTP 5xx / 일시 장애 | Celery autoretry (backoff: 1→2→4→8분, max 600s) | retry 3회 모두 실패 시 worker log + redis stale 키 |
| 종목별 SKIP (delisted) | log warning, 진행 계속 | `fetched_tickers < 90%` 이면 결과 dict 에 warning 플래그 |
| `dump_update` 실패 (return code ≠ 0) | RuntimeError raise → Celery retry | worker log + redis |
| `last_qlib_date` 가 today 와 같음 (이미 갱신됨) | no-op, dict 에 `skipped=True` 반환 | log info |
| 휴장일 (KRX) — yfinance row 0 | calendar 변화 0, `new_calendar_days=0` 정상 반환 | log info |

## Observability — redis status key

```python
# 키: kr_data_refresh:status
# 값: JSON {"status": "ok"|"failed", "ts": ISO8601, "last_date": "2026-05-19", "fetched": 320, "skipped": 14, "error": null}
import redis, json, datetime
r = redis.from_url(settings.celery_broker_url)
r.set("kr_data_refresh:status", json.dumps({...}), ex=86400 * 7)  # 7일 TTL
```

- health endpoint 에서 이 키 읽어 `kr_data_last_refresh_ok=bool, ago=N` 노출
- 외부 모니터링 시스템에서 7일 TTL 만료 시 자동으로 "missing" 신호 잡힘 (silent failure 방지)

## Affected files

| 파일 | 변경 |
|------|------|
| `app/api/services/kr_data_refresh.py` | **신규**. 핵심 로직. |
| `app/api/workers/tasks.py:end` | `refresh_kr_data_task` 추가 |
| `app/api/workers/celery_app.py:35-64` | beat_schedule 항목 추가 |
| `app/api/routers/health.py` (또는 `main.py`) | health response 필드 추가 (옵션) |
| `scripts/kr_data_fetch.py:157` `run_dump_bin` | 그대로 (수동 일회성 fetch 용). `kr_data_refresh.py` 가 별도 `_run_dump_update` 호출 |

## Reused utilities

- `scripts/kr_data_fetch.py:46` `kr_code_to_yahoo`
- `scripts/kr_data_fetch.py:60` `fetch_stock_data`
- `scripts/kr_data_fetch.py:79` `fetch_market_data`
- `scripts/kr_data_fetch.py:118` `generate_instruments_files`
- `scripts/dump_bin.py:392` `DumpDataUpdate` (via subprocess CLI)
- `app/api/core/kr_universes.py` `MARKETS, KOSPI200, KOSDAQ150, KOSPI200_BENCHMARK`

## Implementation order (for Do phase)

1. **새 모듈** `app/api/services/kr_data_refresh.py` 작성 (loop test 가능)
2. **단위 동작 확인**: 로컬에서 `python -c "from app.api.services.kr_data_refresh import refresh_kr_data; print(refresh_kr_data())"` — 더미 qlib_dir 로 dump_update 호출 확인
3. **task wiring** `app/api/workers/tasks.py` 에 `refresh_kr_data_task` 추가
4. **beat schedule** `celery_app.py` 에 cron 추가
5. **운영 테스트**: 다음 배포 후 다음날 15:45 firing → `container_log` 에 `succeeded` 확인
6. **(Optional) Health 엔드포인트 필드** — Phase C

## Verification (for Check phase)

- **Unit**: `_run_dump_update` 가 비어있는 CSV 디렉터리에 대해 graceful 처리 (no-op + success), 1일 새 데이터에 대해 calendar 1줄 append
- **Integration**: 로컬에 더미 `kr_data` 만들고 task 1회 실행 → calendar/instruments/features 가 한 날짜만큼 늘었는지
- **Prod smoke**: 첫 firing 다음날 (15:46) 에 `ssh rocky-monitor container_log qlib_worker_blue | grep refresh_kr_data` → `succeeded` 1건 + redis `kr_data_refresh:status.status == "ok"`
- **End-to-end**: 다음다음날 15:45 체인 `live_signal`(구 15:35 슬롯은 폐기) 의 `as_of` 가 진짜 다음 거래일 (e.g., today+1 영업일) 인지

## Open Questions

1. **Worker concurrency**: 현재 `--concurrency=2`. `refresh_kr_data` 가 yfinance 호출 + dump_bin subprocess 합쳐 ~5분. 동시 `live_signal` firing 이 발생하면 worker pool 점유. 다행히 cron 시간 (15:35 vs 15:45) 분리되어 있어 충돌 없음.
2. **Universe drift**: KOSPI200 / KOSDAQ150 membership 은 시즌마다 변경. 현재 `app/api/core/kr_universes.py` 에 hardcoded. 누락/추가 종목은 별도 PDCA 로 분리 (out of scope).
3. **시간대**: 컨테이너 TZ=Asia/Seoul 확정. cron `crontab(hour=15, minute=45)` 가 KST 로 작동함. Beat singleton 도 KST 처리. 확인 끝.

## Next step

`/pdca do kr-data-refresh` 로 구현 진입. 위의 Implementation order 1~6 단계 실행.
