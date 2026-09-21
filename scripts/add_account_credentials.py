"""`trading_accounts` 에 KIS 자격증명 칼럼을 더한다 (2026-09-21).

왜 스크립트가 필요한가: 이 저장소에는 Alembic 이 없고 `init_db()` 는
`Base.metadata.create_all()` 만 부른다. 그건 **없는 테이블을 만들 뿐 기존 테이블을
ALTER 하지 못한다.** 그래서 모델에 칼럼을 더하면 이미 존재하는 DB 는 그대로 남고,
쿼리가 `no such column` 으로 죽는다(실제로 개발용 SQLite 에서 그렇게 터졌다).

대상이 둘이다:
  - 운영 PostgreSQL(`qlib_live`) — 계좌를 웹에서 등록하려면 필요
  - 저장소의 개발/테스트용 SQLite(`app/api/db/live.sqlite`) — 테스트가 이 파일을 쓴다

둘 다 `ALTER TABLE ... ADD COLUMN` 을 지원하므로 같은 스크립트로 올린다.
**멱등**하다 — 이미 있는 칼럼은 건너뛴다. 여러 번 돌려도 안전하다.

사용법
-----
    # 무엇을 할지 보기만 (기본값)
    python scripts/add_account_credentials.py --url sqlite:///./app/api/db/live.sqlite

    # 실제 적용
    python scripts/add_account_credentials.py --url <URL> --execute
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

TABLE = "trading_accounts"

# (칼럼명, SQL 타입). 타입은 SQLite·PostgreSQL 양쪽에서 통하는 표기로 적는다.
# Boolean 기본값은 DB 마다 리터럴이 다르므로 NOT NULL 을 걸지 않고 애플리케이션
# 기본값(True)에 맡긴다 — 기존 행은 NULL 이 되지만 읽는 쪽이 `is not False` 로 본다.
COLUMNS: list[tuple[str, str]] = [
    ("kis_env", "VARCHAR(8)"),
    ("account_no", "VARCHAR(16)"),
    ("account_product", "VARCHAR(4)"),
    ("app_key_enc", "TEXT"),
    ("app_secret_enc", "TEXT"),
    ("enabled", "BOOLEAN"),
    ("note", "VARCHAR(200)"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="SQLAlchemy URL")
    ap.add_argument("--execute", action="store_true",
                    help="실제로 ALTER 한다. 기본은 드라이런")
    args = ap.parse_args()

    engine = create_engine(args.url)
    print(f"target : {engine.url.render_as_string(hide_password=True)}")
    print(f"mode   : {'EXECUTE' if args.execute else 'DRY RUN'}\n")

    insp = inspect(engine)
    if TABLE not in insp.get_table_names():
        print(f"ABORT: {TABLE} 테이블이 없다 — init_db() 를 먼저 돌릴 것.")
        return 1

    existing = {c["name"] for c in insp.get_columns(TABLE)}
    todo = [(n, t) for n, t in COLUMNS if n not in existing]

    for name, sql_type in COLUMNS:
        mark = "있음(건너뜀)" if name in existing else "추가"
        print(f"  {name:20s} {sql_type:14s} {mark}")

    if not todo:
        print("\n이미 전부 있다 — 할 일 없음.")
        return 0

    if not args.execute:
        print(f"\n드라이런. {len(todo)}개를 추가하려면 --execute 를 붙일 것.")
        return 0

    with engine.begin() as conn:
        for name, sql_type in todo:
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {name} {sql_type}"))
            print(f"  + {name}")
        # 기존 행은 enabled 가 NULL 이 된다. 지금 쓰고 있는 계좌를 꺼진 것으로
        # 오인하지 않도록 명시적으로 켠다.
        conn.execute(text(f"UPDATE {TABLE} SET enabled = TRUE WHERE enabled IS NULL"))

    print(f"\n{len(todo)}개 추가 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
