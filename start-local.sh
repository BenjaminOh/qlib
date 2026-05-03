#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────
# qlib local development script
# ─────────────────────────────────────────────────

COMPOSE_FILE="docker-compose.dev.yml"
PROJECT_NAME="qlib"
IMAGE_NAME="qlib-dev"
JUPYTER_START_PORT=8888

# ─── Colors ──────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── Helpers ─────────────────────────────────────
find_available_port() {
    local port=$1
    while lsof -i :"$port" &>/dev/null 2>&1; do
        port=$((port + 1))
    done
    echo "$port"
}

container_name() {
    local port="${1:-0}"
    echo "${PROJECT_NAME}_dev_${port}"
}

cleanup() {
    log_info "Cleaning up..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down --remove-orphans 2>/dev/null || true
}

ensure_image() {
    log_info "Building dev image..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" build
    log_success "Image built."
}

# ─── Commands ────────────────────────────────────
cmd_setup() {
    ensure_image

    # 1. Download US market data
    log_info "Downloading US market data (~200MB)..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
        python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/us_data --region us

    # 2. Verify config.yaml and qlib initialization
    log_info "Verifying qlib initialization..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
        python -c "import qlib; qlib.auto_init(); print('qlib.auto_init() OK')"

    log_success "Setup complete! Run '$0' to start interactive container."
}

cmd_start() {
    ensure_image
    log_info "Starting interactive container..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm --service-ports qlib bash
}

cmd_jupyter() {
    ensure_image
    local port
    port=$(find_available_port "$JUPYTER_START_PORT")
    log_info "Starting Jupyter on port $port..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm \
        -p "${port}:8888" \
        --name "$(container_name "$port")" \
        qlib \
        jupyter notebook --ip=0.0.0.0 --port=8888 --no-browser --allow-root
    log_success "Jupyter available at http://localhost:${port}"
}

cmd_run() {
    local config="$1"
    if [[ -z "$config" ]]; then
        log_error "Usage: $0 run <workflow_config.yaml>"
        exit 1
    fi
    ensure_image
    log_info "Running workflow: $config"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
        qrun "$config"
}

cmd_data() {
    local region="${1:-us}"
    ensure_image

    case "$region" in
        us)
            log_info "Downloading US market data..."
            docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
                python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/us_data --region us
            ;;
        cn)
            log_info "Downloading CN market data..."
            docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
                python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data
            ;;
        both)
            log_info "Downloading US market data..."
            docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
                python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/us_data --region us
            log_info "Downloading CN market data..."
            docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
                python scripts/get_data.py qlib_data --target_dir ~/.qlib/qlib_data/cn_data
            ;;
        *)
            log_error "Unknown region: $region (use us/cn/both)"
            exit 1
            ;;
    esac
    log_success "Data downloaded to persistent volume."
}

cmd_test() {
    ensure_image
    log_info "Running tests..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
        pytest tests/ -m "not slow" "$@"
}

cmd_up() {
    ensure_image
    log_info "Starting all services in background..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d
    log_success "Services running in background."
    log_info "  API:      http://localhost:5001/api/v1/health"
    log_info "  Frontend: http://localhost:5002"
    log_info "  Jupyter:  http://localhost:8888"
    log_info "Use '$0 shell' to enter, '$0 exec <cmd>' to run commands."
}

cmd_shell() {
    log_info "Entering running container..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec qlib bash
}

cmd_exec() {
    if [[ $# -eq 0 ]]; then
        log_error "Usage: $0 exec <command> [args...]"
        exit 1
    fi
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec qlib "$@"
}

cmd_stop() {
    log_info "Stopping qlib containers..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down --remove-orphans
    log_success "Containers stopped."
}

cmd_status() {
    log_info "qlib containers:"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps
}

cmd_web() {
    ensure_image
    echo
    log_success "Starting web platform (foreground, Ctrl+C to stop all)..."
    echo
    log_info "  Frontend (UI)      : http://localhost:5002"
    log_info "  API health         : http://localhost:5001/api/v1/health"
    log_info "  API docs (Swagger) : http://localhost:5001/docs"
    echo
    log_info "Press Ctrl+C to stop all services"
    echo
    # Foreground: interleaved colored logs from all services.
    # --abort-on-container-exit: if one dies, tear down the rest.
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up \
        --abort-on-container-exit \
        redis api worker frontend
    cleanup
}

cmd_web_bg() {
    ensure_image
    log_info "Starting web platform in background..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d redis api worker frontend
    log_success "Web platform running in background!"
    log_info "  Frontend : http://localhost:5002"
    log_info "  API docs : http://localhost:5001/docs"
    log_info "  Use '$0 logs' to follow logs, '$0 web-down' to stop."
}

cmd_web_down() {
    log_info "Stopping web services..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" stop redis api worker frontend
    log_success "Web services stopped (containers kept; use 'stop' to remove)."
}

cmd_logs() {
    local service="${1:-}"
    if [[ -z "$service" ]]; then
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs -f --tail=200
    else
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs -f --tail=200 "$service"
    fi
}

cmd_restart() {
    local service="${1:-}"
    if [[ -z "$service" ]]; then
        log_info "Restarting all web services..."
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" restart redis api worker frontend
    else
        log_info "Restarting $service..."
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" restart "$service"
    fi
    log_success "Restart complete."
}

cmd_rebuild() {
    local service="${1:-}"
    log_info "Rebuilding${service:+ $service} (no cache)..."
    if [[ -z "$service" ]]; then
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" build --no-cache
    else
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" build --no-cache "$service"
    fi
    log_success "Rebuild complete. Run '$0 restart${service:+ $service}' to apply."
}

cmd_open() {
    local url="http://localhost:5002"
    log_info "Opening $url"
    if command -v open &>/dev/null; then
        open "$url"
    elif command -v xdg-open &>/dev/null; then
        xdg-open "$url"
    else
        log_warning "No browser opener found. Visit $url manually."
    fi
}

cmd_health() {
    log_info "Checking service health..."
    local api_url="http://localhost:5001/api/v1/health"
    local fe_url="http://localhost:5002"
    if curl -sf "$api_url" >/dev/null; then
        log_success "API  OK  ($api_url)"
    else
        log_error   "API  FAIL ($api_url)"
    fi
    if curl -sf -o /dev/null "$fe_url"; then
        log_success "UI   OK  ($fe_url)"
    else
        log_error   "UI   FAIL ($fe_url)"
    fi
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps
}

cmd_data_kr() {
    ensure_image
    log_info "Fetching Korean market data (KOSPI200 via yfinance)..."
    # All three steps run in ONE container so pip-installed yfinance stays in site-packages
    # for the fetch, and the generated CSVs (persisted via ./app bind-mount) can be consumed
    # by dump_bin in the same invocation. CSVs persist to ./data/kr_csv on host, qlib binary
    # data persists to named volume qlib_data at /root/.qlib/qlib_data/kr_data.
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" run --rm qlib \
        sh -c "pip install --quiet yfinance && \
               python scripts/kr_data_fetch.py --market kospi200 --start 2015-01-01 --end 2024-12-31 && \
               echo '[INFO] Converting to qlib binary format...' && \
               python scripts/dump_bin.py dump_all \
                   --data_path ./data/kr_csv \
                   --qlib_dir /root/.qlib/qlib_data/kr_data \
                   --freq day --date_field_name date --symbol_field_name symbol"
    log_success "Korean market data ready!"
}

cmd_test_web() {
    log_info "Running web smoke tests against localhost:5001 / :5002 ..."
    if ! curl -sf http://localhost:5001/api/v1/health >/dev/null 2>&1; then
        log_error "API not reachable at localhost:5001."
        log_error "Start services first with './$0 web' or './$0 web-bg'."
        exit 1
    fi
    if ! curl -sf -o /dev/null http://localhost:5002 2>/dev/null; then
        log_warning "Frontend not reachable at localhost:5002 (tests that hit the UI will fail)."
    fi
    python3 scripts/test_web.py
}

cmd_test_matrix() {
    log_info "Running exploratory backtest matrix (19 scenarios, ~2 min)..."
    if ! curl -sf http://localhost:5001/api/v1/health >/dev/null 2>&1; then
        log_error "API not reachable at localhost:5001."
        log_error "Start services first with './$0 web' or './$0 web-bg'."
        exit 1
    fi
    python3 scripts/test_backtest_matrix.py
}

cmd_help() {
    cat <<EOF
Usage: $0 [command] [args]

Commands:
  setup                One-click setup (build + download US data + verify)
  (default)            Build & start interactive container (bash, one-off)
  up                   Start all services in background
  web                  Start web platform in FOREGROUND (live logs, Ctrl+C tears down all)
  web-bg               Start web platform in background (use 'logs' to follow)
  web-down             Stop web services (keep containers)
  open                 Open the web UI (http://localhost:5002) in browser
  health               Ping API + UI and print compose status
  logs [service]       Tail logs (all, or api/worker/frontend/redis/qlib)
  restart [service]    Restart web services (or a specific one)
  rebuild [service]    Rebuild image(s) with --no-cache
  shell                Enter running background container (bash)
  exec <cmd>           Run a command in the running container
  jupyter              Start Jupyter notebook server
  run <yaml>           Run a qlib workflow config (qrun)
  data [region]        Download market data (us/cn/both, default: us)
  data-kr              Fetch Korean market data (KOSPI200 via yfinance)
  test [args]          Run pytest (passes extra args to pytest)
  test-web             Run end-to-end smoke tests against the running web stack (~12s)
  test-matrix          Run exploratory backtest scenario matrix (~2 min)
  stop                 Stop and remove containers
  status               Show running containers
  help                 Show this message

Web workflow (recommended):
  $0 web               # boot API + Worker + Frontend + Redis
  $0 open              # open the UI in your browser
  $0 logs api          # follow API logs while you click around
  $0 restart api       # re-apply backend changes
  $0 web-down          # pause services when done

See docs/WEB_USAGE.md for the full UI walkthrough.

Examples:
  $0 setup                # one-click: build + US data + verify
  $0                      # interactive bash (one-off, removed on exit)
  $0 up                   # start all services (persistent)
  $0 web                  # start web platform (API + Frontend)
  $0 data-kr              # fetch Korean market data
  $0 shell                # enter background container
  $0 exec python workspace/my_strategy.py   # run from IDE
  $0 data                 # download US data (default)
  $0 data cn              # download CN data
  $0 jupyter              # start Jupyter
  $0 run examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
  $0 test                 # run tests (skip slow)
  $0 stop                 # tear down
EOF
}

# ─── Signal handler ──────────────────────────────
trap cleanup SIGINT SIGTERM

# ─── Main ────────────────────────────────────────
command="${1:-}"
shift 2>/dev/null || true

case "$command" in
    setup)    cmd_setup ;;
    up)       cmd_up ;;
    web)      cmd_web ;;
    web-bg)   cmd_web_bg ;;
    web-down) cmd_web_down ;;
    open)     cmd_open ;;
    health)   cmd_health ;;
    logs)     cmd_logs "${1:-}" ;;
    restart)  cmd_restart "${1:-}" ;;
    rebuild)  cmd_rebuild "${1:-}" ;;
    shell)    cmd_shell ;;
    exec)     cmd_exec "$@" ;;
    jupyter)  cmd_jupyter ;;
    run)      cmd_run "${1:-}" ;;
    data)     cmd_data "${1:-}" ;;
    data-kr)  cmd_data_kr ;;
    test)     cmd_test "$@" ;;
    test-web) cmd_test_web ;;
    test-matrix) cmd_test_matrix ;;
    stop)     cmd_stop ;;
    status)   cmd_status ;;
    help)     cmd_help ;;
    "")       cmd_start ;;
    *)
        log_error "Unknown command: $command"
        cmd_help
        exit 1
        ;;
esac
