#!/usr/bin/env python3
"""Schema drift inspector for the live trading SQLite DB.

Compares ``app.api.db.models`` against the actual SQLite file on disk and prints
idempotent ALTER TABLE / CREATE INDEX statements you can paste into
``sqlite3 live.sqlite``. Nothing is executed — review and run yourself.

Usage:
    # use the URL from app.api.config.settings.live_db_url (default)
    python scripts/inspect_db_schema.py

    # or override
    python scripts/inspect_db_schema.py sqlite:////home/qlib/data/db/live.sqlite
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects import sqlite as _sqlite_dialect  # noqa: E402

from app.api.db import models  # noqa: E402,F401 — registers tables on Base
from app.api.db.session import Base  # noqa: E402

_DIALECT = _sqlite_dialect.dialect()


def _sqlite_type(col) -> str:
    return col.type.compile(dialect=_DIALECT)


def _default_sql(col) -> str | None:
    if col.default is None:
        return None
    arg = col.default.arg
    if callable(arg):
        # e.g. datetime.utcnow — closest SQLite analog
        return "CURRENT_TIMESTAMP"
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    return None


def _alter_for(table_name: str, col) -> tuple[str, str | None]:
    """Return (statement, warning)."""
    parts = [f"ALTER TABLE {table_name} ADD COLUMN {col.name} {_sqlite_type(col)}"]
    if not col.nullable:
        parts.append("NOT NULL")
    default = _default_sql(col)
    if default is not None:
        parts.append(f"DEFAULT {default}")
    warning = None
    if (not col.nullable) and default is None:
        warning = (
            f"column {col.name!r} is NOT NULL with no derivable DEFAULT — "
            "fill existing rows manually or amend the model before applying"
        )
    return " ".join(parts) + ";", warning


def inspect(db_url: str) -> int:
    if not db_url.startswith("sqlite"):
        print(f"!! db url {db_url!r} is not sqlite — this script is sqlite-only.")
        return 2

    db_path = db_url.replace("sqlite:///", "")
    print(f"# Inspecting: {db_path}")
    print(f"# Model tables: {sorted(Base.metadata.tables.keys())}")
    print()

    if not Path(db_path).exists():
        print(
            f"!! DB file {db_path} does not exist.\n"
            "!! Run `python -c 'from app.api.db.session import init_db; init_db()'` "
            "to create it from scratch."
        )
        return 1

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    existing_tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    existing_idx = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    issues = 0
    apply_lines: list[str] = []

    for table_name, table in sorted(Base.metadata.tables.items()):
        print(f"## {table_name}")
        if table_name not in existing_tables:
            print("  ⚠️  table missing — will be created on next init_db()")
            issues += 1
            print()
            continue

        cur.execute(f"PRAGMA table_info({table_name})")
        existing_cols = {r[1] for r in cur.fetchall()}
        missing_cols = [c for c in table.columns if c.name not in existing_cols]

        if missing_cols:
            print(f"  ⚠️  {len(missing_cols)} missing column(s):")
            for col in missing_cols:
                stmt, warning = _alter_for(table_name, col)
                if warning:
                    print(f"    -- {warning}")
                print(f"    {stmt}")
                apply_lines.append(stmt)
                issues += 1
        else:
            print("  ✅ all columns present")

        model_idx_names = {idx.name for idx in table.indexes if idx.name}
        missing_idx = sorted(model_idx_names - existing_idx)
        for idx_name in missing_idx:
            idx = next(i for i in table.indexes if i.name == idx_name)
            cols_sql = ", ".join(c.name for c in idx.columns)
            unique = "UNIQUE " if idx.unique else ""
            stmt = f"CREATE {unique}INDEX IF NOT EXISTS {idx_name} ON {table_name}({cols_sql});"
            print(f"    {stmt}")
            apply_lines.append(stmt)
            issues += 1

        # UniqueConstraints cannot be added via ALTER in SQLite — flag only.
        from sqlalchemy import UniqueConstraint
        for cons in table.constraints:
            if isinstance(cons, UniqueConstraint) and cons.name:
                # SQLite stores UNIQUE constraint indexes with the constraint's
                # auto-generated name (sqlite_autoindex_*), so the named
                # constraint won't appear in existing_idx. Just remind the
                # operator to verify.
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                    (table_name,),
                )
                tbl_idx = [r[0] for r in cur.fetchall()]
                if cons.name not in tbl_idx:
                    print(
                        f"    -- UniqueConstraint {cons.name!r} not present. "
                        "SQLite cannot add via ALTER — rebuild table if required."
                    )
        print()

    conn.close()

    if issues == 0:
        print("# ✅ Schema matches models. Nothing to apply.")
        return 0

    print(f"# 🔧 {issues} issue(s) detected. Review the statements above.")
    print("#")
    print("# Recommended procedure on the prod host:")
    print(f"#   1) cd {Path(db_path).parent}")
    print(f"#   2) cp {Path(db_path).name} {Path(db_path).name}.bak.$(date +%Y%m%d_%H%M%S)")
    print("#   3) docker stop qlib_api_blue qlib_worker_blue qlib_scheduler_blue")
    print(f"#   4) sqlite3 {Path(db_path).name} <<'EOF'")
    for line in apply_lines:
        print(f"#        {line}")
    print("#      EOF")
    print("#   5) docker start qlib_api_blue qlib_worker_blue qlib_scheduler_blue")
    print("#   6) curl -s http://localhost:25023/api/v1/health | jq .")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg is None:
        try:
            from app.api.config import settings  # noqa: E402

            arg = settings.live_db_url
        except Exception as e:
            print(f"!! could not import settings ({e})")
            print(
                "!! pass DB URL explicitly: "
                "python scripts/inspect_db_schema.py sqlite:////path/to/live.sqlite"
            )
            sys.exit(2)
    sys.exit(inspect(arg))
