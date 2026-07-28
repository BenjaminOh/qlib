"""Incrementally extend the qlib kr_data binary store.

Designed to be called by the daily Celery beat task ``refresh_kr_data`` after
the market close (15:45 KST), so the next morning's ``live_signal`` task fits
the model on today's bars.

Key invariants:

- We use ``dump_bin.py dump_update`` (NOT ``dump_all``). ``dump_update`` reads
  the existing ``calendars/day.txt`` and appends rows for dates *after* the
  last calendar entry, preserving multi-year history. ``dump_all`` overwrites
  everything to the fetched range — already burned us once.
- yfinance failures are surfaced via ``RuntimeError`` so Celery's autoretry
  triggers backoff. A redis status key (``kr_data_refresh:status``) records
  the latest run so observers (health endpoint, external monitors) can detect
  silent freshness drift.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

# Bind-mounted on prod at /home/qlib/data/qlib/qlib_data/kr_data.
QLIB_DIR = Path(os.environ.get("QLIB_API_PROVIDER_URI") or settings.provider_uri).expanduser()
CSV_STAGING = Path("/tmp/kr_csv_incremental")
DUMP_BIN_PATH = Path("/app/scripts/dump_bin.py")
KR_FETCH_PATH = Path("/app/scripts/kr_data_fetch.py")
REDIS_STATUS_KEY = "kr_data_refresh:status"
REDIS_STATUS_TTL_SECONDS = 7 * 24 * 3600


def get_qlib_last_date(qlib_dir: Path = QLIB_DIR) -> date | None:
    """Return the last date in ``calendars/day.txt``, or None if the calendar is absent."""
    cal = qlib_dir / "calendars" / "day.txt"
    if not cal.exists():
        return None
    last = cal.read_text().strip().splitlines()[-1].strip()
    return date.fromisoformat(last)


def refresh_kr_data(today: date | None = None, qlib_dir: Path = QLIB_DIR) -> dict:
    """Fetch + dump_update so kr_data extends through ``today``.

    Returns a result dict the caller can log or stash in redis. Raises
    ``RuntimeError`` if fetch/dump returns no usable rows or dump_bin fails —
    the caller (Celery task) should let that propagate so retries kick in.
    """
    today = today or date.today()
    last_date = get_qlib_last_date(qlib_dir)
    if last_date is None:
        result = {"status": "skipped", "reason": "no_calendar", "today": today.isoformat()}
        _set_status(result)
        return result
    if last_date >= today:
        result = {
            "status": "skipped",
            "reason": "up_to_date",
            "last_date": last_date.isoformat(),
            "today": today.isoformat(),
        }
        _set_status(result)
        return result

    # Overlap one day so yfinance returns the last existing bar — dump_update
    # ignores duplicate dates but the extra row guards against off-by-one.
    fetch_start = last_date
    fetch_end = today + timedelta(days=1)  # yfinance end is exclusive

    fetched, skipped = _fetch_universe(fetch_start, fetch_end, CSV_STAGING)
    if fetched == 0:
        raise RuntimeError(
            f"refresh_kr_data: yfinance returned 0 tickers for {fetch_start}..{fetch_end} — "
            "likely network failure or all tickers rate-limited"
        )

    # ORDER MATTERS: dump_update decides what to append by comparing CSV dates
    # against each stock's end date in instruments/all.txt. Extending the
    # instruments files BEFORE the dump (the old order) made every stock look
    # already-current, so the dump appended nothing while the calendar kept
    # growing — feature bins froze and live_signal died on empty predictions.
    ok = _run_dump_update(CSV_STAGING, qlib_dir)
    if not ok:
        raise RuntimeError("refresh_kr_data: dump_bin.py dump_update returned non-zero")

    # Only AFTER the dump has appended may the market membership files
    # (kospi200.txt etc. — dump_update itself only rewrites all.txt) be
    # extended to the fetched range.
    _update_instruments_files(CSV_STAGING)

    new_last = get_qlib_last_date(qlib_dir)
    new_calendar_days = (new_last - last_date).days if new_last else 0
    result = {
        "status": "ok",
        "previous_last_date": last_date.isoformat(),
        "new_last_date": new_last.isoformat() if new_last else None,
        "today": today.isoformat(),
        "fetched_tickers": fetched,
        "skipped_tickers": skipped,
        "new_calendar_days": new_calendar_days,
    }
    _set_status(result)
    log.info("refresh_kr_data: %s", result)
    return result


def _fetch_universe(start: date, end: date, csv_dir: Path) -> tuple[int, int]:
    """Wipe csv_dir then re-fetch the universe via scripts.kr_data_fetch helpers.

    Fetch ONLY — instruments files must not be touched here, or dump_update
    (which runs later and compares against them) sees every stock as current
    and appends nothing. See _update_instruments_files.
    """
    if csv_dir.exists():
        shutil.rmtree(csv_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)

    # Lazy import keeps yfinance off the API import path (only worker pulls it in).
    sys.path.insert(0, "/app")
    from scripts.kr_data_fetch import fetch_market_data  # noqa: E402
    from app.api.core.kr_universes import MARKETS  # noqa: E402

    codes = MARKETS["kr_all"]
    fetched = fetch_market_data(codes, start.isoformat(), end.isoformat(), csv_dir, delay=0.15)
    skipped = len(codes) - fetched
    return fetched, skipped


def _update_instruments_files(csv_dir: Path) -> None:
    """Extend membership spans AFTER dump_update appended the feature rows.

    merge=True: extend membership end-dates only. A plain regenerate from the
    2-day incremental CSVs used to clobber every span down to the fetch
    window, emptying train/valid slices for the next live_signal.
    """
    sys.path.insert(0, "/app")
    from scripts.kr_data_fetch import generate_instruments_files  # noqa: E402
    from app.api.core.kr_universes import MARKETS  # noqa: E402

    market_codes = {m: c for m, c in MARKETS.items()}
    generate_instruments_files(market_codes, csv_dir, QLIB_DIR, merge=True)


def _run_dump_update(csv_dir: Path, qlib_dir: Path) -> bool:
    cmd = [
        sys.executable, str(DUMP_BIN_PATH), "dump_update",
        "--data_path", str(csv_dir),
        "--qlib_dir", str(qlib_dir),
        "--freq", "day",
        "--date_field_name", "date",
        "--symbol_field_name", "symbol",
        "--include_fields", "open,high,low,close,volume",
    ]
    log.info("refresh_kr_data: %s", " ".join(cmd))
    return subprocess.run(cmd, check=False).returncode == 0


def _set_status(payload: dict) -> None:
    """Write the latest refresh outcome to redis with a 7-day TTL.

    Best-effort: redis failure logs but does NOT raise (we don't want
    observability to block the actual data refresh).
    """
    try:
        import redis  # type: ignore[import-not-found]

        client = redis.from_url(settings.celery_broker_url)
        payload_with_ts = {**payload, "ts": datetime.utcnow().isoformat() + "Z"}
        client.set(REDIS_STATUS_KEY, json.dumps(payload_with_ts), ex=REDIS_STATUS_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        log.warning("refresh_kr_data: redis status set failed: %s", exc)
