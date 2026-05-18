"""Trading-day windowing for the live signal task.

Regression for: pre-fix `_walk_forward_windows(today=Monday)` returned
`valid_end = Sunday`, which fed an empty test slice to the model and emitted
`picks=0` every day silently.

These dates are chosen so the calendar-based path (when kr_data is loaded)
and the weekday-fallback path (when it isn't) produce the same answer — no
KRX holiday falls in any of the chosen windows.
"""

from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.api.services import live_trader  # noqa: E402


def test_prev_trading_day_monday_returns_friday():
    assert live_trader._prev_trading_day(date(2026, 5, 18)) == date(2026, 5, 15)


def test_prev_trading_day_tuesday_returns_monday():
    assert live_trader._prev_trading_day(date(2026, 5, 19)) == date(2026, 5, 18)


def test_prev_trading_day_saturday_returns_friday():
    assert live_trader._prev_trading_day(date(2026, 5, 16)) == date(2026, 5, 15)


def test_walk_forward_windows_valid_end_is_weekday():
    """valid_end must be a Mon-Fri date strictly before today for any input."""
    for offset in range(7):
        today = date(2026, 5, 18 + offset)
        _train_end, _valid_start, valid_end = live_trader._walk_forward_windows(today)
        assert valid_end < today, f"today={today}: valid_end {valid_end} not < today"
        assert valid_end.weekday() < 5, (
            f"today={today}: valid_end={valid_end} fell on weekday {valid_end.weekday()}"
        )


def test_walk_forward_windows_monday_picks_prior_friday():
    today = date(2026, 5, 18)  # Monday — the date this regression was found on
    _train_end, _valid_start, valid_end = live_trader._walk_forward_windows(today)
    assert valid_end == date(2026, 5, 15), (
        f"Monday today should snap valid_end to prior Friday, got {valid_end}"
    )


def test_last_trading_day_weekend_returns_friday():
    # Sunday 2026-05-17 → Friday 2026-05-15
    assert live_trader._last_trading_day(date(2026, 5, 17)) == date(2026, 5, 15)
