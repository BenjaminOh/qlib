"""One-time repair: rebuild kr_data instruments files from feature bin data.

Why: the incremental refresh path used to regenerate instruments membership
from the day's 2-row fetch CSVs, clobbering every stock's span down to a
2-day window (e.g. `000150  2026-07-20  2026-07-21`). That emptied the
train/valid dataset slices and killed live_signal daily. The refresh now
merges spans instead (see kr_data_fetch.generate_instruments_files), but the
on-disk files must be repaired once from ground truth.

Ground truth: each feature bin (`features/<code>/close.day.bin`) stores its
first calendar index as element 0, followed by one float32 per trading day —
so the true membership span is directly recoverable per code.

Usage (inside the api/worker container):
    python scripts/repair_kr_instruments.py --qlib_dir /root/.qlib/qlib_data/kr_data
Add --dry_run to print without writing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def _calendar(qlib_dir: Path) -> list[str]:
    return [
        line.strip()
        for line in (qlib_dir / "calendars" / "day.txt").read_text().strip().splitlines()
        if line.strip()
    ]


def _true_span(feat_dir: Path, cal: list[str]) -> tuple[str, str] | None:
    """Recover (start_date, end_date) for one code from its close bin."""
    bin_file = feat_dir / "close.day.bin"
    if not bin_file.exists():
        candidates = sorted(feat_dir.glob("*.day.bin"))
        if not candidates:
            return None
        bin_file = candidates[0]
    data = np.fromfile(bin_file, dtype=np.float32)
    if len(data) < 2:
        return None
    start_idx = int(data[0])
    n_rows = len(data) - 1
    if start_idx < 0 or start_idx >= len(cal):
        return None
    end_idx = min(start_idx + n_rows - 1, len(cal) - 1)
    return cal[start_idx], cal[end_idx]


def repair(qlib_dir: Path, dry_run: bool = False) -> None:
    cal = _calendar(qlib_dir)
    features_dir = qlib_dir / "features"
    instruments_dir = qlib_dir / "instruments"

    spans: dict[str, tuple[str, str]] = {}
    for feat_dir in sorted(p for p in features_dir.iterdir() if p.is_dir()):
        span = _true_span(feat_dir, cal)
        if span:
            spans[feat_dir.name.upper()] = span

    if not spans:
        sys.exit("repair_kr_instruments: no feature bins found — wrong --qlib_dir?")

    print(f"Recovered spans for {len(spans)} codes "
          f"(calendar {cal[0]}..{cal[-1]}, {len(cal)} days)")

    # Market membership lists (KOSPI200 / KOSDAQ150 / ...) come from the app's
    # hardcoded universes; all.txt is every code we have bins for.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, "/app")
    from app.api.core.kr_universes import MARKETS  # noqa: E402

    def _write(path: Path, codes: list[str]) -> None:
        lines = [
            f"{code}\t{spans[code][0]}\t{spans[code][1]}"
            for code in sorted(set(codes))
            if code in spans
        ]
        preview = f"{path.name}: {len(lines)} codes"
        if lines:
            preview += f" (e.g. {lines[0]})"
        print(("DRY-RUN " if dry_run else "Wrote ") + preview)
        if not dry_run:
            path.write_text("\n".join(lines) + "\n")

    _write(instruments_dir / "all.txt", list(spans))
    for market, codes in MARKETS.items():
        _write(instruments_dir / f"{market}.txt", codes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--qlib_dir", required=True, type=Path)
    parser.add_argument("--dry_run", action="store_true")
    args = parser.parse_args()
    repair(args.qlib_dir.expanduser(), dry_run=args.dry_run)
