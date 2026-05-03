#!/usr/bin/env python3
"""Fetch Korean stock market data and convert to qlib binary format.

Usage:
    # Default: KOSPI200 + KOSDAQ150 + KODEX 200 ETF, 2023-01-01 to today
    python scripts/kr_data_fetch.py

    # Specific market only
    python scripts/kr_data_fetch.py --market kospi200 --start 2023-01-01

    # Specific tickers
    python scripts/kr_data_fetch.py --tickers 005930,000660,069500 --start 2020-01-01

    # Custom output directory
    python scripts/kr_data_fetch.py --qlib_dir ~/.qlib/qlib_data/kr_data
"""

import argparse
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("yfinance is required: pip install yfinance")
    sys.exit(1)

# Make `app.api.core.kr_universes` importable when running from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.api.core.kr_universes import (  # noqa: E402
    KOSPI200,
    KOSDAQ150,
    KOSPI200_BENCHMARK,
    MARKETS,
)


def kr_code_to_yahoo(code: str) -> str:
    """Convert Korean stock code to Yahoo Finance ticker.

    KOSPI/KODEX listings use .KS, KOSDAQ uses .KQ. We try .KS first and the
    caller falls back to .KQ on miss; in practice yfinance accepts both for
    the codes in our universe and we hardcode based on KOSDAQ150 membership.
    """
    code = code.strip()
    if code.endswith(".KS") or code.endswith(".KQ"):
        return code
    suffix = ".KQ" if code in set(KOSDAQ150) else ".KS"
    return f"{code}{suffix}"


def fetch_stock_data(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Fetch OHLCV data from Yahoo Finance for a single ticker."""
    try:
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        )
        df = df[["open", "high", "low", "close", "volume"]]
        df.index.name = "date"
        return df
    except Exception as e:
        print(f"  Error fetching {ticker}: {e}")
        return None


def fetch_market_data(codes: list[str], start: str, end: str, output_dir: Path, delay: float = 0.3) -> int:
    """Fetch data for all codes and save as CSV files ready for dump_bin.py."""
    output_dir.mkdir(parents=True, exist_ok=True)
    success_count = 0
    total = len(codes)

    for i, code in enumerate(codes, 1):
        yahoo_ticker = kr_code_to_yahoo(code)
        print(f"[{i}/{total}] Fetching {code} ({yahoo_ticker})...", end=" ", flush=True)

        df = fetch_stock_data(yahoo_ticker, start, end)
        # Fallback: if .KS comes back empty, try .KQ (and vice versa).
        if (df is None or df.empty) and yahoo_ticker.endswith(".KS"):
            alt = f"{code}.KQ"
            print(f"retry as {alt}...", end=" ", flush=True)
            df = fetch_stock_data(alt, start, end)
        if (df is None or df.empty) and yahoo_ticker.endswith(".KQ"):
            alt = f"{code}.KS"
            print(f"retry as {alt}...", end=" ", flush=True)
            df = fetch_stock_data(alt, start, end)

        if df is None or df.empty:
            print("SKIP (no data)")
            continue

        df["symbol"] = code
        df = df.reset_index()
        csv_path = output_dir / f"{code}.csv"
        df.to_csv(csv_path, index=False)
        print(f"OK ({len(df)} rows)")
        success_count += 1

        if delay > 0 and i < total:
            time.sleep(delay)

    print(f"\nDone: {success_count}/{total} stocks fetched -> {output_dir}")
    return success_count


def generate_instruments_files(
    market_codes: dict[str, list[str]],
    csv_dir: Path,
    qlib_dir: Path,
) -> None:
    """Write `all.txt` plus one `<market>.txt` per market.

    `all.txt` is the union of every market we fetched (so qlib's calendar
    spans every code we have data for); each market file lists only its own
    constituents, letting backtests filter by `kospi200` / `kosdaq150`.
    """
    instruments_dir = qlib_dir / "instruments"
    instruments_dir.mkdir(parents=True, exist_ok=True)

    def _entries(codes: list[str]) -> list[str]:
        out = []
        for code in sorted(set(codes)):
            csv_path = csv_dir / f"{code}.csv"
            if not csv_path.exists():
                continue
            df = pd.read_csv(csv_path)
            if df.empty or "date" not in df.columns:
                continue
            out.append(f"{code}\t{df['date'].min()}\t{df['date'].max()}")
        return out

    all_codes: list[str] = []
    for codes in market_codes.values():
        all_codes.extend(codes)
    all_path = instruments_dir / "all.txt"
    all_path.write_text("\n".join(_entries(all_codes)) + "\n")
    print(f"Generated {all_path}")

    for market, codes in market_codes.items():
        market_path = instruments_dir / f"{market}.txt"
        market_path.write_text("\n".join(_entries(codes)) + "\n")
        print(f"Generated {market_path}")


def run_dump_bin(csv_dir: Path, qlib_dir: Path) -> bool:
    """Invoke scripts/dump_bin.py to convert CSVs to qlib binary format."""
    dump_bin = Path(__file__).resolve().parent / "dump_bin.py"
    cmd = [
        sys.executable, str(dump_bin), "dump_all",
        "--data_path", str(csv_dir),
        "--qlib_dir", str(qlib_dir),
        "--freq", "day",
        "--date_field_name", "date",
        "--symbol_field_name", "symbol",
        "--include_fields", "open,high,low,close,volume",
    ]
    print("\n=== Running dump_bin ===")
    print(" ".join(cmd))
    result = subprocess.run(cmd, check=False)
    return result.returncode == 0


def main():
    today = date.today().isoformat()

    parser = argparse.ArgumentParser(description="Fetch Korean stock data for qlib")
    parser.add_argument(
        "--market",
        type=str,
        default="kr_all",
        choices=list(MARKETS.keys()),
        help="Predefined market list (default: kr_all = KOSPI200 + KOSDAQ150 + KODEX 200 ETF)",
    )
    parser.add_argument("--tickers", type=str, default=None,
                        help="Comma-separated Korean stock codes (overrides --market)")
    parser.add_argument("--start", type=str, default="2023-01-01", help="Start date (default: 2023-01-01)")
    parser.add_argument("--end", type=str, default=today, help=f"End date (default: today = {today})")
    parser.add_argument("--csv_dir", type=str, default="./data/kr_csv",
                        help="Directory to save raw CSV files")
    parser.add_argument("--qlib_dir", type=str, default="~/.qlib/qlib_data/kr_data",
                        help="Output directory for qlib binary data")
    parser.add_argument("--skip_fetch", action="store_true",
                        help="Skip fetching, only run dump_bin on existing CSVs")
    parser.add_argument("--skip_dump", action="store_true",
                        help="Skip dump_bin, only fetch CSVs")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Delay between API calls (seconds)")
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir).resolve()
    qlib_dir = Path(args.qlib_dir).expanduser().resolve()

    if args.tickers:
        codes = [t.strip() for t in args.tickers.split(",") if t.strip()]
        market_groups = {"custom": codes}
        all_to_fetch = codes
    else:
        # Always include the benchmark ETF so backtests have something to compare against.
        primary = MARKETS[args.market]
        market_groups = {args.market: primary}
        # Also persist canonical kospi200 / kosdaq150 sub-lists if we're doing kr_all.
        if args.market == "kr_all":
            market_groups = {
                "kospi200": KOSPI200,
                "kosdaq150": KOSDAQ150,
                "kr_all": MARKETS["kr_all"],
            }
        all_to_fetch = list(dict.fromkeys(primary + [KOSPI200_BENCHMARK]))

    if not args.skip_fetch:
        print(f"=== Fetching {len(all_to_fetch)} symbols from {args.start} to {args.end} ===")
        fetch_market_data(all_to_fetch, args.start, args.end, csv_dir, delay=args.delay)

    print("\n=== Writing instruments files ===")
    generate_instruments_files(market_groups, csv_dir, qlib_dir)

    if not args.skip_dump:
        ok = run_dump_bin(csv_dir, qlib_dir)
        if not ok:
            print("\nWARN: dump_bin returned non-zero. CSVs are still in place; rerun with --skip_fetch to retry.")
            sys.exit(1)
        print(f"\nqlib data ready at {qlib_dir}")
        print("To use:  qlib.init(provider_uri='%s', region='kr')" % qlib_dir)
    else:
        print("\nSkipped dump_bin. To convert manually:")
        print(f"  python scripts/dump_bin.py dump_all --csv_path {csv_dir} --qlib_dir {qlib_dir} "
              "--freq day --date_field_name date --symbol_field_name symbol "
              "--include_fields open,high,low,close,volume")


if __name__ == "__main__":
    main()
