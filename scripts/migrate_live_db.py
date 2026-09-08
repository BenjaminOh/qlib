"""Copy the live-trading DB into another SQLAlchemy URL (SQLite → PostgreSQL).

Why this exists: there is no Alembic in this repo — `init_db()` only calls
`Base.metadata.create_all()`, which creates missing tables but can never ALTER
an existing one. The 2026-08-17 multi-account change adds `account_id` to four
tables and widens two unique constraints, so the existing SQLite file cannot be
upgraded in place. Moving to PostgreSQL at the same time turns that limitation
into the migration path: the target starts empty, `create_all()` builds the new
shape, and this script carries the rows across.

PostgreSQL is also the point of the exercise. Ten accounts ordering at 09:00
means ten concurrent writers; SQLite's only defence is `busy_timeout=5000` and
there is no write-retry anywhere in the codebase, while `live_orders` carries no
autoretry on purpose (orders are not idempotent). A lock timeout there loses
that account's session outright.

Usage
-----
    # dry run — reports what WOULD move, touches nothing
    python scripts/migrate_live_db.py \
        --source sqlite:////app/db/live.sqlite \
        --target postgresql+psycopg2://user:pw@host:5432/qlib_live

    # do it
    python scripts/migrate_live_db.py --source ... --target ... --execute

The target must be empty (or `--allow-nonempty`). Re-running against a populated
target would duplicate every row: rows are copied with fresh primary keys, so
there is no natural idempotency to fall back on.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import (
    Date, DateTime, create_engine, func, inspect, select, text,
)  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db.models import (  # noqa: E402
    DEFAULT_ACCOUNT_ID, CafeCandidate, CafeScout, DailyPnL, Fill, MarketFlow,
    MarketPoolSnapshot, Order, OrderbookSnapshot, PositionSnapshot, Signal,
    SurgePick, User,
)
from app.api.db.session import Base  # noqa: E402

# Order matters: Order before Fill (FK), everything else is independent.
# Fill is handled separately because its order_id has to be remapped.
TABLES = [User, Signal, MarketFlow, CafeCandidate, CafeScout,
          MarketPoolSnapshot, SurgePick, OrderbookSnapshot,
          PositionSnapshot, DailyPnL, Order]

# Columns the source may not have — the whole reason for the migration.
ACCOUNT_TABLES = {Order, Fill, PositionSnapshot, DailyPnL}


def _read(session, engine, model):
    """Rows from the SOURCE, read through its own schema.

    Deliberately not `select(model)`: the ORM would list every column the NEW
    model declares, and the source predates `account_id` — SQLAlchemy emits
    `no such column: position_snapshots.account_id` and the migration dies
    before it starts. Reflect what the source actually has and select that.
    """
    table = model.__tablename__
    src_cols = {c["name"] for c in inspect(engine).get_columns(table)}
    tgt_cols = [c.name for c in model.__table__.columns]
    shared = [c for c in tgt_cols if c in src_cols]
    quoted = ", ".join(f'"{c}"' for c in shared)
    rows = session.execute(text(f'SELECT {quoted} FROM "{table}"')).mappings().all()
    return rows, shared


def _coerce(value, col_type):
    """Rebuild a date/datetime that raw SQL handed back as text.

    Reading with `text()` bypasses SQLAlchemy's type layer, and SQLite stores
    dates as strings — the ORM insert then refuses them ("SQLite DateTime type
    only accepts Python datetime and date objects"). PostgreSQL is stricter
    still, so this has to happen regardless of the target.
    """
    if not isinstance(value, str):
        return value
    if isinstance(col_type, DateTime):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return value
    if isinstance(col_type, Date):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return value
    return value


def _row_to_dict(row, model):
    """Copy a row, dropping the PK and defaulting a missing account_id.

    Every pre-migration row belongs to the one account the system ran on, so
    they all take DEFAULT_ACCOUNT_ID.
    """
    types = {c.name: c.type for c in model.__table__.columns}
    out = {k: _coerce(v, types.get(k)) for k, v in row.items() if k != "id"}
    if model in ACCOUNT_TABLES and not out.get("account_id"):
        out["account_id"] = DEFAULT_ACCOUNT_ID
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="source SQLAlchemy URL")
    ap.add_argument("--target", required=True, help="target SQLAlchemy URL")
    ap.add_argument("--execute", action="store_true",
                    help="actually write; default is a dry run")
    ap.add_argument("--allow-nonempty", action="store_true",
                    help="permit a target that already has rows (will duplicate)")
    args = ap.parse_args()

    src_engine = create_engine(args.source)
    tgt_engine = create_engine(args.target)
    Src = sessionmaker(bind=src_engine)
    Tgt = sessionmaker(bind=tgt_engine)

    print(f"source : {src_engine.url.render_as_string(hide_password=True)}")
    print(f"target : {tgt_engine.url.render_as_string(hide_password=True)}")
    print(f"mode   : {'EXECUTE' if args.execute else 'DRY RUN'}\n")

    if args.execute:
        Base.metadata.create_all(bind=tgt_engine)

    # Guard before any write: a second run would silently double every row.
    if args.execute and not args.allow_nonempty:
        with Tgt() as t:
            for model in TABLES + [Fill]:
                try:
                    n = t.execute(select(func.count()).select_from(model)).scalar() or 0
                except Exception:
                    n = 0
                if n:
                    print(f"ABORT: target {model.__tablename__} already has {n} rows.\n"
                          f"       Re-running would duplicate them (PKs are reassigned).\n"
                          f"       Use --allow-nonempty only if that is what you want.")
                    return 1

    total = 0
    order_id_map: dict[int, int] = {}

    with Src() as s, Tgt() as t:
        for model in TABLES:
            rows, shared = _read(s, src_engine, model)
            missing = [c.name for c in model.__table__.columns
                       if c.name not in shared and c.name != "id"]
            note = f"  (+{','.join(missing)} defaulted)" if missing else ""
            print(f"{model.__tablename__:24s} {len(rows):6d} rows{note}")
            total += len(rows)
            if not args.execute:
                continue
            for row in rows:
                obj = model(**_row_to_dict(row, model))
                t.add(obj)
                if model is Order:
                    # Fill.order_id points at the OLD pk; remember the new one.
                    t.flush()
                    order_id_map[row["id"]] = obj.id
            t.flush()

        # Fill last — its FK needs the remapped Order ids.
        fills, _ = _read(s, src_engine, Fill)
        print(f"{'fills':24s} {len(fills):6d} rows")
        total += len(fills)
        if args.execute:
            orphans = 0
            for row in fills:
                data = _row_to_dict(row, Fill)
                new_order_id = order_id_map.get(row["order_id"])
                if new_order_id is None:
                    orphans += 1        # order row missing — skip, do not guess
                    continue
                data["order_id"] = new_order_id
                t.add(Fill(**data))
            if orphans:
                print(f"  ! {orphans} fills skipped — their order row was missing")
            t.commit()

    print(f"\n{'moved' if args.execute else 'would move'}: {total} rows")
    if not args.execute:
        print("\nDry run only. Re-run with --execute to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
