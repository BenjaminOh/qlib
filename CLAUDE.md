# CLAUDE.md - qlib

> ⚠️ **이 저장소는 upstream qlib 이 아니라, qlib 을 엔진으로 쓰는 한국 주식
> 자동매매 봇입니다.** 실제 작업의 거의 전부가 `app/` 아래에 있습니다.
> upstream 설명(아래 "Upstream qlib" 절)만 읽고 판단하면 안 됩니다.

## 이 저장소가 실제로 하는 일

**KIS(한국투자증권) 모의계좌로 11개 매매 전략을 매일 병렬 운영하고, 그 성적을
비교해 승자를 고르는 실험 시스템**입니다. 2026-07-28 시작, ~3개월 테스트.

| 축 | 내용 |
|---|---|
| **실주문 2개** | `open`(기본 계좌, 09:00 시가 시장가, **순위 이탈 매도만** — 익절·손절·트레일 없음) · `cafereal`(카페 계좌, 15:28 현재가 −3% 지정가. 계좌 미설정 시 비활성) |
| **시뮬 9개** | close·flow·trail·scale·limit·cafe·cafeopen·cafecool·surge — DB 장부에만 기록 |
| 신호 | Alpha158 → LightGBM(lr 0.005 · **150라운드 고정** · 조기종료 없음), 유니버스 **kospi200**, topk 10 / n_drop 2 |
| 실행 | FastAPI(`app/api`) + Celery beat 37슬롯 + Next.js(`app/frontend`) |
| 배포 | GitHub push → Jenkins → blue/green. **`pytest tests/app`(412건)이 배포 게이트** |
| 운영 원칙 | **전략 동결** — 테스트 종료까지 설계 변경 금지, 버그 수정만 승인 후 |

### 코드 지도

| 경로 | 역할 |
|---|---|
| `app/api/services/live_trader.py` | 신호 생성·주문·청산의 중심. `EXIT_RULES`·`BRACKET_STRATEGIES`·`LIVE_CONFIG` |
| `app/api/services/market_screener.py` | 카페 역설계 스크리너(패턴 B/A/R/C/D) |
| `app/api/services/kis_client.py` | KIS OpenAPI. 주문·잔고·시세·레이트 게이트 |
| `app/api/workers/celery_app.py` | beat 스케줄(하루 타임라인의 진실) |
| `app/api/db/models.py` | `STRATEGY_*`·`ACCOUNT_STRATEGIES`·13개 테이블 |
| `docs/00-운영-매매-방식-해설.md` | **오너용 해설서 — 시스템을 처음 보면 여기부터** |
| `docs/05-daily/INSIGHTS.md` | 누적 운영 지식(매일 점검 에이전트가 읽는 지식 베이스) |

### 테스트·린트 (이 프로젝트 기준)

```bash
pytest tests/app/          # 실제 게이트. 412건, 의존성 없으면 importorskip 으로 SKIP
```

⚠️ `make lint` 는 `qlib/`·`scripts/` 만 본다 — **`app/` 은 어떤 린터에도 걸리지 않는다.**

### 앱 설정

`app/api/config.py` 의 `Settings` — env prefix 는 **`QLIB_API_`** (운영은 `QLIB_API_LIVE_*`).
아래 upstream 절의 `QLIB_` prefix(`QSettings`)와 **다른 것**이다.

---

# Upstream qlib 참고

Microsoft's AI-oriented quantitative investment platform (pyqlib).

## Build & Install

```bash
# Full install (compiles Cython extensions + installs all deps)
make dev

# Minimal install (Cython extensions + core deps only)
make install

# Or manually:
make prerequisite          # compile Cython .pyx -> .so (rolling, expanding)
pip install -e .[dev]      # editable install with dev extras
```

Cython extensions are in `qlib/data/_libs/` (rolling.pyx, expanding.pyx). The `make prerequisite` step compiles them; it skips if .so files already exist.

## Testing

Tests live in `tests/`. Run from repo root:

```bash
# Run all tests
pytest tests/

# Skip slow tests
pytest tests/ -m "not slow"

# Run a single test file
pytest tests/test_all_pipeline.py

# Run a specific test
pytest tests/test_all_pipeline.py::TestClass::test_method
```

Note: Most tests require qlib data to be downloaded first (`python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data`). RL tests (`tests/rl/`) require the `[rl]` extra (tianshou, torch) and `numpy<2.0`.

pytest config is in `tests/pytest.ini`. Marker: `slow`.

## Linting

```bash
# Run all linters
make lint          # runs: black, pylint, flake8, mypy, nbqa

# Individual linters
make black         # black --check --diff, line-length 120
make pylint        # pylint on qlib/ and scripts/
make flake8        # flake8 on qlib/
make mypy          # mypy on qlib/
make nbqa          # black + pylint on notebooks
```

Line length: 120 (enforced by black). `qlib/_version.py` is excluded from black.

## Architecture

### Key Modules

| Module | Purpose |
|--------|---------|
| `qlib/data/` | Data loading, caching, operators, storage backends |
| `qlib/model/` | Model base classes and training |
| `qlib/backtest/` | Backtesting engine |
| `qlib/strategy/` | Trading strategies |
| `qlib/workflow/` | Experiment management (MLflow-based) |
| `qlib/contrib/` | Community-contributed models, strategies, data handlers |
| `qlib/rl/` | Reinforcement learning integration |
| `qlib/utils/` | Shared utilities |

### Provider Pattern

Qlib uses a **provider pattern** for pluggable data backends. Providers are registered through `qlib/data/base.py` and configured via the `C` singleton. Data can come from local files, NFS mounts, or a client-server setup.

### Configuration: `C` Singleton

The global config is `qlib.config.C` (a `Config` instance). Two modes: `client` and `server`.

```python
import qlib
qlib.init(default_conf="client", provider_uri="~/.qlib/qlib_data/cn_data")
```

`qlib.init()` sets up providers, mounts NFS if needed, and registers the config. `qlib.auto_init()` finds a `config.yaml` in ancestor directories.

Environment-based config via `QSettings` (pydantic-settings) with `QLIB_` prefix (e.g., `QLIB_PROVIDER_URI`).

### YAML Workflows

Experiments can be defined as YAML configs and run via CLI:

```bash
qrun <workflow_config.yaml>
```

`qrun` is the entry point (`qlib.cli.run:run`). Examples in `examples/`.

### Data Pipeline

Raw data -> `qlib/data/ops.py` operators (feature engineering expressions like `$close/Ref($close,1)-1`) -> datasets (`qlib/data/dataset/`) -> model training.

Data is stored in a binary format under `~/.qlib/qlib_data/` by default. Point-in-time (PIT) data supported via `qlib/data/pit.py`.

### Regions

Predefined region constants in `qlib/constant.py`: `REG_CN`, `REG_US`, `REG_TW`, **`REG_KR`**(이 프로젝트가 upstream 에 추가 — `qlib/config.py` 의 KR 설정: trade_unit=1, limit_threshold=0.30, deal_price="close").
