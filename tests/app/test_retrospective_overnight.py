"""H-오버나이트 — overnight slice of a rank-dropout exit.

The user's "sell before the close on a 15:00 provisional signal" idea is
worth money only if dropped stocks fall overnight (prev close → next-morning
fill). enrich_episode computes that slice per closed episode; scoreboard
aggregates it into the H-오버나이트 hypothesis, counting `open` episodes only
(sim strategies fill at bar levels, so their number is not an overnight move).
"""

import pytest

pytest.importorskip("sqlalchemy")

from app.api.services import retrospective  # noqa: E402


def _episode(**kw) -> dict:
    base = dict(code="008930", name="한미사이언스", strategy="open",
                entry_date="2026-08-11", exit_date="2026-08-12",
                avg=98_000.0, exit_px=97_000.0, qty=10, ret_pct=-1.02,
                entry_metrics={}, entry_basis="신호 1위 진입")
    base.update(kw)
    return base


def test_enrich_computes_overnight_from_prev_close(monkeypatch):
    monkeypatch.setattr(retrospective, "_close_series", lambda code: {
        "2026-08-11": 100_000.0,  # signal-day close
        "2026-08-12": 95_000.0,
    })
    ep = retrospective.enrich_episode(_episode(exit_px=97_000.0))
    # Sold at 97,000 next morning vs 100,000 prev close → −3% overnight.
    assert ep["overnight_pct"] == pytest.approx(-3.0)


def test_enrich_without_prior_close_leaves_field_absent(monkeypatch):
    monkeypatch.setattr(retrospective, "_close_series", lambda code: {
        "2026-08-12": 95_000.0,  # nothing before the exit date
    })
    ep = retrospective.enrich_episode(_episode())
    assert "overnight_pct" not in ep


def test_scoreboard_counts_open_episodes_only():
    eps = [
        _episode(overnight_pct=-3.0),                      # open, gap down
        _episode(code="000660", overnight_pct=1.5),        # open, gap up
        _episode(code="078930", strategy="close", overnight_pct=-9.9),  # sim — excluded
        _episode(code="005930", overnight_pct=None),       # missing — excluded
    ]
    row = next(r for r in retrospective.scoreboard(eps) if r["key"] == "H-오버나이트")
    assert row["support"] == 1   # one genuine overnight drop
    assert row["refute"] == 1    # one overnight rise
    assert "2건" in row["evidence"]
