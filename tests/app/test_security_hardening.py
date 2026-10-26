"""2026-10-26 보안 점검에서 재현된 구멍들 — 다시 열리면 여기서 걸린다.

  * 관리자 기본 비번(공개 저장소의 placeholder)으로 운영 로그인이 됐다.
    검사가 kis_env=real 에서만 돌았고, 계정은 최초 기동 때 한 번만 만들어져
    .env 를 바꿔도 DB 해시가 그대로였다.
  * 백테스트 요청의 *_module/*_class/*_kwargs 가 그대로 qlib 의 import·호출로
    들어가 로그인 한 번이면 워커(root)에서 명령 실행이 가능했다. grid 의
    param_sweeps 는 스키마 검증 뒤에 아무 키나 덮어쓴다.
  * /data/features 의 fields 는 qlib 이 eval 한다.
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from app.api.core.backtest_guard import (  # noqa: E402
    ALLOWED, UnsafeBacktestConfig, check_backtest_config, check_field,
)
from app.api.schemas.backtest import BacktestRequest  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _cfg(**over):
    cfg = BacktestRequest().model_dump()
    cfg.update(over)
    return cfg


# ─── 백테스트 import 허용 목록 ────────────────────────────────────────

def test_default_request_is_allowed():
    check_backtest_config(_cfg())


@pytest.mark.parametrize("over", [
    dict(model_module="subprocess", model_class="Popen", model_kwargs={"args": ["id"]}),
    dict(model_module="qlib.contrib.model.gbdt", model_class="os"),
    dict(strategy_module="os", strategy_class="system"),
    dict(handler_module="qlib.contrib.data.handler", handler_class="Alpha999"),
])
def test_classes_outside_the_catalog_are_refused(over):
    with pytest.raises(UnsafeBacktestConfig):
        check_backtest_config(_cfg(**over))


@pytest.mark.parametrize("kwargs", [
    {"x": {"class": "Popen", "module_path": "subprocess"}},
    {"x": {"class": "subprocess.Popen"}},
    {"x": {"func": "system"}},
    {"processors": [{"class": "_Private"}]},
    {"x": "subprocess.Popen"},
    {"deep": [{"a": ["os.system"]}]},
])
def test_kwargs_cannot_smuggle_an_import(kwargs):
    with pytest.raises(UnsafeBacktestConfig):
        check_backtest_config(_cfg(handler_kwargs=kwargs))


def test_ordinary_kwargs_pass():
    check_backtest_config(_cfg(
        strategy_kwargs={"topk": 10, "n_drop": 2},
        handler_kwargs={"infer_processors": [{"class": "Fillna", "kwargs": {"fields_group": "feature"}}]},
        model_kwargs={"learning_rate": 0.05, "note": "v1.2"},
    ))


def test_allowlist_matches_frontend_catalog():
    """프런트 카탈로그에 있는 클래스는 전부 허용, 허용 목록에만 있는 것은 없다."""
    src = (ROOT / "app/frontend/src/lib/catalogs.ts").read_text()
    entries = set(re.findall(r'(\w+): \{\s*module: "([\w.]+)"', src))
    allowed = {(cls, mod) for pairs in ALLOWED.values() for mod, cls in pairs}
    assert entries == allowed


def test_grid_sweep_cannot_override_module(monkeypatch):
    from app.api.routers import backtest as router
    from app.api.schemas.backtest import CatalogEntry, GridBacktestRequest
    queued = []
    monkeypatch.setattr(router.run_backtest_task, "delay", lambda cfg: queued.append(cfg))
    req = GridBacktestRequest(
        base=BacktestRequest(),
        models=[CatalogEntry(**{"class": "LGBModel", "module": "qlib.contrib.model.gbdt"})],
        strategies=[CatalogEntry(**{"class": "TopkDropoutStrategy",
                                    "module": "qlib.contrib.strategy.signal_strategy"})],
        param_sweeps={"model_kwargs.lr": [0.1, 0.2], "model_module": ["subprocess"]},
    )
    with pytest.raises(HTTPException) as e:
        router.submit_grid(req)
    assert e.value.status_code == 400
    assert queued == []  # 한 조합도 큐에 들어가지 않는다


def test_single_submit_refuses_before_queueing(monkeypatch):
    from app.api.routers import backtest as router
    queued = []
    monkeypatch.setattr(router.run_backtest_task, "delay", lambda cfg: queued.append(cfg))
    with pytest.raises(HTTPException):
        router.submit_backtest(BacktestRequest(model_module="subprocess", model_class="Popen"))
    assert queued == []


def test_service_refuses_even_if_called_directly(monkeypatch):
    from app.api.services import backtest_service as svc
    monkeypatch.setattr(svc, "init_instance_by_config",
                        lambda *a, **k: pytest.fail("must not instantiate"))
    out = svc.run_backtest(_cfg(model_module="subprocess", model_class="Popen"))
    assert out["error"].startswith("rejected")


def test_grid_max_jobs_has_a_server_ceiling():
    from pydantic import ValidationError

    from app.api.schemas.backtest import GridBacktestRequest
    with pytest.raises(ValidationError):
        GridBacktestRequest(base=BacktestRequest(), models=[], strategies=[], max_jobs=100_000)


# ─── /data/features 식 ────────────────────────────────────────────────

@pytest.mark.parametrize("field", [
    "$close", "$volume", "$close/Ref($close, 1)-1", "Mean($volume, 5)", "$close*1.5",
    "($high-$low)/$close", "If($close>$open, 1, 0)",
])
def test_legit_fields_pass(field):
    check_field(field)


@pytest.mark.parametrize("field", [
    "__import__('os')", "$close.__class__", "eval", "Ref($close, x)", "'a'",
    '"$close"', "$close.real", "", "a" * 301, "$close;1", "Ref($close,1)[0]",
])
def test_eval_escapes_are_refused(field):
    with pytest.raises(UnsafeBacktestConfig):
        check_field(field)


def test_features_endpoint_400s_before_qlib(monkeypatch):
    from app.api.routers import data as router
    monkeypatch.setattr(router.data_service, "get_features",
                        lambda *a, **k: pytest.fail("must not reach qlib"))
    with pytest.raises(HTTPException) as e:
        router.get_features(instruments="005930", fields="__import__('os')",
                            start="2026-01-01", end="2026-01-02", freq="day")
    assert e.value.status_code == 400


# ─── 관리자 비밀번호 ─────────────────────────────────────────────────

def test_seed_syncs_changed_password(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api import db as dbmod
    from app.api import main
    from app.api.auth.security import bcrypt_hash, bcrypt_verify

    engine = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session)
    with Session() as s:
        s.add(dbmod.User(username="admin", password_hash=bcrypt_hash(main.INSECURE_PLACEHOLDER)))
        s.commit()

    monkeypatch.setattr(main.settings, "admin_username", "admin")
    monkeypatch.setattr(main.settings, "admin_password", "new-strong-password-0123456789")
    main._seed_admin_user()
    with Session() as s:
        h = s.query(dbmod.User).filter_by(username="admin").one().password_hash
    assert bcrypt_verify("new-strong-password-0123456789", h)
    assert not bcrypt_verify(main.INSECURE_PLACEHOLDER, h)


# ─── 로그인 후 이동 주소 ─────────────────────────────────────────────

def test_login_page_uses_safe_next():
    """TS 테스트 러너가 없어 소스로 확인한다: 페이지가 next 를 safeNext 로만 쓴다."""
    page = (ROOT / "app/frontend/src/app/login/page.tsx").read_text()
    auth = (ROOT / "app/frontend/src/lib/auth.ts").read_text()
    assert 'safeNext(params.get("next"))' in page
    assert 'params.get("next") ||' not in page
    body = auth[auth.index("export function safeNext"):]
    for guard in ('!raw.startsWith("/")', 'raw.startsWith("//")', 'raw.startsWith("/\\\\")'):
        assert guard in body


def test_no_real_looking_kis_appkey_in_tests():
    """공개 저장소다. 36자 PS… 리터럴은 더미 모양(x 가 10개 이상 이어짐)이어야 한다."""
    for p in (ROOT / "tests").rglob("*.py"):
        for m in re.findall(r"PS[A-Za-z0-9]{34}", p.read_text()):
            assert "x" * 10 in m, f"{p.name}: real-looking appkey literal"
