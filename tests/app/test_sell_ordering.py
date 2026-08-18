"""Which holdings get sold when more than `n_drop` fall out of the top-K.

2026-08-18: all four holdings had dropped out and only two could be sold that
session. The choice came from iterating a `set`:

    held_codes = {h.code for h in snapshot.holdings}          # :459
    to_sell_codes = [c for c in held_codes
                     if c not in set(target_codes)][:n_drop]  # :468

so it followed hash order — not rank, not pnl, not holding period. The same
inputs produced 088350/010950 on one run and 010950/042700 on another. Real
money moved on an arbitrary tiebreak, and with ten accounts each could shed a
different name, turning cross-account comparison into a measurement of that
arbitrariness.

Worst-rank-first is what TopkDropout means: let go of what the model likes
least. The code tiebreak makes equal ranks reproducible.
"""

from datetime import date

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.db import Base, Signal  # noqa: E402
from app.api.services import live_trader as lt  # noqa: E402

AS_OF = date(2026, 8, 18)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def _rank(session, code, rank, as_of=AS_OF):
    session.add(Signal(as_of=as_of, rank=rank, code=code, score=1.0 / rank,
                       model_class="LGBModel", strategy_class="TopkDropoutStrategy"))
    session.commit()


def test_worst_ranked_holdings_are_sold_first(session):
    _rank(session, "AAA", 12)
    _rank(session, "BBB", 15)
    _rank(session, "CCC", 25)
    # DDD is absent from the stored 30 ranks entirely — the worst case.

    picked = lt._rank_sorted_sells(session, AS_OF, {"AAA", "BBB", "CCC", "DDD"}, 2)

    # DDD(30위권 밖) → CCC(25위) 순. 순서까지 결정적이어야 한다.
    assert picked == ["DDD", "CCC"], picked


def test_unranked_is_worse_than_any_recorded_rank(session):
    _rank(session, "AAA", lt.SIGNAL_STORE_TOP_N)   # 30위 — 기록된 최하위

    picked = lt._rank_sorted_sells(session, AS_OF, {"AAA", "ZZZ"}, 1)

    assert picked == ["ZZZ"], "30위권 밖이 30위보다 나빠야 한다"


def test_result_is_reproducible(session):
    """The defect: a set's iteration order decided this."""
    for code, rank in (("AAA", 12), ("BBB", 15), ("CCC", 25)):
        _rank(session, code, rank)
    held = {"AAA", "BBB", "CCC", "DDD"}

    runs = {tuple(lt._rank_sorted_sells(session, AS_OF, set(held), 2)) for _ in range(50)}

    assert len(runs) == 1, f"같은 입력에 결과가 갈렸다: {runs}"


def test_equal_ranks_break_by_code(session):
    """Two names can share a rank across as_of rows; still need one answer."""
    picked = lt._rank_sorted_sells(session, AS_OF, {"ZZZ", "AAA", "MMM"}, 2)

    assert picked == ["AAA", "MMM"], "동순위는 종목코드 순"


def test_selling_fewer_than_n_drop_is_fine(session):
    _rank(session, "AAA", 12)

    assert lt._rank_sorted_sells(session, AS_OF, {"AAA"}, 2) == ["AAA"]
    assert lt._rank_sorted_sells(session, AS_OF, set(), 2) == []


def test_today_rank_reads_the_right_day(session):
    """Yesterday's rank must not leak into today's ordering."""
    _rank(session, "AAA", 1, as_of=date(2026, 8, 17))   # 어제는 1위
    _rank(session, "AAA", 28)                            # 오늘은 28위

    assert lt._today_rank(session, AS_OF, "AAA") == 28


def test_the_2026_08_18_case(session):
    """The live scenario: four holdings out, two slots.

    Ranks stand in for the real ones; the point is that the answer is now
    determined by them instead of by hash order.
    """
    _rank(session, "010950", 22)   # S-Oil
    _rank(session, "042700", 18)   # 한미반도체
    _rank(session, "078930", 27)   # GS
    # 088350 한화생명 — 30위권 밖

    picked = lt._rank_sorted_sells(
        session, AS_OF, {"010950", "042700", "078930", "088350"}, 2)

    # 088350은 30위권 밖이라 27위인 078930보다 먼저 나간다.
    assert picked == ["088350", "078930"], picked
