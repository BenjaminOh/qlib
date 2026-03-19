# CLAUDE.md - qlib

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

Predefined region constants in `qlib/constant.py`: `REG_CN`, `REG_US`, `REG_TW`.
