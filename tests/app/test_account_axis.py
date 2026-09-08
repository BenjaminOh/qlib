"""The account dimension — what breaks without it.

Before 2026-08-17 the only axis was `strategy`. Two accounts running the same
strategy would therefore:

  * **overwrite each other's snapshot and PnL** — `PositionSnapshot` and
    `DailyPnL` were unique on (date, strategy), so the second account's
    `sync_account` updated the first account's row instead of adding one.
    Silent data loss, and the equity curve of whichever account synced first
    simply vanished.
  * **cross-contaminate realised pnl** — `_episode_avg` replayed every order
    for (strategy, code) regardless of account, pricing account A's sell
    against account B's buys.
  * **share one reconstructed book** — `_simulated_balance` summed all fills
    for a strategy, so ten accounts would each read the same tenfold balance.

`strategy` is String(8) and `cafeopen` uses all eight characters, so the axis
could not be folded into the existing tag — it had to be its own column.
"""

from datetime import date, datetime

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, DailyPnL, Fill, Order, PositionSnapshot  # noqa: E402
from app.api.db.models import DEFAULT_ACCOUNT_ID, STRATEGY_LIMIT  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _stub_prices(monkeypatch):
    monkeypatch.setattr(lt, "_last_close", lambda code: 1000.0)
    monkeypatch.setattr(lt, "_sim_fill_price", lambda code: 1000.0)
    monkeypatch.setattr(lt, "_stock_name", lambda code: f"name-{code}")


def _snapshot(session, account_id, day=date(2026, 8, 17), total=10_000_000.0):
    session.add(PositionSnapshot(
        snapshot_date=day, strategy=STRATEGY_LIMIT, account_id=account_id,
        cash=total, total_eval=total, holdings_json="[]"))
    session.commit()


# ─── the data-loss case ─────────────────────────────────────────────


def test_two_accounts_can_snapshot_the_same_day(session):
    """Previously uq(date, strategy) — the second insert collided."""
    _snapshot(session, "main")
    _snapshot(session, "acct_b")

    rows = session.query(PositionSnapshot).all()
    assert len(rows) == 2
    assert {r.account_id for r in rows} == {"main", "acct_b"}


def test_same_account_still_cannot_duplicate_a_day(session):
    """The constraint must still catch a genuine double-write."""
    _snapshot(session, "main")
    with pytest.raises(IntegrityError):
        _snapshot(session, "main")


def test_two_accounts_can_record_daily_pnl_for_the_same_day(session):
    for acct in ("main", "acct_b"):
        session.add(DailyPnL(trade_date=date(2026, 8, 17), strategy=STRATEGY_LIMIT,
                             account_id=acct, starting_equity=1e7, ending_equity=1e7))
    session.commit()

    assert session.query(DailyPnL).count() == 2


# ─── realised pnl must not cross accounts ───────────────────────────


def _order(session, account_id, side, qty, price, minute):
    o = Order(trade_date=date(2026, 8, 17), strategy=STRATEGY_LIMIT,
              account_id=account_id, code="005930", side=side, qty=qty,
              price=price, ord_dvsn="00", status="FILLED",
              submitted_at=datetime(2026, 8, 17, 9, minute))
    session.add(o)
    session.commit()
    return o


def test_episode_avg_ignores_another_accounts_buys(session):
    """A's sell must price against A's own entry, not B's cheaper one."""
    _order(session, "main", "BUY", 10, 10_000, minute=1)
    _order(session, "acct_b", "BUY", 10, 5_000, minute=2)   # far cheaper, other book
    sell = _order(session, "main", "SELL", 10, 12_000, minute=3)

    avg = lt._episode_avg(session, sell)

    assert avg == 10_000, "다른 계좌의 매수가 평균단가에 섞이면 안 된다"


def test_episode_avg_still_averages_within_one_account(session):
    _order(session, "main", "BUY", 10, 10_000, minute=1)
    _order(session, "main", "BUY", 10, 20_000, minute=2)
    sell = _order(session, "main", "SELL", 5, 30_000, minute=3)

    assert lt._episode_avg(session, sell) == 15_000


# ─── simulated book is per account ──────────────────────────────────


def _sim_fill(session, account_id, side, qty, price):
    lt._persist_simulated_fill(session, date(2026, 8, 17), "005930", side, qty,
                               price, strategy=STRATEGY_LIMIT,
                               account_id=account_id)
    session.commit()


def test_simulated_balance_is_scoped_to_one_account(session):
    _sim_fill(session, "main", "BUY", 10, 1000)
    _sim_fill(session, "acct_b", "BUY", 40, 1000)

    a = lt._simulated_balance(session, strategy=STRATEGY_LIMIT,
                              seed_cash=1_000_000.0, account_id="main")
    b = lt._simulated_balance(session, strategy=STRATEGY_LIMIT,
                              seed_cash=1_000_000.0, account_id="acct_b")

    assert [h.qty for h in a.holdings] == [10]
    assert [h.qty for h in b.holdings] == [40], "계좌 B가 A의 체결까지 합산하면 안 된다"


# ─── defaults keep single-account behaviour intact ──────────────────


def test_writes_default_to_the_original_account(session):
    """Pre-existing callers pass no account and must keep working."""
    lt._persist_simulated_fill(session, date(2026, 8, 17), "005930", "BUY", 1,
                               1000, strategy=STRATEGY_LIMIT)
    session.commit()

    assert session.query(Order).one().account_id == DEFAULT_ACCOUNT_ID
    assert session.query(Fill).one().account_id == DEFAULT_ACCOUNT_ID


# ─── 운영 스키마 괴리를 잊지 않기 위한 가드 ─────────────────────────


def test_account_count_gate_until_the_uq_migration_lands():
    """세 번째 계좌가 생기면 **배포를 막는다.**

    모델의 uq 는 (date, strategy, account_id) 인데 **운영 SQLite 는 아직
    (date, strategy)** 다 (models.py 의 괴리 주석 참조). SQLite 는 unique
    constraint 를 ALTER 로 못 바꾸고 테이블 재작성이 필요해서,
    `scripts/migrate_live_db.py` 로 Postgres 이관과 함께 처리하기로 미뤘다.

    계좌가 둘(main·cafe)인 동안은 무해하다 — cafereal 은 자격증명 미설정이라
    실주문이 0건이고, 같은 (date, strategy) 에 두 행이 생길 일이 없다.

    세 번째 계좌는 다르다. 그 계좌의 첫 `sync_account` 가 운영에서
    IntegrityError 로 죽거나, 더 나쁘게는 남의 곡선을 덮어쓴다. 그러니
    **마이그레이션을 끝내고 이 테스트를 갱신하는 것이 계좌 추가의 전제**다.

    이 가드를 지우고 싶어졌다면, 지울 게 아니라 이관을 먼저 하라.
    """
    from app.api.db.models import ACCOUNT_STRATEGIES

    assert len(ACCOUNT_STRATEGIES) <= 2, (
        f"계좌가 {len(ACCOUNT_STRATEGIES)}개다 — 운영 DB 의 uq 가 아직 계좌 축을 "
        "모른다. scripts/migrate_live_db.py 로 이관을 끝낸 뒤 이 상한을 올릴 것."
    )
