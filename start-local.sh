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

cmd_stop() {
    log_info "Stopping qlib containers..."
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down --remove-orphans
    log_success "Containers stopped."
}

cmd_status() {
    log_info "qlib containers:"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps
}

cmd_help() {
    cat <<EOF
Usage: $0 [command] [args]

Commands:
  setup             One-click setup (build + download US data + verify)
  (default)         Build & start interactive container (bash)
  jupyter           Start Jupyter notebook server
  run <yaml>        Run a qlib workflow config (qrun)
  data [region]     Download market data (us/cn/both, default: us)
  test [args]       Run pytest (passes extra args to pytest)
  stop              Stop and remove containers
  status            Show running containers
  help              Show this message

Examples:
  $0 setup                # one-click: build + US data + verify
  $0                      # interactive bash
  $0 data                 # download US data (default)
  $0 data cn              # download CN data
  $0 data both            # download both US and CN data
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
    jupyter)  cmd_jupyter ;;
    run)      cmd_run "${1:-}" ;;
    data)     cmd_data "${1:-}" ;;
    test)     cmd_test "$@" ;;
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
